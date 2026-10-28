// pool.cpp - worker pool, sharded candidate store and ranking for the
// embedding scoring service.
//
// Deployment: one process per host, kWorkers threads (64-128 on our Xeon
// fleet), each pulling query batches off a shared queue.  A batch scores
// every candidate of one shard against one query and hands the scores to the
// results writer.  Typical batch: 1-4 M candidates.

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <immintrin.h>

#include "scoring.h"

namespace scoring {

constexpr int kMaxWorkers = 128;

// ---------------------------------------------------------------------------
// Locks
// ---------------------------------------------------------------------------

// Protects a shard's append cursor.  Ingest threads take it for every block
// they append, so it sees heavy contention during bulk loads.
class ShardLock {
 public:
  void lock() {
    while (locked_.exchange(true, std::memory_order_acquire))
      _mm_pause();
  }
  void unlock() { locked_.store(false, std::memory_order_release); }

 private:
  std::atomic<bool> locked_{false};
};

// Protects the latency histogram.  Taken once per batch by the workers and
// by the scraper every few seconds.
class MetricsLock {
 public:
  void lock() {
    for (;;) {
      if (!locked_.exchange(true, std::memory_order_acquire))
        return;
      while (locked_.load(std::memory_order_relaxed))
        _mm_pause();
    }
  }
  void unlock() { locked_.store(false, std::memory_order_release); }

 private:
  alignas(64) std::atomic<bool> locked_{false};
};

// ---------------------------------------------------------------------------
// Process-wide statistics (exported on /metrics)
// ---------------------------------------------------------------------------

std::atomic<uint64_t> g_candidates_scored{0};
std::atomic<uint64_t> g_bytes_ingested{0};

// ---------------------------------------------------------------------------
// Candidate store
// ---------------------------------------------------------------------------

struct Shard {
  std::vector<float> rows;       // n * SCORING_DIM
  std::vector<uint8_t> codes;    // n * SCORING_CODE_BYTES
  std::vector<uint32_t> crcs;    // one per row
  size_t n = 0;
  ShardLock lock;

  // Called by ingest threads; one call per incoming candidate.
  void append(const float* row, const uint8_t* code) {
    uint32_t crc = block_crc32c(0, row, SCORING_DIM * sizeof(float));
    lock.lock();
    size_t at = n++;
    rows.resize(n * SCORING_DIM);
    codes.resize(n * SCORING_CODE_BYTES);
    crcs.push_back(crc);
    std::memcpy(&rows[at * SCORING_DIM], row, SCORING_DIM * sizeof(float));
    std::memcpy(&codes[at * SCORING_CODE_BYTES], code, SCORING_CODE_BYTES);
    lock.unlock();
    g_bytes_ingested.fetch_add(SCORING_DIM * sizeof(float) + SCORING_CODE_BYTES,
                               std::memory_order_relaxed);
  }
};

// ---------------------------------------------------------------------------
// Latency histogram
// ---------------------------------------------------------------------------

class MetricsRegistry {
 public:
  void observe_batch_us(uint64_t us) {
    int bucket = 0;
    while (bucket < kBuckets - 1 && (1ull << bucket) < us)
      bucket++;
    lock_.lock();
    hist_[bucket]++;
    lock_.unlock();
  }

  void snapshot(uint64_t out[], int n) {
    lock_.lock();
    for (int i = 0; i < n && i < kBuckets; i++)
      out[i] = hist_[i];
    lock_.unlock();
  }

  static constexpr int kBuckets = 24;

 private:
  MetricsLock lock_;
  uint64_t hist_[kBuckets] = {};
};

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

// Parses the score cut-points ("0.2,0.5,0.8,...") from the service config.
// Called once at startup; the config holds at most eight values.
std::vector<float> load_cutpoints(const char* spec) {
  std::vector<float> cuts;
  const char* p = spec;
  while (*p) {
    char* end = nullptr;
    float v = std::strtof(p, &end);
    if (end == p)
      break;
    cuts.push_back(v);
    p = (*end == ',') ? end + 1 : end;
  }
  std::sort(cuts.begin(), cuts.end());
  return cuts;
}

// ---------------------------------------------------------------------------
// Ranking
// ---------------------------------------------------------------------------

// Orders one batch's scores (1-4 M floats) so the results writer can stream
// them from the back (best first) and collapse equal scores.
void rank_scores(std::vector<float>& scores) {
  std::sort(scores.begin(), scores.end());
}

// ---------------------------------------------------------------------------
// Worker pool
// ---------------------------------------------------------------------------

struct Job {
  const Shard* shard = nullptr;
  const float* query = nullptr;          // SCORING_DIM floats
  const uint8_t* query_code = nullptr;   // SCORING_CODE_BYTES
  const float* bias = nullptr;           // SCORING_DIM floats
  uint32_t max_hamming = 0;
  std::function<void(std::vector<float>&&)> done;
};

// Per-worker scratch space, owned and written by exactly one worker.
struct alignas(64) WorkerScratch {
  uint64_t probes = 0;
  uint64_t rejected = 0;
  float last_best = 0.0f;
};

class ScoringPool {
 public:
  explicit ScoringPool(int workers) : nworkers_(std::min(workers, kMaxWorkers)) {
    for (int i = 0; i < nworkers_; i++)
      threads_.emplace_back([this, i] { worker_loop(i); });
  }

  ~ScoringPool() {
    {
      std::lock_guard<std::mutex> g(mu_);
      stop_ = true;
    }
    cv_.notify_all();
    for (auto& t : threads_)
      t.join();
  }

  // Called by the request front-end for every incoming query batch.
  void submit(Job job) {
    {
      std::lock_guard<std::mutex> g(mu_);
      queue_.push_back(std::move(job));
    }
    cv_.notify_all();
  }

  uint64_t batches_done() const {
    uint64_t s = 0;
    for (int i = 0; i < nworkers_; i++)
      s += batches_done_[i];
    return s;
  }

  MetricsRegistry& metrics() { return metrics_; }

 private:
  void worker_loop(int id) {
    alignas(32) float tmp[SCORING_DIM];
    for (;;) {
      Job job;
      {
        std::unique_lock<std::mutex> g(mu_);
        cv_.wait(g, [this] { return stop_ || !queue_.empty(); });
        if (stop_ && queue_.empty())
          return;
        job = std::move(queue_.front());
        queue_.pop_front();
      }
      auto t0 = std::chrono::steady_clock::now();
      std::vector<float> scores = score_batch(job, tmp, scratch_[id]);
      rank_scores(scores);
      auto us = std::chrono::duration_cast<std::chrono::microseconds>(
                    std::chrono::steady_clock::now() - t0).count();
      metrics_.observe_batch_us(static_cast<uint64_t>(us));
      note_progress(id, scores.size());
      if (job.done)
        job.done(std::move(scores));
    }
  }

  void note_progress(int id, size_t n) {
    batches_done_[id]++;
    candidates_done_[id] += n;
  }

  static void note_probe(WorkerScratch& ws, bool rejected) {
    ws.probes++;
    if (rejected)
      ws.rejected++;
  }

  // Scores every candidate in the job's shard; candidates whose binary code is
  // too far from the query's are rejected before the float rescoring.
  std::vector<float> score_batch(const Job& job, float* tmp, WorkerScratch& ws) {
    const Shard& sh = *job.shard;
    std::vector<float> out;
    out.reserve(sh.n);
    for (size_t c = 0; c < sh.n; c++) {
      const uint8_t* code = &sh.codes[c * SCORING_CODE_BYTES];
      uint32_t hd = hamming_distance(job.query_code, code, SCORING_CODE_BYTES);
      bool reject = hd > job.max_hamming;
      note_probe(ws, reject);
      if (reject)
        continue;
      const float* row = &sh.rows[c * SCORING_DIM];
      for (int t = 0; t < SCORING_DIM; t += 32)
        block_copy_f32x32(tmp + t, row + t);
      float mean = vec_mean_f32(tmp, SCORING_DIM);
      vec_center_f32(tmp, mean, SCORING_DIM);
      alignas(32) float adj[SCORING_DIM];
      vec_affine_f32(adj, tmp, job.bias, 1.0f, 0.5f, SCORING_DIM);
      float s = vec_dot_f32(adj, job.query, SCORING_DIM);
      g_candidates_scored.fetch_add(1, std::memory_order_relaxed);
      out.push_back(s);
      if (s > ws.last_best)
        ws.last_best = s;
    }
    return out;
  }

  int nworkers_;
  std::vector<std::thread> threads_;
  std::mutex mu_;
  std::condition_variable cv_;
  std::deque<Job> queue_;
  bool stop_ = false;

  uint64_t batches_done_[kMaxWorkers] = {};      // [i] written by worker i only
  uint64_t candidates_done_[kMaxWorkers] = {};   // [i] written by worker i only
  WorkerScratch scratch_[kMaxWorkers];
  MetricsRegistry metrics_;
};

}  // namespace scoring

// ---------------------------------------------------------------------------
// Smoke test: `make check` runs a tiny shard through the pool.
// ---------------------------------------------------------------------------

#ifdef SCORING_SELFTEST
int main() {
  using namespace scoring;
  scoring_kernels_init();
  if (!__builtin_cpu_supports("avx")) {
    std::puts("selftest skipped: no AVX");
    return 0;
  }
  auto cuts = load_cutpoints("0.8,0.2,0.5");
  Shard shard;
  alignas(32) float row[SCORING_DIM];
  uint8_t code[SCORING_CODE_BYTES];
  for (int c = 0; c < 1000; c++) {
    for (int i = 0; i < SCORING_DIM; i++)
      row[i] = static_cast<float>((c * 31 + i * 7) % 97) / 97.0f;
    for (int i = 0; i < SCORING_CODE_BYTES; i++)
      code[i] = static_cast<uint8_t>(c * 13 + i);
    shard.append(row, code);
  }
  alignas(32) float query[SCORING_DIM], bias[SCORING_DIM];
  for (int i = 0; i < SCORING_DIM; i++) {
    query[i] = 1.0f / (1 + i);
    bias[i] = 0.01f * i;
  }
  uint8_t qcode[SCORING_CODE_BYTES] = {};
  std::atomic<int> pending{4};
  {
    ScoringPool pool(4);
    for (int j = 0; j < 4; j++) {
      Job job;
      job.shard = &shard;
      job.query = query;
      job.query_code = qcode;
      job.bias = bias;
      job.max_hamming = SCORING_CODE_BYTES * 8;
      job.done = [&pending](std::vector<float>&&) { pending--; };
      pool.submit(std::move(job));
    }
    while (pending.load() > 0)
      std::this_thread::yield();
  }
  std::printf("selftest ok: %zu cuts, %llu scored, crc %08x\n", cuts.size(),
              static_cast<unsigned long long>(g_candidates_scored.load()),
              block_crc32c(0, "123456789", 9));
  return 0;
}
#endif
