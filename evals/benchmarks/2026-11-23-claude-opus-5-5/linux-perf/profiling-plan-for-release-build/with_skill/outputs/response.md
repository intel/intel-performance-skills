# Profiling runbook: `ingest --replay traces/day1.bin`

The plan:
1. Rebuild the same `-O3 -DNDEBUG` binary with only `-g` added, so perf can name functions and source lines.
2. Run `perf stat` to see whether the 40 s is spent on the CPU, in the kernel, or waiting.
3. Record samples with call stacks and break them down by library, function, caller and source line.
4. Annotate the hottest functions.

Two extra branches, **A** (lock or cache-line contention) and **B** (time spent waiting), are only needed if the earlier steps point at them.

Run everything from the repo root while the server is otherwise idle. Every output goes into `prof/`. It takes about 15 minutes plus the rebuild.

---

## 0. Setup: install perf, set permissions, record CPU info

```bash
sudo apt-get install -y linux-tools-common "linux-tools-$(uname -r)"
perf --version
mkdir -p prof
sysctl kernel.perf_event_paranoid kernel.kptr_restrict | tee prof/sysctl-before.txt
sudo sysctl -w kernel.perf_event_paranoid=1 kernel.kptr_restrict=0    # temporary; step 7 restores it
perf stat -e cycles,instructions -- true
lscpu > prof/lscpu.txt
grep -m1 '^flags' /proc/cpuinfo | tr ' ' '\n' | grep -E '^(avx2|fma|avx512f|avx512bw|avx512vl|avx512_vnni|amx_tile)$' | sort | tee prof/cpu-flags.txt
```

What to look for:
- **`perf --version`** should print a version. "WARNING: perf not found for kernel …" means the tools package doesn't match the running kernel. This is common with `-aws`, `-azure` or HWE kernels; install `linux-tools-$(uname -r)` for exactly that kernel.
- **The saved sysctl values:**
  - Ubuntu kernels set `perf_event_paranoid` above 2 (usually 4), which blocks perf for non-root users entirely. A value of `1` lets you profile your own processes, user and kernel side, without sudo.
  - `kptr_restrict=0` makes kernel frames show real names instead of `[unknown]`.
  - I chose this over running everything under sudo so the service runs as you and can't leave root-owned output files in the repo. If you'd rather not change sysctls, put `sudo` in front of every `perf` command instead.
- **The smoke test** must print real numbers for `cycles` and `instructions`. `<not supported>` means the machine doesn't expose hardware counters (typical for a VM). In that case IPC, TopDown and c2c won't work, but sampling still does (perf falls back to `cpu-clock`).
- **CPU flags** are for step 6. `avx512f` means 512-bit `zmm` vector code is possible; `avx2` alone means 256-bit `ymm`.

## 1. Build a profiling copy: same Release flags plus `-g`

```bash
cmake -S . -B build-prof -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CXX_FLAGS="-g" -DCMAKE_C_FLAGS="-g" -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build build-prof -j"$(nproc)"
```

CMake puts `CMAKE_CXX_FLAGS` in front of the Release flags, so every compile and link line becomes `-g -O3 -DNDEBUG`. I checked this on a stand-in CMake project.

`-g` only adds debug-info (DWARF) sections. GCC and Clang don't change the generated code for it, so the profile shows your real binary.

Don't use `RelWithDebInfo` for this. It is `-O2 -g`, so you would be profiling a different binary.

A warning like "Manually-specified variables were not used … CMAKE_C_FLAGS" only means the project has no C sources.

## 2. Check the profiling build

```bash
grep -E '^CMAKE_CXX_FLAGS(_RELEASE)?:' build/CMakeCache.txt build-prof/CMakeCache.txt
grep -m1 '"command"' build-prof/compile_commands.json
file build-prof/bin/ingest
readelf -SW build-prof/bin/ingest | grep -E '\.debug_(info|line)'
time ./build-prof/bin/ingest --replay traces/day1.bin
```

What to look for:
- **The two caches** should have the same `CMAKE_CXX_FLAGS_RELEASE` (`-O3 -DNDEBUG`). The only difference in `CMAKE_CXX_FLAGS` should be the added `-g`. If your normal `build/` has extra flags there (for example from a `CXXFLAGS` environment variable), redo step 1 with `-DCMAKE_CXX_FLAGS="<those flags> -g"`.
- **The compile command** should contain `-g … -O3 -DNDEBUG`. If `-g` is missing, your CMakeLists overwrites `CMAKE_CXX_FLAGS`. In that case, reconfigure with `-DCMAKE_CXX_FLAGS_RELEASE="-O3 -DNDEBUG -g"` instead.
- **`file`** should say `with debug_info, not stripped`, and `readelf` should list `.debug_info` and `.debug_line`. If it says `stripped`, the project links with `-s` or strips the binary after building. Turn that off for `build-prof`, otherwise perf can only show raw addresses.
- **`real`** should be close to your usual 40 s. That confirms the profiling binary behaves like the normal one. This run also loads `traces/day1.bin` into the OS file cache. If it was slower because the trace had to come from disk, run it once more.

## 3. Counters: is the time on the CPU, in the kernel, or waiting? (`perf stat`)

```bash
perf stat -e task-clock,context-switches,cpu-migrations,page-faults,major-faults,cycles,instructions,branches,branch-misses,cache-references,cache-misses \
    -o prof/perf-stat.txt -- ./build-prof/bin/ingest --replay traces/day1.bin
cat prof/perf-stat.txt
# optional (+40 s): which part of the CPU pipeline is the bottleneck; skip if perf says the metric group doesn't exist
perf stat -M TopdownL1 -o prof/perf-topdown.txt -- ./build-prof/bin/ingest --replay traces/day1.bin
```

| Look at | What it means | Rule of thumb |
|---|---|---|
| `task-clock … CPUs utilized` | average number of cores busy | About 1 for a single-threaded replay, or about the number of busy worker threads, means the run is CPU-bound and steps 4–6 explain the 40 s. Clearly lower means part of the time is spent waiting, which a CPU profile can't see. In that case also do **B**. |
| `seconds user` vs `seconds sys` | time in your code vs in the kernel | sys above 30 % of user+sys means system calls, I/O or page faults dominate. Expect `[k]` (kernel) symbols in step 5. |
| `context-switches` (per second) | how often threads block and wake | Thousands per second or more means threads block a lot (locks, condition variables, small I/O). Do **B**, and maybe **A**. |
| `page-faults`, `major-faults` | new memory being mapped / reads from disk | `major-faults` above 0 on a warm run means the trace is still read from disk. Around 100 K faults per second or more means memory is freed and re-faulted (allocator churn). |
| `insn per cycle` (IPC) | how well each core is used | Below 1 means stalled (waiting on memory, or each step waits on the previous result). 1–2.5 is typical. Above 3 is efficient, and only doing less work will help. |
| `branch-misses … of all branches` | mispredicted branches | Above 3–5 % means branch-heavy code (data-dependent branches in parsing or dispatch). |
| `cache-misses … of all cache refs` | last-level cache miss ratio | Above 3–5 % *together with* IPC below 1 means memory-bound. On Xeon this ratio is often high even for healthy code, because only L2 misses reach the last-level cache. So also compute misses per 1000 instructions: `cache-misses ÷ instructions × 1000`. Below 1 is low; above 10 means heavy memory traffic. |
| `(xx.xx%)` at the end of a line | counters were time-shared | Fine for a steady 40 s run. |

If the TopDown run worked, the largest `tma_*` value tells you what limits the CPU:
- **`tma_retiring`** (50 % or more): the core is doing useful work, so only executing fewer instructions will help (a better algorithm, or SIMD).
- **`tma_bad_speculation`** (10–15 % or more): branch mispredictions.
- **`tma_frontend_bound`** (20–25 % or more): the CPU can't fetch instructions fast enough (instruction cache or iTLB misses). This is common in big C++ binaries; profile-guided optimisation (PGO), LTO or BOLT help.
- **`tma_backend_bound`** (30–40 % or more): stalls waiting for data or busy execution units. `perf stat -M TopdownL2 …` splits this into memory-bound and core-bound.

## 4. Record a profile with usable call stacks

```bash
perf record -F 499 --call-graph dwarf -o prof/perf-cpu.data -- ./build-prof/bin/ingest --replay traces/day1.bin
```

- **Why DWARF:** `-O3` drops frame pointers, so `perf record -g` (which follows frame pointers) would give broken stacks. `--call-graph dwarf` rebuilds the stacks from the `-g` debug info, with no code change.
- **File size:** this mode copies 8 KB of stack per sample. At 499 samples per second that is about 4 MB/s per busy thread, or about 170 MB per busy thread for this run.
- **Many threads:** if step 3 showed more than about 8 CPUs utilized, use `-F 199`. On bare metal, `--call-graph lbr` is another option: tiny files, but only user-space stacks up to 32 frames deep.

What to look for:
- **The final line** `[ perf record: Captured and wrote … (N samples) ]` should show roughly 20 000 samples per busy thread.
- **No `lost … chunks` or `Check IO/CPU overload` warnings.** If you see them, record again with `-F 199`.
- **No warning about restricted kernel address maps.** That warning means the `kptr_restrict` change didn't take effect.
- **Run time** should stay close to 40 s.
- **Don't rebuild `build-prof`** until you've finished steps 5–6. The recorded addresses only match this exact binary.

## 5. Where the time goes (`perf report`)

```bash
perf report -i prof/perf-cpu.data --stdio --no-children -g none --sort comm,dso    --percent-limit 1   > prof/report-comm-dso.txt
perf report -i prof/perf-cpu.data --stdio --no-children -g none --sort dso,sym     --percent-limit 0.5 > prof/report-self.txt
perf report -i prof/perf-cpu.data --stdio --children    -g none --sort dso,sym     --percent-limit 3   > prof/report-inclusive.txt
perf report -i prof/perf-cpu.data --stdio --no-children         --sort dso,sym     --percent-limit 2   > prof/report-callers.txt
perf report -i prof/perf-cpu.data --stdio --no-children -g none --sort sym,srcline --percent-limit 0.5 > prof/report-srcline.txt
```

The inclusive and callers reports rebuild every sample's stack, and the source-line report looks up every address. On a big binary each can take a few minutes. If they're too slow, add `--no-inline` (you lose inlined frames in the stacks). Open the files with `less -S`, because C++ names are long.

| File | Question it answers | What to look for |
|---|---|---|
| `report-comm-dso.txt` | which threads and which binaries use the CPU | How the time splits between `ingest` (your code plus inlined standard-library code), `libc.so.6`, `libstdc++.so.6`, `[kernel.kallsyms]` and any third-party `.so`. If your threads have names, it also shows which pipeline stage is busy. |
| `report-self.txt` | **the hotspot list**: time spent in each function itself (`[.]` = user code, `[k]` = kernel) | Any function at 20 % or more: step 6 is required for it. Nothing above about 5 %: a flat profile, so gains come from changing structure (see the inclusive report). Kernel rows adding up to more than 40 %: kernel or I/O bound, so do **B**. A hot function inside a system library (zlib, zstd, OpenSSL…): check its version with `dpkg -l`; newer releases are sometimes much faster. |
| `report-inclusive.txt` | which of *your* high-level functions own the time, including everything they call | `Children` is the time including callees; `Self` is the function's own time. Find the big stages (read, decode, parse, index, write…). `main` and `start_thread` (where worker threads begin) should together account for nearly all samples. If they're well short, stacks were cut off, for example by large buffers on the stack. In that case record again with `--call-graph dwarf,32768`. |
| `report-callers.txt` | why each hotspot is hot | Under each function is a tree of the call paths that lead to it (the first level is the direct caller), as % of all samples. This is what turns "memmove 9 %" into "7 % from copying `Record`s in `Batch::push_back`". |
| `report-srcline.txt` | the hottest source lines in the whole program | Lines in your own files vs in library headers. Headers like `bits/hashtable.h`, `basic_string.h` or `stl_vector.h` mean inlined container or string code; `report-callers.txt` shows which of your calls trigger it. |

At `-O3`, small functions are inlined, and their time is counted under the function they were inlined into. That's why one symbol can carry 40 %, and why the source-line report and step 6 matter.

Common patterns in `report-self.txt`:

| Hot symbols | Usually means |
|---|---|
| `malloc`, `_int_malloc`, `_int_free`, `cfree`, `operator new/delete`, `malloc_consolidate` (together above about 10 %) | Too many allocations, e.g. strings, vectors or `shared_ptr`s created per record. Fix by reusing buffers, `reserve()`, `string_view` or arena allocators. Quick test: run with jemalloc, tcmalloc or mimalloc via `LD_PRELOAD`. |
| `__memmove_*`, `__memcpy_*`, `__memset_*` | Copying or zeroing memory; `report-callers.txt` shows who does it. |
| `std::_Hashtable<…>::_M_find_before_node`, `_M_rehash`, `std::_Rb_tree<…>` | `std::unordered_map` or `std::map` lookups: following pointers between nodes, plus an integer division to pick the hash bucket. Fix with `reserve()` or a flat (open-addressing) hash map. |
| `std::__detail::_Executor…` (`std::regex`), `std::num_get`, `std::locale`, `strtod`, `vfprintf` | Slow parsing or formatting through regex, iostreams or locales. Replace with hand-written parsing or `std::from_chars`/`to_chars`. |
| `__cxa_throw`, `_Unwind_*`, `__dynamic_cast` | Exceptions or `dynamic_cast` on the hot path. |
| `pthread_mutex_lock`, `__lll_lock_wait`, `[k] futex_*`, spin or compare-and-swap helpers, `__pthread_cond_broadcast` | Threads fighting over locks, or waking each other too often. Do **A** and **B**. |
| `std::__introsort_loop` on numbers, CRC32C-like functions | Sorting or checksums; faster SIMD versions exist. |
| `[k] rep_movs_alternative`, `filemap_read`, `copy_page_to_iter` | `read()` calls copying file data. Use larger reads or `mmap`. |
| `[k] clear_page_erms`, `asm_exc_page_fault` | New memory being mapped and zeroed by the kernel. Keep buffers instead of freeing and reallocating them. |
| `[k] entry_SYSCALL_64`, `syscall_exit_to_user_mode` | Many tiny system calls; batch them. |

If start-up or shutdown (destructors, `free` at exit) looks large, re-run a report with `--time 0%-10%` or `--time 10%-100%` added to look at those phases separately.

Optional flame graph (the same information as a picture):

```bash
git clone --depth 1 https://github.com/brendangregg/FlameGraph.git ~/FlameGraph
perf script -i prof/perf-cpu.data | ~/FlameGraph/stackcollapse-perf.pl > prof/cpu.folded
~/FlameGraph/flamegraph.pl --title "ingest --replay day1.bin (on-CPU)" prof/cpu.folded > prof/cpu-flame.svg
```

Each box's width is its share of samples; the horizontal axis is not time. Wide towers are expensive code paths, and wide boxes along the top edge are where the CPU actually spends its time. Open the SVG in a browser: click to zoom, and press Ctrl-F to add up a function across all paths. If `perf script` is very slow, add `--no-inline` to it.

## 6. Drill into the hottest functions (`perf annotate`)

For each function at 20 % or more in `report-self.txt` (ideally the top 3–5), copy its name exactly as printed after `[.] `, then run:

```bash
perf annotate -i prof/perf-cpu.data --stdio -l -s 'paste::the(exact symbol) here' > prof/annotate-1.txt
```

You can also do this interactively: run `perf report -i prof/perf-cpu.data --no-children`, highlight the function, and press `a`.

What to look for:
- **`Sorted summary for file …`** at the top lists the hottest source lines in that function.
- **The % column** in the mixed source and assembly listing. The cost usually shows up on, or just after, the slow instruction. A hot instruction right after a memory load means that load missed the cache. Right after `div`/`idiv`, it's integer division, for example `%` to pick a hash bucket.
- **No SIMD, or narrow SIMD:** the hot loop uses scalar floating-point instructions (`addsd`, `mulsd`, `vaddss`) or only 128-bit `xmm` registers, while `cpu-flags.txt` shows `avx2` or `avx512f`.
- **Serial accumulator:** one floating-point add or FMA instruction holds more than 40 % of the function and adds into the same register every iteration, with IPC below 1 and few cache misses. Each iteration waits for the previous result.
- **Spin or compare-and-swap loop:** `lock cmpxchg` or `xchg` inside a loop, or `pause` instructions. This is lock contention; see **A**.
- **Shared counter:** `lock xadd` or `lock add` whose result isn't used. That's a statistics counter shared between threads.
- **Memory latency:** loads (`mov (%reg),…`) hold more than 30 % with little arithmetic around them. This usually means following pointers. Cross-check with the IPC and misses-per-1000-instructions from step 3.
- **Per-iteration allocation or copy:** a `call` to `malloc`, `operator new` or `memcpy` inside the hot loop.

## A. Only if the service is multithreaded and contention shows up

Do this when CPUs utilized is above 1 and at least one of these is true:
- lock, atomic or spin symbols appear in step 5;
- `lock`-prefixed instructions are hot in step 6;
- adding threads doesn't make the replay faster.

```bash
sudo perf c2c record -o prof/perf-c2c.data -- sudo -u "$USER" ./build-prof/bin/ingest --replay traces/day1.bin
sudo perf c2c report -i prof/perf-c2c.data --full-symbols --stdio --double-cl > prof/c2c-report.txt
grep -A 20 "Shared Data Cache Line Table" prof/c2c-report.txt
```

`perf c2c` finds cache lines that several cores keep fighting over. It runs as root here because that's simplest; `sudo -u "$USER"` keeps the service itself running as you.

What to look for:
- **Cache lines with `Tot Hitm` of 5 % or more.** Ignore the rest. A high `RmtHitm` means the traffic crosses CPU sockets, which is more expensive.
- **The `Offset` column** for each of those lines, in its block under `Shared Cache Line Distribution Pareto`. That block lists the code touching the line (`Symbol`, `Source:Line`) and the byte offset each access hits.
  - **Same offset from several threads (true sharing):** one hot variable, such as an atomic counter, a lock, or a queue's head or tail. Fix by splitting it, e.g. a per-thread copy.
  - **Different offsets (false sharing):** unrelated fields that happen to sit in the same cache line. Fix by separating them with `alignas(128)` or padding.
- **VMs:** c2c needs PEBS hardware sampling, which is available on bare metal. In most VMs it records nothing.

## B. Only if step 3 shows a lot of waiting

Do this when CPUs utilized is clearly below the number of busy threads, when sys time is high, or when there are many context switches.

```bash
sudo perf trace -s -o prof/perf-trace-summary.txt -- sudo -u "$USER" ./build-prof/bin/ingest --replay traces/day1.bin
sudo perf record -e sched:sched_switch --call-graph dwarf -o prof/perf-offcpu.data -- sudo -u "$USER" ./build-prof/bin/ingest --replay traces/day1.bin
sudo perf report -i prof/perf-offcpu.data --stdio --no-children --percent-limit 2 > prof/report-offcpu.txt
```

What to look for:
- **`perf-trace-summary.txt`** has one table of system calls per thread. Rank them by the `total (msec)` column; the biggest totals are where that thread waits:
  - `futex`: waiting on locks or condition variables. This is either contention, or idle workers waiting on a slow producer.
  - `read` or `pread64`: waiting on input I/O.
  - `write` or `fsync`: waiting on output.
  - `epoll_wait` or `poll`: idle.
  - `nanosleep` or `clock_nanosleep`: deliberate sleeps. If the replay paces itself to the trace's timestamps, look for an as-fast-as-possible option before optimising anything.
  - Tracing slows the run down, so compare the totals with each other, not with 40 s.
- **`report-offcpu.txt`** shows the call stacks that led to a thread going to sleep. They're counted per sleep, not weighted by how long it lasted. Follow each branch past the kernel and libc frames (`futex_wait`, `pthread_cond_wait`, `__lll_lock_wait`, `read`) to the first function of yours. That tells you which queue, mutex or reader the threads are waiting on. Stacks that end in ordinary code rather than a blocking call are preemptions: more threads wanted to run than there were cores.
- **File size:** the file grows by about 8 KB per context switch. If step 3 counted millions of switches, add `-c 10` to the record command to keep only every 10th.

## 7. Restore the settings and send me the results

```bash
sudo sysctl -p prof/sysctl-before.txt                 # puts perf_event_paranoid and kptr_restrict back
tar czf ingest-prof.tgz --exclude='*.data*' prof/     # reports, annotate files, flame graph; leaves out the large perf.data files
```

Send me `ingest-prof.tgz`, or paste `perf-stat.txt`, `report-self.txt`, `report-callers.txt` and the annotate files. I'll turn them into a ranked hotspot report with concrete fixes.

**Assumptions:**
- You build with GCC or Clang using CMake's default Release flags.
- The binary ends up in `build-prof/bin/ingest`, the same way it does under `build/`.
- The Xeon is bare metal. If it's a VM, the fallbacks are noted in each step.
- I don't know how the service uses threads, so A and B are conditional.

If it helps, I can turn steps 2–5 into a single script that runs unattended and collects everything into `prof/`.
