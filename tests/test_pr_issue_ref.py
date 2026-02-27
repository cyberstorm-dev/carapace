import base64
import os

import pytest
import yaml

from carapace import pr_issue_ref


def test_accepts_fixes_reference():
    body = "Add feature\n\nFixes #45"
    assert pr_issue_ref.has_issue_reference(body)


def test_accepts_closes_reference_with_text():
    body = "Implements guard. Closes #123 and adds docs."
    assert pr_issue_ref.has_issue_reference(body)


def test_rejects_missing_reference():
    body = "Implements guard without linking issue"
    assert not pr_issue_ref.has_issue_reference(body)


def test_rejects_placeholder_reference():
    body = "Fixes #"
    assert not pr_issue_ref.has_issue_reference(body)


def test_get_auth_prefers_gitea_token(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "token-1")
    monkeypatch.setenv("CI_NETRC_PASSWORD", "token-2")

    headers, source = pr_issue_ref.get_auth_headers()

    assert headers == {"Authorization": "token token-1"}
    assert source == "GITEA_TOKEN"


def test_get_auth_falls_back_to_netrc_token(monkeypatch):
    monkeypatch.delenv("GITEA_TOKEN", raising=False)
    monkeypatch.delenv("GITEA_PAT", raising=False)
    monkeypatch.setenv("CI_NETRC_PASSWORD", "netrc-token")

    headers, source = pr_issue_ref.get_auth_headers()

    assert headers == {"Authorization": "token netrc-token"}
    assert source == "CI_NETRC_PASSWORD"


def test_get_auth_uses_basic_with_netrc_creds(monkeypatch):
    monkeypatch.delenv("GITEA_TOKEN", raising=False)
    monkeypatch.delenv("GITEA_PAT", raising=False)
    monkeypatch.delenv("CI_TOKEN", raising=False)
    monkeypatch.delenv("CI_NETRC_PASSWORD", raising=False)
    monkeypatch.setenv("CI_NETRC_USERNAME", "woodpecker")
    monkeypatch.setenv("CI_NETRC_PASSWORD", "password123")

    headers, source = pr_issue_ref.get_auth_headers()

    encoded = base64.b64encode(b"woodpecker:password123").decode()
    assert headers == {"Authorization": f"Basic {encoded}"}
    assert source == "CI_NETRC_USERNAME/CI_NETRC_PASSWORD"


def test_get_auth_returns_none_when_auth_missing(monkeypatch):
    for env in ("GITEA_TOKEN", "GITEA_PAT", "CI_TOKEN", "CI_NETRC_USERNAME", "CI_NETRC_PASSWORD"):
        monkeypatch.delenv(env, raising=False)

    headers, source = pr_issue_ref.get_auth_headers()

    assert headers is None
    assert source is None


def test_resolves_repo_from_ci_repo(monkeypatch):
    monkeypatch.delenv("CI_REPO_OWNER", raising=False)
    monkeypatch.delenv("CI_REPO_NAME", raising=False)
    monkeypatch.setenv("CI_REPO", "openclaw/nisto-home")

    owner, repo = pr_issue_ref.resolve_repo()

    assert owner == "openclaw"
    assert repo == "nisto-home"


def test_resolves_pr_number_with_fallback(monkeypatch):
    monkeypatch.delenv("CI_COMMIT_PULL_REQUEST", raising=False)
    monkeypatch.setenv("CI_PULL_REQUEST", "123")

    assert pr_issue_ref.resolve_pr_number() == "123"


def test_main_returns_failure_when_auth_missing(monkeypatch, capsys):
    monkeypatch.setenv("CI_COMMIT_PULL_REQUEST", "50")
    monkeypatch.setenv("CI_REPO_OWNER", "openclaw")
    monkeypatch.setenv("CI_REPO_NAME", "nisto-home")
    for env in ("GITEA_TOKEN", "GITEA_PAT", "CI_NETRC_PASSWORD", "CI_TOKEN", "CI_NETRC_USERNAME"):
        monkeypatch.delenv(env, raising=False)

    exit_code = pr_issue_ref.main()

    out = capsys.readouterr().out
    payload = yaml.safe_load(out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "MISSING_ENV"


def test_main_passes_with_netrc_token(monkeypatch, capsys):
    monkeypatch.setenv("CI_COMMIT_PULL_REQUEST", "50")
    monkeypatch.setenv("CI_REPO_OWNER", "openclaw")
    monkeypatch.setenv("CI_REPO_NAME", "nisto-home")
    monkeypatch.setenv("CI_NETRC_PASSWORD", "netrc-token")
    monkeypatch.setattr(pr_issue_ref, "fetch_pr_body", lambda *args, **kwargs: "Closes #1")

    exit_code = pr_issue_ref.main()

    out = capsys.readouterr().out
    payload = yaml.safe_load(out)
    assert exit_code == 0
    assert payload["ok"] is True
