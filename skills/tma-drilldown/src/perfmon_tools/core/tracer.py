"""Decision tracing and observability for perfmon-skills.

Activated via PERFMON_TRACE=1 environment variable.
When disabled, all tracing calls are no-ops with zero overhead.
"""

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from functools import wraps
from pathlib import Path
from typing import Optional


TRACE_ENABLED = os.environ.get("PERFMON_TRACE", "0") == "1"


@dataclass
class DecisionNode:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: str = ""
    actor: str = "system"  # "ai", "human", or "system"
    operation: str = ""
    inputs: dict = field(default_factory=dict)
    reasoning: str = ""
    decision: str = ""
    alternatives: list = field(default_factory=list)  # [{option, reason_rejected}]
    confidence: float = 1.0  # 0.0-1.0
    parent_id: Optional[str] = None
    children_ids: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    duration_ms: float = 0.0


class _NoopDecision:
    """No-op context for when tracing is disabled."""

    def __setattr__(self, name, value):
        pass

    def __getattr__(self, name):
        return None


class _DecisionContext:
    """Context manager for recording a decision."""

    def __init__(self, tracer: "Trace", operation: str, parent_id: Optional[str]):
        self._tracer = tracer
        self._node = DecisionNode(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            operation=operation,
            parent_id=parent_id,
        )
        self._start = time.time()

    @property
    def id(self) -> str:
        return self._node.id

    @property
    def inputs(self):
        return self._node.inputs

    @inputs.setter
    def inputs(self, val):
        self._node.inputs = val

    @property
    def reasoning(self):
        return self._node.reasoning

    @reasoning.setter
    def reasoning(self, val):
        self._node.reasoning = val

    @property
    def decision(self):
        return self._node.decision

    @decision.setter
    def decision(self, val):
        self._node.decision = val

    @property
    def alternatives(self):
        return self._node.alternatives

    @alternatives.setter
    def alternatives(self, val):
        self._node.alternatives = val

    @property
    def confidence(self):
        return self._node.confidence

    @confidence.setter
    def confidence(self, val):
        self._node.confidence = val

    @property
    def actor(self):
        return self._node.actor

    @actor.setter
    def actor(self, val):
        self._node.actor = val

    @property
    def metadata(self):
        return self._node.metadata

    @metadata.setter
    def metadata(self, val):
        self._node.metadata = val

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._node.duration_ms = (time.time() - self._start) * 1000
        self._tracer._add_node(self._node)


class Trace:
    """Collects decision nodes into a DAG."""

    def __init__(self):
        self.nodes: list = []
        self._node_map: dict = {}

    @property
    def root_ids(self) -> list:
        return [n.id for n in self.nodes if n.parent_id is None]

    def decision(self, operation: str, parent_id: Optional[str] = None):
        """Context manager for recording a decision point."""
        if not TRACE_ENABLED:
            return _noop_context()
        return _DecisionContext(self, operation, parent_id)

    def _add_node(self, node: DecisionNode):
        self.nodes.append(node)
        self._node_map[node.id] = node
        if node.parent_id and node.parent_id in self._node_map:
            self._node_map[node.parent_id].children_ids.append(node.id)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(
            {
                "trace_version": "1.0",
                "nodes": [asdict(n) for n in self.nodes],
                "root_ids": self.root_ids,
            },
            indent=indent,
        )

    def to_mermaid(self) -> str:
        """Render trace as Mermaid flowchart."""
        lines = ["graph TD"]
        for node in self.nodes:
            label = f"{node.operation}\\n{node.decision}"
            shape = "([{}])" if node.actor == "human" else "[{}]"
            lines.append(f"    {node.id}{shape.format(label)}")
            if node.parent_id:
                edge_label = node.reasoning[:40] if node.reasoning else ""
                if edge_label:
                    lines.append(f"    {node.parent_id} -->|{edge_label}| {node.id}")
                else:
                    lines.append(f"    {node.parent_id} --> {node.id}")
        return "\n".join(lines)

    def to_dot(self) -> str:
        """Render trace as Graphviz DOT."""
        lines = ["digraph trace {", "    rankdir=TB;", '    node [shape=box, style=rounded];']
        for node in self.nodes:
            label = f"{node.operation}\\n{node.decision}\\nconf={node.confidence:.2f}"
            color = "lightblue" if node.actor == "system" else (
                "lightyellow" if node.actor == "human" else "lightgreen"
            )
            lines.append(
                f'    "{node.id}" [label="{label}", fillcolor="{color}", style="filled,rounded"];'
            )
        for node in self.nodes:
            if node.parent_id:
                label = node.reasoning[:30] if node.reasoning else ""
                lines.append(f'    "{node.parent_id}" -> "{node.id}" [label="{label}"];')
        lines.append("}")
        return "\n".join(lines)

    def to_html(self) -> str:
        """Render as self-contained interactive HTML with DAG visualization."""
        trace_json = self.to_json()
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Perfmon Trace</title>
<style>
body {{ font-family: monospace; margin: 20px; }}
.node {{ border: 1px solid #333; padding: 8px; margin: 4px; border-radius: 4px; cursor: pointer; }}
.node.system {{ background: #e3f2fd; }}
.node.human {{ background: #fff9c4; }}
.node.ai {{ background: #e8f5e9; }}
.detail {{ display: none; margin: 8px 0; padding: 8px; background: #f5f5f5; white-space: pre-wrap; }}
.tree {{ margin-left: 24px; border-left: 2px solid #ccc; padding-left: 12px; }}
</style></head><body>
<h2>Decision Trace</h2>
<div id="trace"></div>
<script>
const trace = {trace_json};
function renderNode(node, container) {{
    const div = document.createElement('div');
    div.className = 'node ' + node.actor;
    div.innerHTML = '<b>' + node.operation + '</b>: ' + node.decision +
        ' <small>(conf=' + node.confidence.toFixed(2) + ', ' + node.actor + ')</small>';
    const detail = document.createElement('div');
    detail.className = 'detail';
    detail.textContent = JSON.stringify({{
        inputs: node.inputs, reasoning: node.reasoning,
        alternatives: node.alternatives, metadata: node.metadata
    }}, null, 2);
    div.onclick = (e) => {{ e.stopPropagation(); detail.style.display = detail.style.display === 'none' ? 'block' : 'none'; }};
    div.appendChild(detail);
    container.appendChild(div);
    if (node.children_ids.length > 0) {{
        const tree = document.createElement('div');
        tree.className = 'tree';
        node.children_ids.forEach(cid => {{
            const child = trace.nodes.find(n => n.id === cid);
            if (child) renderNode(child, tree);
        }});
        container.appendChild(tree);
    }}
}}
const root = document.getElementById('trace');
trace.root_ids.forEach(rid => {{
    const node = trace.nodes.find(n => n.id === rid);
    if (node) renderNode(node, root);
}});
</script></body></html>"""

    def save(self, path: Path):
        """Save trace to session directory."""
        path.write_text(self.to_json())


# Module-level singleton
_tracer: Optional[Trace] = None


def get_tracer() -> Trace:
    """Get or create the global tracer instance."""
    global _tracer
    if _tracer is None:
        _tracer = Trace()
    return _tracer


def reset_tracer():
    """Reset the global tracer (for testing or new sessions)."""
    global _tracer
    _tracer = Trace()


@contextmanager
def _noop_context():
    """No-op context manager when tracing is disabled."""
    yield _NoopDecision()


def trace(operation: str):
    """Decorator for functions that make decisions. Records inputs/outputs.

    When PERFMON_TRACE is disabled, passes through with zero overhead.
    """
    def decorator(func):
        if not TRACE_ENABLED:
            return func

        @wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.decision(operation) as d:
                d.inputs = {"args": str(args)[:200], "kwargs": str(kwargs)[:200]}
                result = func(*args, **kwargs)
                d.decision = str(result)[:200]
                d.confidence = 1.0
            return result
        return wrapper
    return decorator
