#!/usr/bin/env bash
# Memulihkan arsip dari backup.sh ke repo ini. Gagal-tertutup:
#  - arsip dengan path absolut, '..', atau di luar workspace/ DITOLAK;
#  - data yang sudah ada TIDAK ditimpa tanpa --force.
#
#   bash deploy/restore.sh arsip.tar.gz [--force]
set -euo pipefail
AKAR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARSIP="${1:-}"; FORCE="${2:-}"
gagal(){ echo "GAGAL: $*" >&2; exit 1; }

[ -n "$ARSIP" ] && [ -f "$ARSIP" ] || gagal "pakai: restore.sh arsip.tar.gz [--force]"

# Setiap entri harus relatif dan berada di bawah workspace/ (kebal terhadap arsip jahat/salah).
while IFS= read -r entri; do
  case "$entri" in
    /*|*..*) gagal "arsip memuat path berbahaya: $entri" ;;
    workspace/*) ;;
    *) gagal "arsip memuat berkas di luar workspace/: $entri" ;;
  esac
done < <(tar -tzf "$ARSIP")

cd "$AKAR"
if [ "$FORCE" != "--force" ] && [ -d workspace/state/profil ] && [ -n "$(ls -A workspace/state/profil 2>/dev/null)" ]; then
  gagal "workspace/state/profil sudah berisi data. Tambahkan --force bila memang mau menimpanya."
fi

tar -xzf "$ARSIP" -C "$AKAR"
echo "dipulihkan: $(tar -tzf "$ARSIP" | grep -vc '/$') berkas ke $AKAR/workspace"
