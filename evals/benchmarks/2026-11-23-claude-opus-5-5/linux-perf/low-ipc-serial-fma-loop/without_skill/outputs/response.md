The loop isn't waiting on memory. It's waiting on itself. `s += r*r` makes every iteration depend on the one before, so the compiled loop is a single chain of dependent FMAs. It can't go faster than one element per FMA latency, which is 4 cycles on Sapphire Rapids. Five or six instructions every 4 cycles is your 1.2 IPC.

The fix is to sum into several independent vector accumulators. I've rewritten `residuals.c` that way. It keeps the same signature and the same `gcc -O2 -g -march=native` build line, and doesn't need `-ffast-math`.

## What's wrong

These are the hot instructions from your annotate:

```
vmovsd      (%rdi,%rax,8),%xmm1        # obs[i]              independent per i
vsubsd      (%rsi,%rax,8),%xmm1,%xmm1  # r = obs[i]-pred[i]  independent per i
vfmadd231sd %xmm1,%xmm1,%xmm0          # s = r*r + s         needs the previous s  <- 90% of samples
```

`xmm0` holds `s`, and it is both an input and the output of every FMA. So each FMA has to wait 4 cycles for the previous one to finish. The out-of-order core runs the loads, the subtract and the loop counter far ahead, and then they sit waiting. That's why the samples pile up on the FMA. Your counters fit this exactly:

- **Elements processed:** 20.2 G branches is about one `jne` per element, so 2×10¹⁰ elements (100k points × 200k iterations).
- **Cycles per element:** `sse` gets 96.4% of 81.4 G cycles, which is 78.5 G cycles, or **3.9 cycles per element**. The FMA latency is 4.
- **IPC:** 5 to 6 instructions per 4 cycles gives 1.25 to 1.5. You measured 1.24.
- **llvm-mca:** its Sapphire Rapids model, run on your exact loop, gives 4.0 cycles per iteration and names the register dependency as the bottleneck. With no dependency the loop would take 1.0 cycle per iteration, so most of the core is idle.

The low cache-miss rate is real but beside the point. The loop only asks for 16 bytes every 4 cycles (about 4 B/cycle), and any cache level can supply that.

Also, on Intel, perf's generic `cache-references` and `cache-misses` events only count the last-level cache (LLC). Here they show that the 1.6 MB working set (2 × 100k × 8 B) stays in the 2 MB per-core L2. They tell you nothing about L1 or L2 behaviour.

### Why the compiler doesn't fix it

C requires the additions to happen in source order, and floating-point addition isn't associative. Without `-ffast-math` (or `-fassociative-math`), neither GCC nor Clang is allowed to split `s` into partial sums or vectorize the reduction. Clang at `-O2` unrolls the loop by 8, but it is still one chain.

Your binary has the same instruction sequence GCC 11.1–12.2 produce for `-march=sapphirerapids`. GCC 12.3 and later use a separate multiply and add there instead (controlled by `--param avoid-fma-max-bits`). On this core the adder has shorter latency than the FMA: uops.info measures 2 cycles vs 4 on client Golden Cove, and LLVM's model uses 3. That's a modest gain, but it is still one serial chain.

## The fix (applied to `residuals.c`)

The new loop uses four independent accumulators, each a 4-wide vector (GCC/Clang vector extensions). Four FMAs are in flight at once, and each one covers 4 elements:

```c
for (; i + 4 * VL <= n; i += 4 * VL) {
    vdouble r0 = vload(obs + i)          - vload(pred + i);
    vdouble r1 = vload(obs + i + VL)     - vload(pred + i + VL);
    vdouble r2 = vload(obs + i + 2 * VL) - vload(pred + i + 2 * VL);
    vdouble r3 = vload(obs + i + 3 * VL) - vload(pred + i + 3 * VL);
    a0 += r0 * r0;  a1 += r1 * r1;  a2 += r2 * r2;  a3 += r3 * r3;
}
```

After the loop comes a horizontal sum, then a scalar loop for the last 15 or fewer elements. `VL` is 4 (ymm registers) when `__AVX__` is defined and 2 otherwise, so the accumulators always stay in registers. I added that switch after seeing GCC keep 32-byte vectors on the stack on targets that don't have them (arm64 and baseline x86-64).

I compiled it on Compiler Explorer with `gcc -O2 -g -march=sapphirerapids`:
- **GCC 11.4, 12.2, 13.4, 14.4 and 16.2** all produce the intended loop: 16 elements per iteration, 8 ymm loads, 4 `vsubpd` and 4 independent `vfmadd231pd` chains.
- **Warnings:** there are none with `-Wall -Wextra`, or with `-pedantic` in C99/C11 modes.
- **Clang 21** produces the same loop.

**What to expect on the Xeon.** These are estimates; I had no Sapphire Rapids machine to run on.
- With the data in L1, llvm-mca's model gives 4.0 cycles per 16 elements. That's 0.25 cycles per element, against 4 now, at about 4 IPC.
- At n = 100k, the 1.6 MB of input comes from L2 on every call, so L2 bandwidth becomes the limit. That works out to roughly 0.3–0.5 cycles per element (16 B per element at about 30–50 B/cycle).
- So expect **`sse()` to run about 7–12× faster**, and `fitbench` to drop from 21.7 s to roughly 2.5–4 s.
- Judge the result by time or cycles per element, not IPC. Once the loop is L2-bound its IPC may settle around 2–3, while doing about 10× more work per cycle.

**Numerics.** Once n reaches one main-loop step (16 elements with AVX), the result is no longer bit-identical to the old loop, because the summation order changed. The new order is fixed in the source, so it is deterministic and doesn't change with optimization level (only FMA contraction can move the last bit, as before). It is also more accurate. I compared both versions against a reference that computes each square exactly and sums with error compensation. The maximum relative error was 1.4e-15 for the new code and 1.7e-14 for the original. If anything compares these sums bit-for-bit against old values, re-baseline it.

## What I verified (on an Apple M5 Mac, not a Xeon)

**Correctness:** `bench/bench.c` checks:
- 85 sizes (0–70, sizes around multiples of the vector width, 99,999 to 100,001, and others), each at 4 pointer alignments
- that n = 0 returns +0.0
- that results match the original bit-for-bit when n < 8
- that NaN and Inf propagate
- an error bound against the accurate reference

It passed with **0 failures** in four builds: GCC 14 and Clang on native arm64, plus x86-64 builds with AVX2+FMA and with SSE2 only, run under Rosetta 2.

**Speed** at n = 100k, as a sanity check only (these are not Sapphire Rapids numbers):

| Build | Speed-up |
|---|---|
| GCC 14, arm64 (0.84 → 0.13 ns per element) | 6.5× |
| Clang, arm64 | 3.8× |
| x86-64 AVX2 under Rosetta | 5.8× |

The raw output is in `bench/results-mac.txt`.

## Alternatives, and why they're second best

- **`-O3 -ffast-math`, or `#pragma omp simd reduction(+:s)` with `-fopenmp-simd`:** GCC does vectorize the loop, but with only one vector accumulator. That's still one 4-cycle chain, just with 4 elements per step: about 1 cycle per element, roughly 4× faster (confirmed by GCC 12's output and llvm-mca). `-ffast-math` also changes behaviour for the whole program: it assumes there are no NaNs or Infs, and an executable linked with it starts with denormals flushed to zero.
- **Clang `-O2 -ffast-math`:** produces the same 4-accumulator loop as the fix, but with the same fast-math caveats.
- **AVX-512:** I kept 256-bit vectors on purpose. At n = 100k you're L2-bound either way, GCC itself prefers 256-bit on Sapphire Rapids, and with glibc's usual placement of large blocks (16 bytes past a 64-byte boundary) every 64-byte load would split a cache line. Try `vector_size(64)` only if your real n is small enough to stay in L1 (48 KB, about 3k points).

## To confirm on the box

```sh
make -C bench && ./bench/bench     # correctness check, then original vs fixed: ns/elem, cycles/elem, speed-up
perf stat -e cycles,instructions ./fitbench --points 100000 --iters 200000
```

In `perf annotate`, the samples should now spread across the loads and `vsubpd` instead of piling onto one FMA.

If you want more speed after this:
- If `pred` is computed just before `sse()` (in `update_model`, say), computing the residuals in that same loop avoids reading 1.6 MB back from L2 on every step.
- Allocate `obs` and `pred` 64-byte aligned (`aligned_alloc(64, …)`). glibc `malloc` typically places large blocks 16 bytes past a 64-byte boundary, so half of the 32-byte loads split a cache line.

One oddity in your data: the annotated loop is 6 instructions, but the stat counts work out to about 5 per element (100.8 G / 20 G). So the two runs may not have used exactly the same binary. Either way the cost is about 4 cycles per element.

## Files

All paths are under `<work>/`:
- **`residuals.c`:** the fix, a drop-in replacement.
- **`bench/`:** `Makefile`, `bench.c` (tests and timing), `sse_orig.c` (your original loop, renamed), `sse_vec1.c` (the one-accumulator version, for comparison) and `results-mac.txt`.
- **Scratch from my checks, safe to delete:** `tools/`, `mca/`, `variants/`, `ce-out/`, `web/`, `bench/build-*/` and `bench/bench-x86_64-rosetta`. I couldn't delete these myself because a safety check blocked the removal. The build directories hold Mac binaries, so there's no need to copy them to the Xeon.

The x86 GCC builds and llvm-mca runs went through Compiler Explorer's public API, so the `sse()` source was sent to godbolt.org.
