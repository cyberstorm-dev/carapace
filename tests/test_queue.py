import unittest
import argparse
from unittest.mock import MagicMock, patch

from carapace.core import queue

class TestQueue(unittest.TestCase):
    @patch("carapace.core.queue.Scheduler")
    @patch("carapace.core.queue.GiteaClient")
    @patch("carapace.core.queue.dump_yaml")
    def test_queue_filters_by_assignee_but_includes_unassigned(self, mock_dump, mock_client_cls, mock_scheduler_cls):
        """Should return issues assigned to args.assignee AND unassigned issues."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        mock_scheduler = MagicMock()
        mock_scheduler.fetch_dag.return_value = MagicMock()
        
        # 3 issues in ready queue:
        # #1: Assigned to builder
        # #2: Assigned to someone_else
        # #3: Unassigned
        mock_scheduler.compute_ready_queue.return_value = [
            {"number": 1, "title": "t1", "assignees": [{"login": "builder"}]},
            {"number": 2, "title": "t2", "assignees": [{"login": "reviewer"}]},
            {"number": 3, "title": "t3", "assignees": []},
            {"number": 4, "title": "t4"}
        ]
        mock_scheduler_cls.return_value = mock_scheduler
        
        # Patch calculate_priority to return dummy scores
        with patch("carapace.core.queue.calculate_priority", return_value={1: 10, 2: 10, 3: 10, 4: 10}):
            with patch("carapace.core.queue.get_active_subgraph", return_value=[]):
                args = argparse.Namespace(
                    gitea_url="url", token="tok", repo="repo", assignee="builder", claim=False, milestone=None
                )
                queue.run(args)
                
        # Get the output payload passed to dump_yaml
        payload = mock_dump.call_args[0][0]
        self.assertTrue(payload["ok"], payload.get("error"))
        
        result_issues = payload["result"].get("ready_issues", [])
        numbers = [i["number"] for i in result_issues]
        
        # Should include 1 (assigned to me), 3 (unassigned), 4 (unassigned)
        # Should exclude 2 (assigned to someone else)
        self.assertIn(1, numbers)
        self.assertIn(3, numbers)
        self.assertIn(4, numbers)
        self.assertNotIn(2, numbers)

    @patch("carapace.core.queue.Scheduler")
    @patch("carapace.core.queue.GiteaClient")
    @patch("carapace.core.queue.dump_yaml")
    def test_queue_claim_assigns_issue(self, mock_dump, mock_client_cls, mock_scheduler_cls):
        """When --claim is passed, it should assign the issue to args.assignee in Gitea."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        mock_scheduler = MagicMock()
        mock_scheduler.fetch_dag.return_value = MagicMock()
        
        # 1 unassigned issue
        mock_scheduler.compute_ready_queue.return_value = [
            {"number": 42, "title": "test", "assignees": []}
        ]
        mock_scheduler_cls.return_value = mock_scheduler
        
        with patch("carapace.core.queue.calculate_priority", return_value={42: 10}):
            with patch("carapace.core.queue.get_active_subgraph", return_value=[]):
                args = argparse.Namespace(
                    gitea_url="url", token="tok", repo="repo", assignee="builder", claim=True, milestone=None
                )
                queue.run(args)
                
        # Should have called add_label and assign_issue
        mock_client.add_label.assert_called_with(42, 7)
        mock_client.assign_issue.assert_called_with(42, ["builder"])

if __name__ == "__main__":
    unittest.main()
