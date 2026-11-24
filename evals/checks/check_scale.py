"""Checks for performance-patterns/avx2-scale-function-with-dispatch.

usage: check_scale.py <work dir> [result.json]
"""
import os
import sys
import tempfile

from common import build, compile_only, disasm_summary, emit, result, run

HARNESS = r"""
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
void scale_f32(float *dst, const float *src, float k, size_t n);
int main(void) {
    size_t lens[120]; int nl = 0;
    for (size_t n = 0; n <= 100; n++) lens[nl++] = n;
    size_t big[] = {255, 256, 257, 1000, 1023, 4096, 4099, 10007};
    for (unsigned i = 0; i < sizeof big / sizeof *big; i++) lens[nl++] = big[i];
    srand(7);
    int fails = 0, cases = 0, inplace_fails = 0;
    for (int li = 0; li < nl; li++) {
        size_t n = lens[li];
        for (int so = 0; so < 3; so++) for (int dof = 0; dof < 3; dof += 2) {
            float *s = malloc((n + 32) * sizeof(float)), *d = malloc((n + 32) * sizeof(float));
            float *ps = s + so, *pd = d + dof;
            for (size_t i = 0; i < n + 32 - so; i++) ps[i] = (float)rand() / RAND_MAX * 200 - 100;
            for (size_t i = 0; i < n + 32 - dof; i++) pd[i] = -12345.0f;
            float k = 1.0f + (float)(li % 7) * 0.37f;
            scale_f32(pd, ps, k, n);
            cases++;
            int bad = 0;
            for (size_t i = 0; i < n; i++) if (pd[i] != ps[i] * k) bad = 1;
            for (size_t i = n; i < n + 32 - dof; i++) if (pd[i] != -12345.0f) bad = 2;   /* wrote past the end */
            if (bad) { fails++; if (fails < 10) printf("FAIL n=%zu src_off=%d dst_off=%d (%s)\n", n, so, dof, bad == 1 ? "wrong value" : "wrote past n"); }
            free(s); free(d);
        }
        /* in-place call (dst == src): informational only */
        float *b = malloc((n + 8) * sizeof(float)), *ref = malloc((n + 8) * sizeof(float));
        for (size_t i = 0; i < n; i++) { b[i] = (float)i * 0.5f - 3; ref[i] = b[i] * 3.0f; }
        scale_f32(b, b, 3.0f, n);
        if (n && memcmp(b, ref, n * sizeof(float))) inplace_fails++;
        free(b); free(ref);
    }
    printf("cases=%d fails=%d inplace_mismatches=%d\n", cases, fails, inplace_fails);
    return fails != 0;
}
"""


def main(work, out=None):
    src = os.path.join(work, "scale.c")
    gb, gno, gavx = "01-builds-with-plain-flags", "02-correct-without-avx2", "03-correct-with-avx2"
    if not os.path.exists(src):
        emit([result(g, False, "scale.c was not created") for g in (gb, gno, gavx)], {}, out)
        return
    ok, log = compile_only(src, ["-O2", "-fPIC"])
    res = [result(gb, ok, ("builds with plain -O2 -fPIC" if ok else "build failed:\n" + log[-1200:]))]
    if not ok:
        res += [result(g, False, "scale.c does not build") for g in (gno, gavx)]
        emit(res, {}, out)
        return
    tmp = tempfile.mkdtemp(prefix="chk-scale-")
    h = os.path.join(tmp, "harness.c")
    open(h, "w").write(HARNESS)
    exe = os.path.join(tmp, "t")
    ok, log = build([h, src], exe, ["-O2", "-fPIC"])
    if not ok:
        res[0] = result(gb, False, "scale.c compiles but scale_f32(float*, const float*, float, size_t) does not link:\n" + log[-1200:])
        res += [result(g, False, "link failure") for g in (gno, gavx)]
        emit(res, {}, out)
        return
    for grader, avx in ((gno, False), (gavx, True)):
        st, o = run(exe, avx)
        label = "AVX2 CPU" if avx else "CPU without AVX2"
        if st == "skipped":
            res.append(result(grader, False, "not testable here: " + o))
        else:
            res.append(result(grader, st == "ok", "%s: %s" % (label, o.strip())))
    emit(res, {"functions_using_wide_regs": disasm_summary(exe)}, out)


if __name__ == "__main__":
    main(*sys.argv[1:])
