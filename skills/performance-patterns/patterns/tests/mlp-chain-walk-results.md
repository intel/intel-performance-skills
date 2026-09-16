<!-- (C) 2026 Intel Corporation, MIT license -->
# mlp-chain-walk-bench — sample results

Sample output from `run-mlp-chain-walk-bench.sh` on one server-class
CPU. Absolute numbers vary by machine (DRAM latency, outstanding-miss
capacity, cache sizes); the shape of the sweep and the direction of
the counters is what to check on any target.

## Where it works very well: DRAM-resident chains

Command: `./mlp-chain-walk-bench 256 100` — 256 MiB per chain, well
past L3, every step is a real DRAM miss.

         W  serial ns/step  interlv ns/step     speedup
     -----  --------------  --------------  ----------
         1           97.54           99.11       0.98x
         2           99.58           57.27       1.74x
         4          100.47           31.71       3.17x
         8          100.13           18.40       5.44x
        16          100.38           17.93       5.60x

Both modes visit `total_steps` nodes total (serial: one chain, W×
more per-cursor steps; interleaved: W chains, W× fewer per-cursor
steps). The comparison is equal-work, so `ns/step` is a per-node
cost and the speedup column is a clean per-node ratio.

- Serial ns/step is roughly constant across rows — this is the flat
  DRAM load-to-use latency of one dependent miss on this target.
- Interleaved ns/step approaches serial/W at small W and plateaus
  once W exceeds the core's outstanding-miss capacity (between W=8
  and W=16 here).
- **Expected gain in this regime: 4×–8×** on cores whose capacity
  is at least 8.

## Where it still helps but less: cache-resident chains

Command: `./mlp-chain-walk-bench 1 200` — 1 MiB per chain, so 8
chains × 1 MiB fits in L3 and there is no DRAM latency to hide.

         W  serial ns/step  interlv ns/step     speedup
     -----  --------------  --------------  ----------
         1            7.70            9.99       0.77x
         2            7.66           14.42       0.53x
         4            8.75            7.77       1.13x
         8            8.04            4.24       1.89x
        16            7.51            3.38       2.22x

Serial ns/step is ~8 ns — this is L1D-hit territory. There is nothing
for MLP to overlap, so the win collapses from 5.4× to ~2× residual.
The residual comes from the interleaved loop absorbing more
instructions per iteration across the core's execution ports; it is
not the memory-parallelism win the pattern is about.

- **Rule of thumb: if the interleaved gain on the target workload
  looks like this row rather than the previous section's row, the
  walk is not memory-bound and this pattern is not the right fix.**

## The counters confirm it is overlap, not caching

Command: `perf stat -e cycles,instructions,cache-references,cache-misses -- ./mlp-chain-walk-bench 256 50 <mode>`

    W=1  (serial):
      15.1 G cycles  |  0.79 G instructions  | IPC 0.05
      72.5 M cache-refs  |  51.3 M cache-misses  |  5.06 s

    W=8  (interleaved):
       7.2 G cycles  |  4.67 G instructions  | IPC 0.65
      211  M cache-refs  |  94.7 M cache-misses  |  2.39 s

The cache-miss *ratio* moves (71 % → 45 %) but that is code-mix, not
mechanism — the interleaved loop has cursor-array bookkeeping loads
the serial version does not. The absolute miss count also moves;
that too is a caching-side effect (K chains resident concurrently
change LLC replacement).

**Cycles-per-miss is the direct MLP signature.** 295 → 75 cyc/miss.
Nothing about caching can drive that ratio down — MLP overlaps the
same misses so each miss contributes less to the critical path.

If a reviewer asks "isn't this just caching?", the equal-work run
answers it: `./mlp-chain-walk-bench 256 800 <mode>` (both modes
visit 800 M nodes) reports **more** absolute misses for interleaved
(843 M) than for serial (706 M), yet interleaved is 4.75× faster.
A caching mechanism cannot be faster with more misses; only overlap
can.

## `perf report` — the hot line

Command: `perf record -F 4000 --call-graph fp -- ./mlp-chain-walk-bench 256 50 <mode>` then `perf annotate <symbol>`

Serial: `walk_serial` is 96 % of samples; within it, `mov (%rdi),%rdi`
holds 99 % of local samples. One dependent load is the whole
critical path.

Interleaved: `walk_interleaved` is 39 % of total samples; within it,
`mov (%rcx),%rcx` still holds 92 % of local samples — the same
dependent load, still the local hotspot, but the whole function now
runs so much faster that its share of total samples has dropped and
the fixed setup cost (chain construction) has become visible for
the first time. That drop is where the wall-clock gain lives.
