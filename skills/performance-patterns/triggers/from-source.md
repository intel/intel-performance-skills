<!-- (C) 2026 Intel Corporation, MIT license -->
# Pattern triggers: source code

Use this file when you are reading source code and do not (yet) have profiling
data. Find the matching pattern below, then read the linked file in `patterns/`
for the full diagnosis and fix.

These patterns are worth fixing even without profiling confirmation — the code
structure alone is a strong predictor of the performance problem.

---

## Quick-match table

| Signal in source code | Pattern | Detail file |
|-----------------------|---------|-------------|
| Single FP variable updated in a loop: `sum += a[i]*b[i]`, `acc = fma(...)`, running max/min | Serial accumulator | `patterns/parallel-accumulator.md` |
| Inline asm or function uses `ymm`/`zmm0–15` registers with no `vzeroupper` before return or SSE call | Missing vzeroupper | `patterns/missing-vzeroupper.md` |
| `_mm_*` intrinsics (SSE/128-bit) or plain scalar float loop, no `_mm256_*` / `_mm512_*` | Narrow SIMD | `patterns/simd-upconversion.md` |
| C function with two or more pointer parameters, at least one written, no `restrict` qualifier, separate input/output buffers | Missing restrict | `patterns/missing-restrict.md` |
| Spinlock body is `while (!cmpxchg(&lock, ...))` with no prior read of the lock variable | Test-and-Set spinlock | `patterns/ttas.md` |
| Struct fields written by different threads, no `alignas(64)` between them | False sharing | `patterns/false-sharing.md` |
| Global `count++` / `atomic_inc` / `atomic_fetch_add` on a statistics field in a hot path | Shared statistics counter | `patterns/per-cpu-stats.md` |
| Hot function calls error-reporters / rare-case handlers without `[[gnu::cold]]` or `__attribute__((cold))` | Cold-path annotation | `patterns/cold-path-annotation.md` |
| `pthread_cond_broadcast` / `cv.notify_all()` waking a thread pool; `notify_one()` in a loop waking N threads; dispatcher wakes all threads regardless of job count | CV thundering herd | `patterns/cv-thundering-herd.md` |
| `mutex_lock()` / `pthread_mutex_lock()` guarding a lookup, search, or cache read where writes are rare (<25% of acquisitions) | Mutex to rwlock | `patterns/mutex-to-rwlock.md` |
| Function/loop named or described as a known algorithm (`hamming_distance`, `cosine_similarity`, `jaccard_distance`, `iou`, …) | Known algorithm — optimized SIMD replacement available | `references/known-algorithms-impl.md` |
| `std::sort`, `std::nth_element`, `std::partial_sort`, or `qsort` called on `float` / `double` / `int32_t` / `uint32_t` / `int64_t` / `uint64_t` arrays | SIMD sort | `patterns/simd-sort.md` |
| Function/loop named `crc32c` / `crc32_c` / `compute_crc32c`; single `_mm_crc32_u64` accumulator variable; byte-by-byte table-lookup CRC32C loop | Fast CRC32C | `patterns/fast-crc32c.md` |

---

## Pattern descriptions

### Missing vzeroupper

An inline assembly block or function that uses `ymm` registers or `zmm0`–`zmm15`
(which alias `ymm0`–`ymm15`) and returns or calls into code that may use legacy
SSE instructions, without emitting `vzeroupper` first. The upper 128 bits of the
YMM registers remain "dirty" from the CPU's perspective; the first SSE instruction
that follows will trigger an AVX↔SSE transition penalty costing hundreds of cycles.

Recognizable by: `ymm` or `zmm0`–`zmm15` in an asm block with no `vzeroupper`
at the end; or AVX intrinsic code (`_mm256_*`, `_mm512_*`) with no
`_mm256_zeroupper()` before returning to a caller compiled without AVX.

Read `patterns/missing-vzeroupper.md`.

---

### Serial accumulator

A loop that reduces a sequence into a single value — dot product, sum, running
max/min, weighted sum, histogram bucket — using one accumulator variable. The
accumulator is updated on every iteration (`sum += a[i] * b[i]`), creating a
loop-carried dependency: each iteration must wait for the previous one to finish
before it can begin. The CPU cannot exploit its ability to run multiple FP
operations per cycle. Recognizable by a single scalar variable on the left-hand
side of a compound-assignment inside the loop body, with no other loop-carried
dependency present.

Read `patterns/parallel-accumulator.md`.

---

### Missing restrict

A C function (not C++) that takes two or more pointer parameters — at least one
written — without `restrict`, where the buffers are guaranteed by the caller not
to overlap. Common signatures: `void filter(float *in, float *out, int n)`,
`void add(const float *a, const float *b, float *dst, int n)`. Without
`restrict`, the compiler must assume any write might alias any read of the same
type, forcing it to emit a runtime overlap check and a scalar fallback, or to
abandon auto-vectorization entirely.

Only applies to C. For C++, `__restrict__` (GCC/Clang extension) is available
but non-standard.

Read `patterns/missing-restrict.md`.

---

### Narrow SIMD

A floating-point or integer loop that uses 128-bit SSE intrinsics (`_mm_*`,
`__m128`, `__m128d`) or no SIMD at all (plain `float` or `double` arithmetic in
a loop). Modern x86 CPUs support 256-bit (AVX2) and often 512-bit (AVX-512)
operations that process 2–4× more data per instruction. This pattern also applies
when the compiler has auto-vectorized the loop but chose `xmm` registers —
visible in the object file or assembly listing (`-S` output). Check
`/proc/cpuinfo` for `avx2` and `avx512f` flags before recommending a target width.

Read `patterns/simd-upconversion.md`.

---

### Test-and-Set spinlock

A spinlock whose spin loop performs only an atomic read-modify-write operation
with no preceding ordinary read of the lock variable:

```c
while (!cmpxchg(&lock, UNLOCKED, LOCKED))   /* ◄ no read before the atomic */
    _mm_pause();
```

Every iteration of this loop acquires the lock's cache line exclusively — even
on failure — causing the line to bounce between all waiting threads and away from
the thread that holds the lock. Throughput degrades super-linearly with thread
count. The fix (TTAS) adds a cheap shared read before each atomic attempt.

Read `patterns/ttas.md`.

---

### False sharing

A struct contains fields that are written frequently by different threads (e.g.,
per-thread counters, state flags, or work-item metadata), and those fields are
not separated by `alignas(64)` / `__attribute__((aligned(64)))` padding. If they
land on the same 64-byte cache line, each write by one thread invalidates the
line for all other threads, even though no thread needs what the others wrote.
Recognizable by a shared struct definition where different fields are documented
as "owned by thread X" or updated inside per-thread loops with no explicit cache
line alignment between them.

Read `patterns/false-sharing.md`.

---

### Shared statistics counter

A global or shared counter that is incremented atomically from many threads in a
frequently-called path: `global_counter++`, `atomic_inc(&hits)`,
`atomic_fetch_add(&bytes, n)`. Unlike a lock, there is no retry logic — just a
bare increment — but the hardware still requires exclusive cache-line ownership
for every atomic write, causing the counter's cache line to bounce between all
updating threads. Field names are the strongest hint: `count`, `total`, `hits`,
`misses`, `errors`, `stat`, `bytes`, `packets`. No `cmpxchg` loop present
(if there is one, the TTAS pattern applies instead).

Read `patterns/per-cpu-stats.md`.

---

### SIMD sort

`std::sort`, `std::nth_element`, `std::partial_sort`, or C-style `qsort`
called on an array or `std::vector` of `float`, `double`, `int32_t`,
`uint32_t`, `int64_t`, or `uint64_t`. The data type is the key signal —
these are the types for which x86-simd-sort provides a drop-in AVX-512/AVX2
accelerated replacement. A hand-written quicksort or merge sort over numeric
primitives is an equally strong trigger. Check whether `std::stable_sort` is
in use before recommending a replacement (no stable-sort equivalent exists).

Read `patterns/simd-sort.md`.

---

### CV thundering herd

A condition variable (`pthread_cond_broadcast` or `cv.notify_all()`) wakes all
waiting threads when only a subset has work available. Or `notify_one()` is
called in a sequential loop to wake N threads one by one. Common pattern: a
dispatcher/leader thread wakes `num_threads` workers unconditionally
(`for (i = 0; i < nthreads; i++) wake(thread[i])`) regardless of how many jobs
are pending. Workers that find no work after waking call `sched_yield()` or
immediately re-block. The wasted wakeups scale with thread count and become the
dominant cost at high core count (HCC) scale.

Read `patterns/cv-thundering-herd.md`.

---

### Mutex to rwlock

A mutex (`mutex_lock()` in kernel, `pthread_mutex_lock()` in user space)
protects a critical section where the common path only reads shared data —
lookups in a cache, searches in a tree, status checks, configuration reads.
Writes occur rarely (new entry creation, occasional updates). With many threads
and high core counts, all readers serialize on the mutex even though they don't
conflict. The fix is replacing the mutex with an rwlock (`pthread_rwlock_t` in
user space, or `rw_semaphore` in kernel for sleepable contexts; `rwlock_t` in
kernel is spin-based and only suitable for non-sleeping critical sections) so
readers proceed concurrently.

Read `patterns/mutex-to-rwlock.md`.

---

### Known algorithm — optimized SIMD replacement available

A function whose name matches any entry in the table below. The implementation
may be correct but scalar, or use a narrower SIMD width than the CPU supports.
The function name is sufficient trigger — inspect the body only to confirm ISA
level choices.

| Algorithm | Common function names in code |
|-----------|-------------------------------|
| Cosine Similarity | `cosine_similarity`, `cosine_sim`, `cos_sim`, `cosine_distance`, `angular_similarity`, `dot_normalized` |
| Hamming Distance | `hamming_distance`, `hamming_dist`, `hamming`, `count_differing_bits`, `bit_diff_count`, `popcount_xor` |
| Jaccard Distance | `jaccard_distance`, `jaccard_similarity`, `jaccard_sim`, `jaccard_index`, `jaccard_coeff`, `iou` |

If a function name from the table is present in the code being reviewed, read
`references/known-algorithms-impl.md` for the ISA levels, dispatch guards, and
implementation notes. Do not load it otherwise.

---

### Fast CRC32C

A function or loop that computes CRC32C using:

- a single `_mm_crc32_u64` / `_mm_crc32_u32` accumulator variable (latency-bound),
- a byte-by-byte or word-by-word table-lookup loop, or
- a function whose name is `crc32c`, `crc32_c`, `calc_crc32c`, `hash_crc32c`,
  or similar — the name alone strongly implies a suboptimal implementation.

The function name is a distinctive trigger: if you see a function called
`crc32c` in any performance-sensitive context, check whether it uses the
corsix fusion implementation before looking at how it is called.

Read `patterns/fast-crc32c.md`.

---

### Cold-path annotation

A hot function calls one or more functions that are only reached on rarely-taken
branches — error reporters, impossible-state handlers, rare corner-case paths —
and those callees are not marked `[[gnu::cold]]` or `__attribute__((cold))`.

Without the annotation, the compiler interleaves the cold-path instructions with
the hot-path instructions in the compiled output. This pollutes the instruction
cache and generates suboptimal branch sequences for the hot path. The annotation
tells the compiler the branch is almost never taken; it responds by emitting the
cold code after the function's main return sequence, keeping the hot path tight.

Recognizable by: a hot function whose body contains `if (error_condition)
{ handle_error(...); }` or similar guards, where `handle_error` does logging,
`fprintf(stderr, …)`, `exit`, `abort`, `throw`, or other error-only work, and
carries no cold annotation.

Read `patterns/cold-path-annotation.md`.