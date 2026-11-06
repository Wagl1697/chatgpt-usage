#!/usr/bin/env python3
"""Smoke-test the extension in an isolated, headless GNOME Shell 46+.

No browser data, real settings, account credentials or reset helper are used.
Requires a working GNOME installation with the headless Wayland backend.
"""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
UUID = "chatgpt-usage@wlopez.local"

SMOKE_EXTENSION = """
import GLib from 'gi://GLib';
import Monitor from './monitor.js';

function texts(actor) {
    return [typeof actor.get_text === 'function' ? actor.get_text() : '',
        ...actor.get_children().flatMap(texts)].filter(Boolean);
}

export default class SmokeMonitor extends Monitor {
    enable() {
        super.enable();
        this._button.menu.open();
        GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 3, () => {
            const result = {ok: false};
            try {
                if (this._accountRows.size !== 2)
                    throw new Error(`Expected 2 accounts; got ${this._accountRows.size}`);
                if (this._goStatus.label.text !== '● OpenCode Go actualizado')
                    throw new Error(`Go was not rendered: ${this._goStatus.label.text}`);
                const goText = texts(this._goDashboard.item);
                for (const value of ['85%', '60%', '40%']) {
                    if (!goText.includes(value))
                        throw new Error(`Go meter missing: ${value}`);
                }
                if (this._updated.label.text.includes('1970') || this._updated.label.text.includes('· —'))
                    throw new Error(`Last-read date was not updated: ${this._updated.label.text}`);
                const row = this._accountRows.get('alice@example.com').resetItems.get('credit-0');
                if (row.button.label !== 'Usar' || !row.button.reactive)
                    throw new Error('Reset action is not ready');
                const [, buttonHeight] = row.button.get_preferred_height(-1);
                const [, textHeight] = row.title.get_preferred_height(-1);
                if (buttonHeight > textHeight * 1.5)
                    throw new Error(`Reset button too tall: ${buttonHeight} vs text ${textHeight}`);
                this._onResetButton(row);
                if (row.button.label !== 'Confirmar')
                    throw new Error('First click did not arm confirmation');
                this._clearResetConfirmations();
                if (row.button.label !== 'Usar')
                    throw new Error('Confirmation did not clear');
                result.ok = true;
                result.accounts = this._accountRows.size;
                result.goMeters = goText;
                result.buttonHeight = buttonHeight;
                result.textHeight = textHeight;
                result.updated = this._updated.label.text;
            } catch (error) {
                result.error = String(error);
            }
            GLib.file_set_contents(GLib.getenv('CHATGPT_USAGE_SMOKE_RESULT'), JSON.stringify(result));
            global.context.terminate();
            return GLib.SOURCE_REMOVE;
        });
    }
}
"""


def main():
    for command in ("gnome-shell", "dbus-run-session", "gsettings", "glib-compile-schemas"):
        if shutil.which(command) is None:
            raise SystemExit(f"Required command not found: {command}")
    temp_base = Path("/tmp/opencode")
    temp_base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gnome-smoke-", dir=temp_base) as tmp:
        home = Path(tmp)
        extension = home / "data/gnome-shell/extensions" / UUID
        extension.mkdir(parents=True)
        runtime = home / "runtime"
        runtime.mkdir(mode=0o700)
        schemas = extension / "schemas"
        shutil.copytree(ROOT / "gnome-extension/schemas", schemas)
        subprocess.run(["glib-compile-schemas", "--strict", str(schemas)], check=True)
        shutil.copy2(ROOT / "gnome-extension/extension.js", extension / "monitor.js")
        shutil.copy2(ROOT / "gnome-extension/stylesheet.css", extension / "stylesheet.css")
        (extension / "extension.js").write_text(SMOKE_EXTENSION)
        report_file = home / "report.json"
        now = time.time()
        report_file.write_text(json.dumps({
            "polling_enabled": True,
            "chatgpt_accounts": [
                {
                    "email": email, "status": "OK", "plan_type": "plus", "fetched_at": now,
                    "windows": {"five_hour": {"remaining_percent": 20 + index * 30}},
                    "reset_credits": [{
                        "id": f"credit-{index}", "title": "Full reset (Weekly + 5 hr)",
                        "expires_at": "2026-12-01T18:00:00Z", "is_supported_by_plan": True,
                    }],
                }
                for index, email in enumerate(("alice@example.com", "bob@example.com"))
            ],
            "opencode_go": {
                "status": "OK — OpenCode Go", "fetched_at": now,
                "windows": {
                    "five_hour": {"remaining_percent": 85},
                    "weekly": {"remaining_percent": 60},
                    "monthly": {"remaining_percent": 40},
                },
            },
        }))
        metadata = json.loads((ROOT / "gnome-extension/metadata.template.json").read_text())
        metadata["state-file"] = str(report_file)
        # The smoke test must never be able to execute a real redemption.
        metadata.pop("reset-script", None)
        metadata.pop("scan-python", None)
        metadata.pop("scan-script", None)
        (extension / "metadata.json").write_text(json.dumps(metadata))
        result_file = home / "result.json"
        env = {**os.environ,
               "HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"),
               "XDG_DATA_HOME": str(home / "data"), "XDG_CACHE_HOME": str(home / "cache"),
               "XDG_STATE_HOME": str(home / "state"), "XDG_RUNTIME_DIR": str(runtime),
               "GSETTINGS_BACKEND": "keyfile", "GNOME_SHELL_SESSION_MODE": "user",
               "CHATGPT_USAGE_SMOKE_RESULT": str(result_file),
               "LIBGL_ALWAYS_SOFTWARE": "1"}
        for key in ("WAYLAND_DISPLAY", "DISPLAY", "SESSION_MANAGER"):
            env.pop(key, None)
        script = (
            f"gsettings set org.gnome.shell enabled-extensions \"['{UUID}']\"\n"
            "exec gnome-shell --headless --wayland --no-x11 --sm-disable --virtual-monitor=1024x768"
        )
        with subprocess.Popen(
            ["dbus-run-session", "--", "sh", "-e", "-c", script],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        ) as process:
            try:
                _, stderr = process.communicate(timeout=35)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    _, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    _, stderr = process.communicate()
                print(stderr[-12000:])
                raise SystemExit("Headless GNOME smoke test timed out") from None
        if not result_file.exists():
            print(stderr[-12000:])
            raise SystemExit(f"Headless GNOME produced no result (exit {process.returncode})")
        result = json.loads(result_file.read_text())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result.get("ok"):
            print(stderr[-12000:])
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
