#!/usr/bin/env python3
"""Scan installed browsers and print detected ChatGPT accounts as JSON."""

import argparse
import json
import sys

import browser_accounts
import monitor


def scan(families):
    profiles, problems = browser_accounts.gather_profiles(families)
    client = monitor.UsageClient()
    accounts = {}
    unavailable = []
    try:
        for profile in profiles:
            try:
                cookies = browser_accounts.read_cookies(profile)
                if not any(
                    browser_accounts.SESSION_COOKIE in str(cookie["name"])
                    for cookie in cookies
                ):
                    raise browser_accounts.AccountError(
                        "sin sesión de ChatGPT en este perfil"
                    )
                report = client.read_account(cookies)
            except (browser_accounts.AccountError, monitor.MonitorError) as error:
                unavailable.append({
                    "browser": profile["browser"],
                    "profile": profile["name"],
                    "error": str(error),
                })
                continue
            email = report.get("email")
            if not isinstance(email, str) or not email:
                unavailable.append({
                    "browser": profile["browser"],
                    "profile": profile["name"],
                    "error": "la sesión no informó un correo",
                })
                continue
            key = email.casefold()
            entry = accounts.setdefault(key, {"email": email, "sources": []})
            entry["plan_type"] = report.get("plan_type")
            entry["windows"] = monitor.windows_from_rate_limit(
                report.get("rate_limit") or {}
            )
            entry["sources"].append({
                "browser": profile["browser"],
                "profile": profile["name"],
            })
    finally:
        client.close()
    return {
        "accounts": list(accounts.values()),
        "unavailable": unavailable,
        "problems": problems,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--browsers",
        help="limitar a: firefox,chrome,chromium,edge,brave (separados por coma)",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.browsers:
            families = []
            for item in args.browsers.split(","):
                item = item.strip()
                if not item:
                    continue
                family = browser_accounts.browser_family(item)
                if family is None:
                    raise SystemExit(f"Navegador no soportado: {item}")
                if family not in families:
                    families.append(family)
        else:
            families = browser_accounts.installed_families()
        if not families:
            raise SystemExit("No se encontraron navegadores soportados.")
        result = scan(families)
        result["browsers"] = families
        print(json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        ))
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
