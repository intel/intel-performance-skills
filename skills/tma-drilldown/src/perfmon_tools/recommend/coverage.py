"""Event coverage tracking and gap-driven investigation suggestions."""

from dataclasses import dataclass, field
from typing import Optional

from ..core.catalog import PlatformCatalog


# Domain-to-TMA affinity: which unreachable event domains provide deeper insight
# for each TMA bottleneck node
DOMAIN_AFFINITY = {
    "DRAM_Bound": {
        "domains": ["OCR.DEMAND_DATA_RD", "OFFCORE_REQUESTS", "MEM_TRANS_RETIRED"],
        "rationale": "Memory hierarchy detail: NUMA locality, load latency distribution",
    },
    "L1_Bound": {
        "domains": ["CYCLE_ACTIVITY.STALLS_L1D", "L1D_PEND_MISS", "LD_BLOCKS"],
        "rationale": "L1D stall breakdown: fill buffer pressure, address aliasing",
    },
    "L2_Bound": {
        "domains": ["L2_RQSTS.ALL_DEMAND", "L2_TRANS"],
        "rationale": "L2 request types and writebacks",
    },
    "L3_Bound": {
        "domains": ["OCR.DEMAND_DATA_RD.L3_HIT.SNOOP", "CORE_SNOOP_RESPONSE"],
        "rationale": "Cross-core sharing patterns, snoop responses",
    },
    "Core_Bound": {
        "domains": ["EXE_ACTIVITY.3_PORTS", "EXE_ACTIVITY.4_PORTS", "UOPS_EXECUTED.CORE_CYCLES_GE"],
        "rationale": "Execution port saturation and utilization",
    },
    "Fetch_Latency": {
        "domains": ["FRONTEND_RETIRED.LATENCY_GE", "IDQ_BUBBLES", "IDQ_UOPS_NOT_DELIVERED"],
        "rationale": "Frontend delivery gaps and bubble analysis",
    },
    "Branch_Mispredicts": {
        "domains": ["BR_MISP_RETIRED.COND", "BR_MISP_RETIRED.INDIRECT", "BR_MISP_RETIRED.NEAR_TAKEN"],
        "rationale": "Branch type breakdown: conditional vs indirect vs taken",
    },
    "Retiring": {
        "domains": ["INT_VEC_RETIRED", "FP_ARITH_DISPATCHED"],
        "rationale": "Vectorization quality: actual vector width distribution",
    },
    "Store_Bound": {
        "domains": ["L2_RQSTS.RFO", "OFFCORE_REQUESTS.DEMAND_RFO"],
        "rationale": "Store-to-memory path: RFO hits/misses",
    },
    "Machine_Clears": {
        "domains": ["MACHINE_CLEARS.SMC", "RTM_RETIRED"],
        "rationale": "Clear types: self-modifying code, TSX aborts",
    },
}


@dataclass
class CoverageReport:
    total_events: int
    reached_events: set = field(default_factory=set)
    coverage_pct: float = 0.0
    unreached_by_domain: dict = field(default_factory=dict)
    suggested_expansions: list = field(default_factory=list)


class CoverageTracker:
    """Track which events have been touched during an investigation."""

    def __init__(self, catalog: PlatformCatalog):
        self.catalog = catalog
        self._reached = set()
        # Get non-deprecated core events
        self._all_events = {
            e.name for e in catalog.events
            if not e.deprecated
        }

    def record_events(self, events: set):
        """Record that these events were collected."""
        self._reached.update(events)

    @property
    def coverage_pct(self) -> float:
        if not self._all_events:
            return 0.0
        return 100.0 * len(self._reached & self._all_events) / len(self._all_events)

    def report(self, current_path: list = None) -> CoverageReport:
        """Generate coverage report with gap-driven suggestions."""
        reached = self._reached & self._all_events
        unreached = self._all_events - reached

        # Group unreached by prefix
        by_domain = {}
        for ev in sorted(unreached):
            prefix = ev.split(".")[0]
            if prefix not in by_domain:
                by_domain[prefix] = []
            by_domain[prefix].append(ev)

        # Generate suggestions based on current TMA path
        suggestions = []
        if current_path:
            for node_name in current_path:
                affinity = DOMAIN_AFFINITY.get(node_name)
                if affinity:
                    # Find matching unreached events
                    matching = []
                    for domain_prefix in affinity["domains"]:
                        for ev in unreached:
                            if ev.startswith(domain_prefix):
                                matching.append(ev)
                    if matching:
                        suggestions.append({
                            "tma_node": node_name,
                            "rationale": affinity["rationale"],
                            "events": matching[:10],
                            "count": len(matching),
                        })

        return CoverageReport(
            total_events=len(self._all_events),
            reached_events=reached,
            coverage_pct=self.coverage_pct,
            unreached_by_domain=by_domain,
            suggested_expansions=suggestions,
        )
