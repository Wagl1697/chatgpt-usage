#!/usr/bin/python3
"""Read a Chromium-family cookie key from GNOME Secret Service.

The key is written as base64 to stdout and never logged. Run with the system
Python because PyGObject is not available inside the prototype venv.
"""

import base64
import sys

SCHEMAS = (
    "chrome_libsecret_os_crypt_password_v2",
    "chrome_libsecret_os_crypt_password_v1",
)


def main():
    if len(sys.argv) < 2:
        return 1
    try:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
    except (ImportError, ValueError):
        return 1
    for app in sys.argv[1:]:
        for schema_name in SCHEMAS:
            schema = Secret.Schema.new(
                schema_name, Secret.SchemaFlags.NONE,
                {"application": Secret.SchemaAttributeType.STRING},
            )
            try:
                password = Secret.password_lookup_sync(schema, {"application": app}, None)
            except Exception:
                continue
            if password:
                sys.stdout.write(base64.b64encode(password.encode("utf-8")).decode("ascii"))
                return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
