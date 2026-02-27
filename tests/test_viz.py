import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from carapace.validator import viz


@pytest.fixture
def sample_config(tmp_path):
    cfg = tmp_path / "validator.yaml"
    cfg.write_text(
        """
phase: 3
labels:
  tan: tan
  molt: molt
  needs_pr: needs-pr
exempt_issues: []
"""
    )
    return cfg


def test_viz_layers_and_orphans(monkeypatch, sample_config):
    issues = [
        {
            "number": 1,
            "title": "Tan",
            "labels": [{"name": "tan"}],
            "assignee": {"login": "reviewer"},
            "milestone": {"id": 3},
            "state": "open",
        },
        {
            "number": 2,
            "title": "Work",
            "labels": [],
            "assignee": {"login": "builder"},
            "dependencies": [1],
            "milestone": {"id": 3},
            "state": "open",
        },
        {
            "number": 3,
            "title": "Molt",
            "labels": [{"name": "molt"}],
            "assignee": {"login": "nisto"},
            "dependencies": [2],
            "milestone": {"id": 3},
            "state": "open",
        },
        {
            "number": 4,
            "title": "Orphan",
            "labels": [],
            "assignee": {"login": "builder"},
            "dependencies": [],
            "milestone": {"id": 3},
            "state": "open",
        },
    ]
    deps = {i["number"]: i.get("dependencies", []) for i in issues}

    monkeypatch.setattr(viz, "fetch_all_issues", lambda *args, **kwargs: issues)
    monkeypatch.setattr(viz, "_fetch_dependencies", lambda *args, **kwargs: deps[kwargs.get("issue_number") or args[-1]])
    monkeypatch.setattr(viz, "_fetch_pulls", lambda *args, **kwargs: [])
    monkeypatch.setattr(viz, "fetch_open_pulls", lambda *args, **kwargs: [])

    output = viz.viz_phase(phase=3, gitea_url="http://example", repo="o/r", token="t", config_path=str(sample_config))
    lines = output.splitlines()

    assert "── SOURCE (tan) ──" in output
    assert "── SINK (molt) ──" in output
    assert "── UNREACHABLE/ORPHAN ──" in output

    source_idx = lines.index("── SOURCE (tan) ──")
    sink_idx = lines.index("── SINK (molt) ──")
    orphan_idx = lines.index("── UNREACHABLE/ORPHAN ──")

    assert source_idx < sink_idx < orphan_idx

    sink_block = lines[sink_idx:orphan_idx]
    assert any("#3" in line for line in sink_block)
    assert all("#4" not in line for line in sink_block)

    orphan_block = lines[orphan_idx:]
    assert any("#4" in line for line in orphan_block)

    positions = {num: next(i for i, line in enumerate(lines) if f"#{num} " in line) for num in (1, 2, 3)}
    assert positions[1] < positions[2] < positions[3]


def test_prs_render_sorted(monkeypatch, sample_config):
    issues = [
        {
            "number": 1,
            "title": "Tan",
            "labels": [{"name": "tan"}],
            "assignee": {"login": "reviewer"},
            "milestone": {"id": 3},
            "state": "open",
        },
        {
            "number": 2,
            "title": "Work",
            "labels": [],
            "assignee": {"login": "builder"},
            "dependencies": [1],
            "milestone": {"id": 3},
            "state": "open",
        },
    ]
    deps = {i["number"]: i.get("dependencies", []) for i in issues}

    closing_prs = [
        {"number": 12, "title": "Later", "body": "Closes #2", "state": "open", "merged": False},
        {"number": 5, "title": "Earlier", "body": "Closes #2", "state": "merged", "merged": True},
    ]
    open_prs = [
        {"number": 9, "title": "Second", "head": {"ref": "b"}, "base": {"ref": "dev"}, "user": {"login": "u"}},
        {"number": 3, "title": "First", "head": {"ref": "a"}, "base": {"ref": "dev"}, "user": {"login": "u"}},
    ]

    monkeypatch.setattr(viz, "fetch_all_issues", lambda *args, **kwargs: issues)
    monkeypatch.setattr(viz, "_fetch_dependencies", lambda *args, **kwargs: deps[kwargs.get("issue_number") or args[-1]])
    monkeypatch.setattr(viz, "_fetch_pulls", lambda *args, **kwargs: closing_prs)
    monkeypatch.setattr(viz, "fetch_open_pulls", lambda *args, **kwargs: open_prs)

    output = viz.viz_phase(phase=3, gitea_url="http://example", repo="o/r", token="t", config_path=str(sample_config))
    lines = output.splitlines()

    pr5_idx = next(i for i, line in enumerate(lines) if "📎 PR #5" in line)
    pr12_idx = next(i for i, line in enumerate(lines) if "📎 PR #12" in line)
    assert pr5_idx < pr12_idx

    open_section = lines.index("── OPEN PRs ──")
    open_numbers = [int(line.split()[1][1:]) for line in lines[open_section + 1 :] if line.strip().startswith("PR #")]
    assert open_numbers[:2] == [3, 9]

def test_mermaid_output(monkeypatch, sample_config):
    issues = [
        {
            "number": 1,
            "title": "Tan",
            "labels": [{"name": "tan"}],
            "assignee": {"login": "reviewer"},
            "milestone": {"id": 3},
            "state": "open",
        },
        {
            "number": 2,
            "title": "Work",
            "labels": [],
            "assignee": {"login": "builder"},
            "dependencies": [1],
            "milestone": {"id": 3},
            "state": "closed",
        },
        {
            "number": 3,
            "title": "Molt",
            "labels": [{"name": "molt"}],
            "assignee": {"login": "nisto"},
            "dependencies": [2],
            "milestone": {"id": 3},
            "state": "open",
        },
    ]
    deps = {i["number"]: i.get("dependencies", []) for i in issues}

    monkeypatch.setattr(viz, "fetch_all_issues", lambda *args, **kwargs: issues)
    monkeypatch.setattr(viz, "_fetch_dependencies", lambda *args, **kwargs: deps[kwargs.get("issue_number") or args[-1]])
    monkeypatch.setattr(viz, "_fetch_pulls", lambda *args, **kwargs: [])
    monkeypatch.setattr(viz, "fetch_open_pulls", lambda *args, **kwargs: [])

    output = viz.viz_phase(
        phase=3,
        gitea_url="http://example",
        repo="o/r",
        token="t",
        config_path=str(sample_config),
        output_format="mermaid",
    )

    assert output.startswith("%% Phase 3")
    assert "graph LR" in output
    assert "classDef tan fill:#f59e0b" in output
    assert "classDef molt fill:#3b82f6" in output

    lines = output.splitlines()
    assert any("i1 --> i2" in line for line in lines)
    assert any("i2 --> i3" in line for line in lines)
    assert any('class i2 closed' in line for line in lines)
    assert any('class i3 molt' in line for line in lines)
    assert any('class i1 tan' in line for line in lines)
