import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
import install as installer


class InstallTests(unittest.TestCase):
    def paths(self, root):
        return {
            "extension": root / "data/gnome-shell/extensions" / installer.UUID,
            "units": root / "config/systemd/user",
            "state": root / "state/chatgpt-usage",
            "manifest": root / "config/chatgpt-usage/installation.json",
        }

    def test_install_and_update_preserve_state_and_install_all_extension_resources(self):
        with tempfile.TemporaryDirectory(prefix="usage test ") as tmp:
            paths = self.paths(Path(tmp))
            installer.install_files(paths, with_web=True)
            state = paths["state"] / "polling.json"
            state.write_text('{"enabled": false}')
            installer.install_files(paths, with_web=True)
            self.assertEqual(state.read_text(), '{"enabled": false}')
            for filename in ("extension.js", "prefs.js", "stylesheet.css", "schemas/gschemas.compiled"):
                self.assertTrue((paths["extension"] / filename).is_file())
            metadata = json.loads((paths["extension"] / "metadata.json").read_text())
            self.assertEqual(metadata["uuid"], installer.UUID)
            self.assertEqual(metadata["state-file"], str(paths["state"] / "uso.json"))
            self.assertTrue(Path(metadata["scan-script"]).exists())
            self.assertTrue(Path(metadata["reset-script"]).exists())
            self.assertNotIn("@STATE_FILE@", json.dumps(metadata))
            self.assertIn("127.0.0.1", (paths["units"] / installer.WEB).read_text())

    def test_default_install_does_not_install_web(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.paths(Path(tmp))
            installer.install_files(paths)
            self.assertTrue((paths["units"] / installer.MONITOR).is_file())
            self.assertFalse((paths["units"] / installer.WEB).exists())

    def test_uninstall_preserves_state_and_unknown_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.paths(Path(tmp))
            installer.install_files(paths, with_web=True)
            note = paths["extension"] / "my-note.txt"
            note.write_text("preserve")
            state = paths["state"] / "uso.json"
            state.write_text("private")
            installer.uninstall(paths, no_start=True)
            self.assertEqual(state.read_text(), "private")
            self.assertEqual(note.read_text(), "preserve")
            self.assertFalse((paths["units"] / installer.MONITOR).exists())
            self.assertFalse((paths["extension"] / "extension.js").exists())

    def test_source_directory_can_have_spaces_percent_and_dollars(self):
        with patch.object(installer, "ROOT", Path('/home/example/My project % $data')):
            unit = installer.unit_text("monitor", Path('/state with spaces'), Path('/schema'))
        self.assertIn('WorkingDirectory=/home/example/My project %% $data', unit)
        self.assertIn('ExecStart="/home/example/My project %% $data/.venv/bin/python"', unit)
        with self.assertRaises(ValueError):
            installer.quote("/bad\npath")

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze is not installed")
    def test_generated_unit_is_accepted_by_systemd(self):
        with tempfile.TemporaryDirectory(prefix="unit test % $ ") as tmp:
            root = Path(tmp)
            (root / ".venv/bin").mkdir(parents=True)
            (root / ".venv/bin/python").symlink_to(sys.executable)
            with patch.object(installer, "ROOT", root):
                unit = root / installer.MONITOR
                unit.write_text(installer.unit_text("monitor", root / "state", root / "schemas"))
            subprocess.run(["systemd-analyze", "--user", "verify", str(unit)], check=True)

    def test_installed_state_override_does_not_write_into_the_clone(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "CHATGPT_USAGE_STATE_DIR": tmp,
                   "PYTHONPATH": str(ROOT / "src")}
            value = subprocess.check_output([
                sys.executable, "-c", "from chatgpt_usage import monitor; print(monitor.ROOT)",
            ], env=env, text=True).strip()
            self.assertEqual(value, tmp)


if __name__ == "__main__":
    unittest.main()
