"""Reviewer activity reporter for Phase 3.

Fetches pull requests for a milestone and computes review metrics:
- total reviews
- change-requests
- approvals
- time-to-first-review
- rejection rate (change-requests / total reviews)

Outputs a markdown table and optional JSON summary.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Union
from urllib import request

ISO_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z")


@dataclass
class PullRequest:
    number: int
    title: str
    created_at: datetime
    html_url: str


@dataclass
class Review:
    state: str
    submitted_at: datetime


@dataclass
class PRMetrics:
    pr: PullRequest
    total_reviews: int
    change_requests: int
    approvals: int
    time_to_first_review_minutes: Optional[int]


@dataclass
class Summary:
    total_prs: int
    total_reviews: int
    total_change_requests: int
    total_approvals: int
    rejection_rate: float


Fetcher = Callable[[str], Union[List[Any], Dict[str, Any]]]


def _parse_datetime(raw: str) -> datetime:
    cleaned = raw
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    for fmt in ISO_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).astimezone(timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Could not parse datetime: {raw}")


def _fetch_json(url: str, token: str, opener: Optional[Callable[[request.Request], Iterable]] = None):
    opener = opener or request.urlopen
    req = request.Request(url, headers={"Authorization": f"token {token}"})
    with opener(req) as resp:
        return json.loads(resp.read().decode())


def _find_milestone_id(repo: str, milestone: str, base_url: str, token: str, fetcher: Fetcher) -> int:
    url = f"{base_url}/api/v1/repos/{repo}/milestones?state=all"
    milestones = fetcher(url)
    for m in milestones:
        if m.get("title") == milestone:
            return m["id"]
    raise SystemExit(f"Milestone '{milestone}' not found")


def _fetch_pull_requests(repo: str, milestone_id: int, base_url: str, token: str, fetcher: Fetcher) -> List[PullRequest]:
    url = f"{base_url}/api/v1/repos/{repo}/pulls?state=all&milestone={milestone_id}"
    pulls = fetcher(url)
    result = []
    for pr in pulls:
        result.append(
            PullRequest(
                number=pr["number"],
                title=pr.get("title", ""),
                created_at=_parse_datetime(pr["created_at"]),
                html_url=pr.get("html_url", ""),
            )
        )
    return result


def _fetch_reviews(repo: str, pr_number: int, base_url: str, token: str, fetcher: Fetcher) -> List[Review]:
    url = f"{base_url}/api/v1/repos/{repo}/pulls/{pr_number}/reviews"
    reviews = fetcher(url)
    return [
        Review(state=rev.get("state", ""), submitted_at=_parse_datetime(rev["submitted_at"]))
        for rev in reviews
    ]


def compute_metrics(pr: PullRequest, reviews: List[Review]) -> PRMetrics:
    sorted_reviews = sorted(reviews, key=lambda r: r.submitted_at)
    time_to_first = None
    if sorted_reviews:
        delta = sorted_reviews[0].submitted_at - pr.created_at
        time_to_first = int(delta.total_seconds() // 60)

    change_requests = sum(1 for r in reviews if r.state.upper() == "REQUEST_CHANGES")
    approvals = sum(1 for r in reviews if r.state.upper() == "APPROVED")

    return PRMetrics(
        pr=pr,
        total_reviews=len(reviews),
        change_requests=change_requests,
        approvals=approvals,
        time_to_first_review_minutes=time_to_first,
    )


def summarize(metrics: List[PRMetrics]) -> Summary:
    total_reviews = sum(m.total_reviews for m in metrics)
    total_change_requests = sum(m.change_requests for m in metrics)
    total_approvals = sum(m.approvals for m in metrics)
    rejection_rate = (total_change_requests / total_reviews) if total_reviews else 0.0
    return Summary(
        total_prs=len(metrics),
        total_reviews=total_reviews,
        total_change_requests=total_change_requests,
        total_approvals=total_approvals,
        rejection_rate=rejection_rate,
    )


def render_markdown(metrics: List[PRMetrics], summary: Summary) -> str:
    header = "PR | Reviews | Change requests | Approvals | Time to first review (min)"
    separator = "|".join(["---"] * 5)
    rows = [
        " | ".join(
            [
                f"[{m.pr.title} (#{m.pr.number})]({m.pr.html_url})",
                str(m.total_reviews),
                str(m.change_requests),
                str(m.approvals),
                "-" if m.time_to_first_review_minutes is None else str(m.time_to_first_review_minutes),
            ]
        )
        for m in metrics
    ]

    denominator = summary.total_reviews
    rejection_fraction = f"{summary.total_change_requests}/{denominator}" if denominator else "0/0"
    summary_lines = [
        "**Summary**",
        f"Total PRs: {summary.total_prs}",
        f"Total reviews: {summary.total_reviews}",
        f"Change-requests: {summary.total_change_requests}",
        f"Approvals: {summary.total_approvals}",
        f"Rejection rate: {summary.rejection_rate:.0%} ({rejection_fraction})",
    ]

    return "\n".join([header, separator, *rows, "", *summary_lines])


def _build_fetcher(token: str, opener: Optional[Callable[[request.Request], Iterable]] = None) -> Fetcher:
    return lambda url: _fetch_json(url, token, opener)


def collect_metrics(repo: str, milestone: str, base_url: str, token: str, fetcher: Optional[Fetcher] = None) -> List[PRMetrics]:
    fetcher = fetcher or _build_fetcher(token)
    milestone_id = _find_milestone_id(repo, milestone, base_url, token, fetcher)
    pulls = _fetch_pull_requests(repo, milestone_id, base_url, token, fetcher)
    metrics: List[PRMetrics] = []
    for pr in pulls:
        reviews = _fetch_reviews(repo, pr.number, base_url, token, fetcher)
        metrics.append(compute_metrics(pr, reviews))
    return metrics


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Reviewer activity reporter")
    parser.add_argument("--repo", default="openclaw/nisto-home")
    parser.add_argument("--milestone", default="Phase 3: Scoped Autonomous Tasking")
    parser.add_argument("--gitea-url", default="http://100.73.228.90:3000")
    parser.add_argument("--token", default=None, help="Gitea token (or set GITEA_TOKEN)")
    parser.add_argument("--json", action="store_true", help="Also output JSON summary")
    args = parser.parse_args(argv)

    token = args.token or os.environ.get("GITEA_TOKEN")
    if token is None:
        raise SystemExit("--token or GITEA_TOKEN is required")

    metrics = collect_metrics(
        repo=args.repo,
        milestone=args.milestone,
        base_url=args.gitea_url.rstrip("/"),
        token=token,
    )
    summary = summarize(metrics)
    print(render_markdown(metrics, summary))
    if args.json:
        print(json.dumps([m.__dict__ for m in metrics], default=str, indent=2))
        print(json.dumps(summary.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
