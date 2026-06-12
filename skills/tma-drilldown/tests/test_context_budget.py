"""Tests for context budget tracking."""

from perfmon_tools.core.context_budget import ContextBudget


class TestContextBudget:
    def test_estimate_tokens(self):
        budget = ContextBudget()
        # ~4 chars per token, integer division
        assert budget.estimate_tokens("hello world") == 2  # 11 chars // 4 = 2

    def test_would_exceed(self):
        budget = ContextBudget(max_tokens=100)
        # Small text should fit
        assert not budget.would_exceed("short text")
        # Very long text should exceed
        long_text = "x" * 500  # 500//4 = 125 tokens > 100
        assert budget.would_exceed(long_text)

    def test_record_step(self):
        budget = ContextBudget(max_tokens=8192)
        budget.record_step(1, raw_text="x" * 5000, compact_finding="y" * 200)
        report = budget.report()
        assert report["steps"] == 1
        assert report["current_tokens"] == 50  # 200 // 4

    def test_report(self):
        budget = ContextBudget(max_tokens=8192)
        budget.record_step(1, raw_text="x" * 8000, compact_finding="y" * 200)
        budget.record_step(2, raw_text="x" * 6000, compact_finding="y" * 180)
        report = budget.report()
        assert report["steps"] == 2
        assert report["current_tokens"] == 50 + 45  # 200//4 + 180//4
        assert report["headroom"] > 0
