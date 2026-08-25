#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$PROJECT_ROOT/scripts/simulate_skab_live_feed.py" "$@"
