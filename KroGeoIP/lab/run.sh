#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec unshare -Urn bash -c '
    set -e
    ip link set lo up
    ip -brief address
    ip route
    .venv/bin/python -m unittest discover -s tests -v
    .venv/bin/python -m unittest lab.test_integration -v
    .venv/bin/python region_lab.py samples/session_specific.json
    .venv/bin/python region_lab.py samples/cross_session_display.json
    .venv/bin/python -m compileall -q main.py region_lab.py tests lab
'
