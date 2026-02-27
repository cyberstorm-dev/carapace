import unittest
from datetime import datetime, timedelta
from carapace.worker.pool import APIKey, APIKeyPool

class TestAPIKeyPool(unittest.TestCase):
    def setUp(self):
        self.keys = [
            APIKey(label="key1", gemini_key="g1", gitea_token="t1", model="pro"),
            APIKey(label="key2", gemini_key="g2", gitea_token="t2", model="flash"),
            APIKey(label="key3", gemini_key="g3", gitea_token="t3", model="pro"),
        ]
        self.pool = APIKeyPool(self.keys)

    def test_get_next_available_round_robin(self):
        k1 = self.pool.get_next_available()
        k2 = self.pool.get_next_available()
        k3 = self.pool.get_next_available()
        k4 = self.pool.get_next_available()
        
        self.assertNotEqual(k1.label, k2.label)
        self.assertNotEqual(k2.label, k3.label)
        # Should start over or pick least used
        labels = {k1.label, k2.label, k3.label}
        self.assertIn(k4.label, labels)

    def test_model_preference(self):
        k = self.pool.get_next_available(model_preference="flash")
        self.assertEqual(k.label, "key2")
        self.assertEqual(k.model, "flash")

    def test_rate_limiting(self):
        self.pool.mark_rate_limited("key1", reset_after_seconds=10)
        
        # key1 should not be available
        for _ in range(5):
            k = self.pool.get_next_available()
            self.assertNotEqual(k.label, "key1")

    def test_all_rate_limited(self):
        for k in self.keys:
            self.pool.mark_rate_limited(k.label, reset_after_seconds=10)
        
        k = self.pool.get_next_available()
        self.assertIsNone(k)

if __name__ == '__main__':
    unittest.main()
