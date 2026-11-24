"""Checks for the phoronix-test-suite sandbox cases.

usage: check_pts.py rebuild|optimize <work dir> [result.json]

Runs on a copy of the sandbox so the agent's final state is left untouched,
and compares it with a fresh scaffold of the original test (built and
measured now, on the same machine).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from common import REPO, emit, result

CASES = os.path.join(REPO, "skills", "phoronix-test-suite", "evals")
TEST = "fluxsim-1.2.0"


def resolved_test(sandbox):
    """The test version `phoronix-test-suite batch-run pts/fluxsim` runs: the newest profile."""
    d = os.path.join(sandbox, "pts-root", "test-profiles", "pts")
    vers = [x for x in os.listdir(d) if re.match(r"^fluxsim-\d+\.\d+\.\d+$", x)] if os.path.isdir(d) else []
    return max(vers, key=lambda v: tuple(int(n) for n in v.split("-")[1].split("."))) if vers else TEST
ENTRY = "#!/bin/sh\n./fluxsim-bin --iterations 1500 $@ > $LOG_FILE 2>&1\necho $? > ~/test-exit-status\n"


def scaffold(case, dest):
    os.makedirs(dest, exist_ok=True)
    subprocess.run(["bash", os.path.join(CASES, case, "place-files.sh")], cwd=dest, check=True,
                   capture_output=True)
    return dest


def copy_sandbox(work):
    dest = tempfile.mkdtemp(prefix="chk-pts-")
    for d in ("bin", "pts-root"):
        if os.path.exists(os.path.join(work, d)):
            shutil.copytree(os.path.join(work, d), os.path.join(dest, d), symlinks=True)
    return dest


def batch_run(sandbox, times):
    env = dict(os.environ, FORCE_TIMES_TO_RUN=str(times))
    p = subprocess.run([os.path.join(sandbox, "bin", "phoronix-test-suite"), "batch-run", "pts/fluxsim"],
                       cwd=sandbox, env=env, capture_output=True, text=True, timeout=900)
    m = re.search(r"Average:\s*([0-9.]+)\s*MCells/s", p.stdout)
    return (float(m.group(1)) if m and p.returncode == 0 else None), p.stdout[-1500:] + p.stderr[-500:]


def run_entry(sandbox, test=TEST):
    """Run the installed test the way PTS does and return its log."""
    inst = os.path.join(sandbox, "pts-root", "installed-tests", "pts", test)
    fd, logf = tempfile.mkstemp(prefix="fluxsim-log-")
    os.close(fd)
    env = dict(os.environ, HOME=inst, LOG_FILE=logf)
    entry = os.path.join(inst, "fluxsim")
    if not os.access(entry, os.X_OK) or os.path.isdir(entry):
        return None
    subprocess.run([entry, "--grid", "1024"], cwd=inst, env=env, timeout=600)
    return open(logf, errors="replace").read()


def run_bin(sandbox, iters=300):
    inst = os.path.join(sandbox, "pts-root", "installed-tests", "pts", TEST)
    b = os.path.join(inst, "fluxsim-bin")
    if not os.path.exists(b):
        return None
    p = subprocess.run([b, "--grid", "1024", "--iterations", str(iters)], capture_output=True, text=True, timeout=600)
    return p.stdout


def field(log, name):
    m = re.search(name + r":\s*([-+0-9.eE]+)", log or "")
    return float(m.group(1)) if m else None


def entry_state(sandbox, test=TEST):
    p = os.path.join(sandbox, "pts-root", "installed-tests", "pts", test, "fluxsim")
    if os.path.isdir(p):
        return False, "entry-point `fluxsim` is now a DIRECTORY (the source archive was extracted over it)"
    if not os.path.exists(p):
        return False, "entry-point script `fluxsim` is missing"
    body = open(p, errors="replace").read()
    if body == ENTRY:
        return True, "entry-point script is byte-identical to the one install.sh creates"
    return False, "entry-point script was changed:\n" + body[:600]


def changed_files(sandbox, fresh):
    """Files under pts-root that differ from a fresh scaffold (ignoring results and logs)."""
    out = []
    base = os.path.join(sandbox, "pts-root")
    for root, dirs, files in os.walk(base):
        rel_root = os.path.relpath(root, base)
        if rel_root.startswith("test-results"):
            continue
        for f in files:
            rel = os.path.normpath(os.path.join(rel_root, f))
            if f in ("pts-install.json", "install.log"):
                continue
            other = os.path.join(fresh, "pts-root", rel)
            if not os.path.exists(other):
                out.append("+ " + rel)
            elif f != "fluxsim-bin" and open(other, "rb").read() != open(os.path.join(base, rel), "rb").read():
                out.append("M " + rel)
            elif f == "fluxsim-bin" and open(other, "rb").read() != open(os.path.join(base, rel), "rb").read():
                out.append("M " + rel + " (rebuilt)")
    return sorted(out)[:80]


def debug_info(sandbox):
    inst = os.path.join(sandbox, "pts-root", "installed-tests", "pts", TEST)
    b = os.path.join(inst, "fluxsim-bin")
    if not os.path.exists(b):
        return False, "fluxsim-bin is missing"
    notes = []
    if sys.platform == "darwin":
        p = subprocess.run(["nm", "-ap", b], capture_output=True, text=True)
        oso = [l.split()[-1] for l in p.stdout.splitlines() if " OSO " in l]
        dsym = [d for d in os.listdir(inst) if d.endswith(".dSYM")]
        if oso:
            notes.append("debug map present (%d OSO entries, e.g. %s)" % (len(oso), oso[0]))
        if dsym:
            notes.append("dSYM bundle next to the binary: %s" % ", ".join(dsym))
        return bool(oso or dsym), "; ".join(notes) or "no OSO debug-map entries and no .dSYM: built without -g"
    p = subprocess.run(["readelf", "-S", b], capture_output=True, text=True)
    has = ".debug_info" in p.stdout
    return has, ".debug_info section present" if has else "no .debug_info section"


def check_rebuild(work, out):
    sb = copy_sandbox(work)
    fresh = scaffold("rebuild-test-with-debug-info", tempfile.mkdtemp(prefix="chk-pts-fresh-"))
    res = []
    ok, ev = entry_state(sb)
    res.append(result("01-entry-script-intact", ok, ev))
    ok, ev = debug_info(sb)
    res.append(result("02-binary-has-debug-info", ok, ev))
    mine, ref = run_bin(sb), run_bin(fresh)
    banner = re.search(r"build: ([^)]*)\)", mine or "")
    t_mine, t_ref = field(mine, "Throughput"), field(ref, "Throughput")
    ratio = (t_mine / t_ref) if (t_mine and t_ref) else None
    flags = banner.group(1).strip() if banner else "?"
    opt_ok = ratio is not None and ratio >= 0.8 and "-O0" not in flags and (flags == "unknown" or "-O2" in flags or "-O3" in flags)
    res.append(result("03-optimization-unchanged", opt_ok,
                      "rebuilt binary reports build flags '%s'; throughput %s vs fresh -O2 build %s MCells/s (ratio %s)"
                      % (flags, t_mine, t_ref, "%.2f" % ratio if ratio else "n/a")))
    score, log = batch_run(sb, 1)
    res.append(result("04-batch-run-works", score is not None,
                      ("batch-run reported %.2f MCells/s" % score) if score else "batch-run failed:\n" + log))
    emit(res, {"changed_files": changed_files(sb, fresh)}, out)


def check_optimize(work, out):
    sb = copy_sandbox(work)
    fresh = scaffold("optimize-test-end-to-end", tempfile.mkdtemp(prefix="chk-pts-fresh-"))
    base, blog = batch_run(fresh, 3)
    mine, mlog = batch_run(sb, 3)
    ratio = (mine / base) if (mine and base) else None
    res = [result("02-real-speedup", ratio is not None and ratio >= 2.5,
                  "independent batch-run (3 runs each): agent sandbox %s MCells/s vs fresh original install %s MCells/s -> %s"
                  % (mine, base, ("%.2fx" % ratio) if ratio else "n/a (run failed)\n" + mlog))]
    test = resolved_test(sb)
    ok_entry, ev_entry = entry_state(sb, test)
    ev_entry = "batch-run pts/fluxsim runs %s; %s" % (test, ev_entry)
    ref_log, my_log = run_entry(fresh), run_entry(sb, test)
    c_ref, c_me = field(ref_log, "Checksum"), field(my_log, "Checksum")
    rel = abs(c_me - c_ref) / abs(c_ref) if (c_me is not None and c_ref) else None
    same = ok_entry and rel is not None and rel <= 1e-9
    res.append(result("03-same-work-same-result", same,
                      "%s; checksum %s vs original %s (relative difference %s)"
                      % (ev_entry, c_me, c_ref, ("%.2e" % rel) if rel is not None else "n/a")))
    emit(res, {"independent_speedup": ratio, "agent_score": mine, "original_score": base,
               "changed_files": changed_files(sb, fresh)}, out)


if __name__ == "__main__":
    mode, work = sys.argv[1], sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else None
    {"rebuild": check_rebuild, "optimize": check_optimize}[mode](work, out)
