#!/bin/bash
# Oracle: the review a reviewer following performance-patterns/triggers/from-source.md
# writes: one finding per seeded problem, none on the code that is already right.
# Line numbers are looked up in /app/src so the oracle follows edits to the module.
set -euo pipefail
python3 - <<'PY'
import json, pathlib, re

src = pathlib.Path("/app/src")

def line(file, pat):
    for i, l in enumerate((src / file).read_text().splitlines(), 1):
        if re.search(pat, l):
            return i
    raise SystemExit(f"oracle: {pat!r} not found in {file}")

F = [
 ("kernels.c", r"total \+= x\[i\];",
  "vec_mean_f32 sums into one float accumulator: every add waits on the previous one, so the loop runs at FP-add latency, not throughput (and cannot vectorize without reassociation).",
  "Use 4-8 independent partial sums (or an AVX2/AVX-512 accumulator with several chains) and combine at the end; note the result may differ from the serial sum by rounding."),
 ("kernels.c", r"^void vec_affine_f32",
  "vec_affine_f32 takes dst/src/bias without restrict although callers always pass distinct buffers, so the compiler must assume aliasing: it emits a runtime overlap check plus a scalar fallback loop, or does not vectorize at all at -O2.",
  "Declare float *restrict dst, const float *restrict src, const float *restrict bias (header and definition)."),
 ("kernels.c", r"vmovups  %%ymm3,  96",
  "block_copy_f32x32 writes ymm0-ymm3 in inline asm and returns without vzeroupper; the rest of the file is SSE code (built without -mavx), so the next SSE instruction pays the AVX-SSE transition penalty, once per 32-float tile in score_batch.",
  "Emit vzeroupper at the end of the asm block and clobber ymm0-ymm15, or replace the asm with _mm256_loadu_ps/_mm256_storeu_ps in a target(\"avx\") function (or plain memcpy) so the compiler inserts vzeroupper."),
 ("kernels.c", r"crc = crc_table\[",
  "block_crc32c is a byte-at-a-time table-lookup CRC32C (~1-2 GB/s) run on every appended block; it is 10-50x slower than the hardware paths.",
  "Use the SSE4.2 crc32 instruction with three interleaved streams + PCLMULQDQ combine, and the AVX-512 VPCLMULQDQ folding path where available, behind runtime CPU dispatch with the table version as fallback; the checksum value is unchanged."),
 ("kernels.c", r"dist \+= \(uint32_t\)__builtin_popcount",
  "hamming_distance does one popcount per byte (a libgcc call or bit-twiddle without -mpopcnt) with a single accumulator, over a 64-byte code for every candidate.",
  "Process 8 bytes at a time with 64-bit popcnt and several accumulators, or AVX2 (nibble LUT / Harley-Seal) and AVX-512 VPOPCNTDQ variants behind runtime dispatch."),
 ("pool.cpp", r"while \(locked_\.exchange\(true",
  "ShardLock is a test-and-set spinlock: every waiter spins on exchange(), an atomic RMW that takes the cache line exclusive on each try, so under ingest contention the line bounces between all spinning cores.",
  "Make it test-and-test-and-set: try exchange once, then spin on a relaxed load with _mm_pause until the lock looks free before retrying; keep the lock on its own cache line."),
 ("pool.cpp", r"^  uint64_t batches_done_\[kMaxWorkers\]",
  "batches_done_ and candidates_done_ are packed per-worker counters: eight workers share each 64-byte line, so every note_progress() write invalidates the line for the neighbouring workers (false sharing).",
  "Move the counters into the per-worker alignas(64) WorkerScratch (or pad each slot to 64 bytes)."),
 ("pool.cpp", r"g_candidates_scored\.fetch_add",
  "g_candidates_scored (and g_bytes_ingested in Shard::append) is one global atomic incremented per candidate by every worker: true sharing, the cache line bounces across all cores on every increment.",
  "Count in a local variable inside score_batch and add once per batch, or use per-CPU / per-thread padded counters summed when /metrics is read."),
 ("pool.cpp", r"^  void submit\(Job job\)",
  "submit() calls cv_.notify_all() for every single job, waking all 64-128 workers; one wins the job and the rest re-take the mutex and go back to sleep (thundering herd: O(N) futex wakes and context switches per job).",
  "Use cv_.notify_one() per submitted job (notify after releasing the mutex); keep notify_all only for shutdown."),
 ("pool.cpp", r"^  std::sort\(scores\.begin",
  "rank_scores sorts 1-4 M floats per batch with std::sort, a scalar branchy introsort.",
  "Use x86-simd-sort (x86simdsort::qsort(scores.data(), scores.size(), hasnan)) - AVX-512/AVX2 vectorized partitioning, several times faster; set hasnan=true if scores can be NaN."),
]
out = [{"file": "src/" + f, "line": line(f, p), "problem": prob, "fix": fix} for f, p, prob, fix in F]
pathlib.Path("/app/review.json").write_text(json.dumps(out, indent=1) + "\n")
print(f"wrote {len(out)} findings")
PY
