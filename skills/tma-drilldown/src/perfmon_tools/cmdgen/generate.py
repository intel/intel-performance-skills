"""Generate perf stat/record commands from metrics or TMA levels."""

from pathlib import Path
from typing import Optional

from ..core.platform import (
    PlatformInfo,
    _find_perfmon_root,
    detect_cpu,
    resolve_platform,
)
from ..core.catalog import PlatformCatalog
from ..core.tma_tree import TmaTree


def generate_perf_command(
    platform: Optional[str] = None,
    metrics: Optional[list] = None,
    tma_level: Optional[int] = None,
    tma_node: Optional[str] = None,
    events: Optional[list] = None,
    duration: int = 5,
    pid: Optional[int] = None,
    command: Optional[str] = None,
    json_output: bool = False,
    per_core: bool = False,
    interval_ms: Optional[int] = None,
    repeat: Optional[int] = None,
) -> dict:
    """Generate perf stat command(s).

    Returns dict with:
        - commands: list of perf command strings
        - events_used: set of event names
        - counter_info: counter budget analysis
        - notes: any warnings or suggestions
    """
    perfmon_root = _find_perfmon_root()

    # Resolve platform
    from ..lookup.search import _resolve_by_shortname
    if platform:
        plat_info = _resolve_by_shortname(platform, perfmon_root)
    else:
        cpu = detect_cpu()
        plat_info = resolve_platform(cpu, perfmon_root)

    catalog = PlatformCatalog(plat_info, perfmon_root)
    tree = TmaTree(catalog)

    # Collect required events
    required_events = set()
    notes = []
    description = ""

    if events:
        required_events.update(events)
        description = f"Custom events"

    if metrics:
        for metric_name in metrics:
            m = catalog.get_metric(metric_name)
            if m:
                required_events.update(m.event_names_with_modifiers)
            else:
                notes.append(f"Metric '{metric_name}' not found")
        description = f"Metrics: {', '.join(metrics)}"

    if tma_level is not None:
        level_nodes = tree.get_nodes_at_level(tma_level)
        for node in level_nodes:
            required_events.update(node.metric.event_names_with_modifiers)
        description = f"TMA Level {tma_level} ({len(level_nodes)} nodes)"

    if tma_node:
        node = tree.get_node(tma_node)
        if node:
            # Get events for this node's children (drill-down)
            children = tree.get_children(tma_node)
            if children:
                for child in children:
                    required_events.update(child.metric.event_names_with_modifiers)
                description = f"TMA drill-down: {tma_node} → {[c.name for c in children]}"
            else:
                required_events.update(node.metric.event_names_with_modifiers)
                description = f"TMA node: {tma_node} (leaf)"
        else:
            notes.append(f"TMA node '{tma_node}' not found")

    if not required_events:
        # Default: TMA Level 1
        for root in tree.roots:
            required_events.update(root.metric.event_names_with_modifiers)
        description = "TMA Level 1 (default)"

    # Analyze counter budget
    counter_info = _analyze_counter_budget(required_events, plat_info, catalog)

    # Build perf command(s)
    commands = _build_commands(
        required_events,
        plat_info,
        duration=duration,
        pid=pid,
        command=command,
        json_output=json_output,
        per_core=per_core,
        interval_ms=interval_ms,
        repeat=repeat,
        counter_info=counter_info,
    )

    if counter_info["needs_multiplexing"]:
        notes.append(
            f"Requires {counter_info['events_count']} events but only "
            f"{counter_info['available_counters']} counters available. "
            f"Multiplexing will reduce accuracy."
        )

    return {
        "commands": commands,
        "events_used": sorted(required_events),
        "counter_info": counter_info,
        "notes": notes,
        "description": description,
        "platform": plat_info.shortname,
    }


def _analyze_counter_budget(events: set, platform: PlatformInfo, catalog) -> dict:
    """Analyze whether events fit in available counters."""
    # Determine available counters based on platform generation
    if platform.default_level >= 2:
        # ICL+: 8 GP + 4 fixed + perf_metrics
        gp_counters = 8
        fixed_counters = 4
    elif platform.default_level == 1:
        gp_counters = 8
        fixed_counters = 4
    else:
        # Pre-ICL: 4 GP + 3 fixed
        gp_counters = 4
        fixed_counters = 3

    # Count fixed vs programmable events
    fixed_events = set()
    programmable_events = set()
    perf_metrics_events = set()

    for ev_name in events:
        base_name = ev_name.split(":")[0]
        ev_def = catalog.get_event(base_name)

        if "PERF_METRICS" in ev_name or "TOPDOWN.SLOTS" in ev_name:
            perf_metrics_events.add(ev_name)
        elif ev_def and ev_def.is_fixed:
            fixed_events.add(ev_name)
        else:
            programmable_events.add(ev_name)

    needs_multiplexing = len(programmable_events) > gp_counters
    available = gp_counters + fixed_counters

    return {
        "events_count": len(events),
        "programmable_events": len(programmable_events),
        "fixed_events": len(fixed_events),
        "perf_metrics_events": len(perf_metrics_events),
        "available_counters": available,
        "gp_counters": gp_counters,
        "needs_multiplexing": needs_multiplexing,
        "estimated_mux_ratio": (
            len(programmable_events) / gp_counters if needs_multiplexing else 1.0
        ),
    }


def _build_commands(
    events: set,
    platform: PlatformInfo,
    duration: int,
    pid: Optional[int],
    command: Optional[str],
    json_output: bool,
    per_core: bool,
    interval_ms: Optional[int],
    repeat: Optional[int],
    counter_info: dict,
) -> list:
    """Build perf stat command string(s)."""
    # Format event names for perf
    event_specs = []
    for ev in sorted(events):
        spec = _format_event_spec(ev, platform)
        event_specs.append(spec)

    # Build base command
    parts = ["perf stat"]

    if json_output:
        parts.append("-j")
    if per_core:
        parts.append("--per-core")
    if interval_ms:
        parts.append(f"-I {interval_ms}")
    if repeat:
        parts.append(f"-r {repeat}")

    # Event list
    parts.append("-e")
    parts.append(",".join(event_specs))

    # Target
    if pid:
        parts.append(f"-p {pid}")
        if duration:
            parts.append(f"sleep {duration}")
    elif command:
        parts.append("--")
        parts.append(command)
    else:
        parts.append(f"sleep {duration}")

    return [" ".join(parts)]


def _format_event_spec(event_name: str, platform: PlatformInfo) -> str:
    """Format event name as perf event specifier.

    Handles:
    - PERF_METRICS.* → topdown-* perf events
    - TOPDOWN.SLOTS:perf_metrics → slots
    - Regular events: cpu_core/EVENT.NAME/ or cpu/EVENT.NAME/
    - Events with modifiers: EVENT.NAME:c1:e1
    """
    # PERF_METRICS → perf's built-in topdown events
    perf_metrics_map = {
        "PERF_METRICS.FRONTEND_BOUND": "topdown-fe-bound",
        "PERF_METRICS.BAD_SPECULATION": "topdown-bad-spec",
        "PERF_METRICS.BACKEND_BOUND": "topdown-be-bound",
        "PERF_METRICS.RETIRING": "topdown-retiring",
        "PERF_METRICS.FETCH_LATENCY": "topdown-fetch-lat",
        "PERF_METRICS.BRANCH_MISPREDICTS": "topdown-br-mispredict",
        "PERF_METRICS.MEMORY_BOUND": "topdown-mem-bound",
        "PERF_METRICS.HEAVY_OPS": "topdown-heavy-ops",
    }

    if event_name in perf_metrics_map:
        return perf_metrics_map[event_name]

    if "TOPDOWN.SLOTS" in event_name:
        return "slots"

    # Regular event with possible modifiers
    parts = event_name.split(":")
    base = parts[0]
    modifiers = parts[1:] if len(parts) > 1 else []

    # Build perf event spec
    if platform.is_hybrid:
        # For hybrid, specify cpu_core or cpu_atom
        prefix = "cpu_core"
    else:
        prefix = "cpu"

    spec = f"{prefix}/{base}/"
    if modifiers:
        # Translate modifiers: c1 -> cmask=1, e1 -> edge=1
        for mod in modifiers:
            if mod == "perf_metrics":
                continue
            # Keep modifier as-is for perf
            spec = f"{prefix}/{base},{_translate_modifier(mod)}/"

    return spec


def _translate_modifier(mod: str) -> str:
    """Translate event modifier shorthand to perf format."""
    if mod.startswith("c") and mod[1:].isdigit():
        return f"cmask={mod[1:]}"
    if mod.startswith("e") and mod[1:].isdigit():
        return f"edge={mod[1:]}"
    if mod.startswith("i") and mod[1:].isdigit():
        return f"inv={mod[1:]}"
    return mod
