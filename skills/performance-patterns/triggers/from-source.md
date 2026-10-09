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
| Fixed-stride sequential scan inside a batched producer-consumer loop; working set ≫ L3; no `__builtin_prefetch` | Software prefetch | `patterns/software-prefetch.md` |
| Function/loop named or described as a known algorithm (`hamming_distance`, `cosine_similarity`, `jaccard_distance`, `iou`, …) | Known algorithm — optimized SIMD replacement available | `references/known-algorithms-impl.md` |
| `std::sort`, `std::nth_element`, `std::partial_sort`, or `qsort` called on `float` / `double` / `int32_t` / `uint32_t` / `int64_t` / `uint64_t` arrays | SIMD sort | `patterns/simd-sort.md` |
| Function/loop named `crc32c` / `crc32_c` / `compute_crc32c`; single `_mm_crc32_u64` accumulator variable; byte-by-byte table-lookup CRC32C loop | Fast CRC32C | `patterns/fast-crc32c.md` |

---

## Additional detection notes

These cover cases where the table alone doesn't carry enough specificity to
identify the pattern. For the mechanism/rationale behind each pattern, read the
linked `patterns/*.md` file — don't re-derive it here.

- **Missing restrict** — C only; C++ has no standard `restrict` (only the
  non-portable `__restrict__` extension).
- **Narrow SIMD** — check `/proc/cpuinfo` for `avx2`/`avx512f` before
  recommending a target width; also fires when the compiler auto-vectorized to
  `xmm` instead of a wider register.
- **SIMD sort** — check whether `std::stable_sort` is used before recommending
  x86-simd-sort; no stable-sort equivalent exists.
- **Known algorithm** — function name is sufficient trigger; inspect the body
  only to confirm ISA level. Common name variants:

  | Algorithm | Common function names in code |
  |-----------|-------------------------------|
  | Cosine Similarity | `cosine_similarity`, `cosine_sim`, `cos_sim`, `cosine_distance`, `angular_similarity`, `dot_normalized` |
  | Hamming Distance | `hamming_distance`, `hamming_dist`, `hamming`, `count_differing_bits`, `bit_diff_count`, `popcount_xor` |
  | Jaccard Distance | `jaccard_distance`, `jaccard_similarity`, `jaccard_sim`, `jaccard_index`, `jaccard_coeff`, `iou` |

  If a name from the table is present, read `references/known-algorithms-impl.md`
  for ISA levels, dispatch guards, and implementation notes. Do not load it
  otherwise.
- **Fast CRC32C** — name is a sufficient trigger even without inspecting the
  loop body; variants include `crc32c`, `crc32_c`, `calc_crc32c`, `hash_crc32c`.
- **Software prefetch** — stride-derivable addresses only (not `p = p->next`);
  measure with `patterns/tests/software-prefetch-bench.c` first — a flat
  sweep means do not add the hint.
