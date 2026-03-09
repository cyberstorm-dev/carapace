import unittest

from carapace.core import queue_contract
from carapace.issue_ref import IssueRef


class TestQueueContract(unittest.TestCase):
    def test_split_issue_locator_defaults_to_gitea(self):
        forge, repo = queue_contract.split_issue_locator("openclaw/nisto-home")
        self.assertEqual(forge, "gitea")
        self.assertEqual(repo, "openclaw/nisto-home")

    def test_split_issue_locator_accepts_cross_forge_prefix(self):
        forge, repo = queue_contract.split_issue_locator("github:cyberstorm-dev/carapace")
        self.assertEqual(forge, "github")
        self.assertEqual(repo, "cyberstorm-dev/carapace")

    def test_identity_from_ref_uses_forge_prefix(self):
        ref = IssueRef("github:cyberstorm-dev/carapace", 70)
        identity = queue_contract.identity_from_ref(ref)
        self.assertEqual(
            identity,
            {
                "forge": "github",
                "repo": "cyberstorm-dev/carapace",
                "number": 70,
            },
        )

    def test_decode_queue_member_requires_identity(self):
        decoded = queue_contract.decode_queue_member('{"title":"x"}')
        self.assertIsNone(decoded)

    def test_encode_then_decode_queue_member_roundtrip(self):
        item = {
            "identity": {"forge": "gitea", "repo": "openclaw/nisto-home", "number": 282},
            "title": "Queue payload contract",
            "reasons": ["active_subgraph", "dependencies_clear"],
            "upstream": [],
            "downstream": [],
            "next_actions": [{"action": "begin_work"}],
        }
        encoded = queue_contract.encode_queue_member(item)
        decoded = queue_contract.decode_queue_member(encoded)
        self.assertEqual(decoded, item)


if __name__ == "__main__":
    unittest.main()
