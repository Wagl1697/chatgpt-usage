#!/usr/bin/env python3
"""Read browser cookies and the account selection stored in GSettings."""

import ast
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

SCHEMA_ID = "org.gnome.shell.extensions.chatgpt-usage"
SETTINGS_KEY = "tracked-accounts"
BROWSER_FAMILIES = ("firefox", "chrome", "chromium", "edge", "brave")
FIREFOX_ROOTS = (
    Path.home() / "snap/firefox/common/.mozilla/firefox",
    Path.home() / ".var/app/org.mozilla.firefox/.mozilla/firefox",
    Path.home() / ".mozilla/firefox",
)
CHROMIUM_CONFIGS = {
    "chrome": Path.home() / ".config/google-chrome",
    "chromium": Path.home() / ".config/chromium",
    "edge": Path.home() / ".config/microsoft-edge",
    "brave": Path.home() / ".config/BraveSoftware/Brave-Browser",
}
CHROMIUM_APPS = {
    "chrome": ("chrome", "Chromium"),
    "chromium": ("chromium",),
    "edge": ("microsoft-edge", "Microsoft Edge", "edge", "chromium"),
    "brave": ("brave", "Brave"),
}
COOKIE_HOSTS = (".chatgpt.com", "chatgpt.com")
SESSION_COOKIE = "session-token"
KEYRING_HELPER = Path(__file__).with_name("keyring_key.py")
SYSTEM_PYTHON = "/usr/bin/python3"
WINDOWS_EPOCH_OFFSET = 11644473600


class AccountError(Exception):
    pass


def _schema_directories():
    values = []
    configured = os.environ.get("CHATGPT_USAGE_SCHEMA_DIR")
    if configured:
        values.append(Path(configured).expanduser())
    values.append(Path.home() / ".local/share/gnome-shell/extensions/chatgpt-usage@wlopez.local/schemas")
    values.append(Path(__file__).resolve().parent / "gnome-extension" / "schemas")
    return values


def _gsettings_value():
    base = ["get", SCHEMA_ID, SETTINGS_KEY]
    commands = []
    for directory in _schema_directories():
        if (directory / "gschemas.compiled").is_file():
            commands.append(["gsettings", "--schemadir", str(directory), *base])
    commands.append(["gsettings", *base])
    for command in commands:
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode != 0:
            continue
        try:
            return ast.literal_eval(completed.stdout.strip())
        except (ValueError, SyntaxError):
            continue
    return None


def load_tracked_accounts():
    """Read the selection; raise instead of silently falling back on failure."""
    raw = _gsettings_value()
    if raw is None:
        raise AccountError("No se pudo leer la selección de cuentas desde GSettings.")
    if not isinstance(raw, str):
        raise AccountError("GSettings devolvió un tipo inválido para la selección de cuentas.")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        raise AccountError("La selección de cuentas guardada no es JSON válido.") from None
    accounts = data.get("accounts") if isinstance(data, dict) else data
    if not isinstance(accounts, list):
        raise AccountError("La selección de cuentas guardada tiene un formato inválido.")
    result = []
    for entry in accounts:
        if not isinstance(entry, dict) or not isinstance(entry.get("email"), str):
            raise AccountError("La selección de cuentas contiene una entrada inválida.")
        sources = []
        for source in entry.get("sources") or []:
            if isinstance(source, dict) and source.get("browser") in BROWSER_FAMILIES \
                    and isinstance(source.get("profile"), str):
                sources.append({
                    "browser": source["browser"],
                    "profile": source["profile"],
                })
        result.append({
            "email": entry["email"],
            "enabled": bool(entry.get("enabled", True)),
            "sources": sources,
        })
    return result


def browser_family(desktop_id):
    name = str(desktop_id or "").casefold()
    if name in BROWSER_FAMILIES:
        return name
    if "firefox" in name:
        return "firefox"
    if "google-chrome" in name:
        return "chrome"
    if "chromium" in name:
        return "chromium"
    if "microsoft-edge" in name:
        return "edge"
    if "brave" in name:
        return "brave"
    return None


def firefox_profiles(roots=None):
    seen = set()
    profiles = []
    for root in roots if roots is not None else FIREFOX_ROOTS:
        root = Path(root)
        if not root.is_dir():
            continue
        for database in sorted(root.glob("*/cookies.sqlite")):
            profile_dir = database.parent.resolve()
            if profile_dir in seen:
                continue
            seen.add(profile_dir)
            profiles.append({
                "browser": "firefox",
                "kind": "firefox",
                "name": profile_dir.name,
                "path": profile_dir,
                "database": database,
            })
    return profiles


def chromium_profiles(family):
    config = CHROMIUM_CONFIGS.get(family)
    if config is None or not config.is_dir():
        return []
    profiles = []
    for child in sorted(config.iterdir()):
        if not child.is_dir():
            continue
        database = child / "Network" / "Cookies"
        if not database.is_file():
            database = child / "Cookies"
        if database.is_file():
            profiles.append({
                "browser": family,
                "kind": "chromium",
                "name": child.name,
                "path": child,
                "database": database,
                "apps": CHROMIUM_APPS[family],
            })
    return profiles


def installed_families():
    families = []
    if firefox_profiles():
        families.append("firefox")
    for family in CHROMIUM_CONFIGS:
        if chromium_profiles(family):
            families.append(family)
    return families


def profiles_for(family):
    if family == "firefox":
        profiles = firefox_profiles()
        if not profiles:
            raise AccountError("No se encontraron perfiles de Firefox.")
        return profiles
    profiles = chromium_profiles(family)
    if not profiles:
        raise AccountError(f"No se encontraron perfiles de {family}.")
    return profiles


def gather_profiles(families):
    profiles = []
    problems = []
    for family in families:
        try:
            profiles.extend(profiles_for(family))
        except AccountError as error:
            problems.append({"browser": family, "error": str(error)})
    return profiles, problems


def find_profile(source, cache):
    browser = source["browser"]
    if browser not in cache:
        cache[browser] = profiles_for(browser)
    return next(
        (profile for profile in cache[browser] if profile["name"] == source["profile"]),
        None,
    )


def _query_firefox(database, now):
    query = (
        "SELECT host,name,value,path,isSecure,expiry FROM moz_cookies "
        "WHERE host IN (?,?) AND (expiry = 0 OR expiry > ?)"
    )
    for options in ("?mode=ro", "?mode=ro&immutable=1"):
        try:
            with sqlite3.connect(Path(database).as_uri() + options, uri=True, timeout=2) as db:
                return db.execute(query, (*COOKIE_HOSTS, int(now))).fetchall()
        except sqlite3.Error:
            continue
    raise AccountError(f"No se pudo leer la base de cookies de {Path(database).parent.name}.")


def read_firefox_cookies(profile, now=None):
    now = time.time() if now is None else now
    rows = _query_firefox(profile["database"], now)
    cookies = [{
        "name": name,
        "value": value,
        "domain": host,
        "path": path,
        "secure": bool(secure),
        "expires": expiry,
    } for host, name, value, path, secure, expiry in rows]
    if not cookies:
        raise AccountError("El perfil no tiene cookies vigentes de ChatGPT.")
    return cookies


@lru_cache(maxsize=None)
def _chromium_key(apps):
    if not KEYRING_HELPER.is_file() or not Path(SYSTEM_PYTHON).is_file():
        raise AccountError("No está disponible el lector del llavero del sistema.")
    try:
        completed = subprocess.run(
            [SYSTEM_PYTHON, str(KEYRING_HELPER), *apps],
            capture_output=True, text=True, check=False, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise AccountError("No se pudo consultar el llavero del sistema.") from None
    encoded = completed.stdout.strip()
    if completed.returncode != 0 or not encoded:
        raise AccountError(
            "El llavero no tiene la clave de este navegador. "
            "Desbloqueá el llavero e iniciá sesión otra vez."
        )
    try:
        password = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise AccountError("El llavero devolvió una clave inválida.") from None
    return _derive_key(password)


def _derive_key(password):
    return PBKDF2HMAC(
        algorithm=hashes.SHA1(), length=16, salt=b"saltysalt", iterations=1,
    ).derive(password.encode("utf-8"))


def decrypt_chromium(encrypted, key, host):
    encrypted = bytes(encrypted or b"")
    if encrypted[:3] not in (b"v10", b"v11"):
        raise AccountError("Formato de cookie de Chromium no reconocido.")
    payload = encrypted[3:]
    if not payload or len(payload) % 16:
        raise AccountError("La cookie cifrada está dañada.")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    plain = decryptor.update(payload) + decryptor.finalize()
    padding = plain[-1]
    if padding < 1 or padding > 16 or plain[-padding:] != bytes([padding]) * padding:
        raise AccountError("No se pudo descifrar la cookie; la clave no coincide.")
    plain = plain[:-padding]
    digest = hashlib.sha256(str(host).encode("utf-8")).digest()
    if plain[:32] == digest:
        plain = plain[32:]
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        raise AccountError("La cookie descifrada no contiene texto válido.") from None


def chromium_expiry(value):
    if not isinstance(value, (int, float)) or value <= 0:
        return 0
    return value / 1_000_000 - WINDOWS_EPOCH_OFFSET


def _query_chromium(database):
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "Cookies"
        shutil.copy2(database, copy)
        for suffix in ("-wal", "-shm"):
            companion = Path(str(database) + suffix)
            if companion.is_file():
                shutil.copy2(companion, Path(str(copy) + suffix))
        try:
            with sqlite3.connect(copy, timeout=5) as db:
                return db.execute(
                    "SELECT host_key,name,value,encrypted_value,path,expires_utc,is_secure "
                    "FROM cookies"
                ).fetchall()
        except sqlite3.Error:
            raise AccountError(
                f"No se pudo leer la base de cookies de {Path(database).parent.name}."
            ) from None


def read_chromium_cookies(profile, now=None, key=None):
    now = time.time() if now is None else now
    key = key or _chromium_key(tuple(profile["apps"]))
    cookies = []
    for host, name, value, encrypted, path, expires_utc, secure in _query_chromium(profile["database"]):
        normalized = str(host or "").lstrip(".").casefold()
        if normalized != "chatgpt.com" and not normalized.endswith(".chatgpt.com"):
            continue
        expires = chromium_expiry(expires_utc)
        if expires and expires <= now:
            continue
        try:
            if value:
                plain = value
            elif encrypted:
                plain = decrypt_chromium(encrypted, key, host)
            else:
                continue
        except AccountError:
            if SESSION_COOKIE in str(name):
                raise
            continue
        cookies.append({
            "name": name,
            "value": plain,
            "domain": host,
            "path": path or "/",
            "secure": bool(secure),
            "expires": expires,
        })
    if not cookies:
        raise AccountError("El perfil no tiene cookies vigentes de ChatGPT.")
    return cookies


def read_cookies(profile, now=None):
    if profile["kind"] == "firefox":
        return read_firefox_cookies(profile, now)
    return read_chromium_cookies(profile, now)
