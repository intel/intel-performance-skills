"""Grades the source-review task; writes /logs/verifier/reward.json.

The module under /app/src carries N=10 seeded performance problems, each one a
row of the skill's source-review table (triggers/from-source.md), and 4 decoys:
code that looks like one of those rows but is already right.

A finding {"file", "line", "problem", "fix"} hits a seeded issue when its file has
the issue's basename, its line falls in one of the issue's ranges, its problem text
is non-empty, and problem+fix mention at least one broad synonym for the issue (so
a line-range hit for an unrelated remark does not count). A finding whose line falls
in a decoy range is a false positive whatever it says. Ranges are resolved from
anchor regexes against the pristine copy in /tests/src, never against /app.

Checks, all required:
  valid_json              /app/review.json is a list of {file, line, problem} objects
  recall                  >= K of the N seeded issues hit
  decoy_false_positives   <= 1 decoy flagged

Why K = 7 of 10.  The seeded issues split by how much a careful generalist reviewer
finds without the skill:
  widely known (G, 5):  false_sharing, cv_notify_all, tas_spinlock, shared_stats
                        counter (per-CPU stats), serial_accumulator.  Textbook
                        concurrency/perf review items; we assume a strong
                        generalist gets all five.
  skill-specific (S, 5): missing_restrict (the loop looks fine and does vectorize
                        at -O3, behind an alias check), missing_vzeroupper (a short
                        inline-asm copy; nothing looks wrong), simd_sort (std::sort
                        is the idiomatic call), crc32c (byte-table CRC; the skill
                        flags it by name), hamming (per-byte popcount; the skill
                        flags it by name).
K = |G| + 2 so even a reviewer who gets every widely known item must also find two
of the skill-checklist items.  crc32c and hamming are the S items a generalist is
likeliest to reach on their own, so a lucky generalist can pass; one that stops at
the concurrency items cannot.  The decoy bound (<= 1 of 4) stops a reviewer from
clearing recall by flagging every reduction, lock, per-thread array and sort.
"""
import json
import pathlib
import re

APP = pathlib.Path("/app")
SRC = pathlib.Path(__file__).resolve().parent / "src"
K = 7
MAX_DECOY_FP = 1


def S(file, start, end=None, before=0):
    """A line range in SRC/file: from the first line matching `start` (minus
    `before` lines of leading comment) to the first line at or after it matching
    `end` (default: the start line itself)."""
    return (file, start, end, before)


# name -> (ranges, synonym regex over problem+fix, lower-cased)
SEEDED = {
    # from-source.md: "Single FP variable updated in a loop" -> parallel-accumulator.md
    "serial_accumulator": (
        [S("kernels.c", r"^float vec_mean_f32", r"^}", 1)],
        r"accumul|depend|latency|unroll|simd|vectori|reduc|chain|serial|partial sum"),
    # "C function with two or more pointer parameters ... no restrict" -> missing-restrict.md
    "missing_restrict": (
        [S("kernels.c", r"^void vec_affine_f32", r"^}", 2),
         S("scoring.h", r"vec_affine_f32", r"size_t n\);")],
        r"restrict|alias|overlap"),
    # "Inline asm ... ymm ... no vzeroupper" -> missing-vzeroupper.md
    "missing_vzeroupper": (
        [S("kernels.c", r"^void block_copy_f32x32", r"^}", 2),
         S("pool.cpp", r"for \(int t = 0; t < SCORING_DIM; t \+= 32\)", r"block_copy_f32x32")],
        r"vzeroupper|zeroupper|transition|upper|\bsse\b|state|penalt"),
    # "byte-by-byte table-lookup CRC32C loop" / name crc32c -> fast-crc32c.md
    "crc32c": (
        [S("kernels.c", r"^#define CRC32C_POLY", r"^}", 0),
         S("kernels.c", r"^uint32_t block_crc32c", r"^}", 2),
         S("pool.cpp", r"uint32_t crc = block_crc32c")],
        r"crc|sse4|hardware|pclmul|slic|table|instruction|byte"),
    # "Function/loop named ... hamming_distance" -> references/known-algorithms-impl.md
    "hamming": (
        [S("kernels.c", r"^uint32_t hamming_distance", r"^}", 2),
         S("pool.cpp", r"uint32_t hd = hamming_distance")],
        r"popcnt|popcount|simd|avx|vector|word|64.?bit|byte|lookup|intrinsic"),
    # "Spinlock body ... cmpxchg with no prior read" -> ttas.md
    "tas_spinlock": (
        [S("pool.cpp", r"^class ShardLock", r"^};", 2)],
        r"ttas|test.?and.?test|spin|exchange|xchg|\bread|load|cache.?line|bounc|contend|contention|futex|mutex|backoff"),
    # "Struct fields written by different threads, no alignas(64)" -> false-sharing.md
    "false_sharing": (
        [S("pool.cpp", r"^  void note_progress", r"^  }"),
         S("pool.cpp", r"^  uint64_t batches_done_\[", r"^  uint64_t candidates_done_\[")],
        r"false.?shar|cache.?line|pad|align|bounc|contention|invalidat|per.?thread|coheren"),
    # "Global count++ / atomic_fetch_add on a statistics field in a hot path" -> per-cpu-stats.md
    "shared_stats_counter": (
        [S("pool.cpp", r"^std::atomic<uint64_t> g_candidates_scored", r"^std::atomic<uint64_t> g_bytes_ingested"),
         S("pool.cpp", r"g_bytes_ingested\.fetch_add", r"memory_order_relaxed\);"),
         S("pool.cpp", r"g_candidates_scored\.fetch_add")],
        r"atomic|contention|contend|cache.?line|per.?cpu|per.?thread|per.?worker|thread.?local|"
        r"batch|shar|bounc|local|lock add|xadd"),
    # "cv.notify_all() waking a thread pool" -> cv-thundering-herd.md
    "cv_notify_all": (
        [S("pool.cpp", r"^  void submit\(Job job\)", r"^  }", 1),
         S("pool.cpp", r"cv_\.wait\(g,")],
        r"notify|wake|herd|broadcast|futex|context.?switch"),
    # "std::sort ... on float arrays" -> simd-sort.md
    "simd_sort": (
        [S("pool.cpp", r"^void rank_scores", r"^}", 2),
         S("pool.cpp", r"^      rank_scores\(scores\);")],
        r"sort|simd|avx|vector|radix|select|heap|top.?k|partial"),
}

DECOYS = {
    # four-chain AVX2 FMA + four-accumulator scalar dot, restrict, compiler-managed vzeroupper
    "dot_multi_accumulator": [S("kernels.c", r"^/\* Inner product\.", r"^static dot_fn dot_impl", 0),
                              S("kernels.c", r"^float vec_dot_f32", r"^}")],
    # TTAS done right (try-first, read-spin with pause), lock on its own line
    "ttas_lock": [S("pool.cpp", r"^class MetricsLock", r"^};", 2)],
    # per-worker struct already alignas(64)
    "aligned_worker_scratch": [S("pool.cpp", r"^struct alignas\(64\) WorkerScratch", r"^};", 1),
                               S("pool.cpp", r"^  static void note_probe", r"^  }"),
                               S("pool.cpp", r"^  WorkerScratch scratch_\[")],
    # std::sort of <= 8 floats, once at startup
    "cold_small_sort": [S("pool.cpp", r"^std::vector<float> load_cutpoints", r"^}", 2)],
}


def resolve(spec):
    file, start, end, before = spec
    lines = (SRC / file).read_text().splitlines()
    s = next(i for i, l in enumerate(lines) if re.search(start, l))
    e = s if end is None else next(i for i in range(s, len(lines)) if re.search(end, lines[i]))
    return file, s + 1 - before, e + 1


def load_findings(notes):
    p = APP / "review.json"
    if not p.exists():
        notes["json"] = "missing /app/review.json"
        return None
    try:
        data = json.loads(p.read_text())
    except (ValueError, UnicodeDecodeError) as e:
        notes["json"] = f"parse error: {e}"
        return None
    if isinstance(data, dict) and isinstance(data.get("findings"), list):
        data = data["findings"]
    if not isinstance(data, list):
        notes["json"] = "top level is not a list"
        return None
    out = []
    for i, f in enumerate(data):
        try:
            line = int(f["line"])
            file = str(f["file"])
            problem = str(f["problem"])
        except (KeyError, TypeError, ValueError):
            notes["json"] = f"entry {i} lacks file/line/problem"
            return None
        out.append({"file": file, "line": line, "problem": problem, "fix": str(f.get("fix", ""))})
    return out


def grade(findings, notes):
    seeded = {k: ([resolve(r) for r in v[0]], re.compile(v[1])) for k, v in SEEDED.items()}
    decoys = {k: [resolve(r) for r in v] for k, v in DECOYS.items()}
    spans = [(k, r) for k, (rs, _) in seeded.items() for r in rs] + \
            [(k, r) for k, rs in decoys.items() for r in rs]
    for i, (ka, (fa, sa, ea)) in enumerate(spans):  # ranges must not overlap
        for kb, (fb, sb, eb) in spans[i + 1:]:
            assert not (fa == fb and sa <= eb and sb <= ea), (ka, kb)

    def inside(f, rs):
        base = pathlib.PurePosixPath(f["file"].replace("\\", "/")).name
        return any(base == file and s <= f["line"] <= e for file, s, e in rs)

    hit, fp = set(), set()
    for f in findings:
        text = (f["problem"] + " " + f["fix"]).lower()
        for k, (rs, syn) in seeded.items():
            if f["problem"].strip() and inside(f, rs) and syn.search(text):
                hit.add(k)
        for k, rs in decoys.items():
            if inside(f, rs):
                fp.add(k)
    notes.update(found=sorted(hit), missed=sorted(set(SEEDED) - hit),
                 decoys_flagged=sorted(fp), n_findings=len(findings),
                 ranges={k: [f"{f}:{s}-{e}" for f, s, e in v[0]] for k, v in seeded.items()})
    return hit, fp


def main():
    checks, notes = {}, {}
    findings = load_findings(notes)
    checks["valid_json"] = findings is not None
    hit, fp = grade(findings or [], notes)
    checks["recall"] = checks["valid_json"] and len(hit) >= K
    checks["decoy_false_positives"] = checks["valid_json"] and len(fp) <= MAX_DECOY_FP
    notes.update(recall=f"{len(hit)}/{len(SEEDED)} (need {K})",
                 decoy_fp=f"{len(fp)}/{len(DECOYS)} (max {MAX_DECOY_FP})")
    required = ["valid_json", "recall", "decoy_false_positives"]
    rewards = {k: float(checks[k]) for k in required}
    rewards["reward"] = float(all(checks[k] for k in required))
    print(json.dumps({"checks": checks, "notes": notes}, indent=1))
    pathlib.Path("/logs/verifier").mkdir(parents=True, exist_ok=True)
    with open("/logs/verifier/reward.json", "w", encoding="utf-8") as fh:
        json.dump(rewards, fh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
