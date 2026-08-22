#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
source .venv/bin/activate

if [ -S /run/stas-jarvis-ydotool.sock ]; then
  export YDOTOOL_SOCKET=/run/stas-jarvis-ydotool.sock
fi

exec python -m jarvis.main
