"""Grades the Hamming-distance task; writes /logs/verifier/reward.json.

Checks, all required:
  builds       `make -C /app clean all` produces /app/libhamming.a
  correct      bit-by-bit reference agreement on every length 0..1100 at many
               alignment pairs, long buffers, all-bits-differ, nearest (tests/check.c)
  dispatch     the library selects its fast path at run time (a CPUID-based check
               compiled in), so one build stays correct on CPUs without the fast ISA
  fast         re-ranking throughput on 512-byte codes >= GATE x a single-chain
               hardware popcnt baseline, both timed by the same harness on this
               machine, best of three alternating runs

GATE is 2.75x where the CPU has AVX512_VPOPCNTDQ. Calibration on Sapphire Rapids
(gcc 14, 512 B codes, x popcnt baseline): 512-bit vpopcntq paths 3.4-4.2x; best
non-vpopcntq paths ~2.1-2.2x (target_clones auto-vectorization at -O3, zmm nibble
LUT), ymm LUT / Harley-Seal 1.6-1.8x, 4-accumulator popcnt 1.4x. Else 1.4x with
AVX2 (ymm paths ~1.6-1.8x), else 1.2x.
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
DEADLINE = time.monotonic() + 270


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
    if {"avx512f", "avx512_vpopcntdq"} <= f:
        return 2.75
    return 1.4 if "avx2" in f else 1.2


def grade():
    W.mkdir(parents=True, exist_ok=True)
    b = run(["make", "-C", str(APP), "clean", "all"])
    lib = APP / "libhamming.a"
    checks["builds"] = b.returncode == 0 and lib.exists()
    notes["build_tail"] = (b.stdout + b.stderr)[-800:]
    if checks["builds"]:
        c = run(["gcc", "-O2", str(T / "check.c"), str(lib), "-o", str(W / "check")])
        if c.returncode == 0:
            r = run([str(W / "check")])
            checks["correct"] = r.returncode == 0 and "CHECK OK" in r.stdout
            notes["check"] = r.stdout[-600:]
        else:
            checks["correct"] = False
            notes["check"] = c.stderr[-600:]
        nm = run(["nm", str(lib)]).stdout
        src = " ".join(p.read_text(errors="ignore") for p in APP.glob("*.[ch]"))
        checks["dispatch"] = bool(
            re.search(r"__cpu_model|__cpu_indicator_init|__cpu_features2|\bi\s+\w*hamming", nm)
            or re.search(r"__builtin_cpu_supports|target_clones|__get_cpuid|cpuid", code(src)))
        a = run(["gcc", "-O2", str(T / "harness.c"), str(lib), "-o", str(W / "agent")])
        r_ = run(["gcc", "-O2", str(T / "harness.c"), str(T / "ref_popcnt.c"), "-o", str(W / "ref")])
        if a.returncode == 0 and r_.returncode == 0:
            ag, rf = [], []
            for _ in range(3):
                rf.append(float(run([str(W / "ref")]).stdout))
                ag.append(float(run([str(W / "agent")]).stdout))
            gate = gate_for(flags())
            ratio = max(ag) / max(rf)
            notes.update(agent_gbps=max(ag), ref_gbps=max(rf), ratio=round(ratio, 2), gate=gate)
            checks["fast"] = checks.get("correct", False) and ratio >= gate
        else:
            checks["fast"] = False
            notes["harness"] = (a.stderr + r_.stderr)[-600:]


def main():
    try:
        grade()
    except Exception as e:  # pylint: disable=broad-except  # any grading failure scores 0
        notes["error"] = repr(e)[-600:]
    required = ["builds", "correct", "dispatch", "fast"]
    rewards = {k: float(checks.get(k, False)) for k in required}
    rewards["reward"] = float(all(checks.get(k, False) for k in required))
    print(json.dumps({"checks": checks, "notes": notes}, indent=1))
    pathlib.Path("/logs/verifier").mkdir(parents=True, exist_ok=True)
    with open("/logs/verifier/reward.json", "w", encoding="utf-8") as fh:
        json.dump(rewards, fh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
