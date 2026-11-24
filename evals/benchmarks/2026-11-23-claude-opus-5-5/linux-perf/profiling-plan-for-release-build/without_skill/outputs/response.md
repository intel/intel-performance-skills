# Profiling `ingest`: commands in order

**How it goes:**
- **Steps 2–3:** find out whether the 40 s is CPU work or waiting.
- **Step 4:** record CPU call stacks.
- **Steps 5–6:** read them, from coarse to fine.
- **Step 7:** only if it's waiting, profile the waiting.
- **Step 8 (optional):** ask the CPU why the hot code is slow.

Ground rules:
- Run everything from the repo root, on an otherwise quiet box. Results go into `prof/`.
- **Measure with `build/`** (your real binary). **Profile with `build-prof/`.**
- Expect 5–8 runs of about 40 s each, plus one extra build.

I checked the CMake and perf commands, and what their output looks like, in an Ubuntu 24.04 container (perf 6.8.12, GCC 13.3, CMake 3.28) using a small multi-threaded stand-in program. Some things couldn't run there:
- anything needing real Xeon hardware (hardware counters, LBR, Top-down metrics);
- anything needing your kernel's headers (BCC's `offcputime`).

## 0. Setup (once)

```bash
mkdir -p prof
sudo apt update
sudo apt install -y linux-tools-common linux-tools-$(uname -r) libc6-dbg git perl
sudo apt install -y bpfcc-tools linux-headers-$(uname -r)     # only needed for step 7b
perf --version
```
- **Expected:** `perf version 6.8.x`, or newer on an HWE kernel.
- **`WARNING: perf not found for kernel …`:** the tools package for your running kernel is missing. Install the package it names.
- **Don't skip `libc6-dbg`.** Without it, Ubuntu's libc internals show up as bare hex addresses instead of names like `_int_malloc`, `__memmove_avx_unaligned_erms` and `__lll_lock_wait`. perf finds the symbols automatically, even for data you've already recorded.

```bash
sysctl kernel.perf_event_paranoid kernel.kptr_restrict | tee prof/00-sysctl-before.txt
sudo sysctl -w kernel.perf_event_paranoid=-1 kernel.kptr_restrict=0
```
- Ubuntu restricts perf for normal users. This lifts the restriction, and makes kernel symbols readable, until you reboot or run step 9.
- It lets any local user profile anything, which is fine on a benchmark box for one session.
- Profiling as yourself means `ingest` runs exactly as in your benchmark, and the files stay yours.

```bash
systemd-detect-virt
lscpu | grep -E 'Model name|^CPU\(s\)|Thread\(s\) per core'
perf stat -e cycles,instructions -- sleep 1
```
- **`systemd-detect-virt`:** `none` means bare metal; anything else is a VM.
- **`lscpu`:** gives the CPU count (needed for step 2) and the Xeon generation (matters for step 8).
- **`perf stat`:** real counts for `cycles` and `instructions` mean the hardware counters work.
- **`<not supported>`:** you're in a VM without a virtual PMU. Steps 1–7 still work, because perf silently switches to a timer. You lose IPC, step 8 and fallback A.

## 1. Build a profiling copy: same `-O3 -DNDEBUG`, plus symbols and frame pointers

```bash
FLAGS="-g -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer"
cmake -S . -B build-prof -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
      -DCMAKE_C_FLAGS="$FLAGS" -DCMAKE_CXX_FLAGS="$FLAGS"
cmake --build build-prof -j"$(nproc)"
grep -m1 '"command"' build-prof/compile_commands.json
file build-prof/bin/ingest
```
- **Compile line:** must contain `-g -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer -O3 -DNDEBUG`. CMake puts the Release flags after yours. Nothing later on the line should override them, such as another `-O…` or `-fomit-frame-pointer` from your CMakeLists.
- **`file`:** must say `with debug_info, not stripped`.
- **Warning `Manually-specified variables were not used by the project: CMAKE_C_FLAGS`:** harmless; it only means the project has no C sources.
- **Why not `RelWithDebInfo`:** it uses `-O2`, so you'd be profiling a different binary. `-g` doesn't change the generated code. Frame pointers typically cost about 1% and give perf cheap, reliable call stacks.

## 2. Baseline: CPU-bound or waiting? Is the profiling build representative?

```bash
ls -lh traces/day1.bin; free -h
cat traces/day1.bin > /dev/null                  # pre-load the trace into the page cache
perf stat -o prof/02-stat-release.txt -- ./build/bin/ingest      --replay traces/day1.bin > prof/run-release.log
perf stat -o prof/02-stat-prof.txt    -- ./build-prof/bin/ingest --replay traces/day1.bin > prof/run-prof.log
cat prof/02-stat-release.txt prof/02-stat-prof.txt
```
The program's stdout goes to `prof/run-*.log` so it doesn't mix with perf's output.

This output decides which of the later steps matter.

- **`seconds time elapsed`:** the two builds should agree within about 3%. If `build-prof` is more than 5% slower, profile with fallback A instead.
- **`CPUs utilized`** (CPU time divided by wall time) is the key number:
  - **About equal to the number of threads you expect to be busy** (about 1.0 for a single-threaded loop): the service is CPU-bound, and steps 4–6 will account for the 40 s.
  - **Clearly below that** (for example 0.6 single-threaded, or 1.3 with 8 workers): much of the 40 s is spent **waiting** on disk, locks or sleeps. A CPU profile can't see waiting, so step 7 matters as much as step 4.
- **`seconds sys` above ~10% of user+sys:** kernel work (syscalls, page faults) is significant. Expect `[kernel.kallsyms]` near the top in step 5.
- **`page-faults` at tens of thousands per second or more:** memory is being freshly mapped and zeroed over and over, for example big buffers freed and re-allocated.
- **`context-switches` above ~10K/sec:** threads block and wake constantly, from a contended lock or a queue handoff per item.
- **`insn per cycle` below ~1:** the core is mostly stalled, usually on cache misses. **Above ~2:** the code runs efficiently, so speedups have to come from doing less work.
- **`branch-misses` of several %:** note it. Step 8 tells you whether it actually costs time.
- **Trace larger than `available` in `free -h`:** every run reads from disk, so expect waiting time.
- **`tma_…` lines in the output:** keep them for step 8. Newer perf prints them on many Intel CPUs.

## 3. Which threads are busy (skip if `ingest` is single-threaded)

```bash
./build/bin/ingest --replay traces/day1.bin > /dev/null &
PID=$!; sleep 1
perf stat --per-thread -e task-clock,context-switches -p "$PID" -o prof/03-threads.txt   # returns when ingest exits
cat prof/03-threads.txt
```
- **One row per thread** (`name-TID`).
- **`CPUs utilized` about 1.0:** that thread is saturated. In a pipeline it's the bottleneck that everything else waits on. Note its name for step 5e.
- **Threads far below 1.0:** they mostly wait. A very high context-switch count on them points at a lock or a handoff.
- **Why the background run:** `--per-thread` only works when perf attaches with `-p`. Threads started after the 1 s attach are counted in their parent's row.

## 4. Record the CPU profile

```bash
perf record -F 999 --call-graph fp -o prof/perf.data -- ./build-prof/bin/ingest --replay traces/day1.bin > prof/run-record.log
```
- **End of the output:** `[ perf record: Captured and wrote … MB prof/perf.data (… samples) ]`. Expect tens of thousands of samples per busy thread. About 1 kHz per thread is plenty and costs a few %.
- **Warnings about lost samples:** rerun with `-F 499`.
- **Warnings about restricted kernel maps:** step 0's sysctl isn't in effect.

## 5. Read the profile, coarse to fine

Each command takes seconds and writes a text file; keep them for step 9. C++ template names are extremely wide, hence the `cut`. A trailing "Cannot load tips.txt" line is harmless.

**5a. By library**
```bash
perf report -i prof/perf.data --no-children --sort dso -g none --stdio > prof/05a-dso.txt
grep -v '^#' prof/05a-dso.txt | head -15
```
What each entry covers:
- `ingest`: your code plus all the STL and templates inlined into it.
- `libc.so.6`: malloc/free, memcpy/memmove, strtod, printf.
- `libstdc++.so.6`: strings, streams, locale.
- `[kernel.kallsyms]`: syscalls, page faults, futexes.

A large libc or kernel share is a finding in itself.

**5b. Self time per function (the "where" list)**
```bash
perf report -i prof/perf.data --no-children --sort dso,symbol -g none --percent-limit 0.5 --stdio > prof/05b-self.txt
grep -v '^#' prof/05b-self.txt | cut -c1-200 | head -40
```
- **Concentrated** (a handful of entries above 50%): those are your targets.
- **Flat** (top entry below 5%): per-record overhead is spread everywhere. 5c and the flame graph show which stage it sits under.
- **Inlining:** at -O3, an entry includes everything inlined into it. 5f shows which lines inside it are hot.
- **Names:** the cheat sheet at the end explains common ones. Bare hex addresses in a library mean perf has no symbols for that library.

**5c. Inclusive time per function (which stage)**
```bash
perf report -i prof/perf.data --children --sort symbol -g none --percent-limit 1 --stdio > prof/05c-inclusive.txt
grep -v '^#' prof/05c-inclusive.txt | cut -c1-200 | head -60
```
- **`Children`:** the function plus everything it calls. **`Self`:** its own code only.
- **Skip the rows near 100%** (`_start`, `main`, `start_thread`, `clone3`). Below them are your stages (read, decode, parse, apply, flush …) with their share of all CPU time.
- **Children much larger than Self:** the cost is in what the function calls.

**5d. Who calls the hot functions**
```bash
perf report -i prof/perf.data --no-children --sort dso,symbol --percent-limit 2 --stdio > prof/05d-callers.txt
cut -c1-200 prof/05d-callers.txt | less
```
- **Under each hot function:** the call chains that led to it, split by %. This is how you find which call site causes the `malloc`, `memmove` or hash-lookup time.
- **Sanity check:** chains should reach `main` or `start_thread`. If most stop after 1–2 frames or show `[unknown]`, unwinding is broken (for example, a prebuilt library without frame pointers). Use fallback A.
- **Known gap:** when the sampled function is a tiny leaf, or a glibc routine like `memcpy` or `pthread_mutex_unlock`, its direct caller can be missing from the chain. GCC still omits the frame in leaf functions that use no stack, even with these flags. The function itself is still credited correctly; fallback A shows the exact callers.
- **If this (or step 6) takes many minutes:** add `--no-inline`. Ubuntu's perf resolves inlined frames through `addr2line`, which is slow on big binaries.

**5e. One thread at a time (multi-threaded only)**
```bash
perf report -i prof/perf.data --no-children --sort pid -g none --stdio | grep -v '^#' | head -20
TID=12345      # <- replace with the busy thread's TID from the list above
perf report -i prof/perf.data --no-children --tid "$TID" --sort pid,dso,symbol -g none --percentage relative --stdio | cut -c1-200 | head -40
```
- **First command:** lists `TID:name` and each thread's share of samples. TIDs change on every run, so pick the step-3 bottleneck thread here by name, or as the biggest.
- **Second command:** shows what that thread spends its time on. That's your critical path.
- **Keep `pid` in `--sort` when using `--tid`.** With only `--sort dso,symbol`, perf 6.8 silently mixes in other threads' samples; I reproduced this.

**5f. Hot instructions and lines inside the top functions**
```bash
perf annotate -i prof/perf.data --stdio --dsos ingest --percent-limit 5 > prof/05f-annotate.txt
less prof/05f-annotate.txt
```
- **Layout:** one block per function with at least 5% of samples, hottest first. The left column is the % of *that function's* samples, with source lines interleaved.
- **Most samples on or just after a load through a pointer or hash result:** a cache miss.
- **On a `lock`-prefixed instruction:** atomics, for example `shared_ptr` copies.
- **On `div`:** integer division. `std::unordered_map` does `hash % bucket_count`.
- **Skid:** samples tend to land on the instruction *after* the slow one.
- **Interactive alternative:** run `perf report -i prof/perf.data --no-children`, arrow to a function, press `a`.
- **Why not `perf annotate -s NAME`:** it only matches the full demangled signature. A shorter name fails with a misleading "has no samples!" error.

## 6. Flame graph

```bash
git clone --depth 1 https://github.com/brendangregg/FlameGraph.git ~/FlameGraph
perf script -i prof/perf.data > prof/perf.script
~/FlameGraph/stackcollapse-perf.pl prof/perf.script > prof/cpu.folded
~/FlameGraph/flamegraph.pl --title "ingest on-CPU" prof/cpu.folded > prof/cpu-flame.svg
```
Copy `prof/cpu-flame.svg` to your laptop and open it in a browser.
- **Reading it:** width is the share of CPU time, and stacks grow upward from `main` or `start_thread`. Click to zoom.
  - **Wide towers:** expensive subtrees.
  - **Wide flat tops:** functions burning CPU in their own code.
- **Search** (top right, or Ctrl-F) takes a regex and shows the total as `Matched: X%` at the bottom right. Searches worth running:
  - `malloc|free|_int_|operator new|operator delete`: total allocator cost across all call sites.
  - `memmove|memcpy`
  - `futex|pthread_mutex`
  - `page_fault`

## 7. Off-CPU time (only if step 2 or 3 showed real waiting)

**7a. Which syscalls the threads wait in**
```bash
sudo -v                                      # cache sudo so a password prompt doesn't delay the attach
./build-prof/bin/ingest --replay traces/day1.bin > /dev/null &
PID=$!; sleep 1
sudo perf trace -s -p "$PID" -o prof/07a-syscalls.txt      # returns when ingest exits
less prof/07a-syscalls.txt
```
For each thread you get every syscall's `calls` and `total (msec)`. Compare the totals with the ~40 s wall time.
- **`futex` with a large total:** waiting on a mutex or condition variable. An empty queue means the thread is starved by its producer; a full one means it's blocked by its consumer.
- **Main thread with one `futex` lasting about the whole run:** it's sitting in `join()`; ignore it.
- **`read`/`pread64`:** waiting for the disk.
- **`clock_nanosleep`/`nanosleep`:** deliberate sleeping, for example the replay pacing itself to the trace's timestamps. If so, much of the 40 s is by design.
- **`epoll_wait`/`poll`:** idle. **`write`/`fsync`/`fdatasync`:** output and durability.
- **Very high counts of `mmap`/`munmap`/`mprotect`/`madvise`/`brk`:** the allocator is growing and shrinking memory.

**7b. Where in your code the waiting happens**
```bash
./build-prof/bin/ingest --replay traces/day1.bin > /dev/null &
PID=$!; sleep 1
sudo offcputime-bpfcc -df -p "$PID" 30 > prof/offcpu.folded      # 30 s: must end before ingest exits
wait "$PID"
~/FlameGraph/flamegraph.pl --color=io --countname=us --title "ingest off-CPU" prof/offcpu.folded > prof/offcpu-flame.svg
```
- **Reading it:** width is time blocked, summed over threads. Each tower has your code at the bottom, a `-` separator, then the kernel reason on top:
  - `futex_wait`: a lock or condition variable.
  - `io_schedule`/`filemap_…`: disk.
  - `hrtimer_nanosleep`: sleep.
  - `ep_poll`: epoll.
- **Idle pool threads** waiting for work also show up as wide `futex` towers. That's a symptom; the cause is whatever the thread feeding them is doing (see 5e).
- **Your frames show as `[unknown]`:** ingest exited before the 30 s were up, so use a shorter duration.
- **Why BCC:** perf's own `--off-cpu` mode isn't compiled into Ubuntu's perf.

## 8. Optional: why the hot code is slow (Intel Top-down)

```bash
perf stat -M TopdownL1,TopdownL2 -o prof/08-topdown.txt -- ./build/bin/ingest --replay traces/day1.bin > /dev/null
cat prof/08-topdown.txt
```
The L1 numbers split all of the CPU's pipeline slots into four buckets:
- **`tma_retiring` high (above ~50%):** the core is doing useful work. You need fewer instructions: less copying, allocation or parsing work, better algorithms, or SIMD.
- **`tma_backend_bound`:** check the L2 numbers.
  - `tma_memory_bound`: cache or DRAM misses from hash tables, pointer chasing or a big working set. Fix the data layout.
  - `tma_core_bound`: divides, execution-port pressure or dependency chains.
- **`tma_frontend_bound` / `tma_fetch_latency`:** instruction-cache or iTLB misses from a large hot code footprint. PGO and LTO help.
- **`tma_bad_speculation` / `tma_branch_mispredicts`:** unpredictable branches, which are typical in parsers.

If perf prints "Some events weren't counted. Try disabling the NMI watchdog", run `sudo sysctl -w kernel.nmi_watchdog=0`, rerun the command, then set it back to 1.

On older Xeons (Skylake or Cascade Lake) with Hyper-Threading, perf may require `-a` (system-wide). Use it only on an otherwise idle box.

If you want this breakdown per function, Intel VTune's microarchitecture-exploration analysis does that on Xeon.

## 9. Restore settings and collect the results

```bash
sudo sysctl -p prof/00-sysctl-before.txt
tar czf prof-results.tgz prof/*.txt prof/*.svg       # send me this if you want help reading it
```

## Fallbacks

**A. Profile the untouched release binary with hardware call stacks.** This needs a bare-metal Xeon. Use it if `build-prof` was more than 5% slower, or if the call chains look broken.
```bash
perf record -F 999 --call-graph lbr -o prof/perf-lbr.data -- ./build/bin/ingest --replay traces/day1.bin > /dev/null
```
Then rerun the step-5 commands with `-i prof/perf-lbr.data`.
- LBR records the real calls, so no frames go missing.
- It only covers the innermost ~32 user-space frames.
- Without `-g`, annotate shows assembly only.

**B. VM without a PMU.** For exact call chains, use DWARF unwinding. Files run from hundreds of MB to GBs, and reports are slower.
```bash
perf record -F 499 --call-graph dwarf -o prof/perf-dwarf.data -- ./build-prof/bin/ingest --replay traces/day1.bin > /dev/null
```

## Cheat sheet: common hot symbols

| You see | It usually means |
|---|---|
| `malloc`, `free`/`cfree`, `_int_malloc`, `_int_free`, `malloc_consolidate`, `operator new`/`delete` | Heap allocation per record (strings, vectors, map nodes). Quick test: `sudo apt install libjemalloc2`, then rerun with `LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2` |
| `__memmove_*_unaligned_erms`, `__memcpy_*` | Copying buffers, records or strings |
| `std::_Hashtable…`, `_Map_base…::operator[]`, `std::_Rb_tree…`, `std::_Hash_bytes` | Hash or tree lookups, often limited by cache misses |
| `strtod`, `strtol`, `__vfscanf_internal`, `std::num_get`, `std::basic_istream` | Number parsing on slow, locale-aware paths (`std::from_chars` is far faster) |
| `std::locale`, `std::ostream::operator<<`, `__vsnprintf_internal`, `__printf_buffer` | Formatting or logging on the hot path |
| `std::__detail::_Executor` | `std::regex` run per record |
| `__cxa_throw`, `_Unwind_*` | Exceptions used for control flow |
| `pthread_mutex_lock`, `__lll_lock_wait`, kernel `futex_*`, `try_to_wake_up` | Lock contention, or a wake-up per item |
| kernel `asm_exc_page_fault`, `clear_page_erms` | Touching freshly mapped memory (big allocations churned through mmap) |
| kernel `entry_SYSCALL_64`, `ksys_read`, `rep_movs_alternative`, `filemap_read` | Many small `read()` calls, or copying out of the page cache |
| `ZSTD_*`, `inflate`, `LZ4_*` | Decompression |

**Assumptions:**
- **Compiler:** GCC, Ubuntu's default. With clang the same flags work, and clang keeps frame pointers even in leaf functions.
- **CMake generator:** single-config (Makefiles or Ninja).
- **Binary:** your CMakeLists doesn't strip `ingest`.
- **How you run it:** as a normal user with sudo, from the repo root.
