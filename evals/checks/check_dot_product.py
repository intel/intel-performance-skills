"""Checks for performance-patterns/dot-product-review-mixed-fleet.

usage: check_dot_product.py <work dir> [result.json]
"""
import os
import sys
import tempfile

from common import build, compile_only, disasm_summary, emit, result, run

HARNESS = r"""
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
float dot(const float *a, const float *b, int n);
int main(void) {
    static const int lens[] = {0,1,2,3,4,5,7,8,9,15,16,17,31,32,33,63,64,65,100,127,128,129,
                               255,256,257,1000,1023,1024,1025,4093,4096,4099,10007};
    srand(12345);
    int fails = 0; double worst = 0;
    for (unsigned k = 0; k < sizeof lens / sizeof *lens; k++) {
        for (int mis = 0; mis < 2; mis++) {
            int n = lens[k];
            float *a = malloc((n + 16) * sizeof(float)), *b = malloc((n + 16) * sizeof(float));
            float *pa = a + mis, *pb = b + 2 * mis;
            for (int i = 0; i < n; i++) {
                pa[i] = (float)rand() / RAND_MAX * 2 - 1;
                pb[i] = (float)rand() / RAND_MAX * 2 - 1;
            }
            double ref = 0, mag = 0;
            for (int i = 0; i < n; i++) { ref += (double)pa[i] * pb[i]; mag += fabs((double)pa[i] * pb[i]); }
            float got = dot(pa, pb, n);
            double err = fabs((double)got - ref) / (mag > 0 ? mag : 1);
            if (!(err <= 1e-4)) { fails++; printf("FAIL n=%d misaligned=%d got=%.9g ref=%.9g relerr=%.3g\n", n, mis, got, ref, err); }
            if (err > worst) worst = err;
            free(a); free(b);
        }
    }
    printf("cases=%d fails=%d worst_relerr=%.3g\n", (int)(2 * sizeof lens / sizeof *lens), fails, worst);
    return fails != 0;
}
"""


def main(work, out=None):
    src = os.path.join(work, "dot_fast.c")
    g_no, g_avx = "05-correct-without-avx", "06-correct-with-avx2"
    if not os.path.exists(src):
        emit([result(g_no, False, "dot_fast.c was not created"), result(g_avx, False, "dot_fast.c was not created")], {}, out)
        return
    ok, log = compile_only(src, ["-O2", "-fPIC"])
    if not ok:
        emit([result(g_no, False, "dot_fast.c does not build with plain -O2 -fPIC:\n" + log[-1200:]),
              result(g_avx, False, "dot_fast.c does not build with plain -O2 -fPIC")], {}, out)
        return
    tmp = tempfile.mkdtemp(prefix="chk-dot-")
    h = os.path.join(tmp, "harness.c")
    open(h, "w").write(HARNESS)
    exe = os.path.join(tmp, "t")
    ok, log = build([h, src], exe, ["-O2", "-fPIC"], extra=["-lm"])
    if not ok:
        emit([result(g_no, False, "builds alone but fails to link with a caller:\n" + log[-1200:]),
              result(g_avx, False, "link failure")], {}, out)
        return
    res = []
    for grader, avx in ((g_no, False), (g_avx, True)):
        st, o = run(exe, avx)
        label = "AVX2+FMA CPU" if avx else "CPU without AVX"
        if st == "skipped":
            res.append(result(grader, False, "not testable here: " + o))
        else:
            res.append(result(grader, st == "ok", "%s: %s" % (label, o.strip().splitlines()[-1] if o.strip() else st)
                              + ("\n" + o if st != "ok" else "")))
    emit(res, {"functions_using_wide_regs": disasm_summary(exe, skip=("main", "dot_ref"))}, out)


if __name__ == "__main__":
    main(*sys.argv[1:])
