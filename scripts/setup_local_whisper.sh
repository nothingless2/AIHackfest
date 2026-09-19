#!/usr/bin/env bash
# Pasang Whisper LOKAL (faster-whisper) di venv terpisah, unduh modelnya, dan
# buktikan ia berjalan. Aman diulang.
#
# Kenapa venv sendiri: faster-whisper menarik ctranslate2/onnxruntime/numpy, dan
# memasangnya ke Python sistem berisiko mengubah numpy yang dipakai moviepy/openai.
# Kenapa modelnya diunduh DI SINI: pengunduhan pertama ~460 MB, dan tidak boleh
# terjadi di tengah run pipeline yang punya batas waktu.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$RAIZ/.venv-whisper"
MODEL="${TRANSCRIBE_LOCAL_MODEL:-small}"

echo "==> 1/3 venv + faster-whisper ($VENV)"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet faster-whisper

echo "==> 2/3 unduh model '$MODEL' (sekali; ~460 MB untuk small)"
"$VENV/bin/python" - <<PY
from faster_whisper import WhisperModel
WhisperModel("$MODEL", device="cpu", compute_type="int8")
print("model siap")
PY

echo "==> 3/3 uji nyata: transkrip 3 detik audio sintetis lewat worker"
TMP="$(mktemp --suffix=.wav)"
trap 'rm -f "$TMP"' EXIT
ffmpeg -y -v error -f lavfi -i "sine=frequency=440:duration=3" "$TMP"
SALUR="$("$VENV/bin/python" "$RAIZ/scripts/local_whisper.py" --model "$MODEL" "$TMP")"
echo "$SALUR" | grep -q '"tipe": "berkas"' || { echo "GAGAL: worker tidak mengembalikan hasil:"; echo "$SALUR"; exit 1; }
echo "$SALUR" | grep -q '"tipe": "fatal"' && { echo "GAGAL: $SALUR"; exit 1; }
echo "SELESAI. Whisper lokal siap (model $MODEL). TRANSCRIBE_PROVIDER=auto akan memakainya."
