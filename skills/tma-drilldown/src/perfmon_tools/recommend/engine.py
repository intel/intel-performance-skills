"""Recommendation engine — state machine orchestrator.

Deterministic layer: runs the full TMA drill-down without an LLM.
The LLM layer (Claude Code skill) sits on top and adds interpretation.
"""

import json
import subprocess
import time
from pathlib import Path
from typing import Optional

from ..core.catalog import PlatformCatalog
from ..core.context_budget import ContextBudget
from ..core.formula import evaluate_metric
from ..core.perf_output import parse_auto, parse_perf_stat_interval
from ..core.platform import PlatformInfo, _find_perfmon_root, detect_cpu, resolve_platform
from ..core.tma_tree import TmaTree
from ..core.tracer import get_tracer, TRACE_ENABLED
from ..cmdgen.generate import generate_perf_command, _format_event_spec
from .coverage import CoverageTracker
from .guidance import get_guidance, NO_OBSERVABILITY_NODES
from .preflight import create_strategy, detect_steady_state, compute_counter_budget
from .session import Session, SessionState, StepFinding
from .tma_drilldown import TmaDrillDown


DEFAULT_SESSIONS_DIR = Path.cwd() / "sessions"


class RecommendationEngine:
    """Orchestrates iterative TMA drill-down investigation."""

    def __init__(self, sessions_dir: Optional[Path] = None):
        self.sessions_dir = sessions_dir or DEFAULT_SESSIONS_DIR
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._perfmon_root = _find_perfmon_root()

    def start(
        self,
        platform: Optional[str] = None,
        pid: Optional[int] = None,
        command: Optional[str] = None,
        duration: int = 5,
    ) -> dict:
        """Start a new investigation session.

        Returns dict with session info and first perf command to run.
        """
        # Resolve platform
        from ..lookup.search import _resolve_by_shortname
        if platform:
            plat_info = _resolve_by_shortname(platform, self._perfmon_root)
        else:
            cpu = detect_cpu()
            plat_info = resolve_platform(cpu, self._perfmon_root)

        # Pre-flight checks
        strategy = create_strategy(plat_info)

        # Create session
        session = Session.create_new(
            self.sessions_dir, plat_info.shortname, pid=pid,
            command=command, duration=duration,
        )
        session.state.smt_active = strategy.smt_active
        session.state.use_perf_metrics = strategy.use_perf_metrics

        # Load catalog and tree
        catalog = PlatformCatalog(plat_info, self._perfmon_root)
        tree = TmaTree(catalog)
        drilldown = TmaDrillDown(tree, catalog)

        # Generate initial command (L1 + Bottlenecks)
        initial_events = drilldown.initial_events()
        budget = compute_counter_budget(initial_events, plat_info)

        cmd_result = generate_perf_command(
            platform=plat_info.shortname,
            tma_level=1,
            duration=duration,
            pid=pid,
            command=command,
            json_output=True,
        )

        session.state.state = "COLLECTING"
        session.save()

        # Trace decision
        if TRACE_ENABLED:
            with get_tracer().decision("start_investigation") as d:
                d.inputs = {"platform": plat_info.shortname, "pid": pid, "command": command}
                d.decision = f"Starting L1 collection with {len(initial_events)} events"
                d.confidence = 1.0

        return {
            "session_dir": str(session.dir),
            "platform": plat_info.shortname,
            "strategy": {
                "smt_active": strategy.smt_active,
                "use_perf_metrics": strategy.use_perf_metrics,
                "counters": f"{strategy.programmable_counters} GP + {strategy.fixed_counters} fixed",
            },
            "counter_budget": budget,
            "command": cmd_result["commands"][0],
            "notes": strategy.notes + cmd_result.get("notes", []),
            "state": "COLLECTING",
            "next_action": "Run the perf command, then feed output to 'recommend analyze'",
        }

    def analyze(
        self,
        perf_output: str,
        session_dir: Optional[str] = None,
        constants: Optional[dict] = None,
    ) -> dict:
        """Analyze perf stat output and determine next steps.

        Args:
            perf_output: raw perf stat output (text or JSON)
            session_dir: path to session directory (default: most recent)
            constants: system constants (SYSTEM_TSC_FREQ, etc.)

        Returns:
            dict with analysis results, finding, and next step suggestion
        """
        if constants is None:
            constants = {}

        # Load session
        if session_dir:
            session = Session(Path(session_dir))
        else:
            session = Session.find_latest(self.sessions_dir)
            if not session:
                raise ValueError("No active session. Run 'recommend start' first.")

        # Load platform catalog and tree
        from ..lookup.search import _resolve_by_shortname
        plat_info = _resolve_by_shortname(session.state.platform, self._perfmon_root)
        catalog = PlatformCatalog(plat_info, self._perfmon_root)
        tree = TmaTree(catalog)
        drilldown = TmaDrillDown(tree, catalog)
        coverage = CoverageTracker(catalog)

        # Parse perf output
        parsed = parse_auto(perf_output)
        event_values = parsed.event_values

        # Record coverage
        coverage.record_events(set(event_values.keys()))
        # Also record base names (strip cpu/ prefix)
        base_events = set()
        for ev in event_values:
            base = ev.replace("cpu/", "").replace("cpu_core/", "").rstrip("/")
            base_events.add(base)
        coverage.record_events(base_events)

        # Determine which nodes to evaluate
        current_path = session.state.path
        if not current_path:
            # First step: evaluate L1 roots
            nodes_to_eval = tree.roots
        else:
            # Drill-down: evaluate children of last node in path
            last_node = current_path[-1]
            children = tree.get_children(last_node)
            if children:
                nodes_to_eval = children
            else:
                nodes_to_eval = []

        # Evaluate nodes
        results = drilldown.evaluate_level(nodes_to_eval, event_values, constants)

        # Get suggestion for next step
        suggestion = drilldown.suggest_next(results, current_path[-1] if current_path else None)

        # Save step data
        step_dir = session.new_step()
        node_values = {r.name: r.value for r in results if r.value is not None}
        analysis_data = {
            "node_values": node_values,
            "threshold_results": [
                {"name": r.name, "value": r.value, "passed": r.threshold_passed}
                for r in results
            ],
            "suggestion": suggestion.__dict__ if suggestion else None,
        }
        # Convert sets to lists for JSON serialization
        analysis_json = json.loads(json.dumps(analysis_data, default=lambda x: list(x) if isinstance(x, set) else x))
        session.save_step_data(
            step_dir,
            command="(provided by user)",
            raw_output=perf_output,
            parsed=event_values,
            analysis=analysis_json,
        )

        # Create finding
        top_result = results[0] if results else None
        if top_result and top_result.value is not None:
            siblings = {r.name: r.value for r in results[1:] if r.value is not None}
            finding = StepFinding(
                level=top_result.level,
                top_node=top_result.name,
                value=top_result.value,
                threshold_passed=top_result.threshold_passed or False,
                siblings=siblings,
                path_so_far=current_path + [top_result.name],
            )
            session.add_finding(finding)

        # Determine next state
        is_complete = False
        next_command = None
        guidance_info = None

        if suggestion is None or suggestion.is_leaf:
            is_complete = True
            session.state.state = "COMPLETE"
            # Generate guidance
            leaf_node = top_result.name if top_result else current_path[-1] if current_path else ""
            guidance_info = get_guidance(leaf_node)

            # Generate summary
            coverage_report = coverage.report(session.state.path)
            summary = {
                "bottleneck_path": session.state.path,
                "final_node": leaf_node,
                "guidance": guidance_info,
                "coverage_pct": coverage_report.coverage_pct,
                "suggested_expansions": [
                    {"node": s["tma_node"], "rationale": s["rationale"], "events": s["events"][:5]}
                    for s in coverage_report.suggested_expansions
                ],
                "locate_with": list(suggestion.locate_with_events) if suggestion else [],
            }
            session.save_summary(summary)
        else:
            session.state.state = "COLLECTING"
            # Generate next perf command
            cmd_result = generate_perf_command(
                platform=session.state.platform,
                tma_node=top_result.name if top_result else None,
                duration=session.state.duration,
                pid=session.state.target_pid,
                command=session.state.target_command,
                json_output=True,
            )
            next_command = cmd_result["commands"][0] if cmd_result["commands"] else None

        session.save()

        # Trace
        if TRACE_ENABLED and top_result:
            with get_tracer().decision("select_bottleneck") as d:
                d.inputs = {"node_values": node_values}
                d.decision = top_result.name
                d.reasoning = (
                    f"{top_result.name} = {top_result.value:.1f}%, "
                    f"threshold {'passed' if top_result.threshold_passed else 'not evaluated'}"
                )
                d.confidence = 0.95 if top_result.threshold_passed else 0.7
                d.alternatives = [
                    {"option": r.name, "reason_rejected": f"value={r.value:.1f}%"}
                    for r in results[1:3]
                ]

        # Build response
        response = {
            "session_dir": str(session.dir),
            "state": session.state.state,
            "step": session.state.current_step,
            "path": session.state.path,
            "results": [
                {"name": r.name, "value": r.value, "threshold_passed": r.threshold_passed}
                for r in results
            ],
            "multiplexing_issues": [
                {"event": m.event, "measured_pct": m.enabled_pct}
                for m in parsed.multiplexing_issues
            ],
            "is_complete": is_complete,
        }

        if next_command:
            response["next_command"] = next_command
            response["next_action"] = f"Run the command, then feed output to 'recommend analyze'"
        if guidance_info:
            response["guidance"] = guidance_info
        if is_complete and suggestion and suggestion.locate_with_events:
            response["sampling_suggestion"] = {
                "events": list(suggestion.locate_with_events),
                "command": f"perf record -e {','.join(suggestion.locate_with_events)} "
                           f"-p {session.state.target_pid}" if session.state.target_pid else
                           f"perf record -e {','.join(suggestion.locate_with_events)} -- "
                           f"{session.state.target_command or 'sleep 5'}",
            }

        return response

    def status(self, session_dir: Optional[str] = None) -> dict:
        """Get current session status."""
        if session_dir:
            session = Session(Path(session_dir))
        else:
            session = Session.find_latest(self.sessions_dir)
            if not session:
                return {"state": "NO_SESSION", "message": "No active session found."}

        return {
            "session_dir": str(session.dir),
            "state": session.state.state,
            "platform": session.state.platform,
            "step": session.state.current_step,
            "path": session.state.path,
            "findings": [
                {"level": f.level, "node": f.top_node, "value": f.value}
                for f in session.state.findings
            ],
            "target": {
                "pid": session.state.target_pid,
                "command": session.state.target_command,
            },
        }

    def summary(self, session_dir: Optional[str] = None) -> dict:
        """Get investigation summary."""
        if session_dir:
            session = Session(Path(session_dir))
        else:
            session = Session.find_latest(self.sessions_dir)
            if not session:
                return {"error": "No session found."}

        summary_path = session.dir / "summary.json"
        if summary_path.exists():
            return json.loads(summary_path.read_text())

        # Not complete yet
        return {
            "state": session.state.state,
            "path_so_far": session.state.path,
            "message": "Investigation not complete. Continue with 'recommend analyze'.",
        }
