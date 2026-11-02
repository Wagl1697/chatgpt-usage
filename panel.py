#!/usr/bin/python3
"""Native Ubuntu top-bar indicator; only reads the local usage report."""

from datetime import datetime
import fcntl
import json
from pathlib import Path
import signal
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3, Gio, GLib, Gtk

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "uso.txt"


def read_report(path=REPORT, now=None):
    """Never present old data as current, even if the monitor stopped abruptly."""
    now = time.time() if now is None else now
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"text": "Todavía no hay un informe disponible.", "fresh": False,
                "status": "Esperando datos", "windows": {}}
    result = {"text": text, "fresh": False, "status": "Esperando datos", "windows": {}}
    current = None
    for line in text.splitlines():
        if line.startswith("OpenCode Go"):
            current = None
        elif line in ("Límite de 5 horas", "Límite semanal"):
            current = "5h" if line == "Límite de 5 horas" else "week"
            result["windows"][current] = {}
        elif line.startswith("Estado: "):
            result["status"] = line.removeprefix("Estado: ")
        elif line.startswith("Datos obtenidos: "):
            try:
                result["updated"] = datetime.fromisoformat(line.removeprefix("Datos obtenidos: "))
            except ValueError:
                pass
        elif current:
            for prefix, key in (("Restante: ", "remaining"), ("Se restablece: ", "reset")):
                if line.startswith(prefix):
                    result["windows"][current][key] = line.removeprefix(prefix)
    updated = result.get("updated")
    if updated:
        age = now - updated.timestamp()
        result["fresh"] = result["status"].startswith("OK") and -5 <= age <= 30
    try:
        snapshot = json.loads((path.parent / "uso.json").read_text(encoding="utf-8"))
        accounts = snapshot.get("chatgpt_accounts") or []
        normalized_accounts = []
        for account in accounts:
            account = dict(account)
            account_time = account.get("fetched_at")
            account["fresh"] = (
                isinstance(account_time, (int, float))
                and -5 <= now - account_time <= 90
                and account.get("status") == "OK"
            )
            normalized_accounts.append(account)
        result["chatgpt_accounts"] = normalized_accounts
        if normalized_accounts:
            result["fresh"] = all(account["fresh"] for account in normalized_accounts)
        go = snapshot.get("opencode_go") or {}
        go_windows = go.get("windows") or {}
        go_fetched = go.get("fetched_at")
        result["go_windows"] = go_windows
        result["go_status"] = go.get("status", "Sin consultar")
        result["go_fetched_at"] = go_fetched
        result["go_fresh"] = (
            isinstance(go_fetched, (int, float))
            and -5 <= now - go_fetched <= 150
            and str(result["go_status"]).startswith("OK")
        )
    except (OSError, ValueError, AttributeError):
        result["chatgpt_accounts"] = []
        result["go_windows"] = {}
        result["go_status"] = "Sin consultar"
        result["go_fetched_at"] = None
        result["go_fresh"] = False
    return result


def countdown(iso, now=None):
    try:
        seconds = max(0, int(datetime.fromisoformat(iso).timestamp() - (time.time() if now is None else now)))
        minutes = seconds // 60
        days, minutes = divmod(minutes, 1440)
        hours, minutes = divmod(minutes, 60)
        return f"{days} d {hours} h {minutes} min" if days else f"{hours} h {minutes} min"
    except (TypeError, ValueError):
        return "No disponible"


def panel_label(report):
    windows = report["windows"]
    short = lambda key: windows.get(key, {}).get("remaining", "—").replace(" %", "%")
    warning = " ⚠" if not report["fresh"] else ""
    go = report.get("go_windows", {})
    go_short = lambda key: (
        f"{go[key]['remaining_percent']:g}%" if isinstance(go.get(key), dict)
        and isinstance(go[key].get("remaining_percent"), (int, float)) else "—"
    )
    accounts = report.get("chatgpt_accounts", [])
    if accounts:
        account_labels = []
        for account in accounts:
            identity = account.get("email") or account.get("label") or "perfil"
            identity = identity.split("@", 1)[0]
            if len(identity) > 9:
                identity = identity[:8] + "…"
            remaining = (account.get("windows") or {}).get("five_hour", {}).get("remaining_percent")
            value = f"{remaining:g}%" if isinstance(remaining, (int, float)) else "—"
            account_labels.append(f"{identity}{'' if account.get('fresh') else ' ⚠'} 5h {value}")
        gpt_label = "GPT " + " · ".join(account_labels)
    else:
        gpt_label = f"GPT{warning}  5h {short('5h')}"
    go_warning = " ⚠" if not report.get("go_fresh") else ""
    return f"{gpt_label} · Go{go_warning} 5h {go_short('five_hour')}"


class Panel:
    def __init__(self):
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "chatgpt-plan-usage", "view-statistics-symbolic",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_title("Uso del plan de ChatGPT")
        self.menu = Gtk.Menu()
        self.heading = self.item("Plan ChatGPT · cuota compartida")
        self.state = self.item("Esperando datos")
        self.menu.append(Gtk.SeparatorMenuItem())
        self.rows = {}
        for key in ("5h", "week"):
            self.rows[key] = (self.item(""), self.item(""), self.item(""))
            self.menu.append(Gtk.SeparatorMenuItem())
        self.updated = self.item("")
        self.menu.append(Gtk.SeparatorMenuItem())
        self.go_heading = self.item("OpenCode Go · esperando datos")
        self.go_state = self.item("Esperando consulta")
        self.go_rows = {}
        for key in ("five_hour", "weekly", "monthly"):
            self.go_rows[key] = (self.item(""), self.item(""), self.item(""))
            self.menu.append(Gtk.SeparatorMenuItem())
        self.go_updated = self.item("")
        self.item("Ver informe completo…", self.show_report)
        self.item("Abrir carpeta…", self.open_folder)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.item("Cerrar indicador (no detiene el monitor)", lambda *_: Gtk.main_quit())
        self.menu.show_all()
        self.indicator.set_menu(self.menu)
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.update()
        self.timer = GLib.timeout_add_seconds(5, self.update)

    def item(self, label, callback=None):
        item = Gtk.MenuItem.new_with_label(label)
        if callback:
            item.connect("activate", callback)
        else:
            item.set_sensitive(False)
        self.menu.append(item)
        return item

    def update(self):
        report = read_report()
        label = panel_label(report)
        try:
            if json.loads((ROOT / "panel-settings.json").read_text()).get("icon_only"):
                label = ""
        except (OSError, ValueError, AttributeError):
            pass
        self.indicator.set_label(label, "GPT  5h 100% · Sem 100%")
        self.indicator.set_icon_full(
            "view-statistics-symbolic" if report["fresh"] else "dialog-warning-symbolic",
            "Porcentajes restantes del plan" if report["fresh"] else "Datos sin actualizar",
        )
        self.state.set_label(
            ("● ChatGPT y OpenCode Go actualizados" if report["fresh"] and report.get("go_fresh")
             else "⚠ Hay datos sin actualizar — ver estados abajo")
        )
        for key, title in (("5h", "Límite de 5 horas"), ("week", "Límite semanal")):
            window = report["windows"].get(key, {})
            remaining, reset = window.get("remaining", "No disponible"), window.get("reset")
            rows = self.rows[key]
            rows[0].set_label(f"{title}: {remaining} restante")
            rows[1].set_label(f"Se restablece en {countdown(reset)}")
            try:
                reset_label = datetime.fromisoformat(reset).strftime("%d/%m %H:%M")
            except (TypeError, ValueError):
                reset_label = "No disponible"
            rows[2].set_label(f"Restablecimiento: {reset_label}")
        updated = report.get("updated")
        self.updated.set_label("Última lectura: " + (updated.strftime("%d/%m %H:%M:%S") if updated else "—"))
        self._update_opencode_go(report)
        return GLib.SOURCE_CONTINUE

    def _update_opencode_go(self, report):
        go = report.get("go_windows", {})
        status = report.get("go_status", "Sin consultar")
        self.go_heading.set_label("OpenCode Go · porcentajes restantes")
        self.go_state.set_label("● " + status if report.get("go_fresh") else "⚠ " + status)
        for key, label in (("five_hour", "Límite de 5 horas"), ("weekly", "Límite semanal"), ("monthly", "Límite mensual")):
            meter = go.get(key) or {}
            rows = self.go_rows[key]
            remaining = meter.get("remaining_percent")
            rows[0].set_label(f"{label}: {remaining:g} % restante" if isinstance(remaining, (int, float)) else f"{label}: No disponible")
            rows[1].set_label("Límite alcanzado" if meter.get("limited") else "Cuota disponible")
            rows[2].set_label("Se restablece en " + countdown(timestamp(meter["reset_at"])) if isinstance(meter.get("reset_at"), (int, float)) else "Restablecimiento: No disponible")
        fetched = report.get("go_fetched_at")
        self.go_updated.set_label("Última lectura de Go: " + (datetime.fromtimestamp(fetched).astimezone().strftime("%d/%m %H:%M:%S") if isinstance(fetched, (int, float)) else "—"))

    def show_report(self, *_):
        dialog = Gtk.MessageDialog(
            text="Uso del plan de ChatGPT", message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE,
        )
        dialog.format_secondary_text(read_report()["text"])
        dialog.connect("response", lambda dialog, response: dialog.destroy())
        dialog.show_all()

    def open_folder(self, *_):
        Gio.AppInfo.launch_default_for_uri(ROOT.as_uri(), None)

    def close(self):
        GLib.source_remove(self.timer)
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)


def main():
    with (ROOT / "panel.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("El indicador ya está abierto.")
        panel = Panel()
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (Gtk.main_quit(), GLib.SOURCE_REMOVE)[1])
        try:
            print("Indicador iniciado: " + panel_label(read_report()), flush=True)
            Gtk.main()
        finally:
            panel.close()


if __name__ == "__main__":
    main()
