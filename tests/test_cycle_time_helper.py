import json
from datetime import datetime, timezone

import pytest

from carapace import cycle_time as cth


def test_build_entry_computes_durations_and_formats_markdown():
    entry = cth.build_entry(
        issue=55,
        started_at="2026-02-22T19:30:00Z",
        decomposed_at="2026-02-22T19:53:00Z",
        finished_at="2026-02-22T20:10:00Z",
        delegation_outperformed=True,
        notes="delegate faster",
        executor="builder",
    )

    assert entry.durations["start_to_decompose_minutes"] == 23
    assert entry.durations["start_to_finish_minutes"] == 40

    markdown = cth.format_markdown(entry)
    assert "Cycle-Time Entry" in markdown
    assert "#55" in markdown
    assert "builder" in markdown

    json_blob = cth.to_json(entry)
    assert json_blob["delegation_outperformed"] is True
    assert json_blob["durations"]["decompose_to_finish_minutes"] == 17


def test_build_entry_rejects_unordered_timestamps():
    with pytest.raises(ValueError):
        cth.build_entry(
            issue=55,
            started_at="2026-02-22T20:00:00Z",
            decomposed_at="2026-02-22T19:59:00Z",
            finished_at="2026-02-22T21:00:00Z",
            delegation_outperformed=False,
        )


@pytest.mark.parametrize(
    "missing_field",
    [
        {"started_at": None},
        {"decomposed_at": None},
        {"finished_at": None},
    ],
)
def test_build_entry_requires_all_timestamps(missing_field):
    kwargs = {
        "issue": 55,
        "started_at": "2026-02-22T19:30:00Z",
        "decomposed_at": "2026-02-22T19:53:00Z",
        "finished_at": "2026-02-22T20:10:00Z",
        "delegation_outperformed": False,
    }
    kwargs.update(missing_field)

    with pytest.raises(ValueError):
        cth.build_entry(**kwargs)


def test_post_comment_uses_repo_and_token(monkeypatch):
    entry = cth.build_entry(
        issue=55,
        started_at="2026-02-22T19:30:00Z",
        decomposed_at="2026-02-22T19:53:00Z",
        finished_at="2026-02-22T20:10:00Z",
        delegation_outperformed=False,
    )

    captured = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"{}"

    def fake_requester(request):
        captured["url"] = request.full_url
        captured["headers"] = request.headers
        captured["data"] = request.data
        return DummyResponse()

    cth.post_comment(
        entry,
        repo="openclaw/nisto-home",
        token="secret-token",
        base_url="http://example.com",
        requester=fake_requester,
    )

    assert (
        captured["url"]
        == "http://example.com/api/v1/repos/openclaw/nisto-home/issues/55/comments"
    )
    assert captured["headers"]["Authorization"] == "token secret-token"
    payload = json.loads(captured["data"].decode())
    assert "Cycle-Time Entry" in payload["body"]
