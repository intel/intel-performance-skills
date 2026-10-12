"""TMA-based iterative drill-down logic."""

from dataclasses import dataclass
from typing import Optional

from ..core.catalog import PlatformCatalog
from ..core.formula import evaluate_metric
from ..core.tma_tree import TmaTree, TmaNode


@dataclass
class NodeResult:
    name: str
    level: int
    value: Optional[float]
    threshold_passed: Optional[bool]
    locate_with: str


@dataclass
class DrillDownSuggestion:
    target_nodes: list  # child node names to investigate
    events_needed: set  # events for perf stat
    perf_command_events: set  # events formatted for command
    rationale: str
    locate_with_events: set  # events for perf record sampling
    is_leaf: bool


class TmaDrillDown:
    """Manages TMA drill-down decisions."""

    def __init__(self, tree: TmaTree, catalog: PlatformCatalog):
        self.tree = tree
        self.catalog = catalog

    def initial_events(self) -> set:
        """Events needed for L1 TMA (4 root nodes) + Bottlenecks View."""
        events = set()
        for root in self.tree.roots:
            events.update(root.metric.event_names_with_modifiers)
        # Also include bottleneck metrics events
        for bm in self.tree.bottlenecks:
            events.update(bm.event_names_with_modifiers)
        return events

    def evaluate_level(
        self, nodes: list, event_values: dict, constants: dict = None
    ) -> list:
        """Evaluate metrics for a set of nodes given collected event values.

        Returns list of NodeResult sorted by value descending.
        """
        if constants is None:
            constants = {}

        results = []
        metric_values = {}

        for node in nodes:
            metric = node.metric
            value = evaluate_metric(metric, event_values, constants)
            if value is not None:
                metric_values[metric.legacy_name] = value
                metric_values[metric.name] = value

        # Evaluate thresholds
        for node in nodes:
            metric = node.metric
            value = evaluate_metric(metric, event_values, constants)

            threshold_passed = None
            if value is not None and metric.threshold:
                threshold_passed = self._evaluate_threshold(metric, metric_values)

            results.append(
                NodeResult(
                    name=node.name,
                    level=node.level,
                    value=value,
                    threshold_passed=threshold_passed,
                    locate_with=metric.locate_with or "",
                )
            )

        # Sort by value descending (highest bottleneck first)
        results.sort(key=lambda r: r.value if r.value is not None else 0, reverse=True)
        return results

    def _evaluate_threshold(self, metric, metric_values: dict) -> Optional[bool]:
        """Evaluate a metric's threshold formula."""
        threshold = metric.threshold
        if not threshold:
            return None

        formula = threshold.get("Formula", "")
        threshold_metrics = threshold.get("ThresholdMetrics", [])
        if not formula or not threshold_metrics:
            return None

        # Build alias values from threshold metrics
        alias_values = {}
        for tm in threshold_metrics:
            alias = tm["Alias"]
            value_key = tm["Value"]
            # Try both name and legacy_name
            if value_key in metric_values:
                alias_values[alias] = metric_values[value_key]
            else:
                # Try matching the metric name directly
                for key, val in metric_values.items():
                    if key in value_key or value_key in key:
                        alias_values[alias] = val
                        break
                if alias not in alias_values:
                    return None

        # Evaluate threshold formula
        try:
            expr = formula
            for alias in sorted(alias_values.keys(), key=len, reverse=True):
                expr = expr.replace(alias, str(float(alias_values[alias])))
            result = eval(expr, {"__builtins__": {}})
            return bool(result)
        except (SyntaxError, NameError, TypeError, ValueError, ZeroDivisionError):
            return None

    def suggest_next(
        self, results: list, current_node: Optional[str] = None
    ) -> Optional[DrillDownSuggestion]:
        """Based on evaluation results, suggest which subtree to explore next.

        Args:
            results: NodeResult list from evaluate_level
            current_node: name of current node (whose children we evaluated)

        Returns:
            DrillDownSuggestion or None if no further drill-down needed
        """
        # Find top node that passes threshold
        top = None
        for r in results:
            if r.threshold_passed and r.value is not None:
                top = r
                break

        # If no threshold passes, take the highest value anyway (if significant)
        if top is None:
            for r in results:
                if r.value is not None and r.value > 5.0:  # > 5% is worth investigating
                    top = r
                    break

        if top is None:
            return None

        # Get children of the top node
        node = self.tree.get_node(top.name)
        if node is None or node.is_leaf:
            # Leaf reached
            locate_events = set()
            if top.locate_with and top.locate_with != "#NA":
                locate_events = {e.strip() for e in top.locate_with.split(";")}
            return DrillDownSuggestion(
                target_nodes=[top.name],
                events_needed=set(),
                perf_command_events=set(),
                rationale=f"Leaf node reached: {top.name} = {top.value:.1f}%",
                locate_with_events=locate_events,
                is_leaf=True,
            )

        # Get events for children
        children = node.children
        children_events = set()
        for child in children:
            children_events.update(child.metric.event_names_with_modifiers)

        # Collect locate_with events for the top node
        locate_events = set()
        if top.locate_with and top.locate_with != "#NA":
            locate_events = {e.strip() for e in top.locate_with.split(";")}

        others = [r for r in results if r.name != top.name and r.value is not None]
        others_str = ", ".join(f"{r.name}={r.value:.1f}%" for r in others[:3])
        rationale = (
            f"{top.name} = {top.value:.1f}% "
            f"(threshold {'passed' if top.threshold_passed else 'highest value'}). "
            f"Others: {others_str}"
        )

        return DrillDownSuggestion(
            target_nodes=[c.name for c in children],
            events_needed=children_events,
            perf_command_events=children_events,
            rationale=rationale,
            locate_with_events=locate_events,
            is_leaf=False,
        )
