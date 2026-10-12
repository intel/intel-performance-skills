"""Pre-flight system checks before data collection.

Detects SMT, steady-state behavior, and counter budget constraints.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.platform import PlatformInfo


@dataclass
class PhaseInfo:
    phase_id: int
    intervals: list  # indices of intervals belonging to this phase
    runtime_pct: float  # percentage of total runtime
    tma_l1: dict  # {Frontend_Bound: %, Backend_Bound: %, ...}


@dataclass
class MeasurementStrategy:
    smt_active: bool
    use_perf_metrics: bool
    programmable_counters: int
    fixed_counters: int
    interval_mode: bool
    interval_ms: int
    phase_detected: bool
    phases: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def detect_smt(smt_path: str = "/sys/devices/system/cpu/smt/active") -> bool:
    """Detect whether SMT/Hyper-Threading is active."""
    try:
        content = Path(smt_path).read_text().strip()
        return content == "1"
    except (FileNotFoundError, PermissionError):
        # Fallback: check /proc/cpuinfo for siblings vs cores
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text()
            siblings = None
            cores = None
            for line in cpuinfo.splitlines():
                if "siblings" in line and siblings is None:
                    siblings = int(line.split(":")[1].strip())
                if "cpu cores" in line and cores is None:
                    cores = int(line.split(":")[1].strip())
            if siblings and cores:
                return siblings > cores
        except (FileNotFoundError, ValueError):
            pass
    return False


def detect_steady_state(interval_values: list, threshold_cv: float = 0.20) -> tuple:
    """Analyze per-interval TMA L1 values for steady-state behavior.

    Args:
        interval_values: list of dicts, each mapping metric_name -> value
        threshold_cv: coefficient of variation threshold (default 20%)

    Returns:
        (is_steady, phases: list[PhaseInfo])
    """
    if len(interval_values) < 3:
        return True, []

    # Extract key metrics across intervals
    metric_series = {}
    for iv in interval_values:
        for name, val in iv.items():
            if name not in metric_series:
                metric_series[name] = []
            metric_series[name].append(val)

    # Compute coefficient of variation for each metric
    max_cv = 0.0
    for name, values in metric_series.items():
        if len(values) < 3:
            continue
        mean = sum(values) / len(values)
        if mean == 0:
            continue
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = variance ** 0.5
        cv = std / mean
        max_cv = max(max_cv, cv)

    is_steady = max_cv < threshold_cv

    phases = []
    if not is_steady:
        phases = _cluster_intervals(interval_values)

    return is_steady, phases


def _cluster_intervals(interval_values: list) -> list:
    """Simple k=2 clustering of intervals by TMA L1 profile.

    Uses the dominant metric (highest value) as the clustering key.
    """
    if not interval_values:
        return []

    # Determine dominant metric for each interval
    dominants = []
    for iv in interval_values:
        if iv:
            dominant = max(iv.items(), key=lambda x: x[1])
            dominants.append(dominant[0])
        else:
            dominants.append("")

    # Group by dominant metric
    groups = {}
    for i, dom in enumerate(dominants):
        if dom not in groups:
            groups[dom] = []
        groups[dom].append(i)

    # Convert to PhaseInfo
    total = len(interval_values)
    phases = []
    for phase_id, (dom, indices) in enumerate(
        sorted(groups.items(), key=lambda x: -len(x[1]))
    ):
        # Compute average TMA L1 for this phase
        tma_l1 = {}
        for idx in indices:
            for name, val in interval_values[idx].items():
                tma_l1[name] = tma_l1.get(name, 0) + val
        for name in tma_l1:
            tma_l1[name] /= len(indices)

        phases.append(
            PhaseInfo(
                phase_id=phase_id,
                intervals=indices,
                runtime_pct=100.0 * len(indices) / total,
                tma_l1=tma_l1,
            )
        )

    return phases


def compute_counter_budget(
    events: set, platform: PlatformInfo
) -> dict:
    """Compute whether events fit without multiplexing.

    Returns dict with fit analysis and suggested splits if needed.
    """
    if platform.default_level >= 1:
        gp_counters = 8
        fixed_counters = 4
    else:
        gp_counters = 4
        fixed_counters = 3

    # Categorize events
    perf_metrics = set()
    fixed = set()
    programmable = set()

    fixed_event_names = {
        "INST_RETIRED.ANY", "CPU_CLK_UNHALTED.THREAD",
        "CPU_CLK_UNHALTED.REF_TSC", "TOPDOWN.SLOTS",
    }

    for ev in events:
        base = ev.split(":")[0]
        if "PERF_METRICS" in ev or "TOPDOWN.SLOTS:perf_metrics" in ev:
            perf_metrics.add(ev)
        elif base in fixed_event_names:
            fixed.add(ev)
        else:
            programmable.add(ev)

    needs_split = len(programmable) > gp_counters
    mux_ratio = len(programmable) / gp_counters if needs_split else 1.0

    # Suggest splits if needed
    splits = []
    if needs_split:
        prog_list = sorted(programmable)
        for i in range(0, len(prog_list), gp_counters):
            group = set(prog_list[i:i + gp_counters])
            group.update(perf_metrics)
            group.update(fixed)
            splits.append(group)
    else:
        splits = [events]

    return {
        "fits_single_run": not needs_split,
        "programmable_needed": len(programmable),
        "gp_available": gp_counters,
        "mux_ratio": mux_ratio,
        "confidence_pct": min(100.0, 100.0 / mux_ratio) if mux_ratio > 0 else 100.0,
        "suggested_splits": splits,
        "split_count": len(splits),
    }


def create_strategy(platform: PlatformInfo) -> MeasurementStrategy:
    """Create measurement strategy based on platform and system state."""
    smt = detect_smt()

    use_perf_metrics = platform.default_level >= 1
    if platform.default_level >= 1:
        gp = 8
        fixed = 4
    else:
        gp = 4
        fixed = 3

    notes = []
    if smt:
        notes.append(
            "SMT active: using per-thread events. "
            "Cross-thread interference may affect L3+ accuracy."
        )
    if use_perf_metrics:
        notes.append(
            f"PERF_METRICS supported: L1/L2 TMA available without multiplexing."
        )
    else:
        notes.append(
            "Pre-ICL platform: L1 TMA requires TOPDOWN.SLOTS + programmable counters."
        )

    return MeasurementStrategy(
        smt_active=smt,
        use_perf_metrics=use_perf_metrics,
        programmable_counters=gp,
        fixed_counters=fixed,
        interval_mode=False,
        interval_ms=1000,
        phase_detected=False,
        notes=notes,
    )
