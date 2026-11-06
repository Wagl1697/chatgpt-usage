#!/usr/bin/env python3
"""Consume one ChatGPT usage reset after an explicit GNOME confirmation."""

import json
from pathlib import Path
import sys
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatgpt_usage import browser_accounts, monitor


def redeem(email, credit_id, confirmed=False):
    if confirmed is not True:
        raise monitor.MonitorError("Se requiere confirmación explícita para canjear un restablecimiento")
    if not isinstance(email, str) or not email.strip():
        raise monitor.MonitorError("No se indicó una cuenta de ChatGPT válida")
    if not isinstance(credit_id, str) or not credit_id or len(credit_id) > 512:
        raise monitor.MonitorError("El restablecimiento seleccionado no es válido")

    selected = next((
        entry for entry in browser_accounts.load_tracked_accounts()
        if entry.get("enabled", True)
        and entry.get("email", "").casefold() == email.casefold()
    ), None)
    if selected is None:
        raise monitor.MonitorError("La cuenta ya no está activa en Preferencias")

    client = monitor.UsageClient()
    cache = {}
    errors = []
    try:
        for source in selected.get("sources") or []:
            try:
                profile = browser_accounts.find_profile(source, cache)
                if profile is None:
                    continue
                cookies = browser_accounts.read_cookies(profile)
                account = client.read_account(cookies)
            except (browser_accounts.AccountError, monitor.MonitorError) as error:
                errors.append(str(error))
                continue

            actual_email = account.get("email")
            if not isinstance(actual_email, str) or actual_email.casefold() != email.casefold():
                errors.append("El perfil ya no corresponde a la cuenta seleccionada")
                continue

            credits, status = client.read_reset_credits(force=True)
            if status:
                raise monitor.MonitorError(
                    "No se pudo verificar la lista actual de restablecimientos"
                )
            credit = next((item for item in credits if item["id"] == credit_id), None)
            if credit is None:
                raise monitor.MonitorError(
                    "Ese restablecimiento ya no está disponible; actualizá la lista"
                )
            if not credit["is_supported_by_plan"]:
                raise monitor.MonitorError(
                    "Este restablecimiento no es compatible con el plan actual"
                )

            result = client.consume_reset_credit(
                credit_id, str(uuid.uuid4())
            )
            code = result.get("code")
            if code == "reset":
                return {"ok": True, "code": code, "message": "Restablecimiento aplicado."}
            if code == "already_redeemed":
                return {
                    "ok": True,
                    "code": code,
                    "message": "Ese restablecimiento ya se había utilizado.",
                }
            if code in ("no_credit", "nothing_to_reset"):
                raise monitor.MonitorError(
                    "OpenAI indicó que ya no hay un restablecimiento utilizable"
                )
            raise monitor.MonitorError(
                "OpenAI devolvió un resultado de restablecimiento no reconocido"
            )

        raise monitor.MonitorError(
            "No se pudo verificar la cuenta seleccionada" +
            (f": {errors[-1]}" if errors else "")
        )
    finally:
        client.close()


def main():
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise monitor.MonitorError("La solicitud de restablecimiento no es válida")
        result = redeem(
            request.get("email"), request.get("credit_id"),
            request.get("confirmed") is True,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (browser_accounts.AccountError, monitor.MonitorError, ValueError) as error:
        print(json.dumps({"ok": False, "message": str(error)}, ensure_ascii=False))
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "message": "Operación cancelada."}))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
