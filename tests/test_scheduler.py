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

    def test_compute_ready_queue_uses_topological_bounding(self):
        # 3 issues: 
        # #1: TAN (Source)
        # #2: Work (between #1 and #3)
        # #3: MOLT (Sink)
        # #4: Random issue (not in active subgraph)
        
        self.client_mock.list_issues.return_value = [
            {"number": 1, "title": "[TAN] Phase 4 Start", "labels": [{"name": "tan"}]},
            {"number": 2, "title": "Real Work", "labels": [{"name": "needs-pr"}]},
            {"number": 3, "title": "[TERMINAL] Phase 4 End", "labels": [{"name": "molt"}]},
            {"number": 4, "title": "Other Stuff", "labels": [{"name": "needs-pr"}]}
        ]
        
        def mock_request(method, path, data=None):
            if "issues/1/dependencies" in path: return []
            if "issues/2/dependencies" in path: return [{"number": 1, "state": "closed"}]
            if "issues/3/dependencies" in path: return [{"number": 2, "state": "open"}]
            if "issues/4/dependencies" in path: return []
            if "issues/1" in path: return {"number": 1, "title": "[TAN] Phase 4 Start"}
            if "issues/2" in path: return {"number": 2, "title": "Real Work"}
            if "issues/3" in path: return {"number": 3, "title": "[TERMINAL] Phase 4 End"}
            return []
            
        self.client_mock._request.side_effect = mock_request
        
        ready = self.scheduler.compute_ready_queue()
        
        # Only Issue 2 should be ready. 
        # Issue 1 is tan, but usually doesn't have needs-pr.
        # Issue 3 is blocked by 2.
        # Issue 4 is not between 1 and 3.
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["number"], 2)

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
