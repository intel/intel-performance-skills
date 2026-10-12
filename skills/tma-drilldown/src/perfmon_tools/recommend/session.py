"""Session directory management for investigation state."""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class StepFinding:
    level: int
    top_node: str
    value: float
    threshold_passed: bool
    siblings: dict  # {name: value}
    path_so_far: list  # path from L1 to current


@dataclass
class SessionState:
    created: str
    platform: str
    target_pid: Optional[int] = None
    target_command: Optional[str] = None
    duration: int = 5
    state: str = "IDLE"  # IDLE, STARTED, COLLECTING, ANALYZED, COMPLETE
    current_step: int = 0
    path: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    smt_active: bool = False
    phase_detected: bool = False
    use_perf_metrics: bool = False


class Session:
    """Manages investigation session directory and state."""

    def __init__(self, session_dir: Path):
        self.dir = session_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self.dir / "session.json"

        if self._state_path.exists():
            self.state = self._load_state()
        else:
            self.state = SessionState(
                created=time.strftime("%Y-%m-%dT%H:%M:%S"),
                platform="",
            )

    def _load_state(self) -> SessionState:
        data = json.loads(self._state_path.read_text())
        findings = [StepFinding(**f) for f in data.pop("findings", [])]
        state = SessionState(**data)
        state.findings = findings
        return state

    def save(self):
        data = {
            "created": self.state.created,
            "platform": self.state.platform,
            "target_pid": self.state.target_pid,
            "target_command": self.state.target_command,
            "duration": self.state.duration,
            "state": self.state.state,
            "current_step": self.state.current_step,
            "path": self.state.path,
            "findings": [asdict(f) for f in self.state.findings],
            "smt_active": self.state.smt_active,
            "phase_detected": self.state.phase_detected,
            "use_perf_metrics": self.state.use_perf_metrics,
        }
        self._state_path.write_text(json.dumps(data, indent=2))

    def new_step(self) -> Path:
        """Create directory for next step."""
        self.state.current_step += 1
        step_name = f"step_{self.state.current_step:02d}"
        if self.state.path:
            step_name += f"_{self.state.path[-1].lower()}"
        step_dir = self.dir / step_name
        step_dir.mkdir(parents=True, exist_ok=True)
        return step_dir

    def save_step_data(self, step_dir: Path, command: str, raw_output: str,
                       parsed: dict, analysis: dict):
        """Save all data for a completed step."""
        (step_dir / "command.txt").write_text(command)
        (step_dir / "raw_output.txt").write_text(raw_output)
        (step_dir / "parsed.json").write_text(json.dumps(parsed, indent=2))
        (step_dir / "analysis.json").write_text(json.dumps(analysis, indent=2))

    def add_finding(self, finding: StepFinding):
        self.state.findings.append(finding)
        if finding.top_node:
            self.state.path.append(finding.top_node)

    def save_summary(self, summary: dict):
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=2))

    @staticmethod
    def create_new(base_dir: Path, platform: str, pid: Optional[int] = None,
                   command: Optional[str] = None, duration: int = 5) -> "Session":
        """Create a new investigation session."""
        timestamp = time.strftime("%Y-%m-%d_%H%M%S")
        target = f"pid{pid}" if pid else "cmd"
        session_dir = base_dir / f"{timestamp}_{target}"
        session = Session(session_dir)
        session.state.platform = platform
        session.state.target_pid = pid
        session.state.target_command = command
        session.state.duration = duration
        session.state.state = "STARTED"
        session.save()
        return session

    @staticmethod
    def find_latest(base_dir: Path) -> Optional["Session"]:
        """Find the most recent session."""
        if not base_dir.exists():
            return None
        sessions = sorted(base_dir.iterdir(), reverse=True)
        for d in sessions:
            if d.is_dir() and (d / "session.json").exists():
                return Session(d)
        return None
