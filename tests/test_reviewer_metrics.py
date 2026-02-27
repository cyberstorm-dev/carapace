from datetime import datetime, timedelta, timezone

from carapace import reviewer_metrics as rm


def test_compute_metrics_counts_and_timing():
    pr = rm.PullRequest(
        number=1,
        title="Example",
        created_at=datetime(2026, 2, 23, 0, 0, tzinfo=timezone.utc),
        html_url="http://example/pr/1",
    )
    reviews = [
        rm.Review(state="APPROVED", submitted_at=pr.created_at + timedelta(minutes=30)),
        rm.Review(state="REQUEST_CHANGES", submitted_at=pr.created_at + timedelta(minutes=90)),
    ]

    metrics = rm.compute_metrics(pr, reviews)

    assert metrics.total_reviews == 2
    assert metrics.approvals == 1
    assert metrics.change_requests == 1
    assert metrics.time_to_first_review_minutes == 30


def test_collect_metrics_and_render_markdown():
    base_url = "http://gitea.local"
    repo = "openclaw/nisto-home"
    milestone = "Phase 3: Scoped Autonomous Tasking"

    milestone_url = f"{base_url}/api/v1/repos/{repo}/milestones?state=all"
    pulls_url = f"{base_url}/api/v1/repos/{repo}/pulls?state=all&milestone=2"
    reviews_url_1 = f"{base_url}/api/v1/repos/{repo}/pulls/10/reviews"
    reviews_url_2 = f"{base_url}/api/v1/repos/{repo}/pulls/11/reviews"

    responses = {
        milestone_url: [{"id": 2, "title": milestone}],
        pulls_url: [
            {
                "number": 10,
                "title": "Add metrics reporter",
                "created_at": "2026-02-23T01:00:00Z",
                "html_url": "http://example/pr/10",
            },
            {
                "number": 11,
                "title": "Fix docs",
                "created_at": "2026-02-23T02:00:00Z",
                "html_url": "http://example/pr/11",
            },
        ],
        reviews_url_1: [
            {"state": "REQUEST_CHANGES", "submitted_at": "2026-02-23T01:30:00Z"},
            {"state": "APPROVED", "submitted_at": "2026-02-23T02:00:00Z"},
        ],
        reviews_url_2: [
            {"state": "APPROVED", "submitted_at": "2026-02-23T02:30:00Z"},
        ],
    }

    def fetcher(url: str):
        return responses[url]

    metrics = rm.collect_metrics(
        repo=repo,
        milestone=milestone,
        base_url=base_url,
        token="dummy",
        fetcher=fetcher,
    )
    summary = rm.summarize(metrics)
    markdown = rm.render_markdown(metrics, summary)

    assert summary.total_prs == 2
    assert summary.total_reviews == 3
    assert summary.total_change_requests == 1
    assert summary.total_approvals == 2
    assert "Rejection rate: 33% (1/3)" in markdown
    assert "Add metrics reporter" in markdown
    assert "Fix docs" in markdown
    assert "Time to first review" in markdown
