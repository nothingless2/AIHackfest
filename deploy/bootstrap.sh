#!/usr/bin/env bash
# Membangun lingkungan Klipa dari nol di Ubuntu 22.04 (laptop, WSL, atau VPS). Idempoten:
# aman dijalankan ulang, langkah yang sudah beres dilewati.
#
#   bash deploy/bootstrap.sh                  # sistem + venv + Remotion + Chromium + .env
#   bash deploy/bootstrap.sh --with-hermes    # + Hermes Agent dan skill content-factory
#
# TIDAK menyentuh rahasia: .env dibuat dari .env.example bila belum ada, dan nilainya kamu isi sendiri.
# Diuji di Ubuntu 22.04 (ffmpeg 4.4.2 bawaan apt, sama dengan mesin produksi). Ubuntu lain: belum diuji.
set -euo pipefail

AKAR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${KLIPA_VENV:-/opt/klipa-venv}"
NODE_VER="${KLIPA_NODE_VERSION:-v24.21.0}"
NODE_DIR="${KLIPA_NODE_DIR:-/opt/node}"
DENGAN_HERMES=0

for a in "$@"; do
  case "$a" in
    --with-hermes) DENGAN_HERMES=1 ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "argumen tidak dikenal: $a" >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;32m[klipa]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[klipa] PERHATIAN:\033[0m %s\n' "$*" >&2; }
gagal(){ printf '\033[1;31m[klipa] GAGAL:\033[0m %s\n' "$*" >&2; exit 1; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || gagal "butuh root atau sudo"
  SUDO="sudo"
fi

[ -f "$AKAR/requirements.txt" ] || gagal "requirements.txt tidak ada di $AKAR (skrip harus di dalam repo)"
case "$(uname -m)" in
  x86_64|amd64) ARCH_NODE=x64 ;;
  *) gagal "arsitektur $(uname -m) belum didukung skrip ini (hanya x86_64)" ;;
esac

CH=""

# ------------------------------------------------------------------ 1. paket sistem
pasang_apt() {
  export DEBIAN_FRONTEND=noninteractive
  log "paket sistem (apt)"
  $SUDO apt-get update -qq
  local asound=libasound2
  apt-cache show libasound2t64 >/dev/null 2>&1 && asound=libasound2t64   # Ubuntu 24.04
  $SUDO apt-get install -y -qq \
    ca-certificates curl git xz-utils unzip \
    ffmpeg python3 python3-venv python3-pip \
    fontconfig fonts-dejavu-core fonts-noto-color-emoji \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 libgbm1 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libcups2 libdrm2 \
    libpango-1.0-0 libcairo2 "$asound" >/dev/null
  # ffmpeg harus punya drawtext (build statis johnvansickle TIDAK punya -> 43 tes gagal, terukur 3 Okt).
  # Disimpan ke variabel dulu: "ffmpeg | awk | grep -q" di bawah pipefail memberi status 141 (SIGPIPE)
  # begitu grep keluar lebih awal, sehingga filter yang ADA dilaporkan hilang (terbukti di Ubuntu 22.04).
  local filter
  filter="$(ffmpeg -hide_banner -filters 2>/dev/null | awk '{print $2}')"
  grep -qx drawtext <<<"$filter" \
    || gagal "ffmpeg ini tidak punya filter drawtext; pakai ffmpeg dari apt (bukan build statis)"
  log "ffmpeg: $(ffmpeg -version | head -1 | cut -d' ' -f3)"
}

# ------------------------------------------------------------------ 2. Node.js (versi dikunci, checksum diverifikasi)
pasang_node() {
  if [ -x "$NODE_DIR/bin/node" ] && [ "$("$NODE_DIR/bin/node" --version)" = "$NODE_VER" ]; then
    log "node $NODE_VER sudah terpasang"
  else
    log "node $NODE_VER"
    local tmp berkas url sums
    tmp="$(mktemp -d)"; berkas="node-$NODE_VER-linux-$ARCH_NODE.tar.xz"
    url="https://nodejs.org/dist/$NODE_VER"
    curl -fL --retry 5 --retry-delay 3 -C - -o "$tmp/$berkas" "$url/$berkas"
    curl -fsSL "$url/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt"
    sums="$(grep " $berkas\$" "$tmp/SHASUMS256.txt")" || gagal "checksum $berkas tidak ada di SHASUMS256.txt"
    (cd "$tmp" && echo "$sums" | sha256sum -c - >/dev/null) || gagal "checksum node TIDAK cocok; unduhan ditolak"
    $SUDO rm -rf "$NODE_DIR"; $SUDO mkdir -p "$NODE_DIR"
    $SUDO tar -xJf "$tmp/$berkas" -C "$NODE_DIR" --strip-components=1
    rm -rf "$tmp"
  fi
  for b in node npm npx; do $SUDO ln -sf "$NODE_DIR/bin/$b" "/usr/local/bin/$b"; done
}

# ------------------------------------------------------------------ 3. venv Python
pasang_venv() {
  log "venv Python di $VENV"
  $SUDO mkdir -p "$(dirname "$VENV")"
  [ -x "$VENV/bin/python" ] || $SUDO python3 -m venv "$VENV"
  $SUDO "$VENV/bin/pip" install -q --upgrade pip
  # requirements.txt hanya memberi batas bawah (openai>=1.0 dst.), jadi dua build pada hari berbeda bisa
  # mendapat versi berbeda (terbukti 3 Okt: openai 2.48 di Python 3.9, 3.24 di 3.10). Bila requirements.lock
  # ada (hasil pip freeze dari lingkungan yang lolos tes), pasang PERSIS versi itu.
  if [ -f "$AKAR/requirements.lock" ]; then
    log "memakai requirements.lock (versi dikunci)"
    $SUDO "$VENV/bin/pip" install -q -r "$AKAR/requirements.lock"
  else
    $SUDO "$VENV/bin/pip" install -q -r "$AKAR/requirements.txt"
  fi
}

# ------------------------------------------------------------------ 4. Remotion + Chromium
pasang_remotion() {
  local R="$AKAR/remotion" F="$AKAR/remotion/public/fonts"
  # remotion/public/fonts adalah symlink di git. Di checkout Windows (core.symlinks=false) ia jadi berkas
  # teks 18 byte dan Remotion membalas 404. Di filesystem Linux asli kita pulihkan; di /mnt/* tidak disentuh.
  if [ -f "$F" ] && [ ! -L "$F" ]; then
    case "$AKAR" in
      /mnt/*) warn "remotion/public/fonts bukan symlink dan repo ada di $AKAR (drive Windows). Render Remotion akan 404. Clone repo ke filesystem Linux (mis. /opt/klipa) lalu jalankan ulang." ;;
      *) if [ "$(cat "$F")" = "../../assets/fonts" ]; then rm "$F" && ln -s ../../assets/fonts "$F"; log "symlink fonts dipulihkan"; fi ;;
    esac
  fi
  log "Remotion (npm ci)"
  (cd "$R" && PATH="$NODE_DIR/bin:$PATH" npm ci --no-audit --no-fund --loglevel=error)
  CH="$(find "$R/node_modules/.remotion" -type f -name chrome-headless-shell -print -quit 2>/dev/null || true)"
  if [ -z "$CH" ]; then
    log "mengunduh Chromium (chrome-headless-shell, +-90 MB)"
    (cd "$R" && PATH="$NODE_DIR/bin:$PATH" npx remotion browser ensure >/dev/null)
    CH="$(find "$R/node_modules/.remotion" -type f -name chrome-headless-shell -print -quit)"
  fi
  [ -n "$CH" ] || gagal "binari Chromium tidak ditemukan setelah diunduh"
  local kurang
  # "|| true": grep keluar 1 saat TIDAK ADA yang kurang (kabar baik); tanpa ini set -e + pipefail mematikan skrip.
  kurang="$(ldd "$CH" 2>/dev/null | grep 'not found' | awk '{print $1}' | sort -u | tr '\n' ' ' || true)"
  [ -z "$kurang" ] || gagal "library sistem kurang untuk Chromium: $kurang"
  log "chromium: $CH"
}

# ------------------------------------------------------------------ 5. .env (tanpa menimpa, tanpa mencetak nilai)
atur_env() {  # atur_env KUNCI NILAI -> ganti baris KUNCI= bila ada, selain itu tambahkan
  local k="$1" v="$2" f="$AKAR/.env"
  if grep -q "^${k}=" "$f"; then sed -i "s|^${k}=.*|${k}=${v}|" "$f"; else printf '%s=%s\n' "$k" "$v" >> "$f"; fi
}
siapkan_env() {
  if [ ! -f "$AKAR/.env" ]; then
    # sed membuang CR: .env.example hasil checkout Windows berakhir baris CRLF, dan "\r" ikut jadi isi nilai.
    umask 077; sed 's/\r$//' "$AKAR/.env.example" > "$AKAR/.env"; chmod 600 "$AKAR/.env"
    log ".env dibuat dari .env.example -> ISI kuncinya sendiri (OPENAI_API_KEY, dst.); file ini tidak masuk git"
  else
    log ".env sudah ada, tidak ditimpa"
  fi
  atur_env REMOTION_CHROMIUM "$CH"
}

# ------------------------------------------------------------------ 6. (opsional) Hermes + skill
pasang_hermes() {
  log "Hermes Agent"
  local h="${HERMES_HOME:-$HOME/.hermes}" tmp
  if [ ! -d "$h/hermes-agent/.git" ]; then
    tmp="$(mktemp)"
    curl -fsSL "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh" -o "$tmp"
    log "install.sh sha256: $(sha256sum "$tmp" | cut -d' ' -f1)"
    bash "$tmp" --non-interactive
    rm -f "$tmp"
  else
    log "Hermes sudah terpasang di $h"
  fi
  mkdir -p "$h/skills/content-factory"
  cp "$AKAR/hermes-skill/content-factory/SKILL.md" "$h/skills/content-factory/SKILL.md"
  # SKILL.md memanggil python3 /root/AIHackfest/scripts/... (path produksi). Tautkan ke lokasi sebenarnya.
  if [ "$AKAR" != "/root/AIHackfest" ]; then
    if [ "$(id -u)" -eq 0 ]; then
      ln -sfn "$AKAR" /root/AIHackfest; log "tautan /root/AIHackfest -> $AKAR"
    else
      warn "bukan root: path /root/AIHackfest di SKILL.md tidak akan berlaku. Jalankan sebagai root atau sesuaikan SKILL.md."
    fi
  fi
}

pasang_apt
pasang_node
pasang_venv
pasang_remotion
siapkan_env
if [ "$DENGAN_HERMES" = 1 ]; then pasang_hermes; fi
log "selesai. Verifikasi: bash deploy/verify.sh   (tambah --tes untuk uji render nyata)"
