#!/usr/bin/env bash
# Menjalankan pytest dengan lingkungan yang benar (venv, Node, Chromium Remotion).
#   bash deploy/run-tests.sh                      # seluruh suite
#   bash deploy/run-tests.sh tests/test_revisi.py # sebagian
set -uo pipefail
AKAR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${KLIPA_VENV:-/opt/klipa-venv}"
export PATH="${KLIPA_NODE_DIR:-/opt/node}/bin:$PATH"

# Dibaca sebagai teks, TIDAK di-source: .env tidak boleh dieksekusi sebagai skrip.
CH="$(grep -m1 '^REMOTION_CHROMIUM=' "$AKAR/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r')"
[ -n "$CH" ] && export REMOTION_CHROMIUM="$CH"

cd "$AKAR" || exit 1
[ $# -eq 0 ] && set -- tests/
exec "$VENV/bin/python" -m pytest "$@" -q -p no:cacheprovider
