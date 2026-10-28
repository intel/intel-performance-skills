"""Grades lp-hotspot-report; writes /logs/verifier/reward.json.

Ground truth comes from skills/linux-perf (paths relative to the skill directory);
all of it is references/flow-e.md unless noted.

  top_table
      "Phase 3 -- Top functions table": a Markdown table of the top functions with
      their share. From perf_report.txt: accumulate_scores 58.42%, worker_update_stats
      18.77%, __memmove_avx_unaligned_erms 7.11% (rounded or exact accepted).
  top_table_cutoff (informational, not in the reward)
      Phase 3 "Selection cutoffs": stop when the next function would be below 5%.
      parse_record (4.61%) and everything after it stay out of that table. This is a
      format rule of the flow rather than a correctness one; it is the check a report
      without the flow most often misses.
  annotated_hot_line / annotated_second_function
      "Phase 4 -- Per-function detailed sections", Steps 4a and 4d: every function in
      the table gets annotated source with a within-function percentage per source
      line. From the annotate files: score.c:16 `sum += a[i] * b[i];` = 75.84% -> 76%;
      worker.c:34 `stats[id].processed++;` = 91.04% -> 91%. The instruction-level
      figures (addsd 59.79%, addq 88.41%) are not the line figures.
  serial_accumulator / scalar_fp
      "Step 4e -- Observations" runs references/building-blocks.md "Annotate pattern
      scan": Pattern 1 (Scalar FP: only scalar movsd/mulsd/addsd in the hot loop, no
      packed FP) and Pattern 3 (Serial accumulator: one addsd at 59.79% (> 40%) with
      cache-miss rate 1.07% (< 2%) and IPC 0.61, accumulating into xmm0 every
      iteration).
  false_sharing
      Pattern-scan Step 2 also asks for analysis beyond the checklist; the recorded c2c
      shows 84.37% Tot Hitm on one line written at offsets 0x0/0x10/0x20/0x30 by
      worker_update_stats on four CPUs. building-blocks.md "c2c access map", Step 3:
      different offsets -> false sharing.
  no_fix_claims / sources_untouched
      Step 4e "Do not prescribe fixes. Name the pattern only." The verifier does not
      penalise recommendations (the instruction does not forbid them); it fails a
      report that claims code was changed, or sources that were edited.

Lenient on headings, table layout and wording.
"""
import hashlib
import json
import pathlib
import re

OUT = pathlib.Path("/app/hotspot_report.md")
SRC = {
    "/app/src/score.c": "405cb2d22c76e057ff5f6ed1db61920434ffbbeae89456eb2356a9b92dafa8bc",
    "/app/src/worker.c": "5b9746ab6caeaac80eb8b66ad734ce61fc9b8aec9e1b71f7a8f8dedf2f82a0c3",
}
TOP = {
    "accumulate_scores": r"58|58\.4|58\.42",
    "worker_update_stats": r"19|18\.8|18\.77",
    "memmove": r"7|7\.1|7\.11",
}
TAIL = ("parse_record", "futex_wait_queue", "read_block", "clear_page_erms", "_int_malloc")
# The hot source line, quoted or referred to by number (score.c:16 / "line 16").
HOT = re.compile(r"sum\s*\+=\s*a\s*\[\s*i\s*\]\s*\*\s*b\s*\[\s*i\s*\]|score\.c\s*:\s*16\b|\blines?\s+16\b", re.I)
HOT_PCT = re.compile(r"(?<![\d.])(76\s*%|75\.84?\s*%?)")
SECOND = re.compile(r"stats\s*\[\s*id\s*\]\s*\.\s*processed\s*\+\+|worker\.c\s*:\s*34\b|\blines?\s+34\b", re.I)
SECOND_PCT = re.compile(r"(?<![\d.])(91\s*%|91\.04?\s*%?)")
SERIAL = re.compile(r"serial[- ]accumulat|loop[- ]carried|depend(ency|ence|ent) chain|"
                    r"dependent add|single accumulator|one accumulator|latency[- ]bound", re.I)
SCALAR = re.compile(r"scalar|not (been )?vectori[sz]|no (simd|vectori)|unvectori|non-vectori", re.I)
CLAIMS = re.compile(r"\bi(?:'ve| have)? (?:fixed|changed|modified|rewrote|rewritten|applied|patched)\b|"
                    r"applied the fix|fixed the binary|changed the code|after optimi[sz]ation,? (?:it|the)|"
                    r"\bnow runs\b", re.I)


def blocks(text):
    cur, out = [], []
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            cur.append(line)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def row_has(row, key, pct):
    if key not in row:
        return False
    cells = [c.strip().strip("*` ") for c in row.split("|")]
    return any(re.fullmatch(rf"~?({pct})\s*%?", c) for c in cells)


def grade(text):
    checks, notes = {}, {}
    checks["report_written"] = len(text) >= 400
    table = next((b for b in blocks(text)
                  if any("accumulate_scores" in r for r in b)
                  and any("worker_update_stats" in r for r in b)), [])
    notes["top_table"] = table[:12]
    checks["top_table"] = all(any(row_has(r, k, p) for r in table) for k, p in TOP.items())
    checks["top_table_cutoff"] = bool(table) and not any(t in r for r in table for t in TAIL)
    lines = text.splitlines()
    checks["annotated_hot_line"] = any(HOT.search(l) and HOT_PCT.search(l) for l in lines)
    checks["annotated_second_function"] = any(SECOND.search(l) and SECOND_PCT.search(l)
                                              for l in lines)
    checks["serial_accumulator"] = bool(SERIAL.search(text))
    checks["scalar_fp"] = bool(SCALAR.search(text))
    checks["false_sharing"] = bool(re.search(r"false[- ]sharing", text, re.I)
                                   and re.search(r"hitm|c2c|cache[- ]?line", text, re.I))
    m = CLAIMS.search(text)
    checks["no_fix_claims"] = m is None
    notes["fix_claim"] = m.group(0) if m else None
    notes["regime_mentions_memory_bound"] = bool(re.search(r"memory[- ]bound", text, re.I))
    return checks, notes


# Recorded, not part of the reward: the 5% table cutoff is a Flow E formatting convention the
# instruction does not ask for.
INFORMATIONAL = {"top_table_cutoff"}


def main():
    text = OUT.read_text(errors="replace") if OUT.exists() else ""
    checks, notes = grade(text)
    changed = [p for p, h in SRC.items()
               if not pathlib.Path(p).exists()
               or hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() != h]
    checks["sources_untouched"] = not changed
    notes["changed_sources"] = changed
    rewards = {k: float(v) for k, v in checks.items()}
    rewards["reward"] = float(all(v for k, v in checks.items() if k not in INFORMATIONAL))
    print(json.dumps({"checks": checks, "notes": notes}, indent=1))
    pathlib.Path("/logs/verifier").mkdir(parents=True, exist_ok=True)
    with open("/logs/verifier/reward.json", "w", encoding="utf-8") as fh:
        json.dump(rewards, fh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
