from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from panel import countdown, panel_label, read_report


class PanelTests(unittest.TestCase):
    def report(self, age=0, status="OK — consulta real al servidor", go_age=None):
        now = 1800000000
        timestamp = datetime.fromtimestamp(now - age, timezone.utc).isoformat()
        go_timestamp = now - (age if go_age is None else go_age)
        text = (f"Estado: {status}\nDatos obtenidos: {timestamp}\n"
                "Límite de 5 horas\nRestante: 75 %\n"
                f"Se restablece: {timestamp}\n"
                "Límite semanal\nRestante: 96 %\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "uso.txt"
            path.write_text(text)
            (Path(tmp) / "uso.json").write_text(json.dumps({
                "opencode_go": {"status": "OK — OpenCode Go", "fetched_at": go_timestamp, "windows": {
                    "five_hour": {"remaining_percent": 98},
                    "weekly": {"remaining_percent": 96},
                }}
            }))
            return read_report(path, now=now)

    def test_label(self):
        report = self.report()
        self.assertTrue(report["fresh"])
        self.assertEqual(panel_label(report), "GPT  5h 75% · Go 5h 98%")

    def test_old_values_warn(self):
        self.assertIn("⚠", panel_label(self.report(age=31)))

    def test_explicit_error_warns(self):
        self.assertFalse(self.report(status="DETENIDO")["fresh"])

    def test_missing_report(self):
        self.assertIn("⚠", panel_label(read_report(Path("/nonexistent/usage"))))

    def test_old_go_data_warns_without_marking_chatgpt_stale(self):
        report = self.report(age=0, go_age=151)
        self.assertTrue(report["fresh"])
        self.assertFalse(report["go_fresh"])
        self.assertIn("Go ⚠", panel_label(report))

    def test_go_text_does_not_overwrite_chatgpt_weekly_window(self):
        now = 1800000000
        updated = datetime.fromtimestamp(now, timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "uso.txt").write_text(
                f"Estado: OK\nDatos obtenidos: {updated}\n"
                "Límite de 5 horas\nRestante: 55 %\nSe restablece: 2027-01-01T00:00:00+00:00\n"
                "Límite semanal\nRestante: 93 %\nSe restablece: 2027-01-02T00:00:00+00:00\n"
                "OpenCode Go: OK\nOpenCode Go · límite semanal\nRestante: 100 %\n"
                "Se restablece: 2027-01-03T00:00:00+00:00\n"
            )
            (root / "uso.json").write_text(json.dumps({"opencode_go": {
                "status": "OK", "fetched_at": now, "windows": {
                    "weekly": {"remaining_percent": 100}
                }
            }}))
            report = read_report(root / "uso.txt", now=now)
        self.assertEqual(report["windows"]["week"]["remaining"], "93 %")
        self.assertIn("5h 55%", panel_label(report))
        self.assertNotIn("Sem 93%", panel_label(report))

    def test_two_chatgpt_accounts_are_labeled_separately(self):
        report = self.report()
        report["chatgpt_accounts"] = [
            {"email": "alice@example.com", "status": "OK", "fresh": True,
             "windows": {"five_hour": {"remaining_percent": 50}}},
            {"email": "bob@example.com", "status": "OK", "fresh": True,
             "windows": {"five_hour": {"remaining_percent": 80}}},
        ]
        label = panel_label(report)
        self.assertIn("alice 5h 50%", label)
        self.assertIn("bob 5h 80%", label)

    def test_countdown(self):
        self.assertEqual(countdown("1970-01-02T02:03:00+00:00", now=0), "1 d 2 h 3 min")
        self.assertEqual(countdown("invalid"), "No disponible")


if __name__ == "__main__":
    unittest.main()
