"""Render engine ContentMakers — berbasis ffmpeg native, bukan moviepy per-frame.

RIWAYAT PERFORMA (diprofilkan langsung, bukan asumsi):
  moviepy vfx.Resize+Crop ke 1080x1920  : 32.1s / klip  vs ffmpeg native: 3.6s
  moviepy concatenate crossfade(compose): 99.1s / 2 klip vs ffmpeg chain: 8.7s
  moviepy CompositeVideoClip text overlay: +45s          vs ffmpeg drawtext: +5.9s
  Total 2 klip pendek: moviepy 332.8s  ->  ffmpeg-native rewrite jauh lebih cepat.

moviepy TIDAK dipakai lagi untuk pemrosesan video/gambar berat — cuma dipakai
untuk baca durasi voice-over (AudioFileClip, murah). Semua scale/crop/loop/
concat/teks dilakukan lewat subprocess ffmpeg (C-native, jauh lebih cepat
daripada operasi per-frame moviepy di Python).
"""

import asyncio
import json
import re
import shutil
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone

import edge_tts

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"),
)
from canvas import (  # noqa: E402
    ASPECT_PRESETS, FIT_MODES, CanvasError, resolve_canvas,
)
from duration import (  # noqa: E402
    DURATION_TOLERANCE, off_target, word_target,
)
import broll as _broll  # noqa: E402
import overlay_remotion as _ovr  # noqa: E402
import visual_quality as _vq  # noqa: E402
from broll import BrollError  # noqa: E402
from edit_plan import MERGE_GAP  # noqa: E402
from retry import with_retry_async  # noqa: E402
from music import (  # noqa: E402
    MUSIC_BELOW_AMBIENT_DB,
    MusicError, auto_volume, build_filter as music_filter, has_audio_stream,
    music_wanted, pick_track, requested_mood, solo_volume,
)
from spoken import prompt_rule, spoken_text  # noqa: E402
from style import (  # noqa: E402
    auto_zoom_enabled, resolve_color_filter, resolve_speed_factor,
    resolve_text_font, resolve_text_position,
)
from subtitle_layout import layout_group, text_width  # noqa: E402
from thumbnail import (  # noqa: E402
    THUMBNAIL_ENABLED, extract_thumbnail, thumbnail_time,
)
from trim_silence import (  # noqa: E402
    MIN_KEEP_DURATION, TRIM_SILENCE, detect_silence, keep_ranges, map_time, total_kept,
)
from moviepy import AudioFileClip

TARGET_W, TARGET_H, TARGET_FIT = resolve_canvas()
# Font dipilih lewat NAMA keluarga (mis. "Inter", "Roboto"), bukan path file --
# path berbeda-beda antar distribusi dan antar versi paket. fc-match yang
# menerjemahkannya, dan selalu mengembalikan sesuatu (font terdekat) sehingga
# nama yang salah ketik tidak membuat render gagal.
SUBTITLE_FONT = os.getenv("SUBTITLE_FONT", "DejaVu Sans")
SUBTITLE_FONT_WEIGHT = os.getenv("SUBTITLE_FONT_WEIGHT", "bold")
FONT_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def resolve_font(nama=None, berat=None):
    """Nama keluarga font -> path file, lewat fc-match.

    fc-match SELALU mengembalikan hasil (font terdekat yang ada), jadi nama yang
    tidak terpasang tidak menggagalkan render -- tapi hasilnya bisa bukan yang
    diminta. Karena itu diperingatkan kalau keluarga yang kembali berbeda.
    """
    if nama is None and os.getenv("SUBTITLE_STYLE", "karaoke") == "capcut":
        local_font = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "fonts", "Montserrat-ExtraBold.ttf")
        if os.path.exists(local_font):
            return os.path.abspath(local_font)

    minta = nama or SUBTITLE_FONT
    pola = f"{minta}:{berat or SUBTITLE_FONT_WEIGHT}"
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}|%{family}", pola],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and "|" in out.stdout:
            berkas, keluarga = out.stdout.split("|", 1)
            if berkas and os.path.exists(berkas):
                if minta.lower() not in keluarga.lower():
                    print(f"[warn] font {minta!r} tidak terpasang; memakai "
                          f"{keluarga.strip()!r}. Lihat `fc-list : family`.")
                return berkas
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[warn] fc-match gagal ({e}); memakai font bawaan.")
    return FONT_FALLBACK


FONT_PATH = resolve_font()
SAFE_TOP_MARGIN_PX = 300
SAFE_BOTTOM_MARGIN_PX = 300

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}
MIN_CLIP_DURATION = 1.5
# Durasi tampil satu GAMBAR saat mode audio asli. Di mode ini tiap klip video
# main sepanjang durasi aslinya, jadi gambar butuh angkanya sendiri.
IMAGE_CLIP_SECONDS = float(os.getenv("IMAGE_CLIP_SECONDS", "3"))
# Parameter audio seragam untuk SEMUA segmen. Concat demuxer dengan -c copy
# menuntut stream yang identik; segmen tanpa audio atau dengan laju berbeda
# akan membuat penggabungan gagal atau menghasilkan audio kacau.
# --- Transisi antar klip ---------------------------------------------------
# "fade" = redup ke hitam di ujung tiap segmen. DIPILIH setelah mengukur:
#   hard cut : 8,02 dtk durasi
#   xfade    : 7,67 dtk  <- MENYUSUT, karena klip tumpang tindih
#   fade     : 8,06 dtk  <- utuh
# xfade memendekkan video sebesar durasi overlap di TIAP sambungan. Dengan 7
# sambungan itu ~2,8 detik pergeseran, dan semua subtitle sesudahnya melenceng --
# padahal sinkronisasi itu baru saja dibangun susah payah lewat map_time().
#
# Alasan kedua, khusus bahan talking-head: crossfade dua rekaman orang yang sama
# di posisi yang sama terlihat seperti bayangan ganda, bukan transisi. Dan
# acrossfade akan menumpuk dua suara yang sedang bicara.
#
# Filter fade ditumpangkan ke encode segmen yang MEMANG sudah berjalan, jadi
# tidak ada pass encoding tambahan.
TRANSITION = os.getenv("TRANSITION", "fade")
TRANSITION_DURATION = float(os.getenv("TRANSITION_DURATION", "0.2"))
# Fade audio dibuat jauh lebih pendek: 0,2 dtk cukup untuk memotong suku kata,
# sedangkan tujuannya hanya mencegah bunyi "klik" di sambungan.
AUDIO_FADE_DURATION = float(os.getenv("AUDIO_FADE_DURATION", "0.06"))
# Jeda ASLI (sebelum dipotong) yang membuat sebuah sambungan pantas diberi fade.
# Di bawah ini artinya ucapan mengalir terus melewati sambungan, dan fade di situ
# terbaca sebagai kerusakan, bukan transisi.
TRANSITION_MIN_GAP = float(os.getenv("TRANSITION_MIN_GAP", "0.6"))

AUDIO_RATE = "44100"
AUDIO_CHANNELS = "2"
FPS = 24

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_DIR = os.path.join(PROJECT_ROOT, "workspace", "state")
ERROR_LOG_PATH = os.path.join(STATE_DIR, "error.log")
STATUS_PATH = os.path.join(STATE_DIR, "render_status.json")


def log_error(context, exc):
    os.makedirs(STATE_DIR, exist_ok=True)
    entry = (
        f"[{datetime.now(timezone.utc).isoformat()}] {context}: {exc}\n"
        f"{traceback.format_exc()}\n"
    )
    with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry)


def write_status(status, **extra):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump({"status": status, **extra}, f, indent=4, ensure_ascii=False)


def run_ffmpeg(args, context):
    """Jalankan ffmpeg, lempar error dengan stderr lengkap kalau gagal."""
    result = subprocess.run(
        ["ffmpeg", "-y", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg gagal ({context}):\n{result.stderr[-2000:]}")


TTS_ATTEMPT_TIMEOUT = int(os.getenv("TTS_ATTEMPT_TIMEOUT_SECONDS", "30"))

# --- Penyedia voice-over ---------------------------------------------------
# "openai" memakai gpt-4o-mini-tts yang gayanya bisa diarahkan lewat instruksi,
# terdengar jauh lebih natural daripada edge-tts yang kaku. "edge" dipertahankan
# sebagai cadangan: gratis, dan satu-satunya yang tetap jalan kalau kredit habis.
TTS_PROVIDER = (os.getenv("TTS_PROVIDER") or "openai").strip().lower()
TTS_MODEL = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")

# Persona = pasangan SUARA + GAYA BICARA. Dipisah jadi nama supaya user bisa
# meminta lewat chat ("pakai suara profesional") tanpa tahu nama suara OpenAI.
# Kedelapan suara di bawah sudah diverifikasi bekerja lewat relay.
VOICE_PERSONAS = {
    "ramah": {
        "voice": "nova",
        "instructions": "Bicara Bahasa Indonesia yang hangat dan akrab, seperti "
                        "menjelaskan ke teman. Tempo sedang, jangan datar.",
    },
    "profesional": {
        "voice": "onyx",
        "instructions": "Bicara Bahasa Indonesia yang tenang, jelas, dan berwibawa "
                        "seperti presenter bisnis. Tempo mantap, artikulasi tegas.",
    },
    "energik": {
        "voice": "shimmer",
        "instructions": "Bicara Bahasa Indonesia dengan antusias dan bersemangat "
                        "seperti konten promosi. Tempo agak cepat, intonasi naik-turun.",
    },
    "tenang": {
        "voice": "alloy",
        "instructions": "Bicara Bahasa Indonesia dengan tenang dan lembut, tempo "
                        "pelan, cocok untuk penjelasan yang perlu dicerna.",
    },
    "bercerita": {
        "voice": "fable",
        "instructions": "Bicara Bahasa Indonesia seperti bercerita: ada jeda "
                        "dramatis, penekanan pada bagian penting, tidak terburu-buru.",
    },
}
TTS_PERSONA = (os.getenv("TTS_PERSONA") or "ramah").strip().lower()


def voice_persona():
    """(suara, instruksi) untuk run ini.

    Urutan: TTS_VOICE/TTS_INSTRUCTIONS eksplisit menimpa persona, supaya kamu bisa
    memakai suara yang belum ada di daftar tanpa mengubah kode. Nama persona tak
    dikenal -> peringatan + default, bukan diam-diam berganti gaya.
    """
    p = VOICE_PERSONAS.get(TTS_PERSONA)
    if p is None:
        print(f"[warn] TTS_PERSONA tidak dikenal ({TTS_PERSONA!r}), memakai 'ramah'. "
              f"Pilihan: {', '.join(sorted(VOICE_PERSONAS))}.")
        p = VOICE_PERSONAS["ramah"]
    return (
        os.getenv("TTS_VOICE") or p["voice"],
        os.getenv("TTS_INSTRUCTIONS") or p["instructions"],
    )


TTS_VOICE, TTS_INSTRUCTIONS = voice_persona()
# Jenis suara narasi yang diminta user: "wanita" (bawaan) atau "pria". Berlaku untuk
# ElevenLabs DAN cadangan edge-tts, supaya kegagalan tidak diam-diam mengganti jenis suara.
TTS_VOICE_GENDER = (os.getenv("TTS_VOICE_GENDER") or "wanita").strip().lower()
EDGE_VOICES = {"wanita": "id-ID-GadisNeural", "pria": "id-ID-ArdiNeural"}
EDGE_VOICE = os.getenv("EDGE_TTS_VOICE") or EDGE_VOICES.get(TTS_VOICE_GENDER, "id-ID-GadisNeural")

# --- ElevenLabs ------------------------------------------------------------
# Suara BAWAAN (premade) saja: paket gratis menolak suara pustaka lewat API (HTTP 402,
# terukur 24 Sep untuk suara narator Indonesia di akun user). Dipilih dengan MENGUKUR:
# kalimat Indonesia yang sama disintesis tiap suara lalu ditranskrip Whisper lokal --
# semua di bawah 0% salah kata. Brian/Daniel/Eric (14%, "laksa mana") dan Sarah (7%) tidak dipakai.
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
ELEVENLABS_SUARA = {
    ("wanita", "energik"): ("XrExE9yKIg1WjnnlVkGX", "Matilda"),
    ("wanita", None): ("hpp4J3VqNfWAUOO0d1Us", "Bella"),
    ("pria", "energik"): ("TX3LPaxmHKxFdv7VOQHJ", "Liam"),
    ("pria", "ramah"): ("iP95p4xoKVk53GoZ742B", "Chris"),
    ("pria", None): ("JBFqnCBsd6RMkjVDRZzb", "George"),
}
TTS_CATATAN = {}
TTS_KATA = []        # [{word, start, end}] dari mesin TTS: dasar teks yang mengikuti suara


def kata_dari_karakter(chars, starts, ends):
    """Waktu per KARAKTER (ElevenLabs) -> waktu per kata (dipisah spasi)."""
    kata, buf, t0, t1 = [], "", None, None
    for c, s, e in zip(chars, starts, ends):
        if c.isspace():
            if buf:
                kata.append({"word": buf, "start": round(t0, 3), "end": round(t1, 3)})
            buf, t0 = "", None
            continue
        if not buf:
            t0 = s
        buf += c
        t1 = e
    if buf:
        kata.append({"word": buf, "start": round(t0, 3), "end": round(t1, 3)})
    return kata


def petakan_kata(tulisan, waktu):
    """Kata TULISAN (full_voice_over, ejaan benar) diberi waktu dari kata UCAPAN (naskah
    lafal yang dibacakan TTS). Jumlah sama -> satu-satu. Beda (ejaan fonetis memecah/
    menggabung kata) -> dipetakan menurut posisi karakter relatif, monoton naik."""
    kt = (tulisan or "").split()
    if not kt or not waktu:
        return []
    if len(kt) == len(waktu):
        return [{"word": w, "start": x["start"], "end": x["end"]} for w, x in zip(kt, waktu)]
    import bisect
    total_u = sum(len(x["word"]) + 1 for x in waktu)
    pos_u, acc = [], 0
    for x in waktu:
        pos_u.append(acc / total_u)
        acc += len(x["word"]) + 1
    total_t = sum(len(w) + 1 for w in kt)
    hasil, acc = [], 0
    for w in kt:
        f = acc / total_t
        i = max(0, bisect.bisect_right(pos_u, f) - 1)
        hasil.append({"word": w, "start": waktu[i]["start"]})
        acc += len(w) + 1
    for j, h in enumerate(hasil):
        h["end"] = hasil[j + 1]["start"] if j + 1 < len(hasil) else waktu[-1]["end"]
        if h["end"] <= h["start"]:
            h["end"] = h["start"] + 0.12
    return hasil

def suara_elevenlabs():
    """(voice_id, nama) dari TTS_VOICE_GENDER + TTS_PERSONA; ELEVENLABS_VOICE_ID menimpa."""
    paksa = (os.getenv("ELEVENLABS_VOICE_ID") or "").strip()
    if paksa:
        return paksa, paksa
    g = TTS_VOICE_GENDER if TTS_VOICE_GENDER in ("pria", "wanita") else "wanita"
    return ELEVENLABS_SUARA.get((g, TTS_PERSONA)) or ELEVENLABS_SUARA[(g, None)]
EDGE_RATE = os.getenv("EDGE_TTS_RATE", "+5%")


def _tts_retriable(exc):
    """edge-tts memakai layanan Microsoft tanpa autentikasi: tidak ada kategori
    kegagalan permanen yang jelas selain bug pemakaian. Kegagalan jaringan/protokol
    diperlakukan sementara; TypeError/ValueError (salah pakai API) tidak."""
    return not isinstance(exc, (TypeError, ValueError, KeyError))


async def _voice_edge(text, output_audio):
    """edge-tts. Instance Communicate DIBUAT BARU tiap percobaan -- objek yang
    sudah gagal menyimpan state koneksi dan tidak aman dipakai ulang."""

    async def sekali():
        communicate = edge_tts.Communicate(text, voice=EDGE_VOICE, rate=EDGE_RATE,
                                           boundary="WordBoundary")
        kata = []
        with open(output_audio, "wb") as f:
            async for ch in communicate.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    mulai = ch["offset"] / 1e7          # satuan 100 ns
                    kata.append({"word": ch["text"], "start": round(mulai, 3),
                                 "end": round(mulai + ch["duration"] / 1e7, 3)})
        TTS_KATA[:] = kata

    await with_retry_async(
        sekali, is_retriable=_tts_retriable,
        attempt_timeout=TTS_ATTEMPT_TIMEOUT, label="voice-over edge-tts",
    )


async def _voice_openai(text, output_audio):
    """OpenAI TTS. `instructions` mengarahkan GAYA bicara -- itu yang membedakan
    gpt-4o-mini-tts dari TTS lama yang membaca datar."""
    from common import make_openai_client, openai_is_retriable, openai_retry_after
    from retry import with_retry

    client = make_openai_client(timeout=TTS_ATTEMPT_TIMEOUT * 2, service="TTS")

    def sekali():
        kw = {}
        # Hanya model gpt-4o-* yang menerima `instructions`; mengirimnya ke
        # tts-1 akan ditolak.
        if TTS_MODEL.startswith("gpt-4o") and TTS_INSTRUCTIONS:
            kw["instructions"] = TTS_INSTRUCTIONS
        r = client.audio.speech.create(
            model=TTS_MODEL, voice=TTS_VOICE, input=text, **kw)
        with open(output_audio, "wb") as f:
            f.write(r.content)
        if os.path.getsize(output_audio) == 0:
            raise RuntimeError("TTS mengembalikan audio kosong")

    with_retry(
        sekali, is_retriable=openai_is_retriable,
        extract_retry_after=openai_retry_after, label=f"voice-over {TTS_MODEL}",
    )


class TtsTidakTersedia(RuntimeError):
    """Kegagalan permanen (key salah, kuota habis, butuh paket berbayar): jangan diulang."""


def _elevenlabs_http(url, *, data=None):
    import urllib.request
    kunci = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
    req = urllib.request.Request(url, data=data, headers={
        "xi-api-key": kunci, "Content-Type": "application/json", "User-Agent": "content-factory/1.0"})
    return urllib.request.urlopen(req, timeout=TTS_ATTEMPT_TIMEOUT)


def _sisa_kuota_elevenlabs():
    """Karakter tersisa, atau None bila tidak bisa dicek (tidak menghalangi)."""
    try:
        with _elevenlabs_http("https://api.elevenlabs.io/v1/user/subscription") as r:
            s = json.load(r)
        return int(s["character_limit"]) - int(s["character_count"])
    except Exception:
        return None


async def _voice_elevenlabs(text, output_audio):
    import urllib.error
    from retry import with_retry

    if not (os.getenv("ELEVENLABS_API_KEY") or "").strip():
        raise TtsTidakTersedia("ELEVENLABS_API_KEY belum diisi")
    sisa = _sisa_kuota_elevenlabs()
    # Penghitung ElevenLabs tertunda (terukur: 0 setelah ~900 karakter) -- ini pengaman
    # kasar, bukan jaminan; penolakan kuota di tengah jalan tetap jatuh ke edge-tts.
    if sisa is not None and sisa < len(text):
        raise TtsTidakTersedia(f"kuota ElevenLabs tersisa {sisa} karakter, naskah {len(text)}")
    vid, _ = suara_elevenlabs()
    badan = json.dumps({"text": text, "model_id": ELEVENLABS_MODEL, "language_code": "id"}).encode()

    def sekali():
        try:
            with _elevenlabs_http(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
                                  "/with-timestamps?output_format=mp3_44100_128", data=badan) as r:
                jawab = json.load(r)
            import base64
            isi = base64.b64decode(jawab.get("audio_base64") or "")
            a = jawab.get("alignment") or {}
            TTS_KATA[:] = kata_dari_karakter(a.get("characters") or [],
                                             a.get("character_start_times_seconds") or [],
                                             a.get("character_end_times_seconds") or [])
        except urllib.error.HTTPError as e:
            pesan = e.read()[:200].decode(errors="replace")
            if e.code in (401, 402, 403, 422) or "quota" in pesan.lower():
                raise TtsTidakTersedia(f"ElevenLabs HTTP {e.code}: {pesan}")
            raise
        if not isi:
            raise RuntimeError("ElevenLabs mengembalikan audio kosong")
        with open(output_audio, "wb") as f:
            f.write(isi)

    with_retry(sekali, is_retriable=lambda e: not isinstance(e, TtsTidakTersedia),
               label="voice-over ElevenLabs")


async def generate_voice(text, output_audio):
    """Voice-over dgn penyedia yang bisa dipilih.

    Kegagalan OpenAI JATUH ke edge-tts, bukan menggagalkan render: kredit habis
    atau relay bermasalah tidak boleh membuat video gagal total kalau masih ada
    jalur gratis yang bekerja.
    """
    TTS_CATATAN.clear()
    TTS_KATA.clear()
    if TTS_PROVIDER == "elevenlabs":
        vid, nama = suara_elevenlabs()
        try:
            print(f"🎙️ Voice-over ElevenLabs {ELEVENLABS_MODEL} (suara: {nama})")
            await _voice_elevenlabs(text, output_audio)
            _catat_pemakaian_tts(text, mesin="elevenlabs")
            TTS_CATATAN.update(mesin="elevenlabs", suara=nama)
            return
        except Exception as e:
            alasan = f"{type(e).__name__}: {str(e)[:120]}"
            print(f"[warn] ElevenLabs gagal ({alasan}); jatuh ke edge-tts.")
            TTS_CATATAN.update(cadangan=True, alasan=alasan)

    if TTS_PROVIDER == "openai":
        try:
            print(f"🎙️ Voice-over {TTS_MODEL} (suara: {TTS_VOICE})")
            await _voice_openai(text, output_audio)
            _catat_pemakaian_tts(text, mesin=TTS_MODEL)
            return
        except Exception as e:
            print(f"[warn] TTS {TTS_MODEL} gagal ({type(e).__name__}: {str(e)[:80]}); "
                  "jatuh ke edge-tts.")

    print(f"🎙️ Voice-over edge-tts (suara: {EDGE_VOICE})")
    await _voice_edge(text, output_audio)
    _catat_pemakaian_tts(text, mesin="edge-tts")
    TTS_CATATAN.update(mesin="edge-tts", suara=EDGE_VOICE)


def _catat_pemakaian_tts(text, mesin="edge-tts"):
    """Catat jumlah karakter TTS. Dibungkus try/except -- pelacakan biaya tidak
    boleh menggagalkan render yang sudah berhasil."""
    try:
        from cost_estimate import estimate_tts_cost
        from run_log import log_event

        chars = len(text or "")
        log_event(
            "tts_call",
            (os.getenv("CONTENT_FACTORY_RUN_ID") or "").strip() or None,
            chat_id=(os.getenv("CONTENT_FACTORY_CHAT_ID") or "").strip() or None,
            engine=mesin,
            chars=chars,
            cost_usd=estimate_tts_cost(mesin, chars),
        )
    except Exception as e:
        print(f"[warn] cost: gagal mencatat pemakaian TTS: {type(e).__name__}: {e}")


# Ukuran versi kecil yang di-blur sebelum diperbesar jadi latar. gblur berbiaya
# kuadratik terhadap luas, jadi memblur 1080x1920 langsung jauh lebih mahal
# daripada memblur versi kecil lalu meregangkannya -- dan hasilnya tidak berbeda
# karena memang sengaja dibuat kabur.
BLUR_SMALL_W = int(os.getenv("BLUR_SMALL_WIDTH", "160"))
BLUR_SIGMA = float(os.getenv("BLUR_SIGMA", "18"))


# Ken Burns: seberapa besar gambar membesar dari awal ke akhir klipnya. Subtle
# dengan sengaja -- efek yang terlalu agresif terasa seperti kesalahan, bukan gaya.
ZOOM_MAX_FACTOR = float(os.getenv("ZOOM_MAX_FACTOR", "1.12"))


def scale_crop_filter(w=None, h=None, fit=None, duration=None, zoom=False, color_filter=None):
    """Filter ffmpeg untuk memuat bahan ke kanvas w x h.

    CATATAN BUG LAMA: versi sebelumnya memakai
        scale=-2:{H}:force_original_aspect_ratio=increase
    yang hanya menyebut SATU dimensi. `increase` tidak punya pembanding lebar,
    jadi untuk sumber yang lebih sempit dari kanvas hasilnya lebih kecil dari
    lebar target dan crop gagal. Tidak pernah terlihat selama kanvas selalu 9:16;
    langsung meledak begitu rasio jadi parameter. Kedua dimensi kini disebut.

    `zoom=True` menambah efek Ken Burns (HANYA dipakai pemanggil untuk bahan
    GAMBAR -- lihat build_segment). `color_filter` adalah chain filter ffmpeg
    siap pakai dari style.resolve_color_filter(), atau None.
    """
    w = w or TARGET_W
    h = h or TARGET_H
    fit = (fit or TARGET_FIT).lower()

    if fit == "letterbox":
        base = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black")
    elif fit == "blur":
        kecil_h = max(2, int(BLUR_SMALL_W * h / w) // 2 * 2)
        base = (
            f"split[bg][fg];"
            f"[bg]scale={BLUR_SMALL_W}:{kecil_h}:force_original_aspect_ratio=increase,"
            f"crop={BLUR_SMALL_W}:{kecil_h},gblur=sigma={BLUR_SIGMA},"
            f"scale={w}:{h}[bgx];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgx];"
            f"[bgx][fgx]overlay=(W-w)/2:(H-h)/2"
        )
    else:
        base = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}")

    if zoom and duration and duration > 0:
        # Resep zoompan standar untuk SATU gambar diam yang di-loop (`-loop 1`):
        # d = total frame OUTPUT yang diminta (bukan 1) -- itu yang membuat
        # zoompan sendiri men-generate seluruh durasi dari satu input, dan
        # kenapa filter `fps=` terpisah TIDAK ditambahkan lagi sesudahnya
        # (zoompan sudah menormalkan lewat parameter s=/fps= miliknya sendiri).
        # Versi sebelumnya memakai variabel `time` yang TIDAK ADA di zoompan,
        # sehingga zoom selalu diam di 1.0 -- tidak pernah membesar.
        frame_total = max(2, round(duration * FPS))
        step = (ZOOM_MAX_FACTOR - 1.0) / frame_total
        base += (
            f",zoompan=z='min(zoom+{step:.6f},{ZOOM_MAX_FACTOR})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frame_total}:s={w}x{h}:fps={FPS}"
        )
    else:
        base += f",fps={FPS}"

    if color_filter:
        base += f",{color_filter}"

    return base


def fade_filters(durasi, *, keep_audio, fade_in=True, fade_out=True):
    """(filter video, filter audio) untuk transisi redup di ujung segmen.

    Segmen yang terlalu pendek untuk menampung dua fade dilewati — memaksakannya
    membuat klip nyaris tidak pernah terlihat terang.

    Fade masuk dan keluar dikendalikan TERPISAH, dan itu yang membuat aturan
    "hard cut di dalam klip, fade hanya di pergantian klip" bisa diwujudkan:
    satu sambungan mematikan fade keluar segmen sebelumnya SEKALIGUS fade masuk
    segmen sesudahnya. Kalau hanya salah satu yang dimatikan, layar tetap
    berkedip hitam di sambungan itu.

    Dua sebab konkret di balik ini:
    - Frame 0 hitam membuat Telegram (yang mengabaikan thumbnail kita dan memakai
      frame pertama video) menampilkan preview hitam polos.
    - Pemotongan jeda memecah satu klip jadi beberapa segmen. Dengan fade di tiap
      segmen, setiap jeda yang dibuang meninggalkan kedipan hitam DI TENGAH
      KALIMAT: terukur 17 kedipan dalam 68,9 detik pada video nyata milik user.

    Fade AUDIO tetap dipasang di kedua ujung apa pun keputusan fade video: ia
    hanya 0,06 detik dan tugasnya mencegah bunyi "klik" di sambungan, bukan
    menjadi transisi.
    """
    if TRANSITION != "fade" or TRANSITION_DURATION <= 0:
        return "", ""
    d = min(TRANSITION_DURATION, durasi / 3)
    if d < 0.05:
        return "", ""

    bagian = []
    if fade_in:
        bagian.append(f"fade=t=in:st=0:d={d:.3f}")
    if fade_out:
        bagian.append(f"fade=t=out:st={max(0, durasi - d):.3f}:d={d:.3f}")
    vf = ",".join(bagian)

    if not keep_audio:
        return vf, ""
    a = min(AUDIO_FADE_DURATION, durasi / 6)
    af = f"afade=t=in:st=0:d={a:.3f},afade=t=out:st={max(0, durasi - a):.3f}:d={a:.3f}"
    return vf, af




def rencana_semua_klip(existing_assets):
    """Perilaku lama: setiap bahan dipakai utuh, hanya jeda diam yang dibuang.

    Return (rencana, detik_dibuang).
    """
    rencana, dibuang = [], 0.0
    for path in existing_assets:
        adalah_video = os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS
        asli = media_duration(path) if adalah_video else IMAGE_CLIP_SECONDS
        asli = asli if asli > 0 else IMAGE_CLIP_SECONDS
        if TRIM_SILENCE and adalah_video:
            ranges = keep_ranges(asli, detect_silence(path))
        else:
            ranges = [(0.0, asli)]
        baru = total_kept(ranges)
        dibuang += asli - baru
        # `asli` disimpan karena rencana transisi butuh tahu berapa jeda
        # yang DIBUANG di ekor klip, bukan cuma berapa yang dipertahankan.
        rencana.append({"path": path, "ranges": ranges, "durasi": baru, "asli": asli})
    return rencana, dibuang


def _potong_jeda_dalam(path, ranges, asli, cache):
    """Buang jeda diam DI DALAM rentang terpilih (perilaku 'potong jeda' yang sama
    dengan mode biasa). Rentang yang habis terpotong dikembalikan utuh."""
    if not TRIM_SILENCE:
        return ranges
    if path not in cache:
        cache[path] = keep_ranges(asli, detect_silence(path))
    hasil = []
    for a, b in ranges:
        for ka, kb in cache[path]:
            x, y = max(a, ka), min(b, kb)
            if y - x >= MIN_KEEP_DURATION:
                hasil.append((x, y))
    return hasil or ranges


def rencana_dari_plan(plan, existing_assets):
    """Ubah rencana seleksi (dari tahap brief) menjadi `rencana` renderer.

    Diverifikasi ULANG di sini terhadap berkas yang benar-benar ada, meskipun
    brief sudah memverifikasinya: nama berkas harus ada di daftar bahan, dan
    rentang dijepit ke durasi nyata. Pick yang tidak lolos dibuang, bukan
    dipercaya. Return [] kalau tidak ada yang tersisa (pemanggil kembali ke
    perilaku lama).
    """
    peta = {os.path.basename(p): p for p in existing_assets}
    durasi_cache, jeda_cache, item_list = {}, {}, []

    for pick in plan.get("picks") or []:
        path = peta.get(pick.get("file"))
        if not path:
            print(f"[warn] seleksi: {pick.get('file')!r} tidak ada di daftar bahan — dilewati.")
            continue
        if path not in durasi_cache:
            durasi_cache[path] = media_duration(path)
        asli = durasi_cache[path]
        a, b = pick["range"]
        a, b = max(0.0, float(a)), min(float(b), asli) if asli > 0 else float(b)
        if b - a < 0.3:
            continue

        anak = item_list[-1] if item_list else None
        if anak and anak["path"] == path:
            # Klip yang sama berurutan: satu item, beberapa rentang (hard cut di
            # antaranya). Rentang yang nyaris bersambung digabung supaya tidak
            # ada loncatan tanpa alasan.
            terakhir_a, terakhir_b = anak["ranges"][-1]
            if a - terakhir_b <= MERGE_GAP and a >= terakhir_a:
                anak["ranges"][-1] = (terakhir_a, max(terakhir_b, b))
            else:
                anak["ranges"].append((a, b))
        else:
            item_list.append({"path": path, "ranges": [(a, b)], "asli": asli,
                              "fade_masuk": bool(pick.get("fade_masuk"))})

    for item in item_list:
        item["ranges"] = _potong_jeda_dalam(item["path"], item["ranges"], item["asli"], jeda_cache)
        item["durasi"] = total_kept(item["ranges"])
    return [i for i in item_list if i["durasi"] > 0]


POTONG_VISUAL = {}


def _catat_visual(kunci, isi):
    POTONG_VISUAL.setdefault(kunci, []).append(isi)


def _buruk_visual(path):
    """Rentang tak layak satu video, atau None bila tidak terukur (dicatat, TIDAK dianggap
    bersih -- aturan #7)."""
    if os.path.splitext(path)[1].lower() not in VIDEO_EXTENSIONS:
        return []
    try:
        return _vq.analisis(path)["buruk"]
    except Exception as e:
        _catat_visual("gagal_ukur", {"file": os.path.basename(path), "alasan": f"{type(e).__name__}: {e}"[:160]})
        return None


def terapkan_potong_visual(rencana, data):
    """Buang bagian goyang/oleng/buram dari tiap item rencana (mode audio asli/mute).

    Bagian yang berisi UCAPAN tidak dibuang (memotongnya menghilangkan kata-kata); ia
    dicatat sebagai dipertahankan. Sambungan yang tercipta karena potongan visual diberi
    fade (`fade_setelah`) -- permintaan user: "dipotong dan diberikan transisi"."""
    if not _vq.aktif():
        return rencana
    kata_per = data.get("transcript_words") or {}
    hasil = []
    for item in rencana:
        buruk = _buruk_visual(item["path"])
        if not buruk:
            hasil.append(item)
            continue
        nama = os.path.basename(item["path"])
        kata = kata_per.get(nama) or []
        dipotong = []
        for a, b, alasan in buruk:
            if any(float(w.get("start", 0)) < b and float(w.get("end", 0)) > a for w in kata):
                _catat_visual("dipertahankan_ucapan", {"file": nama, "dari": round(a, 2),
                                                       "sampai": round(b, 2), "alasan": alasan})
            else:
                dipotong.append((a, b, alasan))
        if not dipotong:
            hasil.append(item)
            continue
        ranges = _vq.kurangi(item["ranges"], dipotong, min_keep=MIN_KEEP_DURATION)
        for a, b, alasan in dipotong:
            _catat_visual("dipotong", {"file": nama, "dari": round(a, 2), "sampai": round(b, 2),
                                       "alasan": alasan})
        if not ranges:
            _catat_visual("klip_dibuang", {"file": nama})
            continue
        fade_setelah = {j for j in range(len(ranges) - 1)
                        if any(ranges[j][1] <= a < ranges[j + 1][0] for a, _, _ in dipotong)}
        hasil.append({**item, "ranges": ranges, "durasi": total_kept(ranges),
                      "fade_setelah": fade_setelah})
    if not hasil:          # SEMUA klip tak layak: lebih baik tampil apa adanya daripada kosong
        _catat_visual("dikembalikan", {"alasan": "semua klip tak layak; dipakai apa adanya"})
        return rencana
    return hasil


def rentang_layak_ai(path):
    """(a, b) rentang layak TERPANJANG untuk mode voice-over AI, atau None bila tidak
    perlu dipotong / tidak terukur."""
    if not _vq.aktif():
        return None
    buruk = _buruk_visual(path)
    if not buruk:
        return None
    try:
        dur = media_duration(path)
    except Exception:
        return None
    layak = _vq.kurangi([(0.0, dur)], buruk, min_keep=MIN_CLIP_DURATION)
    nama = os.path.basename(path)
    for a, b, alasan in buruk:
        _catat_visual("dipotong", {"file": nama, "dari": round(a, 2), "sampai": round(b, 2),
                                   "alasan": alasan})
    if not layak:
        _catat_visual("dikembalikan", {"file": nama, "alasan": "tidak ada bagian layak yang cukup panjang"})
        return None
    return max(layak, key=lambda r: r[1] - r[0])


def fade_flags(batas, jumlah):
    """[(fade_in, fade_out)] per segmen dari keputusan per SAMBUNGAN.

    `batas[k]` = ada fade di sambungan antara segmen k dan k+1. Satu sambungan
    mengendalikan dua ujung sekaligus (keluar dari k, masuk ke k+1) -- kalau
    hanya salah satunya dimatikan, layar tetap berkedip hitam di situ.

    Segmen pertama tidak pernah fade masuk (frame 0 hitam merusak preview), dan
    segmen terakhir selalu fade keluar (penutup video).
    """
    hasil = []
    for k in range(jumlah):
        masuk = batas[k - 1] if k > 0 else False
        keluar = batas[k] if k < jumlah - 1 else True
        hasil.append((masuk, keluar))
    return hasil


def sambungan_audio_asli(rencana):
    """Fade di sambungan mana saja, untuk mode audio asli.

    Aturannya, sesuai keputusan user: potongan DI DALAM satu klip (akibat jeda
    atau bagian yang dibuang) selalu hard cut; pergantian klip diberi fade HANYA
    kalau di sambungan itu memang ada jeda bicara -- yaitu jeda yang kita buang
    di ekor klip sebelumnya ditambah jeda di awal klip berikutnya.

    Tanpa aturan ini tiap jeda yang dibuang meninggalkan kedipan hitam di tengah
    kalimat: terukur 17 kedipan dalam 68,9 detik pada video nyata.
    """
    if rencana and "fade_masuk" in rencana[0]:
        return sambungan_plan(rencana)

    batas = []
    for i, item in enumerate(rencana):
        ranges = item["ranges"]
        for j in range(len(ranges)):
            terakhir_di_klip = j == len(ranges) - 1
            if not terakhir_di_klip:
                # potongan internal -> hard cut, KECUALI bagian goyang yang dibuang di situ
                batas.append(j in item.get("fade_setelah", ()))
                continue
            if i == len(rencana) - 1:
                continue                      # tidak ada sambungan sesudahnya
            asli = item.get("asli") or ranges[-1][1]
            ekor = max(0.0, asli - ranges[-1][1])
            kepala = max(0.0, rencana[i + 1]["ranges"][0][0])
            batas.append(ekor + kepala >= TRANSITION_MIN_GAP)
    return batas


def sambungan_plan(rencana):
    """Fade di sambungan mana saja, untuk rencana hasil SELEKSI KONTEN.

    Di sini keputusannya semantik, bukan fisik: potongan di dalam satu klip
    (bagian yang dibuang di antaranya) selalu hard cut, dan pergantian klip
    hanya diberi fade kalau editor menandainya sebagai pergantian topik --
    sudah dibatasi sepertiga oleh susun_rencana(). Sesuai keputusan user:
    "hard cut untuk konten yang tidak sesuai, fade untuk pergantian klip tapi
    tidak semuanya".
    """
    batas = []
    for i, item in enumerate(rencana):
        n = len(item["ranges"])
        batas.extend(j in item.get("fade_setelah", ()) for j in range(n - 1))
        if i < len(rencana) - 1:
            batas.append(bool(rencana[i + 1].get("fade_masuk")))
    return batas


def sambungan_scene(durasi_klip, scenes):
    """Fade di sambungan mana saja, untuk mode voice-over AI.

    Di sini tidak ada jeda bicara yang bisa diukur (naskahnya dibacakan tanpa
    putus), jadi yang dipakai adalah TEKS: sambungan yang jatuh di tengah satu
    scene berarti kalimatnya masih berjalan -> hard cut. Sambungan yang jatuh
    di pergantian scene adalah pergantian gagasan -> fade.

    Tanpa data scene sama sekali, semua sambungan diberi fade (perilaku lama).
    """
    if not scenes:
        return [True] * max(0, len(durasi_klip) - 1)
    batas, waktu = [], 0.0
    for d in durasi_klip[:-1]:
        waktu += d
        di_tengah_scene = any(
            float(sc.get("start", 0)) + 0.15 < waktu < float(sc.get("end", 0)) - 0.15
            for sc in scenes if sc.get("end") is not None
        )
        batas.append(not di_tengah_scene)
    return batas


def build_segment(asset_path, duration, segment_path, *, keep_audio=False,
                  potong=None, fade_in=True, fade_out=True, speed_factor=1.0):
    """Satu bahan mentah (gambar atau video) -> satu segmen 9:16 sepanjang `duration`.

    `keep_audio=True` (mode audio asli) mempertahankan suara asli video, dan
    memberi gambar trek audio SENYAP sepanjang durasinya. Trek senyap itu wajib:
    concat demuxer dengan -c copy menuntut semua segmen punya susunan stream yang
    sama, jadi satu segmen tanpa audio akan merusak penggabungan.

    `speed_factor` (speed ramp) HANYA diterapkan untuk VIDEO tanpa audio yang
    dipertahankan (`keep_audio=False`) -- lihat catatan di style.resolve_speed_factor()
    kenapa mode audio asli/mute tidak pernah mengirim nilai selain 1.0 ke sini.
    Zoom otomatis (Ken Burns) hanya untuk GAMBAR, diputuskan di sini dari ekstensi
    berkas, bukan diminta pemanggil.
    """
    ext = os.path.splitext(asset_path)[1].lower()
    is_image = ext in IMAGE_EXTENSIONS
    enable_xfade = os.getenv("ENABLE_XFADE", "False").lower() == "true"
    pad = TRANSITION_DURATION if enable_xfade else 0
    actual_duration = duration + pad

    _, filter_warna = resolve_color_filter()
    vf = scale_crop_filter(duration=actual_duration,
                           zoom=is_image and auto_zoom_enabled(),
                           color_filter=filter_warna)

    if enable_xfade:
        fade_in = False
        fade_out = False
        
    fade_v, fade_a = fade_filters(actual_duration, keep_audio=keep_audio,
                                  fade_in=fade_in, fade_out=fade_out)
    if fade_v:
        vf = f"{vf},{fade_v}"
    audio_enc = ["-c:a", "aac", "-ar", AUDIO_RATE, "-ac", AUDIO_CHANNELS]
    if fade_a:
        audio_enc = ["-af", fade_a] + audio_enc
    # `potong` = (mulai, selesai) untuk mengambil sepotong klip saja (pemotongan
    # jeda). -ss diletakkan SEBELUM -i supaya ffmpeg mencari cepat ke posisi itu
    # alih-alih mendekode dari awal.
    
    iris = []
    if potong:
        mulai = max(0.0, potong[0] - (pad / 2))
        selesai = potong[1] + (pad / 2)
        iris = ["-ss", f"{mulai:.3f}", "-to", f"{selesai:.3f}"]

    if ext in IMAGE_EXTENSIONS:
        args = ["-loop", "1", "-i", asset_path]
        if keep_audio:
            args += ["-f", "lavfi", "-i",
                     f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}"]
        args += ["-t", f"{actual_duration:.3f}", "-vf", vf,
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast"]
        args += audio_enc if keep_audio else []
        run_ffmpeg(args + [segment_path], f"gambar {os.path.basename(asset_path)}")

    elif ext in VIDEO_EXTENSIONS:
        if keep_audio:
            # TANPA -stream_loop: di mode audio asli klip main sepanjang durasi
            # aslinya. Mengulang video berarti mengulang ucapannya juga.
            args = [*iris, "-i", asset_path, "-vf", vf,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", *audio_enc]
        else:
            # -stream_loop -1 mengulang video kalau lebih pendek dari `duration`;
            # -t memotongnya persis di durasi target.
            #
            # speed ramp: `-t` di sini adalah batas durasi OUTPUT (posisinya
            # setelah -i), jadi tetap actual_duration apa pun speed_factor-nya --
            # setpts hanya mengubah SEBERAPA BANYAK bahan sumber terpakai untuk
            # mengisi jendela waktu itu (lebih cepat = lebih banyak, lebih lambat
            # = diulang lebih sering lewat -stream_loop). Slot di timeline gabungan
            # tidak pernah berubah, jadi ini aman untuk scene/subtitle di sekitarnya.
            vf_speed = vf if speed_factor == 1.0 else f"{vf},setpts=PTS/{speed_factor}"
            sumber = asset_path
            if potong:
                # Rentang layak saja (bagian goyang dibuang), lalu di-loop bila lebih pendek
                # dari slot. Dipotong ke berkas sementara: -stream_loop tidak menghormati -ss.
                sumber = os.path.splitext(segment_path)[0] + "_layak.mp4"
                run_ffmpeg(["-ss", f"{potong[0]:.3f}", "-to", f"{potong[1]:.3f}", "-i", asset_path,
                            "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", sumber],
                           f"rentang layak {os.path.basename(asset_path)}")
            args = ["-stream_loop", "-1", "-i", sumber,
                    "-t", f"{actual_duration:.3f}", "-vf", vf_speed, "-an",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast"]
        try:
            run_ffmpeg(args + [segment_path], f"video {os.path.basename(asset_path)}")
        finally:
            if not keep_audio and potong and sumber != asset_path and os.path.exists(sumber):
                os.remove(sumber)

    else:
        raise ValueError(f"Ekstensi tidak didukung untuk '{asset_path}': {ext}")


def concat_segments(segment_paths, output_path, workdir, keep_audio=False):
    """Gabung semua segmen jadi satu. Menggunakan Xfade jika diaktifkan."""
    enable_xfade = os.getenv("ENABLE_XFADE", "False").lower() == "true"
    
    if enable_xfade and len(segment_paths) > 1:
        # XFADE Logic
        args = []
        for p in segment_paths:
            args.extend(["-i", p])
            
        filter_complex = []
        offset = 0.0
        last_v = "0:v"
        last_a = "0:a" if keep_audio else None
        
        for i in range(1, len(segment_paths)):
            prev_dur = media_duration(segment_paths[i-1])
            offset += (prev_dur - TRANSITION_DURATION)
            
            next_v = f"{i}:v"
            out_v = f"v{i}"
            filter_complex.append(f"[{last_v}][{next_v}]xfade=transition=fade:duration={TRANSITION_DURATION}:offset={offset}[{out_v}]")
            last_v = out_v
            
            if keep_audio:
                next_a = f"{i}:a"
                out_a = f"a{i}"
                filter_complex.append(f"[{last_a}][{next_a}]acrossfade=d={TRANSITION_DURATION}[{out_a}]")
                last_a = out_a
                
        args.extend(["-filter_complex", ";".join(filter_complex)])
        args.extend(["-map", f"[{last_v}]"])
        if keep_audio:
            args.extend(["-map", f"[{last_a}]"])
            
        args.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast"])
        if keep_audio:
            args.extend(["-c:a", "aac", "-ar", AUDIO_RATE, "-ac", AUDIO_CHANNELS])
        
        args.append(output_path)
        run_ffmpeg(args, "penggabungan segmen dengan xfade")
        return

    # HARD CUT Logic (Default)
    list_path = os.path.join(workdir, "_concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in segment_paths:
            f.write(f"file '{p}'\n")

    try:
        run_ffmpeg(
            ["-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path],
            "penggabungan segmen",
        )
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)


def escape_drawtext(text):
    """Escape karakter yang bermasalah untuk filter drawtext ffmpeg."""
    return (
        text.replace("\\", "\\\\\\\\")
        .replace(":", "\\:")
        .replace("'", "’")  # ganti kutip lurus dengan kutip tipografi, hindari escaping rumit
        .replace("%", "\\%")
    )


# Perkiraan lebar rata-rata karakter DejaVuSans-Bold relatif terhadap fontsize.
# Dipakai untuk membungkus baris: drawtext TIDAK melakukan wrapping sendiri, jadi
# kalimat panjang akan melebar keluar kanvas dan terpotong di kedua sisi.
# Diukur dari hasil render nyata, bukan ditebak: baris 34 karakter pada
# fontsize 52 mengisi penuh 1080 px -> 1080/34/52 = 0,61. Dipakai 0,62 + lebar
# aman 88% supaya ada margin untuk huruf lebar (M, W) dan tepi tidak tersentuh.
CHAR_WIDTH_RATIO = 0.62

# --- Gaya subtitle ---------------------------------------------------------
# Ukuran font adalah FRAKSI tinggi kanvas, bukan piksel tetap. Dengan begitu ia
# otomatis benar untuk 9:16, 1:1, dan 16:9 tanpa disetel ulang. Nilai lama 52 px
# pada tinggi 1920 = 2,7% -- terlalu kecil untuk ditonton di HP; patokan caption
# sosial media 4-5%.
SUBTITLE_SIZE_RATIO = float(os.getenv("SUBTITLE_SIZE_RATIO", "0.045"))
SUBTITLE_MAX_LINES = int(os.getenv("SUBTITLE_MAX_LINES", "2"))
# Jarak aman dari tepi bawah (zona UI Reels/TikTok/Shorts) sebagai fraksi tinggi.
SAFE_BOTTOM_RATIO = float(os.getenv("SAFE_BOTTOM_RATIO", "0.156"))

SUBTITLE_STYLES = {
    # --- Karaoke: SELURUH frasa tampil diam di tempat, hanya kata yang sedang
    # diucapkan yang berganti warna (kunci "highlight"). Ini gaya bawaan.
    "karaoke":         {"color": "white", "highlight": "0xFFD400",
                        "box": "black@0.55", "pad": 24},
    "karaoke-tebal":   {"color": "white", "highlight": "0xFFD400", "border": 6},
    "karaoke-kapital": {"color": "white", "highlight": "0xFFD400",
                        "box": "black@0.55", "pad": 24, "upper": True},
    # --- CapCut Style: Tebal, uppercase, shadow kuat, warna mencolok
    "capcut": {"color": "white", "highlight": "0x00FF99", "border": 8, "shadow": 7, "upper": True},
    # --- Gaya lama: teks KUMULATIF (kata muncul satu per satu, seluruh teks
    # di-center ulang tiap kata baru sehingga kata sebelumnya bergeser ke kiri).
    "putih-kotak":  {"color": "white",  "box": "black@0.55", "pad": 24},
    "kuning-kotak": {"color": "yellow", "box": "black@0.55", "pad": 24},
    "putih-tebal":  {"color": "white",  "border": 5},
    "kuning":       {"color": "yellow", "border": 4},  # gaya lama
}
# Satu kata per tampilan, besar & kapital, berganti TEPAT mengikuti suara (meniru video
# referensi user 24 Sep: kata berganti tiap ~0,38 dtk tanpa efek; "halus"-nya dari sinkron).
SUBTITLE_STYLES["kata"] = {"color": "white", "border": 3, "shadow": 6, "upper": True,
                           "per_kata": True, "font": "Montserrat-ExtraBold.ttf",
                           "ukuran": 0.062, "y_rel": 0.66}
SUBTITLE_STYLE = os.getenv("SUBTITLE_STYLE", "karaoke")
# Gaya teks NARASI voice-over AI (bukan ucapan asli): bawaan "kata" kecuali user memilih gaya.
NARASI_STYLE = os.getenv("SUBTITLE_STYLE") or "kata"
DEFAULT_SUBTITLE_STYLE = "karaoke"


def subtitle_style():
    """Gaya terpilih. Nama tak dikenal -> peringatan + default, bukan diam-diam."""
    gaya = SUBTITLE_STYLES.get(SUBTITLE_STYLE)
    if gaya is None:
        print(f"[warn] SUBTITLE_STYLE tidak dikenal ({SUBTITLE_STYLE!r}), memakai "
              f"'{DEFAULT_SUBTITLE_STYLE}'. Pilihan: {', '.join(sorted(SUBTITLE_STYLES))}.")
        return SUBTITLE_STYLES[DEFAULT_SUBTITLE_STYLE]
    return gaya


def subtitle_geometry(video_width, video_height):
    """(fontsize, y) untuk subtitle dua baris di sepertiga bawah, di atas zona aman."""
    fs = max(16, round(video_height * SUBTITLE_SIZE_RATIO))
    tinggi_blok = SUBTITLE_MAX_LINES * fs * 1.25
    y = round(video_height - video_height * SAFE_BOTTOM_RATIO - tinggi_blok)
    return fs, max(0, y)
# SATU sumber kebenaran untuk batas baris. Dulu ada dua: MAX_SUBTITLE_LINES=3
# (dipakai split_for_subtitle saat mengemas teks) dan SUBTITLE_MAX_LINES=2
# (dipakai saat menggambar). Akibatnya potongan dikemas untuk 3 baris lalu
# dipotong jadi 2 saat digambar -- terukur 4 kata ucapan user hilang diam-diam,
# diganti "...", pada satu kalimat transkrip biasa.
MAX_SUBTITLE_LINES = SUBTITLE_MAX_LINES


def wrap_text(teks, fontsize, video_width, *, max_lines=MAX_SUBTITLE_LINES):
    """Bungkus teks jadi beberapa baris agar muat di kanvas.

    Wajib untuk subtitle dari transkrip: potongan Whisper adalah kalimat utuh
    (100+ karakter), sedangkan scene tulisan LLM dibatasi 6 kata. Tanpa ini,
    kalimat panjang melebar keluar layar dan kedua ujungnya terpotong.
    """
    lebar_aman = video_width * 0.88
    maks = max(8, int(lebar_aman / (fontsize * CHAR_WIDTH_RATIO)))

    baris, sekarang = [], ""
    for kata in teks.split():
        calon = f"{sekarang} {kata}".strip()
        if len(calon) <= maks:
            sekarang = calon
            continue
        if sekarang:
            baris.append(sekarang)
        sekarang = kata
        if len(baris) == max_lines:
            break
    if sekarang and len(baris) < max_lines:
        baris.append(sekarang)

    # Tandai kalau ada yang dibuang, supaya tidak tampak seperti kalimat selesai.
    dipakai = " ".join(baris)
    if len(dipakai.split()) < len(teks.split()):
        baris[-1] = baris[-1] + "..."
    return "\n".join(baris)


ASSETS_FONTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "assets", "fonts")
ZONA_ATAS_RATIO = 0.16   # ~300px di kanvas 1920, sama dengan zona aman atas platform


def text_font_path():
    """Path font untuk teks on-screen, dari TEXT_FONT. Berkas bundel yang hilang
    MENGGAGALKAN render (bukan diam-diam ganti font): user memilih font itu."""
    nama, spec = resolve_text_font()
    if spec.get("file"):
        p = os.path.join(ASSETS_FONTS, spec["file"])
        if not os.path.exists(p):
            raise FileNotFoundError(f"Font '{nama}' dipilih tapi berkasnya tidak ada: {p}")
        return p
    if spec.get("family"):
        return resolve_font(spec["family"], "bold")
    return FONT_PATH


def text_y(video_height, y_bawah):
    """Ekspresi y drawtext untuk TEXT_POSITION. `bawah` = perilaku lama (y_bawah)."""
    posisi = resolve_text_position()
    if posisi == "tengah":
        return "(h-text_h)/2"
    if posisi == "atas":
        return str(round(video_height * ZONA_ATAS_RATIO))
    return y_bawah


def _muat_lebar(teks, fs, video_width, font_path, *, batas=0.9):
    """Ukuran font terbesar <= fs yang membuat SETIAP baris hasil wrap_text muat di
    `batas` x lebar kanvas, DIUKUR dengan berkas font yang dipakai. CHAR_WIDTH_RATIO
    hanyalah perkiraan satu font; font lebar (Montserrat ExtraBold, Pacifico) melewati
    tepi dan terpotong -- terukur di render nyata saat judul tengah dibesarkan."""
    lebar_maks = video_width * batas
    f, minimum = fs, max(16, int(fs * 0.5))
    kata = teks.split()
    while f > minimum:
        terbungkus = wrap_text(teks, f, video_width, max_lines=SUBTITLE_MAX_LINES)
        baris = terbungkus.split("\n")
        # SEMUA kata harus selamat: wrap_text memotong yang tidak muat menjadi "..." --
        # versi awal fungsi ini hanya memeriksa lebar baris, sehingga "Aksi Merah Laksamana
        # Muda 🩸" lolos sebagai "...Laksamana Muda..." (terlihat di video nyata).
        if terbungkus.split() == kata and all(
                text_width(font_path, f, b) <= lebar_maks for b in baris):
            return f
        f -= 4
    return max(minimum, f)


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\U0001F1E6-\U0001F1FF]+")
EMOJI_DIHAPUS = []


def hapus_emoji(teks):
    """Font teks (semua yang ada) tidak punya glyph emoji, dan drawtext tidak bisa jatuh ke
    font lain per karakter: emoji tampil kosong/kotak. Dihapus, DICATAT (EMOJI_DIHAPUS) dan
    dilaporkan ke user -- bukan hilang diam-diam."""
    ditemukan = _EMOJI.findall(teks or "")
    if not ditemukan:
        return teks
    EMOJI_DIHAPUS.extend(ditemukan)
    return re.sub(r"\s{2,}", " ", _EMOJI.sub("", teks)).strip()


def _drawtext(teks, fs, y, gaya, enable, alpha=None, font_path=None):
    bagian = [
        "drawtext=",
        f"fontfile={font_path or FONT_PATH}:text='{teks}':fontsize={fs}:",
        f"fontcolor={gaya['color']}:line_spacing=8:x=(w-text_w)/2:y={y}",
    ]
    if gaya.get("box"):
        bagian.append(f":box=1:boxcolor={gaya['box']}:boxborderw={gaya.get('pad', 24)}")
    else:
        border = gaya.get('border', 4)
        shadow = gaya.get('shadow', 0)
        sh_str = f":shadowcolor=black@0.8:shadowx={shadow}:shadowy={shadow}" if shadow else ""
        bagian.append(f":bordercolor=black:borderw={border}{sh_str}")
    if alpha:
        bagian.append(f":alpha='{alpha}'")
    bagian.append(f":enable='{enable}'")
    return "".join(bagian)


_WARNA_NAMA = {"white": 0xFFFFFF, "yellow": 0xFFFF00, "black": 0x000000}


def warna_int(nilai):
    """'white' / '0xRRGGBB' / '#RRGGBB' / int -> int 0xRRGGBB."""
    if isinstance(nilai, int):
        return nilai
    s = str(nilai).strip().lower()
    if s in _WARNA_NAMA:
        return _WARNA_NAMA[s]
    return int(s.lstrip("#").replace("0x", ""), 16)


def _filter_kata(kata, x, dasar, fs, gaya, aktif, grup):
    """Satu drawtext untuk SATU kata, dengan warna yang berganti menurut waktu.

    `y=<garis_dasar>-ascent`: y pada drawtext adalah puncak tinta string itu, jadi
    tanpa ini kata tanpa huruf tinggi berdiri lebih rendah daripada tetangganya.

    Warna lewat fontcolor_expr + eif (heksadesimal 6 digit): satu filter per kata
    cukup, tanpa menggambar kata yang sama dua kali. Pembanding gte*lt (bukan
    between) supaya di batas antar-kata hanya satu kata yang menyala.
    """
    a, b = aktif
    g0, g1 = grup
    nyala, biasa = warna_int(gaya["highlight"]), warna_int(gaya["color"])
    warna = ("0x%{eif\\:if(gte(t\\," + f"{a:.3f}" + ")*lt(t\\," + f"{b:.3f}" + ")\\,"
             + f"{nyala}\\,{biasa})" + "\\:x\\:6}")
    bagian = [
        f"drawtext=fontfile={FONT_PATH}:text='{escape_drawtext(kata)}':fontsize={fs}",
        f":x={x}:y={dasar}-ascent:fontcolor_expr='{warna}'",
    ]
    if not gaya.get("box"):
        border = gaya.get('border', 5)
        shadow = gaya.get('shadow', 3)
        sh_str = f":shadowcolor=black@0.8:shadowx={shadow}:shadowy={shadow}" if shadow else ""
        bagian.append(f":bordercolor=black:borderw={border}{sh_str}")
    bagian.append(f":enable='between(t,{g0:.3f},{g1:.3f})'")
    return "".join(bagian)


def filter_karaoke(kata, jendela, W, fs, y_top, gaya):
    """Filter untuk satu tampilan subtitle bergaya karaoke.

    `kata`: [{"word","start","end"}] dengan waktu ABSOLUT di video hasil.
    `jendela`: (mulai, selesai) tampilan. Kalau kata-katanya tidak muat di
    SUBTITLE_MAX_LINES baris, tampilan dipecah dua di tengah dan tiap separuh
    mendapat jendelanya sendiri -- ucapan tidak pernah dipangkas.
    """
    g0, g1 = jendela
    if gaya.get("upper"):
        kata = [{**w, "word": w["word"].upper()} for w in kata]
    tata = layout_group(
        kata, font_path=FONT_PATH, fs=fs, canvas_w=W, max_lines=SUBTITLE_MAX_LINES,
        y_top=y_top, pad_x=gaya.get("pad", 24), pad_y=round(gaya.get("pad", 24) * 0.75))
    if tata is None:
        if len(kata) < 2:      # satu kata tak bisa dipecah lagi; layout_group
            return []          # sendiri tidak menolaknya, jadi ini hanya pengaman
        t = len(kata) // 2
        batas = float(kata[t].get("start") or g0)
        return (filter_karaoke(kata[:t], (g0, batas), W, fs, y_top, gaya)
                + filter_karaoke(kata[t:], (batas, g1), W, fs, y_top, gaya))

    filters = []
    if gaya.get("box"):
        x, yb, w, h = tata["box"]
        filters.append(f"drawbox=x={x}:y={yb}:w={w}:h={h}:color={gaya['box']}:t=fill"
                       f":enable='between(t,{g0:.3f},{g1:.3f})'")
    urut = [(ln["baseline"], w) for ln in tata["lines"] for w in ln["words"]]
    for i, (dasar, w) in enumerate(urut):
        mulai = float(w["start"] if w["start"] is not None else g0)
        nxt = urut[i + 1][1]["start"] if i + 1 < len(urut) else None
        selesai = float(nxt) if nxt is not None else float(g1)
        if selesai <= mulai:
            selesai = mulai + 0.05
        filters.append(_filter_kata(w["word"], w["x"], dasar, fs, gaya,
                                    (mulai, selesai), (g0, g1)))
    return filters


def filter_per_kata(kata, akhir, W, H, gaya):
    """Satu drawtext per kata, aktif dari mulainya sampai kata berikutnya dimulai."""
    fs = max(16, round(H * gaya.get("ukuran", 0.06)))
    font = os.path.join(ASSETS_FONTS, gaya["font"]) if gaya.get("font") else FONT_PATH
    if not os.path.exists(font):
        font = FONT_PATH
    y = round(H * gaya.get("y_rel", 0.66))
    hasil = []
    for i, w in enumerate(kata):
        teks = hapus_emoji(w["word"])
        if not teks:
            continue
        teks = teks.upper() if gaya.get("upper") else teks
        dari = float(w["start"])
        sampai = float(kata[i + 1]["start"]) if i + 1 < len(kata) else akhir
        if sampai <= dari:
            continue
        f = fs
        while f > 16 and text_width(font, f, teks) > W * 0.9:
            f -= 4
        hasil.append(_drawtext(escape_drawtext(teks), f, f"{y}-text_h/2", gaya,
                               f"between(t,{dari:.3f},{sampai:.3f})", font_path=font))
    return hasil


def build_drawtext_chain(scenes, video_height, video_width=None):
    """Filter drawtext per scene, dengan animasi.

    Dua mode:
    - Scene punya `words` (dari timestamp per-kata Whisper): teks muncul KATA DEMI
      KATA persis saat diucapkan. Tiap keadaan adalah satu drawtext berisi kata
      yang sudah terucap, aktif dari kata itu muncul sampai kata berikutnya.
    - Tanpa `words` (naskah tulisan LLM): satu drawtext per scene dengan fade
      masuk singkat, supaya tidak muncul mendadak.
    """
    W = video_width or TARGET_W
    fs, y = subtitle_geometry(W, video_height)
    gaya = subtitle_style()
    y_teks = text_y(video_height, y)       # hanya untuk teks on-screen, bukan subtitle ucapan
    font_teks = text_font_path()

    filters = []
    for sc in scenes:
        text = sc.get("text")
        start, end = sc.get("start", 0), sc.get("end")
        if not text or end is None or end <= start:
            continue

        kata = sc.get("words") or []
        if not kata:
            text = hapus_emoji(text)      # teks tulisan saja; subtitle ucapan tetap apa adanya
            if not text:
                continue
        gaya_sc = SUBTITLE_STYLES.get(sc.get("gaya")) or gaya
        if kata and gaya_sc.get("per_kata"):
            filters.extend(filter_per_kata(kata, float(end), W, video_height, gaya_sc))
            continue
        if kata and gaya.get("highlight"):
            filters.extend(filter_karaoke(kata, (float(start), float(end)), W, fs, y, gaya))
            continue
        if kata:
            for i, w in enumerate(kata):
                terucap = " ".join(k["word"] for k in kata[: i + 1])
                aktif_dari = float(w.get("start", start))
                aktif_sampai = float(kata[i + 1]["start"]) if i + 1 < len(kata) else float(end)
                if aktif_sampai <= aktif_dari:
                    continue
                filters.append(_drawtext(
                    escape_drawtext(wrap_text(terucap, fs, W, max_lines=SUBTITLE_MAX_LINES)),
                    fs, y, gaya, f"between(t,{aktif_dari},{aktif_sampai})",
                ))
        else:
            # Teks TANPA timestamp kata (teks on-screen tulisan LLM). Dipecah jadi
            # beberapa tampilan yang masing-masing muat, waktunya dibagi menurut
            # jumlah kata -- BUKAN dipangkas dengan "...". Versi lama memakai
            # wrap_text langsung, sehingga "Ratusan orang berkumpul, diskusi aktif"
            # tampil sebagai "Ratusan orang berkumpul,..." (terlihat di video nyata).
            fs_tampil = fs
            if sc.get("statis"):
                # Teks STATIS harus tetap SATU tampilan sepanjang video: font dikecilkan
                # sampai muat, bukan dipecah jadi beberapa tampilan yang berganti.
                fs_tampil = max(16, int(fs * 0.5))
                # Judul di tengah/atas dibesarkan (bukan ukuran subtitle bawah), lalu
                # tetap mengecil otomatis sampai muat.
                fs_maks = fs if resolve_text_position() == "bawah" else int(fs * 1.4)
                for f2 in range(fs_maks, int(fs * 0.5) - 1, -4):
                    if len(split_for_subtitle(text, f2, W, max_lines=SUBTITLE_MAX_LINES)) <= 1:
                        fs_tampil = f2
                        break
            bagian = split_for_subtitle(text, fs_tampil, W, max_lines=SUBTITLE_MAX_LINES)
            total_kata = sum(len(b.split()) for b in bagian) or 1
            jalan, rentang = float(start), float(end) - float(start)
            for b in bagian:
                fs_b = _muat_lebar(b, fs_tampil, W, font_teks)
                porsi = rentang * (len(b.split()) / total_kata)
                # Fade masuk 0,25 detik; dijepit ke 1 supaya tetap penuh setelahnya.
                alpha = f"min(1,(t-{jalan:.3f})/0.25)"
                filters.append(_drawtext(
                    escape_drawtext(wrap_text(b, fs_b, W, max_lines=SUBTITLE_MAX_LINES)),
                    fs_b, y_teks, gaya, f"between(t,{jalan:.3f},{jalan + porsi:.3f})",
                    alpha=alpha, font_path=font_teks,
                ))
                jalan += porsi
    return filters


TEKS_ANIMASI = {}


def apply_text_overlay(input_path, scenes, output_path):
    """Teks TULISAN (scene tanpa `words`) -> lapisan animasi Remotion bila TEXT_ANIMATION aktif;
    subtitle UCAPAN (scene ber-`words`) tetap drawtext karaoke. Remotion gagal -> SEMUA teks
    jatuh ke drawtext statis dan kegagalannya dicatat di TEKS_ANIMASI (dilaporkan ke user)."""
    EMOJI_DIHAPUS.clear()
    TEKS_ANIMASI.clear()
    animasi = _ovr.animasi_diminta()
    tulisan = [s for s in scenes if s.get("text") and not s.get("words")
               and s.get("end") is not None and float(s["end"]) > float(s.get("start", 0))]
    if animasi and tulisan:
        ucapan = [s for s in scenes if s.get("words")]
        tengah = input_path
        if ucapan:
            tengah = os.path.splitext(output_path)[0] + "_subtitle.mp4"
            _drawtext_saja(input_path, ucapan, tengah, pindahkan=False)
        try:
            info = _ovr.tempel_teks_animasi(
                tengah, [{"text": s["text"], "mulai": float(s.get("start", 0)),
                          "selesai": float(s["end"])} for s in tulisan],
                output_path, lebar=TARGET_W, tinggi=TARGET_H, fps=FPS,
                durasi=max(float(s["end"]) for s in tulisan),
                posisi=resolve_text_position(), font=resolve_text_font()[0], animasi=animasi)
            TEKS_ANIMASI.update(dipakai=True, gagal=None, **info)
            print(f"✨ Teks animasi ({animasi}): {info['item']} teks, {info['frame_chromium']} frame dirender Chromium.")
            return
        except _ovr.OverlayError as e:
            TEKS_ANIMASI.update(animasi=animasi, dipakai=False, gagal=str(e))
            print(f"[warn] animasi teks gagal ({e}) — memakai teks statis.")
        finally:
            if tengah != input_path and os.path.exists(tengah):
                os.remove(tengah)
    _drawtext_saja(input_path, scenes, output_path)


def _drawtext_saja(input_path, scenes, output_path, pindahkan=True):
    filters = build_drawtext_chain(scenes, TARGET_H, TARGET_W)
    if not filters:
        # pindahkan=False: masukan masih dibutuhkan (cadangan teks statis bila Remotion gagal).
        (os.replace if pindahkan else shutil.copyfile)(input_path, output_path)
        return
    # Rantai filter lewat BERKAS, bukan argumen -vf: satu argumen di Linux dibatasi
    # 128 KB (MAX_ARG_STRLEN). Video 68 detik dengan ~200 kata memakai puluhan
    # KB pada gaya lama, dan gaya karaoke menambah filter per kata + kotak.
    skrip = os.path.join(os.path.dirname(os.path.abspath(output_path)), "_filter_teks.txt")
    with open(skrip, "w", encoding="utf-8") as f:
        f.write(",".join(filters))
    try:
        run_ffmpeg(
            ["-i", input_path, "-filter_script:v", skrip, "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-preset", "fast", output_path],
            "overlay teks",
        )
    finally:
        if os.path.exists(skrip):
            os.remove(skrip)


def mux_audio(video_path, audio_path, output_path):
    run_ffmpeg(
        [
            "-i", video_path, "-i", audio_path,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            output_path,
        ],
        "penggabungan audio",
    )


def media_duration(path):
    """Durasi file media dalam detik, atau 0.0 kalau tidak terbaca."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip()) if out.returncode == 0 else 0.0
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return 0.0


def split_for_subtitle(teks, fontsize, video_width, *, max_lines=MAX_SUBTITLE_LINES):
    """Pecah kalimat panjang jadi beberapa tampilan subtitle.

    Membungkus baris saja tidak cukup: potongan Whisper bisa jauh lebih panjang
    dari yang muat di layar, dan memotongnya berarti membuang ucapan user. Di
    sini kalimat dipecah jadi beberapa bagian yang masing-masing muat, lalu
    pemanggil membagi durasinya secara proporsional.
    """
    lebar_aman = video_width * 0.88
    per_baris = max(8, int(lebar_aman / (fontsize * CHAR_WIDTH_RATIO)))
    # 90% dari anggaran penuh: pemecahan dan pembungkusan memakai perkiraan yang
    # sama, jadi tanpa margin ini batas kata bisa mendorong potongan ke baris
    # keempat dan wrap_text terpaksa memotongnya dengan "..." -- membuang ucapan
    # user padahal seluruhnya sebenarnya muat.
    per_tampilan = int(per_baris * max_lines * 0.9)

    bagian, sekarang = [], ""
    for kata in teks.split():
        calon = f"{sekarang} {kata}".strip()
        if len(calon) <= per_tampilan or not sekarang:
            sekarang = calon
        else:
            bagian.append(sekarang)
            sekarang = kata
    if sekarang:
        bagian.append(sekarang)
    return bagian or [teks]


def chunk_words(words, fs, video_width, *, max_lines=None):
    """Kelompokkan kata jadi tampilan subtitle yang muat di layar.

    Lebih akurat daripada membagi durasi kalimat secara proporsional: waktu tiap
    tampilan diambil dari kata pertama dan terakhirnya sendiri.
    """
    baris_maks = max_lines or SUBTITLE_MAX_LINES
    per_baris = max(8, int(video_width * 0.88 / (fs * CHAR_WIDTH_RATIO)))
    per_tampilan = int(per_baris * baris_maks * 0.9)

    kelompok, sekarang = [], []
    for w in words:
        calon = " ".join(x["word"] for x in sekarang + [w])
        if sekarang and len(calon) > per_tampilan:
            kelompok.append(sekarang)
            sekarang = [w]
        else:
            sekarang.append(w)
    if sekarang:
        kelompok.append(sekarang)
    return kelompok


def _di_dalam(t0, t1, ranges):
    """Kata [t0, t1] ikut terdengar di salah satu rentang yang dipertahankan?

    Berdasarkan TUMPANG-TINDIH, bukan titik tengah. Alasannya terukur pada klip
    nyata: Whisper melaporkan kata pertama setiap klip mulai di 0,00 padahal
    suaranya baru mulai ~0,66 (silencedetect: hening 0,28-0,66), jadi "Berapa"
    tercatat 0,00-0,96 dengan titik tengah 0,48 -- di LUAR rentang yang dimulai
    0,54, padahal audionya ada. Penyaring titik-tengah membuang kata pertama
    hampir setiap klip dari subtitle. Ujung kata (end) jauh lebih bisa dipercaya
    daripada awalnya, dan tumpang-tindih memanfaatkan itu.

    Ambang: minimal min(0,10 dtk, separuh durasi kata), supaya kata yang sangat
    pendek pun tidak dibuang hanya karena durasinya kecil.
    """
    t0, t1 = float(t0), float(t1)
    perlu = min(0.10, 0.5 * max(t1 - t0, 0.0))
    return any(min(b, t1) - max(a, t0) >= perlu for a, b in ranges)


def subtitle_scenes(data, rencana, video_width=None, video_height=None):
    """Ubah transkrip jadi scene subtitle dengan waktu ABSOLUT di video hasil.

    `rencana` adalah daftar {"path", "ranges", "durasi"} per klip: `ranges` adalah
    bagian yang DIPERTAHANKAN setelah jeda dipotong, `durasi` panjangnya setelah
    dipotong.

    Dua penggeseran harus dilakukan bersama, dan keduanya wajib:
    1. `map_time` — waktu Whisper mengacu ke audio ASLI. Setiap jeda yang dibuang
       memajukan semua ucapan sesudahnya.
    2. offset klip — klip ke-i mulai setelah jumlah durasi klip sebelumnya.

    Melewatkan salah satunya membuat subtitle melenceng makin jauh ke belakang.
    """
    W = video_width or TARGET_W
    H = video_height or TARGET_H
    fs, _ = subtitle_geometry(W, H)

    per_kata = data.get("transcript_words") or {}
    per_segmen = data.get("transcript_segments") or {}
    if not per_kata and not per_segmen:
        return []

    hasil = []
    offset = 0.0
    for item in rencana:
        nama = os.path.basename(item["path"])
        ranges = item["ranges"]
        batas = item["durasi"]

        def geser(t):
            return offset + min(map_time(float(t or 0), ranges), batas)

        # HANYA kata yang jatuh di bagian yang dipertahankan. Tanpa penyaringan ini,
        # seleksi konten membocorkan teks kalimat yang DIBUANG: kelompok kata
        # dibentuk dari seluruh ucapan klip, lalu waktunya dipetakan ke potongan
        # yang dipertahankan, sehingga teks yang tidak diucapkan ikut tampil.
        kata = [w for w in (per_kata.get(nama) or [])
                if _di_dalam(w.get("start", 0), w.get("end", 0), ranges)]
        if kata:
            for kelompok in chunk_words(kata, fs, W):
                mulai_w = geser(kelompok[0].get("start", 0))
                selesai_w = geser(kelompok[-1].get("end", 0))
                if selesai_w <= mulai_w:
                    continue
                hasil.append({
                    "start": round(mulai_w, 2),
                    "end": round(selesai_w, 2),
                    "text": " ".join(k["word"] for k in kelompok),
                    "words": [
                        {"word": k["word"],
                         "start": round(geser(k.get("start", 0)), 2),
                         "end": round(geser(k.get("end", 0)), 2)}
                        for k in kelompok
                    ],
                })
        else:
            # TIDAK disaring per-rentang seperti jalur kata, dan sengaja: segmen di
            # area yang dibuang sudah runtuh jadi durasi nol lewat map_time() lalu
            # dilewati di bawah, sedangkan penyaring titik-tengah akan salah
            # membuang segmen panjang yang hanya SEBAGIAN masuk rentang (segmen
            # [0, 99] pada klip 3 detik titik tengahnya di luar klip).
            for seg in per_segmen.get(nama, []):
                teks = (seg.get("text") or "").strip()
                m, sel = geser(seg.get("start", 0)), geser(seg.get("end", 0))
                if not teks or sel <= m:
                    continue
                bagian = split_for_subtitle(teks, fs, W)
                total_kata = sum(len(b.split()) for b in bagian) or 1
                jalan, rentang = m, sel - m
                for b in bagian:
                    porsi = rentang * (len(b.split()) / total_kata)
                    hasil.append({"start": round(jalan, 2),
                                  "end": round(jalan + porsi, 2), "text": b})
                    jalan += porsi
        offset += batas

    if hasil:
        berkata = sum(1 for h in hasil if h.get("words"))
        print(f"📝 {len(hasil)} subtitle dari ucapan asli"
              + (f" ({berkata} dengan animasi per-kata)" if berkata else ""))
    return hasil



# None = pakai LLM_MODEL. Dipilih SAAT DIPANGGIL, bukan saat import: .env baru
# terbaca ketika `common` diimpor, dan urutan impor tidak boleh menentukan modelnya.
DURATION_FIX_MODEL = os.getenv("DURATION_FIX_MODEL") or None
DURATION_FIX_TIMEOUT = float(os.getenv("DURATION_FIX_TIMEOUT_SECONDS", "25"))


def bagi_durasi(assets, total_duration):
    """(aset_dipakai, durasi_per_klip). ATURAN TETAP: durasi video = durasi audio.

    `per_clip = total / n` apa adanya. Kalau hasilnya di bawah MIN_CLIP_DURATION,
    yang dikurangi adalah JUMLAH ASET, bukan per_clip-nya.

    Versi lama memakai `max(MIN_CLIP_DURATION, total/n)`, yang membuat video
    MELEBIHI audio saat asetnya banyak; `-shortest` lalu memotongnya, jadi aset
    terakhir hilang di tengah. `min(...)` juga salah dengan cara sebaliknya:
    audio 30 detik dengan 3 aset akan jadi video 4,5 detik.
    """
    n = len(assets)
    if not n or total_duration <= 0:
        return assets, [0.0] * n
    if total_duration / n < MIN_CLIP_DURATION:
        muat = max(1, int(total_duration // MIN_CLIP_DURATION))
        if muat < n:
            print(f"⚠️ {n} bahan terlalu banyak untuk {total_duration:.1f} detik "
                  f"(min {MIN_CLIP_DURATION} dtk/klip) — dipakai {muat} bahan pertama.")
            assets = assets[:muat]
    per = total_duration / len(assets)
    return assets, [per] * len(assets)


def clamp_scenes(scenes, total_duration):
    """Jepit scene ke durasi video yang sebenarnya.

    Scene dengan end=30 di video 22 detik hanya berhenti saat videonya habis,
    dan scene yang MULAI setelah akhir tidak pernah tampil sama sekali — dua-duanya
    membuat timing teks terlihat benar di JSON tapi salah di layar.
    """
    if not total_duration or total_duration <= 0:
        return scenes
    hasil = []
    for sc in scenes or []:
        mulai = float(sc.get("start") or 0)
        selesai = sc.get("end")
        if mulai >= total_duration:
            continue
        selesai = total_duration if selesai is None else min(float(selesai), total_duration)
        if selesai <= mulai:
            continue
        hasil.append({**sc, "start": mulai, "end": selesai})
    return hasil


def perbaiki_durasi(data, aktual, target):
    """Satu kali penulisan ulang naskah supaya durasinya mendekati target.

    Mengembalikan dict berisi full_voice_over / voice_over_spoken / scenes yang
    baru, atau None kalau gagal. Naskah DAN scenes ditulis ulang bersama: kalau
    hanya naskahnya yang dipendekkan, timing scene ikut salah.

    Satu percobaan dengan timeout pendek, dan kegagalannya TIDAK menggagalkan
    render — lebih baik video dengan durasi meleset daripada tidak ada video.
    """
    kmin, kmax = word_target(target)
    arah = "PENDEKKAN" if aktual > target else "PANJANGKAN"
    kata_sekarang = len((data.get("full_voice_over") or "").split())
    pesan = (
        f"Naskah voice-over ini {kata_sekarang} kata dan menghasilkan audio "
        f"{aktual:.1f} detik, padahal target {target} detik. {arah} naskahnya "
        f"menjadi {kmin}-{kmax} kata.\n"
        f"WAJIB: jumlah kata \"full_voice_over\" harus berada di dalam rentang "
        f"{kmin}-{kmax}. Jangan kurang dari {kmin} kata dan jangan lebih dari "
        f"{kmax} kata — hitung sebelum menjawab. Pertahankan makna, gaya, dan "
        f"struktur Hook-Masalah-Solusi-CTA.\n\n"
        f"Naskah sekarang:\n{data.get('full_voice_over', '')}\n\n"
        f"Scenes sekarang:\n{json.dumps(data.get('scenes', []), ensure_ascii=False)}\n\n"
        "Balas HANYA JSON: {\"full_voice_over\": \"...\", "
        "\"voice_over_spoken\": \"versi fonetis untuk TTS\", "
        "\"scenes\": [{\"start\": 0, \"end\": 3, \"text\": \"...\"}]}. "
        "Waktu scene harus muat di dalam target durasi."
    )
    # Aturan lafal ikut dibawa: tanpa ini naskah hasil koreksi kehilangan ejaan
    # fonetisnya dan TTS kembali membaca "leads" jadi "lid".
    aturan = prompt_rule()
    if aturan:
        pesan = f"{pesan}\n\n{aturan}"
    try:
        from common import LLM_MODEL, chat_json
        return chat_json(
            [{"role": "user", "content": pesan}],
            model=DURATION_FIX_MODEL or LLM_MODEL,
            label="koreksi durasi naskah",
            max_attempts=1,
            timeout=DURATION_FIX_TIMEOUT,
        )
    except Exception as e:
        print(f"[warn] koreksi durasi gagal ({e}); lanjut dengan naskah asli.")
        return None



def tambah_musik(video_path, track, out_path, durasi, ada_ucapan=True):
    """Campur musik latar ke audio video, dengan ducking otomatis.

    Musik di-loop kalau lebih pendek dari video, dan `-shortest` memastikan
    hasilnya tidak ikut memanjang mengikuti musik.
    `-c:v copy`: videonya tidak disentuh sama sekali, jadi tahap ini tidak
    menambah kerugian kualitas dan waktunya hanya beberapa detik.
    """
    punya = has_audio_stream(video_path)
    # Volume diukur dari loudness video ini, bukan angka tetap: gain tetap
    # membuat musik tenggelam di rekaman keras dan terlalu maju di rekaman pelan.
    # Video TANPA audio (dibisukan): musik adalah satu-satunya suara, jadi dibawa ke
    # target kenyaringan solo -- bukan MUSIC_VOLUME 0,15 yang dirancang sebagai latar
    # dan akan nyaris tak terdengar.
    #
    # Ducking HANYA bila ada UCAPAN. Terukur 24 Sep pada video food court user: suara
    # keramaian yang terus-menerus memicu sidechain sepanjang video, sehingga nada uji
    # di trek musik hilang TOTAL (-22,7 dB = identik dengan kontrol tanpa musik); tanpa
    # ducking +34,4 dB. User: "saya tidak dapat mendengar audio musik yang diberikan".
    # Tanpa ucapan, musik juga diletakkan lebih dekat ke suasana (MUSIC_BELOW_AMBIENT_DB).
    if not punya:
        vol = solo_volume(track)
    else:
        vol = auto_volume(video_path, track,
                          below_db=None if ada_ucapan else MUSIC_BELOW_AMBIENT_DB)
    run_ffmpeg(
        ["-i", video_path, "-stream_loop", "-1", "-i", track,
         "-filter_complex", music_filter(durasi, punya_ucapan=punya and ada_ucapan, volume=vol),
         "-map", "0:v", "-map", "[aout]",
         "-c:v", "copy", "-c:a", "aac", "-ar", AUDIO_RATE, "-ac", AUDIO_CHANNELS,
         "-shortest", out_path],
        "musik latar",
    )
    return out_path


def render_from_agent_script(
    json_path="workspace/drafts/script.json",
    image_path="",
    output_video="workspace/drafts/video_output.mp4",
):
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"File {json_path} tidak ditemukan!")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    POTONG_VISUAL.clear()
    audio_mode = (data.get("audio_mode") or "ai").strip().lower()
    bisu = audio_mode == "mute"
    # `pakai_audio_asli` = jalur berbasis durasi klip + transkrip (seleksi, subtitle kata).
    # Mode mute memakai jalur yang SAMA, hanya audio klipnya dibuang.
    pakai_audio_asli = audio_mode in ("original", "mute")
    edit_dipakai = False

    full_vo = data.get("full_voice_over", "")
    if not full_vo and not pakai_audio_asli:
        raise ValueError(f"'full_voice_over' kosong di {json_path}, tidak ada narasi untuk di-render.")

    scenes = data.get("scenes", [])
    media_assets = data.get("media_assets", [])

    target_duration = data.get("target_duration")
    durasi_dikoreksi = False

    output_dir = os.path.dirname(output_video) or "."
    os.makedirs(output_dir, exist_ok=True)
    temp_audio = os.path.join(output_dir, "temp_vo.mp3")

    print(f"🎬 Judul Konten: {data.get('judul', 'Untitled')}")

    existing_assets = [p for p in media_assets if os.path.exists(p)]
    missing = [p for p in media_assets if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(f"Bahan mentah tidak ditemukan: {missing}")

    if not existing_assets:
        raise ValueError("Tidak ada bahan mentah (media_assets) untuk dirender.")

    broll_info, broll_tmp = None, []
    ada_ucapan = True          # voice-over AI = ucapan; cabang audio asli menghitungnya sendiri
    if pakai_audio_asli and _broll.aktif():
        # Diberi tahu, bukan diabaikan diam-diam: user memintanya.
        broll_info = {"dipakai": [], "gagal": "B-roll hanya untuk mode voice-over AI; mode audio asli/mute "
                      "menampilkan orang yang bicara sehingga klip sisipan menggeser subtitle."}
        print(f"[warn] {broll_info['gagal']}")

    if pakai_audio_asli:
        # Durasi ditentukan bahan, bukan TTS: tiap klip main sepanjang aslinya
        # supaya ucapan user tidak terpotong di tengah kalimat.
        print("🔇 Video DIBISUKAN (suara asli tidak dipakai)." if bisu
              else "🔊 Memakai AUDIO ASLI dari video user (tanpa voice-over AI).")
        plan_edit = data.get("edit_plan")
        rencana, dibuang, edit_dipakai = [], 0.0, False
        if plan_edit and plan_edit.get("status") == "applied":
            rencana = rencana_dari_plan(plan_edit, existing_assets)
            edit_dipakai = bool(rencana)
            if edit_dipakai:
                print(f"🎞️ Seleksi konten dipakai: {plan_edit.get('dipilih')} dari "
                      f"{plan_edit.get('kandidat')} potongan.")
            else:
                print("[warn] seleksi konten tidak menghasilkan potongan valid — "
                      "memakai semua bahan.")
        if not rencana:
            rencana, dibuang = rencana_semua_klip(existing_assets)
        rencana = terapkan_potong_visual(rencana, data)
        if POTONG_VISUAL.get("dipotong"):
            print(f"🎥 {len(POTONG_VISUAL['dipotong'])} bagian goyang/oleng dibuang: "
                  + ", ".join(f"{d['file']} {d['dari']}-{d['sampai']} dtk ({d['alasan']})"
                              for d in POTONG_VISUAL["dipotong"]))

        durasi_klip = [r["durasi"] for r in rencana]
        total_duration = sum(durasi_klip)
        if dibuang > 0.05:
            print(f"✂️ {dibuang:.1f} detik jeda dipotong dari {len(rencana)} bahan.")
        scenes = subtitle_scenes(data, rencana, TARGET_W, TARGET_H) or scenes
        # Ada ucapan = ada subtitle dari timestamp kata. Tanpa itu, audio asli hanyalah
        # suasana/keramaian dan TIDAK boleh menekan musik (lihat tambah_musik).
        ada_ucapan = any(s.get("words") for s in scenes)
        print(f"⏱️ Durasi total dari {len(existing_assets)} bahan: {total_duration:.1f} detik")
    else:
        # Yang DIBACAKAN adalah voice_over_spoken (ejaan fonetis), yang DITULIS
        # di subtitle tetap full_voice_over. Tertukar = subtitle salah eja.
        ucapan = spoken_text(data)
        if ucapan != full_vo:
            print("🗣️ Memakai naskah lafal (voice_over_spoken) untuk TTS.")
        print("🎙️ Menghasilkan Voice-Over AI secara dinamis...")
        asyncio.run(generate_voice(ucapan, temp_audio))
        total_duration = AudioFileClip(temp_audio).duration
        print(f"⏱️ Durasi Voice-Over: {total_duration:.1f} detik")

        # Koreksi HANYA kalau user benar-benar meminta durasi. Tanpa permintaan:
        # tidak ada pengecekan, tidak ada panggilan LLM tambahan, perilaku lama utuh.
        if off_target(total_duration, target_duration):
            print(f"⚠️ Durasi {total_duration:.1f} dtk meleset dari target "
                  f"{target_duration} dtk (toleransi {DURATION_TOLERANCE:.0%}) — "
                  "naskah ditulis ulang sekali.")
            revisi = perbaiki_durasi(data, total_duration, target_duration)
            if revisi and revisi.get("full_voice_over"):
                data = {**data, **{k: v for k, v in revisi.items() if v}}
                full_vo = data["full_voice_over"]
                scenes = data.get("scenes", scenes)
                asyncio.run(generate_voice(spoken_text(data), temp_audio))
                total_duration = AudioFileClip(temp_audio).duration
                durasi_dikoreksi = True
                print(f"⏱️ Durasi setelah koreksi: {total_duration:.1f} detik")

        # B-roll stok (Pexels) disisipkan SETELAH durasi narasi diketahui: jumlahnya dibatasi
        # supaya bagi_durasi tidak membuang bahan user (lihat broll.jatah).
        try:
            cfg_broll = _broll.resolve_broll()
        except BrollError as e:
            cfg_broll, broll_info = None, {"dipakai": [], "gagal": str(e)}
            print(f"[warn] {e}")
        if cfg_broll:
            kuota = _broll.jatah(cfg_broll["jumlah"], len(existing_assets), total_duration,
                                 MIN_CLIP_DURATION)
            if kuota <= 0:
                broll_info = {"dipakai": [], "gagal": "durasi terlalu pendek untuk menyisipkan "
                              "B-roll tanpa membuang bahanmu"}
                print(f"[warn] B-roll dilewati: {broll_info['gagal']}")
            else:
                klip, gagal = _broll.ambil(
                    cfg_broll["queries"] or [data.get("judul") or "b-roll"], kuota,
                    _broll.orientasi_untuk(TARGET_W, TARGET_H), output_dir,
                    os.getenv("CONTENT_FACTORY_RUN_ID") or "")
                broll_tmp = [k["path"] for k in klip]
                broll_info = {"dipakai": [{"id": k["id"], "kredit": k["kredit"],
                                           "halaman": k["halaman"]} for k in klip],
                              "gagal": gagal}
                if klip:
                    existing_assets = _broll.susun_urutan(existing_assets, broll_tmp)
                    print(f"🎞️ Menyisipkan {len(klip)} klip B-roll stok (Pexels).")
                else:
                    print(f"[warn] B-roll tidak tersedia: {gagal}")

        existing_assets, durasi_klip = bagi_durasi(existing_assets, total_duration)
        per = durasi_klip[0] if durasi_klip else 0.0
        print(f"🖼️ Menyusun {len(existing_assets)} bahan mentah user ({per:.1f} detik per bahan)")

    # Teks statis: SATU teks sepanjang video, hanya untuk teks tulisan (tanpa timestamp
    # kata). Bahan berucapan tetap memakai subtitle -- teks statis bukan penggantinya.
    if data.get("static_text") and not any(s.get("words") for s in scenes):
        teks_statis = next((s.get("text") for s in scenes if s.get("text")), None) \
            or data.get("judul") or ""
        scenes = ([{"start": 0.0, "end": float(total_duration), "text": teks_statis,
                    "statis": True}] if teks_statis else [])

    if not pakai_audio_asli and TTS_KATA and (os.getenv("NARASI_TEKS") or "1") != "0":
        # Teks yang MENGIKUTI SUARA narasi menggantikan teks per-scene tulisan LLM (yang
        # tidak sinkron dengan narasi). Teks statis (judul) tetap ada di atasnya.
        narasi = petakan_kata(full_vo, TTS_KATA)
        if narasi:
            statis = [s for s in scenes if s.get("statis")]
            scenes = statis + [{"start": narasi[0]["start"], "end": float(total_duration),
                                "text": " ".join(w["word"] for w in narasi),
                                "words": narasi, "gaya": NARASI_STYLE}]
            print(f"💬 Teks narasi mengikuti suara: {len(narasi)} kata (gaya {NARASI_STYLE}).")

    scenes = clamp_scenes(scenes, total_duration)

    segment_paths = []
    if pakai_audio_asli:
        # Satu segmen per rentang yang dipertahankan: klip dengan jeda di tengah
        # jadi beberapa potong, dan concat menyambungnya kembali tanpa jeda itu.
        potongan = [(i, j, a, b) for i, item in enumerate(rencana)
                    for j, (a, b) in enumerate(item["ranges"])]
        bendera = fade_flags(sambungan_audio_asli(rencana), len(potongan))
        for (i, j, a, b), (f_in, f_out) in zip(potongan, bendera):
            seg_path = os.path.join(output_dir, f"_segment_{i}_{j}.mp4")
            build_segment(rencana[i]["path"], b - a, seg_path,
                          keep_audio=not bisu, potong=(a, b),
                          fade_in=f_in, fade_out=f_out)
            segment_paths.append(seg_path)
    else:
        # speed ramp HANYA di jalur ini (voice-over AI): tidak ada ucapan asli
        # tersinkron ke klip mana pun di sini (narasinya lagu/trek terpisah,
        # scene dipatok ke waktu narasi -- bukan ke isi klip), jadi mengubah
        # kecepatan klip tidak membuat apa pun lepas sinkron. Mode audio asli/
        # mute (cabang di atas) TIDAK memanggil resolve_speed_factor() sama
        # sekali -- lihat catatan di style.resolve_speed_factor().
        faktor_kecepatan = resolve_speed_factor()
        bendera = fade_flags(sambungan_scene(durasi_klip, scenes), len(existing_assets))
        for i, asset_path in enumerate(existing_assets):
            seg_path = os.path.join(output_dir, f"_segment_{i}.mp4")
            layak = rentang_layak_ai(asset_path) if asset_path not in broll_tmp else None
            build_segment(asset_path, durasi_klip[i], seg_path, keep_audio=False,
                          fade_in=bendera[i][0], fade_out=bendera[i][1],
                          speed_factor=faktor_kecepatan, potong=layak)
            segment_paths.append(seg_path)

    fade_dipakai = sum(1 for f_in, _ in bendera if f_in)
    print(f"✂️ {len(segment_paths)} potongan, {fade_dipakai} sambungan pakai fade, "
          f"sisanya hard cut.")

    silent_combined = os.path.join(output_dir, "_combined_silent.mp4")
    if len(segment_paths) > 1:
        concat_segments(segment_paths, silent_combined, output_dir, keep_audio=pakai_audio_asli and not bisu)
    else:
        os.replace(segment_paths[0], silent_combined)

    if pakai_audio_asli:
        # Audio sudah menyatu di segmen; tidak ada yang perlu di-mux.
        print("🎨 Menambahkan subtitle dari ucapan asli...")
        apply_text_overlay(silent_combined, scenes, output_video)
        with_text = None
    else:
        with_text = os.path.join(output_dir, "_combined_text.mp4")
        print("🎨 Menambahkan teks per-scene...")
        apply_text_overlay(silent_combined, scenes, with_text)
        print("🚀 Menggabungkan voice-over...")
        mux_audio(with_text, temp_audio, output_video)

    # Musik ditambahkan SETELAH audio final terbentuk (mode apa pun), dan
    # SEBELUM cover diambil supaya artefaknya berasal dari berkas yang sama
    # dengan yang dikirim ke user.
    musik_dipakai = None
    musik_mood = None
    if music_wanted():
        try:
            track = pick_track(requested_mood(),
                               run_id=os.getenv("CONTENT_FACTORY_RUN_ID") or "")
        except MusicError as e:
            print(f"[warn] {e} — video dibuat TANPA musik.")
            track = None
        if track:
            sementara = os.path.join(output_dir, "_with_music.mp4")
            solo = not has_audio_stream(output_video)      # dibisukan: musik satu-satunya suara
            try:
                tambah_musik(output_video, track, sementara, total_duration, ada_ucapan=ada_ucapan)
                os.replace(sementara, output_video)
                musik_dipakai = os.path.basename(track)
                try:
                    from music_mood import analisis_cached, ringkas as ringkas_mood
                    musik_mood = analisis_cached(track)
                    print(f"🎼 Suasana musik: {ringkas_mood(musik_mood)}")
                except Exception as e:      # label hanya informasi; tidak boleh menggagalkan
                    print(f"[warn] analisis suasana musik dilewati ({type(e).__name__}: {e})")
                print(f"🎵 Musik: {musik_dipakai} (satu-satunya suara, level ke ±16 LUFS)" if solo
                      else f"🎵 Musik latar: {musik_dipakai} "
                           + ("(level menyesuaikan suara video + auto-ducking)" if ada_ucapan
                              else "(di atas suara suasana, tanpa ducking)"))
            except Exception as e:
                # Musik itu hiasan: video yang sudah jadi jauh lebih berharga.
                print(f"[warn] gagal menambahkan musik ({e}); video tetap dipakai tanpa musik.")
                if os.path.exists(sementara):
                    os.remove(sementara)
        else:
            print("[info] belum ada track di assets/music/ — video dibuat tanpa musik."
                  + (" Karena suara asli dibisukan, video ini TANPA SUARA sama sekali." if bisu else ""))

    # Cover diambil SETELAH mux dan SEBELUM cleanup: hanya `output_video` yang
    # sudah punya teks terbakar, rasio kanvas, dan encoding final. Mengambilnya
    # dari segmen atau bahan mentah akan menghasilkan gambar tanpa hook.
    thumb_path = None
    if THUMBNAIL_ENABLED:
        detik = thumbnail_time(scenes, total_duration)
        thumb_path = extract_thumbnail(
            output_video, os.path.splitext(output_video)[0] + ".jpg", detik)
        if thumb_path:
            print(f"🖼️ Cover diambil dari detik {detik:.2f}: {thumb_path}")

    for tmp in [*segment_paths, silent_combined, with_text, temp_audio, *broll_tmp]:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)

    print(f"✅ SUKSES! Video otomatis siap di: {output_video}")

    write_status(
        "SUCCESS",
        file_path=output_video,
        thumb_path=thumb_path,
        duration=total_duration,
        target_duration=target_duration,
        actual_duration=round(total_duration, 2),
        duration_adjusted=durasi_dikoreksi,
        edit_plan_applied=edit_dipakai,
        music=musik_dipakai,
        music_mood=musik_mood,
        broll=broll_info,
        emoji_dihapus=sorted(set(EMOJI_DIHAPUS)) or None,
        teks_animasi=dict(TEKS_ANIMASI) or None,
        potong_visual={k: v for k, v in POTONG_VISUAL.items()} or None,
        suara=dict(TTS_CATATAN) if audio_mode == "ai" else None,
        audio_mode=audio_mode,
        judul=data.get("judul"),
        scene_count=len(scenes),
        source_assets=existing_assets,
    )


if __name__ == "__main__":
    script_file = sys.argv[1] if len(sys.argv) > 1 else "workspace/drafts/script.json"
    image_file = sys.argv[2] if len(sys.argv) > 2 else ""

    try:
        render_from_agent_script(script_file, image_file)
    except Exception as e:
        log_error("auto_render.py render failure", e)
        write_status("FAILED", error=str(e))
        print(f"❌ Error: {e}. Detail lengkap dicatat di {ERROR_LOG_PATH}.")
