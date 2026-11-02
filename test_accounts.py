import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from chatgpt_usage import browser_accounts
from chatgpt_usage import monitor

KEY = b"0123456789abcdef"


def encrypt(value, key=KEY, host=".chatgpt.com"):
    plain = hashlib.sha256(host.encode()).digest() + value.encode()
    padding = 16 - len(plain) % 16
    plain += bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
    return b"v11" + encryptor.update(plain) + encryptor.finalize()


class AccountTests(unittest.TestCase):
    def test_windows_are_converted_to_remaining_percent(self):
        windows = monitor.windows_from_rate_limit({
            "primary_window": {"used_percent": 25, "reset_at": 1234},
            "secondary_window": {"used_percent": 0},
        })
        self.assertEqual(windows["five_hour"]["remaining_percent"], 75)
        self.assertEqual(windows["weekly"]["remaining_percent"], 100)
        self.assertEqual(windows["five_hour"]["reset_at"], 1234)

    def test_tracked_accounts_are_read_from_gsettings_json(self):
        payload = json.dumps({"accounts": [
            {"email": "one@example.com", "enabled": True,
             "sources": [{"browser": "firefox", "profile": "uno"}]},
            {"email": "two@example.com", "enabled": False,
             "sources": [{"browser": "chrome", "profile": "Default"}]},
        ]})
        with patch.object(browser_accounts, "_gsettings_value", return_value=payload):
            accounts = browser_accounts.load_tracked_accounts()
        self.assertEqual([entry["email"] for entry in accounts], ["one@example.com", "two@example.com"])
        self.assertFalse(accounts[1]["enabled"])

    def test_invalid_tracked_accounts_fail_closed(self):
        with patch.object(browser_accounts, "_gsettings_value", return_value="not-json"):
            with self.assertRaises(browser_accounts.AccountError):
                browser_accounts.load_tracked_accounts()

    def test_unreadable_tracked_accounts_fail_closed(self):
        with patch.object(browser_accounts, "_gsettings_value", return_value=None):
            with self.assertRaises(browser_accounts.AccountError):
                browser_accounts.load_tracked_accounts()

    def test_firefox_cookies_keep_only_chatgpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "uno"
            profile.mkdir()
            with sqlite3.connect(profile / "cookies.sqlite") as db:
                db.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT, path TEXT, isSecure INTEGER, expiry INTEGER)")
                db.executemany("INSERT INTO moz_cookies VALUES (?,?,?,?,?,?)", [
                    (".chatgpt.com", "session-token", "cookie", "/", 1, 0),
                    (".example.com", "other", "ignore", "/", 1, 0),
                ])
                db.commit()
            cookies = browser_accounts.read_firefox_cookies({
                "kind": "firefox", "database": profile / "cookies.sqlite",
            }, now=100)
            self.assertEqual([cookie["name"] for cookie in cookies], ["session-token"])

    def test_chromium_v11_cookie_is_decrypted(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "Default"
            profile.mkdir()
            with sqlite3.connect(profile / "Cookies") as db:
                db.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, path TEXT, expires_utc INTEGER, is_secure INTEGER)")
                db.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?,?)", (
                    ".chatgpt.com", "__Secure-next-auth.session-token",
                    "", encrypt("secret"), "/", 0, 1,
                ))
                db.commit()
            cookies = browser_accounts.read_chromium_cookies({
                "kind": "chromium", "database": profile / "Cookies", "apps": ("chrome",),
            }, now=100, key=KEY)
            self.assertEqual(cookies[0]["value"], "secret")


if __name__ == "__main__":
    unittest.main()
