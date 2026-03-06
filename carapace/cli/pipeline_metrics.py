"""Pipeline metrics collection and Prometheus exposition.

Collects pull requests and review activity from Gitea and emits Prometheus
metrics covering cycle time, throughput, quality, and health signals.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional

DEFAULT_BUCKETS_SECONDS = [3600, 7200, 14400, 28800, 86400, 172800, 604800]


@dataclass
class Review:
    state: str
    submitted_at: datetime


@dataclass
class PullRequest:
    number: int
    state: str
    created_at: datetime
    merged_at: Optional[datetime]
    reviews: List[Review]


Fetcher = Callable[[str], Iterable[Mapping[str, object]]]


ISO_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z")


def _parse_datetime(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    cleaned = raw
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    for fmt in ISO_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).astimezone(timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Could not parse datetime: {raw}")


def _fetch_json(url: str, token: str, opener: Optional[Callable[[urllib.request.Request], Iterable]] = None):
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(url, headers={"Authorization": f"token {token}"})
    with opener(req) as resp:
        return json.loads(resp.read().decode())


def _build_fetcher(token: str, opener: Optional[Callable[[urllib.request.Request], Iterable]] = None) -> Fetcher:
    return lambda url: _fetch_json(url, token, opener)


def collect_pull_requests(
    *, repo: str, base_url: str, token: str, fetcher: Optional[Fetcher] = None
) -> List[PullRequest]:
    fetch = fetcher or _build_fetcher(token)
    pulls_url = f"{base_url.rstrip('/')}/api/v1/repos/{repo}/pulls?state=all"
    pulls = fetch(pulls_url)
    result: List[PullRequest] = []
    for pr in pulls:
        number = int(pr["number"])
        reviews_url = f"{base_url.rstrip('/')}/api/v1/repos/{repo}/pulls/{number}/reviews"
        reviews_raw = fetch(reviews_url)
        reviews = [
            Review(state=r.get("state", ""), submitted_at=_parse_datetime(r.get("submitted_at")) or datetime.now(timezone.utc))
            for r in reviews_raw
        ]
        result.append(
            PullRequest(
                number=number,
                state=str(pr.get("state", "")),
                created_at=_parse_datetime(pr.get("created_at")) or datetime.now(timezone.utc),
                merged_at=_parse_datetime(pr.get("merged_at")),
                reviews=reviews,
            )
        )
    return result


@dataclass
class PipelineSummary:
    merged: int = 0
    open: int = 0
    closed_unmerged: int = 0
    merged_last_7d: int = 0
    reviewer_skipped: int = 0
    stale_open: int = 0
    total_reviews: int = 0
    change_requests: int = 0
    approvals: int = 0
    re_reviews: int = 0
    rejection_rate: float = 0.0
    time_to_first_review: List[float] = None  # type: ignore[assignment]
    time_to_merge: List[float] = None  # type: ignore[assignment]
    review_to_merge: List[float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.time_to_first_review = [] if self.time_to_first_review is None else self.time_to_first_review
        self.time_to_merge = [] if self.time_to_merge is None else self.time_to_merge
        self.review_to_merge = [] if self.review_to_merge is None else self.review_to_merge


def compute_summary(
    pull_requests: Iterable[PullRequest],
    *,
    now: Optional[datetime] = None,
    stale_after_days: int = 3,
) -> PipelineSummary:
    now = now or datetime.now(timezone.utc)
    summary = PipelineSummary()
    stale_delta = timedelta(days=stale_after_days)

    for pr in pull_requests:
        total_reviews = len(pr.reviews)
        change_requests = sum(1 for r in pr.reviews if r.state.upper() == "REQUEST_CHANGES")
        approvals = sum(1 for r in pr.reviews if r.state.upper() == "APPROVED")

        summary.total_reviews += total_reviews
        summary.change_requests += change_requests
        summary.approvals += approvals
        if total_reviews > 1:
            summary.re_reviews += total_reviews - 1

        first_review_at = min((r.submitted_at for r in pr.reviews), default=None)
        if first_review_at:
            summary.time_to_first_review.append((first_review_at - pr.created_at).total_seconds())

        if pr.merged_at:
            summary.merged += 1
            if pr.merged_at >= now - timedelta(days=7):
                summary.merged_last_7d += 1
            summary.time_to_merge.append((pr.merged_at - pr.created_at).total_seconds())
            if first_review_at:
                summary.review_to_merge.append((pr.merged_at - first_review_at).total_seconds())
            if total_reviews == 0:
                summary.reviewer_skipped += 1
        else:
            if pr.state.lower() == "open":
                summary.open += 1
                if now - pr.created_at > stale_delta:
                    summary.stale_open += 1
            else:
                summary.closed_unmerged += 1

    if summary.total_reviews:
        summary.rejection_rate = summary.change_requests / summary.total_reviews
    else:
        summary.rejection_rate = 0.0

    return summary


def _render_labels(labels: Mapping[str, str]) -> str:
    def _sanitize(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\"", "\\\"")

    return ",".join(f'{k}="{_sanitize(v)}"' for k, v in sorted(labels.items()))


def _histogram_lines(
    *,
    name: str,
    help_text: str,
    samples: Iterable[float],
    buckets: List[float],
    labels: Mapping[str, str],
) -> List[str]:
    ordered_buckets: List[float] = sorted({b for b in buckets if b > 0}) + [float("inf")]
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]

    sample_list = list(samples)
    for upper in ordered_buckets:
        count = len([s for s in sample_list if s <= upper])
        if upper == float("inf"):
            bucket_label = "+Inf"
        elif isinstance(upper, float) and upper.is_integer():
            bucket_label = str(int(upper))
        else:
            bucket_label = str(upper)
        lines.append(f"{name}_bucket{{{_render_labels(labels | {'le': bucket_label})}}} {count}")

    lines.append(f"{name}_count{{{_render_labels(labels)}}} {len(sample_list)}")
    lines.append(f"{name}_sum{{{_render_labels(labels)}}} {sum(sample_list)}")
    return lines


def _counter_line(name: str, value: float, labels: Mapping[str, str], help_text: Optional[str] = None) -> List[str]:
    lines = []
    if help_text:
        lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    lines.append(f"{name}{{{_render_labels(labels)}}} {value}")
    return lines


def render_prometheus(
    *,
    pull_requests: Iterable[PullRequest],
    model: str = "unknown",
    now: Optional[datetime] = None,
    stale_after_days: int = 3,
    buckets: Optional[List[float]] = None,
) -> str:
    buckets = buckets or DEFAULT_BUCKETS_SECONDS
    now = now or datetime.now(timezone.utc)
    summary = compute_summary(pull_requests, now=now, stale_after_days=stale_after_days)
    lines: List[str] = []

    base_labels = {"model": model}
    lines.extend(
        _counter_line(
            "pipeline_pr_total",
            summary.merged,
            labels=base_labels | {"state": "merged"},
            help_text="Count of merged pull requests",
        )
    )
    lines.extend(
        _counter_line(
            "pipeline_pr_total",
            summary.open,
            labels=base_labels | {"state": "open"},
        )
    )
    lines.extend(
        _counter_line(
            "pipeline_pr_total",
            summary.closed_unmerged,
            labels=base_labels | {"state": "closed"},
        )
    )
    lines.extend(
        _counter_line(
            "pipeline_pr_merged_last_7d",
            summary.merged_last_7d,
            labels=base_labels,
            help_text="Merged pull requests in the last 7 days",
        )
    )

    lines.extend(
        _counter_line(
            "pipeline_pr_reviewer_skipped_total",
            summary.reviewer_skipped,
            labels=base_labels,
            help_text="Merged PRs without any reviews",
        )
    )
    lines.extend(
        _counter_line(
            "pipeline_pr_stale_open_total",
            summary.stale_open,
            labels=base_labels,
            help_text="Open PRs older than the stale threshold",
        )
    )

    lines.extend(
        _counter_line(
            "pipeline_pr_reviews_total",
            summary.total_reviews,
            labels=base_labels,
            help_text="Total reviews across PRs",
        )
    )
    lines.extend(
        _counter_line(
            "pipeline_pr_change_requests_total",
            summary.change_requests,
            labels=base_labels,
            help_text="Total change-requests across PRs",
        )
    )
    lines.extend(
        _counter_line(
            "pipeline_pr_approvals_total",
            summary.approvals,
            labels=base_labels,
            help_text="Total approvals across PRs",
        )
    )
    lines.extend(
        _counter_line(
            "pipeline_pr_re_review_total",
            summary.re_reviews,
            labels=base_labels,
            help_text="Reviews beyond the first per PR",
        )
    )
    lines.extend(
        _counter_line(
            "pipeline_pr_rejection_rate",
            round(summary.rejection_rate, 4),
            labels=base_labels,
            help_text="Ratio of change-requests to total reviews",
        )
    )

    if summary.time_to_first_review:
        lines.extend(
            _histogram_lines(
                name="pipeline_pr_time_to_first_review_seconds",
                help_text="Time from PR creation to first review",
                samples=summary.time_to_first_review,
                buckets=buckets,
                labels=base_labels,
            )
        )
    if summary.time_to_merge:
        lines.extend(
            _histogram_lines(
                name="pipeline_pr_time_to_merge_seconds",
                help_text="Time from PR creation to merge",
                samples=summary.time_to_merge,
                buckets=buckets,
                labels=base_labels,
            )
        )
    if summary.review_to_merge:
        lines.extend(
            _histogram_lines(
                name="pipeline_pr_review_to_merge_seconds",
                help_text="Time from first review to merge",
                samples=summary.review_to_merge,
                buckets=buckets,
                labels=base_labels,
            )
        )

    return "\n".join(lines) + "\n"


def _push_to_gateway(url: str, body: str, basic_auth: Optional[str], timeout: int = 5) -> None:
    data = body.encode()
    req = urllib.request.Request(url, data=data, method="PUT")
    if basic_auth:
        req.add_header("Authorization", f"Basic {basic_auth}")
    req.add_header("Content-Type", "text/plain; version=0.0.4")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        resp.read()


def resolve_model_label(env: Mapping[str, str]) -> str:
    return env.get("CARAPACE_MODEL") or env.get("MODEL_ID") or "unknown"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline metrics Prometheus emitter")
    parser.add_argument("--repo", default="openclaw/nisto-home")
    parser.add_argument("--gitea-url", default=os.environ.get("GITEA_URL", "http://100.73.228.90:3000"))
    parser.add_argument("--token", help="Gitea token", default=os.environ.get("GITEA_TOKEN"))
    parser.add_argument("--model", help="Model label for metrics", default=None)
    parser.add_argument("--stale-after-days", type=int, default=3)
    parser.add_argument("--bucket", action="append", type=float, help="Histogram bucket (seconds)")
    parser.add_argument("--pushgateway-url", default=os.environ.get("PIPELINE_METRICS_PUSHGATEWAY"))
    parser.add_argument("--job", default=os.environ.get("PIPELINE_METRICS_JOB", "pipeline"))
    parser.add_argument("--instance", default=os.environ.get("PIPELINE_METRICS_INSTANCE"))
    parser.add_argument("--basic-auth", default=os.environ.get("PIPELINE_METRICS_BASIC_AUTH"))
    parser.add_argument("--dry-run", action="store_true", help="Print metrics without pushing")
    args = parser.parse_args(argv)

    if not args.token:
        raise SystemExit("--token or GITEA_TOKEN is required")

    model_label = args.model or resolve_model_label(os.environ)

    pull_requests = collect_pull_requests(
        repo=args.repo,
        base_url=args.gitea_url,
        token=args.token,
    )
    metrics_text = render_prometheus(
        pull_requests=pull_requests,
        model=model_label,
        now=datetime.now(timezone.utc),
        stale_after_days=args.stale_after_days,
        buckets=args.bucket or DEFAULT_BUCKETS_SECONDS,
    )

    push_url = args.pushgateway_url
    if push_url and args.instance:
        push_url = f"{push_url}/metrics/job/{urllib.parse.quote(args.job)}/instance/{urllib.parse.quote(args.instance)}"
    elif push_url:
        push_url = f"{push_url}/metrics/job/{urllib.parse.quote(args.job)}"

    if args.dry_run or not push_url:
        if not push_url:
            print("pipeline-metrics: dry-run (no pushgateway configured)")
        sys.stdout.write(metrics_text)
        return 0

    try:
        _push_to_gateway(push_url, metrics_text, args.basic_auth)
        print(f"pipeline-metrics: pushed metrics to {push_url}")
    except urllib.error.URLError as exc:  # pragma: no cover - best-effort push
        print(f"pipeline-metrics: WARNING - failed to push metrics: {exc}", file=sys.stderr)
        return 0
    return 0


__all__ = [
    "Review",
    "PullRequest",
    "collect_pull_requests",
    "compute_summary",
    "render_prometheus",
    "resolve_model_label",
    "DEFAULT_BUCKETS_SECONDS",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
