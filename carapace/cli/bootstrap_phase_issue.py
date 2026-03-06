"""Phase issue bootstrapper for Phase 3.

Reads a structured issue spec (YAML or JSON) and creates issues wired for
Phase 3:
- Milestone = "Phase 3: Scoped Autonomous Tasking"
- Depends on #21
- Optional labels
- Agent-selection justification comment

Supports dry-run and idempotent creation (skips if an issue with the same
"title" already exists).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

DEFAULT_REPO = "openclaw/nisto-home"
DEFAULT_GITEA_URL = "http://100.73.228.90:3000"
PHASE3_MILESTONE = "Phase 3: Scoped Autonomous Tasking"
DEFAULT_DEPENDS_ON = 21
PAGE_SIZE = 50

JsonDict = Dict[str, Any]
Requester = Callable[[urllib.request.Request], Any]


def _request_json(request: urllib.request.Request, requester: Requester) -> JsonDict:
    with requester(request) as response:
        data = response.read()
        return json.loads(data.decode()) if data else {}


def _auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"token {token}"}


def _json_headers(token: str) -> Dict[str, str]:
    headers = _auth_headers(token)
    headers["Content-Type"] = "application/json"
    return headers


def _ensure_depends_text(body: str, depends_on: int) -> str:
    marker = f"Depends on: #{depends_on}"
    if marker.lower() in body.lower():
        return body
    if body and not body.endswith("\n"):
        body += "\n"
    return f"{body}\n{marker}\n"


def load_specs(path: str | Path) -> List[JsonDict]:
    content = Path(path).read_text()
    data: Any
    if str(path).endswith((".yml", ".yaml")):
        data = yaml.safe_load(content)
    else:
        data = json.loads(content)

    issues = data.get("issues") if isinstance(data, dict) else data
    if not isinstance(issues, list):
        raise ValueError("Spec must be a list of issues or contain an 'issues' key")
    return issues


@dataclass
class GiteaClient:
    repo: str
    token: str
    base_url: str = DEFAULT_GITEA_URL
    requester: Requester = urllib.request.urlopen

    @property
    def owner(self) -> str:
        return self.repo.split("/")[0]

    @property
    def name(self) -> str:
        return self.repo.split("/")[1]

    def _build_url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"

    def list_milestones(self) -> List[JsonDict]:
        url = self._build_url(f"/api/v1/repos/{self.repo}/milestones?state=all&limit={PAGE_SIZE}")
        req = urllib.request.Request(url, headers=_auth_headers(self.token))
        return _request_json(req, self.requester)

    def list_issues(self) -> List[JsonDict]:
        issues: List[JsonDict] = []
        page = 1
        while True:
            url = self._build_url(
                f"/api/v1/repos/{self.repo}/issues?state=all&type=issues&page={page}&limit={PAGE_SIZE}"
            )
            req = urllib.request.Request(url, headers=_auth_headers(self.token))
            batch = _request_json(req, self.requester)
            issues.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            page += 1
        return issues

    def create_issue(self, payload: JsonDict) -> JsonDict:
        url = self._build_url(f"/api/v1/repos/{self.repo}/issues")
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers=_json_headers(self.token),
            method="POST",
        )
        return _request_json(req, self.requester)

    def add_dependency(self, issue_number: int, depends_on: int) -> None:
        url = self._build_url(
            f"/api/v1/repos/{self.repo}/issues/{issue_number}/dependencies"
        )
        payload = {"index": depends_on, "owner": self.owner, "repo": self.name}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers=_json_headers(self.token),
            method="POST",
        )
        _request_json(req, self.requester)

    def post_comment(self, issue_number: int, body: str) -> None:
        url = self._build_url(
            f"/api/v1/repos/{self.repo}/issues/{issue_number}/comments"
        )
        payload = {"body": body}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers=_json_headers(self.token),
            method="POST",
        )
        _request_json(req, self.requester)


def find_milestone_id(client: GiteaClient, title: str) -> Optional[int]:
    for milestone in client.list_milestones():
        if milestone.get("title") == title:
            return milestone.get("id") or milestone.get("number")
    return None


def build_justification_comment(issue: JsonDict) -> str:
    assignee = issue.get("assignee")
    justification = issue.get("justification") or issue.get("rationale")
    capability = issue.get("capability") or issue.get("capabilities")
    category = issue.get("category")

    lines = ["Agent selection justification:"]
    lines.append(f"- Assignee: @{assignee}" if assignee else "- Assignee: (unset)")
    if category:
        lines.append(f"- Category: {category}")
    if justification:
        lines.append(f"- Rationale: {justification}")
    else:
        lines.append("- Rationale: not provided; please update")
    if capability:
        lines.append(f"- Capability fit: {capability}")

    return "\n".join(lines)


def create_or_skip_issue(
    client: GiteaClient,
    issue: JsonDict,
    *,
    milestone_id: int,
    depends_on: int,
    existing_titles: Dict[str, JsonDict],
    dry_run: bool = False,
) -> Optional[JsonDict]:
    title = issue.get("title")
    if not title:
        raise ValueError("Issue spec missing 'title'")

    normalized = title.strip().lower()
    if normalized in existing_titles:
        return None

    body = _ensure_depends_text(issue.get("body", ""), depends_on)
    payload: JsonDict = {
        "title": title,
        "body": body,
        "milestone": milestone_id,
    }

    if issue.get("assignee"):
        payload["assignee"] = issue["assignee"]
    if issue.get("labels"):
        payload["labels"] = issue["labels"]

    if dry_run:
        return {
            "title": title,
            "dry_run": True,
            "payload": payload,
        }

    created = client.create_issue(payload)
    issue_number = created.get("number")
    if issue_number:
        client.add_dependency(issue_number, depends_on)
        client.post_comment(issue_number, build_justification_comment(issue))
    return created


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap Phase 3 issues")
    parser.add_argument("--spec", required=True, help="Path to YAML/JSON issue spec")
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITEA_REPO", DEFAULT_REPO),
        help="Target repo (owner/name)",
    )
    parser.add_argument(
        "--gitea-url",
        default=os.environ.get("GITEA_URL", DEFAULT_GITEA_URL),
        help="Gitea base URL",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITEA_TOKEN"),
        help="Gitea token",
    )
    parser.add_argument(
        "--milestone-title",
        default=PHASE3_MILESTONE,
        help="Milestone title to attach",
    )
    parser.add_argument(
        "--depends-on",
        type=int,
        default=DEFAULT_DEPENDS_ON,
        help="Issue number to depend on",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without creating issues")
    return parser.parse_args(argv)


def run_bootstrap(
    *,
    spec_path: str,
    repo: str,
    gitea_url: str,
    token: str,
    milestone_title: str,
    depends_on: int,
    dry_run: bool = False,
    requester: Requester = urllib.request.urlopen,
) -> List[JsonDict]:
    if not token and not dry_run:
        raise ValueError("Gitea token is required unless --dry-run")

    issues = load_specs(spec_path)
    client = GiteaClient(repo=repo, token=token or "", base_url=gitea_url, requester=requester)

    milestone_id = find_milestone_id(client, milestone_title)
    if milestone_id is None:
        raise RuntimeError(f"Milestone '{milestone_title}' not found in {repo}")

    existing = client.list_issues() if not dry_run else []
    existing_titles = {i.get("title", "").strip().lower(): i for i in existing}

    results: List[JsonDict] = []
    for issue in issues:
        created = create_or_skip_issue(
            client,
            issue,
            milestone_id=milestone_id,
            depends_on=depends_on,
            existing_titles=existing_titles,
            dry_run=dry_run,
        )
        if created:
            results.append(created)
    return results


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    try:
        results = run_bootstrap(
            spec_path=args.spec,
            repo=args.repo,
            gitea_url=args.gitea_url,
            token=args.token,
            milestone_title=args.milestone_title,
            depends_on=args.depends_on,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for result in results:
        if result.get("dry_run"):
            print(f"[dry-run] Would create: {result['title']}")
        else:
            title = result.get("title")
            number = result.get("number") or result.get("id")
            print(f"Created issue #{number}: {title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
