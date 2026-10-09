<!-- (C) 2026 Intel Corporation, MIT license -->
# software-prefetch-bench — sample results

Sample output from `run-software-prefetch-bench.sh` on one server-class
CPU where the pattern applies strongly. Absolute numbers change from
machine to machine; the qualitative story to check on any target is
whether the sweep is *flat* (pattern does not apply here) or *shaped*
(pattern applies — pick the flat maximum of the valley).

## Where it works very well: shaped sweep

Command: `./software-prefetch-bench 256 64 3` — 256 MiB arena of 64 B
records inside a batched producer-consumer scan (1024-row batches,
consumer does one pseudo-random probe per row into an 8 MiB table).

      prefetch_N      ns/row      vs N=0
      ----------  ----------  ----------
               0        7.54       +0.0%
            1024        7.14       -5.3%
            2048        6.32      -16.3%
            4096        5.52      -26.9%
            8192        5.52      -26.8%

- The hardware prefetcher does not fully cover this loop shape here;
  a fixed-lookahead software hint recovers cycles.
- The valley flattens between N = 4 KiB and 8 KiB — either is a good
  operating point on this target. For production, sweep the actual
  caller under real row widths and pick the flat maximum.
- **Expected gain in this regime: 10–30 %** on the scan region.

## Where the pattern does not apply: flat sweep

If every `vs N=0` row is within ~2 % of zero, the target CPU's
hardware prefetcher already covers this loop shape. **Do not add the
hint.** The `#if defined(__x86_64__)` guard is not enough on its own
— it only bounds *where* the hint compiles, not *whether* it helps.

Common causes of a flat sweep to check before concluding the pattern
does not apply:

- The working set fits in the last-level cache. Raise `arena_mib`
  well past the L3 size and re-run.
- The consumer is far more expensive than the scan step. If the scan
  is a small share of total cycles the hint has little room to move
  the total; verify with `perf record` that the scan symbol is a
  meaningful cycle share first.

## The counters confirm the hint is doing work

Command: `perf stat -e cycles,instructions,cache-references,cache-misses -- ./software-prefetch-bench 256 64 2 <mode>`

    N=0 (noprefetch):
      8.0 G cycles  |  10.6 G instructions  | IPC 1.33
      752 M cache-refs  |  752 M cache-misses  |  2.24 s
      ns/row 6.64

    N=2048 (prefetch):
      7.2 G cycles  |  11.6 G instructions  | IPC 1.61
      799 M cache-refs  |  799 M cache-misses  |  2.22 s
      ns/row 6.21

- **Cycles drop** (8.0 G → 7.2 G, -10 %) while retired instructions
  *rise* (10.6 G → 11.6 G) — the hint retires as a `prefetcht0`
  instruction per iteration, but overlapping the DRAM fetch pays for
  it many times over.
- **IPC rises** 1.33 → 1.61 for the same reason: the core spends
  fewer cycles stalled on the first load of each record.
- **Cache-miss count rises slightly** because a prefetch that is
  never later consumed still counts as a miss. This is expected and
  is why the wall-clock number, not the miss count, is what you use
  to judge a prefetch fix.

## `perf annotate` — the header-byte load's cycle share drops

Command: `perf record -F 4000 --call-graph fp -- ./software-prefetch-bench 256 64 2 <mode>` then `perf annotate scan_and_consume`

Noprefetch (`scan_and_consume` is 91.87 % of total samples; local
percentages within the function):

       3.95 :   cmp    %rcx,%r10
      36.81 :   testb  $0x1,(%rax)          <-- header-byte load
       3.56 :   lea    0x1(%rdx),%esi
       ...
      35.47 :   xor    (%r8,%rax,8),%rsi    <-- consumer's probe load

The scan's header-byte load and the consumer's random probe each
carry ~35 % of local samples — the scan is on the critical path.

Prefetch, N=2048 (`scan_and_consume` is 90.76 % of total samples):

       4.89 :   cmp    %rcx,%r10
      24.08 :   prefetcht0 0x0(%r13,%rax,1)  <-- the hint's retirement
       8.78 :   testb  $0x1,(%rax)           <-- header-byte load
       ...
      36.49 :   xor    (%r8,%rax,8),%rsi     <-- consumer's probe load

The header-byte load drops from 36.81 % → 8.78 % of local samples —
the fetch has been overlapped away. The `prefetcht0` at 24 % is not
a stall; that is just where the retirement of the hint instruction
itself lands in the sample stream. The consumer's probe is now the
remaining bottleneck.
