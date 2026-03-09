import io
import json
import unittest
from argparse import Namespace
from unittest.mock import MagicMock, patch

from carapace.core import queue


class TestQueueCLI(unittest.TestCase):
    @patch("carapace.core.queue.run_daemon")
    def test_daemon_invokes_helper(self, mock_run_daemon):
        args = Namespace(
            gitea_url="http://gitea.test",
            token="token",
            repo="repo/name",
            redis_url="redis://localhost:6379/0",
            poll_interval=30,
            daemon=True,
            milestone=None,
            assignee=None,
            claim=False,
        )

        code = queue.run(args)

        self.assertEqual(code, 0)
        mock_run_daemon.assert_called_once_with(
            "http://gitea.test", "token", "repo/name", "redis://localhost:6379/0", 30, "strict"
        )

    def test_daemon_requires_redis_url(self):
        args = Namespace(
            gitea_url="http://gitea.test",
            token="token",
            repo="repo/name",
            redis_url=None,
            poll_interval=60,
            daemon=True,
            milestone=None,
            assignee=None,
            claim=False,
        )

        with patch("sys.stdout", new_callable=io.StringIO) as fake_stdout:
            code = queue.run(args)

        self.assertEqual(code, 1)
        self.assertIn("Redis URL is required for daemon mode", fake_stdout.getvalue())

    @patch("redis.from_url")
    @patch("carapace.core.queue.GiteaClient")
    @patch("carapace.core.queue.WorkerPool")
    def test_redis_queue_mode_emits_queue_items_contract(self, mock_pool_cls, mock_client_cls, mock_redis_from_url):
        mock_client_cls.return_value = MagicMock()
        mock_pool_cls.return_value = MagicMock()
        mock_redis = MagicMock()
        mock_redis_from_url.return_value = mock_redis
        member = json.dumps(
            {
                "identity": {"forge": "gitea", "repo": "repo/name", "number": 282},
                "title": "Test work item",
                "reasons": ["dependencies_clear"],
                "upstream": [],
                "downstream": [],
                "next_actions": [{"action": "begin_work"}],
            }
        )
        mock_redis.zrevrange.return_value = [(member, 5.0)]

        args = Namespace(
            gitea_url="http://gitea.test",
            token="token",
            repo="repo/name",
            redis_url="redis://localhost:6379/0",
            poll_interval=60,
            daemon=False,
            milestone=None,
            assignee=None,
            claim=False,
        )

        with patch("sys.stdout", new_callable=io.StringIO) as fake_stdout:
            code = queue.run(args)

        self.assertEqual(code, 0)
        out = fake_stdout.getvalue()
        self.assertIn("queue_items:", out)
        self.assertIn("count: 1", out)
        self.assertNotIn("ready_issues", out)


if __name__ == "__main__":
    unittest.main()
