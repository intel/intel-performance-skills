"""Shared helpers for the programmatic eval checks.

The checks build the code an agent produced for x86-64 and run it on two CPU
profiles: one without AVX (SSE4.2/PCLMUL only) and one with AVX2+FMA.

- macOS on Apple Silicon: builds with `clang -arch x86_64` and runs under
  Rosetta 2, which executes up to AVX2/FMA but only reports AVX in CPUID when
  ROSETTA_ADVERTISE_AVX=1 is set. That gives both profiles on one machine.
  AVX-512 code can be compiled but not run.
- Linux x86-64: runs natively for the AVX2 profile (the host must have AVX2)
  and under `qemu-x86_64 -cpu Westmere` for the no-AVX profile when qemu-user
  is installed; otherwise that profile is reported as skipped.
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

DARWIN_ARM = sys.platform == "darwin" and platform.machine() == "arm64"


def compiler(cxx=False):
    if DARWIN_ARM:
        return ["clang++" if cxx else "clang", "-arch", "x86_64"]
    return ["c++" if cxx else "cc"]


def build(srcs, out, flags=(), cxx=False, extra=(), cwd=None):
    """Compile+link; returns (ok, combined output)."""
    cmd = compiler(cxx) + list(flags) + list(srcs) + ["-o", out] + list(extra)
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode == 0, " ".join(cmd) + "\n" + p.stdout + p.stderr


def compile_only(src, flags=(), cxx=False, cwd=None):
    cmd = compiler(cxx) + list(flags) + ["-c", src, "-o", os.devnull]
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode == 0, " ".join(cmd) + "\n" + p.stdout + p.stderr


def run(binary, avx2, args=(), timeout=300, cwd=None):
    """Run an x86-64 test binary on the no-AVX or AVX2 CPU profile.
    Returns (status, stdout+stderr) where status is 'ok', 'fail' or 'skipped'."""
    env = dict(os.environ)
    cmd = [binary] + list(args)
    if DARWIN_ARM:
        if avx2:
            env["ROSETTA_ADVERTISE_AVX"] = "1"
        else:
            env.pop("ROSETTA_ADVERTISE_AVX", None)
    elif not avx2:
        q = shutil.which("qemu-x86_64")
        if not q:
            return "skipped", "no-AVX profile needs qemu-x86_64 (qemu-user) on Linux"
        cmd = [q, "-cpu", "Westmere"] + cmd
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return "fail", "timed out after %ds" % timeout
    return ("ok" if p.returncode == 0 else "fail"), p.stdout + p.stderr


def disasm_summary(binary, skip=("main",)):
    """Which functions in a binary use ymm/zmm registers or the crc32/pclmul
    instructions - evidence for the LLM grader about what the code compiles to."""
    tool = shutil.which("objdump")
    if not tool:
        return {}
    p = subprocess.run([tool, "-d", "--no-show-raw-insn", binary], capture_output=True, text=True)
    funcs, cur = {}, None
    insn = re.compile(r"^\s*[0-9a-f]+:\s+([a-z0-9.]+)\s*(.*)$")
    for line in p.stdout.splitlines():
        if line.endswith(">:"):
            cur = line.split("<", 1)[1][:-2]
            funcs[cur] = set()
            continue
        m = insn.match(line.lower())
        if cur is None or not m:
            continue
        mnem, ops = m.group(1), m.group(2).split("<")[0]
        if "%zmm" in ops:
            funcs[cur].add("zmm")
        if "%ymm" in ops:
            funcs[cur].add("ymm")
        if mnem.startswith("crc32"):
            funcs[cur].add("crc32")
        if "pclmul" in mnem:
            funcs[cur].add("pclmul")
        if mnem == "vzeroupper":
            funcs[cur].add("vzeroupper")
        if mnem.startswith("vfmadd"):
            funcs[cur].add("fma")
    return {f: sorted(t) for f, t in funcs.items() if t and f.lstrip("_") not in skip}


def new_files(work, original_dir, exts):
    """Files with the given extensions in work/ that the agent created or changed."""
    out = []
    for name in sorted(os.listdir(work)):
        path = os.path.join(work, name)
        if not os.path.isfile(path) or not name.endswith(tuple(exts)):
            continue
        orig = os.path.join(original_dir, name)
        if os.path.exists(orig) and open(orig, "rb").read() == open(path, "rb").read():
            continue
        out.append(path)
    return out


def result(grader, passed, evidence):
    return {"grader": grader, "passed": bool(passed), "evidence": evidence.strip()[:1500]}


def emit(results, info, out_path=None):
    doc = {"checks": results, "info": info}
    text = json.dumps(doc, indent=2)
    if out_path:
        with open(out_path, "w") as f:
            f.write(text + "\n")
    print(text)
