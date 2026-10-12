"""Context window budget tracking for the recommendation engine.

Prevents attention loss by tracking token usage across stateful workflows,
enforcing compression, and ensuring decision-relevant data stays within
the LLM's effective attention window.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StepUsage:
    step: int
    raw_size: int  # tokens in raw perf output
    compressed_size: int  # tokens in compact finding
    passed_forward: int  # tokens actually sent to LLM


@dataclass
class ContextBudget:
    max_tokens: int = 8192
    current_tokens: int = 0
    per_step_usage: list = field(default_factory=list)

    def estimate_tokens(self, text: str) -> int:
        """Approximate token count (~4 chars per token for mixed content)."""
        return len(text) // 4

    def would_exceed(self, new_data: str) -> bool:
        """Check if adding new_data exceeds budget."""
        return self.current_tokens + self.estimate_tokens(new_data) > self.max_tokens

    def record_step(self, step: int, raw_text: str, compact_finding: str):
        """Record token usage for a completed step."""
        raw_tokens = self.estimate_tokens(raw_text)
        compact_tokens = self.estimate_tokens(compact_finding)
        self.current_tokens += compact_tokens
        self.per_step_usage.append(
            StepUsage(
                step=step,
                raw_size=raw_tokens,
                compressed_size=compact_tokens,
                passed_forward=compact_tokens,
            )
        )

    @property
    def headroom(self) -> int:
        return max(0, self.max_tokens - self.current_tokens)

    @property
    def compression_ratio(self) -> float:
        """Overall compression: how much we reduced raw data."""
        total_raw = sum(s.raw_size for s in self.per_step_usage)
        total_compressed = sum(s.compressed_size for s in self.per_step_usage)
        if total_raw == 0:
            return 1.0
        return total_compressed / total_raw

    def report(self) -> dict:
        return {
            "max_tokens": self.max_tokens,
            "current_tokens": self.current_tokens,
            "headroom": self.headroom,
            "steps": len(self.per_step_usage),
            "compression_ratio": f"{self.compression_ratio:.2%}",
            "per_step": [
                {
                    "step": s.step,
                    "raw_tokens": s.raw_size,
                    "compressed_tokens": s.compressed_size,
                }
                for s in self.per_step_usage
            ],
        }


def prepare_focus_window(
    findings: list,
    current_step_data: dict,
    budget: int = 4096,
) -> dict:
    """Prepare data for LLM consumption with attention-aware ordering.

    Strategy: critical data at start and end (not buried in middle).

    Args:
        findings: list of compact findings from prior steps
        current_step_data: current step's analyzed data (metrics, thresholds)
        budget: max tokens for the focus window

    Returns:
        dict with 'history' (prior findings) and 'current' (focus data)
    """
    # History always fits (compact findings are ~200 tokens each)
    history_tokens = sum(len(str(f)) // 4 for f in findings)

    # Current step: prioritize by relevance
    current = current_step_data.copy()
    if "node_values" in current:
        # Sort by value descending (most important first)
        sorted_nodes = sorted(
            current["node_values"].items(), key=lambda x: x[1], reverse=True
        )
        remaining_budget = budget - history_tokens
        # Keep top nodes that fit in budget
        trimmed = {}
        for name, value in sorted_nodes:
            entry_tokens = len(f"{name}: {value}") // 4
            if remaining_budget - entry_tokens < 0:
                break
            trimmed[name] = value
            remaining_budget -= entry_tokens
        current["node_values"] = trimmed

    return {
        "history": findings,  # at the start (attention is good here)
        "current": current,  # at the end (attention is also good here)
    }
