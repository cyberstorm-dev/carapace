import unittest
from unittest.mock import MagicMock, patch
from carapace.cli.gatus import run_gatus_check
from urllib.error import URLError

class TestGatusCheck(unittest.TestCase):
    @patch('carapace.cli.gatus.request.urlopen')
    @patch('carapace.cli.gatus.request.Request')
    def test_run_gatus_check_healthy(self, mock_request, mock_urlopen):
        # Mock healthy response for required nodes
        mock_response = MagicMock()
        mock_response.read.return_value = b"""
        [
            {"name": "cyberstorm-citadel-api", "group": "core", "results": [{"success": true}]},
            {"name": "infralink-dev-node", "group": "dev", "results": [{"success": false}]}
        ]
        """
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        result = run_gatus_check("http://test", ["cyberstorm-citadel", "cyberstorm-watchtower"])
        
        self.assertTrue(result["ok"])
        self.assertEqual(result["total_checked"], 1)
        self.assertEqual(result["message"], "All relevant endpoints are healthy")

    @patch('carapace.cli.gatus.request.urlopen')
    @patch('carapace.cli.gatus.request.Request')
    def test_run_gatus_check_unhealthy(self, mock_request, mock_urlopen):
        # Mock unhealthy response for a required node
        mock_response = MagicMock()
        mock_response.read.return_value = b"""
        [
            {"name": "cyberstorm-citadel-api", "group": "core", "results": [{"success": false, "errors": ["timeout"]}]},
            {"name": "cyberstorm-watchtower-db", "group": "db", "results": [{"success": true}]}
        ]
        """
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        result = run_gatus_check("http://test", ["cyberstorm-citadel", "cyberstorm-watchtower"])
        
        self.assertFalse(result["ok"])
        self.assertEqual(result["total_checked"], 2)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(len(result["failures"]), 1)
        self.assertEqual(result["failures"][0]["name"], "cyberstorm-citadel-api")

    @patch('carapace.cli.gatus.request.urlopen')
    @patch('carapace.cli.gatus.request.Request')
    def test_run_gatus_check_network_error(self, mock_request, mock_urlopen):
        mock_urlopen.side_effect = URLError("connection refused")
        
        with self.assertRaises(RuntimeError):
            run_gatus_check("http://test", ["cyberstorm-citadel"])

if __name__ == '__main__':
    unittest.main()
