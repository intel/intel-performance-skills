"""CPU detection and platform resolution against perfmon mapfile."""

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CoreInfo:
    core_type: str  # "P-core", "E-core", or "" for non-hybrid
    role_name: str  # "Core", "Atom", "LowPower_Atom", or ""
    native_model_id: str  # hex mask, e.g. "0x40"
    event_files: dict = field(default_factory=dict)  # {type: path} e.g. {"core": Path(...)}
    metrics_files: list = field(default_factory=list)


@dataclass
class PlatformInfo:
    shortname: str  # e.g. "SPR"
    name: str  # e.g. "Sapphire Rapids Server"
    family_model: str  # e.g. "GenuineIntel-6-8F"
    version: str  # e.g. "V1.39"
    is_hybrid: bool
    default_level: int  # 0-2, TMA level supported natively by PERF_METRICS
    core_types: list  # list[CoreInfo]


@dataclass
class CpuInfo:
    vendor: str  # e.g. "GenuineIntel"
    family: int  # decimal, e.g. 6
    model: int  # decimal, e.g. 143 (0x8F)
    stepping: int
    model_name: str
    family_model: str  # e.g. "GenuineIntel-6-8F"


def detect_cpu(cpuinfo_path: str = "/proc/cpuinfo") -> CpuInfo:
    """Read /proc/cpuinfo and return structured CPU info."""
    text = Path(cpuinfo_path).read_text()
    info = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if key not in info:
                info[key] = val

    vendor = info.get("vendor_id", "")
    family = int(info.get("cpu family", "0"))
    model = int(info.get("model", "0"))
    stepping = int(info.get("stepping", "0"))
    model_name = info.get("model name", "")
    model_hex = format(model, "X")
    family_model = f"{vendor}-{family}-{model_hex}"

    return CpuInfo(
        vendor=vendor,
        family=family,
        model=model,
        stepping=stepping,
        model_name=model_name,
        family_model=family_model,
    )


def _find_perfmon_root() -> Path:
    """Locate the perfmon data directory. Checks:
    1. PERFMON_DATA env var
    2. ./perfmon/ (symlink or submodule in current repo)
    3. ../perfmon/ (sibling directory)
    """
    import os

    env_path = os.environ.get("PERFMON_DATA")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    candidates = [
        Path(__file__).resolve().parents[3] / "perfmon",  # src/perfmon_tools/core -> repo/perfmon
        Path.cwd() / "perfmon",
        Path.cwd().parent / "perfmon",
    ]
    for p in candidates:
        if p.exists() and (p / "mapfile.csv").exists():
            return p

    raise FileNotFoundError(
        "Cannot find perfmon data. Set PERFMON_DATA env var or ensure ./perfmon/ exists."
    )


def _load_platform_config(perfmon_root: Path) -> dict:
    """Load platform_config.json as a dict keyed by ShortName."""
    config_path = perfmon_root / "scripts" / "config" / "platform_config.json"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        entries = json.load(f)
    result = {}
    for entry in entries:
        short = entry.get("ShortName", "")
        if short not in result:
            result[short] = entry
    return result


def _parse_mapfile(perfmon_root: Path) -> dict:
    """Parse mapfile.csv into a dict keyed by family-model string.
    Returns: {family_model: [{version, filename, event_type, core_type, native_model_id, role_name}]}
    """
    mapfile_path = perfmon_root / "mapfile.csv"
    entries = {}
    with open(mapfile_path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if len(row) < 4:
                continue
            fm = row[0].strip()
            entry = {
                "family_model": fm,
                "version": row[1].strip() if len(row) > 1 else "",
                "filename": row[2].strip() if len(row) > 2 else "",
                "event_type": row[3].strip() if len(row) > 3 else "",
                "core_type": row[4].strip() if len(row) > 4 else "",
                "native_model_id": row[5].strip() if len(row) > 5 else "",
                "role_name": row[6].strip() if len(row) > 6 else "",
            }
            if fm not in entries:
                entries[fm] = []
            entries[fm].append(entry)
    return entries


def _match_family_model(target: str, mapfile_entries: dict) -> Optional[str]:
    """Find matching family-model key in mapfile, handling regex patterns.
    Target format: GenuineIntel-6-8F
    Mapfile may have patterns like: GenuineIntel-6-9[7A]
    """
    if target in mapfile_entries:
        return target

    for key in mapfile_entries:
        if key == target:
            return key
        try:
            pattern = "^" + re.escape(key).replace(r"\[", "[").replace(r"\]", "]") + "$"
            if re.match(pattern, target):
                return key
        except re.error:
            continue
    return None


def _derive_shortname(filename: str) -> str:
    """Extract platform shortname from file path. e.g. /SPR/events/... -> SPR"""
    parts = filename.strip("/").split("/")
    if parts:
        return parts[0]
    return ""


def resolve_platform(
    cpu: CpuInfo, perfmon_root: Optional[Path] = None
) -> PlatformInfo:
    """Map CpuInfo to perfmon platform files."""
    if perfmon_root is None:
        perfmon_root = _find_perfmon_root()

    mapfile_entries = _parse_mapfile(perfmon_root)
    platform_config = _load_platform_config(perfmon_root)

    matched_key = _match_family_model(cpu.family_model, mapfile_entries)
    if matched_key is None:
        raise ValueError(
            f"CPU {cpu.family_model} ({cpu.model_name}) not found in mapfile.csv"
        )

    rows = mapfile_entries[matched_key]
    shortname = _derive_shortname(rows[0]["filename"])

    # Group by role_name for hybrid detection
    roles = {}
    for row in rows:
        role = row["role_name"] or ""
        if role not in roles:
            roles[role] = CoreInfo(
                core_type=row["core_type"],
                role_name=role,
                native_model_id=row["native_model_id"],
            )
        core_info = roles[role]

        event_type = row["event_type"]
        filepath = perfmon_root / row["filename"].lstrip("/")

        if event_type == "metrics":
            core_info.metrics_files.append(filepath)
        else:
            core_info.event_files[event_type] = filepath

    named_roles = {k for k in roles if k != ""}
    is_hybrid = len(named_roles) > 1
    core_types = list(roles.values())

    # Look up platform config metadata
    config = platform_config.get(shortname, {})
    name = config.get("Name", shortname)
    default_level = config.get("DefaultLevel", 0)

    version = rows[0]["version"] if rows else ""

    return PlatformInfo(
        shortname=shortname,
        name=name,
        family_model=cpu.family_model,
        version=version,
        is_hybrid=is_hybrid,
        default_level=default_level,
        core_types=core_types,
    )


def list_platforms(perfmon_root: Optional[Path] = None) -> list:
    """Enumerate all available platforms from mapfile."""
    if perfmon_root is None:
        perfmon_root = _find_perfmon_root()

    mapfile_entries = _parse_mapfile(perfmon_root)
    platform_config = _load_platform_config(perfmon_root)

    seen = set()
    platforms = []
    for fm, rows in mapfile_entries.items():
        shortname = _derive_shortname(rows[0]["filename"])
        if shortname in seen:
            continue
        seen.add(shortname)

        config = platform_config.get(shortname, {})
        is_hybrid = any(r["role_name"] for r in rows)
        platforms.append(
            PlatformInfo(
                shortname=shortname,
                name=config.get("Name", shortname),
                family_model=fm,
                version=rows[0].get("version", ""),
                is_hybrid=is_hybrid,
                default_level=config.get("DefaultLevel", 0),
                core_types=[],
            )
        )

    return sorted(platforms, key=lambda p: p.shortname)
