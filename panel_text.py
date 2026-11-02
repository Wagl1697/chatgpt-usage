#!/usr/bin/python3
"""Compact five-hour quota summary for GNOME Executor."""

import fcntl
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def short_name(account):
    identity = str(account.get("email") or account.get("label") or "GPT").split("@", 1)[0]
    return identity


def main():
    try:
        report = json.loads((ROOT / "uso.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("GPT ⚠ — | Go ⚠ —")
        return

    accounts = report.get("chatgpt_accounts") or []
    if not accounts:
        accounts = [{
            "label": "ChatGPT",
            "status": report.get("status", "Sin datos"),
            "windows": report.get("windows", {}),
        }]

    accounts = sorted(accounts, key=lambda account: str(account.get("email") or account.get("label", "")).casefold())
    state_path = ROOT / ".panel-carousel.json"
    try:
        with state_path.open("a+", encoding="utf-8") as state:
            fcntl.flock(state, fcntl.LOCK_EX)
            state.seek(0)
            previous = json.loads(state.read() or "{}")
            index = int(previous.get("index", -1))
            index = (index + 1) % len(accounts)
            state.seek(0)
            state.truncate()
            json.dump({"index": index}, state)
            state.flush()
    except (OSError, ValueError, TypeError):
        index = 0

    current = accounts[index]
    fresh = current.get("status") == "OK"
    meter = (current.get("windows") or {}).get("five_hour") or {}
    remaining = meter.get("remaining_percent")
    value = f"{remaining:g}%" if isinstance(remaining, (int, float)) else "—"
    warning = "" if fresh else " ⚠"

    go = report.get("opencode_go") or {}
    go_meter = (go.get("windows") or {}).get("five_hour") or {}
    go_remaining = go_meter.get("remaining_percent")
    go_value = f"{go_remaining:g}%" if isinstance(go_remaining, (int, float)) else "—"
    go_fresh = go.get("status", "").startswith("OK")
    go_warning = "" if go_fresh else " ⚠"

    print(f"GPT {short_name(current)}{warning} {value} | Go{go_warning} {go_value}")


if __name__ == "__main__":
    main()
