import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from migrate_panel import without_legacy_command
import monitor


class NativePanelTests(unittest.TestCase):
    def test_removes_only_our_command(self):
        config = {"commands": [
            {"uuid": "user-command", "isActive": False},
            {"uuid": "chatgpt-usage-panel-left", "isActive": True},
            {"uuid": "another-user-command", "isActive": True},
        ]}
        original = json.dumps(config)
        result = without_legacy_command(config)
        self.assertEqual([item["uuid"] for item in result["commands"]], ["user-command", "another-user-command"])
        self.assertEqual(json.dumps(config), original)

    def test_snapshot_excludes_private_fields(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(monitor, "ROOT", Path(tmp)):
            go_meters = {"five_hour": {"usage_percent": 2, "remaining_percent": 98, "limited": False, "reset_at": 1800000000}}
            monitor.save_report({"email": "PRIVATE", "rate_limit": {
                "primary_window": {"used_percent": 10, "reset_at": 1800000000, "private": "PRIVATE"}
            }}, 1700000000, "OK", go_meters, 1700000000, "OK — OpenCode Go")
            text = (Path(tmp) / "uso.json").read_text()
            self.assertNotIn("PRIVATE", text)
            self.assertEqual(json.loads(text)["windows"]["five_hour"]["remaining_percent"], 90)
            self.assertEqual(json.loads(text)["opencode_go"]["windows"]["five_hour"]["remaining_percent"], 98)


if __name__ == "__main__":
    unittest.main()
