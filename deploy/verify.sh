#!/usr/bin/env bash
# Memeriksa bahwa lingkungan Klipa benar-benar siap. Setiap pemeriksaan "ada" disertai KONTROL POSITIF
# (nama palsu harus dinyatakan tidak ada), supaya hasil OK bukan karena alat ukurnya buta.
#
#   bash deploy/verify.sh          # pemeriksaan cepat (tanpa render)
#   bash deploy/verify.sh --tes    # + tes render nyata (Remotion + ffmpeg), ±2 menit
set -uo pipefail

AKAR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${KLIPA_VENV:-/opt/klipa-venv}"
NODE_DIR="${KLIPA_NODE_DIR:-/opt/node}"
export PATH="$NODE_DIR/bin:$PATH"
DENGAN_TES=0; [ "${1:-}" = "--tes" ] && DENGAN_TES=1

GAGAL=0
ok()  { printf '  \033[32mOK\033[0m     %s\n' "$*"; }
bad() { printf '  \033[31mGAGAL\033[0m  %s\n' "$*"; GAGAL=$((GAGAL + 1)); }
info(){ printf '  \033[33m..\033[0m     %s\n' "$*"; }

echo "== ffmpeg =="
if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg $(ffmpeg -version | head -1 | cut -d' ' -f3)"
  filters="$(ffmpeg -hide_banner -filters 2>/dev/null | awk '{print $2}')"
  if grep -qx zz_filter_palsu <<<"$filters"; then
    bad "kontrol positif gagal: filter palsu terdeteksi 'ada' -> pemeriksaan tidak bisa dipercaya"
  else
    ok "kontrol: filter palsu dinyatakan tidak ada (alat ukur bekerja)"
    for f in drawtext subtitles overlay sidechaincompress loudnorm afftdn volumedetect silencedetect; do
      grep -qx "$f" <<<"$filters" && ok "filter $f" || bad "filter $f TIDAK ADA"
    done
    amix_help="$(ffmpeg -hide_banner -h filter=amix 2>/dev/null)"
    grep -q normalize <<<"$amix_help" && ok "amix punya opsi normalize" || bad "amix tanpa opsi normalize (ffmpeg < 4.4)"
  fi
else
  bad "ffmpeg tidak terpasang"
fi

echo "== Python =="
if [ -x "$VENV/bin/python" ]; then
  "$VENV/bin/python" -c "import fcntl, numpy, cv2, PIL, openai, dotenv, edge_tts, moviepy" 2>/dev/null \
    && ok "venv $VENV: semua modul bisa di-import (termasuk fcntl)" || bad "ada modul yang gagal di-import di $VENV"
else
  bad "venv tidak ada di $VENV"
fi

echo "== Node & Remotion =="
if command -v node >/dev/null 2>&1; then ok "node $(node --version)"; else bad "node tidak terpasang"; fi
[ -d "$AKAR/remotion/node_modules/remotion" ] && ok "paket Remotion terpasang" || bad "remotion/node_modules belum ada (npm ci)"
F="$AKAR/remotion/public/fonts"
if [ -d "$F/" ] && [ "$(find "$F/" -maxdepth 1 -name '*.ttf' | wc -l)" -gt 0 ]; then
  ok "remotion/public/fonts berisi font ($(find "$F/" -maxdepth 1 -name '*.ttf' | wc -l) .ttf)"
else
  bad "remotion/public/fonts bukan folder berisi font (symlink git rusak?) -> render Remotion akan 404"
fi

echo "== Chromium =="
CH="$(grep -m1 '^REMOTION_CHROMIUM=' "$AKAR/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r')"
if [ -n "$CH" ] && [ -x "$CH" ]; then
  kurang="$(ldd "$CH" 2>/dev/null | grep 'not found' | awk '{print $1}' | sort -u | tr '\n' ' ')"
  [ -z "$kurang" ] && ok "chromium + semua library sistemnya lengkap" || bad "library kurang: $kurang"
else
  bad "REMOTION_CHROMIUM di .env kosong atau binarinya tidak ada"
fi

echo "== .env =="
if [ -f "$AKAR/.env" ]; then
  izin="$(stat -c %a "$AKAR/.env")"
  [ "$izin" = "600" ] && ok ".env ada, izin 600" || bad ".env izin $izin (harus 600; berisi kunci API)"
  grep -q '^OPENAI_API_KEY=[^[:space:]]' "$AKAR/.env" && ok "OPENAI_API_KEY terisi" || info "OPENAI_API_KEY masih kosong (isi sebelum dipakai)"
else
  bad ".env belum ada"
fi

if [ "$DENGAN_TES" = 1 ]; then
  echo "== tes render nyata =="
  if bash "$AKAR/deploy/run-tests.sh" tests/test_sampul.py tests/test_overlay_remotion.py tests/test_carousel.py; then
    ok "tes render lolos"
  else
    bad "tes render ada yang gagal (lihat keluaran di atas)"
  fi
fi

echo
if [ "$GAGAL" -eq 0 ]; then echo "SEMUA PEMERIKSAAN LOLOS"; else echo "$GAGAL pemeriksaan GAGAL"; exit 1; fi
