import unittest
from unittest.mock import patch, MagicMock
from carapace.worker.base import WorkerConfig
from carapace.worker.container import ContainerWorker

class TestContainerWorker(unittest.TestCase):
    @patch('subprocess.run')
    def test_run_success_parses_metrics(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = """
Some logs from the worker
--- METRICS_START ---
{"status": "success", "tokens": {"prompt": 100, "completion": 50}, "tool_calls": {"run_shell": 1}}
--- METRICS_END ---
"""
        mock_run.return_value = mock_result

        worker = ContainerWorker(image="test-image")
        config = WorkerConfig(
            issue_id=123,
            api_token="token",
            model="test-model"
        )
        
        result = worker.run(config)
        self.assertTrue(result.ok)
        self.assertEqual(result.tokens_prompt, 100)
        self.assertEqual(result.tokens_completion, 50)
        self.assertEqual(result.tool_calls.get("run_shell"), 1)
        
        # Verify subprocess was called correctly
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], "docker")
        self.assertEqual(cmd[1], "run")
        self.assertIn("ISSUE_NUMBER=123", cmd)

if __name__ == '__main__':
    unittest.main()
