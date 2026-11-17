#!/usr/bin/env bash
# Thin wrapper; logic lives in allocate-address.py (also runnable directly on Windows).

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

SCRIPT_DIR="${BASH_SOURCE[0]%[/\\]*}"
[ "$SCRIPT_DIR" != "${BASH_SOURCE[0]}" ] || SCRIPT_DIR="."
exec "${PYTHON:-python3}" "$SCRIPT_DIR/allocate-address.py" "$@"
