#!/usr/bin/env bash
#
# Install plugin content-factory ke OpenClaw secara berulang dan aman.
#
# LATAR BELAKANG (kenapa skrip ini ada):
# `openclaw plugins install <dir>` menyalin SELURUH isi folder plugin ke staging
# internalnya, termasuk node_modules. Dengan devDependency terpasang (openclaw,
# typescript, vitest) folder itu ~607 MB, dan proses install node-nya kena
# OOM-killed (exit 137) di mesin 8 GB yang juga menjalankan gateway. Akibatnya
# lebih buruk daripada sekadar gagal: install yang mati di tengah meninggalkan
# ~/.openclaw/extensions/content-factory TANPA openclaw.plugin.json, sehingga
# gateway diam-diam menjalankan build plugin yang LAMA.
#
# Solusinya bukan menghapus devDependency dari folder kerja (itu merusak build
# dan test berikutnya), melainkan memisahkan folder kerja dari folder yang
# diinstal:
#   folder kerja  -> devDependency LENGKAP, dipakai build + test. Tidak disentuh.
#   folder staging-> dependency RUNTIME saja (~6,5 MB), itulah yang diinstal.
#
set -euo pipefail

PLUGIN_DIR="${PLUGIN_DIR:-/root/AIHackfest/openclaw-plugin}"
STAGING_DIR="${STAGING_DIR:-/tmp/content-factory-plugin-staging}"

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "1/6 Install dependency lengkap (termasuk devDependency) di folder kerja"
cd "$PLUGIN_DIR"
npm install
echo "folder kerja: $(du -sh node_modules | cut -f1) (devDependency sengaja dipertahankan)"

step "2/6 Build TypeScript -> dist/"
npm run build
if ! grep -q "CONTENT_FACTORY_RUN_ID" dist/index.js; then
  echo "GAGAL: dist/index.js tidak memuat CONTENT_FACTORY_RUN_ID — build tidak sesuai sumber." >&2
  exit 1
fi
echo "dist/index.js terverifikasi memuat CONTENT_FACTORY_RUN_ID"

step "3/6 Jalankan test plugin"
npm test

step "4/6 Siapkan folder staging tanpa devDependency"
rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"
# Hanya yang dibutuhkan runtime. node_modules kerja SENGAJA tidak ikut disalin.
cp -r dist "$STAGING_DIR"/
cp package.json package-lock.json openclaw.plugin.json "$STAGING_DIR"/
[ -f README.md ] && cp README.md "$STAGING_DIR"/
cd "$STAGING_DIR"
# npm ci di staging membuat node_modules runtime-only yang baru; folder kerja utuh.
npm ci --omit=dev
echo "staging: $(du -sh "$STAGING_DIR" | cut -f1)"

step "5/6 Install ke OpenClaw dari staging"
openclaw plugins install "$STAGING_DIR" --force --accept-capabilities

INSTALLED="/root/.openclaw/extensions/content-factory"
if [ ! -f "$INSTALLED/openclaw.plugin.json" ]; then
  echo "GAGAL: $INSTALLED/openclaw.plugin.json tidak ada — install kemungkinan mati di tengah (cek OOM)." >&2
  exit 1
fi
if ! grep -q "CONTENT_FACTORY_RUN_ID" "$INSTALLED/dist/index.js"; then
  echo "GAGAL: plugin terpasang bukan build terbaru." >&2
  exit 1
fi
echo "plugin terpasang terverifikasi (manifest ada + build terbaru)"

step "6/6 Restart gateway"
openclaw gateway restart
sleep 15
systemctl --user is-active openclaw-gateway.service

printf '\n\033[1mSELESAI.\033[0m Folder kerja masih lengkap dengan devDependency:\n'
du -sh "$PLUGIN_DIR/node_modules"
