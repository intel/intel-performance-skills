# Harbor evals: does an agent do better work with the skill?

Each skill is measured on [Harbor](https://github.com/laude-institute/harbor) tasks, in
two arms that differ only by the skill:

| arm | what the agent gets |
|---|---|
| `no_skill` | the task instruction |
| `with_skill` | the same, plus `--skill skills/<name>` and [`instructions/use-<name>.md`](instructions/) appended |

Same image, agent, model, Claude Code version, settings, timeouts and attempt count in
both arms. The treatment instruction says a skill is installed and to read it, and
nothing else, so `with_skill` measures the skill's body rather than whether its
description gets it loaded. The layout and arm design follow
[intel/skills `evaluation/harbor/`](https://github.com/intel/skills/tree/main/evaluation/harbor).

## How the tasks are written

- **The instruction is a user request, not a hint.** It states the symptom, the
  constraints and the deliverable. It never names the pattern, the fix, the library or
  the instruction set; if it did, any model would pass and the task would measure
  nothing.
- **The verifier checks the outcome.** Executable tasks build the agent's library and
  time it with the verifier's own harness against a reference baseline timed in the
  same run, so the gate is a ratio and survives a loaded host. Correctness is checked
  against a bitwise or `std::sort` reference over many lengths and alignments.
- **Speed gates are calibrated, not guessed.** Each gate sits between what a strong
  engineer writes without the skill and what the skill's guidance produces, both
  measured on the reference host (Xeon Platinum 8480+, Sapphire Rapids). The gate
  is relaxed on CPUs without the relevant ISA; see each `tests/verify.py`.
- **Conventions are recorded, not rewarded.** A check for something the skill
  prefers but the instruction does not ask for (the 5% cutoff in the hotspot and c2c
  tasks, TTAS as the only acceptable lock fix, `perf c2c` as the next measurement) is listed in `INFORMATIONAL` in its `verify.py`:
  written to `reward.json`, left out of `reward`.
- **No live `perf`.** linux-perf tasks ship recorded `perf stat` / `report` /
  `annotate` / `c2c` output (synthetic, with no real host names or paths), so no
  task needs a privileged container, `CAP_PERFMON` or `perf_event_open`.
- **Network:** the environment and agent are `public` (image build, model API, and the
  agent may fetch third-party code); the verifier runs with `no-network`.

## Tasks

| task | skill | asks for | verifier |
|---|---|---|---|
| [`pp-crc32c-throughput`](tasks/pp-crc32c-throughput/) | performance-patterns | make a byte-table `crc32c()` as fast as possible, one build for the whole fleet | correctness vs bitwise CRC32C; runtime dispatch present; ≥ 5x a single-chain `_mm_crc32_u64` |
| [`pp-hamming-search`](tasks/pp-hamming-search/) | performance-patterns | speed up Hamming distance over 4096-bit codes | correctness; dispatch; ≥ 2.75x a single-chain `popcnt` at 512 B |
| [`pp-simd-sort`](tasks/pp-simd-sort/) | performance-patterns | speed up a single-threaded sort of 1M floats, self-contained static library | equal to `std::sort`; dispatch; single-threaded; ≥ 12x `std::sort` |
| [`pp-source-review`](tasks/pp-source-review/) | performance-patterns | review a module for performance problems before shipping | ≥ 7 of 10 seeded issues found by line range; ≤ 1 of 4 decoys flagged |
| [`lp-hotspot-report`](tasks/lp-hotspot-report/) | linux-perf | a shareable hotspot report from recorded profiles | top-functions table; source-line % for both hot functions; serial accumulator, scalar FP and false sharing named; report-only |
| [`lp-c2c-contention`](tasks/lp-c2c-contention/) | linux-perf | what to change, from a recorded `perf c2c` report | the hot lines classified true/false sharing, with fields and fix |
| [`lp-scaling-bottleneck`](tasks/lp-scaling-bottleneck/) | linux-perf | why a service stops scaling, from 1- and 32-thread profiles | the two functions whose share jumps (not the hottest one), and their category |

## Running

The tasks are plain Harbor tasks; this directory ships no runner. Run them with
`harbor` directly, or with intel/skills' [`tools/compare_harbor_skill.py`](https://github.com/intel/skills/blob/main/tools/compare_harbor_skill.py),
which runs the arms and computes the delta.

```bash
# from the repo root
export HARBOR_TELEMETRY=off
# credentials for Bedrock: CLAUDE_CODE_USE_BEDROCK=1, AWS_REGION, AWS_BEARER_TOKEN_BEDROCK
harbor run -p evaluation/harbor/tasks -a oracle -o evaluation/harbor/jobs --yes           # every task must score 1.0

# both arms of one skill: same flags, only --skill and the treatment instruction differ
common=(-p evaluation/harbor/tasks -i pp-crc32c-throughput -k 3 -a claude-code -m <model>
  --ak version=2.1.296 --ak config=evaluation/harbor/claude-settings.json
  --ak disable_web_search=true --ae CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1
  --ae CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL=1 -o evaluation/harbor/jobs --yes)
harbor run "${common[@]}" --job-name no_skill
harbor run "${common[@]}" --job-name with_skill --skill skills/performance-patterns \
  --extra-instruction-path evaluation/harbor/instructions/use-performance-patterns.md
```

A task belongs to a skill by `[metadata] skill` in its `task.toml`. `claude-settings.json`
turns the bundled Claude Code plugins off in both arms and caps the skill listing, so
the skill under test is the only one the `with_skill` agent sees.

## Results — 2026-10-18

Harbor 0.24.0, `claude-code` 2.1.296, skills at 3e33aec. Pass = every required check.
Haiku is `us.anthropic.claude-haiku-5-5`, Sonnet `us.anthropic.claude-sonnet-5-5`.
No trial errored.

| task | model | no_skill | with_skill | what separates the arms |
|---|---|---|---|---|
| pp-crc32c-throughput | Haiku | 0/5 | **5/5** | no_skill stops at a 3-way `crc32` interleave (2.8x, 22 GB/s); with_skill uses the skill's VPCLMULQDQ fusion file (11.9x, 95 GB/s) |
| | Sonnet | 0/3 | **3/3** | same |
| lp-hotspot-report | Haiku | 1/5 | **5/5** | no_skill reports instruction-level percentages from `perf annotate` and omits per-source-line attribution for one or both hot functions |
| | Sonnet | 1/3 | **3/3** | same |
| pp-hamming-search | Haiku | 3/5 | **5/5** | 2 of 5 no_skill attempts stop at 1.6–1.7x; every with_skill attempt reaches 3.4–4.0x |
| | Sonnet | 3/3 | 3/3 | ceiling: Sonnet finds VPOPCNTDQ without the skill |
| pp-source-review | Haiku | 2/3 | 3/3 | recall 7.0 → 9.3 of 10; `restrict` found 0/3 → 3/3; the 4-accumulator decoy flagged 2/3 → 0/3 |
| pp-simd-sort | Haiku | 1/3 | 2/3 | with_skill reaches x86-simd-sort (21x) in 2/3, no_skill once (23x); the other attempts reach 6–9x |
| lp-c2c-contention | Haiku | 3/3 | 3/3 | ceiling: classification and fixes equal in both arms; informational checks differ: 5% cutoff 0/3 → 2/3, TTAS lock fix 1/3 → 3/3 |
| lp-scaling-bottleneck | Haiku | 2/3 | 2/3 | ceiling: both arms find the two jumping functions and pick `perf c2c` |

Attempts are per task: 5 for the first three tasks on Haiku, 3 elsewhere. The trials
come from several Harbor jobs per model, run 2026-10-18/19 at the same task revision,
agent and settings; job outputs are not committed.

The first two rows are the evidence that the skills change outcomes on both models.
Hamming discriminates for Haiku only. Source review and sort point the right way
at k=3 but are too close to call; c2c and scaling do not discriminate on reward and
are kept as smoke tasks.

The c2c row is recomputed from each trial's per-check values with its convention
checks informational; the scaling row is unchanged (every trial passed `next_is_c2c`). Two task bugs were found by reading trials and fixed
before the numbers above: the sort instruction implied no download was possible (both arms gave up on third-party
code; re-run after the fix), and the hotspot verifier accepted a source-line
percentage only when the source text was quoted, not `line 16` / `score.c:16` (all
hotspot trials re-graded from their trajectories).

## Skill defects these tasks surfaced

- [#7](https://github.com/intel/intel-performance-skills/issues/7) `performance-patterns/patterns/fast-crc32c-impl.md`: the compile line for
  `crc32c_avx512_vpclmulqdq.c` lacks `-mpclmul -msse4.2`; GCC 13 fails with
  "inlining failed in call to `_mm_clmulepi64_si128`".
- [#8](https://github.com/intel/intel-performance-skills/issues/8) `performance-patterns/references/known-algorithms-impl.md`: the AVX-512 Hamming and
  Jaccard rows use `_mm512_popcnt_epi8` (AVX512_BITALG) under an `avx512vpopcntdq`-only
  guard; `_mm512_popcnt_epi64` needs no widening. Its GB/s figures are low by ~3x
  (POPCNT) and ~1.3x (AVX-512) on Sapphire Rapids.
- [#9](https://github.com/intel/intel-performance-skills/issues/9) `performance-patterns/patterns/simd-sort.md`: Option B (copy one header) does not
  compile on its own; Option A does not say `-Dlib_type=static`, and assumes meson and
  `sudo`.
- [#10](https://github.com/intel/intel-performance-skills/issues/10) `linux-perf`: Flow A, Flow E and SKILL.md disagree on how to read low IPC with low
  miss rates; flow-c.md's "same field name means true sharing" conflicts with the
  offset rule in building-blocks.md; the intro lists flows A–C of A–E.
