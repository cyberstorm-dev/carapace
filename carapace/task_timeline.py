from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Mapping, MutableMapping, Optional

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = ROOT / "workspace" / "logs" / "agent-task-events.jsonl"
SCHEMA_PATH = ROOT / "workspace" / "schemas" / "agent-task-timeline.schema.json"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5MB
DEFAULT_BACKUP_COUNT = 3
DEFAULT_BUCKETS = [1000, 5000, 15000, 60000, 300000]


def normalize_buckets(buckets: Optional[List[int]]) -> List[int]:
    """Normalize histogram buckets for Prometheus output.

    - None → default buckets
    - Deduplicate + sort
    - Drop non-positive entries; if all are dropped, fall back to defaults
    """

    if not buckets:
        return DEFAULT_BUCKETS

    cleaned = sorted({b for b in buckets if b > 0})
    return cleaned or DEFAULT_BUCKETS


@dataclass
class TaskEvent:
    agent: str
    task_type: str
    task_ref: str
    run_id: str
    status: str
    started_at: datetime
    ended_at: datetime
    retry_count: int = 0
    metadata: Optional[Mapping[str, object]] = None
    schema_version: str = "1.0.0"
    recorded_at: Optional[datetime] = None

    @property
    def duration_ms(self) -> int:
        return math.floor((self.ended_at - self.started_at).total_seconds() * 1000)

    def to_dict(self) -> MutableMapping[str, object]:
        payload = asdict(self)
        payload["duration_ms"] = self.duration_ms
        payload["started_at"] = self.started_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload["ended_at"] = self.ended_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload["recorded_at"] = (
            (self.recorded_at or datetime.now(timezone.utc))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        return payload


class TaskTimelineLogger:
    def __init__(
        self,
        log_path: Path = DEFAULT_LOG_PATH,
        *,
        schema_path: Path = SCHEMA_PATH,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
    ) -> None:
        self.log_path = log_path
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with schema_path.open("r", encoding="utf-8") as fh:
            schema = json.load(fh)
        self.validator = Draft202012Validator(schema)

    def _validate(self, payload: Mapping[str, object]) -> None:
        errors = sorted(self.validator.iter_errors(payload), key=lambda e: e.path)
        if errors:
            messages = "; ".join(f"{'/'.join([str(p) for p in err.path])}: {err.message}" for err in errors)
            raise ValueError(f"Invalid task event: {messages}")

    def _rotate_if_needed(self, next_record_bytes: int) -> None:
        if self.max_bytes <= 0 or self.backup_count <= 0:
            return
        if not self.log_path.exists():
            return

        current_size = self.log_path.stat().st_size
        if current_size + next_record_bytes <= self.max_bytes:
            return

        for idx in range(self.backup_count - 1, 0, -1):
            src = self.log_path.with_name(f"{self.log_path.name}.{idx}")
            dst = self.log_path.with_name(f"{self.log_path.name}.{idx + 1}")
            if src.exists():
                dst.unlink(missing_ok=True)
                src.replace(dst)

        first_backup = self.log_path.with_name(f"{self.log_path.name}.1")
        first_backup.unlink(missing_ok=True)
        self.log_path.replace(first_backup)

    def log_event(self, event: TaskEvent) -> Mapping[str, object]:
        payload = event.to_dict()
        self._validate(payload)
        line = json.dumps(payload, separators=(",", ":"))
        encoded_length = len(line.encode("utf-8")) + 1  # +1 for newline
        self._rotate_if_needed(encoded_length)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return payload


@dataclass
class PrometheusMetrics:
    tasks: MutableMapping[tuple, int]
    retries: MutableMapping[tuple, int]
    durations: MutableMapping[tuple, List[int]]

    def render(self, buckets: Optional[List[int]] = None) -> str:
        buckets = normalize_buckets(buckets)
        lines: List[str] = []
        lines.append("# HELP agent_task_total Count of agent task runs by status and type")
        lines.append("# TYPE agent_task_total counter")
        for (agent, task_type, status), count in sorted(self.tasks.items()):
            lines.append(
                f"agent_task_total{{agent=\"{agent}\",task_type=\"{task_type}\",status=\"{status}\"}} {count}"
            )

        lines.append("# HELP agent_task_retry_total Total retries before each completed run")
        lines.append("# TYPE agent_task_retry_total counter")
        for (agent, task_type), total_retries in sorted(self.retries.items()):
            lines.append(f"agent_task_retry_total{{agent=\"{agent}\",task_type=\"{task_type}\"}} {total_retries}")

        lines.append("# HELP agent_task_duration_ms Duration of agent task runs")
        lines.append("# TYPE agent_task_duration_ms histogram")
        inf = float("inf")
        buckets_with_inf = buckets + [inf]
        for (agent, task_type), durations in sorted(self.durations.items()):
            counts = []
            for upper in buckets_with_inf:
                count = len([d for d in durations if d <= upper])
                bucket_label = "+Inf" if upper is inf else str(upper)
                counts.append((bucket_label, count))
            for bucket_label, count in counts:
                lines.append(
                    f"agent_task_duration_ms_bucket{{agent=\"{agent}\",task_type=\"{task_type}\",le=\"{bucket_label}\"}} {count}"
                )
            lines.append(
                f"agent_task_duration_ms_count{{agent=\"{agent}\",task_type=\"{task_type}\"}} {len(durations)}"
            )
            lines.append(
                f"agent_task_duration_ms_sum{{agent=\"{agent}\",task_type=\"{task_type}\"}} {sum(durations)}"
            )
        return "\n".join(lines) + "\n"


def load_events(jsonl_path: Path, schema_path: Path = SCHEMA_PATH) -> List[Mapping[str, object]]:
    validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))
    events: List[Mapping[str, object]] = []
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            payload = json.loads(line)
            errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)
            if errors:
                messages = "; ".join(f"{'/'.join([str(p) for p in err.path])}: {err.message}" for err in errors)
                raise ValueError(f"Invalid task event in {jsonl_path}: {messages}")
            events.append(payload)
    return events


def build_metrics(events: Iterable[Mapping[str, object]]) -> PrometheusMetrics:
    tasks: MutableMapping[tuple, int] = {}
    retries: MutableMapping[tuple, int] = {}
    durations: MutableMapping[tuple, List[int]] = {}

    for event in events:
        agent = str(event["agent"])
        task_type = str(event["task_type"])
        status = str(event["status"])
        retry_count = int(event["retry_count"])
        duration_ms = int(event["duration_ms"])

        tasks[(agent, task_type, status)] = tasks.get((agent, task_type, status), 0) + 1
        retries[(agent, task_type)] = retries.get((agent, task_type), 0) + retry_count
        durations.setdefault((agent, task_type), []).append(duration_ms)

    return PrometheusMetrics(tasks=tasks, retries=retries, durations=durations)


def jsonl_to_prometheus(jsonl_path: Path, buckets: Optional[List[int]] = None) -> str:
    events = load_events(jsonl_path)
    metrics = build_metrics(events)
    return metrics.render(buckets=buckets)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Agent task timeline utilities")
    parser.add_argument(
        "--file",
        dest="jsonl_path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to agent-task-events JSONL file (default: workspace/logs/agent-task-events.jsonl)",
    )
    parser.add_argument(
        "--bucket",
        action="append",
        dest="buckets",
        type=int,
        help="Optional histogram bucket upper bound in milliseconds (repeatable)",
    )
    args = parser.parse_args(argv)

    metrics_text = jsonl_to_prometheus(args.jsonl_path, buckets=args.buckets)
    print(metrics_text)
    return 0


__all__ = [
    "TaskEvent",
    "TaskTimelineLogger",
    "jsonl_to_prometheus",
    "build_metrics",
    "load_events",
    "PrometheusMetrics",
    "SCHEMA_PATH",
    "DEFAULT_LOG_PATH",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_BACKUP_COUNT",
    "DEFAULT_BUCKETS",
    "normalize_buckets",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
