# Intel Performance Skills — AI Agent Skills

A collection of AI agent **skills** for CPU performance analysis and optimization on
x86 Linux. The skills give an agent structured, multi-step workflows for profiling
with Linux `perf`, identifying performance anti-patterns in source code and profiling
output, and optimizing benchmarks from the Phoronix Test Suite.

## Example prompts

Once installed, you can talk to the agent naturally. A few examples:

**Profile a workload and find hotspots**
> Please profile `./myapp --bench` and tell me where the time goes.

**Identify why a loop is slow**
> This function runs at 0.3 IPC despite low cache misses. What's wrong, and how do I fix it?

**Optimize from source code alone**
> Review this dot-product loop for performance issues:
> ```c
> for (int i = 0; i < n; i++) sum += a[i] * b[i];
> ```

**Cache-line contention**
> My server gets slower as I add threads. Can you use `perf c2c` to find false sharing?

**Core-count scaling**
> This workload doesn't scale past 8 cores. Help me find the bottleneck.

**Phoronix benchmark optimization**
> Profile `pts/compress-zstd` and optimize it. Report the before/after improvement.

**Write new SIMD code correctly**
> Write a vectorized AVX2 float array scale function with CPU dispatch.

---

## Contents

| Directory | Purpose |
|-----------|---------|
| `skills/linux-perf/` | Data collection skill: `perf` workflows, building blocks, hotspot reporting |
| `skills/performance-patterns/` | Pattern detection and fix playbooks: source code and profiling signals |
| `skills/phoronix-test-suite/` | Supporting skill: install, run, and optimize PTS benchmarks |
| `evaluation/harbor/` | Harbor tasks that measure the skills: with vs without, same agent and model |

---

## Skills

### `linux-perf` — profiling data collection

**Profile Linux workloads with `perf` and identify performance bottlenecks.**

When the user asks why code is slow, this skill guides data collection from hardware
counters through to annotated hotspot reports. It provides:

- **Flow A** — `perf stat`: quick IPC, cache-miss rate, and branch-miss rate in seconds.
- **Flow B** — `perf record` + `perf report`: which functions and source lines consume CPU.
- **Flow C** — `perf c2c`: cache-line contention, false sharing, and HITM events in
  multi-threaded code.
- **Flow D** — dual-profile scaling analysis: identify bottlenecks that only appear at
  high core counts.
- **Flow E** — structured hotspot report: formatted deliverable with top-function table,
  annotated source, and pattern observations.
- **Annotate pattern scan**: scan `perf annotate` output for scalar FP, narrow SIMD,
  serial accumulators, horizontal reductions, lock CAS, and memory pressure patterns.

When a pattern is identified, `linux-perf` hands off to `performance-patterns` for the fix.

Trigger phrases: *"profile"*, *"hotspot"*, *"IPC"*, *"cache miss"*, *"false sharing"*,
*"HITM"*, *"does not scale"*, *"why is this slow"*, *"where does time go"*.

---

### `performance-patterns` — pattern detection and fixes

**Detect and fix well-known performance anti-patterns — from source code or profiling output.**

This skill owns the full catalog of patterns with detection signals and fix playbooks.
It works standalone (source review only) or in combination with `linux-perf` (profiling
data). Patterns covered:

| Pattern | Detection signal | Fix |
|---------|-----------------|-----|
| Serial accumulator | Low IPC + low cache misses; `vaddss`/`vfmadd` dominates annotate | Multiple independent accumulators + SIMD |
| Narrow SIMD | `xmm`/scalar in hot loop on AVX2/AVX-512 CPU | Vector width upconversion (zipper algorithm) |
| Missing `vzeroupper` | `other_assists.avx_to_sse` > 0; extreme cycle count on first SSE instruction | Insert `vzeroupper` before SSE transitions |
| Missing `restrict` | Aliasing preamble before vectorizable loop; two loop versions in annotate | Add `restrict` to pointer parameters |
| Test-and-Set spinlock | `lock cmpxchg` cluster in annotate; throughput drops with more threads | Test-and-Test-and-Set (TTAS) |
| False sharing | `perf c2c` HITM > 5%; different offsets written by different threads | Struct padding and layout reorganisation |
| Shared statistics counter | `lock add`/`lock inc` in hot path; true sharing in `perf c2c` | Per-CPU bucket array or local batching |

Also provides guidelines for writing new performance-sensitive C/C++ and SIMD code
correctly from the start.

Trigger phrases: *"optimize"*, *"vectorize"*, *"why is this slow"*, *"review for
performance"*, *"improve throughput"*, *"write a fast…"*, any `_mm*` intrinsics or
inline asm with `xmm`/`ymm`/`zmm` registers.

---

### `phoronix-test-suite` — supporting skill

**Install, run, and optimize benchmarks from the Phoronix Test Suite.**

Invoked automatically by `linux-perf` when the profiling target is a `pts/<name>`
benchmark. Handles the full lifecycle: install, source extraction, rebuild with
debug symbols, binary deployment, and result recording. Knows the correct
`install.sh` build flags and source layout for each test.

Trigger: any `pts/<name>` reference, or the words *"phoronix"* / *"phoronix-test-suite"*.

---

## Evaluation

`linux-perf` and `performance-patterns` are measured on [Harbor](https://github.com/laude-institute/harbor)
tasks in [`evaluation/harbor/`](evaluation/harbor/): the same agent and model solve each
task without and with the skill, and a verifier grades the result. On Claude Haiku 5.5 and
Sonnet 5.5, the skills turn a fast CRC32C (0/8 → 8/8 passes) and a hotspot report with
source-line attribution (2/8 → 8/8) from failures into passes. Tasks, method and the full
table: [`evaluation/harbor/README.md`](evaluation/harbor/README.md).

---

## Installation

This skill collection follows the open [Agent Skills standard](https://agentskills.io).
Each skill directory contains a `SKILL.md` file that any compatible agent loads on
demand. The same directories work across GitHub Copilot (CLI and VS Code), Claude Code,
OpenAI Codex, Gemini CLI, and other compatible agents.

| Agent | Project-level path | User-level path |
|---|---|---|
| GitHub Copilot CLI / VS Code | `.github/skills/` | `~/.copilot/skills/` |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| OpenAI Codex | `.agents/skills/` | `~/.agents/skills/` |
| Gemini CLI | `.gemini/skills/` | — |
| OpenCode | `.opencode/skills/` | `~/.config/opencode/skills/` |

### GitHub CLI (`gh skill`) — recommended

The easiest way to install across any supported agent. Requires
[GitHub CLI v2.90.0](https://github.com/cli/cli/releases/tag/v2.90.0) or later.

```bash
# Install all skills (user-level)
gh skill install intel/intel-performance-skills linux-perf
gh skill install intel/intel-performance-skills performance-patterns
gh skill install intel/intel-performance-skills phoronix-test-suite
```

Keep them up to date:

```bash
gh skill update linux-perf
gh skill update performance-patterns
gh skill update phoronix-test-suite
```

### GitHub Copilot CLI

Skills are installed per-user under `~/.copilot/skills/`:

```bash
cp -r skills/linux-perf ~/.copilot/skills/
cp -r skills/performance-patterns ~/.copilot/skills/
cp -r skills/phoronix-test-suite ~/.copilot/skills/
```

### GitHub Copilot in VS Code

GitHub Copilot in VS Code loads skills from `.github/skills/` at project level or
`~/.copilot/skills/` at user level. Project-level skills can be committed so the
whole team benefits automatically:

```bash
# Project-level (commit to your repository)
mkdir -p .github/skills
cp -r skills/linux-perf skills/performance-patterns skills/phoronix-test-suite \
    .github/skills/

# User-level (available in every project)
cp -r skills/linux-perf skills/performance-patterns skills/phoronix-test-suite \
    ~/.copilot/skills/
```

VS Code also scans `.claude/skills/` and `.agents/skills/`, so a single project-level
installation covers GitHub Copilot, Claude Code, and Codex simultaneously.

### Claude Code

Claude Code discovers skills in `.claude/skills/` (project) or `~/.claude/skills/`
(user):

```bash
# Project-level
mkdir -p .claude/skills
cp -r skills/linux-perf skills/performance-patterns skills/phoronix-test-suite \
    .claude/skills/

# User-level
cp -r skills/linux-perf skills/performance-patterns skills/phoronix-test-suite \
    ~/.claude/skills/
```

### OpenAI Codex

Codex reads skills from `.agents/skills/` at project level and `~/.agents/skills/`
at user level:

```bash
# Project-level
mkdir -p .agents/skills
cp -r skills/linux-perf skills/performance-patterns skills/phoronix-test-suite \
    .agents/skills/

# User-level
cp -r skills/linux-perf skills/performance-patterns skills/phoronix-test-suite \
    ~/.agents/skills/
```

### Gemini CLI

Gemini CLI reads project skills from `.gemini/skills/`:

```bash
mkdir -p .gemini/skills
cp -r skills/linux-perf skills/performance-patterns skills/phoronix-test-suite \
    .gemini/skills/
```

### OpenCode

OpenCode has native skill support and reads skills from `.opencode/skills/`:

```bash
mkdir -p .opencode/skills
cp -r skills/linux-perf skills/performance-patterns skills/phoronix-test-suite \
    .opencode/skills/
```

For user-level installation, copy to `~/.config/opencode/skills/` instead.

---

## License

Copyright (C) 2026 Intel Corporation. Released under the [MIT License](COPYRIGHT.md).
