"""Checks for performance-patterns/crc32c-table-lookup-rewrite.

usage: check_crc32c.py <work dir> [result.json]

Builds every .c file the agent added (except the original crc32c.c and any
file with a main()) with plain -O2 -fPIC, links it against a harness that
compares it with the original table implementation, and measures throughput
on 64 KiB blocks (informational only; under Rosetta the numbers are emulated).
"""
import os
import re
import sys
import tempfile

from common import REPO, build, disasm_summary, emit, new_files, result, run

ORIG = os.path.join(REPO, "skills", "performance-patterns", "evals", "crc32c-table-lookup-rewrite", "files", "crc32c.c")

HARNESS = r"""
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
uint32_t crc32c(uint32_t crc, const void *buf, size_t len);
uint32_t crc32c_ref(uint32_t crc, const void *buf, size_t len);
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }
static double gbps(uint32_t (*f)(uint32_t, const void *, size_t), const uint8_t *b, size_t n) {
    uint32_t c = ~0u; int it = 0; double t0 = now(), t;
    do { for (int k = 0; k < 64; k++) c = f(c, b, n); it += 64; t = now() - t0; } while (t < 0.4);
    if (c == 0x12345678) printf(" ");
    return (double)n * it / t / 1e9;
}
int main(int argc, char **argv) {
    enum { MAX = 70000 + 64 };
    uint8_t *buf = malloc(MAX);
    srand(99);
    for (int i = 0; i < MAX; i++) buf[i] = (uint8_t)rand();
    int fails = 0, cases = 0;
    uint32_t chk = ~crc32c(~0u, "123456789", 9);
    if (chk != 0xE3069283u) { fails++; printf("FAIL check string: ~crc32c(~0u, \"123456789\", 9) = %08x, want e3069283\n", chk); }
    cases++;
    if (crc32c(0x1234abcd, buf, 0) != 0x1234abcd) { fails++; printf("FAIL empty buffer must return the seed unchanged\n"); }
    cases++;
    static const size_t fixed[] = {1,2,3,7,8,9,15,16,17,23,24,25,31,32,33,63,64,65,127,128,129,191,192,193,255,256,257,
                                   383,384,385,511,512,513,1023,1024,1025,2047,2048,4095,4096,4097,8191,8192,16383,
                                   16384,32768,65535,65536,65537,70000};
    for (unsigned k = 0; k < sizeof fixed / sizeof *fixed; k++)
        for (int off = 0; off < 8; off += 3) {
            uint32_t seed = (uint32_t)rand() * 2654435761u;
            uint32_t a = crc32c(seed, buf + off, fixed[k]), r = crc32c_ref(seed, buf + off, fixed[k]);
            cases++;
            if (a != r) { fails++; if (fails < 12) printf("FAIL len=%zu off=%d seed=%08x got=%08x want=%08x\n", fixed[k], off, seed, a, r); }
        }
    for (int t = 0; t < 400; t++) {
        size_t len = (size_t)rand() % 70001, off = (size_t)rand() % 64;
        uint32_t seed = t & 1 ? ~0u : (uint32_t)rand();
        uint32_t a = crc32c(seed, buf + off, len), r = crc32c_ref(seed, buf + off, len);
        cases++;
        if (a != r) { fails++; if (fails < 12) printf("FAIL random len=%zu off=%zu got=%08x want=%08x\n", len, off, a, r); }
    }
    for (int t = 0; t < 100; t++) {   /* chained calls */
        size_t len = (size_t)rand() % 70000, cut = len ? (size_t)rand() % len : 0;
        uint32_t a = crc32c(crc32c(~0u, buf + 5, cut), buf + 5 + cut, len - cut), r = crc32c_ref(~0u, buf + 5, len);
        cases++;
        if (a != r) { fails++; if (fails < 12) printf("FAIL chained len=%zu cut=%zu got=%08x want=%08x\n", len, cut, a, r); }
    }
    printf("cases=%d fails=%d\n", cases, fails);
    if (argc > 1 && !fails) {
        printf("throughput_64k_GBps new=%.2f original=%.2f\n", gbps(crc32c, buf, 65536), gbps(crc32c_ref, buf, 65536));
        printf("throughput_4k_GBps new=%.2f original=%.2f\n", gbps(crc32c, buf, 4096), gbps(crc32c_ref, buf, 4096));
    }
    return fails != 0;
}
"""


def main(work, out=None):
    g = "01-identical-checksums"
    fixture_dir = os.path.dirname(ORIG)
    srcs = [p for p in new_files(work, fixture_dir, (".c",))
            if os.path.basename(p) != "crc32c.c" and not re.search(r"\bmain\s*\(", open(p, errors="replace").read())]
    if not any(os.path.basename(p) == "crc32c_fast.c" for p in srcs):
        emit([result(g, False, "crc32c_fast.c was not created")], {"sources": [os.path.basename(p) for p in srcs]}, out)
        return
    tmp = tempfile.mkdtemp(prefix="chk-crc-")
    h = os.path.join(tmp, "harness.c")
    open(h, "w").write(HARNESS)
    ref_o = os.path.join(tmp, "ref.c")
    open(ref_o, "w").write(open(ORIG).read().replace("uint32_t crc32c(", "uint32_t crc32c_ref("))
    exe = os.path.join(tmp, "t")
    ok, log = build([h, ref_o] + srcs, exe, ["-O2", "-fPIC"])
    info = {"sources": [os.path.basename(p) for p in srcs]}
    if not ok:
        emit([result(g, False, "does not build/link with plain -O2 -fPIC:\n" + log[-1500:])], info, out)
        return
    lines, passed = [], True
    for avx in (False, True):
        st, o = run(exe, avx, ["bench"])
        label = "AVX2 CPU (no AVX-512)" if avx else "CPU without AVX (SSE4.2+PCLMUL)"
        if st == "skipped":
            passed = False
            lines.append("%s: not testable here (%s)" % (label, o))
            continue
        passed &= st == "ok"
        lines.append("%s: %s" % (label, o.strip().replace("\n", " | ")))
        info["throughput_" + ("avx2" if avx else "noavx")] = [l for l in o.splitlines() if l.startswith("throughput")]
    info["functions_using_crc_or_wide_regs"] = disasm_summary(exe, skip=("main", "crc32c_ref", "gbps", "now"))
    emit([result(g, passed, "\n".join(lines))], info, out)


if __name__ == "__main__":
    main(*sys.argv[1:])
