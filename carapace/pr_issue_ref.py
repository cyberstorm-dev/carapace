"""PR issue reference checker with YAML HATEOAS output."""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from typing import Dict, Optional, Tuple
from urllib import error, request

from .hateoas import dump_yaml, envelope

ISSUE_REF_PATTERN = re.compile(r"\b(Fixes|Closes)\s+#\d+", re.IGNORECASE)
DEFAULT_GITEA_URL = "http://100.73.228.90:3000"


def get_auth_headers() -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Return auth headers and the source env used."""
    username = os.environ.get("CI_NETRC_USERNAME")
    password = os.environ.get("CI_NETRC_PASSWORD")

    for name in ("GITEA_TOKEN", "GITEA_PAT", "CI_TOKEN"):
        token = os.environ.get(name)
        if token:
            return {"Authorization": f"token {token}"}, name

    if username and password:
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {creds}"}, "CI_NETRC_USERNAME/CI_NETRC_PASSWORD"

    if password:
        return {"Authorization": f"token {password}"}, "CI_NETRC_PASSWORD"

    return None, None


def has_issue_reference(body: str) -> bool:
    """Return True if body contains a Fixes/Closes issue reference."""
    if not body:
        return False
    return bool(ISSUE_REF_PATTERN.search(body))


def fetch_pr_body(gitea_url: str, headers: Dict[str, str], owner: str, repo: str, pr_number: str) -> str:
    api_url = f"{gitea_url.rstrip('/')}/api/v1/repos/{owner}/{repo}/pulls/{pr_number}"
    req = request.Request(api_url, headers=headers)
    try:
        with request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data.get("body", "")
    except error.HTTPError as exc:  # pragma: no cover - network failure path
        print(f"Failed to fetch PR #{pr_number} body: {exc}", file=sys.stderr)
        raise


def resolve_repo() -> Tuple[Optional[str], Optional[str]]:
    owner = os.environ.get("CI_REPO_OWNER")
    repo = os.environ.get("CI_REPO_NAME")

    if owner and repo:
        return owner, repo

    ci_repo = os.environ.get("CI_REPO")
    if ci_repo and "/" in ci_repo:
        parts = ci_repo.split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1]

    return owner, repo


def resolve_pr_number() -> Optional[str]:
    return (
        os.environ.get("CI_COMMIT_PULL_REQUEST")
        or os.environ.get("CI_PULL_REQUEST")
        or os.environ.get("CI_PR_NUMBER")
    )


def run_check(command: str) -> Tuple[int, Dict[str, object]]:
    pr_number = resolve_pr_number()
    owner, repo = resolve_repo()
    headers, auth_source = get_auth_headers()
    gitea_url = os.environ.get("GITEA_URL", DEFAULT_GITEA_URL)

    missing = [name for name, val in {"CI_REPO_OWNER": owner, "CI_REPO_NAME": repo}.items() if not val]
    if not headers:
        missing.append("GITEA_TOKEN or CI_NETRC_USERNAME/CI_NETRC_PASSWORD")
    if not pr_number:
        missing.append("CI_COMMIT_PULL_REQUEST")

    if missing:
        payload = envelope(
            command=command,
            ok=False,
            error={"message": f"Missing required environment variables: {', '.join(missing)}", "code": "MISSING_ENV"},
            fix="Ensure Woodpecker exports CI_COMMIT_PULL_REQUEST, CI_REPO_OWNER, CI_REPO_NAME, and a token (GITEA_TOKEN or CI_NETRC_USERNAME/CI_NETRC_PASSWORD)",
            next_actions=[
                {
                    "command": "export CI_COMMIT_PULL_REQUEST=<id> CI_REPO_OWNER=<owner> CI_REPO_NAME=<repo> GITEA_TOKEN=<token>",
                    "description": "Provide CI context and retry",
                }
            ],
        )
        return 1, payload

    try:
        body = fetch_pr_body(gitea_url, headers, owner, repo, pr_number)
    except error.HTTPError:
        payload = envelope(
            command=command,
            ok=False,
            error={"message": "Failed to fetch PR body", "code": "FETCH_FAILED"},
            fix="Verify token permissions and PR existence",
            next_actions=[{"command": command, "description": "Retry once connectivity is restored"}],
        )
        return 1, payload

    if not has_issue_reference(body):
        payload = envelope(
            command=command,
            ok=False,
            error={"message": "PR description is missing required issue reference (Fixes/Closes #<issue>).", "code": "MISSING_REFERENCE"},
            fix="Edit the PR description to include `Closes #<issue>`.",
            next_actions=[{"command": command, "description": "Re-run check after updating PR body"}],
        )
        return 1, payload

    payload = envelope(
        command=command,
        ok=True,
        result={
            "pr": int(pr_number),
            "repo": f"{owner}/{repo}",
            "auth_source": auth_source,
            "message": "Issue reference found in PR description.",
        },
        next_actions=[
            {"command": "carapace", "description": "List available carapace commands"},
        ],
    )
    return 0, payload


def main() -> int:
    command = "carapace-pr-issue-ref"
    exit_code, payload = run_check(command)
    print(dump_yaml(payload))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
