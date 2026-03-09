import unittest
from argparse import Namespace
from unittest.mock import MagicMock, patch

from carapace.cli import trigger


class TestTriggerQueueContract(unittest.TestCase):
    def test_extract_queue_issue_refs_filters_by_repo(self):
        members = [
            '{"identity":{"forge":"gitea","repo":"openclaw/nisto-home","number":282}}',
            '{"identity":{"forge":"github","repo":"cyberstorm-dev/carapace","number":1}}',
            '{"identity":{"forge":"gitea","repo":"openclaw/nisto-home","number":283}}',
        ]

        refs = trigger._extract_queue_issue_refs(members, default_forge="gitea")
        self.assertEqual(
            refs,
            [
                ("gitea", "openclaw/nisto-home", 282),
                ("github", "cyberstorm-dev/carapace", 1),
                ("gitea", "openclaw/nisto-home", 283),
            ],
        )

    def test_build_queue_next_actions_includes_graph_traversal_hints(self):
        queue_items = [
            {
                "identity": {"forge": "gitea", "repo": "openclaw/nisto-home", "number": 282},
                "upstream": [
                    {"forge": "gitea", "repo": "openclaw/nisto-home", "number": 281},
                    {"forge": "github", "repo": "cyberstorm-dev/carapace", "number": 1},
                ],
                "downstream": [
                    {"forge": "gitea", "repo": "openclaw/nisto-home", "number": 283},
                ],
            }
        ]
        actions = trigger._build_queue_next_actions(
            queue_items=queue_items,
            default_forge="gitea",
            target_repo="openclaw/nisto-home",
            redis_url="redis://localhost:6379/0",
        )
        self.assertTrue(any("claim" in action.get("command", "") for action in actions))
        self.assertTrue(any("upstream" in action.get("description", "").lower() for action in actions))
        self.assertTrue(any("downstream" in action.get("description", "").lower() for action in actions))

    @patch("carapace.cli.trigger.redis.from_url")
    @patch("carapace.cli.trigger.GiteaClient")
    def test_run_emits_next_actions_with_queue_graph_context(self, mock_client_cls, mock_redis_from_url):
        client = MagicMock()
        client._request.return_value = []
        client.list_issues.return_value = [{"number": 282, "title": "Work item", "assignees": [], "labels": []}]
        mock_client_cls.return_value = client

        member = (
            '{"identity":{"forge":"gitea","repo":"openclaw/nisto-home","number":282},'
            '"upstream":[{"forge":"gitea","repo":"openclaw/nisto-home","number":281}],'
            '"downstream":[{"forge":"gitea","repo":"openclaw/nisto-home","number":283}]}'
        )
        redis_client = MagicMock()
        redis_client.zrevrange.return_value = [member]
        mock_redis_from_url.return_value = redis_client

        args = Namespace(
            gitea_url="http://gitea.test",
            token="token",
            repo="openclaw/nisto-home",
            redis_url="redis://localhost:6379/0",
        )
        payload, code = trigger.run(args)

        self.assertEqual(code, 0)
        self.assertIn("next_actions", payload)
        self.assertGreater(len(payload["next_actions"]), 0)
        descriptions = [action.get("description", "") for action in payload["next_actions"]]
        self.assertTrue(any("upstream" in desc.lower() for desc in descriptions))
        self.assertTrue(any("downstream" in desc.lower() for desc in descriptions))


if __name__ == "__main__":
    unittest.main()
