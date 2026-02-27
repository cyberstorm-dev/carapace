from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List
import re

import yaml


@dataclass
class Config:
    milestone: int
    labels: Dict[str, str]
    exempt_issues: List[int]
    phase: int
    check_tiers: Dict[str, str] = field(default_factory=dict)
    base_branch: str = "dev"


DEFAULT_LABELS = {
    "molt": "molt",
    "tan": "tan",
    "needs_pr": "needs-pr",
}


def _parse_phase(value) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        m = re.search(r"phase\s+(\d+)", value, re.IGNORECASE)
        if m:
            return int(m.group(1))
    raise ValueError(f"Cannot parse phase/milestone from: {value}")


def load_config(path: str) -> Config:
    data = yaml.safe_load(Path(path).read_text())
    labels = {**DEFAULT_LABELS, **(data.get("labels", {}) or {})}
    exempt = data.get("exempt_issues") or []
    phase = _parse_phase(data.get("phase") or data.get("milestone"))
    return Config(
        milestone=phase,
        labels=labels,
        exempt_issues=[int(i) for i in exempt],
        phase=phase,
        check_tiers=data.get("check_tiers", {}) or {},
        base_branch=data.get("base_branch", "dev"),
    )
