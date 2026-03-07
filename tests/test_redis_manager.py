import unittest
from unittest.mock import MagicMock, patch
from carapace.cli.redis_manager import run_manager

class TestRedisManager(unittest.TestCase):
    @patch("carapace.cli.redis_manager.GiteaClient")
    @patch("carapace.cli.redis_manager.WorkerPool")
    @patch("carapace.cli.redis_manager.Scheduler")
    @patch("redis.from_url")
    @patch("time.sleep", side_effect=InterruptedError) # Exit loop after one iteration
    def test_run_manager_updates_zset(self, mock_sleep, mock_redis_from_url, mock_scheduler_cls, mock_pool_cls, mock_gitea_cls):
        # Setup mocks
        mock_client = MagicMock()
        mock_gitea_cls.return_value = mock_client
        
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        
        # Mock ready issues
        mock_scheduler.compute_ready_queue.return_value = [
            {"number": 101, "title": "High Priority Task"},
            {"number": 102, "title": "Medium Priority Task"}
        ]
        
        mock_redis = MagicMock()
        mock_redis_from_url.return_value = mock_redis
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline
        
        # Run manager (it will hit InterruptedError on first sleep)
        try:
            run_manager(
                gitea_url="http://gitea.test",
                token="test-token",
                repo="test/repo",
                redis_url="redis://localhost:6379",
                poll_interval=5
            )
        except InterruptedError:
            pass
            
        # Verify Scheduler was called
        mock_scheduler.compute_ready_queue.assert_called_once()
        
        # Verify Redis updates
        # Highest priority (index 0) gets highest score (count - index)
        # 2 issues: #101 score 2.0, #102 score 1.0
        expected_zadd = {"101": 2.0, "102": 1.0}
        
        mock_pipeline.delete.assert_called_with("carapace:queue:test/repo")
        mock_pipeline.zadd.assert_called_with("carapace:queue:test/repo", expected_zadd)
        mock_pipeline.execute.assert_called_once()

if __name__ == "__main__":
    unittest.main()
