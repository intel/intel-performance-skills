"""Grades lp-scaling-bottleneck; writes /logs/verifier/reward.json.

Ground truth comes from skills/linux-perf (paths relative to the skill directory):

  jumpers_found / no_other_functions
      references/building-blocks.md, "Building block: Dual-profile comparison",
      "Step 5 -- Identify 'jumper' functions": a function is a scaling-bottleneck
      candidate if, between the 1-core and N-core top-15, its rank rises >= 3, its share
      rises >= 2x or by >= 3 points, or it is a new entrant at >= 2%.
      references/flow-d.md, "Phase 1 -- Dual-profile collection": the jumpers are the
      input to the rest of the flow. In the artifacts exactly two functions qualify:
        ring_lock_acquire  0.71% (#8) -> 27.35% (#2)
        batch_commit       0.58% (#9) ->  8.94% (#3)
      score_block is the hottest at both counts but its share falls (72.41% -> 40.12%);
      native_queued_spin_lock_slowpath is a new entrant below 2% (1.42%); every other
      function's share falls. Listing any of them fails no_other_functions.
  lock_category
      references/flow-d.md, "Phase 4 -- Categorize and diagnose", row
      "cmpxchg / Test-and-Set spin": `lock cmpxchg` in the hot path (94% of the
      function, retried by `jne`) -> category lock_spin. Same rule as
      references/building-blocks.md, "Annotate pattern scan", Pattern 5.
  counter_category
      references/flow-d.md, Phase 4, row "True sharing -- statistics": a counter
      updated with an atomic add and no compare-exchange loop -> shared_counter.
      references/building-blocks.md, "Annotate pattern scan", Pattern 7: `lock add`
      whose result is ignored is a statistics counter, not a lock.
  next_is_c2c
      references/flow-d.md, "Phase 3 -- Focused c2c": after the dual profile, run
      perf c2c and filter to the cache lines the jumpers touch; flow-d's Phase 4 table
      classifies from that c2c evidence. Informational: the instruction does not
      name perf c2c, so this is recorded, not rewarded.

Lenient on symbol decoration ([.], (), +0x..), category case and separators, and the
wording of next_measurement.
"""
import json
import pathlib
import re

OUT = pathlib.Path("/app/diagnosis.json")
JUMPERS = {"ring_lock_acquire": "lock_spin", "batch_commit": "shared_counter"}
KNOWN = set(JUMPERS) | {
    "score_block", "parse_record", "__memmove_avx_unaligned_erms", "hash_lookup",
    "native_queued_spin_lock_slowpath", "decode_varint", "ring_pop", "_int_malloc",
    "ring_push", "_int_free", "emit_batch", "clear_page_erms", "checksum_block",
    "syscall_exit_to_user_mode", "ring_lock_release", "worker_main",
}
CATS = {"compute", "memory_bandwidth", "lock_spin", "shared_counter", "false_sharing",
        "shared_data", "syscall_or_io"}
C2C = re.compile(r"\bc2c\b|hitm|cache[- ]to[- ]cache|cache[- ]line (contention|bouncing|sharing)",
                 re.I)


def symbol(raw):
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(raw))
    for t in tokens:
        if t in KNOWN:
            return t
    return str(raw).strip().lower()


def category(raw):
    c = re.sub(r"[\s\-/]+", "_", str(raw).strip().lower())
    return c if c in CATS else "?" + c


def grade(doc):
    checks, notes = {}, {}
    rows = doc.get("bottlenecks") if isinstance(doc, dict) else None
    checks["valid_json"] = isinstance(rows, list) and all(isinstance(r, dict) for r in rows)
    if not checks["valid_json"]:
        rows = []
    seen = {}
    for r in rows:
        seen.setdefault(symbol(r.get("function", "")), category(r.get("category", "")))
    notes["listed"] = seen
    checks["jumpers_found"] = set(JUMPERS) <= set(seen)
    extra = sorted(set(seen) - set(JUMPERS))
    notes["extra_functions"] = extra
    checks["no_other_functions"] = checks["valid_json"] and not extra
    checks["lock_category"] = seen.get("ring_lock_acquire") == "lock_spin"
    checks["counter_category"] = seen.get("batch_commit") == "shared_counter"
    nm = doc.get("next_measurement", "") if isinstance(doc, dict) else ""
    if not isinstance(nm, str):
        nm = json.dumps(nm)
    checks["next_is_c2c"] = bool(C2C.search(nm))
    notes["next_measurement"] = nm[:300]
    return checks, notes


# The instruction asks what to measure next, not for perf c2c: recorded, not rewarded.
INFORMATIONAL = {"next_is_c2c"}


def main():
    try:
        doc = json.loads(OUT.read_text())
    except Exception as e:  # missing or unparsable deliverable
        doc, err = None, repr(e)
    else:
        err = None
    checks, notes = grade(doc)
    if err:
        notes["error"] = err
    rewards = {k: float(v) for k, v in checks.items()}
    rewards["reward"] = float(all(v for k, v in checks.items() if k not in INFORMATIONAL))
    print(json.dumps({"checks": checks, "notes": notes}, indent=1))
    pathlib.Path("/logs/verifier").mkdir(parents=True, exist_ok=True)
    with open("/logs/verifier/reward.json", "w", encoding="utf-8") as fh:
        json.dump(rewards, fh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
