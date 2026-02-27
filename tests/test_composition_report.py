"""Tests for carapace.composition_report."""

from __future__ import annotations

import json

import pytest

from carapace.composition_report import (
    AgentContribution,
    ComposedDeliverable,
    find_composed_prs,
    render_json,
    render_markdown,
)


# -- Unit tests for dataclasses --


def test_composed_deliverable_is_composed():
    d = ComposedDeliverable(pr_number=1, title="Test", url="http://x")
    d.agents = [
        AgentContribution(agent="builder", role="author", details="opened"),
        AgentContribution(agent="reviewer", role="reviewer", details="approved"),
    ]
    assert d.is_composed is True
    assert d.agent_names == ["builder", "reviewer"]


def test_single_agent_not_composed():
    d = ComposedDeliverable(pr_number=2, title="Solo", url="http://x")
    d.agents = [
        AgentContribution(agent="builder", role="author", details="opened"),
    ]
    assert d.is_composed is False


# -- Render tests --


def test_render_markdown_empty():
    md = render_markdown([])
    assert "No composed deliverables found" in md


def test_render_markdown_with_data():
    d = ComposedDeliverable(
        pr_number=10,
        title="Feature X",
        url="http://example.com/pr/10",
        composition_type="code+review",
    )
    d.agents = [
        AgentContribution(agent="builder", role="author", details="Opened PR #10"),
        AgentContribution(agent="reviewer", role="reviewer", details="APPROVED on PR #10"),
    ]
    md = render_markdown([d])
    assert "# Cross-Agent Output Composition Report" in md
    assert "builder, reviewer" in md
    assert "#10" in md
    assert "code+review" in md


def test_render_json():
    d = ComposedDeliverable(
        pr_number=5,
        title="Infra change",
        url="http://example.com/pr/5",
        composition_type="orchestrated",
    )
    d.agents = [
        AgentContribution(agent="builder", role="author", details="code"),
        AgentContribution(agent="cloudops", role="merger", details="merged"),
    ]
    output = json.loads(render_json([d]))
    assert output["count"] == 1
    assert output["composed_deliverables"][0]["agent_names"] == ["builder", "cloudops"]


# -- Integration test with mock fetcher --


def _make_mock_fetcher(pulls, reviews_by_pr):
    """Return a fetcher that returns canned data."""

    def fetcher(url: str):
        if "/pulls?" in url:
            return pulls
        for pr_num, reviews in reviews_by_pr.items():
            if f"/pulls/{pr_num}/reviews" in url:
                return reviews
        return []

    return fetcher


def test_find_composed_prs_detects_composition():
    pulls = [
        {
            "number": 42,
            "title": "Add widget",
            "html_url": "http://git/pr/42",
            "merged": True,
            "user": {"login": "builder"},
            "created_at": "2026-01-01T00:00:00Z",
            "merged_by": {"login": "allenday"},
        }
    ]
    reviews = {
        42: [
            {
                "user": {"login": "reviewer"},
                "state": "APPROVED",
                "submitted_at": "2026-01-02T00:00:00Z",
            }
        ]
    }
    fetcher = _make_mock_fetcher(pulls, reviews)
    result = find_composed_prs(
        repo="test/repo",
        base_url="http://fake",
        token="x",
        fetcher=fetcher,
    )
    assert len(result) == 1
    d = result[0]
    assert d.is_composed
    assert "builder" in d.agent_names
    assert "reviewer" in d.agent_names


def test_find_composed_prs_skips_unmerged():
    pulls = [
        {
            "number": 99,
            "title": "Draft",
            "html_url": "http://git/pr/99",
            "merged": False,
            "user": {"login": "builder"},
            "created_at": "2026-01-01T00:00:00Z",
            "merged_by": None,
        }
    ]
    fetcher = _make_mock_fetcher(pulls, {})
    result = find_composed_prs(
        repo="test/repo",
        base_url="http://fake",
        token="x",
        fetcher=fetcher,
    )
    assert len(result) == 0


def test_find_composed_prs_single_agent_excluded():
    pulls = [
        {
            "number": 50,
            "title": "Solo work",
            "html_url": "http://git/pr/50",
            "merged": True,
            "user": {"login": "builder"},
            "created_at": "2026-01-01T00:00:00Z",
            "merged_by": {"login": "builder"},
        }
    ]
    reviews = {50: []}
    fetcher = _make_mock_fetcher(pulls, reviews)
    result = find_composed_prs(
        repo="test/repo",
        base_url="http://fake",
        token="x",
        fetcher=fetcher,
    )
    assert len(result) == 0
