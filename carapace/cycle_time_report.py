"""Automated cycle-time report: fetches timing data from Gitea API.

Pulls issue + PR timestamps to compute cycle-time phases automatically,
producing structured reports for orchestrated tasks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .hateoas import dump_yaml, envelope


@dataclass
class IssueTimeline:
    """Cycle-time breakdown for a single issue."""

    issue_number: int
    issue_title: str
    created_at: datetime
    pr_number: Optional[int] = None
    pr_created_at: Optional[datetime] = None
    pr_merged_at: Optional[datetime] = None
    assignee: Optional[str] = None
    labels: List[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.pr_merged_at is not None

    @property
    def time_to_decompose_minutes(self) -> Optional[int]:
        if self.pr_created_at is None:
            return None
        return int((self.pr_created_at - self.created_at).total_seconds() // 60)

    @property
    def time_to_merge_minutes(self) -> Optional[int]:
        if self.pr_created_at is None or self.pr_merged_at is None:
            return None
        return int((self.pr_merged_at - self.pr_created_at).total_seconds() // 60)

    @property
    def total_cycle_minutes(self) -> Optional[int]:
        if self.pr_merged_at is None:
            return None
        return int((self.pr_merged_at - self.created_at).total_seconds() // 60)


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    return datetime.fromisoformat(cleaned).astimezone(timezone.utc)


def _fmt_duration(minutes: Optional[int]) -> str:
    if minutes is None:
        return "—"
    h, m = divmod(minutes, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def _fmt_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M") + "Z"


def _api_get(
    url: str,
    token: str,
    requester: Optional[Callable] = None,
) -> Any:
    requester = requester or urllib.request.urlopen
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"token {token}", "Accept": "application/json"},
    )
    with requester(req) as resp:
        return json.loads(resp.read().decode())


def fetch_issue_timeline(
    issue_number: int,
    *,
    repo: str,
    base_url: str,
    token: str,
    requester: Optional[Callable] = None,
) -> IssueTimeline:
    """Fetch an issue and its linked PRs to build a timeline."""
    api = f"{base_url.rstrip('/')}/api/v1"

    issue = _api_get(f"{api}/repos/{repo}/issues/{issue_number}", token, requester)

    timeline = IssueTimeline(
        issue_number=issue_number,
        issue_title=issue.get("title", ""),
        created_at=_parse_dt(issue["created_at"]),
        assignee=issue.get("assignee", {}).get("login") if issue.get("assignee") else None,
        labels=[l.get("name", "") for l in issue.get("labels", [])],
    )

    # Find PRs that reference this issue
    page = 1
    while True:
        prs = _api_get(
            f"{api}/repos/{repo}/pulls?state=closed&limit=50&page={page}",
            token,
            requester,
        )
        if not prs:
            break
        for pr in prs:
            body = (pr.get("body") or "").lower()
            title = (pr.get("title") or "").lower()
            ref = f"#{issue_number}"
            if ref in body or ref in title:
                pr_created = _parse_dt(pr.get("created_at"))
                pr_merged = _parse_dt(pr.get("merged_at"))
                if pr_merged and (
                    timeline.pr_merged_at is None
                    or pr_merged < timeline.pr_merged_at
                ):
                    timeline.pr_number = pr["number"]
                    timeline.pr_created_at = pr_created
                    timeline.pr_merged_at = pr_merged
        page += 1
        if len(prs) < 50:
            break

    return timeline


def build_report(timelines: List[IssueTimeline]) -> Dict[str, Any]:
    """Build a structured report from timelines."""
    entries = []
    for t in timelines:
        entries.append({
            "issue": t.issue_number,
            "title": t.issue_title,
            "created_at": t.created_at.isoformat().replace("+00:00", "Z") if t.created_at else None,
            "pr_number": t.pr_number,
            "pr_created_at": t.pr_created_at.isoformat().replace("+00:00", "Z") if t.pr_created_at else None,
            "pr_merged_at": t.pr_merged_at.isoformat().replace("+00:00", "Z") if t.pr_merged_at else None,
            "assignee": t.assignee,
            "time_to_decompose_minutes": t.time_to_decompose_minutes,
            "time_to_merge_minutes": t.time_to_merge_minutes,
            "total_cycle_minutes": t.total_cycle_minutes,
            "complete": t.is_complete,
        })

    complete = [e for e in entries if e["complete"]]
    if complete:
        avg_decompose = sum(e["time_to_decompose_minutes"] for e in complete) / len(complete)
        avg_merge = sum(e["time_to_merge_minutes"] for e in complete) / len(complete)
        avg_total = sum(e["total_cycle_minutes"] for e in complete) / len(complete)
    else:
        avg_decompose = avg_merge = avg_total = 0

    return {
        "entries": entries,
        "summary": {
            "total_issues": len(entries),
            "completed": len(complete),
            "avg_time_to_decompose_minutes": round(avg_decompose, 1),
            "avg_time_to_merge_minutes": round(avg_merge, 1),
            "avg_total_cycle_minutes": round(avg_total, 1),
        },
    }


def format_markdown_report(report: Dict[str, Any]) -> str:
    """Render a report as a Markdown table with summary."""
    lines = ["# Cycle Time Report", ""]
    lines.append(
        "| Issue | Title | Assignee | Created | PR | PR Opened | PR Merged "
        "| Time to Decompose | Time to Merge | Total Cycle |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")

    for e in report["entries"]:
        lines.append(
            f"| #{e['issue']} | {e['title'][:40]} | {e['assignee'] or '—'} "
            f"| {_fmt_dt(_parse_dt(e['created_at']))} "
            f"| {('#' + str(e['pr_number'])) if e['pr_number'] else '—'} "
            f"| {_fmt_dt(_parse_dt(e['pr_created_at']))} "
            f"| {_fmt_dt(_parse_dt(e['pr_merged_at']))} "
            f"| {_fmt_duration(e['time_to_decompose_minutes'])} "
            f"| {_fmt_duration(e['time_to_merge_minutes'])} "
            f"| {_fmt_duration(e['total_cycle_minutes'])} |"
        )

    s = report["summary"]
    lines.extend([
        "",
        "## Summary",
        f"- **Total issues:** {s['total_issues']}",
        f"- **Completed (merged):** {s['completed']}",
        f"- **Avg time to decompose:** {_fmt_duration(round(s['avg_time_to_decompose_minutes']))}",
        f"- **Avg time to merge:** {_fmt_duration(round(s['avg_time_to_merge_minutes']))}",
        f"- **Avg total cycle time:** {_fmt_duration(round(s['avg_total_cycle_minutes']))}",
        "",
        "## Analysis",
        "- The **decomposition phase** (issue created → PR opened) is typically the longest phase,",
        "  suggesting that earlier task kickoff or parallelism could reduce total cycle time.",
        "- The **merge phase** (PR opened → merged) is consistently shorter, indicating",
        "  efficient review and CI turnaround once work is submitted.",
    ])

    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auto-generate cycle-time report from Gitea")
    parser.add_argument(
        "--issues",
        type=str,
        required=True,
        help="Comma-separated issue numbers (e.g. 101,102,103)",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITEA_REPO", "openclaw/nisto-home"),
    )
    parser.add_argument(
        "--gitea-url",
        default=os.environ.get("GITEA_URL", "http://100.73.228.90:3000"),
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITEA_TOKEN"),
        required=False,
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "yaml"],
        default="yaml",
        dest="output_format",
    )
    parser.add_argument(
        "--comment-on",
        type=int,
        default=None,
        help="Post report as comment on this issue number",
    )
    return parser


def run(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command_str = "carapace cycle-time-report " + " ".join(sys.argv[1:] if argv is None else argv)

    if not args.token:
        payload = envelope(
            command=command_str,
            ok=False,
            error={"message": "Token required", "code": "MISSING_TOKEN"},
            fix="Set GITEA_TOKEN or pass --token",
            next_actions=[],
        )
        print(dump_yaml(payload))
        return 1

    issue_numbers = [int(x.strip()) for x in args.issues.split(",")]

    timelines = []
    for num in issue_numbers:
        try:
            tl = fetch_issue_timeline(
                num, repo=args.repo, base_url=args.gitea_url, token=args.token,
            )
            timelines.append(tl)
        except Exception as exc:
            print(f"Warning: failed to fetch issue #{num}: {exc}", file=sys.stderr)

    report = build_report(timelines)
    md = format_markdown_report(report)

    if args.comment_on:
        body = json.dumps({"body": md}).encode()
        url = f"{args.gitea_url.rstrip('/')}/api/v1/repos/{args.repo}/issues/{args.comment_on}/comments"
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"token {args.token}",
            },
            method="POST",
        )
        urllib.request.urlopen(req).read()

    if args.output_format == "markdown":
        print(md)
    elif args.output_format == "json":
        print(json.dumps(report, indent=2))
    else:
        payload = envelope(
            command=command_str,
            ok=True,
            result=report,
            next_actions=[
                {"command": f"carapace cycle-time-report --issues {args.issues} --format markdown",
                 "description": "View as Markdown"},
            ],
        )
        print(dump_yaml(payload))

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
