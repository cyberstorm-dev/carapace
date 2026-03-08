import io
import unittest
from argparse import Namespace
from unittest.mock import patch

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
            "http://gitea.test", "token", "repo/name", "redis://localhost:6379/0", 30
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
        self.assertIn("Missing REDIS_URL", fake_stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
