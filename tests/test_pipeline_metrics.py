from datetime import datetime, timedelta, timezone

from carapace.pipeline_metrics import (
    PullRequest,
    Review,
    render_prometheus,
)


def _dt(base: datetime, *, days: int = 0, hours: int = 0, minutes: int = 0) -> datetime:
    return base + timedelta(days=days, hours=hours, minutes=minutes)


def test_render_prometheus_emits_counts_histograms_and_health_signals():
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)

    pr_with_review = PullRequest(
        number=1,
        state="closed",
        created_at=base,
        merged_at=_dt(base, hours=2),
        reviews=[Review(state="APPROVED", submitted_at=_dt(base, hours=1))],
    )

    pr_skipped = PullRequest(
        number=2,
        state="closed",
        created_at=base,
        merged_at=_dt(base, minutes=30),
        reviews=[],
    )

    pr_stale = PullRequest(
        number=3,
        state="open",
        created_at=_dt(base, days=-5),
        merged_at=None,
        reviews=[],
    )

    pr_open_with_reviews = PullRequest(
        number=4,
        state="open",
        created_at=base,
        merged_at=None,
        reviews=[
            Review(state="REQUEST_CHANGES", submitted_at=_dt(base, minutes=30)),
            Review(state="APPROVED", submitted_at=_dt(base, minutes=90)),
        ],
    )

    metrics_text = render_prometheus(
        pull_requests=[pr_with_review, pr_skipped, pr_stale, pr_open_with_reviews],
        model="gpt-4o",
        now=_dt(base, days=7),
        stale_after_days=3,
        buckets=[3600, 7200],
    )

    assert 'pipeline_pr_total{model="gpt-4o",state="merged"} 2' in metrics_text
    assert 'pipeline_pr_total{model="gpt-4o",state="open"} 2' in metrics_text
    assert 'pipeline_pr_reviewer_skipped_total{model="gpt-4o"} 1' in metrics_text
    assert 'pipeline_pr_stale_open_total{model="gpt-4o"} 2' in metrics_text
    assert 'pipeline_pr_rejection_rate{model="gpt-4o"} 0.3333' in metrics_text

    # Histograms
    assert 'pipeline_pr_time_to_first_review_seconds_bucket{le="3600",model="gpt-4o"} 2' in metrics_text
    assert 'pipeline_pr_time_to_merge_seconds_bucket{le="3600",model="gpt-4o"} 1' in metrics_text
    assert 'pipeline_pr_time_to_merge_seconds_sum{model="gpt-4o"} 9000.0' in metrics_text
    assert 'pipeline_pr_review_to_merge_seconds_bucket{le="7200",model="gpt-4o"} 1' in metrics_text
