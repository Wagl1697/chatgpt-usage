#!/usr/bin/python3
"""Remove only our legacy widgets after the native extension is actually active."""

import json
import os
from pathlib import Path
import subprocess

from gi.repository import Gio, GLib

ROOT = Path(__file__).resolve().parent
UUID = "chatgpt-usage@wlopez.local"
COMMAND_UUID = "chatgpt-usage-panel-left"


def without_legacy_command(config):
    updated = dict(config)
    updated["commands"] = [item for item in config["commands"] if item.get("uuid") != COMMAND_UUID]
    return updated


def main():
    os.umask(0o077)
    # Do not remove the working UI merely because the new files were installed.
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    info = bus.call_sync(
        "org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell.Extensions",
        "GetExtensionInfo", GLib.Variant("(s)", (UUID,)), None,
        Gio.DBusCallFlags.NONE, 5000, None,
    ).unpack()[0]
    if info.get("state") != 1:
        raise SystemExit("La extensión nativa todavía no está activa; se conserva la interfaz anterior.")
    schema_dir = Path.home() / ".local/share/gnome-shell/extensions/executor@raujonas.github.io/schemas"
    if schema_dir.is_dir():
        source = Gio.SettingsSchemaSource.new_from_directory(
            str(schema_dir), Gio.SettingsSchemaSource.get_default(), False
        )
        schema = source.lookup("org.gnome.shell.extensions.executor", False)
        if schema:
            settings = Gio.Settings.new_full(schema, None, None)
            config = json.loads(settings.get_string("left-commands-json"))
            updated = without_legacy_command(config)
            if updated != config:
                backup = ROOT / "executor-before-native-panel.json"
                if not backup.exists():
                    backup.write_text(json.dumps({
                        "left-commands-json": settings.get_string("left-commands-json"),
                        "left-active": settings.get_boolean("left-active"),
                    }, indent=2) + "\n")
                settings.set_string("left-commands-json", json.dumps(updated))
                if not any(item.get("isActive", True) for item in updated["commands"]):
                    settings.set_boolean("left-active", False)
                Gio.Settings.sync()
    # Monitor service is intentionally untouched.
    subprocess.run([
        "systemctl", "--user", "disable", "--now", "chatgpt-usage-panel.service"
    ], check=True)
    (ROOT / "native-panel.ready").write_text("El menú nativo de la izquierda reemplazó los indicadores anteriores.\n")


if __name__ == "__main__":
    main()
