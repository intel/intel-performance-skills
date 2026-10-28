"""Grades the score-sorting task; writes /logs/verifier/reward.json.

Checks, all required:
  builds         `make -C /app clean all` produces /app/libranker.a
  correct        element-for-element agreement with std::sort on all lengths 0..600,
                 long arrays and adversarial shapes (tests/check.cpp), linking only
                 libranker.a and the C++ standard library
  dispatch       the library selects its fast path at run time (a CPUID-based check
                 compiled in), so one build stays correct on CPUs without the fast ISA
  single_thread  process CPU time / wall time <= 1.3 while sorting (no worker threads)
  fast           throughput on 1M floats >= GATE x std::sort, both timed by the same
                 harness on this machine, best of three alternating runs

GATE is 12x where the CPU has AVX-512F. Calibration on Sapphire Rapids (g++ 14, 1M
floats, x std::sort): vectorized-partitioning quicksort with AVX-512 ~20x, the same
with AVX2 only ~14x, a tuned LSD radix sort (8- or 11-bit digits, reused scratch)
~8-9x, a textbook radix ~4-6x. Else 10x with AVX2, else 4x.
"""
import json
import pathlib
import re
import subprocess
import time

APP = pathlib.Path("/app")
T = pathlib.Path("/tests")
W = pathlib.Path("/tmp/verify")
checks, notes = {}, {}
# Every command shares one budget, kept under task.toml's verifier timeout so
# reward.json is written before Harbor kills the verifier.
DEADLINE = time.monotonic() + 870


def run(cmd, timeout=240, **kw):
    timeout = max(1, min(timeout, DEADLINE - time.monotonic()))
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"timed out after {timeout}s")


def code(text):
    """Source text without C/C++ comments, so a comment cannot satisfy a code check."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)


def flags():
    text = pathlib.Path("/proc/cpuinfo").read_text()
    line = next((l for l in text.splitlines() if l.startswith("flags")), "")
    return set(line.split(":", 1)[-1].split())


def gate_for(f):
    if "avx512f" in f:
        return 12.0
    return 10.0 if "avx2" in f else 4.0


def sources():
    out = []
    for p in APP.rglob("*"):
        if p.suffix in {".c", ".cc", ".cpp", ".h", ".hpp"} and p.is_file():
            out.append(p.read_text(errors="ignore"))
    return " ".join(out)


def grade():
    W.mkdir(parents=True, exist_ok=True)
    b = run(["make", "-C", str(APP), "clean", "all"], timeout=600)
    lib = APP / "libranker.a"
    checks["builds"] = b.returncode == 0 and lib.exists()
    notes["build_tail"] = (b.stdout + b.stderr)[-800:]
    if checks["builds"]:
        cxx = ["g++", "-O2", "-std=c++17"]
        c = run(cxx + [str(T / "check.cpp"), str(lib), "-o", str(W / "check")])
        if c.returncode == 0:
            r = run([str(W / "check")])
            checks["correct"] = r.returncode == 0 and "CHECK OK" in r.stdout
            notes["check"] = r.stdout[-600:]
        else:
            checks["correct"] = False
            notes["check"] = c.stderr[-600:]
        nm = run(["nm", str(lib)]).stdout
        checks["dispatch"] = bool(
            re.search(r"__cpu_model|__cpu_indicator_init|__cpu_features2|\bi\s+\w*sort", nm)
            or re.search(r"__builtin_cpu_supports|target_clones|__get_cpuid|cpuid", code(sources())))
        a = run(cxx + [str(T / "harness.cpp"), str(lib), "-o", str(W / "agent")])
        r_ = run(cxx + [str(T / "harness.cpp"), str(T / "ref_std.cpp"), "-o", str(W / "ref")])
        if a.returncode == 0 and r_.returncode == 0:
            ag, rf, par = [], [], []
            for _ in range(3):
                rf.append(float(run([str(W / "ref")]).stdout.split()[0]))
                out = run([str(W / "agent")]).stdout.split()
                ag.append(float(out[0]))
                par.append(float(out[1]))
            gate = gate_for(flags())
            ratio = max(ag) / max(rf)
            notes.update(agent_melems=max(ag), ref_melems=max(rf), ratio=round(ratio, 2),
                         gate=gate, cpu_over_wall=max(par))
            checks["single_thread"] = max(par) <= 1.3
            checks["fast"] = checks.get("correct", False) and ratio >= gate
        else:
            checks["fast"] = checks["single_thread"] = False
            notes["harness"] = (a.stderr + r_.stderr)[-600:]


def main():
    try:
        grade()
    except Exception as e:  # pylint: disable=broad-except  # any grading failure scores 0
        notes["error"] = repr(e)[-600:]
    required = ["builds", "correct", "dispatch", "single_thread", "fast"]
    rewards = {k: float(checks.get(k, False)) for k in required}
    rewards["reward"] = float(all(checks.get(k, False) for k in required))
    print(json.dumps({"checks": checks, "notes": notes}, indent=1))
    pathlib.Path("/logs/verifier").mkdir(parents=True, exist_ok=True)
    with open("/logs/verifier/reward.json", "w", encoding="utf-8") as fh:
        json.dump(rewards, fh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
