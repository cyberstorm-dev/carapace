import json
import unittest
from unittest.mock import MagicMock, patch
from carapace.gt import GiteaClient


class TestGT(unittest.TestCase):
    def setUp(self):
        self.token = "test-token"
        self.repo = "owner/repo"
        self.client = GiteaClient("http://localhost", self.token, self.repo)

    @patch("urllib.request.urlopen")
    def test_add_dependency_prevents_duplicate(self, mock_urlopen):
        """Should check for existing dependencies and not POST if present."""
        # Mock GET returns an existing dependency #20
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
        # Mock GET returns a dependency issue #20
        mock_get = MagicMock()
        mock_get.status = 200
        mock_get.read.return_value = json.dumps([{"number": 20}]).encode("utf-8")
        mock_get.__enter__.return_value = mock_get

        # Mock DELETE response
        mock_del = MagicMock()
        mock_del.status = 204
        mock_del.__enter__.return_value = mock_del

        mock_urlopen.side_effect = [mock_get, mock_del]

        self.client.remove_dependency(10, 20)

        # Verify the DELETE call
        last_call_req = mock_urlopen.call_args_list[-1][0][0]
        # URL should NOT have /20 at the end
        self.assertEqual(last_call_req.get_full_url(), "http://localhost/api/v1/repos/owner/repo/issues/10/dependencies")
        self.assertEqual(last_call_req.get_method(), "DELETE")
        
        # Body should contain the IssueMeta
        payload = json.loads(last_call_req.data.decode("utf-8"))
        self.assertEqual(payload, {"index": 20, "owner": "owner", "repo": "repo"})

if __name__ == "__main__":
    unittest.main()
