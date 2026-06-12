"""Event and metric loading, indexing, and search."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class EventDef:
    name: str
    event_code: str
    umask: str
    brief_description: str
    public_description: str
    counter: str
    sample_after_value: str
    precise: str
    deprecated: bool
    platform: str
    core_type: str
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def is_fixed(self) -> bool:
        return "fixed" in self.counter.lower()

    @property
    def raw_encoding(self) -> str:
        return f"event={self.event_code},umask={self.umask}"


@dataclass
class MetricDef:
    name: str
    legacy_name: str
    level: int
    brief_description: str
    unit_of_measure: str
    events: list  # [{Name, Alias}]
    constants: list  # [{Name, Alias}]
    formula: str
    base_formula: str
    category: str
    parent_category: str
    threshold: dict
    metric_group: str
    locate_with: str
    count_domain: str
    resolution_levels: str
    platform: str
    core_type: str

    @property
    def is_tma(self) -> bool:
        return self.category == "TMA"

    @property
    def is_bottleneck(self) -> bool:
        return self.name.startswith("Bottleneck_")

    @property
    def is_info(self) -> bool:
        return self.name.startswith("Info_")

    @property
    def is_tree_node(self) -> bool:
        return self.is_tma and not self.is_bottleneck and not self.is_info

    @property
    def event_names(self) -> set:
        """All event names referenced in this metric (without modifiers)."""
        return {ev["Name"].split(":")[0] for ev in self.events}

    @property
    def event_names_with_modifiers(self) -> set:
        """All event names with their modifiers."""
        return {ev["Name"] for ev in self.events}


def _load_events_file(path: Path, platform: str, core_type: str) -> list:
    """Load a single event JSON file."""
    with open(path) as f:
        data = json.load(f)

    events = []
    for raw in data.get("Events", []):
        deprecated = (
            raw.get("Deprecated", "0") == "1"
            or "deprecated" in raw.get("BriefDescription", "").lower()
        )
        events.append(
            EventDef(
                name=raw.get("EventName", ""),
                event_code=raw.get("EventCode", ""),
                umask=raw.get("UMask", ""),
                brief_description=raw.get("BriefDescription", ""),
                public_description=raw.get("PublicDescription", ""),
                counter=raw.get("Counter", ""),
                sample_after_value=raw.get("SampleAfterValue", ""),
                precise=raw.get("Precise", "0"),
                deprecated=deprecated,
                platform=platform,
                core_type=core_type,
                raw=raw,
            )
        )
    return events


def _load_metrics_file(path: Path, platform: str, core_type: str) -> list:
    """Load a single metric JSON file."""
    with open(path) as f:
        data = json.load(f)

    metrics = []
    for raw in data.get("Metrics", []):
        metrics.append(
            MetricDef(
                name=raw.get("MetricName", ""),
                legacy_name=raw.get("LegacyName", ""),
                level=raw.get("Level", 0),
                brief_description=raw.get("BriefDescription", ""),
                unit_of_measure=raw.get("UnitOfMeasure", ""),
                events=raw.get("Events", []),
                constants=raw.get("Constants", []),
                formula=raw.get("Formula", ""),
                base_formula=raw.get("BaseFormula", ""),
                category=raw.get("Category", ""),
                parent_category=raw.get("ParentCategory", ""),
                threshold=raw.get("Threshold", {}),
                metric_group=raw.get("MetricGroup", ""),
                locate_with=raw.get("LocateWith", ""),
                count_domain=raw.get("CountDomain", ""),
                resolution_levels=raw.get("ResolutionLevels", ""),
                platform=platform,
                core_type=core_type,
            )
        )
    return metrics


class PlatformCatalog:
    """Loaded event + metric catalog for one platform."""

    def __init__(self, platform_info, perfmon_root: Optional[Path] = None):
        from .platform import _find_perfmon_root

        if perfmon_root is None:
            perfmon_root = _find_perfmon_root()

        self.platform = platform_info
        self._events = []
        self._metrics = []
        self._event_index = {}  # name -> EventDef
        self._metric_index = {}  # name -> MetricDef

        for core in platform_info.core_types:
            core_type = core.core_type or ""
            for etype, path in core.event_files.items():
                if path.exists():
                    self._events.extend(
                        _load_events_file(path, platform_info.shortname, core_type)
                    )
            for path in core.metrics_files:
                if path.exists():
                    self._metrics.extend(
                        _load_metrics_file(path, platform_info.shortname, core_type)
                    )

        # Build indexes
        for ev in self._events:
            self._event_index[ev.name] = ev
            self._event_index[ev.name.upper()] = ev

        for m in self._metrics:
            self._metric_index[m.name] = m
            if m.legacy_name:
                self._metric_index[m.legacy_name] = m

    @property
    def events(self) -> list:
        return self._events

    @property
    def metrics(self) -> list:
        return self._metrics

    @property
    def tma_metrics(self) -> list:
        return [m for m in self._metrics if m.is_tma]

    @property
    def bottleneck_metrics(self) -> list:
        return [m for m in self._metrics if m.is_bottleneck]

    @property
    def tree_nodes(self) -> list:
        return [m for m in self._metrics if m.is_tree_node]

    @property
    def info_metrics(self) -> list:
        return [m for m in self._metrics if m.is_info]

    def get_event(self, name: str) -> Optional[EventDef]:
        return self._event_index.get(name) or self._event_index.get(name.upper())

    def get_metric(self, name: str) -> Optional[MetricDef]:
        return self._metric_index.get(name)

    def get_metrics_by_level(self, level: int) -> list:
        return [m for m in self._metrics if m.level == level]

    def get_metrics_by_category(self, category: str) -> list:
        return [m for m in self._metrics if m.category.lower() == category.lower()]

    def search_events(
        self, query: str, include_deprecated: bool = False
    ) -> list:
        """Search events by name or description (case-insensitive)."""
        query_lower = query.lower()
        tokens = query_lower.split()
        results = []
        for ev in self._events:
            if not include_deprecated and ev.deprecated:
                continue
            text = f"{ev.name} {ev.brief_description}".lower()
            if all(t in text for t in tokens):
                results.append(ev)
        return results

    def search_metrics(
        self, query: str, category: Optional[str] = None
    ) -> list:
        """Search metrics by name, description, or group (case-insensitive)."""
        query_lower = query.lower()
        tokens = query_lower.split()
        results = []
        for m in self._metrics:
            if category and m.category.lower() != category.lower():
                continue
            text = f"{m.name} {m.brief_description} {m.metric_group}".lower()
            if all(t in text for t in tokens):
                results.append(m)
        return results

    def get_all_referenced_events(self) -> set:
        """All event names referenced by any metric formula or LocateWith."""
        referenced = set()
        for m in self._metrics:
            for ev in m.events:
                referenced.add(ev["Name"].split(":")[0])
            if m.locate_with and m.locate_with != "#NA":
                for e in m.locate_with.split(";"):
                    referenced.add(e.strip())
        return referenced

    def get_unreachable_events(self, include_deprecated: bool = False) -> list:
        """Events not referenced by any metric or LocateWith."""
        referenced = self.get_all_referenced_events()
        unreachable = []
        for ev in self._events:
            if not include_deprecated and ev.deprecated:
                continue
            if ev.name not in referenced:
                unreachable.append(ev)
        return unreachable

    def coverage_stats(self) -> dict:
        """Event coverage statistics."""
        non_deprecated = [e for e in self._events if not e.deprecated]
        referenced = self.get_all_referenced_events()
        reached = [e for e in non_deprecated if e.name in referenced]
        unreachable = [e for e in non_deprecated if e.name not in referenced]
        return {
            "total_events": len(non_deprecated),
            "reached_events": len(reached),
            "unreachable_events": len(unreachable),
            "coverage_pct": (
                100.0 * len(reached) / len(non_deprecated) if non_deprecated else 0
            ),
        }
