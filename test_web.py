import unittest
from unittest.mock import patch

from chatgpt_usage import web


class WebReportTests(unittest.TestCase):
    def test_public_report_omits_redeemable_reset_ids(self):
        source = {
            "status": "OK",
            "chatgpt_accounts": [{
                "email": "alice@example.com",
                "windows": {},
                "reset_credits": [{"id": "private-credit-id"}],
                "reset_credits_status": None,
                "reset_credits_fetched_at": 1,
            }],
        }
        with patch.object(web, "_read_report", return_value=source):
            public = web._public_report()
        self.assertEqual(public["chatgpt_accounts"], [{
            "email": "alice@example.com", "windows": {},
        }])
        self.assertIn("private-credit-id", source["chatgpt_accounts"][0]["reset_credits"][0]["id"])

    def test_public_report_handles_invalid_report_shape(self):
        with patch.object(web, "_read_report", return_value=[]):
            self.assertEqual(web._public_report(), {})


if __name__ == "__main__":
    unittest.main()
