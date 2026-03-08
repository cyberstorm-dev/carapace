import json
import unittest
from unittest.mock import MagicMock, patch
from carapace.cli.gt import GiteaClient


class TestGT(unittest.TestCase):
    def setUp(self):
        self.token = "test-token"
        self.repo = "owner/repo"
        self.client = GiteaClient("http://localhost", self.token, self.repo)

    @patch("urllib.request.urlopen")
    def test_add_dependency_prevents_duplicate(self, mock_urlopen):
        """Should check for existing dependencies and not POST if present."""
        # Mock GET returns an existing dependency #20
        mock_get = MagicMock()
        mock_get.status = 200
        mock_get.read.return_value = json.dumps([{"number": 20}]).encode("utf-8")
        mock_get.__enter__.return_value = mock_get

        mock_urlopen.side_effect = [mock_get]

        result = self.client.add_dependency(10, 20)
        self.assertEqual(result.get("message"), "Dependency already exists")

    @patch("urllib.request.urlopen")
    def test_remove_dependency_sends_body(self, mock_urlopen):
        """GREEN: Should send IssueMeta in body of DELETE request."""
        # Mock GET returns a dependency issue #20
        mock_get = MagicMock()
        mock_get.status = 200
        mock_get.read.return_value = json.dumps([{"number": 20}]).encode("utf-8")
        mock_get.__enter__.return_value = mock_get

        # Mock DELETE response
        mock_del = MagicMock()
        mock_del.status = 204
        mock_del.__enter__.return_value = mock_del

        mock_urlopen.side_effect = [mock_get, mock_del]

        self.client.remove_dependency(10, 20)

        # Verify the DELETE call
        last_call_req = mock_urlopen.call_args_list[-1][0][0]
        # URL should NOT have /20 at the end
        self.assertEqual(last_call_req.get_full_url(), "http://localhost/api/v1/repos/owner/repo/issues/10/dependencies")
        self.assertEqual(last_call_req.get_method(), "DELETE")
        
        # Body should contain the IssueMeta
        payload = json.loads(last_call_req.data.decode("utf-8"))
        self.assertEqual(payload, {"index": 20, "owner": "owner", "repo": "repo"})

    @patch("urllib.request.urlopen")
    def test_dependency_reference_parses_cross_repo(self, mock_urlopen):
        """Should accept owner/repo#number dependency references."""
        mock_get = MagicMock()
        mock_get.status = 200
        mock_get.read.return_value = json.dumps([]).encode("utf-8")
        mock_get.__enter__.return_value = mock_get

        mock_post = MagicMock()
        mock_post.status = 204
        mock_post.read.return_value = b""
        mock_post.__enter__.return_value = mock_post

        mock_urlopen.side_effect = [mock_get, mock_post]

        self.client.add_dependency(10, "acme/other#123")

        post_request = mock_urlopen.call_args_list[-1][0][0]
        self.assertEqual(
            post_request.get_full_url(),
            "http://localhost/api/v1/repos/owner/repo/issues/10/dependencies",
        )
        self.assertEqual(post_request.get_method(), "POST")
        payload = json.loads(post_request.data.decode("utf-8"))
        self.assertEqual(payload, {"index": 123, "owner": "acme", "repo": "other"})

    @patch("urllib.request.urlopen")
    def test_list_issues_filters_by_assignee(self, mock_urlopen):
        """List command should return only matching assignee even if API ignores filter param."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(
            [
                {"number": 1, "assignee": {"login": "builder"}},
                {"number": 2, "assignee": None},
                {"number": 3, "assignee": {"login": "other"}},
            ]
        ).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        mock_urlopen.return_value = mock_resp

        issues = self.client.list_issues(state="open", assignee="builder")

        self.assertEqual([issue["number"] for issue in issues], [1])
        self.assertEqual(issues[0]["assignee"]["login"], "builder")

        request = mock_urlopen.call_args_list[0][0][0]
        self.assertIn("assignee=builder", request.full_url)

if __name__ == "__main__":
    unittest.main()
