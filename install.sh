#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export PYTHONDONTWRITEBYTECODE=1
case "${1:-}" in
  server|starport) shift; exec python3 server/install.py "$@" ;;
  client|starship) shift; exec python3 client/install.py "$@" ;;
  --*) exec python3 install.py "$@" ;;
  "")
    printf 'Install role [server / client]: '
    read -r role
    case "$role" in
      server|starport) exec python3 server/install.py ;;
      client|starship) exec python3 client/install.py ;;
      *) printf 'Choose server or client.\n' >&2; exit 2 ;;
    esac ;;
  *) printf 'Usage: ./install.sh server|client [--check|--plan|--yes]\n' >&2; exit 2 ;;
esac
