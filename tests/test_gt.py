import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from carapace.cli import gt
from carapace.cli.gt import GiteaClient


class TestGT(unittest.TestCase):
    def setUp(self):
        self.token = "test-token"
        self.repo = "owner/repo"
        self.client = GiteaClient("http://localhost", self.token, self.repo)

    @patch("urllib.request.urlopen")
    def test_add_dependency_prevents_duplicate(self, mock_urlopen):
        """Should check for existing dependencies and not POST if present."""
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
        mock_get = MagicMock()
        mock_get.status = 200
        mock_get.read.return_value = json.dumps([{"number": 20}]).encode("utf-8")
        mock_get.__enter__.return_value = mock_get

        mock_del = MagicMock()
        mock_del.status = 204
        mock_del.__enter__.return_value = mock_del

        mock_urlopen.side_effect = [mock_get, mock_del]

        self.client.remove_dependency(10, 20)

        last_call_req = mock_urlopen.call_args_list[-1][0][0]
        self.assertEqual(
            last_call_req.get_full_url(),
            "http://localhost/api/v1/repos/owner/repo/issues/10/dependencies",
        )
        self.assertEqual(last_call_req.get_method(), "DELETE")

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

    @patch("urllib.request.urlopen")
    def test_list_pulls_uses_expected_query(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps([{"number": 10, "state": "open"}]).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        pulls = self.client.list_pulls(state="open")

        self.assertEqual(len(pulls), 1)
        req = mock_urlopen.call_args_list[0][0][0]
        self.assertIn("/api/v1/repos/owner/repo/pulls?state=open&limit=100", req.full_url)

    @patch("urllib.request.urlopen")
    def test_create_pull_posts_expected_payload(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"number": 22, "html_url": "http://example/pr/22"}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = self.client.create_pull(
            title="feat: x",
            head="issue-2-fix",
            base="main",
            body="summary",
        )

        self.assertEqual(result["number"], 22)
        req = mock_urlopen.call_args_list[0][0][0]
        self.assertEqual(req.get_full_url(), "http://localhost/api/v1/repos/owner/repo/pulls")
        self.assertEqual(req.get_method(), "POST")
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(
            payload,
            {"title": "feat: x", "head": "issue-2-fix", "base": "main", "body": "summary"},
        )

    @patch("urllib.request.urlopen")
    def test_submit_pull_review_posts_expected_payload(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"id": 99, "state": "APPROVED"}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = self.client.submit_pull_review(7, "APPROVED", "looks good")

        self.assertEqual(result["state"], "APPROVED")
        req = mock_urlopen.call_args_list[0][0][0]
        self.assertEqual(req.get_full_url(), "http://localhost/api/v1/repos/owner/repo/pulls/7/reviews")
        self.assertEqual(req.get_method(), "POST")
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload, {"event": "APPROVED", "body": "looks good"})

    @patch("urllib.request.urlopen")
    def test_merge_pull_posts_expected_payload(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"sha": "abc123", "merged": True}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = self.client.merge_pull(7, merge_method="squash", commit_title="merge title")

        self.assertTrue(result["merged"])
        req = mock_urlopen.call_args_list[0][0][0]
        self.assertEqual(req.get_full_url(), "http://localhost/api/v1/repos/owner/repo/pulls/7/merge")
        self.assertEqual(req.get_method(), "POST")
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload, {"Do": "squash", "MergeTitleField": "merge title"})

    def test_load_gt_config_reads_named_remotes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = os.path.join(tmpdir, "gt.toml")
            with open(cfg_path, "w", encoding="utf-8") as fh:
                fh.write(
                    """
default_remote = "cyberstorm"

[remotes.cyberstorm]
url = "https://gitea.example"
owner = "acme"
repo = "widgets"
token_env = "ACME_TOKEN"
"""
                )
            config = gt.load_gt_config(cfg_path)
            self.assertEqual(config.get("default_remote"), "cyberstorm")
            self.assertIn("cyberstorm", config.get("remotes", {}))

    def test_resolve_connection_settings_uses_default_remote_from_config(self):
        config = {
            "default_remote": "cyberstorm",
            "remotes": {
                "cyberstorm": {
                    "url": "https://gitea.example",
                    "owner": "acme",
                    "repo": "widgets",
                    "token_env": "ACME_TOKEN",
                }
            },
        }
        args = gt.parse_args([])
        with patch.dict(os.environ, {"ACME_TOKEN": "from-config"}, clear=True):
            settings = gt.resolve_connection_settings(args, config=config)
        self.assertEqual(settings["url"], "https://gitea.example")
        self.assertEqual(settings["repo"], "acme/widgets")
        self.assertEqual(settings["token"], "from-config")

    def test_resolve_connection_settings_prefers_env_over_config(self):
        config = {
            "default_remote": "cyberstorm",
            "remotes": {
                "cyberstorm": {
                    "url": "https://gitea.example",
                    "owner": "acme",
                    "repo": "widgets",
                    "token_env": "ACME_TOKEN",
                }
            },
        }
        args = gt.parse_args([])
        env = {
            "GITEA_URL": "https://env.example",
            "GITEA_REPO": "env/repo",
            "GITEA_TOKEN": "env-token",
            "ACME_TOKEN": "from-config",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = gt.resolve_connection_settings(args, config=config)
        self.assertEqual(settings["url"], "https://env.example")
        self.assertEqual(settings["repo"], "env/repo")
        self.assertEqual(settings["token"], "env-token")

    def test_resolve_connection_settings_prefers_cli_over_env_and_config(self):
        config = {
            "default_remote": "cyberstorm",
            "remotes": {
                "cyberstorm": {
                    "url": "https://gitea.example",
                    "owner": "acme",
                    "repo": "widgets",
                    "token_env": "ACME_TOKEN",
                }
            },
        }
        args = gt.parse_args([
            "--url",
            "https://cli.example",
            "--repo",
            "cli/repo",
            "--token",
            "cli-token",
        ])
        env = {
            "GITEA_URL": "https://env.example",
            "GITEA_REPO": "env/repo",
            "GITEA_TOKEN": "env-token",
            "ACME_TOKEN": "from-config",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = gt.resolve_connection_settings(args, config=config)
        self.assertEqual(settings["url"], "https://cli.example")
        self.assertEqual(settings["repo"], "cli/repo")
        self.assertEqual(settings["token"], "cli-token")


if __name__ == "__main__":
    unittest.main()
