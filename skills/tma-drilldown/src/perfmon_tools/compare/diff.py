"""Cross-platform comparison of events and metrics."""

from typing import Optional

from ..core.platform import _find_perfmon_root
from ..core.catalog import PlatformCatalog
from ..lookup.search import _resolve_by_shortname


def compare_platforms(
    platform1: str,
    platform2: str,
    compare_type: str = "all",  # "events", "metrics", "all"
    metric_name: Optional[str] = None,
    event_name: Optional[str] = None,
    category: Optional[str] = None,
) -> dict:
    """Compare events/metrics between two platforms.

    Returns:
        dict with added, removed, changed entries for events and metrics
    """
    perfmon_root = _find_perfmon_root()
    plat1 = _resolve_by_shortname(platform1, perfmon_root)
    plat2 = _resolve_by_shortname(platform2, perfmon_root)
    cat1 = PlatformCatalog(plat1, perfmon_root)
    cat2 = PlatformCatalog(plat2, perfmon_root)

    result = {
        "platform1": platform1,
        "platform2": platform2,
        "events": {"added": [], "removed": [], "changed": []},
        "metrics": {"added": [], "removed": [], "changed": []},
    }

    # Compare specific metric
    if metric_name:
        m1 = cat1.get_metric(metric_name)
        m2 = cat2.get_metric(metric_name)
        if m1 and m2:
            diff = _diff_metric(m1, m2)
            if diff:
                result["metrics"]["changed"].append(diff)
        elif m1 and not m2:
            result["metrics"]["removed"].append({"name": metric_name})
        elif not m1 and m2:
            result["metrics"]["added"].append({"name": metric_name})
        return result

    # Compare specific event
    if event_name:
        e1 = cat1.get_event(event_name)
        e2 = cat2.get_event(event_name)
        if e1 and e2:
            diff = _diff_event(e1, e2)
            if diff:
                result["events"]["changed"].append(diff)
        elif e1 and not e2:
            result["events"]["removed"].append({"name": event_name})
        elif not e1 and e2:
            result["events"]["added"].append({"name": event_name})
        return result

    # Full comparison
    if compare_type in ("events", "all"):
        _compare_events(cat1, cat2, result)

    if compare_type in ("metrics", "all"):
        _compare_metrics(cat1, cat2, result, category=category)

    return result


def _compare_events(cat1, cat2, result):
    """Compare all core events between two catalogs."""
    # Only compare core events (not uncore)
    names1 = {e.name for e in cat1.events if not e.deprecated and "uncore" not in str(e.raw.get("Unit", "")).lower()}
    names2 = {e.name for e in cat2.events if not e.deprecated and "uncore" not in str(e.raw.get("Unit", "")).lower()}

    # Added in platform2
    for name in sorted(names2 - names1):
        ev = cat2.get_event(name)
        result["events"]["added"].append({
            "name": name,
            "description": ev.brief_description[:80] if ev else "",
        })

    # Removed from platform2
    for name in sorted(names1 - names2):
        ev = cat1.get_event(name)
        result["events"]["removed"].append({
            "name": name,
            "description": ev.brief_description[:80] if ev else "",
        })

    # Changed (same name, different encoding)
    for name in sorted(names1 & names2):
        e1 = cat1.get_event(name)
        e2 = cat2.get_event(name)
        if e1 and e2:
            diff = _diff_event(e1, e2)
            if diff:
                result["events"]["changed"].append(diff)


def _compare_metrics(cat1, cat2, result, category=None):
    """Compare all metrics between two catalogs."""
    metrics1 = {m.name: m for m in cat1.metrics}
    metrics2 = {m.name: m for m in cat2.metrics}

    if category:
        metrics1 = {k: v for k, v in metrics1.items() if v.category.lower() == category.lower()}
        metrics2 = {k: v for k, v in metrics2.items() if v.category.lower() == category.lower()}

    names1 = set(metrics1.keys())
    names2 = set(metrics2.keys())

    for name in sorted(names2 - names1):
        m = metrics2[name]
        result["metrics"]["added"].append({
            "name": name,
            "level": m.level,
            "category": m.category,
            "description": m.brief_description[:80],
        })

    for name in sorted(names1 - names2):
        m = metrics1[name]
        result["metrics"]["removed"].append({
            "name": name,
            "level": m.level,
            "category": m.category,
            "description": m.brief_description[:80],
        })

    for name in sorted(names1 & names2):
        diff = _diff_metric(metrics1[name], metrics2[name])
        if diff:
            result["metrics"]["changed"].append(diff)


def _diff_event(e1, e2) -> Optional[dict]:
    """Compare two event definitions."""
    changes = {}
    if e1.event_code != e2.event_code:
        changes["event_code"] = {"from": e1.event_code, "to": e2.event_code}
    if e1.umask != e2.umask:
        changes["umask"] = {"from": e1.umask, "to": e2.umask}
    if e1.counter != e2.counter:
        changes["counter"] = {"from": e1.counter, "to": e2.counter}
    if not changes:
        return None
    return {"name": e1.name, "changes": changes}


def _diff_metric(m1, m2) -> Optional[dict]:
    """Compare two metric definitions."""
    changes = {}
    if m1.formula != m2.formula:
        changes["formula"] = {"from": m1.formula, "to": m2.formula}
    if m1.base_formula != m2.base_formula:
        changes["base_formula"] = {"from": m1.base_formula, "to": m2.base_formula}

    events1 = {e["Name"] for e in m1.events}
    events2 = {e["Name"] for e in m2.events}
    if events1 != events2:
        changes["events_added"] = sorted(events2 - events1)
        changes["events_removed"] = sorted(events1 - events2)

    if m1.level != m2.level:
        changes["level"] = {"from": m1.level, "to": m2.level}

    if not changes:
        return None
    return {
        "name": m1.name,
        "category": m1.category,
        "level": m1.level,
        "changes": changes,
    }
