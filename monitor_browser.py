#!/usr/bin/env python3
"""Legacy browser-based monitor, retained as a reference only."""

import argparse
from datetime import datetime
import fcntl
import math
import os
from pathlib import Path
import signal
import sqlite3
import time

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
URL = "https://chatgpt.com/settings/usage?tab=overview"


def firefox_cookies():
    """Read only ChatGPT cookies, without changing or copying the Firefox profile."""
    candidates = []
    for base in (
        Path.home() / "snap/firefox/common/.mozilla/firefox",
        Path.home() / ".mozilla/firefox",
    ):
        for path in base.glob("*/cookies.sqlite"):
            try:
                # immutable avoids Firefox's exclusive lock. Only committed data is read.
                with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
                    rows = db.execute(
                        "SELECT host,name,value,path,isSecure,isHttpOnly FROM moz_cookies "
                        "WHERE host IN ('.chatgpt.com','chatgpt.com')"
                    ).fetchall()
                score = sum("session-token" in row[1] for row in rows)
                if score:
                    candidates.append((score, path.stat().st_mtime, rows))
            except (sqlite3.Error, OSError):
                continue
    if not candidates:
        return []
    rows = max(candidates, key=lambda item: item[:2])[2]
    return [
        dict(name=n, value=v, domain=h, path=p, secure=bool(s), httpOnly=bool(ho))
        for h, n, v, p, s, ho in rows
    ]


def timestamp(value):
    return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds")


def duration(seconds):
    seconds = max(0, math.ceil(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{days} d {hours} h {minutes} min {seconds} s"


def render(data, fetched_at, status):
    lines = [f"Último intento: {timestamp(time.time())}", f"Estado: {status}"]
    if data is None:
        return "\n".join(lines + ["Todavía no se obtuvieron datos.", ""])
    lines += [f"Datos obtenidos: {timestamp(fetched_at)}", f"Plan: {data.get('plan_type', 'No disponible')}", ""]
    limits = data.get("rate_limit") or {}
    for key, label in (("primary_window", "Límite de 5 horas"), ("secondary_window", "Límite semanal")):
        window = limits.get(key)
        lines.append(label)
        if not window:
            lines += ["No disponible", ""]
            continue
        used = window.get("used_percent")
        if isinstance(used, (int, float)):
            lines += [f"Restante: {100 - used:g} %", f"Usado: {used:g} %"]
        reset = window.get("reset_at")
        if isinstance(reset, (int, float)):
            lines += [f"Se restablece: {timestamp(reset)}", f"Tiempo restante: {duration(reset - time.time())}"]
        lines.append("")
    return "\n".join(lines) + "\n"


def save_text(text):
    temporary = ROOT / ".uso.txt.tmp"
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(ROOT / "uso.txt")


READ_USAGE = """async (accountId) => {
    const request = async (path, headers = {}) => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 15000);
        try {
            return await fetch(path, {headers, cache: 'no-store', redirect: 'error',
                signal: controller.signal});
        } finally { clearTimeout(timer); }
    };
    const session = await request('/api/auth/session');
    if (!session.ok) return {status: session.status};
    const {accessToken} = await session.json();
    if (!accessToken) return {status: 401};
    const headers = {Authorization: 'Bearer ' + accessToken};
    if (accountId) headers['ChatGPT-Account-Id'] = accountId;
    const response = await request('/backend-api/wham/usage', headers);
    if (!response.ok) return {status: response.status,
        retryAfter: response.headers.get('retry-after')};
    const data = await response.json();
    // Exclude email, account IDs, and all authentication information.
    return {status: 200, data: {plan_type: data.plan_type, rate_limit: data.rate_limit}};
}"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=0, help="Stop after N attempts; 0 means keep running")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    with (ROOT / "monitor.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Ya hay un monitor ejecutándose.")
        (ROOT / "monitor.pid").write_text(str(os.getpid()), encoding="ascii")
        try:
            run(args)
        finally:
            (ROOT / "monitor.pid").unlink(missing_ok=True)


def run(args):
    data, fetched_at = None, None
    save_text(render(data, fetched_at, "Abriendo navegador"))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=args.headless)
        context = browser.new_context()
        context.add_cookies(firefox_cookies())
        page = context.new_page()
        account = {"id": None}

        def capture_account(request):
            if request.url == "https://chatgpt.com/backend-api/wham/usage":
                account["id"] = request.headers.get("chatgpt-account-id")

        page.on("request", capture_account)
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            print("Página abierta. Si se solicita iniciar sesión, hacelo en esta ventana.", flush=True)
            attempts = 0
            failures = 0
            while not page.is_closed():
                start = time.monotonic()
                delay = 5
                try:
                    result = page.evaluate(READ_USAGE, account["id"])
                    code = result["status"]
                    if code == 200 and isinstance(result.get("data", {}).get("rate_limit"), dict):
                        data, fetched_at = result["data"], time.time()
                        status = "OK — consulta real al servidor"
                        failures = 0
                    else:
                        failures += 1
                        status = f"HTTP {code}: esperando sesión o datos; los valores anteriores NO son actuales"
                        delay = min(300, 5 * 2 ** min(failures, 6))
                        if code == 429:
                            try:
                                delay = max(60, float(result.get("retryAfter") or 60))
                            except ValueError:
                                delay = 60
                except Exception:
                    if page.is_closed():
                        break
                    failures += 1
                    status = "Error de conexión/navegación; los valores anteriores NO son actuales"
                    delay = min(300, 5 * 2 ** min(failures, 6))
                save_text(render(data, fetched_at, status))
                print(f"{timestamp(time.time())} | {status}", flush=True)
                attempts += 1
                if args.samples and attempts >= args.samples:
                    break
                page.wait_for_timeout(max(0, delay - (time.monotonic() - start)) * 1000)
        finally:
            save_text(render(data, fetched_at, "DETENIDO — los valores ya no se actualizan"))
            browser.close()


if __name__ == "__main__":
    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        main()
    except KeyboardInterrupt:
        pass
