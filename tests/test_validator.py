import json
import os
import sys
from pathlib import Path

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from carapace.validator import cli
from carapace.validator import config as config_module
from carapace.validator import validation


@pytest.fixture
def sample_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "validator.yaml"
    cfg.write_text(
        """
phase: 3
labels:
  molt: molt
  tan: tan
  needs_pr: needs-pr
exempt_issues: []
"""
    )
    return cfg


@pytest.fixture
def valid_issues() -> list[dict]:
    return [
        {
            "number": 1,
            "labels": [{"name": "tan"}],
            "assignee": {"login": "reviewer"},
            "milestone": {"id": 3},
        },
        {
            "number": 2,
            "labels": [{"name": "needs-pr"}],
            "assignee": {"login": "builder"},
            "dependencies": [1],
            "milestone": {"id": 3},
        },
        {
            "number": 3,
            "labels": [{"name": "molt"}],
            "assignee": {"login": "nisto"},
            "dependencies": [2],
            "milestone": {"id": 3},
        },
    ]


def _messages_by_tier(messages: list[validation.ValidationMessage], tier: str) -> list[str]:
    return [m.message for m in messages if m.tier == tier]


def test_load_config_defaults(sample_config: Path):
    cfg = config_module.load_config(str(sample_config))
    assert cfg.phase == 3
    assert cfg.labels["needs_pr"] == "needs-pr"
    assert cfg.exempt_issues == []
    assert cfg.check_tiers == {}


def test_validate_success(valid_issues: list[dict], sample_config: Path):
    cfg = config_module.load_config(str(sample_config))
    messages = validation.validate_issues(valid_issues, cfg)
    assert _messages_by_tier(messages, validation.TIER_HARD) == []


def test_allows_missing_assignee(valid_issues: list[dict], sample_config: Path):
    """Unassigned issues are valid — heartbeat parceler assigns them to builder."""
    cfg = config_module.load_config(str(sample_config))
    valid_issues[1]["assignee"] = None
    messages = validation.validate_issues(valid_issues, cfg)
    advisories = _messages_by_tier(messages, validation.TIER_ADVISORY)
    assert any("missing an assignee" in msg for msg in advisories)
    assert not _messages_by_tier(messages, validation.TIER_HARD)


def test_flags_missing_parent_dependency(valid_issues: list[dict], sample_config: Path):
    cfg = config_module.load_config(str(sample_config))
    valid_issues[1]["labels"] = []
    valid_issues[1]["dependencies"] = []
    messages = validation.validate_issues(valid_issues, cfg)
    errors = _messages_by_tier(messages, validation.TIER_HARD)
    assert "Issue #2 has no dependency/parent" in " ".join(errors)


def test_ignores_out_of_scope_dependencies(valid_issues: list[dict], sample_config: Path):
    cfg = config_module.load_config(str(sample_config))
    valid_issues[1]["dependencies"].append(99)
    messages = validation.validate_issues(valid_issues, cfg)
    errors = _messages_by_tier(messages, validation.TIER_HARD)
    assert errors == []


def test_flags_unwired_issue(valid_issues: list[dict], sample_config: Path):
    cfg = config_module.load_config(str(sample_config))
    valid_issues.append({"number": 4, "labels": [], "assignee": {"login": "builder"}, "milestone": {"id": 3}})
    messages = validation.validate_issues(valid_issues, cfg)
    joined = " ".join(_messages_by_tier(messages, validation.TIER_HARD))
    assert "Issue #4 is not depended on" in joined
    assert "Issue #4 does not reach a molt-labeled issue" in joined


def test_cli_with_fixture_file(tmp_path: Path, valid_issues: list[dict], sample_config: Path):
    issues_file = tmp_path / "issues.json"
    issues_file.write_text(json.dumps(valid_issues))
    exit_code = cli.main(["--config", str(sample_config), "--issues-file", str(issues_file)])
    assert exit_code == 0


def test_cli_json_output_on_failure(capsys, tmp_path: Path, valid_issues: list[dict], sample_config: Path):
    issues_file = tmp_path / "issues.json"
    valid_issues[1]["dependencies"] = []  # trigger missing parent dependency error
    issues_file.write_text(json.dumps(valid_issues))

    exit_code = cli.main(
        [
            "--config",
            str(sample_config),
            "--issues-file",
            str(issues_file),
            "--output",
            "json",
        ]
    )

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["hard"]


def test_cli_json_output_on_success(capsys, tmp_path: Path, valid_issues: list[dict], sample_config: Path):
    issues_file = tmp_path / "issues.json"
    issues_file.write_text(json.dumps(valid_issues))

    exit_code = cli.main(
        [
            "--config",
            str(sample_config),
            "--issues-file",
            str(issues_file),
            "--output",
            "json",
        ]
    )

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert exit_code == 0
    assert payload == {
        "status": "passed",
        "level": "hard",
        "hard": [],
        "advisory": [],
        "info": [],
        "visible": [],
    }


def test_requires_reachability_from_tan(valid_issues: list[dict], sample_config: Path):
    cfg = config_module.load_config(str(sample_config))
    valid_issues.append(
        {
            "number": 4,
            "labels": [],
            "assignee": {"login": "builder"},
            "dependencies": [99],
            "milestone": {"id": 3},
        }
    )
    messages = validation.validate_issues(valid_issues, cfg)
    advisories = _messages_by_tier(messages, validation.TIER_ADVISORY)
    assert any("Issue #4 is not reachable from a tan-labeled issue" in msg for msg in advisories)


def test_requires_path_to_molt(valid_issues: list[dict], sample_config: Path):
    cfg = config_module.load_config(str(sample_config))
    valid_issues.append(
        {
            "number": 4,
            "labels": [],
            "assignee": {"login": "builder"},
            "dependencies": [2],
            "milestone": {"id": 3},
        }
    )
    messages = validation.validate_issues(valid_issues, cfg)
    errors = _messages_by_tier(messages, validation.TIER_HARD)
    assert "Issue #4 does not reach a molt-labeled issue" in " ".join(errors)


def test_needs_pr_still_must_connect_to_molt(valid_issues: list[dict], sample_config: Path):
    cfg = config_module.load_config(str(sample_config))
    valid_issues.append(
        {
            "number": 4,
            "labels": [{"name": "needs-pr"}],
            "assignee": {"login": "builder"},
            "dependencies": [2],
            "milestone": {"id": 3},
        }
    )
    messages = validation.validate_issues(valid_issues, cfg)
    errors = _messages_by_tier(messages, validation.TIER_HARD)
    assert "Issue #4 does not reach a molt-labeled issue" in " ".join(errors)


def test_cli_filters_visible_by_level(capsys, tmp_path: Path, valid_issues: list[dict], sample_config: Path):
    issues_file = tmp_path / "issues.json"
    valid_issues[1]["assignee"] = None  # advisory
    issues_file.write_text(json.dumps(valid_issues))

    cli.main(
        [
            "--config",
            str(sample_config),
            "--issues-file",
            str(issues_file),
            "--output",
            "json",
            "--level",
            "advisory",
        ]
    )
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["status"] == "passed"
    assert payload["advisory"]  # visible at advisory

    cli.main(
        [
            "--config",
            str(sample_config),
            "--issues-file",
            str(issues_file),
            "--output",
            "json",
            "--level",
            "hard",
        ]
    )
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["status"] == "passed"
    assert payload["visible"] == []  # advisory hidden at hard level
