import json
import os
import io
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import yaml

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

    @patch("urllib.request.urlopen")
    def test_request_pull_reviewer_posts_expected_payload(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"requested_reviewers": [{"login": "builder"}]}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = self.client.request_pull_reviewer(7, "builder")

        self.assertEqual(result["requested_reviewers"][0]["login"], "builder")
        req = mock_urlopen.call_args_list[0][0][0]
        self.assertEqual(
            req.get_full_url(),
            "http://localhost/api/v1/repos/owner/repo/pulls/7/requested_reviewers",
        )
        self.assertEqual(req.get_method(), "POST")
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload, {"reviewers": ["builder"]})

    @patch("urllib.request.urlopen")
    def test_close_pull_posts_expected_payload(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"number": 7, "state": "closed"}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = self.client.close_pull(7)

        self.assertEqual(result["state"], "closed")
        req = mock_urlopen.call_args_list[0][0][0]
        self.assertEqual(req.get_full_url(), "http://localhost/api/v1/repos/owner/repo/pulls/7")
        self.assertEqual(req.get_method(), "PATCH")
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload, {"state": "closed"})

    @patch("urllib.request.urlopen")
    def test_list_issue_comments_uses_expected_query(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps([{"id": 7, "body": "## Codex Workpad"}]).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        comments = self.client.list_issue_comments(12)

        self.assertEqual(comments[0]["id"], 7)
        req = mock_urlopen.call_args_list[0][0][0]
        self.assertEqual(
            req.get_full_url(),
            "http://localhost/api/v1/repos/owner/repo/issues/12/comments",
        )

    @patch("urllib.request.urlopen")
    def test_upsert_marker_comment_updates_existing_comment(self, mock_urlopen):
        mock_list = MagicMock()
        mock_list.status = 200
        mock_list.read.return_value = json.dumps(
            [{"id": 44, "body": "## Codex Workpad\nold body"}]
        ).encode("utf-8")
        mock_list.__enter__.return_value = mock_list

        mock_patch = MagicMock()
        mock_patch.status = 200
        mock_patch.read.return_value = json.dumps(
            {"id": 44, "body": "## Codex Workpad\nnew body"}
        ).encode("utf-8")
        mock_patch.__enter__.return_value = mock_patch

        mock_urlopen.side_effect = [mock_list, mock_patch]

        result = self.client.upsert_issue_comment_marker(
            12, "## Codex Workpad", "## Codex Workpad\nnew body"
        )

        self.assertEqual(result["action"], "updated")
        self.assertEqual(result["comment_id"], 44)
        req = mock_urlopen.call_args_list[-1][0][0]
        self.assertEqual(
            req.get_full_url(),
            "http://localhost/api/v1/repos/owner/repo/issues/comments/44",
        )
        self.assertEqual(req.get_method(), "PATCH")
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload, {"body": "## Codex Workpad\nnew body"})

    @patch("urllib.request.urlopen")
    def test_upsert_marker_comment_creates_when_missing(self, mock_urlopen):
        mock_list = MagicMock()
        mock_list.status = 200
        mock_list.read.return_value = json.dumps(
            [{"id": 44, "body": "some other comment"}]
        ).encode("utf-8")
        mock_list.__enter__.return_value = mock_list

        mock_post = MagicMock()
        mock_post.status = 200
        mock_post.read.return_value = json.dumps(
            {"id": 55, "body": "## Codex Workpad\nnew body"}
        ).encode("utf-8")
        mock_post.__enter__.return_value = mock_post

        mock_urlopen.side_effect = [mock_list, mock_post]

        result = self.client.upsert_issue_comment_marker(
            12, "## Codex Workpad", "## Codex Workpad\nnew body"
        )

        self.assertEqual(result["action"], "created")
        self.assertEqual(result["comment_id"], 55)
        req = mock_urlopen.call_args_list[-1][0][0]
        self.assertEqual(
            req.get_full_url(),
            "http://localhost/api/v1/repos/owner/repo/issues/12/comments",
        )
        self.assertEqual(req.get_method(), "POST")
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload, {"body": "## Codex Workpad\nnew body"})

    @patch("urllib.request.urlopen")
    def test_assign_issue_posts_expected_payload(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"number": 12, "assignees": [{"login": "builder"}]}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = self.client.assign_issue(12, "builder")

        self.assertEqual(result["number"], 12)
        req = mock_urlopen.call_args_list[0][0][0]
        self.assertEqual(req.get_full_url(), "http://localhost/api/v1/repos/owner/repo/issues/12")
        self.assertEqual(req.get_method(), "PATCH")
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload, {"assignees": ["builder"]})

    @patch("urllib.request.urlopen")
    def test_unassign_issue_posts_empty_assignees_for_all(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"number": 12, "assignees": []}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = self.client.unassign_issue(12, all_assignees=True)

        self.assertEqual(result["number"], 12)
        req = mock_urlopen.call_args_list[0][0][0]
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload, {"assignees": []})

    def test_unassign_issue_requires_username_or_all_flag(self):
        with self.assertRaises(ValueError):
            self.client.unassign_issue(12)

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

    def test_resolve_connection_settings_reads_web_cookie_from_remote(self):
        config = {
            "default_remote": "cyberstorm",
            "remotes": {
                "cyberstorm": {
                    "url": "https://gitea.example",
                    "owner": "acme",
                    "repo": "widgets",
                    "token": "cfg-token",
                    "web_cookie": "lang=en-US;_csrf=testcsrf;session=abc",
                }
            },
        }
        args = gt.parse_args([])
        with patch.dict(os.environ, {}, clear=True):
            settings = gt.resolve_connection_settings(args, config=config)
        self.assertEqual(settings["web_cookie"], "lang=en-US;_csrf=testcsrf;session=abc")

    def test_resolve_connection_settings_reads_web_cookie_env_from_remote(self):
        config = {
            "default_remote": "cyberstorm",
            "remotes": {
                "cyberstorm": {
                    "url": "https://gitea.example",
                    "owner": "acme",
                    "repo": "widgets",
                    "token": "cfg-token",
                    "web_cookie_env": "ACME_WEB_COOKIE",
                }
            },
        }
        args = gt.parse_args([])
        with patch.dict(os.environ, {"ACME_WEB_COOKIE": "lang=en-US;_csrf=fromenv;session=xyz"}, clear=True):
            settings = gt.resolve_connection_settings(args, config=config)
        self.assertEqual(settings["web_cookie"], "lang=en-US;_csrf=fromenv;session=xyz")



    def test_parse_args_supports_project_add_command(self):
        args = gt.parse_args(["project", "add", "2", "73"])
        self.assertEqual(args.command, "project")
        self.assertEqual(args.project_action, "add")
        self.assertEqual(args.project_id, 2)
        self.assertEqual(args.issue, 73)

    def test_parse_args_supports_issue_comments_list(self):
        args = gt.parse_args(["issue", "comments", "list", "12"])
        self.assertEqual(args.command, "issue")
        self.assertEqual(args.issue_action, "comments")
        self.assertEqual(args.issue_comments_action, "list")
        self.assertEqual(args.issue, 12)

    def test_parse_args_supports_issue_comments_upsert_marker(self):
        args = gt.parse_args(
            [
                "issue",
                "comments",
                "upsert-marker",
                "12",
                "--marker",
                "## Codex Workpad",
                "--file",
                "body.md",
            ]
        )
        self.assertEqual(args.command, "issue")
        self.assertEqual(args.issue_action, "comments")
        self.assertEqual(args.issue_comments_action, "upsert-marker")
        self.assertEqual(args.issue, 12)
        self.assertEqual(args.marker, "## Codex Workpad")
        self.assertEqual(args.file, "body.md")

    def test_parse_args_supports_issue_assign(self):
        args = gt.parse_args(["issue", "assign", "12", "builder"])
        self.assertEqual(args.command, "issue")
        self.assertEqual(args.issue_action, "assign")
        self.assertEqual(args.issue, 12)
        self.assertEqual(args.username, "builder")

    def test_parse_args_supports_issue_unassign_all(self):
        args = gt.parse_args(["issue", "unassign", "12", "--all"])
        self.assertEqual(args.command, "issue")
        self.assertEqual(args.issue_action, "unassign")
        self.assertEqual(args.issue, 12)
        self.assertTrue(args.all)

    def test_read_body_prefers_file(self):
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as fh:
            fh.write("## Codex Workpad\nfrom file")
            fh.flush()
            with patch("sys.stdin.read", return_value="from stdin"):
                body = gt.read_body_from_args(file_path=fh.name)
        self.assertEqual(body, "## Codex Workpad\nfrom file")

    def test_read_body_falls_back_to_stdin(self):
        with patch("sys.stdin.read", return_value="## Codex Workpad\nfrom stdin"):
            body = gt.read_body_from_args()
        self.assertEqual(body, "## Codex Workpad\nfrom stdin")

    def test_read_body_rejects_empty_input(self):
        with patch("sys.stdin.read", return_value=""):
            with self.assertRaises(ValueError):
                gt.read_body_from_args()

    def test_parse_args_supports_issue_state(self):
        args = gt.parse_args(["issue", "state", "12", "--to", "In Progress"])
        self.assertEqual(args.command, "issue")
        self.assertEqual(args.issue_action, "state")
        self.assertEqual(args.issue, 12)
        self.assertEqual(args.to, "In Progress")

    def test_parse_args_supports_pr_request_reviewer(self):
        args = gt.parse_args(["pr", "request-reviewer", "7", "builder"])
        self.assertEqual(args.command, "pr")
        self.assertEqual(args.pr_action, "request-reviewer")
        self.assertEqual(args.pull, 7)
        self.assertEqual(args.username, "builder")

    def test_parse_args_supports_pr_close(self):
        args = gt.parse_args(["pr", "close", "7"])
        self.assertEqual(args.command, "pr")
        self.assertEqual(args.pr_action, "close")
        self.assertEqual(args.pull, 7)

    def test_parse_args_supports_project_cards(self):
        args = gt.parse_args(["project", "cards", "3", "--issue", "42"])
        self.assertEqual(args.command, "project")
        self.assertEqual(args.project_action, "cards")
        self.assertEqual(args.project_id, 3)
        self.assertEqual(args.issue, 42)

    def test_normalize_issue_state_target(self):
        self.assertEqual(gt.normalize_issue_state_target("backlog"), "Backlog")
        self.assertEqual(gt.normalize_issue_state_target("to do"), "To Do")
        self.assertEqual(gt.normalize_issue_state_target("In Progress"), "In Progress")
        self.assertEqual(gt.normalize_issue_state_target("duplicate"), "Duplicate")

    def test_find_default_kanban_project_returns_board_with_standard_columns(self):
        with patch.object(self.client, "list_projects", return_value=[{"id": 1}, {"id": 2}]):
            with patch.object(
                self.client,
                "list_project_columns",
                side_effect=[
                    [{"title": "Todo"}],
                    [
                        {"title": "Backlog"},
                        {"title": "To Do"},
                        {"title": "In Progress"},
                        {"title": "Done"},
                    ],
                ],
            ):
                project = self.client.find_default_kanban_project()
        self.assertEqual(project["id"], 2)

    def test_find_default_kanban_project_raises_when_missing(self):
        with patch.object(self.client, "list_projects", return_value=[{"id": 1}]):
            with patch.object(self.client, "list_project_columns", return_value=[{"title": "Todo"}]):
                with self.assertRaises(RuntimeError):
                    self.client.find_default_kanban_project()

    def test_transition_issue_state_closes_terminal_states(self):
        with patch.object(self.client, "patch_issue", return_value={"number": 12, "state": "closed"}) as mock_patch:
            result = self.client.transition_issue_state(12, "Duplicate")
        mock_patch.assert_called_once_with(12, {"state": "closed"})
        self.assertEqual(result["state"], "Duplicate")
        self.assertEqual(result["issue_state"], "closed")

    def test_transition_issue_state_moves_issue_to_board_column(self):
        with patch.object(self.client, "find_default_kanban_project", return_value={"id": 3, "name": "Sprint"}):
            with patch.object(self.client, "list_project_cards", return_value=[]):
                with patch.object(self.client, "add_issue_to_project", return_value={"project_id": 3}) as mock_add:
                    with patch.object(self.client, "patch_issue", return_value={"number": 12, "state": "open"}) as mock_patch:
                        with patch.object(
                            self.client,
                            "move_issue_to_project_column",
                            return_value={"project_id": 3, "column_title": "In Progress"},
                        ) as mock_move:
                            result = self.client.transition_issue_state(12, "In Progress")
        mock_add.assert_called_once_with(3, 12)
        mock_patch.assert_called_once_with(12, {"state": "open"})
        mock_move.assert_called_once_with(3, 12, "In Progress")
        self.assertEqual(result["state"], "In Progress")
        self.assertEqual(result["project_id"], 3)

    @patch.object(gt.GiteaClient, "_web_request")
    def test_list_project_cards_parses_issue_membership(self, mock_web_request):
        mock_web_request.return_value = """
<div class="project-column" data-id="11">
  <div data-modal-project-column-id="11" data-modal-project-column-title-input="To Do"></div>
  <div class="project-card" data-issue-id="467">
    <a href="/owner/repo/issues/42">#42 Example</a>
  </div>
</div>
<div class="project-column" data-id="12">
  <div data-modal-project-column-id="12" data-modal-project-column-title-input="Done"></div>
  <div class="project-card" data-issue-id="468">
    <a href="/owner/repo/issues/43">#43 Finished</a>
  </div>
</div>
"""

        cards = self.client.list_project_cards(3)

        self.assertEqual(
            cards,
            [
                {
                    "project_id": 3,
                    "column_id": 11,
                    "column_title": "To Do",
                    "issue_id": 467,
                    "issue_number": 42,
                },
                {
                    "project_id": 3,
                    "column_id": 12,
                    "column_title": "Done",
                    "issue_id": 468,
                    "issue_number": 43,
                },
            ],
        )

    @patch.object(gt.GiteaClient, "_web_request")
    def test_list_project_cards_filters_by_issue(self, mock_web_request):
        mock_web_request.return_value = """
<div class="project-column" data-id="11">
  <div data-modal-project-column-id="11" data-modal-project-column-title-input="To Do"></div>
  <div class="project-card" data-issue-id="467">
    <a href="/owner/repo/issues/42">#42 Example</a>
  </div>
</div>
"""

        cards = self.client.list_project_cards(3, issue_number=42)

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["issue_number"], 42)

    @patch.object(gt, "resolve_connection_settings")
    @patch.object(gt.GiteaClient, "list_issue_comments")
    def test_main_issue_comments_list_returns_comment_count(
        self, mock_list_comments, mock_resolve_connection_settings
    ):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": None,
            "remote": None,
        }
        mock_list_comments.return_value = [{"id": 7, "body": "## Codex Workpad"}]

        with patch("sys.argv", ["gt", "issue", "comments", "list", "12"]):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        payload = yaml.safe_load(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["count"], 1)
        self.assertEqual(payload["result"]["comments"][0]["id"], 7)

    @patch.object(gt, "read_body_from_args")
    @patch.object(gt, "resolve_connection_settings")
    @patch.object(gt.GiteaClient, "upsert_issue_comment_marker")
    def test_main_issue_comments_upsert_marker_returns_action_and_comment_id(
        self,
        mock_upsert,
        mock_resolve_connection_settings,
        mock_read_body,
    ):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": None,
            "remote": None,
        }
        mock_read_body.return_value = "## Codex Workpad\nupdated"
        mock_upsert.return_value = {"action": "updated", "comment_id": 44, "comment": {"id": 44}}

        with patch(
            "sys.argv",
            [
                "gt",
                "issue",
                "comments",
                "upsert-marker",
                "12",
                "--marker",
                "## Codex Workpad",
            ],
        ):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        payload = yaml.safe_load(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["action"], "updated")
        self.assertEqual(payload["result"]["comment_id"], 44)

    @patch.object(gt, "resolve_connection_settings")
    @patch.object(gt.GiteaClient, "assign_issue")
    def test_main_issue_assign_returns_updated_assignee(
        self, mock_assign_issue, mock_resolve_connection_settings
    ):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": None,
            "remote": None,
        }
        mock_assign_issue.return_value = {
            "number": 12,
            "assignees": [{"login": "builder"}],
        }

        with patch("sys.argv", ["gt", "issue", "assign", "12", "builder"]):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        payload = yaml.safe_load(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["issue"]["number"], 12)
        self.assertEqual(payload["result"]["issue"]["assignees"], ["builder"])

    @patch.object(gt, "resolve_connection_settings")
    @patch.object(gt.GiteaClient, "assign_issue")
    def test_main_issue_assign_surfaces_gitea_api_errors(
        self, mock_assign_issue, mock_resolve_connection_settings
    ):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": None,
            "remote": None,
        }
        mock_assign_issue.side_effect = gt.GiteaAPIError("user not found", 422, "Unprocessable Entity")

        with patch("sys.argv", ["gt", "issue", "assign", "12", "missing-user"]):
            with self.assertRaises(SystemExit) as exc:
                with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    gt.main()

        self.assertEqual(exc.exception.code, 1)
        payload = yaml.safe_load(stdout.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["message"], "user not found")
        self.assertEqual(payload["error"]["code"], 422)
        self.assertEqual(payload["error"]["reason"], "Unprocessable Entity")

    # Project remove ---------------------------------------------------

    @patch.object(gt, "resolve_connection_settings")
    def test_project_remove_requires_project_or_use_default(self, mock_resolve_connection_settings):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": None,
            "remote": None,
        }

        with patch("sys.argv", ["gt", "project", "remove", "7"]):
            with self.assertRaises(SystemExit) as exc, patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        self.assertEqual(exc.exception.code, 1)
        payload = yaml.safe_load(stdout.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("project id", payload["error"]["message"].lower())

    @patch.object(gt, "resolve_connection_settings")
    @patch.object(gt.GiteaClient, "find_default_kanban_project")
    def test_project_remove_default_missing(self, mock_find_default, mock_resolve_connection_settings):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": None,
            "remote": None,
        }
        mock_find_default.side_effect = ValueError("No default kanban project found with columns: Backlog, To Do, In Progress, Done")

        with patch("sys.argv", ["gt", "project", "remove", "7", "--use-default"]):
            with self.assertRaises(SystemExit) as exc, patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        self.assertEqual(exc.exception.code, 1)
        payload = yaml.safe_load(stdout.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("default kanban", payload["error"]["message"].lower())

    @patch.object(gt, "resolve_connection_settings")
    @patch.object(gt.GiteaClient, "list_project_cards")
    def test_project_remove_missing_card(self, mock_list_cards, mock_resolve_connection_settings):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": "cookie",
            "remote": None,
        }
        mock_list_cards.return_value = []

        with patch("sys.argv", ["gt", "project", "remove", "7", "--project-id", "3"]):
            with self.assertRaises(SystemExit) as exc, patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        self.assertEqual(exc.exception.code, 1)
        payload = yaml.safe_load(stdout.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("not on project", payload["error"]["message"].lower())

    @patch.object(gt, "resolve_connection_settings")
    @patch.object(gt.GiteaClient, "list_project_cards")
    def test_project_remove_duplicate_cards(self, mock_list_cards, mock_resolve_connection_settings):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": "cookie",
            "remote": None,
        }
        mock_list_cards.return_value = [
            {"issue": 7, "project_id": 3, "column_title": "Backlog"},
            {"issue": 7, "project_id": 3, "column_title": "To Do"},
        ]

        with patch("sys.argv", ["gt", "project", "remove", "7", "--project-id", "3"]):
            with self.assertRaises(SystemExit) as exc, patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        self.assertEqual(exc.exception.code, 1)
        payload = yaml.safe_load(stdout.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("multiple cards", payload["error"]["message"].lower())

    @patch.object(gt, "resolve_connection_settings")
    @patch.object(gt.GiteaClient, "list_project_cards")
    @patch.object(gt.GiteaClient, "remove_issue_from_project")
    def test_project_remove_success(self, mock_remove, mock_list_cards, mock_resolve_connection_settings):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": "cookie",
            "remote": None,
        }
        mock_list_cards.return_value = [
            {"issue": 7, "project_id": 3, "column_title": "Backlog"}
        ]
        mock_remove.return_value = {"project_id": 3, "issue": 7}

        with patch("sys.argv", ["gt", "project", "remove", "7", "--project-id", "3"]):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        payload = yaml.safe_load(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["project_id"], 3)
        self.assertEqual(payload["result"]["issue"], 7)
        self.assertEqual(payload["result"]["removed_from"], "Backlog")

    @patch.object(gt, "resolve_connection_settings")
    @patch.object(gt.GiteaClient, "list_project_cards")
    @patch.object(gt.GiteaClient, "remove_issue_from_project")
    def test_project_remove_surfaces_api_error(self, mock_remove, mock_list_cards, mock_resolve_connection_settings):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": "cookie",
            "remote": None,
        }
        mock_list_cards.return_value = [
            {"issue": 7, "project_id": 3, "column_title": "Backlog"}
        ]
        mock_remove.side_effect = gt.GiteaAPIError("forbidden", 403, "Forbidden")

        with patch("sys.argv", ["gt", "project", "remove", "7", "--project-id", "3"]):
            with self.assertRaises(SystemExit) as exc, patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        self.assertEqual(exc.exception.code, 1)
        payload = yaml.safe_load(stdout.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["message"], "forbidden")
        self.assertEqual(payload["error"]["code"], 403)
        self.assertEqual(payload["error"]["reason"], "Forbidden")

    @patch.object(gt, "resolve_connection_settings")
    @patch.object(gt.GiteaClient, "transition_issue_state")
    def test_main_issue_state_returns_normalized_state(
        self, mock_transition_issue_state, mock_resolve_connection_settings
    ):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": None,
            "remote": None,
        }
        mock_transition_issue_state.return_value = {
            "issue_number": 12,
            "state": "In Progress",
            "project_id": 3,
            "column_title": "In Progress",
        }

        with patch("sys.argv", ["gt", "issue", "state", "12", "--to", "In Progress"]):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        payload = yaml.safe_load(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["state"], "In Progress")
        self.assertEqual(payload["result"]["project_id"], 3)

    @patch.object(gt, "resolve_connection_settings")
    @patch.object(gt.GiteaClient, "request_pull_reviewer")
    def test_main_pr_request_reviewer_returns_requested_reviewer(
        self, mock_request_reviewer, mock_resolve_connection_settings
    ):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": None,
            "remote": None,
        }
        mock_request_reviewer.return_value = {"requested_reviewers": [{"login": "builder"}]}

        with patch("sys.argv", ["gt", "pr", "request-reviewer", "7", "builder"]):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        payload = yaml.safe_load(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["pull"], 7)
        self.assertEqual(payload["result"]["requested_reviewers"], ["builder"])

    @patch.object(gt, "resolve_connection_settings")
    @patch.object(gt.GiteaClient, "close_pull")
    def test_main_pr_close_returns_closed_state(
        self, mock_close_pull, mock_resolve_connection_settings
    ):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": None,
            "remote": None,
        }
        mock_close_pull.return_value = {"number": 7, "state": "closed"}

        with patch("sys.argv", ["gt", "pr", "close", "7"]):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        payload = yaml.safe_load(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["pull"], 7)
        self.assertEqual(payload["result"]["state"], "closed")

    @patch.object(gt, "resolve_connection_settings")
    @patch.object(gt.GiteaClient, "list_project_cards")
    def test_main_project_cards_returns_card_rows(
        self, mock_list_project_cards, mock_resolve_connection_settings
    ):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": None,
            "remote": None,
        }
        mock_list_project_cards.return_value = [
            {
                "project_id": 3,
                "column_id": 11,
                "column_title": "To Do",
                "issue_id": 467,
                "issue_number": 42,
            }
        ]

        with patch("sys.argv", ["gt", "project", "cards", "3", "--issue", "42"]):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        payload = yaml.safe_load(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["project_id"], 3)
        self.assertEqual(payload["result"]["cards"][0]["issue_number"], 42)

    def test_parse_args_rejects_issue_unassign_username_with_all(self):
        args = gt.parse_args(["issue", "unassign", "12", "builder", "--all"])
        self.assertTrue(args.all)
        self.assertEqual(args.username, "builder")

    @patch.object(gt, "resolve_connection_settings")
    def test_main_issue_unassign_conflict_returns_yaml_error(self, mock_resolve_connection_settings):
        mock_resolve_connection_settings.return_value = {
            "url": "http://localhost",
            "repo": "owner/repo",
            "token": "test-token",
            "web_cookie": None,
            "remote": None,
        }

        with patch("sys.argv", ["gt", "issue", "unassign", "12", "builder", "--all"]):
            with self.assertRaises(SystemExit) as exc:
                with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    gt.main()

        self.assertEqual(exc.exception.code, 1)
        payload = yaml.safe_load(stdout.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("does not accept both", payload["error"]["message"])

    def test_main_command_tree_mentions_new_commands(self):
        with patch("sys.argv", ["gt"]):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        payload = yaml.safe_load(stdout.getvalue())
        command_names = {entry["name"] for entry in payload["result"]["commands"]}
        self.assertIn("issue state", command_names)
        self.assertIn("project cards", command_names)
        self.assertIn("pr request-reviewer", command_names)
        self.assertIn("pr close", command_names)

    def test_main_root_short_help_returns_yaml(self):
        with patch("sys.argv", ["gt", "-h"]):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        payload = yaml.safe_load(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "gt")

    def test_main_root_long_help_returns_yaml(self):
        with patch("sys.argv", ["gt", "--help"]):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        payload = yaml.safe_load(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "gt")

    def test_main_no_command_with_global_flag_returns_yaml(self):
        with patch("sys.argv", ["gt", "--config", "/tmp/example.toml"]):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                gt.main()

        payload = yaml.safe_load(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "gt")

    def test_main_invalid_subcommand_returns_yaml_error(self):
        with patch("sys.argv", ["gt", "issue", "frob"]):
            with self.assertRaises(SystemExit) as exc:
                with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    gt.main()

        self.assertEqual(exc.exception.code, 1)
        payload = yaml.safe_load(stdout.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("invalid choice", payload["error"]["message"])

    @patch.object(gt.GiteaClient, "_web_request")
    def test_list_project_cards_parses_live_issue_card_markup(self, mock_web_request):
        mock_web_request.return_value = """
<div class="project-column" data-id="8">
  <a data-modal-project-column-id="8" data-modal-project-column-title-input="Backlog"></a>
  <div class="ui cards" data-url="/cyberstorm/symphony/projects/2/8" data-project="2" data-board="8" id="board_8">
    <div class="issue-card tw-break-anywhere tw-cursor-grab" data-issue="469">
      <a class="issue-card-title muted issue-title tw-break-anywhere" href="/cyberstorm/symphony/issues/75">Example</a>
    </div>
  </div>
</div>
<div class="ui small modal" id="project-column-modal-edit"></div>
"""

        cards = self.client.list_project_cards(2)

        self.assertEqual(
            cards,
            [
                {
                    "project_id": 2,
                    "column_id": 8,
                    "column_title": "Backlog",
                    "issue_id": 469,
                    "issue_number": 75,
                }
            ],
        )

    @patch.object(gt.GiteaClient, "_web_request")
    @patch.object(gt.GiteaClient, "_issue_internal_id", return_value=467)
    def test_add_issue_to_project_posts_expected_web_payload(self, _mock_internal_id, mock_web_request):
        self.client.add_issue_to_project(2, 73)

        mock_web_request.assert_called_once_with(
            "POST",
            "issues/projects?issue_ids=467",
            "id=2",
            content_type="application/x-www-form-urlencoded; charset=UTF-8",
        )
if __name__ == "__main__":
    unittest.main()
