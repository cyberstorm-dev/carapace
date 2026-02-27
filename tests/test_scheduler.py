import unittest
from unittest.mock import MagicMock
from carapace.scheduler import Scheduler
from carapace.worker.pool import WorkerPool, APIKeyPool, APIKey
from carapace.worker.base import WorkerResult

class TestScheduler(unittest.TestCase):
    def setUp(self):
        self.client_mock = MagicMock()
        self.client_mock.repo_full_name = "test/repo"
        
        self.key_pool = APIKeyPool([APIKey(label="test", gemini_key="123", gitea_token="abc")])
        self.worker_pool_mock = MagicMock(spec=WorkerPool)
        self.worker_pool_mock.max_parallel = 1
        self.worker_pool_mock.dispatch.return_value = [WorkerResult(ok=True)]
        
        self.scheduler = Scheduler(self.client_mock, self.worker_pool_mock, milestone="3")

    def test_compute_ready_queue_skips_open_deps(self):
        # 2 issues: #1 has closed dep, #2 has open dep
        self.client_mock.list_issues.return_value = [
            {"number": 1, "title": "Issue 1"},
            {"number": 2, "title": "Issue 2"}
        ]
        
        def mock_request(method, path, data=None):
            if "issues/1/dependencies" in path:
                return [{"state": "closed"}]
            if "issues/2/dependencies" in path:
                return [{"state": "open"}]
            return []
            
        self.client_mock._request.side_effect = mock_request
        
        ready = self.scheduler.compute_ready_queue()
        
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["number"], 1)

    def test_auto_merge_requires_passing_ci(self):
        # PR #1 is approved but CI failed. PR #2 is approved and CI passed.
        self.client_mock.list_issues.return_value = [
            {"number": 1, "pull_request": True},
            {"number": 2, "pull_request": True}
        ]
        
        def mock_request(method, path, data=None):
            if "reviews" in path:
                return [{"state": "APPROVED"}]
            
            if "pulls/1" in path and "reviews" not in path:
                return {"head": {"sha": "failsha"}}
            if "commits/failsha/status" in path:
                return {"state": "failure"}
                
            if "pulls/2" in path and "reviews" not in path:
                return {"head": {"sha": "passsha"}}
            if "commits/passsha/status" in path:
                return {"state": "success"}
                
            return {}
            
        self.client_mock._request.side_effect = mock_request
        
        self.scheduler.auto_merge_approved_prs()
        
        # Merge should only be called for PR 2
        calls = [c for c in self.client_mock._request.call_args_list if "merge" in str(c)]
        self.assertEqual(len(calls), 1)
        self.assertIn("pulls/2/merge", calls[0][0][1])

if __name__ == '__main__':
    unittest.main()
