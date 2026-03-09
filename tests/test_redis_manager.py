import unittest
import json
from unittest.mock import MagicMock, patch

from carapace.core.queue import run_daemon


class TestQueueDaemon(unittest.TestCase):
    @patch("carapace.core.queue.GiteaClient")
    @patch("carapace.core.queue.WorkerPool")
    @patch("carapace.core.queue.Scheduler")
    @patch("redis.from_url")
    @patch("time.sleep", side_effect=InterruptedError)  # Exit loop after one iteration
    def test_run_daemon_updates_zset(self, mock_sleep, mock_redis_from_url, mock_scheduler_cls, mock_pool_cls, mock_gitea_cls):
        mock_client = MagicMock()
        mock_gitea_cls.return_value = mock_client

        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler

        mock_scheduler.compute_ready_queue.return_value = [
            {"number": 101, "title": "High Priority Task"},
            {"number": 102, "title": "Medium Priority Task"},
        ]

        mock_redis = MagicMock()
        mock_redis_from_url.return_value = mock_redis
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline

        try:
            run_daemon(
                gitea_url="http://gitea.test",
                token="test-token",
                repo="test/repo",
                redis_url="redis://localhost:6379",
                poll_interval=5,
            )
        except InterruptedError:
            pass

        mock_scheduler.compute_ready_queue.assert_called_once()

        mock_pipeline.delete.assert_called_with("carapace:queue:test/repo")
        zset_payload = mock_pipeline.zadd.call_args[0][1]
        self.assertEqual(len(zset_payload), 2)
        members = list(zset_payload.keys())
        scores = list(zset_payload.values())
        self.assertEqual(scores, [2.0, 1.0])
        decoded = [json.loads(member) for member in members]
        self.assertEqual(
            decoded[0]["identity"],
            {"forge": "gitea", "repo": "test/repo", "number": 101},
        )
        self.assertEqual(
            decoded[1]["identity"],
            {"forge": "gitea", "repo": "test/repo", "number": 102},
        )
        mock_pipeline.execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
