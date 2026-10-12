"""Formula expansion and evaluation for perfmon metrics."""

import math
import re
from typing import Optional


def expand_formula(metric) -> str:
    """Replace aliases (a, b, c...) with actual event/constant names in formula."""
    formula = metric.formula
    if not formula:
        return ""

    # Build alias -> name mapping
    alias_map = {}
    for ev in metric.events:
        alias_map[ev["Alias"]] = ev["Name"]
    for const in metric.constants:
        alias_map[const["Alias"]] = const["Name"]

    # Sort by alias length descending to avoid partial replacement
    # (e.g., "aa" before "a")
    sorted_aliases = sorted(alias_map.keys(), key=len, reverse=True)

    # Replace aliases that appear as standalone tokens (word boundaries)
    result = formula
    for alias in sorted_aliases:
        # Match alias as a standalone token (not part of a longer identifier)
        pattern = r'\b' + re.escape(alias) + r'\b'
        result = re.sub(pattern, alias_map[alias], result)

    return result


def extract_events(metric) -> set:
    """Return set of hardware event names needed for this metric (without modifiers)."""
    return metric.event_names


def extract_events_with_modifiers(metric) -> set:
    """Return set of event names with modifiers (e.g., UOPS_RETIRED.MS:c1:e1)."""
    return metric.event_names_with_modifiers


def extract_constants(metric) -> set:
    """Return set of constant names needed for this metric."""
    return {c["Name"] for c in metric.constants}


def _safe_eval_formula(formula: str, values: dict) -> Optional[float]:
    """Evaluate a metric formula with the given event/constant values.

    The formula uses standard arithmetic operators and functions:
    min(), max(), d_ratio(), source_count(), has_event(), if/else
    """
    if not formula:
        return None

    # Replace event/constant names with their values
    expr = formula

    # Sort names by length descending to avoid partial replacement
    sorted_names = sorted(values.keys(), key=len, reverse=True)
    for name in sorted_names:
        val = values[name]
        pattern = re.escape(name)
        expr = re.sub(pattern, str(float(val)), expr)

    # Replace common functions
    expr = re.sub(r'\bmin\b', 'min', expr)
    expr = re.sub(r'\bmax\b', 'max', expr)

    # Handle d_ratio (safe division)
    def _d_ratio_replace(match):
        return f"_d_ratio({match.group(1)}, {match.group(2)})"
    expr = re.sub(r'd_ratio\s*\(([^,]+),\s*([^)]+)\)', _d_ratio_replace, expr)

    # Handle source_count (returns 1 for counting mode)
    expr = re.sub(r'source_count\s*\([^)]*\)', '1', expr)

    # Handle has_event (returns 1 if event value exists, 0 otherwise)
    expr = re.sub(r'has_event\s*\([^)]*\)', '1', expr)

    # Handle #NA
    expr = expr.replace('#NA', '0')

    # Safe eval environment
    safe_globals = {
        "__builtins__": {},
        "min": min,
        "max": max,
        "_d_ratio": lambda a, b: a / b if b != 0 else 0,
        "math": math,
    }

    try:
        result = eval(expr, safe_globals)
        if isinstance(result, (int, float)) and not math.isnan(result) and not math.isinf(result):
            return float(result)
        return None
    except (SyntaxError, NameError, TypeError, ZeroDivisionError, ValueError):
        return None


def evaluate_metric(metric, event_values: dict, constants: dict = None) -> Optional[float]:
    """Compute metric value from collected event counts.

    Args:
        metric: MetricDef with events, constants, and formula
        event_values: dict of event_name -> collected value (float)
        constants: dict of constant_name -> value (e.g., SYSTEM_TSC_FREQ)
    """
    if constants is None:
        constants = {}

    # Build alias -> value mapping
    alias_values = {}
    for ev in metric.events:
        name = ev["Name"]
        alias = ev["Alias"]
        # Try exact match first, then without modifiers
        base_name = name.split(":")[0]
        if name in event_values:
            alias_values[alias] = event_values[name]
        elif base_name in event_values:
            alias_values[alias] = event_values[base_name]
        else:
            return None  # Missing required event

    for const in metric.constants:
        name = const["Name"]
        alias = const["Alias"]
        if name in constants:
            alias_values[alias] = constants[name]
        else:
            return None  # Missing required constant

    return _safe_eval_formula(metric.formula, alias_values)


def evaluate_threshold(metric, metric_values: dict) -> Optional[bool]:
    """Evaluate a TMA metric's threshold formula.

    Args:
        metric: MetricDef with threshold field
        metric_values: dict of metric_legacy_name -> value

    Returns:
        True if threshold passes (bottleneck detected), False if not, None if can't evaluate
    """
    threshold = metric.threshold
    if not threshold:
        return None

    formula = threshold.get("Formula", "")
    if not formula:
        return None

    threshold_metrics = threshold.get("ThresholdMetrics", [])
    if not threshold_metrics:
        return None

    # Build alias -> value mapping for threshold formula
    alias_values = {}
    for tm in threshold_metrics:
        alias = tm["Alias"]
        value_key = tm["Value"]
        if value_key in metric_values:
            alias_values[alias] = metric_values[value_key]
        else:
            return None

    result = _safe_eval_formula(formula, alias_values)
    if result is None:
        return None
    return bool(result)
