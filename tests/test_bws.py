import unittest
from unittest.mock import MagicMock, patch

from carapace.cli.bws import run_cli


class TestBWS(unittest.TestCase):
    @patch("carapace.cli.bws.list_secrets")
    def test_list_uses_project_from_env(self, mock_list_secrets):
        mock_list_secrets.return_value = []
        with patch.dict("os.environ", {"CARAPACE_BWS_PROJECT_ID": "11111111-1111-1111-1111-111111111111"}):
            result = run_cli(["list"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["project_id"], "11111111-1111-1111-1111-111111111111")

    @patch("carapace.cli.bws.resolve_bws_binary", return_value="/usr/local/bin/bws-real")
    @patch("carapace.cli.bws.subprocess.run")
    def test_passthrough_proxy_uses_real_bws_binary(self, mock_run, _):
        mock_proc = MagicMock()
        mock_proc.stdout = '{"id": "abc", "key": "token"}\n'
        mock_run.return_value = mock_proc

        result = run_cli(["secret", "list", "11111111-1111-1111-1111-111111111111"])

        self.assertTrue(result["ok"])
        self.assertIn("proxy", result["result"])
        self.assertEqual(result["command"], "carapace-bws secret list 11111111-1111-1111-1111-111111111111")
        self.assertEqual(mock_run.call_args_list[0][0][0][0], "/usr/local/bin/bws-real")
        self.assertEqual(mock_run.call_args_list[0][0][0][1:], ["secret", "list", "11111111-1111-1111-1111-111111111111"])


if __name__ == "__main__":
    unittest.main()
