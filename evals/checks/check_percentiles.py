"""Checks for performance-patterns/percentile-sort-with-nan-samples.

usage: check_percentiles.py <work dir> [result.json]

Needs a checkout of https://github.com/intel/x86-simd-sort in $XSS_DIR, and
for code that uses the library's runtime-dispatch API (x86simdsort.h) a static
build of it in $XSS_LIB (libx86simdsortcpp.a); see evals/README.md.
Code that only uses the header-only AVX-512 build is compiled with
-march=haswell for the run, because AVX-512 can't execute under Rosetta.
"""
import os
import re
import sys
import tempfile

from common import build, compile_only, emit, result, run

XSS_DIR = os.environ.get("XSS_DIR", "")
XSS_LIB = os.environ.get("XSS_LIB", "")

HARNESS = r"""
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <vector>
#define main percentiles_fast_main
#include "percentiles_fast.cpp"
#undef main

static bool same(float a, float b) { return (std::isnan(a) && std::isnan(b)) || a == b; }

int main(int argc, char **argv) {
    std::mt19937 rng(2024);
    struct Spec { size_t n; double nan_frac; int kind; };
    const Spec specs[] = {{1000000, 0.02, 0}, {1000003, 0.0, 1}, {5, 0.4, 0}, {3, 1.0, 0}, {1, 0.0, 0},
                          {200000, 0.10, 2}, {2, 0.5, 0}, {1048576, 0.001, 1}, {777, 0.0, 2}};
    int fails = 0, cases = 0;
    double t_new = 0;
    for (const Spec &s : specs) {
        std::vector<float> v(s.n);
        std::uniform_real_distribution<float> u(0.1f, 500.0f);
        std::lognormal_distribution<float> ln(1.5f, 1.0f);
        std::uniform_int_distribution<int> small(0, 50);
        std::uniform_real_distribution<double> coin(0, 1);
        for (size_t i = 0; i < s.n; i++) {
            v[i] = s.kind == 0 ? u(rng) : s.kind == 1 ? ln(rng) : (float)small(rng);
            if (coin(rng) < s.nan_frac) v[i] = NAN;
        }
        if (s.nan_frac == 1.0) for (auto &x : v) x = NAN;
        std::vector<float> ok;
        for (float x : v) if (!std::isnan(x)) ok.push_back(x);
        std::sort(ok.begin(), ok.end());
        size_t failed = v.size() - ok.size();
        float want[4] = {NAN, NAN, NAN, NAN};
        const double qs[4] = {0.50, 0.90, 0.99, 0.999};
        if (!ok.empty()) for (int k = 0; k < 4; k++) want[k] = ok[(size_t)(qs[k] * (ok.size() - 1))];
        auto t0 = std::chrono::steady_clock::now();
        Percentiles got = compute_percentiles(v);
        t_new += std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        float g[4] = {got.p50, got.p90, got.p99, got.p999};
        cases++;
        bool bad = got.failed != failed;
        for (int k = 0; k < 4; k++) bad |= !same(g[k], want[k]);
        if (bad) {
            fails++;
            std::printf("FAIL n=%zu nan_frac=%.3f: got p50=%g p90=%g p99=%g p999=%g failed=%zu; want %g %g %g %g failed=%zu\n",
                        s.n, s.nan_frac, g[0], g[1], g[2], g[3], (size_t)got.failed, want[0], want[1], want[2], want[3], failed);
        }
    }
    std::printf("cases=%d fails=%d new_total_seconds=%.3f\n", cases, fails, t_new);
    return fails != 0;
}
"""


def main(work, out=None):
    gc, gr = "05-compiles", "06-same-results"
    src = os.path.join(work, "percentiles_fast.cpp")
    if not os.path.exists(src):
        emit([result(gc, False, "percentiles_fast.cpp was not created"), result(gr, False, "no file")], {}, out)
        return
    code = open(src, errors="replace").read()
    uses_dyn = re.search(r'#\s*include\s*[<"]x86simdsort\.h[>"]', code) is not None
    uses_static = re.search(r'#\s*include\s*[<"](x86simdsort-static-incl\.h|avx512-[^">]+|avx2-[^">]+|xss-[^">]+)[>"]', code) is not None
    inc, link, flags = [], [], ["-std=c++17", "-O2"]
    note = "plain C++17 build"
    if uses_dyn or uses_static:
        if not XSS_DIR:
            emit([result(gc, False, "uses x86-simd-sort but XSS_DIR is not set; cannot check"), result(gr, False, "not checked")], {}, out)
            return
        inc = ["-I" + os.path.join(XSS_DIR, d) for d in ("lib", "src", "utils")]
    if uses_dyn:
        link = [XSS_LIB] if XSS_LIB else []
        note = "x86-simd-sort runtime-dispatch API (lib/x86simdsort.h), linked with libx86simdsortcpp.a"
    compile_flags = flags + inc
    if uses_static and not uses_dyn:
        ok512, log512 = compile_only(src, flags + inc + ["-march=sapphirerapids"], cxx=True)
        note = "x86-simd-sort header-only build; AVX-512 compile %s; run build uses -march=haswell" % ("ok" if ok512 else "FAILED")
        compile_flags = flags + inc + ["-march=haswell"]
    ok, log = compile_only(src, compile_flags, cxx=True)
    if not ok:
        emit([result(gc, False, "%s: compile failed\n%s" % (note, log[-1500:])), result(gr, False, "does not compile")], {"build": note}, out)
        return
    tmp = tempfile.mkdtemp(prefix="chk-pct-")
    h = os.path.join(tmp, "harness.cpp")
    open(h, "w").write(HARNESS)
    exe = os.path.join(tmp, "t")
    ok, log = build([h], exe, compile_flags + ["-I" + work], cxx=True, extra=link)
    if not ok:
        emit([result(gc, False, "%s: compiles alone, but compute_percentiles(std::vector<float>) can't be called/linked:\n%s" % (note, log[-1500:])),
              result(gr, False, "link failure")], {"build": note}, out)
        return
    res = [result(gc, True, note + ": compiles and links")]
    lines, passed = [], True
    for avx in (False, True):
        st, o = run(exe, avx, timeout=600)
        if st == "skipped":
            continue
        passed &= st == "ok"
        lines.append("%s: %s" % ("AVX2 CPU" if avx else "CPU without AVX", o.strip().replace("\n", " | ")))
    res.append(result(gr, passed, "\n".join(lines)))
    emit(res, {"build": note}, out)


if __name__ == "__main__":
    main(*sys.argv[1:])
