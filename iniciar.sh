#!/bin/sh
cd "$(dirname "$0")" || exit 1
PYTHONPATH="$PWD/src" exec .venv/bin/python -m chatgpt_usage.monitor "$@"
