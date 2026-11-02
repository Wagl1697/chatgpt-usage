#!/usr/bin/env python3
"""Install the local GNOME extension, generating machine-specific metadata."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "gnome-extension"
UUID = "chatgpt-usage@wlopez.local"
DEFAULT_DESTINATION = Path.home() / ".local/share/gnome-shell/extensions" / UUID
DEFAULT_PYTHON = ROOT / ".venv/bin/python"


def render_metadata(destination, state_file, scan_script, scan_python):
    template = json.loads((SOURCE / "metadata.template.json").read_text(encoding="utf-8"))
    replacements = {
        "@STATE_FILE@": str(state_file),
        "@SCAN_SCRIPT@": str(scan_script),
        "@SCAN_PYTHON@": str(scan_python),
    }
    for key, value in list(template.items()):
        if isinstance(value, str) and value in replacements:
            template[key] = replacements[value]
    (destination / "metadata.json").write_text(
        json.dumps(template, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def install(destination, state_file, scan_script, scan_python):
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("extension.js", "prefs.js", "stylesheet.css"):
        shutil.copy2(SOURCE / name, destination / name)
    schemas = destination / "schemas"
    schemas.mkdir(exist_ok=True)
    for source in (SOURCE / "schemas").glob("*.xml"):
        shutil.copy2(source, schemas / source.name)
    compiled = schemas / "gschemas.compiled"
    if compiled.exists():
        compiled.unlink()
    subprocess.run(
        ["glib-compile-schemas", "--strict", str(schemas)],
        check=True,
    )
    render_metadata(destination, state_file, scan_script, scan_python)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--state-file", type=Path, default=ROOT / "uso.json")
    parser.add_argument("--scan-script", type=Path, default=ROOT / "src/chatgpt_usage/scan_accounts.py")
    parser.add_argument("--scan-python", type=Path, default=DEFAULT_PYTHON)
    args = parser.parse_args()
    install(
        args.destination.expanduser(),
        args.state_file.expanduser(),
        args.scan_script.expanduser(),
        args.scan_python.expanduser(),
    )
    print(f"Extensión instalada en {args.destination.expanduser()}")


if __name__ == "__main__":
    main()
