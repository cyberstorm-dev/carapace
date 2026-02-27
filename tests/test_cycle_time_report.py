"""Tests for carapace.cycle_time_report — automated cycle-time reporting."""

from datetime import datetime, timezone

import pytest

from carapace.cycle_time_report import (
    IssueTimeline,
    build_report,
    format_markdown_report,
    _fmt_duration,
    _parse_dt,
)


def _dt(s: str) -> datetime:
    return _parse_dt(s)


class TestIssueTimeline:
    def test_complete_timeline_computes_durations(self):
        tl = IssueTimeline(
            issue_number=101,
            issue_title="Test issue",
            created_at=_dt("2026-02-24T09:00:00Z"),
            pr_number=111,
            pr_created_at=_dt("2026-02-24T11:30:00Z"),
            pr_merged_at=_dt("2026-02-24T12:15:00Z"),
        )
        assert tl.is_complete is True
        assert tl.time_to_decompose_minutes == 150  # 2h30m
        assert tl.time_to_merge_minutes == 45
        assert tl.total_cycle_minutes == 195  # 3h15m

    def test_incomplete_timeline(self):
        tl = IssueTimeline(
            issue_number=102,
            issue_title="WIP",
            created_at=_dt("2026-02-24T09:00:00Z"),
        )
        assert tl.is_complete is False
        assert tl.time_to_decompose_minutes is None
        assert tl.time_to_merge_minutes is None
        assert tl.total_cycle_minutes is None

    def test_pr_opened_but_not_merged(self):
        tl = IssueTimeline(
            issue_number=103,
            issue_title="Open PR",
            created_at=_dt("2026-02-24T09:00:00Z"),
            pr_number=112,
            pr_created_at=_dt("2026-02-24T10:00:00Z"),
        )
        assert tl.is_complete is False
        assert tl.time_to_decompose_minutes == 60
        assert tl.time_to_merge_minutes is None


class TestBuildReport:
    def test_report_with_completed_entries(self):
        timelines = [
            IssueTimeline(
                issue_number=101,
                issue_title="Task A",
                created_at=_dt("2026-02-24T09:00:00Z"),
                pr_number=111,
                pr_created_at=_dt("2026-02-24T11:00:00Z"),
                pr_merged_at=_dt("2026-02-24T12:00:00Z"),
            ),
            IssueTimeline(
                issue_number=102,
                issue_title="Task B",
                created_at=_dt("2026-02-24T09:00:00Z"),
                pr_number=112,
                pr_created_at=_dt("2026-02-24T10:00:00Z"),
                pr_merged_at=_dt("2026-02-24T11:00:00Z"),
            ),
            IssueTimeline(
                issue_number=103,
                issue_title="Task C",
                created_at=_dt("2026-02-24T09:00:00Z"),
                pr_number=113,
                pr_created_at=_dt("2026-02-24T12:00:00Z"),
                pr_merged_at=_dt("2026-02-24T13:00:00Z"),
            ),
        ]
        report = build_report(timelines)
        assert report["summary"]["total_issues"] == 3
        assert report["summary"]["completed"] == 3
        assert report["summary"]["avg_time_to_decompose_minutes"] == 120.0  # (120+60+180)/3
        assert report["summary"]["avg_time_to_merge_minutes"] == 60.0
        assert report["summary"]["avg_total_cycle_minutes"] == 180.0  # (180+120+240)/3

    def test_report_with_no_complete_entries(self):
        timelines = [
            IssueTimeline(
                issue_number=101,
                issue_title="WIP",
                created_at=_dt("2026-02-24T09:00:00Z"),
            ),
        ]
        report = build_report(timelines)
        assert report["summary"]["completed"] == 0
        assert report["summary"]["avg_total_cycle_minutes"] == 0

    def test_empty_report(self):
        report = build_report([])
        assert report["summary"]["total_issues"] == 0


class TestFormatMarkdownReport:
    def test_markdown_contains_table_and_summary(self):
        timelines = [
            IssueTimeline(
                issue_number=101,
                issue_title="Task A",
                created_at=_dt("2026-02-24T09:00:00Z"),
                pr_number=111,
                pr_created_at=_dt("2026-02-24T11:00:00Z"),
                pr_merged_at=_dt("2026-02-24T12:00:00Z"),
                assignee="builder",
            ),
        ]
        report = build_report(timelines)
        md = format_markdown_report(report)
        assert "# Cycle Time Report" in md
        assert "#101" in md
        assert "builder" in md
        assert "## Summary" in md
        assert "## Analysis" in md


class TestFmtDuration:
    def test_minutes_only(self):
        assert _fmt_duration(45) == "45m"

    def test_hours_and_minutes(self):
        assert _fmt_duration(125) == "2h 05m"

    def test_none(self):
        assert _fmt_duration(None) == "—"

    def test_zero(self):
        assert _fmt_duration(0) == "0m"
