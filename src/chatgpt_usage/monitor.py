#!/usr/bin/env python3
"""Consult ChatGPT plan limits over HTTPS, without launching a browser."""

import argparse
import base64
from datetime import datetime
from email.utils import parsedate_to_datetime
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import time
from urllib.parse import urlsplit

from curl_cffi import requests

from chatgpt_usage import browser_accounts

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "https://chatgpt.com"
OPENCODE_USAGE_INTERVAL = 10
CHATGPT_USAGE_INTERVAL = 10


class MonitorError(Exception):
    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def firefox_cookie_profiles_named():
    """Read read-only cookie sets with profile names, without merging accounts."""
    candidates = []
    bases = (
        Path.home() / "snap/firefox/common/.mozilla/firefox",
        Path.home() / ".mozilla/firefox",
        Path.home() / ".var/app/org.mozilla.firefox/.mozilla/firefox",
    )
    profile = os.environ.get("CHATGPT_USAGE_FIREFOX_PROFILE")
    paths = [Path(profile).expanduser() / "cookies.sqlite"] if profile else [
        path for base in bases for path in base.glob("*/cookies.sqlite")
    ]
    for path in paths:
        rows = None
        # Normal read-only mode sees Firefox's committed WAL updates. Immutable
        # mode is a fallback for profiles whose DB is locked, but may miss WAL data.
        for options in ("?mode=ro", "?mode=ro&immutable=1"):
            try:
                with sqlite3.connect(path.as_uri() + options, uri=True, timeout=2) as db:
                    rows = db.execute(
                        "SELECT host,name,value,path,isSecure FROM moz_cookies "
                        "WHERE host IN ('.chatgpt.com','chatgpt.com') "
                        "AND (expiry = 0 OR expiry > ?)", (int(time.time()),)
                    ).fetchall()
                break
            except sqlite3.Error:
                continue
        try:
            if rows is None:
                continue
            score = sum("session-token" in row[1] for row in rows)
            if score:
                candidates.append((score, path.stat().st_mtime, path.parent.name, rows))
        except OSError:
            continue
    if not candidates:
        raise MonitorError("No hay una sesión local de ChatGPT disponible")
    candidates.sort(key=lambda item: item[:2], reverse=True)
    return [(profile_name, rows) for _, _, profile_name, rows in candidates]


def firefox_cookie_profiles():
    """Return cookie rows without profile names for legacy callers/tests."""
    return [rows for _, rows in firefox_cookie_profiles_named()]


def firefox_cookies():
    """Return the newest candidate for callers that need one profile."""
    return firefox_cookie_profiles()[0]


def retry_seconds(value):
    if not value:
        return 60
    try:
        return max(60, float(value))
    except ValueError:
        try:
            return max(60, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return 60


def token_lifetime(token):
    """Use exp only to schedule refresh, not as an authentication check."""
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return max(1, min(600, float(claims["exp"]) - time.time() - 60))
    except (ValueError, IndexError, KeyError, TypeError):
        return 300


class UsageClient:
    def __init__(self):
        # Browser-compatible TLS/HTTP from a native library, no browser process.
        self.session = requests.Session(impersonate="chrome", timeout=20)
        self.token = None
        self.refresh_at = 0
        self.email = None

    def close(self):
        self.token = None
        self.email = None
        self.session.close()

    def get_json(self, path, headers=None):
        try:
            response = self.session.get(
                ORIGIN + path, headers=headers or {}, allow_redirects=False
            )
        except requests.RequestsError:
            raise MonitorError("Error de conexión HTTPS") from None
        if response.status_code != 200:
            raise MonitorError(
                f"HTTP {response.status_code}", response.status_code,
                response.headers.get("retry-after"),
            )
        try:
            data = response.json()
        except ValueError:
            raise MonitorError("El servidor no devolvió JSON") from None
        if not isinstance(data, dict):
            raise MonitorError("Respuesta inesperada del servidor")
        return data

    def authenticate(self, cookies=None):
        self.session.cookies.clear()
        for host, name, value, path, secure in cookies if cookies is not None else firefox_cookies():
            self.session.cookies.set(name, value, domain=host, path=path, secure=bool(secure))
        data = self.get_json("/api/auth/session")
        token = data.get("accessToken")
        if not isinstance(token, str) or not token:
            raise MonitorError("La sesión de ChatGPT venció; iniciá sesión de nuevo", 401)
        self.token = token
        self.refresh_at = time.monotonic() + token_lifetime(token)
        user = data.get("user")
        self.email = user.get("email") if isinstance(user, dict) and isinstance(user.get("email"), str) else None

    def read_account(self, cookies):
        """Read one selected account without falling back to another profile."""
        self.session.cookies.clear()
        for cookie in cookies:
            self.session.cookies.set(
                cookie["name"], cookie["value"], domain=cookie["domain"],
                path=cookie["path"], secure=bool(cookie["secure"]),
            )
        session_data = self.get_json("/api/auth/session")
        user = session_data.get("user")
        self.email = user.get("email") if isinstance(user, dict) and isinstance(user.get("email"), str) else None
        token = session_data.get("accessToken")
        if not isinstance(token, str) or not token:
            raise MonitorError("La sesión de ChatGPT venció; iniciá sesión de nuevo", 401)
        self.token = token
        data = self.get_json(
            "/backend-api/wham/usage", {"Authorization": "Bearer " + token}
        )
        limits = data.get("rate_limit")
        if not isinstance(limits, dict):
            raise MonitorError("El servidor no incluyó los límites del plan")
        return {"email": self.email, "plan_type": data.get("plan_type"), "rate_limit": limits}

    def read(self):
        cached = False
        if self.token and time.monotonic() < self.refresh_at:
            try:
                data = self.get_json(
                    "/backend-api/wham/usage", {"Authorization": "Bearer " + self.token}
                )
                cached = True
            except MonitorError as error:
                if error.status != 401:
                    raise
                self.token = None
        last_error = None
        if not cached:
            for cookies in firefox_cookie_profiles():
                try:
                    self.authenticate(cookies)
                    data = self.get_json(
                        "/backend-api/wham/usage", {"Authorization": "Bearer " + self.token}
                    )
                except MonitorError as error:
                    if error.status != 401:
                        raise
                    last_error = error
                    self.token = None
                    continue
                break
            else:
                raise last_error or MonitorError("ChatGPT no aceptó la sesión de Firefox", 401)
        limits = data.get("rate_limit")
        if not isinstance(limits, dict):
            raise MonitorError("El servidor no incluyó los límites del plan")
        # Do not retain email, IDs, or other fields returned by the API.
        return {"plan_type": data.get("plan_type"), "rate_limit": limits}

    def read_accounts(self):
        """Query each Firefox profile independently; never substitute another account."""
        accounts = {}
        for profile_name, cookies in firefox_cookie_profiles_named():
            self.session.cookies.clear()
            for host, name, value, path, secure in cookies:
                self.session.cookies.set(name, value, domain=host, path=path, secure=bool(secure))
            identity = f"Perfil {profile_name}"
            email = None
            record = {"label": identity, "email": None, "status": "Sin consultar", "fetched_at": None, "windows": {}}
            try:
                session_data = self.get_json("/api/auth/session")
                user = session_data.get("user")
                if isinstance(user, dict) and isinstance(user.get("email"), str):
                    email = user["email"].strip()
                    if email:
                        identity = email
                record["label"] = identity
                record["email"] = email
                token = session_data.get("accessToken")
                if not isinstance(token, str) or not token:
                    raise MonitorError("Sesión vencida", 401)
                usage = self.get_json("/backend-api/wham/usage", {"Authorization": "Bearer " + token})
                rate_limit = usage.get("rate_limit")
                if not isinstance(rate_limit, dict):
                    raise MonitorError("OpenAI no devolvió los límites de este perfil")
                record["windows"] = windows_from_rate_limit(rate_limit)
                record.update({
                    "status": "OK",
                    "fetched_at": time.time(),
                    "plan_type": usage.get("plan_type"),
                })
            except MonitorError as error:
                # Keep failure attached to this identity; never fall back to a
                # different profile and label its quota as this account's.
                record["status"] = str(error)
                record["retry_after"] = error.retry_after
            key = (email or profile_name).casefold()
            previous = accounts.get(key)
            if previous is None or (not previous.get("windows") and record.get("windows")):
                accounts[key] = record
        return list(accounts.values())


def windows_from_rate_limit(rate_limit):
    windows = {}
    for source, target in (("primary_window", "five_hour"), ("secondary_window", "weekly")):
        window = rate_limit.get(source) if isinstance(rate_limit, dict) else None
        if not isinstance(window, dict):
            continue
        used, reset = window.get("used_percent"), window.get("reset_at")
        windows[target] = {
            "usage_percent": used if isinstance(used, (int, float)) else None,
            "remaining_percent": 100 - used if isinstance(used, (int, float)) else None,
            "reset_at": reset if isinstance(reset, (int, float)) else None,
        }
    return windows


def read_tracked_accounts(client, entries):
    """Query only enabled accounts using their saved browser sources."""
    cache = {}
    results = []
    for entry in entries:
        if not entry.get("enabled", True):
            continue
        record = {
            "label": entry["email"],
            "email": entry["email"],
            "status": "Sin consultar",
            "fetched_at": None,
            "windows": {},
        }
        errors = []
        for source in entry.get("sources") or []:
            browser = source.get("browser")
            profile_name = source.get("profile")
            try:
                profile = browser_accounts.find_profile(source, cache)
            except browser_accounts.AccountError as error:
                errors.append(str(error))
                continue
            if profile is None:
                errors.append(f"{browser}/{profile_name}: perfil no encontrado")
                continue
            try:
                cookies = browser_accounts.read_cookies(profile)
                report = client.read_account(cookies)
            except (browser_accounts.AccountError, MonitorError) as error:
                errors.append(f"{browser}/{profile_name}: {error}")
                continue
            email = report.get("email")
            if email and email.casefold() != entry["email"].casefold():
                errors.append(f"{browser}/{profile_name}: ahora tiene otra cuenta ({email})")
                continue
            record.update({
                "email": email or entry["email"],
                "status": "OK",
                "fetched_at": time.time(),
                "plan_type": report.get("plan_type"),
                "windows": windows_from_rate_limit(report.get("rate_limit") or {}),
            })
            break
        else:
            record["status"] = "; ".join(errors) or "sin fuentes guardadas"
        results.append(record)
    return results


def opencode_database():
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return data_home / "opencode" / "opencode.db"


def opencode_credential():
    """Read the active OpenCode Console integration without modifying its DB."""
    path = opencode_database()
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
            row = db.execute(
                "SELECT value FROM credential WHERE integration_id = ? AND active = 1 LIMIT 1",
                ("opencode",),
            ).fetchone()
    except (sqlite3.Error, OSError):
        raise MonitorError("No se pudo leer la conexión local de OpenCode Console") from None
    if not row:
        raise MonitorError("OpenCode Console no está conectado en esta instalación")
    try:
        credential = json.loads(row[0])
    except (TypeError, ValueError):
        raise MonitorError("La conexión local de OpenCode no tiene un formato válido") from None
    metadata = credential.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("orgID"), str):
        raise MonitorError("La conexión de OpenCode no tiene un workspace asociado")
    if credential.get("type") == "oauth":
        token = credential.get("access")
    elif credential.get("type") == "api":
        token = credential.get("key")
    else:
        token = None
    if not isinstance(token, str) or not token:
        raise MonitorError("La conexión de OpenCode no contiene una credencial utilizable")
    server = metadata.get("server", "https://opencode.ai/console")
    try:
        parsed = urlsplit(server)
    except (TypeError, ValueError):
        parsed = None
    if not parsed or parsed.scheme != "https" or parsed.hostname != "opencode.ai" or parsed.port:
        raise MonitorError("La conexión de OpenCode no usa el servidor oficial HTTPS")
    return token, parsed.hostname, metadata["orgID"]


def parse_opencode_go(payload):
    """Keep only Go quota meters from the official usage response."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        raise MonitorError("OpenCode no devolvió los medidores de Go")
    meters = {}
    for source, target in (("rolling", "five_hour"), ("weekly", "weekly"), ("monthly", "monthly")):
        meter = usage.get(source)
        if not isinstance(meter, dict):
            raise MonitorError("OpenCode no devolvió todos los medidores de Go")
        percent, resets_at = meter.get("percent"), meter.get("resetsAt")
        if not isinstance(percent, (int, float)) or not 0 <= percent <= 100:
            raise MonitorError("OpenCode devolvió un porcentaje de uso inválido")
        if not isinstance(resets_at, str):
            raise MonitorError("OpenCode no devolvió el restablecimiento de un medidor")
        try:
            reset = datetime.fromisoformat(resets_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            raise MonitorError("OpenCode devolvió una fecha de restablecimiento inválida") from None
        meters[target] = {
            "usage_percent": percent,
            "remaining_percent": 100 - percent,
            "limited": meter.get("status") == "rate-limited",
            "reset_at": reset,
        }
    return meters


def read_opencode_go():
    token, host, workspace_id = opencode_credential()
    try:
        response = requests.get(
            f"https://{host}/zen/go/v1/usage",
            headers={
                "Authorization": "Bearer " + token,
                "x-opencode-org-id": workspace_id,
                "Accept": "application/json",
            },
            impersonate="chrome",
            timeout=20,
            allow_redirects=False,
        )
    except requests.RequestsError:
        raise MonitorError("Error de conexión HTTPS con OpenCode Go") from None
    if response.status_code != 200:
        if response.status_code == 401:
            raise MonitorError("La sesión de OpenCode Console venció; abrí OpenCode y volvé a conectar la cuenta", 401, response.headers.get("retry-after"))
        if response.status_code == 403:
            raise MonitorError("OpenCode Go rechazó el workspace; revisá la suscripción o la conexión", 403, response.headers.get("retry-after"))
        raise MonitorError(f"OpenCode Go respondió HTTP {response.status_code}", response.status_code, response.headers.get("retry-after"))
    try:
        payload = response.json()
    except ValueError:
        raise MonitorError("OpenCode Go no devolvió JSON válido") from None
    return parse_opencode_go(payload)


def timestamp(value):
    return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds")


def duration(seconds):
    seconds = max(0, math.ceil(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{days} d {hours} h {minutes} min {seconds} s"


def render(data, fetched_at, status, opencode_go=None, go_fetched_at=None, go_status="Sin consultar", chatgpt_accounts=None):
    lines = [f"Último intento: {timestamp(time.time())}", f"Estado: {status}", "Modo: HTTPS directo, sin navegador"]
    if chatgpt_accounts:
        for account in chatgpt_accounts:
            lines += [f"Cuenta ChatGPT: {account.get('email') or account.get('label')}", f"Estado de la cuenta: {account.get('status', 'Desconocido')}"]
            if account.get("fetched_at") is not None:
                lines.append(f"Datos obtenidos: {timestamp(account['fetched_at'])}")
            lines.append(f"Plan: {account.get('plan_type') or 'No disponible'}")
            for key, label in (("five_hour", "Límite de 5 horas"), ("weekly", "Límite semanal")):
                window = (account.get("windows") or {}).get(key)
                lines.append(label)
                if not window:
                    lines += ["No disponible", ""]
                    continue
                remaining = window.get("remaining_percent")
                if isinstance(remaining, (int, float)):
                    lines.append(f"Restante: {remaining:g} %")
                reset = window.get("reset_at")
                if isinstance(reset, (int, float)):
                    lines += [f"Se restablece: {timestamp(reset)}", f"Tiempo restante: {duration(reset - time.time())}"]
                lines.append("")
    elif data is None:
        lines += ["Todavía no se obtuvieron datos de ChatGPT.", ""]
    else:
        lines += [f"Datos obtenidos: {timestamp(fetched_at)}", f"Plan: {data.get('plan_type') or 'No disponible'}", ""]
        limits = data.get("rate_limit") or {}
        for key, label in (("primary_window", "Límite de 5 horas"), ("secondary_window", "Límite semanal")):
            window = limits.get(key)
            lines.append(label)
            if not isinstance(window, dict):
                lines += ["No disponible", ""]
                continue
            used = window.get("used_percent")
            if isinstance(used, (int, float)):
                lines += [f"Restante: {100 - used:g} %", f"Usado: {used:g} %"]
            reset = window.get("reset_at")
            if isinstance(reset, (int, float)):
                lines += [f"Se restablece: {timestamp(reset)}", f"Tiempo restante: {duration(reset - time.time())}"]
            lines.append("")
    lines += [f"OpenCode Go: {go_status}"]
    if go_fetched_at is not None:
        lines.append(f"Datos OpenCode Go obtenidos: {timestamp(go_fetched_at)}")
    for key, label in (("five_hour", "OpenCode Go · límite de 5 horas"), ("weekly", "OpenCode Go · límite semanal"), ("monthly", "OpenCode Go · límite mensual")):
        meter = (opencode_go or {}).get(key)
        lines.append(label)
        if not meter:
            lines += ["No disponible", ""]
            continue
        lines.append(f"Restante: {meter['remaining_percent']:g} %")
        lines.append(f"Usado: {meter['usage_percent']:g} %")
        if meter.get("limited"):
            lines.append("Límite alcanzado")
        lines += [f"Se restablece: {timestamp(meter['reset_at'])}", f"Tiempo restante: {duration(meter['reset_at'] - time.time())}", ""]
    return "\n".join(lines) + "\n"


def save_text(text):
    temporary = ROOT / ".uso.txt.tmp"
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(ROOT / "uso.txt")


def save_report(data, fetched_at, status, opencode_go=None, go_fetched_at=None, go_status="Sin consultar", chatgpt_accounts=None, polling_enabled=True):
    """Publish only usage fields for the native GNOME panel, never credentials."""
    save_text(render(data, fetched_at, status, opencode_go, go_fetched_at, go_status, chatgpt_accounts))
    report = {"status": status, "fetched_at": fetched_at, "windows": {}, "polling_enabled": polling_enabled}
    if data:
        for source, target in (("primary_window", "five_hour"), ("secondary_window", "weekly")):
            window = data["rate_limit"].get(source)
            if not isinstance(window, dict):
                continue
            used, reset = window.get("used_percent"), window.get("reset_at")
            report["windows"][target] = {
                "remaining_percent": 100 - used if isinstance(used, (int, float)) else None,
                "reset_at": reset if isinstance(reset, (int, float)) else None,
            }
    serialized_accounts = []
    for account in chatgpt_accounts or []:
        serialized_accounts.append({
            "label": account.get("label"),
            "email": account.get("email"),
            "status": account.get("status", "Desconocido"),
            "fetched_at": account.get("fetched_at"),
            "plan_type": account.get("plan_type"),
            "windows": account.get("windows", {}),
        })
    report["chatgpt_accounts"] = serialized_accounts
    temporary = ROOT / ".uso.json.tmp"
    go_report = {"status": go_status, "fetched_at": go_fetched_at, "windows": opencode_go or {}}
    report["opencode_go"] = go_report
    temporary.write_text(json.dumps(report, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(ROOT / "uso.json")


def account_key(account):
    return str(account.get("email") or account.get("label") or "unknown").casefold()


def merge_chatgpt_accounts(previous, current):
    old = {account_key(account): account for account in previous}
    merged = []
    for account in current:
        account = dict(account)
        prior = old.get(account_key(account))
        if prior and account.get("status") != "OK" and prior.get("windows"):
            account["windows"] = prior["windows"]
            account["fetched_at"] = prior.get("fetched_at")
            account["plan_type"] = prior.get("plan_type")
        merged.append(account)
    return merged


def legacy_account_data(accounts):
    account = next((item for item in accounts if item.get("status") == "OK"), None)
    if not account:
        return None
    windows = account.get("windows") or {}
    rate_limit = {}
    for key, target in (("five_hour", "primary_window"), ("weekly", "secondary_window")):
        window = windows.get(key) or {}
        remaining, reset = window.get("remaining_percent"), window.get("reset_at")
        if isinstance(remaining, (int, float)):
            rate_limit[target] = {"used_percent": 100 - remaining, "reset_at": reset}
    return {"plan_type": account.get("plan_type"), "rate_limit": rate_limit}


def polling_enabled():
    """The top-bar switch controls this local flag; a missing file means enabled."""
    try:
        config = json.loads((ROOT / "polling.json").read_text(encoding="utf-8"))
        return config.get("enabled") is not False
    except (OSError, ValueError, AttributeError):
        return True


def run(samples):
    client = UsageClient()
    data, fetched_at = None, None
    chatgpt_accounts = []
    failures = 0
    attempts = 0
    go_data, go_fetched_at = None, None
    go_status = "Esperando primera consulta"
    go_failures = 0
    next_go_poll = 0.0
    next_chatgpt_poll = 0.0
    was_enabled = True
    save_report(data, fetched_at, "Iniciando consulta directa", go_data, go_fetched_at, go_status, chatgpt_accounts)
    try:
        while True:
            start = time.monotonic()
            delay = 5
            is_enabled = polling_enabled()
            if not is_enabled:
                status = "PAUSADO — consultas desactivadas desde la barra"
                go_status = "PAUSADO — consultas desactivadas desde la barra"
                if was_enabled:
                    print(f"{timestamp(time.time())} | Consultas pausadas desde la barra", flush=True)
                was_enabled = False
                save_report(data, fetched_at, status, go_data, go_fetched_at, go_status, chatgpt_accounts, polling_enabled=False)
                attempts += 1
                if samples and attempts >= samples:
                    return 0
                time.sleep(max(0, delay - (time.monotonic() - start)))
                continue
            if not was_enabled:
                next_chatgpt_poll = 0.0
                next_go_poll = 0.0
                print(f"{timestamp(time.time())} | Consultas reanudadas desde la barra", flush=True)
            was_enabled = True
            if time.monotonic() >= next_chatgpt_poll:
                try:
                    tracked = browser_accounts.load_tracked_accounts()
                    selection_error = None
                except browser_accounts.AccountError as error:
                    tracked = None
                    selection_error = str(error)
                if tracked is None:
                    status = f"No se pudo leer la selección de cuentas: {selection_error}"
                    next_chatgpt_poll = time.monotonic() + 30
                    chatgpt_accounts = [
                        {**item, "status": status} for item in chatgpt_accounts
                    ]
                else:
                    try:
                        using_tracked = bool(tracked)
                        fresh_accounts = (
                            read_tracked_accounts(client, tracked)
                            if using_tracked else client.read_accounts()
                        )
                        chatgpt_accounts = merge_chatgpt_accounts(chatgpt_accounts, fresh_accounts)
                        successful = [item for item in chatgpt_accounts if item.get("status") == "OK"]
                        if successful:
                            data = legacy_account_data(chatgpt_accounts)
                            fetched_at = max(item["fetched_at"] for item in successful if item.get("fetched_at"))
                            failures = 0
                        total = len(chatgpt_accounts)
                        if total and len(successful) == total:
                            status = f"OK — ChatGPT ({total} cuentas, separadas)"
                            next_chatgpt_poll = time.monotonic() + CHATGPT_USAGE_INTERVAL
                        elif total:
                            status = f"Parcial — ChatGPT ({len(successful)}/{total} cuentas actualizadas)"
                            failures += 1
                            backoff = min(300, 10 * 2 ** min(failures - 1, 5))
                            server_retry = max((retry_seconds(item["retry_after"]) for item in chatgpt_accounts if item.get("retry_after")), default=0)
                            next_chatgpt_poll = time.monotonic() + max(backoff, server_retry)
                        elif using_tracked:
                            status = "Sin cuentas activas — activalas en Preferencias"
                            data = None
                            fetched_at = None
                            next_chatgpt_poll = time.monotonic() + CHATGPT_USAGE_INTERVAL
                        else:
                            raise MonitorError("No se encontraron perfiles de Firefox")
                    except MonitorError as error:
                        failures += 1
                        status = f"{error}; los valores anteriores NO son actuales"
                        next_chatgpt_poll = time.monotonic() + min(300, 15 * 2 ** min(failures - 1, 5))
                        if error.status == 429:
                            next_chatgpt_poll = time.monotonic() + retry_seconds(error.retry_after)
                        chatgpt_accounts = [
                            {**item, "status": str(error)} for item in chatgpt_accounts
                        ]
            else:
                status = "OK — ChatGPT (datos por cuenta)" if all(item.get("status") == "OK" for item in chatgpt_accounts) else "Parcial — revisar cuentas ChatGPT"
            if time.monotonic() >= next_go_poll:
                try:
                    go_data = read_opencode_go()
                    go_fetched_at = time.time()
                    go_status = "OK — OpenCode Go"
                    go_failures = 0
                    next_go_poll = time.monotonic() + OPENCODE_USAGE_INTERVAL
                except MonitorError as error:
                    go_failures += 1
                    go_status = f"{error}; los valores anteriores NO son actuales"
                    go_delay = min(300, 15 * 2 ** min(go_failures - 1, 5))
                    if error.status == 429:
                        go_delay = max(go_delay, retry_seconds(error.retry_after))
                    next_go_poll = time.monotonic() + go_delay
            save_report(data, fetched_at, status, go_data, go_fetched_at, go_status, chatgpt_accounts, polling_enabled=True)
            print(f"{timestamp(time.time())} | HTTPS directo | {status}", flush=True)
            attempts += 1
            if samples and attempts >= samples:
                return 0 if failures == 0 else 1
            time.sleep(max(0, delay - (time.monotonic() - start)))
    finally:
        client.close()
        stopped_accounts = [{**account, "status": "Monitor detenido — los valores ya no se actualizan"} for account in chatgpt_accounts]
        save_report(data, fetched_at, "DETENIDO — los valores ya no se actualizan", go_data, go_fetched_at, "Monitor detenido — los valores ya no se actualizan", stopped_accounts, polling_enabled=polling_enabled())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=0, help="Stop after N attempts; 0 means keep running")
    args = parser.parse_args()
    if args.samples < 0:
        parser.error("--samples no puede ser negativo")
    os.umask(0o077)
    with (ROOT / "monitor.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Ya hay un monitor ejecutándose.")
        (ROOT / "monitor.pid").write_text(str(os.getpid()), encoding="ascii")
        try:
            return run(args.samples)
        finally:
            (ROOT / "monitor.pid").unlink(missing_ok=True)


if __name__ == "__main__":
    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
