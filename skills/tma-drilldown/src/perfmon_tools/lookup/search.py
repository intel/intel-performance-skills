"""Event and metric search across platforms."""

from pathlib import Path
from typing import Optional

from ..core.platform import (
    CpuInfo,
    PlatformInfo,
    _find_perfmon_root,
    detect_cpu,
    list_platforms,
    resolve_platform,
)
from ..core.catalog import PlatformCatalog


def search(
    query: str,
    platform: Optional[str] = None,
    search_type: str = "all",  # "events", "metrics", "all"
    category: Optional[str] = None,
    level: Optional[int] = None,
    cross_arch: bool = False,
    include_deprecated: bool = False,
) -> dict:
    """Search events and metrics.

    Args:
        query: search term (matches name and description)
        platform: platform shortname (auto-detect if None)
        search_type: "events", "metrics", or "all"
        category: filter metrics by category (e.g., "TMA", "Freq")
        level: filter metrics by level
        cross_arch: search across all architectures
        include_deprecated: include deprecated events

    Returns:
        dict with "events" and "metrics" lists
    """
    perfmon_root = _find_perfmon_root()

    if cross_arch:
        platforms_to_search = _resolve_all_platforms(perfmon_root)
    elif platform:
        platforms_to_search = [_resolve_by_shortname(platform, perfmon_root)]
    else:
        try:
            cpu = detect_cpu()
            platforms_to_search = [resolve_platform(cpu, perfmon_root)]
        except (FileNotFoundError, ValueError):
            raise ValueError(
                "Cannot auto-detect CPU. Specify --platform or ensure /proc/cpuinfo exists."
            )

    results = {"events": [], "metrics": [], "platform": None}

    for plat_info in platforms_to_search:
        catalog = PlatformCatalog(plat_info, perfmon_root)
        results["platform"] = plat_info.shortname

        if search_type in ("events", "all"):
            events = catalog.search_events(query, include_deprecated=include_deprecated)
            for ev in events:
                results["events"].append({
                    "name": ev.name,
                    "description": ev.brief_description,
                    "event_code": ev.event_code,
                    "umask": ev.umask,
                    "counter": ev.counter,
                    "precise": ev.precise,
                    "platform": ev.platform,
                    "deprecated": ev.deprecated,
                })

        if search_type in ("metrics", "all"):
            metrics = catalog.search_metrics(query, category=category)
            if level is not None:
                metrics = [m for m in metrics if m.level == level]
            for m in metrics:
                results["metrics"].append({
                    "name": m.name,
                    "level": m.level,
                    "description": m.brief_description,
                    "category": m.category,
                    "unit": m.unit_of_measure,
                    "metric_group": m.metric_group,
                    "parent_category": m.parent_category,
                    "platform": m.platform,
                })

    return results


def _resolve_by_shortname(shortname: str, perfmon_root: Path) -> PlatformInfo:
    """Resolve platform by shortname."""
    from ..core.platform import _parse_mapfile, _load_platform_config, _derive_shortname, CoreInfo

    mapfile_entries = _parse_mapfile(perfmon_root)
    platform_config = _load_platform_config(perfmon_root)

    for fm, rows in mapfile_entries.items():
        sn = _derive_shortname(rows[0]["filename"])
        if sn.upper() == shortname.upper():
            # Reconstruct PlatformInfo
            roles = {}
            for row in rows:
                role = row["role_name"] or ""
                if role not in roles:
                    roles[role] = CoreInfo(
                        core_type=row["core_type"],
                        role_name=role,
                        native_model_id=row["native_model_id"],
                    )
                core_info = roles[role]
                event_type = row["event_type"]
                filepath = perfmon_root / row["filename"].lstrip("/")
                if event_type == "metrics":
                    core_info.metrics_files.append(filepath)
                else:
                    core_info.event_files[event_type] = filepath

            config = platform_config.get(sn, {})
            named_roles = {k for k in roles if k != ""}
            return PlatformInfo(
                shortname=sn,
                name=config.get("Name", sn),
                family_model=fm,
                version=rows[0]["version"],
                is_hybrid=len(named_roles) > 1,
                default_level=config.get("DefaultLevel", 0),
                core_types=list(roles.values()),
            )

    raise ValueError(f"Platform '{shortname}' not found. Use --cross-arch to list all.")


def _resolve_all_platforms(perfmon_root: Path) -> list:
    """Get PlatformInfo for all platforms (expensive but needed for cross-arch search)."""
    all_platforms = list_platforms(perfmon_root)
    result = []
    for p in all_platforms:
        try:
            full = _resolve_by_shortname(p.shortname, perfmon_root)
            result.append(full)
        except ValueError:
            continue
    return result
