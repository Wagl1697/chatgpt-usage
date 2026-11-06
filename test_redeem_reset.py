import unittest
from unittest.mock import Mock, patch

from chatgpt_usage import redeem_reset


class RedeemResetTests(unittest.TestCase):
    def setUp(self):
        self.account = {
            "email": "alice@example.com",
            "enabled": True,
            "sources": [{"browser": "firefox", "profile": "profile-a"}],
        }
        self.profile = {"browser": "firefox", "name": "profile-a"}
        self.client = Mock()
        self.client.read_account.return_value = {"email": "alice@example.com"}
        self.client.read_reset_credits.return_value = ([{
            "id": "credit-1", "is_supported_by_plan": True,
        }], None)
        self.client.consume_reset_credit.return_value = {"code": "reset"}

    def run_with_account(self, account=None, confirmed=True):
        tracked = [] if account is None else [account]
        patches = (
            patch.object(redeem_reset.browser_accounts, "load_tracked_accounts", return_value=tracked),
            patch.object(redeem_reset.browser_accounts, "find_profile", return_value=self.profile),
            patch.object(redeem_reset.browser_accounts, "read_cookies", return_value=[{"name": "session-token"}]),
            patch.object(redeem_reset.monitor, "UsageClient", return_value=self.client),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return redeem_reset.redeem("alice@example.com", "credit-1", confirmed=confirmed)

    def test_requires_explicit_confirmation_before_any_network_action(self):
        with self.assertRaisesRegex(redeem_reset.monitor.MonitorError, "confirmación explícita"):
            self.run_with_account(self.account, confirmed=False)
        self.client.read_account.assert_not_called()
        self.client.consume_reset_credit.assert_not_called()

    def test_requires_an_enabled_selected_account(self):
        with self.assertRaisesRegex(redeem_reset.monitor.MonitorError, "ya no está activa"):
            self.run_with_account({**self.account, "enabled": False}, confirmed=True)
        self.client.consume_reset_credit.assert_not_called()

    def test_revalidates_credit_before_consuming(self):
        self.client.read_reset_credits.return_value = ([], None)
        with self.assertRaisesRegex(redeem_reset.monitor.MonitorError, "ya no está disponible"):
            self.run_with_account(self.account, confirmed=True)
        self.client.consume_reset_credit.assert_not_called()

    def test_consumes_only_matching_supported_credit(self):
        result = self.run_with_account(self.account, confirmed=True)
        self.assertEqual(result["code"], "reset")
        self.assertTrue(result["ok"])
        self.client.consume_reset_credit.assert_called_once()
        credit_id, request_id = self.client.consume_reset_credit.call_args.args
        self.assertEqual(credit_id, "credit-1")
        self.assertTrue(request_id)

    def test_rejects_profile_that_now_belongs_to_another_account(self):
        self.client.read_account.return_value = {"email": "bob@example.com"}
        with self.assertRaisesRegex(redeem_reset.monitor.MonitorError, "(?i)no se pudo verificar"):
            self.run_with_account(self.account, confirmed=True)
        self.client.consume_reset_credit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
