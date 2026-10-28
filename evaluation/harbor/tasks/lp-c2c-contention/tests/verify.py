"""Grades lp-c2c-contention; writes /logs/verifier/reward.json.

Ground truth comes from skills/linux-perf (paths relative to the skill directory):

  reported_lines
      SKILL.md, "Flow C: Cache-line contention with perf c2c", **Threshold**, and
      references/building-blocks.md, "Building block: c2c hot cache lines": ignore
      entries below 5% Tot Hitm by default; Tot Hitm is the importance score.
      references/flow-c.md, "Phase 2": only lines above the threshold are mapped.
      Above 5%: index 0 (0x5603c1a8e100, 38.71%), 1 (0x5603c1a8e200, 26.04%),
      2 (0x7f3b2c001080, 19.55%). Index 3 (4.12%, the most records and stores in the
      table, and a textbook per-worker false-sharing layout) and 4, 5 are below it.
      INFORMATIONAL: the instruction leaves the cut to the agent.
  l0_false / l1_true / l2_true
      references/building-blocks.md, "Building block: c2c access map for a cache line",
      "Step 3 -- Classify": the Offset column is the definitive discriminator; all
      significant accesses at one offset -> true sharing, significant accesses at
      different offsets -> false sharing. references/flow-c.md, "Phase 3": confirm by
      field name.
        0: offsets 0x0 / 0x10 / 0x18 = parsed / flushed / flush_bytes, one writer
           thread each -> false.
        1: 95.82% of HITM at 0x0 (requests); the 4.18% read of max_inflight at 0x8
           is not significant -> true.
        2: every access at 0x0 (lock) -> true.
  l0_fields
      references/flow-c.md, "Phase 3" and the report template "Case N: False sharing":
      name the different fields; parsed and flushed must both be named.
  l0_fix
      flow-c.md template (false sharing) -> "Resolution strategy: Structured
      false-sharing fix" (SKILL.md Part 5): separate / pad / align the fields onto
      their own cache lines.
  l1_fix
      flow-c.md template, true sharing without the spin pattern -> "Per-CPU / per-thread
      sharding"; references/flow-d.md Phase 4 "True sharing -- statistics" ->
      Per-CPU statistics aggregation. Padding max_inflight away alone does not count.
  l2_fix
      flow-c.md template, "Test-and-Set spin detection": a `cmpxchg` loop on the
      contended word -> "apply Resolution strategy: Test-and-Test-and-Set before
      considering other strategies". Informational only: a mutex, MCS or backoff is not wrong, so l2_fix is recorded but not in the reward.

Lenient on address formatting, field-name decoration and fix wording.
"""
import json
import pathlib
import re

OUT = pathlib.Path("/app/contention.json")
L0, L1, L2 = 0x5603C1A8E100, 0x5603C1A8E200, 0x7F3B2C001080
FIX = {
    L0: re.compile(r"pad|align|separat|split|own (64|128)?[- ]?(byte )?(cache[- ]?)?line|"
                   r"different (cache[- ]?)?lines|cache[- ]?line[- ]?(size|boundar)|"
                   r"\b(64|128)[- ]?byte|reorder|move .{0,40}(apart|away|own|separate)", re.I),
    L1: re.compile(r"per[- ]?(thread|cpu|core|worker)|thread[- ]?local|_thread_local|shard|"
                   r"privat|local (counter|count|accumulat|cop)|batch|aggregat", re.I),
    L2: re.compile(r"\bttas\b|test[- ]and[- ]test|spin(s|ning)? on (a |the )?(plain |relaxed |normal |"
                   r"ordinary |non-atomic )?(read|load)|read[- ]only spin|"
                   r"(load|read|check|test)\w*.{0,60}before .{0,40}(cas|cmpxchg|compare|exchange|atomic)",
                   re.I),
}


def addr(raw):
    for h in re.findall(r"0x[0-9a-fA-F]+|[0-9a-fA-F]{8,}", str(raw)):
        v = int(h, 16)
        if v > 0xFFFF:
            return v
    return None


def sharing(raw):
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s.startswith("true"):
        return True
    if s.startswith("false"):
        return False
    return None


def fields(raw):
    items = raw if isinstance(raw, list) else [raw]
    return {t for it in items for t in re.findall(r"[a-z_][a-z0-9_]*", str(it).lower())}


def grade(doc):
    checks, notes = {}, {}
    rows = doc.get("lines") if isinstance(doc, dict) else None
    checks["valid_json"] = isinstance(rows, list) and all(isinstance(r, dict) for r in rows)
    by = {}
    for r in rows if checks["valid_json"] else []:
        a = addr(r.get("address", ""))
        by.setdefault(a, r)
    notes["listed"] = [hex(a) if a else a for a in by]
    checks["reported_lines"] = checks["valid_json"] and set(by) == {L0, L1, L2}
    get = lambda a, k: by.get(a, {}).get(k)  # noqa: E731
    checks["l0_false"] = sharing(get(L0, "sharing")) is False
    checks["l1_true"] = sharing(get(L1, "sharing")) is True
    checks["l2_true"] = sharing(get(L2, "sharing")) is True
    checks["l0_fields"] = {"parsed", "flushed"} <= fields(get(L0, "fields") or "")
    for a, name in ((L0, "l0_fix"), (L1, "l1_fix"), (L2, "l2_fix")):
        fx = str(get(a, "fix") or "")
        checks[name] = bool(FIX[a].search(fx))
        notes[name] = fx[:200]
    return checks, notes


# Recorded, not rewarded: the skill's 5% cutoff and its TTAS-only lock fix are
# conventions the instruction does not ask for (a mutex, sharding or backoff is also
# a defensible lock fix).
INFORMATIONAL = {"reported_lines", "l2_fix"}


def main():
    err = None
    try:
        doc = json.loads(OUT.read_text())
    except Exception as e:  # missing or unparsable deliverable
        doc, err = None, repr(e)
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
