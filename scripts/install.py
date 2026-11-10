#!/usr/bin/env python3
"""Install/update or uninstall the monitor for the current Ubuntu desktop user."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import install_local

ROOT = Path(__file__).resolve().parents[1]
UUID = install_local.UUID
MONITOR = "chatgpt-usage-monitor.service"
WEB = "chatgpt-usage-web.service"


def locations():
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    state = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state")) / "chatgpt-usage"
    return {
        "extension": data / "gnome-shell/extensions" / UUID,
        "units": config / "systemd/user",
        "state": state,
        "manifest": config / "chatgpt-usage/installation.json",
    }


def quote(value):
    """Quote one systemd argument, preserving literal specifiers and spaces."""
    value = str(value)
    if any(character in value for character in ("\n", "\r", "\x00")):
        raise ValueError("Las rutas no pueden contener saltos de línea ni NUL")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def unit_text(module, state, schema):
    # Explicit paths make installation independent of the clone directory name.
    python = ROOT / ".venv/bin/python"
    return "\n".join([
        "[Unit]", "Description=ChatGPT usage monitor" if module == "monitor" else "Description=ChatGPT usage web panel",
        "After=network-online.target", "", "[Service]", "Type=simple",
        "WorkingDirectory=" + str(ROOT).replace("%", "%%"),
        "Environment=" + quote("PYTHONPATH=" + str(ROOT / "src")),
        "Environment=" + quote("CHATGPT_USAGE_STATE_DIR=" + str(state)),
        "Environment=" + quote("CHATGPT_USAGE_SCHEMA_DIR=" + str(schema)),
        "Environment=CHATGPT_USAGE_WEB_HOST=127.0.0.1",
        "Environment=CHATGPT_USAGE_WEB_PORT=8088",
        "ExecStart=" + quote(python) + " -m chatgpt_usage." + module,
        "Restart=on-failure", "RestartSec=15", "UMask=0077", "NoNewPrivileges=yes",
        "", "[Install]", "WantedBy=default.target", "",
    ])


def systemctl(*args):
    subprocess.run(["systemctl", "--user", *args], check=True)


def install_files(paths, with_web=False):
    state = paths["state"]
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    polling = state / "polling.json"
    legacy_polling = ROOT / "polling.json"
    if not polling.exists() and legacy_polling.is_file():
        try:
            enabled = json.loads(legacy_polling.read_text()).get("enabled")
            if isinstance(enabled, bool):
                polling.write_text(json.dumps({"enabled": enabled}) + "\n")
                polling.chmod(0o600)
        except (ValueError, AttributeError):
            pass
    install_local.install(
        paths["extension"], state / "uso.json",
        ROOT / "src/chatgpt_usage/scan_accounts.py", ROOT / ".venv/bin/python",
        ROOT / "src/chatgpt_usage/redeem_reset.py",
    )
    paths["units"].mkdir(parents=True, exist_ok=True)
    services = [(MONITOR, "monitor")]
    if with_web:
        services.append((WEB, "web"))
    for filename, module in services:
        (paths["units"] / filename).write_text(unit_text(module, state, paths["extension"] / "schemas"))
    manifest = paths["manifest"]
    manifest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest.write_text(json.dumps({
        "source": str(ROOT), "with_web": with_web, "state": str(state),
        "uuid": UUID, "services": [name for name, _ in services],
    }, indent=2) + "\n")


def uninstall(paths, no_start=False):
    if not paths["manifest"].is_file():
        raise ValueError("No hay una instalación registrada; no se borrará ningún archivo")
    manifest = json.loads(paths["manifest"].read_text())
    if not no_start:
        for service in manifest["services"]:
            if service not in (MONITOR, WEB):
                raise ValueError("Servicio desconocido en el registro de instalación")
            systemctl("disable", "--now", service)
        subprocess.run(["gnome-extensions", "disable", UUID], check=False)
    for service in manifest["services"]:
        if service not in (MONITOR, WEB):
            raise ValueError("Servicio desconocido en el registro de instalación")
        (paths["units"] / service).unlink(missing_ok=True)
    extension = paths["extension"]
    # Remove only the known files installed by this program. Preserve unknown files.
    for filename in ("extension.js", "prefs.js", "stylesheet.css", "metadata.json",
                     "schemas/gschemas.compiled",
                     "schemas/org.gnome.shell.extensions.chatgpt-usage.gschema.xml"):
        (extension / filename).unlink(missing_ok=True)
    for directory in (extension / "schemas", extension):
        try:
            directory.rmdir()
        except OSError:
            pass
    paths["manifest"].unlink()
    if not no_start:
        systemctl("daemon-reload")
    print("Desinstalado. Se conservaron cuentas, preferencias, estado y el código descargado.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-web", action="store_true", help="instalar también el panel web local")
    parser.add_argument("--uninstall", action="store_true", help="desinstalar sin borrar cuentas ni estado")
    parser.add_argument("--no-start", action="store_true", help="instalar archivos sin activar servicios ni extensión")
    args = parser.parse_args(argv)
    if os.geteuid() == 0:
        parser.error("Ejecutá el instalador como tu usuario, sin sudo")
    paths = locations()
    if args.uninstall:
        uninstall(paths, args.no_start)
        return 0
    for command in ("glib-compile-schemas", "gnome-shell", "gnome-extensions", "gsettings", "systemctl"):
        if shutil.which(command) is None:
            parser.error(f"Falta {command}. Usá Ubuntu Desktop con GNOME y revisá los requisitos del README")
    version = subprocess.check_output(["gnome-shell", "--version"], text=True).strip().split()[-1].split(".")[0]
    supported = json.loads((ROOT / "gnome-extension/metadata.template.json").read_text())["shell-version"]
    if version not in supported:
        parser.error(f"GNOME {version} aún no está validado. Versiones admitidas: {', '.join(supported)}")
    previous = json.loads(paths["manifest"].read_text()) if paths["manifest"].exists() else {}
    with_web = args.with_web or previous.get("with_web", False) or (paths["units"] / WEB).is_file()
    python = ROOT / ".venv/bin/python"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True)
    install_files(paths, with_web)
    if not args.no_start:
        systemctl("daemon-reload")
        services = [MONITOR] + ([WEB] if with_web else [])
        systemctl("enable", *services)
        systemctl("restart", *services)
        result = subprocess.run(["gnome-extensions", "enable", UUID], check=False)
        if result.returncode:
            print("GNOME cargará la extensión al volver a iniciar sesión.")
    print("Instalado. Cerrá sesión y volvé a entrar; después abrí Preferencias → Actualizar detección.")
    print("Las cuentas nuevas quedan desactivadas hasta que las elijas. No muevas la carpeta del proyecto.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"No se pudo completar la instalación: {error}") from None
