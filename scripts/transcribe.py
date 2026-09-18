"""Transkripsi audio dari bahan video milik user.

Kenapa ada: sebelum ini model HANYA melihat satu frame diam per video
(`vision.py` mengambil frame di detik ke-1) dan tidak pernah mendengar apa pun.
Untuk video talking-head — orang bicara ke kamera — itu berarti hampir seluruh
isinya tidak terlihat sistem. Buktinya ada di brief yang pernah dihasilkan:
`observed_material` berbunyi "seseorang berbicara di depan kamera dengan latar
belakang dinding yang dekoratif", lalu naskahnya dikarang jadi motivasi generik.

Modul ini menutup lubang itu: audio diekstrak dengan ffmpeg (sudah jadi
dependensi render, jadi tidak ada paket baru) lalu ditranskrip lewat OpenAI.

Aturan yang dijaga:
- Video tanpa trek audio dilewati tanpa membuang kuota.
- Audio dikompres kecil (16 kHz mono) sebelum dikirim: akurasi bicara tidak
  butuh lebih, dan batas unggah API 25 MB jadi tidak pernah terancam.
- TIDAK PERNAH melempar ke pemanggil. Transkripsi adalah pengayaan, bukan
  syarat — gagal berarti brief dibuat tanpa transkrip, bukan pipeline berhenti.
"""

import os
import subprocess
import tempfile

from common import OPENAI_API_KEY

TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "whisper-1")
TRANSCRIBE_ENABLED = os.getenv("TRANSCRIBE_ENABLED", "1") not in ("0", "false", "False")
TRANSCRIBE_TIMEOUT = int(os.getenv("TRANSCRIBE_TIMEOUT_SECONDS", "60"))
TRANSCRIBE_MAX_ASSETS = int(os.getenv("TRANSCRIBE_MAX_ASSETS", "6"))
TRANSCRIBE_MAX_SECONDS = int(os.getenv("TRANSCRIBE_MAX_SECONDS", "600"))

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".opus"}


def _ffprobe(path, entries):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", entries,
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        return ""


def has_audio(path):
    """True kalau file punya trek audio. Tanpa ini kita membuang kuota untuk
    video bisu (screen recording, timelapse, video tanpa suara)."""
    return "audio" in _ffprobe(path, "stream=codec_type").split()


def media_duration(path):
    try:
        return float(_ffprobe(path, "format=duration") or 0)
    except ValueError:
        return 0.0


def extract_audio(path, out_path):
    """Ambil trek audio jadi MP3 16 kHz mono. Return True kalau berhasil."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", path, "-vn",
             "-ac", "1", "-ar", "16000", "-b:a", "48k", out_path],
            capture_output=True, timeout=TRANSCRIBE_TIMEOUT,
        )
        return proc.returncode == 0 and os.path.getsize(out_path) > 0
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[warn] transcribe: gagal mengekstrak audio dari {os.path.basename(path)}: {e}")
        return False


def transcribe_file(audio_path, *, durasi=0.0):
    """Transkrip satu file audio. Return teks, atau "" kalau gagal."""
    from openai import OpenAI

    from retry import with_retry

    from common import openai_is_retriable, openai_retry_after

    client = OpenAI(api_key=OPENAI_API_KEY, max_retries=0, timeout=TRANSCRIBE_TIMEOUT)

    def sekali():
        with open(audio_path, "rb") as f:
            return client.audio.transcriptions.create(model=TRANSCRIBE_MODEL, file=f)

    hasil = with_retry(
        sekali,
        is_retriable=openai_is_retriable,
        extract_retry_after=openai_retry_after,
        label=f"transkripsi {TRANSCRIBE_MODEL}",
    )
    teks = (getattr(hasil, "text", "") or "").strip()
    _catat_biaya(durasi, teks)
    return teks


def _catat_biaya(durasi_detik, teks):
    """Biaya transkripsi dihitung per MENIT audio, bukan per token."""
    try:
        from cost_estimate import estimate_transcribe_cost
        from run_log import log_event

        log_event(
            "transcribe_call",
            (os.getenv("CONTENT_FACTORY_RUN_ID") or "").strip() or None,
            chat_id=(os.getenv("CONTENT_FACTORY_CHAT_ID") or "").strip() or None,
            model=TRANSCRIBE_MODEL,
            audio_seconds=round(durasi_detik, 1),
            chars=len(teks),
            cost_usd=estimate_transcribe_cost(TRANSCRIBE_MODEL, durasi_detik),
        )
    except Exception as e:
        print(f"[warn] cost: gagal mencatat transkripsi: {type(e).__name__}: {e}")


def transcribe_assets(paths, *, max_assets=None):
    """Transkrip semua bahan yang punya audio.

    Return dict {nama_file: transkrip} — hanya berisi yang BERHASIL dan tidak
    kosong. Bahan tanpa audio, gagal ekstrak, atau gagal transkrip tidak muncul,
    sehingga pemanggil tidak pernah menyangka ada teks padahal tidak ada.
    """
    if not TRANSCRIBE_ENABLED or not OPENAI_API_KEY:
        return {}

    batas = TRANSCRIBE_MAX_ASSETS if max_assets is None else max_assets
    hasil = {}

    for path in paths[:batas]:
        ext = os.path.splitext(path)[1].lower()
        if ext not in VIDEO_EXTENSIONS and ext not in AUDIO_EXTENSIONS:
            continue
        if not has_audio(path):
            print(f"[info] transcribe: {os.path.basename(path)} tidak punya trek audio, dilewati.")
            continue

        durasi = media_duration(path)
        if durasi > TRANSCRIBE_MAX_SECONDS:
            print(f"[warn] transcribe: {os.path.basename(path)} {durasi:.0f} dtk "
                  f"melebihi batas {TRANSCRIBE_MAX_SECONDS} dtk, dilewati.")
            continue

        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()
        try:
            if not extract_audio(path, tmp.name):
                continue
            # strip defensif: jangan bergantung pada transcribe_file sudah
            # membersihkannya. Transkrip berisi spasi saja bukan "isi" dan tidak
            # boleh masuk ke prompt seolah-olah user mengatakan sesuatu.
            teks = (transcribe_file(tmp.name, durasi=durasi) or "").strip()
            if teks:
                hasil[os.path.basename(path)] = teks
                print(f"[info] transcribe: {os.path.basename(path)} -> {len(teks)} karakter")
        except Exception as e:
            print(f"[warn] transcribe: {os.path.basename(path)} gagal: {type(e).__name__}: {e}")
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    return hasil
