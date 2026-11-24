# Why `sse()` runs at 1.24 IPC, and how to fix it

## What's wrong

Nothing is stalling on memory or branches. The loop is waiting on its own arithmetic. `s` is the only accumulator, so each iteration's FMA has to wait for the previous iteration's result:

```
1a80: vmovsd      (%rdi,%rax,8),%xmm1
1a85: vsubsd      (%rsi,%rax,8),%xmm1,%xmm1
1a8a: inc         %rax
1a8d: vfmadd231sd %xmm1,%xmm1,%xmm0   <- 90% of samples: xmm0 += r*r, needs last iteration's xmm0
1a92: cmp         %rax,%rdx
1a95: jne         1a80
```

An FMA takes 4 cycles to produce its result on Sapphire Rapids (Golden Cove). So this loop can finish at most one element every 4 cycles, however wide the core is. The loads, the subtraction and the counter don't depend on that chain, so they run ahead and then wait on it. perf charges the waiting to the FMA, which is why it shows 90%.

Your counters match this:

| Metric | Value |
|---|---|
| Cycles in `sse` | 96.4% × 81.4 G = 78.4 G |
| Elements | 100k points × 200k iterations = 2.0×10¹⁰ (branches = 2.02×10¹⁰, one loop branch per element) |
| **Cycles per element** | **3.9, which is the FMA latency of 4** |
| Instructions per element | 100.8 G / 20.2 G ≈ 5, so about 5 instructions per 4 cycles = **IPC ≈ 1.25** (you measured 1.24)¹ |

IPC 1.24 is simply the most this loop can reach. The counters rule out the other usual causes:
- **Branches:** 0.01% mispredicted.
- **Cache:** about 260 LLC references per call, against 25,000 cache lines touched per call. The 1.6 MB of `obs` + `pred` stays in the 2 MB L2.
- **Kernel:** 0.01 s of system time.

Pattern scan of the annotated loop:

| Pattern | Evidence | Fix |
|---|---|---|
| Serial accumulator | One `vfmadd231sd` = 90% of samples, accumulating into `xmm0` every iteration; IPC 1.24 with a 1.2% cache-miss rate | Several independent accumulators |
| Scalar FP on an AVX-512 CPU | Only scalar `sd` ops (one double each); the CPU has avx512f/dq/bw/vl and two 512-bit FMA units | Wide SIMD (zmm), with the lanes acting as extra accumulators |

Together these mean the loop does one FMA every 4 cycles, while the core can do 16 double-precision FMAs per cycle. That's about 1/64 of peak.

**Why `-O2`, `-O3` and `-march=native` don't fix it.** Splitting the sum, whether into several accumulators or into SIMD lanes, changes the order of the floating-point additions. That changes rounding, so the compiler isn't allowed to do it on its own. I checked both compilers:
- **Clang** (`-O3 -march=sapphirerapids`) reports: *"loop not vectorized: cannot prove it is safe to reorder floating-point operations"*. At `-O2` it unrolls the loop ×8 but still chains all eight FMAs through one register.
- **GCC 14** only tries an in-order ("fold-left") vector reduction, which is still serial, and rejects it as not worthwhile. It vectorizes only with `-fassociative-math`/`-ffast-math` or `#pragma omp simd reduction(+:s)`.
- **`restrict`** isn't the blocker: nothing is stored, so there's no aliasing problem.

## The fix (applied to `residuals.c`)

Use several independent accumulators, treat every SIMD lane as another partial sum, and add them together at the end. The core of the AVX-512 version:

```c
for (; i + 32 <= n; i += 32) {
    __m512d r0 = _mm512_sub_pd(_mm512_loadu_pd(obs + i +  0), _mm512_loadu_pd(pred + i +  0));
    __m512d r1 = _mm512_sub_pd(_mm512_loadu_pd(obs + i +  8), _mm512_loadu_pd(pred + i +  8));
    __m512d r2 = _mm512_sub_pd(_mm512_loadu_pd(obs + i + 16), _mm512_loadu_pd(pred + i + 16));
    __m512d r3 = _mm512_sub_pd(_mm512_loadu_pd(obs + i + 24), _mm512_loadu_pd(pred + i + 24));
    a0 = _mm512_fmadd_pd(r0, r0, a0);      /* four independent chains */
    a1 = _mm512_fmadd_pd(r1, r1, a1);
    a2 = _mm512_fmadd_pd(r2, r2, a2);
    a3 = _mm512_fmadd_pd(r3, r3, a3);
}
/* then the 0-31 leftover elements, 8 at a time, the last group with a masked load;
   then _mm512_reduce_add_pd */
```

- **Partial sums:** 4 zmm accumulators × 8 lanes = 32.
- **Why 4 accumulators:** each 8-element step needs two 64-byte loads, and the core does at most two such loads per cycle. That allows at most one FMA per cycle, and with a 4-cycle latency, 4 chains keep it busy.
- **Other paths:** `sse()` checks the CPU at runtime with `__builtin_cpu_supports`. It uses AVX-512F if available, then AVX2+FMA (4 ymm accumulators, 16 partial sums), then plain C with 4 scalar accumulators. So the file stays correct if you build without `-march=native`.
- **Unchanged:** the function signature and the build command.
- **Leftover elements:** the masked load reads nothing past the end of the arrays. The compiler adds `vzeroupper` on exit. Each CPU check covers exactly the instructions that variant uses.
- **Profiling note:** on your Xeon the work now happens in a function called **`sse_avx512`**, so that's the name perf will show.

**What to expect on the 8480+ (an estimate until you measure it):**
- **New limit:** with the dependency chain gone, the loop is limited by how fast the arrays stream from L2. Something like 0.3–0.5 cycles per element instead of 3.9.
- **Speed:** `sse()` should be roughly 8–15× faster, taking the whole run from 21.7 s to about 2–3.5 s. `generate_points` and `update_model` (about 0.8 s) then become a large share of the total.
- **IPC won't reach 4.** The new loop runs 17 instructions per 32 elements instead of about 5 per element. Instructions fall about as much as cycles do, so IPC stays around 1–2. Judge the fix by time or cycles per element.
- **AVX-512 vs AVX2:** they may come out close, since both are limited by L2. `sse_check` times both. If AVX2 is as fast and the clock speed drops with the 512-bit code, move the AVX2 check first in `sse_select()`.

## Floating-point caveat

Reordering floating-point additions assumes they're associative, which they aren't exactly. Results will differ from the old serial loop by rounding in the last few bits, not by a logic error.

For this function the effect is benign. Every term is ≥ 0, so nothing cancels, and the worst-case error actually gets smaller. Against a compensated reference sum, the maximum relative error was:
- old loop: 6.4e-15
- AVX2: 4.4e-16
- AVX-512 logic (emulated): 2.2e-16

The results are not bit-identical, though:
- Golden tests that compare exact values need a tolerance.
- AVX-512 and AVX2 machines will differ from each other in the last bits.
- If you need bit-exact reproducibility with the old code, you can't use this optimization.

## Verify on the Xeon

```bash
gcc -O2 -g -march=native -o sse_check sse_check.c -lm && ./sse_check   # correctness + timing of every variant
# rebuild fitbench as usual, then:
perf stat -e task-clock,cycles,instructions -- ./fitbench --points 100000 --iters 200000
perf stat -e fp_arith_inst_retired.scalar_double,fp_arith_inst_retired.256b_packed_double,fp_arith_inst_retired.512b_packed_double \
    -- ./fitbench --points 100000 --iters 200000
perf record -- ./fitbench --points 100000 --iters 200000 && perf annotate --stdio -s sse_avx512
```

What you should see:
- **Cycles:** down roughly 8–15×.
- **FP counters:** `scalar_double` falls from about 6×10¹⁰ to near 0, and `512b_packed_double` is about 7.5×10⁹. Both count an FMA as two operations.
- **perf annotate:** the time is spread across the four FMA/subtract/load groups instead of 90% on one instruction.
- **Clock speed:** the GHz figure in `perf stat` should stay roughly the same.

## What I could and couldn't check here

I worked on an Apple Silicon Mac, so there was no `perf`, no GCC that targets x86, and no AVX-512 hardware.

- **AVX2 and plain-C paths:** I built them for x86-64 with Clang and ran them under Rosetta. They passed all three checks in `sse_check.c`:
  - exact sums on integer data for every length from 0 to 300 plus large lengths, at 8 different start alignments;
  - a guard page placed right after the arrays (no reads past the end);
  - a worst-case rounding-error bound.

  The checks also caught all three bugs I injected on purpose.
- **AVX-512 path:** I compiled it for Sapphire Rapids and for a generic target. The disassembly shows four independent `vfmadd231pd` zmm chains, the zero-masked load for the last elements, `vzeroupper`, and only AVX-512F instructions. Its logic passed the same tests through a plain-C emulation of the intrinsics. **It has not run on real AVX-512 hardware, so run `./sse_check` on the Xeon before relying on it.**
- **Timings:** these are only indicative, because Rosetta translates x86 to ARM. The AVX2 path was 5.7× faster than the original FMA loop at n = 100k. The plain-C 4-accumulator version alone was 3.8× faster (GCC 14, native arm64).

## If you'd rather not maintain intrinsics

Put `#pragma omp simd reduction(+:s)` above the original loop and compile with `-fopenmp-simd`. That allows reordering for this one loop; don't switch on `-ffast-math` globally. GCC then vectorizes the loop but keeps a single vector accumulator, so it's still one chain, just 4–8 lanes wide. Expect roughly 4–8× rather than about 10×, and add `-mprefer-vector-width=512` to get zmm. Clang splits it into four accumulators on its own.

Files are in the working directory:
- **`residuals.c`**: rewritten.
- **`sse_check.c`**: new test and benchmark harness. It `#include`s `residuals.c` and keeps your original loop as the baseline.

---
¹ The annotated loop is 6 instructions, but your counters work out to about 5 per element. The stat and annotate runs may have come from slightly different builds. Either way it's 5–6 instructions per 4-cycle step, so the conclusion doesn't change.
