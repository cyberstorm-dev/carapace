import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from carapace.task_timeline import (
    SCHEMA_PATH,
    TaskEvent,
    TaskTimelineLogger,
    build_metrics,
    jsonl_to_prometheus,
    load_events,
    main,
)


@pytest.fixture
def temp_log(tmp_path: Path) -> Path:
    return tmp_path / "agent-task-events.jsonl"


@pytest.fixture
def sample_event() -> TaskEvent:
    return TaskEvent(
        agent="builder",
        task_type="issue",
        task_ref="#103",
        run_id="run-abc",
        status="success",
        started_at=datetime(2026, 2, 24, 11, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 2, 24, 11, 20, tzinfo=timezone.utc),
        retry_count=1,
        metadata={"branch": "builder/issue-103"},
        recorded_at=datetime(2026, 2, 24, 11, 20, tzinfo=timezone.utc),
    )


def test_logger_writes_valid_jsonl(temp_log: Path, sample_event: TaskEvent):
    logger = TaskTimelineLogger(temp_log, schema_path=SCHEMA_PATH)

    logged = logger.log_event(sample_event)

    stored = load_events(temp_log)[0]
    assert stored == logged
    assert stored["duration_ms"] == 20 * 60 * 1000
    assert stored["recorded_at"].endswith("Z")


def test_rotates_logs_when_max_bytes_exceeded(temp_log: Path, sample_event: TaskEvent):
    baseline_line = json.dumps(sample_event.to_dict(), separators=(",", ":")) + "\n"
    max_bytes = len(baseline_line.encode("utf-8")) + 5
    logger = TaskTimelineLogger(
        temp_log,
        schema_path=SCHEMA_PATH,
        max_bytes=max_bytes,
        backup_count=3,
    )

    for _ in range(3):
        logger.log_event(sample_event)

    first_backup = temp_log.with_name(temp_log.name + ".1")
    second_backup = temp_log.with_name(temp_log.name + ".2")

    assert temp_log.exists()
    assert first_backup.exists()
    assert second_backup.exists()
    assert not temp_log.with_name(temp_log.name + ".3").exists()

    assert sum(1 for _ in temp_log.open(encoding="utf-8")) == 1
    assert sum(1 for _ in first_backup.open(encoding="utf-8")) == 1


def test_jsonl_to_prometheus_aggregates_sample(tmp_path: Path):
    sample = Path("workspace/logs/examples/agent-task-events.sample.jsonl")
    # copy to tmp to ensure relative paths work regardless of cwd
    temp_copy = tmp_path / sample.name
    temp_copy.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")

    metrics_text = jsonl_to_prometheus(temp_copy, buckets=[10000, 2000000])
    lines = {line for line in metrics_text.strip().split("\n") if not line.startswith("#")}

    assert "agent_task_total{agent=\"builder\",task_type=\"issue\",status=\"success\"} 1" in lines
    assert "agent_task_total{agent=\"builder\",task_type=\"pull_request\",status=\"failed\"} 1" in lines
    assert "agent_task_total{agent=\"reviewer\",task_type=\"pull_request\",status=\"success\"} 1" in lines

    assert "agent_task_retry_total{agent=\"builder\",task_type=\"pull_request\"} 1" in lines
    assert "agent_task_retry_total{agent=\"builder\",task_type=\"issue\"} 0" in lines

    # bucket checks for builder/issue durations
    assert (
        "agent_task_duration_ms_bucket{agent=\"builder\",task_type=\"issue\",le=\"10000\"} 0"
        in lines
    )
    assert (
        "agent_task_duration_ms_bucket{agent=\"builder\",task_type=\"issue\",le=\"2000000\"} 1"
        in lines
    )

    # histogram summary lines
    assert "agent_task_duration_ms_count{agent=\"builder\",task_type=\"issue\"} 1" in lines
    assert "agent_task_duration_ms_sum{agent=\"builder\",task_type=\"issue\"} 1515000" in lines


def test_task_timeline_cli_outputs_metrics(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    sample = Path("workspace/logs/examples/agent-task-events.sample.jsonl")
    temp_copy = tmp_path / sample.name
    temp_copy.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")

    exit_code = main(["--file", str(temp_copy), "--bucket", "10000", "--bucket", "2000000"])

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert "agent_task_total" in captured
    assert "agent_task_duration_ms_bucket" in captured


def test_build_metrics_rejects_invalid(tmp_path: Path):
    bad_file = tmp_path / "bad.jsonl"
    bad_file.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_events(bad_file, schema_path=SCHEMA_PATH)


def test_bucket_normalization_sorts_and_dedupes(sample_event: TaskEvent):
    metrics = build_metrics([sample_event.to_dict()])

    output = metrics.render(buckets=[5000, -10, 1000, 5000])
    bucket_lines = [line for line in output.splitlines() if "agent_task_duration_ms_bucket" in line]

    assert any('le="1000"' in line for line in bucket_lines)
    assert any('le="5000"' in line for line in bucket_lines)
    assert not any('le="-10"' in line for line in bucket_lines)

    le_1000_idx = next(idx for idx, line in enumerate(bucket_lines) if 'le="1000"' in line)
    le_5000_idx = next(idx for idx, line in enumerate(bucket_lines) if 'le="5000"' in line)
    assert le_1000_idx < le_5000_idx
