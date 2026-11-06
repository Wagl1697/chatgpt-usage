import base64
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from chatgpt_usage import monitor


class MonitorTests(unittest.TestCase):
    def test_percent_is_remaining_not_used(self):
        data = {"plan_type": "plus", "rate_limit": {
            "primary_window": {"used_percent": 23, "reset_at": 1800000000},
            "secondary_window": {"used_percent": 4, "reset_at": 1800000000},
        }}
        text = monitor.render(data, 1700000000, "OK")
        self.assertIn("Restante: 77 %", text)
        self.assertIn("Restante: 96 %", text)
        self.assertIn("sin navegador", text)

    def test_missing_window_not_invented(self):
        text = monitor.render({"rate_limit": {}}, 1700000000, "OK")
        self.assertEqual(text.count("No disponible"), 6)
        self.assertNotIn("Restante:", text)

    def test_zero_usage_is_displayed(self):
        text = monitor.render({"rate_limit": {"primary_window": {"used_percent": 0}}}, 1700000000, "OK")
        self.assertIn("Restante: 100 %", text)

    def test_reset_credit_parser_keeps_only_available_whitelisted_fields(self):
        payload = {"credits": [
            {
                "id": "credit-1", "status": "available", "title": "Restablecimiento total",
                "expires_at": "2026-12-01T18:00:00Z", "reset_type": "total",
                "is_supported_by_plan": True, "profile_user_id": "private",
                "profile_image_url": "https://example.invalid/private.png",
            },
            {"id": "credit-2", "status": "redeemed", "title": "No conservar"},
        ], "total_earned_count": 99}
        result = monitor.parse_reset_credits(payload)
        self.assertEqual(result, [{
            "id": "credit-1", "title": "Restablecimiento total",
            "expires_at": "2026-12-01T18:00:00Z", "reset_type": "total",
            "is_supported_by_plan": True,
        }])
        self.assertNotIn("profile_user_id", json.dumps(result))

    def test_reset_credit_parser_rejects_invalid_payload(self):
        with self.assertRaises(monitor.MonitorError):
            monitor.parse_reset_credits({"credits": {}})

    def test_reset_credit_list_is_cached_and_can_be_forced(self):
        client = self.make_client()
        client.email = "alice@example.com"
        payload = {"credits": [{
            "id": "credit-1", "status": "available", "title": "Reset",
            "expires_at": "2026-12-01T18:00:00Z", "reset_type": "total",
            "is_supported_by_plan": True,
        }]}
        client.get_json = Mock(return_value=payload)
        first, status = client.read_reset_credits()
        second, _ = client.read_reset_credits()
        forced, _ = client.read_reset_credits(force=True)
        self.assertIsNone(status)
        self.assertEqual(first, second)
        self.assertEqual(first, forced)
        self.assertEqual(client.get_json.call_count, 2)

    def test_reset_consumption_posts_only_the_requested_credit(self):
        client = self.make_client()
        response = Mock(status_code=200, headers={})
        response.json.return_value = {"code": "reset"}
        client.session.post.return_value = response
        result = client.consume_reset_credit("credit-1", "request-1")
        self.assertEqual(result, {"code": "reset"})
        args, kwargs = client.session.post.call_args
        self.assertEqual(args[0], monitor.ORIGIN + "/backend-api/wham/rate-limit-reset-credits/consume")
        self.assertEqual(kwargs["json"], {
            "credit_id": "credit-1", "redeem_request_id": "request-1",
        })
        self.assertFalse(kwargs["allow_redirects"])

    def test_expired_countdown_not_negative(self):
        self.assertEqual(monitor.duration(-100), "0 d 0 h 0 min 0 s")

    def test_polling_switch_disables_and_defaults_to_enabled(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(monitor, "ROOT", Path(tmp)):
            self.assertTrue(monitor.polling_enabled())
            (Path(tmp) / "polling.json").write_text('{"enabled": false}')
            self.assertFalse(monitor.polling_enabled())
            (Path(tmp) / "polling.json").write_text("invalid")
            self.assertTrue(monitor.polling_enabled())

    def test_paused_monitor_makes_no_network_calls(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(monitor, "ROOT", Path(tmp)):
            (Path(tmp) / "polling.json").write_text('{"enabled": false}')
            with patch.object(monitor, "UsageClient") as client_class, \
                 patch.object(monitor, "read_opencode_go") as read_go:
                self.assertEqual(monitor.run(1), 0)
                client_class.return_value.read_accounts.assert_not_called()
                read_go.assert_not_called()
            report = json.loads((Path(tmp) / "uso.json").read_text())
            self.assertFalse(report["polling_enabled"])

    def test_retry_after(self):
        self.assertEqual(monitor.retry_seconds("180"), 180)
        self.assertEqual(monitor.retry_seconds("5"), 60)
        self.assertEqual(monitor.retry_seconds("invalid"), 60)
        with patch("chatgpt_usage.monitor.time.time", return_value=0):
            self.assertEqual(monitor.retry_seconds("Thu, 01 Jan 1970 00:03:00 GMT"), 180)

    def test_token_refresh_schedule(self):
        payload = base64.urlsafe_b64encode(json.dumps({"exp": 2000}).encode()).decode().rstrip("=")
        with patch("chatgpt_usage.monitor.time.time", return_value=1000):
            self.assertEqual(monitor.token_lifetime("x." + payload + ".x"), 600)
        self.assertEqual(monitor.token_lifetime("opaque-token"), 300)

    def make_client(self):
        with patch("chatgpt_usage.monitor.requests.Session") as factory:
            client = monitor.UsageClient()
        client.token = "test-token"
        client.refresh_at = float("inf")
        return client

    def test_only_usage_fields_retained(self):
        client = self.make_client()
        client.get_json = Mock(return_value={
            "rate_limit": {}, "plan_type": "plus", "email": "private", "account_id": "private"
        })
        self.assertEqual(client.read(), {"plan_type": "plus", "rate_limit": {}})

    def test_authentication_renewed_after_401(self):
        client = self.make_client()
        def authenticate(*args):
            client.token = "refreshed-token"
            client.refresh_at = float("inf")
        client.authenticate = Mock(side_effect=authenticate)
        client.get_json = Mock(side_effect=[monitor.MonitorError("HTTP 401", 401), {"rate_limit": {}}])
        with patch.object(monitor, "firefox_cookie_profiles", return_value=[[]]):
            client.read()
        client.authenticate.assert_called_once()
        self.assertEqual(client.get_json.call_count, 2)

    def test_no_endless_authentication_retries(self):
        client = self.make_client()
        def authenticate(*args):
            client.token = "refreshed-token"
            client.refresh_at = float("inf")
        client.authenticate = Mock(side_effect=authenticate)
        client.get_json = Mock(side_effect=monitor.MonitorError("HTTP 401", 401))
        with patch.object(monitor, "firefox_cookie_profiles", return_value=[[]]):
            with self.assertRaises(monitor.MonitorError):
                client.read()
        self.assertEqual(client.get_json.call_count, 2)

    def test_periodic_authentication_refresh(self):
        client = self.make_client()
        client.refresh_at = 0
        def authenticate(*args):
            client.token = "refreshed-token"
            client.refresh_at = float("inf")
        client.authenticate = Mock(side_effect=authenticate)
        client.get_json = Mock(return_value={"rate_limit": {}})
        with patch.object(monitor, "firefox_cookie_profiles", return_value=[[]]):
            client.read()
        client.authenticate.assert_called_once()

    def test_usage_tries_next_firefox_profile_after_401(self):
        client = self.make_client()
        client.authenticate = Mock(side_effect=lambda *_: setattr(client, "token", "profile-token"))
        client.get_json = Mock(side_effect=[
            monitor.MonitorError("HTTP 401", 401),
            monitor.MonitorError("HTTP 401", 401),
            {"rate_limit": {"primary_window": {"used_percent": 10}}},
        ])
        with patch.object(monitor, "firefox_cookie_profiles", return_value=[[], []]):
            result = client.read()
        self.assertEqual(result["rate_limit"]["primary_window"]["used_percent"], 10)
        self.assertEqual(client.authenticate.call_count, 2)

    def test_profiles_are_reported_as_separate_chatgpt_accounts(self):
        client = self.make_client()
        cookie_a = [(".chatgpt.com", "session-token.a", "cookie-a", "/", True)]
        cookie_b = [(".chatgpt.com", "session-token.b", "cookie-b", "/", True)]
        client.get_json = Mock(side_effect=[
            {"accessToken": "token-a", "user": {"email": "alice@example.com"}},
            {"rate_limit": {"primary_window": {"used_percent": 20, "reset_at": 1800000000}, "secondary_window": {"used_percent": 10}}, "plan_type": "plus"},
            {"credits": []},
            {"accessToken": "token-b", "user": {"email": "bob@example.com"}},
            {"rate_limit": {"primary_window": {"used_percent": 70, "reset_at": 1800000100}, "secondary_window": {"used_percent": 40}}, "plan_type": "pro"},
            {"credits": []},
        ])
        with patch.object(monitor, "firefox_cookie_profiles_named", return_value=[("profile-a", cookie_a), ("profile-b", cookie_b)]):
            accounts = client.read_accounts()
        self.assertEqual([account["email"] for account in accounts], ["alice@example.com", "bob@example.com"])
        self.assertEqual([account["windows"]["five_hour"]["remaining_percent"] for account in accounts], [80, 30])
        self.assertEqual([account["plan_type"] for account in accounts], ["plus", "pro"])

    def test_one_chatgpt_account_failure_does_not_use_another_account_data(self):
        client = self.make_client()
        profiles = [
            ("profile-a", [(".chatgpt.com", "session-token.a", "cookie-a", "/", True)]),
            ("profile-b", [(".chatgpt.com", "session-token.b", "cookie-b", "/", True)]),
        ]
        client.get_json = Mock(side_effect=[
            {"accessToken": "token-a", "user": {"email": "alice@example.com"}},
            monitor.MonitorError("HTTP 401", 401),
            {"accessToken": "token-b", "user": {"email": "bob@example.com"}},
            {"rate_limit": {"primary_window": {"used_percent": 30}}, "plan_type": "plus"},
            {"credits": []},
        ])
        with patch.object(monitor, "firefox_cookie_profiles_named", return_value=profiles):
            accounts = client.read_accounts()
        self.assertEqual(accounts[0]["email"], "alice@example.com")
        self.assertEqual(accounts[0]["status"], "HTTP 401")
        self.assertEqual(accounts[0]["windows"], {})
        self.assertEqual(accounts[1]["email"], "bob@example.com")
        self.assertEqual(accounts[1]["windows"]["five_hour"]["remaining_percent"], 70)

    def test_http_failure_does_not_expose_body(self):
        client = self.make_client()
        client.session.get.return_value = Mock(status_code=403, headers={}, text="SECRET")
        with self.assertRaises(monitor.MonitorError) as error:
            client.get_json("/api/auth/session")
        self.assertEqual(str(error.exception), "HTTP 403")
        self.assertFalse(client.session.get.call_args.kwargs["allow_redirects"])

    def test_no_browser_import(self):
        import sys
        self.assertFalse(any(name.startswith("playwright") for name in sys.modules))

    def test_opencode_go_parser_converts_used_to_remaining(self):
        payload = {"usage": {
            "rolling": {"status": "ok", "percent": 2, "resetsAt": "2026-10-28T21:14:41.000Z"},
            "weekly": {"status": "rate-limited", "percent": 100, "resetsAt": "2026-11-02T00:00:00.000Z"},
            "monthly": {"status": "ok", "percent": 12.5, "resetsAt": "2026-11-28T16:11:43.000Z"},
        }, "email": "MUST NOT BE SAVED"}
        result = monitor.parse_opencode_go(payload)
        self.assertEqual(result["five_hour"]["remaining_percent"], 98)
        self.assertEqual(result["weekly"]["remaining_percent"], 0)
        self.assertTrue(result["weekly"]["limited"])
        self.assertEqual(result["monthly"]["remaining_percent"], 87.5)
        self.assertIsInstance(result["five_hour"]["reset_at"], float)
        self.assertNotIn("email", json.dumps(result))

    def test_opencode_go_parser_rejects_incomplete_or_invalid_meters(self):
        with self.assertRaises(monitor.MonitorError):
            monitor.parse_opencode_go({"usage": {}})
        payload = {"usage": {
            "rolling": {"percent": 101, "resetsAt": "2026-10-28T21:14:41Z"},
            "weekly": {"percent": 0, "resetsAt": "2026-10-28T21:14:41Z"},
            "monthly": {"percent": 0, "resetsAt": "2026-10-28T21:14:41Z"},
        }}
        with self.assertRaises(monitor.MonitorError):
            monitor.parse_opencode_go(payload)

    def test_reads_only_active_opencode_workspace_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "opencode.db"
            with sqlite3.connect(db_path) as db:
                db.execute("CREATE TABLE credential (integration_id TEXT, active INTEGER, value TEXT)")
                db.execute("INSERT INTO credential VALUES (?, ?, ?)", ("opencode", 1, json.dumps({
                    "type": "oauth", "access": "test-secret", "metadata": {
                        "orgID": "wrk_test_workspace", "server": "https://opencode.ai/console"
                    }
                })))
            with patch.object(monitor, "opencode_database", return_value=db_path):
                token, host, workspace = monitor.opencode_credential()
            self.assertEqual(token, "test-secret")
            self.assertEqual(host, "opencode.ai")
            self.assertEqual(workspace, "wrk_test_workspace")

    def test_rejects_other_opencode_workspace_and_server(self):
        for metadata in (
            {"server": "https://opencode.ai/console"},
            {"orgID": "wrk_test_workspace", "server": "https://evil.example/console"},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                db_path = Path(tmp) / "opencode.db"
                with sqlite3.connect(db_path) as db:
                    db.execute("CREATE TABLE credential (integration_id TEXT, active INTEGER, value TEXT)")
                    db.execute("INSERT INTO credential VALUES (?, ?, ?)", ("opencode", 1, json.dumps({
                        "type": "oauth", "access": "test-secret", "metadata": metadata
                    })))
                with patch.object(monitor, "opencode_database", return_value=db_path):
                    with self.assertRaises(monitor.MonitorError):
                        monitor.opencode_credential()

    def test_go_http_request_uses_only_opencode_host_and_expected_workspace(self):
        payload = {"usage": {
            name: {"status": "ok", "percent": 10, "resetsAt": "2026-11-02T00:00:00Z"}
            for name in ("rolling", "weekly", "monthly")
        }}
        response = Mock(status_code=200)
        response.json.return_value = payload
        with patch.object(monitor, "opencode_credential", return_value=("private-token", "opencode.ai", "wrk_test_workspace")), \
             patch.object(monitor.requests, "get", return_value=response) as get:
            result = monitor.read_opencode_go()
        self.assertEqual(result["weekly"]["remaining_percent"], 90)
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://opencode.ai/zen/go/v1/usage")
        self.assertEqual(kwargs["headers"]["x-opencode-org-id"], "wrk_test_workspace")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer private-token")

    def test_go_http_errors_do_not_expose_response_body(self):
        response = Mock(status_code=401, headers={"retry-after": "60"}, text="PRIVATE BODY")
        with patch.object(monitor, "opencode_credential", return_value=("private-token", "opencode.ai", "wrk_test_workspace")), \
             patch.object(monitor.requests, "get", return_value=response):
            with self.assertRaises(monitor.MonitorError) as error:
                monitor.read_opencode_go()
        self.assertEqual(error.exception.status, 401)
        self.assertNotIn("PRIVATE", str(error.exception))

    def test_report_includes_go_meters_but_never_credentials(self):
        meters = {"five_hour": {"usage_percent": 2, "remaining_percent": 98, "limited": False, "reset_at": 1800000000}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(monitor, "ROOT", Path(tmp)):
            monitor.save_report(
                {"plan_type": "plus", "rate_limit": {}, "email": "PRIVATE"}, 1700000000, "OK",
                meters, 1700000000, "OK — OpenCode Go",
            )
            report_text = (Path(tmp) / "uso.json").read_text()
            self.assertNotIn("PRIVATE", report_text)
            report = json.loads(report_text)
            self.assertEqual(report["opencode_go"]["windows"]["five_hour"]["remaining_percent"], 98)
            self.assertIn("OpenCode Go · límite de 5 horas", (Path(tmp) / "uso.txt").read_text())

    def test_report_keeps_reset_action_fields_but_discards_profile_metadata(self):
        account = {
            "email": "alice@example.com", "status": "OK", "fetched_at": 1700000000,
            "reset_credits": [{
                "id": "credit-1", "title": "Reset", "expires_at": "2026-12-01T18:00:00Z",
                "reset_type": "total", "is_supported_by_plan": True,
                "profile_user_id": "must-not-be-saved",
            }],
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(monitor, "ROOT", Path(tmp)):
            monitor.save_report(None, None, "OK", chatgpt_accounts=[account])
            text = (Path(tmp) / "uso.json").read_text()
        self.assertIn("credit-1", text)
        self.assertNotIn("must-not-be-saved", text)


if __name__ == "__main__":
    unittest.main()
