#!/usr/bin/env bash
# Convenience launcher for Remotia.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

# Recreate the virtualenv if it is missing or was copied from another path
# (Python virtualenvs are not relocatable: they embed absolute paths).
if [ ! -x .venv/bin/python ] || ! ./.venv/bin/python -c 'import sys' >/dev/null 2>&1; then
    echo "Creating virtual environment…"
    rm -rf .venv
    "$PYTHON" -m venv .venv
    ./.venv/bin/python -m pip install --upgrade pip
    ./.venv/bin/pip install -r requirements.txt
fi

# Build the embedded RDP helper (needs FreeRDP: brew install freerdp)
if [ ! -x native/rdp_helper ] && command -v pkg-config >/dev/null && pkg-config --exists freerdp-client3; then
    echo "Building RDP helper…"
    scripts/build_rdp_helper.sh
fi

exec ./.venv/bin/python -m remotia "$@"
