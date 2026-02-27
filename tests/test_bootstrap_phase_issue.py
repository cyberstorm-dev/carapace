import json
from pathlib import Path

import pytest
import yaml

from carapace import bootstrap_phase_issue as bpi


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
                "title": "Bootstrap Phase Issue",
                "body": "Create automation",
                "assignee": "builder",
                "labels": ["needs-pr"],
                "justification": "builder owns automation",
                "capability": "repo tooling",
            }
        ]
    }
    path = tmp_path / "issues.yml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_load_specs_supports_yaml_and_json(tmp_path: Path):
    yaml_path = tmp_path / "issues.yaml"
    yaml_path.write_text("issues:\n  - title: YAML Issue\n")
    json_path = tmp_path / "issues.json"
    json_path.write_text(json.dumps([{"title": "JSON Issue"}]))

    yaml_specs = bpi.load_specs(yaml_path)
    json_specs = bpi.load_specs(json_path)

    assert yaml_specs[0]["title"] == "YAML Issue"
    assert json_specs[0]["title"] == "JSON Issue"


def test_run_bootstrap_creates_issue_and_posts_dependency_and_comment(spec_file: Path):
    requester = FakeRequester()
    requester.queue([{"id": 99, "title": bpi.PHASE3_MILESTONE}])  # milestones
    requester.queue([])  # existing issues
    requester.queue({"number": 123, "title": "Bootstrap Phase Issue"})  # create issue
    requester.queue({})  # add dependency
    requester.queue({})  # comment

    results = bpi.run_bootstrap(
        spec_path=str(spec_file),
        repo="openclaw/nisto-home",
        gitea_url="http://example.com",
        token="secret",
        milestone_title=bpi.PHASE3_MILESTONE,
        depends_on=21,
        dry_run=False,
        requester=requester,
    )

    assert len(results) == 1
    assert results[0]["number"] == 123

    # Create issue payload contains milestone and dependency marker
    create_request = requester.requests[2]
    payload = json.loads(create_request.data.decode())
    assert payload["milestone"] == 99
    assert "Depends on: #21" in payload["body"]
    assert payload["labels"] == ["needs-pr"]

    dep_request = requester.requests[3]
    dep_payload = json.loads(dep_request.data.decode())
    assert dep_payload["index"] == 21

    comment_request = requester.requests[4]
    comment_payload = json.loads(comment_request.data.decode())
    assert "Assignee: @builder" in comment_payload["body"]
    assert "builder owns automation" in comment_payload["body"]


def test_skips_existing_issue_by_title(spec_file: Path):
    requester = FakeRequester()
    requester.queue([{"id": 42, "title": bpi.PHASE3_MILESTONE}])  # milestones
    requester.queue([{"title": "Bootstrap Phase Issue", "number": 5}])  # existing issue

    results = bpi.run_bootstrap(
        spec_path=str(spec_file),
        repo="openclaw/nisto-home",
        gitea_url="http://example.com",
        token="secret",
        milestone_title=bpi.PHASE3_MILESTONE,
        depends_on=21,
        dry_run=False,
        requester=requester,
    )

    assert results == []
    # Only milestone + list issues requests were made
    assert len(requester.requests) == 2
