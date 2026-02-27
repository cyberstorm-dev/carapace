import argparse
import base64
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, request

from .config import Config, load_config
from .validation import (
    DEFAULT_CHECK_TIERS,
    TIER_ADVISORY,
    TIER_HARD,
    TIER_INFO,
    TIER_ORDER,
    ValidationMessage,
    _milestone_id,
    validate_issues,
)

DEFAULT_GITEA_URL = "http://100.73.228.90:3000"
PAGE_SIZE = 50

LEVEL_TIERS = {
    "hard": {TIER_HARD},
    "advisory": {TIER_HARD, TIER_ADVISORY},
    "all": set(TIER_ORDER),
}


def build_auth_headers(token: Optional[str]) -> Tuple[Dict[str, str], str]:
    """Construct Authorization headers from token or CI netrc credentials.

    Preference order:
    1) Basic auth when a username is available (uses password if set, else token)
    2) Bearer token when only a token is provided
    """

    username = os.environ.get("CI_NETRC_USERNAME") or os.environ.get("GITEA_USERNAME")
    password = os.environ.get("CI_NETRC_PASSWORD") or os.environ.get("GITEA_PASSWORD")

    if username and (password or token):
        secret = password or token or ""
        encoded = base64.b64encode(f"{username}:{secret}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}, "basic"

    if token:
        return {"Authorization": f"token {token}"}, "token"

    raise RuntimeError(
        "Missing credentials: provide GITEA_TOKEN or CI_NETRC_USERNAME/CI_NETRC_PASSWORD"
    )


def _request_json(url: str, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    req = request.Request(url, headers=headers)
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except error.HTTPError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def _fetch_dependencies(
    gitea_url: str, owner: str, name: str, headers: Dict[str, str], issue_number: int
) -> List[int]:
    url = f"{gitea_url.rstrip('/')}/api/v1/repos/{owner}/{name}/issues/{issue_number}/dependencies"
    deps = _request_json(url, headers)
    return [int(dep.get("number")) for dep in deps]


def _phase_of_issue(issue: Dict[str, Any]) -> Optional[int]:
    """
    Determine the phase number for an issue. Priority:
    1) milestone title matching "Phase N ..."
    2) issue title matching "Phase N ..."
    3) milestone id/index
    """

    titles: List[str] = []
    milestone = issue.get("milestone")
    if isinstance(milestone, dict):
        t = milestone.get("title")
        if t:
            titles.append(str(t))
    elif isinstance(milestone, str):
        titles.append(milestone)

    if issue.get("title"):
        titles.append(str(issue["title"]))

    for title in titles:
        m = re.search(r"phase\s+(\d+)", title, re.IGNORECASE)
        if m:
            return int(m.group(1))

    return _milestone_id(issue)


def fetch_open_pulls(gitea_url: str, repo: str, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    owner, name = repo.split("/")
    pulls: List[Dict[str, Any]] = []
    page = 1
    while True:
        url = (
            f"{gitea_url.rstrip('/')}/api/v1/repos/{owner}/{name}/pulls"
            f"?state=open&page={page}&limit={PAGE_SIZE}"
        )
        batch = _request_json(url, headers)
        pulls.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    return pulls


def fetch_all_issues(gitea_url: str, repo: str, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    owner, name = repo.split("/")
    issues: List[Dict[str, Any]] = []
    page = 1
    while True:
        url = (
            f"{gitea_url.rstrip('/')}/api/v1/repos/{owner}/{name}/issues"
            f"?state=all&type=issues&page={page}&limit={PAGE_SIZE}"
        )
        batch = _request_json(url, headers)
        issues.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    return issues


def load_issues_from_file(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate phase issue graph")
    parser.add_argument("--config", required=True, help="Path to validator.yaml")
    parser.add_argument("--repo", help="<owner>/<repo>")
    parser.add_argument("--token", help="Gitea token for API calls")
    parser.add_argument("--gitea-url", default=DEFAULT_GITEA_URL, help="Gitea base URL")
    parser.add_argument("--issues-file", help="Load issues JSON from file instead of live API")
    parser.add_argument("--output", choices=["text", "json"], default="text", help="Output format")
    parser.add_argument("--level", choices=["hard", "advisory", "all"], default="hard", help="Visibility level for advisory/info checks")
    return parser.parse_args(argv)


def _tier_for(check: str, config: Config) -> str:
    tier = config.check_tiers.get(check) or DEFAULT_CHECK_TIERS.get(check)
    return tier if tier in TIER_ORDER else TIER_ADVISORY


def _render_text(level: str, messages: List[ValidationMessage]) -> None:
    hard_messages = [m for m in messages if m.tier == TIER_HARD]

    if hard_messages:
        print("Validation failed:")
        for msg in hard_messages:
            print(f"- {msg.message}")
    else:
        print("Validation passed")

    for tier, heading in ((TIER_ADVISORY, "Advisories"), (TIER_INFO, "Informational")):
        tier_msgs = [m for m in messages if m.tier == tier]
        if tier_msgs:
            print(f"{heading}:")
            for msg in tier_msgs:
                print(f"  • {msg.message}")


def _render_json(level: str, messages: List[ValidationMessage]) -> None:
    hard_messages = [m for m in messages if m.tier == TIER_HARD]
    payload = {
        "status": "failed" if hard_messages else "passed",
        "level": level,
        "hard": [m.message for m in messages if m.tier == TIER_HARD],
        "advisory": [m.message for m in messages if m.tier == TIER_ADVISORY],
        "info": [m.message for m in messages if m.tier == TIER_INFO],
        "visible": [
            {"tier": m.tier, "check": m.check, "message": m.message}
            for m in messages
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config: Config = load_config(args.config)

    if args.issues_file:
        phase_issues = load_issues_from_file(args.issues_file)
        tan_next: List[Dict[str, Any]] = []
        headers = {}
    else:
        if not args.repo:
            print("--repo is required without --issues-file", file=sys.stderr)
            return 1

        try:
            headers, _source = build_auth_headers(args.token)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        owner, name = args.repo.split("/")
        all_issues = fetch_all_issues(args.gitea_url, args.repo, headers)
        phase = config.phase
        tan_label = config.labels.get("tan", "tan")
        phase_issues = [i for i in all_issues if _phase_of_issue(i) == phase]
        tan_next = [i for i in all_issues if _phase_of_issue(i) == phase + 1 and any(l.get("name") == tan_label for l in i.get("labels", []))]

        for issue in tan_next:
            issue["dependencies"] = _fetch_dependencies(args.gitea_url, owner, name, headers, issue["number"])
            issue["synthetic"] = True

        for issue in phase_issues:
            issue["dependencies"] = _fetch_dependencies(args.gitea_url, owner, name, headers, issue["number"])

    messages: List[ValidationMessage] = validate_issues(phase_issues, config, tan_next_phase=tan_next)

    # PR base branch check (hard gate)
    if not args.issues_file and args.repo:
        expected_base = config.base_branch if hasattr(config, "base_branch") and config.base_branch else "dev"
        try:
            open_pulls = fetch_open_pulls(args.gitea_url, args.repo, headers)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        for pr in open_pulls:
            base_label = pr.get("base", {}).get("ref", "")
            if base_label and base_label != expected_base:
                messages.append(
                    ValidationMessage(
                        check="base_branch",
                        tier=_tier_for("base_branch", config),
                        message=(
                            f"PR #{pr['number']} ({pr.get('title','')[:50]}) targets `"
                            f"{base_label}` instead of `{expected_base}`"
                        ),
                    )
                )

    hard_messages = [m for m in messages if m.tier == TIER_HARD]
    visible_messages = [m for m in messages if m.tier in LEVEL_TIERS[args.level]]

    if args.output == "json":
        _render_json(args.level, visible_messages)
    else:
        _render_text(args.level, visible_messages)

    return 1 if hard_messages else 0


if __name__ == "__main__":
    sys.exit(main())
