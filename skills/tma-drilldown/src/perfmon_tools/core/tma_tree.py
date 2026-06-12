"""TMA hierarchy builder from ParentCategory relationships."""

from dataclasses import dataclass, field
from typing import Optional


TMA_L1_ROOTS = ["Frontend_Bound", "Bad_Speculation", "Backend_Bound", "Retiring"]


@dataclass
class TmaNode:
    metric: object  # MetricDef
    children: list = field(default_factory=list)
    parent: Optional["TmaNode"] = None

    @property
    def name(self) -> str:
        return self.metric.name

    @property
    def level(self) -> int:
        return self.metric.level

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    @property
    def threshold(self) -> dict:
        return self.metric.threshold

    @property
    def locate_with(self) -> str:
        return self.metric.locate_with


class TmaTree:
    """TMA hierarchy built from metric ParentCategory relationships."""

    def __init__(self, catalog):
        self.roots = []
        self.bottlenecks = catalog.bottleneck_metrics
        self.info_metrics = catalog.info_metrics
        self._nodes = {}  # name -> TmaNode

        tree_metrics = catalog.tree_nodes

        # Create all nodes
        for m in tree_metrics:
            self._nodes[m.name] = TmaNode(metric=m)

        # Link parent-child
        for m in tree_metrics:
            node = self._nodes[m.name]
            parent_name = m.parent_category
            if parent_name and parent_name in self._nodes:
                parent_node = self._nodes[parent_name]
                parent_node.children.append(node)
                node.parent = parent_node
            elif m.name in TMA_L1_ROOTS:
                self.roots.append(node)

        # Sort roots in canonical order
        root_order = {name: i for i, name in enumerate(TMA_L1_ROOTS)}
        self.roots.sort(key=lambda n: root_order.get(n.name, 99))

        # Sort children by level then name
        for node in self._nodes.values():
            node.children.sort(key=lambda n: (n.level, n.name))

    @property
    def max_level(self) -> int:
        if not self._nodes:
            return 0
        return max(n.level for n in self._nodes.values())

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    def get_node(self, name: str) -> Optional[TmaNode]:
        return self._nodes.get(name)

    def get_children(self, name: str) -> list:
        node = self._nodes.get(name)
        if node is None:
            return []
        return node.children

    def get_nodes_at_level(self, level: int) -> list:
        return [n for n in self._nodes.values() if n.level == level]

    def get_path_to_root(self, name: str) -> list:
        """Return path from node to root (inclusive), leaf first."""
        node = self._nodes.get(name)
        if node is None:
            return []
        path = []
        while node is not None:
            path.append(node)
            node = node.parent
        return path

    def get_subtree_events(self, name: str) -> set:
        """All events needed to compute a node and all its descendants."""
        node = self._nodes.get(name)
        if node is None:
            return set()
        events = set()
        stack = [node]
        while stack:
            current = stack.pop()
            events.update(current.metric.event_names)
            stack.extend(current.children)
        return events

    def get_level_events(self, level: int) -> set:
        """All events needed to compute all nodes at a given level."""
        events = set()
        for node in self.get_nodes_at_level(level):
            events.update(node.metric.event_names)
        return events

    def get_children_events(self, name: str) -> set:
        """Events needed to compute direct children of a node."""
        events = set()
        for child in self.get_children(name):
            events.update(child.metric.event_names)
        return events

    def print_tree(self, max_level: Optional[int] = None) -> str:
        """Text representation of the TMA tree."""
        lines = []

        def _walk(node, indent=0):
            if max_level and node.level > max_level:
                return
            prefix = "  " * indent
            leaf_marker = " [leaf]" if node.is_leaf else ""
            lines.append(f"{prefix}{node.name} (L{node.level}){leaf_marker}")
            for child in node.children:
                _walk(child, indent + 1)

        for root in self.roots:
            _walk(root)
        return "\n".join(lines)

    def level_summary(self) -> dict:
        """Count of nodes per level."""
        summary = {}
        for node in self._nodes.values():
            level = node.level
            summary[level] = summary.get(level, 0) + 1
        return dict(sorted(summary.items()))
