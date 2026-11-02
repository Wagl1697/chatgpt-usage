#!/usr/bin/env python3
"""Read-only web dashboard for the local ChatGPT usage report."""

import ast
import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get("CHATGPT_USAGE_STATE_DIR", ROOT)) / "uso.json"
HOST = os.environ.get("CHATGPT_USAGE_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("CHATGPT_USAGE_WEB_PORT", "8088"))
SCHEMA_ID = "org.gnome.shell.extensions.chatgpt-usage"
SCHEMA_DIRS = tuple(
    directory for directory in (
        Path(os.environ["CHATGPT_USAGE_SCHEMA_DIR"]) if os.environ.get("CHATGPT_USAGE_SCHEMA_DIR") else None,
        Path.home() / ".local/share/gnome-shell/extensions/chatgpt-usage@wlopez.local/schemas",
        ROOT / "gnome-extension" / "schemas",
    ) if directory is not None
)
_names_cache = {"at": 0.0, "value": {}}

PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Uso de ChatGPT</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
    margin: 0;
    background: #101114;
    color: #f1f0ee;
    font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 1000px; margin: 0 auto; padding: 24px 18px 40px; }
header { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 4px; }
h1 { font-size: 22px; margin: 0; letter-spacing: -0.02em; }
.pill {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 10px; border-radius: 999px;
    background: #22242a; color: #c9c7c2; font-size: 13px;
    border: 1px solid #30323a;
}
.pill.ok { color: #9be3b1; border-color: #2e5c3c; background: #14251a; }
.pill.warn { color: #ffd08a; border-color: #6b4c1c; background: #2a2113; }
.pill.bad { color: #ff9f9f; border-color: #6b2c2c; background: #2a1616; }
.meta { color: #a6a4a0; font-size: 13px; margin: 8px 0 22px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 14px; }
.card {
    background: #191b20; border: 1px solid #2a2c34; border-radius: 12px;
    padding: 16px; min-width: 0;
}
.card h2 { font-size: 15px; margin: 0 0 2px; overflow-wrap: anywhere; }
.card .sub { color: #a6a4a0; font-size: 12.5px; margin-bottom: 10px; overflow-wrap: anywhere; }
.metrics { display: flex; flex-wrap: wrap; gap: 10px; justify-content: space-around; }
.metric { flex: 1 1 84px; display: flex; flex-direction: column; align-items: center; gap: 4px; min-width: 76px; }
.metric .title { color: #b9b7b3; font-size: 12px; }
.ring {
    width: 62px; height: 62px; border-radius: 50%;
    background: conic-gradient(var(--c) calc(var(--p) * 1%), #30323a 0);
    display: grid; place-items: center; position: relative;
}
.ring::after { content: ""; position: absolute; inset: 6px; border-radius: 50%; background: #191b20; }
.ring span { position: relative; z-index: 1; font-size: 13px; font-weight: 600; }
.ring.low { --c: #ef5350; }
.ring.mid { --c: #f0a63a; }
.ring.ok { --c: #52c47a; }
.ring.unknown { --c: #555862; }
.reset { color: #a6a4a0; font-size: 11.5px; text-align: center; }
.bad-status { color: #ff9f9f; font-size: 12px; margin-top: 8px; }
.empty { color: #a6a4a0; padding: 18px; border: 1px dashed #353741; border-radius: 12px; }
footer { color: #777a82; font-size: 12px; margin-top: 26px; }
a { color: #8fb8ff; }
</style>
</head>
<body>
<div class="wrap">
    <header>
        <h1>Uso de ChatGPT</h1>
        <span id="state" class="pill">Cargando…</span>
    </header>
    <div class="meta" id="meta">Leyendo el informe local…</div>
    <main class="grid" id="grid"></main>
    <footer>Solo accesible desde tu tailnet · datos locales, sin credenciales.</footer>
</div>
<script>
const grid = document.getElementById('grid');
const state = document.getElementById('state');
const meta = document.getElementById('meta');

const pad = value => String(value).padStart(2, '0');

function formatReset(epoch) {
    if (typeof epoch !== 'number' || !Number.isFinite(epoch))
        return '⏰ —';
    const date = new Date(epoch * 1000);
    return `⏰ ${pad(date.getDate())}/${pad(date.getMonth() + 1)}/${String(date.getFullYear()).slice(-2)} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatTime(epoch) {
    if (typeof epoch !== 'number' || !Number.isFinite(epoch))
        return '—';
    const date = new Date(epoch * 1000);
    return `${pad(date.getDate())}/${pad(date.getMonth() + 1)}/${String(date.getFullYear()).slice(-2)} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function ageText(epoch) {
    if (typeof epoch !== 'number' || !Number.isFinite(epoch))
        return '';
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
    if (seconds < 60)
        return `hace ${seconds} s`;
    if (seconds < 3600)
        return `hace ${Math.floor(seconds / 60)} min`;
    return `hace ${Math.floor(seconds / 3600)} h`;
}

function ringClass(value) {
    if (typeof value !== 'number' || !Number.isFinite(value))
        return 'unknown';
    if (value <= 15)
        return 'low';
    if (value <= 40)
        return 'mid';
    return 'ok';
}

function metric(label, window) {
    const value = window && typeof window.remaining_percent === 'number' ? window.remaining_percent : null;
    const box = document.createElement('div');
    box.className = 'metric';
    const title = document.createElement('div');
    title.className = 'title';
    title.textContent = label;
    const ring = document.createElement('div');
    ring.className = `ring ${ringClass(value)}`;
    ring.style.setProperty('--p', value === null ? 0 : Math.max(0, Math.min(100, value)));
    const text = document.createElement('span');
    text.textContent = value === null ? '—' : `${value}%`;
    ring.appendChild(text);
    const reset = document.createElement('div');
    reset.className = 'reset';
    reset.textContent = formatReset(window ? window.reset_at : null);
    box.append(title, ring, reset);
    return box;
}

function card(title, subtitle, windows, status) {
    const element = document.createElement('section');
    element.className = 'card';
    const heading = document.createElement('h2');
    heading.textContent = title;
    const sub = document.createElement('div');
    sub.className = 'sub';
    sub.textContent = subtitle;
    const metrics = document.createElement('div');
    metrics.className = 'metrics';
    for (const [label, key] of windows)
        metrics.appendChild(metric(label, (status && status.windows ? status.windows : {})[key]));
    element.append(heading, sub, metrics);
    if (status && status.status && status.status !== 'OK') {
        const warning = document.createElement('div');
        warning.className = 'bad-status';
        warning.textContent = `⚠ ${status.status}`;
        element.appendChild(warning);
    }
    return element;
}

function render(data) {
    grid.replaceChildren();
    const accounts = Array.isArray(data.chatgpt_accounts) ? data.chatgpt_accounts : [];
    const legacy = data.windows || {};
    const legacyAccount = (!accounts.length && (legacy.five_hour || legacy.weekly))
        ? [{ label: 'ChatGPT', email: 'ChatGPT', status: data.status, windows: legacy }]
        : [];
    const list = accounts.length ? accounts : legacyAccount;
    const paused = data.polling_enabled === false;
    const age = typeof data.fetched_at === 'number'
        ? Math.max(0, Math.floor(Date.now() / 1000 - data.fetched_at)) : null;

    if (paused) {
        state.className = 'pill warn';
        state.textContent = '⏸ Consultas pausadas';
    } else if (age !== null && age > 90) {
        state.className = 'pill warn';
        state.textContent = '⚠ Datos sin actualizar';
    } else if (!list.length) {
        state.className = 'pill warn';
        state.textContent = 'Sin cuentas activas';
    } else {
        state.className = 'pill ok';
        state.textContent = '● Actualizado';
    }

    meta.textContent = [
        data.status || 'Sin estado',
        data.fetched_at ? `Última lectura: ${formatTime(data.fetched_at)} (${ageText(data.fetched_at)})` : 'Sin lecturas',
    ].join(' · ');

    if (!list.length) {
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = 'No hay cuentas activas. Activalas en Preferencias → Cuentas controladas.';
        grid.appendChild(empty);
    }
    for (const account of list) {
        const name = account.email || account.label || 'ChatGPT';
        const plan = account.plan_type ? `plan ${account.plan_type}` : 'ChatGPT';
        grid.appendChild(card(name, plan, [['5 horas', 'five_hour'], ['Semanal', 'weekly']], account));
    }

    const go = data.opencode_go;
    if (go && typeof go === 'object') {
        const goCard = card('OpenCode Go', go.status || 'OpenCode Go', [
            ['5 horas', 'five_hour'], ['Semanal', 'weekly'], ['Mensual', 'monthly'],
        ], { status: String(go.status || '').startsWith('OK') ? 'OK' : go.status, windows: go.windows || {} });
        grid.appendChild(goCard);
    }
}

async function load() {
    try {
        const response = await fetch(`/api/uso.json?t=${Date.now()}`, { cache: 'no-store' });
        if (!response.ok)
            throw new Error(`HTTP ${response.status}`);
        render(await response.json());
    } catch (error) {
        state.className = 'pill bad';
        state.textContent = '⚠ Sin conexión con el monitor';
        meta.textContent = 'No se pudo leer uso.json.';
    }
}

load();
setInterval(load, 5000);
document.addEventListener('visibilitychange', () => {
    if (!document.hidden)
        load();
});
</script>
</body>
</html>
"""


WIDGET_PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Uso de ChatGPT</title>
<style>
* { box-sizing: border-box; }
html, body { margin: 0; background: transparent; }
body { color: #f1f0ee; font: 14px/1.3 system-ui, -apple-system, "Segoe UI", sans-serif; }
.widget {
    background: rgba(17, 18, 22, 0.94);
    border: 1px solid #2c2e36;
    border-radius: 16px;
    padding: 10px 12px;
    display: grid;
    gap: 7px;
}
.head { display: flex; justify-content: space-between; gap: 8px; color: #a6a4a0; font-size: 11px; }
.state.ok { color: #9be3b1; }
.state.warn { color: #ffd08a; }
.state.bad { color: #ff9f9f; }
.row { display: flex; align-items: center; gap: 6px; min-width: 0; }
.name { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
.badge {
    flex: 0 0 auto;
    padding: 2px 6px;
    border-radius: 7px;
    background: #24262d;
    color: #d8d6d2;
    font-size: 11.5px;
    font-variant-numeric: tabular-nums;
}
.badge.low { background: #4a1f1f; color: #ffb3b3; }
.badge.mid { background: #4a3618; color: #ffd08a; }
.badge.ok { background: #173a25; color: #a7e9bd; }
.empty { color: #a6a4a0; font-size: 12px; }
</style>
</head>
<body>
<div class="widget">
    <div class="head">
        <span id="state" class="state">Cargando…</span>
        <span id="age"></span>
    </div>
    <div id="rows"></div>
    <div class="empty" id="empty" hidden>Sin cuentas activas</div>
</div>
<script>
const rows = document.getElementById('rows');
const state = document.getElementById('state');
const age = document.getElementById('age');
const empty = document.getElementById('empty');

function level(value) {
    if (typeof value !== 'number')
        return '';
    if (value <= 15)
        return 'low';
    if (value <= 40)
        return 'mid';
    return 'ok';
}

function badge(label, value) {
    const element = document.createElement('span');
    element.className = `badge ${level(value)}`;
    element.textContent = `${label} ${typeof value === 'number' ? `${value}%` : '—'}`;
    return element;
}

function render(data) {
    const lines = Array.isArray(data.lines) ? data.lines : [];
    if (data.paused) {
        state.className = 'state warn';
        state.textContent = '⏸ Pausado';
    } else if (data.stale) {
        state.className = 'state warn';
        state.textContent = '⚠ Sin actualizar';
    } else {
        state.className = 'state ok';
        state.textContent = '● Actualizado';
    }
    age.textContent = typeof data.age_seconds === 'number' ? `hace ${data.age_seconds} s` : '';
    rows.replaceChildren();
    empty.hidden = lines.length > 0;
    for (const line of lines) {
        const row = document.createElement('div');
        row.className = 'row';
        const name = document.createElement('span');
        name.className = 'name';
        name.textContent = line.name + (line.status && line.status !== 'OK' ? ' ⚠' : '');
        name.title = [line.reset_five_hour, line.reset_weekly, line.reset_monthly]
            .filter(Boolean).join(' · ');
        row.appendChild(name);
        row.appendChild(badge('5h', line.five_hour));
        row.appendChild(badge('sem', line.weekly));
        if (line.kind === 'opencode')
            row.appendChild(badge('mes', line.monthly));
        rows.appendChild(row);
    }
}

async function load() {
    try {
        const response = await fetch(`/api/summary.json?t=${Date.now()}`, { cache: 'no-store' });
        if (!response.ok)
            throw new Error(`HTTP ${response.status}`);
        render(await response.json());
    } catch (error) {
        state.className = 'state bad';
        state.textContent = '⚠ Sin conexión';
        age.textContent = '';
        rows.replaceChildren();
        empty.hidden = false;
        empty.textContent = 'No se pudo leer el monitor.';
    }
}

load();
setInterval(load, 30000);
document.addEventListener('visibilitychange', () => {
    if (!document.hidden)
        load();
});
</script>
</body>
</html>
"""


def _read_report():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _display_names():
    now = time.time()
    if now - _names_cache["at"] < 30:
        return _names_cache["value"]
    value = {}
    for directory in SCHEMA_DIRS:
        if not (directory / "gschemas.compiled").is_file():
            continue
        try:
            completed = subprocess.run(
                ["gsettings", "--schemadir", str(directory), "get", SCHEMA_ID, "account-display-names"],
                capture_output=True, text=True, check=False, timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode != 0:
            continue
        try:
            raw = ast.literal_eval(completed.stdout.strip())
            parsed = json.loads(raw)
        except (ValueError, SyntaxError):
            continue
        if isinstance(parsed, dict):
            value = parsed
            break
    _names_cache.update({"at": now, "value": value})
    return value


def _format_percent(value):
    return f"{value}%" if isinstance(value, (int, float)) else "—"


def _format_reset(epoch):
    if not isinstance(epoch, (int, float)):
        return "⏰ —"
    return time.strftime("⏰ %d/%m/%y %H:%M", time.localtime(epoch))


def _ok_status(value):
    value = str(value or "")
    return "OK" if value.startswith("OK") else value


def build_summary():
    report = _read_report()
    names = _display_names()
    lines = []
    for account in report.get("chatgpt_accounts") or []:
        email = str(account.get("email") or account.get("label") or "ChatGPT")
        key = email.casefold()
        name = names.get(key) or (email.split("@", 1)[0] if "@" in email else email)
        windows = account.get("windows") or {}
        five = windows.get("five_hour") or {}
        weekly = windows.get("weekly") or {}
        lines.append({
            "kind": "chatgpt",
            "name": name,
            "email": email,
            "status": _ok_status(account.get("status")),
            "five_hour": five.get("remaining_percent") if isinstance(five.get("remaining_percent"), (int, float)) else None,
            "weekly": weekly.get("remaining_percent") if isinstance(weekly.get("remaining_percent"), (int, float)) else None,
            "monthly": None,
            "reset_five_hour": _format_reset(five.get("reset_at")),
            "reset_weekly": _format_reset(weekly.get("reset_at")),
            "reset_monthly": "⏰ —",
        })
    go = report.get("opencode_go")
    if isinstance(go, dict):
        windows = go.get("windows") or {}
        five = windows.get("five_hour") or {}
        weekly = windows.get("weekly") or {}
        monthly = windows.get("monthly") or {}
        lines.append({
            "kind": "opencode",
            "name": names.get("opencode-go") or "OpenCode Go",
            "email": None,
            "status": _ok_status(go.get("status")),
            "five_hour": five.get("remaining_percent") if isinstance(five.get("remaining_percent"), (int, float)) else None,
            "weekly": weekly.get("remaining_percent") if isinstance(weekly.get("remaining_percent"), (int, float)) else None,
            "monthly": monthly.get("remaining_percent") if isinstance(monthly.get("remaining_percent"), (int, float)) else None,
            "reset_five_hour": _format_reset(five.get("reset_at")),
            "reset_weekly": _format_reset(weekly.get("reset_at")),
            "reset_monthly": _format_reset(monthly.get("reset_at")),
        })
    fetched = report.get("fetched_at")
    age = max(0, int(time.time() - fetched)) if isinstance(fetched, (int, float)) else None
    paused = report.get("polling_enabled") is False
    stale = paused or age is None or age > 90
    text_lines = []
    for line in lines:
        parts = [
            f"{line['name']} · 5h {_format_percent(line['five_hour'])}",
            f"sem {_format_percent(line['weekly'])}",
        ]
        if line["kind"] == "opencode":
            parts.append(f"mes {_format_percent(line['monthly'])}")
        text_lines.append(" · ".join(parts))
    updated_text = time.strftime("%d/%m/%y %H:%M", time.localtime(fetched)) if isinstance(fetched, (int, float)) else "—"
    return {
        "updated": fetched,
        "updated_text": updated_text,
        "age_seconds": age,
        "paused": paused,
        "stale": bool(stale),
        "status": report.get("status"),
        "lines": lines,
        "text": "\n".join(text_lines + [f"Actualizado {updated_text}"]),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ChatGPTUsage/1"

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/widget":
            self._send(200, WIDGET_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/summary.json":
            body = json.dumps(build_summary(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/api/summary.txt":
            self._send(200, build_summary()["text"].encode("utf-8"), "text/plain; charset=utf-8")
        elif path == "/api/uso.json":
            try:
                body = STATE.read_bytes()
                json.loads(body)
            except (OSError, ValueError):
                body = b"{}"
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/healthz":
            self._send(200, b"ok\n", "text/plain; charset=utf-8")
        else:
            self._send(404, b"No encontrado\n", "text/plain; charset=utf-8")

    def log_message(self, _format, *_args):
        pass


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Panel web en http://{HOST}:{PORT}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
