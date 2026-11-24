# Evals for the Intel performance skills

These evals measure whether the skills improve an agent's results, by running the
same realistic task with the skills installed and without them, and grading both.
Results of the first run are in
[`benchmarks/2026-11-23-claude-opus-5-5/`](benchmarks/2026-11-23-claude-opus-5-5/).

---

## Contents

| Path | Purpose |
|------|---------|
| `skills/<skill>/evals/<case>/` | One folder per test case, in the layout `claude plugin eval` reads |
| `evals/checks/` | Programmatic checks that build and run what an agent produced |
| `evals/benchmarks/<date>-<model>/` | Results of a run, in skill-creator's benchmark format (see [Results](#results)) |

Each case folder holds:

| File | What it is |
|------|------------|
| `prompt.md` | Run settings, then the prompt exactly as a user would type it |
| `case.yaml` | Name, a description of a good result, and the scaffold script |
| `graders/NN-*.md` | One assertion per file |
| `files/` | Input files placed in the agent's working directory |
| `place-files.sh` | Copies `files/` into the sandbox (the PTS cases also install the test) |

---

## Cases

| Skill | Case | What it checks |
|-------|------|----------------|
| `performance-patterns` | `dot-product-review-mixed-fleet` | README example: serial accumulator, runtime dispatch from Westmere to SPR, correct results with and without AVX2 |
| `performance-patterns` | `avx2-scale-function-with-dispatch` | README example: new AVX2 code with dispatch under plain `-O2` flags, exact results, deliberate `restrict` use |
| `performance-patterns` | `percentile-sort-with-nan-samples` | `std::sort` hotspot on floats with NaNs: catches the strict-weak-ordering bug, selection vs. full sort, x86-simd-sort |
| `performance-patterns` | `crc32c-table-lookup-rewrite` | Table CRC32C rewrite: bit-identical results (the input uses the no-inversion seed convention), multi-stream/PCLMUL, AVX-512 path |
| `performance-patterns` | `startup-config-loader-not-hot` | Over-engineering check: startup-only code that should not get micro-optimizations |
| `linux-perf` | `low-ipc-serial-fma-loop` | README example: low IPC with low cache misses, diagnosed from perf stat and annotate output |
| `linux-perf` | `c2c-false-vs-true-sharing` | README example: a c2c report mixing false sharing, true sharing and a false-sharing victim |
| `linux-perf` | `profiling-plan-for-release-build` | Command plan for a remote box: perf permissions, `-g` without losing `-O3`, stat → record → annotate |
| `linux-perf` | `spinlock-scaling-collapse` | README example: dual profile, annotate and c2c of a test-and-set spinlock guarding an LRU cache |
| `phoronix-test-suite` | `rebuild-test-with-debug-info` | Rebuild an installed PTS test with `-g`, keeping `-O2` and the entry-point script intact |
| `phoronix-test-suite` | `optimize-test-end-to-end` | README example: profile and optimize a PTS test; the speedup is re-measured independently |

### Fixtures

- **Profiling data is recorded, not live.** The perf stat, annotate, report and c2c
  outputs are synthetic but internally consistent with the attached sources (line
  numbers, offsets, addresses), so the cases run anywhere, without perf or x86 hardware.
- **The PTS cases run in a sandbox.** `files/bin/phoronix-test-suite` is a small
  stand-in for the PTS CLI (`install`, `batch-install`, `force-install`, `run`,
  `batch-run`, `info`, `list-*`). It works on `files/pts-root/`, which uses the regular
  `~/.phoronix-test-suite` layout. The test, `pts/fluxsim`, is a small benchmark with two
  real bottlenecks (a strided column sum and a serial reduction). Like many real
  `install.sh` scripts, its install deletes the source and writes an entry-point script
  with the same name as the archive's top-level directory, so extracting the archive
  in place replaces the script.

---

## Running the cases

### Keep `evals/` out of the evaluated agent's sight

The with-skill agent must not be able to see this folder. In the first run, agents that
listed a skill folder saw the grader file names for their own case (the transcripts
show they never opened them). For with-skill runs, give the agent a copy of the skills
without the evals:

```bash
rsync -a --exclude 'evals/' skills/ /tmp/skills-under-test/
```

Check whether `claude plugin eval` hides `evals/` before relying on it. Installs are
affected too: `gh skill install` copies the whole skill folder, so it would ship the
`evals/` folders (about 600 KB, including the fixtures) to users.

### With `claude plugin eval`

From a skill folder (Claude Code 2.1.269 or later):

```bash
cd skills/linux-perf
claude plugin eval . --allow-tools Write,Edit,Bash --scaffold
```

The CLI runs each case with and without the skill and writes its report under
`evals/results/` (git-ignored). These are real, billed model runs.

### As subagent runs

The results in `benchmarks/` were produced this way, following the skill-creator workflow:

1. For each case, scaffold two empty sandboxes with `place-files.sh`. Give them
   neutral names, so the path doesn't reveal which configuration a run is.
2. Run one agent per sandbox with the task prompt. Only the with-skill agent is told
   that the skills exist (name, path and description of all three, as an agent sees
   installed skills), and it is pointed at the copy without `evals/`. The exact
   instructions are in each run's `executor-prompt-*.txt`.
3. Run the programmatic checks on each sandbox.
4. Grade the remaining assertions blind: a grader sees the two runs only as "A" and "B"
   (reply and files, no run notes), with the programmatic verdicts filled in
   (`grader-prompt.txt`).

Run the agents in a session with no other skills or plugins installed. In the first
run, two baseline agents loaded general-purpose process skills from an unrelated
plugin.

A full run of all 11 cases cost about 5.9M tokens (about 230K per run, plus grading).

---

## Programmatic checks

```bash
python3 evals/checks/run_checks.py <case-name> <agent work dir> [result.json]
```

| Case | Check |
|------|-------|
| `dot-product-review-mixed-fleet` | Builds `dot_fast.c` with plain `-O2 -fPIC`; checks 66 lengths and alignments against a double reference, with and without AVX2 |
| `avx2-scale-function-with-dispatch` | Builds `scale.c`; checks exact results for lengths 0–10007, unaligned pointers and no writes past `n`, with and without AVX2 |
| `crc32c-table-lookup-rewrite` | Compares against the original on 652 cases (lengths 0–70,000, unaligned, chained calls, check string); reports emulated throughput |
| `percentile-sort-with-nan-samples` | Compiles against the real x86-simd-sort headers if used; compares percentiles and the failed count on 9 data sets with NaNs |
| `rebuild-test-with-debug-info` | Entry script unchanged, debug info present, `-O2` kept (build banner plus throughput vs. a fresh build), batch-run works |
| `optimize-test-end-to-end` | Independent batch-run vs. a fresh install of the original (needs ≥2.5×), checksum unchanged, entry script unchanged |

The text-only cases (most `linux-perf` ones and `startup-config-loader-not-hot`) have no
programmatic check; an LLM grader reads the reply.

**CPU profiles.** Each code check runs the build on two CPU profiles:

- **macOS on Apple Silicon:** builds with `clang -arch x86_64` and runs under Rosetta 2.
  Rosetta reports AVX2 only when `ROSETTA_ADVERTISE_AVX=1` is set, which gives a
  no-AVX profile and an AVX2 profile.
- **Linux x86-64:** runs natively for the AVX2 profile, and under
  `qemu-x86_64 -cpu Westmere` for the no-AVX profile.

AVX-512 code is compiled but cannot be executed on either profile, and throughput
under Rosetta is only indicative.

**x86-simd-sort** (percentile case) needs a checkout of
https://github.com/intel/x86-simd-sort in `XSS_DIR` and a static build in `XSS_LIB`:

```bash
cd x86-simd-sort && mkdir -p build && cd build
for spec in "x86simdsort.cpp:" "x86simdsort-avx2.cpp:-march=haswell" \
            "x86simdsort-skx.cpp:-march=skylake-avx512" "x86simdsort-icl.cpp:-march=icelake-client" \
            "x86simdsort-spr.cpp:-march=sapphirerapids"; do
  f=${spec%%:*}; clang++ -arch x86_64 -std=c++17 -O2 -fPIC ${spec#*:} \
     -I../src -I../lib -I../utils -c ../lib/$f -o ${f%.cpp}.o
done
ar rcs libx86simdsortcpp.a *.o
export XSS_DIR=$PWD/.. XSS_LIB=$PWD/libx86simdsortcpp.a
```

(Drop `-arch x86_64` on Linux.)

---

## Results

Each run of the suite goes in `benchmarks/<date>-<executor model>/`, in the layout that
skill-creator's `aggregate_benchmark.py` and eval viewer read:

| File | What it is |
|------|------------|
| `<skill>/benchmark.json` | Pass rate, tokens and time of every run, means per configuration, and the analyst's notes |
| `<skill>/<case>/eval_metadata.json` | The prompt and assertions as they were run |
| `<skill>/<case>/<config>/grading.json` | Verdict and evidence for each assertion, and the skill files the run read |
| `<skill>/<case>/<config>/checks.json` | Output of `evals/checks/run_checks.py` for the run |
| `<skill>/<case>/<config>/timing.json` | Tokens and wall-clock time |
| `<skill>/<case>/<config>/outputs/response.md` | The agent's final reply |
| `executor-prompt-*.txt`, `grader-prompt.txt` | The instructions the agents and the graders got |

`<config>` is `with_skill` or `without_skill`. Local paths are replaced with `<work>`
(the run's working directory), `<outputs>` and `<skills>`. The files the agents wrote
are not included; `checks.json` and the grading evidence describe them.

To browse a run, build the viewer from the skill-creator skill's folder:

```bash
python eval-viewer/generate_review.py <repo>/evals/benchmarks/<run>/<skill> \
  --benchmark <repo>/evals/benchmarks/<run>/<skill>/benchmark.json --static review.html
```

`python -m scripts.aggregate_benchmark <run>/<skill> --skill-name <skill>` rebuilds
`benchmark.json` from the per-run files, without the analyst's notes.

---

## Adding a case

1. Create `skills/<skill>/evals/<case-name>/` with `prompt.md`, `case.yaml`, one
   `graders/NN-*.md` per assertion and, if needed, `files/` plus `place-files.sh`
   (copy the existing ones).
2. Write assertions about outcomes a performance engineer would care about, not about
   the skill's own conventions. Make each one something a strong agent without the
   skill could plausibly miss; an assertion both configurations always pass doesn't
   tell you anything.
3. If the result can be checked by building or running it, add a check to
   `evals/checks/` and register it in `run_checks.py`.
