#!/usr/bin/env bash
# Mencadangkan DATA PENGGUNA (profil gaya, kamus, pustaka musik, riwayat) ke satu arsip.
# Yang TIDAK ikut, dengan sengaja: .env, token bot, ~/.hermes, kunci 9Router (rahasia, isi ulang di tempat baru),
# serta draf/pemeriksaan/pratinjau posting (sementara dan sekali pakai).
#
#   bash deploy/backup.sh [berkas-tujuan.tar.gz]
set -euo pipefail
AKAR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TUJUAN="${1:-$AKAR/../klipa-backup-$(date +%Y%m%d-%H%M%S).tar.gz}"
gagal(){ echo "GAGAL: $*" >&2; exit 1; }

cd "$AKAR"
ISI=()
for p in workspace/state workspace/published workspace/music_lib; do
  [ -e "$p" ] && ISI+=("$p")
done
[ "${#ISI[@]}" -gt 0 ] || gagal "tidak ada data (workspace/state dst.) untuk dicadangkan"

umask 077
tar -czf "$TUJUAN" \
  --exclude='workspace/state/draf_naskah' --exclude='workspace/state/inspect' --exclude='workspace/state/terbit' \
  --exclude='*.lock' --exclude='*.dipakai' --exclude='*.tmp' \
  "${ISI[@]}"

# Pagar terakhir: arsip tidak boleh memuat berkas yang tampak seperti rahasia.
if tar -tzf "$TUJUAN" | grep -iE '(^|/)(\.env|.*token.*|.*secret.*|.*\.pem|.*\.key|auth[^/]*)$' >/dev/null; then
  rm -f "$TUJUAN"
  gagal "arsip memuat berkas yang tampak seperti rahasia; dibatalkan dan dihapus"
fi

chmod 600 "$TUJUAN"
echo "cadangan: $(cd "$(dirname "$TUJUAN")" && pwd)/$(basename "$TUJUAN")"
echo "berkas  : $(tar -tzf "$TUJUAN" | grep -vc '/$')  | ukuran: $(du -h "$TUJUAN" | cut -f1)"
