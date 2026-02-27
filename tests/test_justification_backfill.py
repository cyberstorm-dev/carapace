import json
from pathlib import Path

import pytest

from carapace import justification_backfill as jb


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


class FakeRequester:
    def __init__(self):
        self.requests = []
        self.responses = []

    def queue(self, payload):
        self.responses.append(payload)

    def __call__(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("No queued response for request")
        payload = self.responses.pop(0)
        return FakeResponse(payload)


@pytest.fixture
def spec_file(tmp_path: Path) -> Path:
    data = {
        "issues": [
            {
                "number": 41,
                "assignee": "builder",
                "category": "process",
                "justification": "builder owns guardrails",
                "capability": "python automation",
            }
        ]
    }
    path = tmp_path / "justifications.yml"
    path.write_text(json.dumps(data))
    return path


def test_posts_standardized_justification_comment(spec_file: Path):
    requester = FakeRequester()
    requester.queue({})  # post comment response

    results = jb.post_justifications(
        spec_path=str(spec_file),
        repo="openclaw/nisto-home",
        gitea_url="http://example.com",
        token="secret",
        dry_run=False,
        requester=requester,
    )

    assert results == [{"issue": 41, "body": jb.build_justification_comment({
        "assignee": "builder",
        "category": "process",
        "justification": "builder owns guardrails",
        "capability": "python automation",
    })}]

    assert len(requester.requests) == 1
    payload = json.loads(requester.requests[0].data.decode())
    assert payload["body"].startswith("Agent selection justification:")
    assert "Assignee: @builder" in payload["body"]
    assert "builder owns guardrails" in payload["body"]


def test_dry_run_skips_network(spec_file: Path):
    requester = FakeRequester()

    results = jb.post_justifications(
        spec_path=str(spec_file),
        repo="openclaw/nisto-home",
        gitea_url="http://example.com",
        token="",
        dry_run=True,
        requester=requester,
    )

    assert results[0]["dry_run"] is True
    assert requester.requests == []


def test_missing_issue_number_raises(tmp_path: Path):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"issues": [{"assignee": "builder"}]}))

    with pytest.raises(ValueError):
        jb.post_justifications(
            spec_path=str(path),
            repo="openclaw/nisto-home",
            gitea_url="http://example.com",
            token="secret",
            dry_run=False,
            requester=FakeRequester(),
        )
