"""Tests for decision tracing."""

import json
import os
from unittest.mock import patch

from perfmon_tools.core.tracer import (
    DecisionNode,
    Trace,
    _DecisionContext,
    get_tracer,
    reset_tracer,
)


class TestTrace:
    def setup_method(self):
        self.trace = Trace()

    def test_add_node(self):
        node = DecisionNode(operation="test_op", decision="chose_A")
        self.trace._add_node(node)
        assert len(self.trace.nodes) == 1
        assert self.trace.nodes[0].operation == "test_op"

    def test_root_ids(self):
        root = DecisionNode(operation="root")
        child = DecisionNode(operation="child", parent_id=root.id)
        self.trace._add_node(root)
        self.trace._add_node(child)
        assert root.id in self.trace.root_ids
        assert child.id not in self.trace.root_ids

    def test_parent_child_linkage(self):
        root = DecisionNode(operation="root")
        child = DecisionNode(operation="child", parent_id=root.id)
        self.trace._add_node(root)
        self.trace._add_node(child)
        assert child.id in root.children_ids

    def test_to_json(self):
        node = DecisionNode(operation="test", decision="result")
        self.trace._add_node(node)
        output = json.loads(self.trace.to_json())
        assert output["trace_version"] == "1.0"
        assert len(output["nodes"]) == 1
        assert output["nodes"][0]["operation"] == "test"

    def test_to_mermaid(self):
        node = DecisionNode(operation="analyze", decision="Backend_Bound")
        self.trace._add_node(node)
        mermaid = self.trace.to_mermaid()
        assert "graph TD" in mermaid
        assert "analyze" in mermaid
        assert "Backend_Bound" in mermaid

    def test_to_dot(self):
        node = DecisionNode(operation="select", decision="Memory_Bound", confidence=0.95)
        self.trace._add_node(node)
        dot = self.trace.to_dot()
        assert "digraph trace" in dot
        assert "Memory_Bound" in dot
        assert "0.95" in dot

    def test_to_html(self):
        node = DecisionNode(operation="drill", decision="DRAM_Bound")
        self.trace._add_node(node)
        html = self.trace.to_html()
        assert "<!DOCTYPE html>" in html
        assert "DRAM_Bound" in html


class TestDecisionContext:
    def test_context_manager(self):
        trace = Trace()
        ctx = _DecisionContext(trace, "test_decision", None)
        with ctx as d:
            d.inputs = {"key": "value"}
            d.decision = "chose_X"
            d.confidence = 0.8
        assert len(trace.nodes) == 1
        assert trace.nodes[0].decision == "chose_X"
        assert trace.nodes[0].confidence == 0.8
        assert trace.nodes[0].duration_ms >= 0
