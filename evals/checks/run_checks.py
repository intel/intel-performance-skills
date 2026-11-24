"""Run the programmatic checks for one case.

usage: run_checks.py <case-name> <work dir> [result.json]

Cases without a programmatic check print an empty result.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

CHECKS = {
    "dot-product-review-mixed-fleet": ["check_dot_product.py"],
    "avx2-scale-function-with-dispatch": ["check_scale.py"],
    "percentile-sort-with-nan-samples": ["check_percentiles.py"],
    "crc32c-table-lookup-rewrite": ["check_crc32c.py"],
    "rebuild-test-with-debug-info": ["check_pts.py", "rebuild"],
    "optimize-test-end-to-end": ["check_pts.py", "optimize"],
}

if __name__ == "__main__":
    case, work = sys.argv[1], os.path.abspath(sys.argv[2])
    out = os.path.abspath(sys.argv[3]) if len(sys.argv) > 3 else None
    if case not in CHECKS:
        print('{"checks": [], "info": {}}')
        if out:
            open(out, "w").write('{"checks": [], "info": {}}\n')
        sys.exit(0)
    script, *mode = CHECKS[case]
    sys.exit(subprocess.call([sys.executable, os.path.join(HERE, script)] + mode + [work] + ([out] if out else []), cwd=HERE))
