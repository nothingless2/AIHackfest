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
from retry import with_retry_async  # noqa: E402
from music import (  # noqa: E402
    MusicError, auto_volume, build_filter as music_filter, has_audio_stream,
    music_wanted, pick_track, requested_mood,
)
from spoken import prompt_rule, spoken_text  # noqa: E402
from thumbnail import (  # noqa: E402
    THUMBNAIL_ENABLED, extract_thumbnail, thumbnail_time,
)
from trim_silence import (  # noqa: E402
    TRIM_SILENCE, detect_silence, keep_ranges, map_time, total_kept,
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
EDGE_VOICE = os.getenv("EDGE_TTS_VOICE", "id-ID-GadisNeural")
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
        communicate = edge_tts.Communicate(text, voice=EDGE_VOICE, rate=EDGE_RATE)
        await communicate.save(output_audio)

    await with_retry_async(
        sekali, is_retriable=_tts_retriable,
        attempt_timeout=TTS_ATTEMPT_TIMEOUT, label="voice-over edge-tts",
    )


async def _voice_openai(text, output_audio):
    """OpenAI TTS. `instructions` mengarahkan GAYA bicara -- itu yang membedakan
    gpt-4o-mini-tts dari TTS lama yang membaca datar."""
    from common import make_openai_client, openai_is_retriable, openai_retry_after
    from retry import with_retry

    client = make_openai_client(timeout=TTS_ATTEMPT_TIMEOUT * 2)

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


async def generate_voice(text, output_audio):
    """Voice-over dgn penyedia yang bisa dipilih.

    Kegagalan OpenAI JATUH ke edge-tts, bukan menggagalkan render: kredit habis
    atau relay bermasalah tidak boleh membuat video gagal total kalau masih ada
    jalur gratis yang bekerja.
    """
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


def scale_crop_filter(w=None, h=None, fit=None):
    """Filter ffmpeg untuk memuat bahan ke kanvas w x h.

    CATATAN BUG LAMA: versi sebelumnya memakai
        scale=-2:{H}:force_original_aspect_ratio=increase
    yang hanya menyebut SATU dimensi. `increase` tidak punya pembanding lebar,
    jadi untuk sumber yang lebih sempit dari kanvas hasilnya lebih kecil dari
    lebar target dan crop gagal. Tidak pernah terlihat selama kanvas selalu 9:16;
    langsung meledak begitu rasio jadi parameter. Kedua dimensi kini disebut.
    """
    w = w or TARGET_W
    h = h or TARGET_H
    fit = (fit or TARGET_FIT).lower()

    if fit == "letterbox":
        return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,fps={FPS}")

    if fit == "blur":
        kecil_h = max(2, int(BLUR_SMALL_W * h / w) // 2 * 2)
        return (
            f"split[bg][fg];"
            f"[bg]scale={BLUR_SMALL_W}:{kecil_h}:force_original_aspect_ratio=increase,"
            f"crop={BLUR_SMALL_W}:{kecil_h},gblur=sigma={BLUR_SIGMA},"
            f"scale={w}:{h}[bgx];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgx];"
            f"[bgx][fgx]overlay=(W-w)/2:(H-h)/2,fps={FPS}"
        )

    return (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},fps={FPS}")


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
    batas = []
    for i, item in enumerate(rencana):
        ranges = item["ranges"]
        for j in range(len(ranges)):
            terakhir_di_klip = j == len(ranges) - 1
            if not terakhir_di_klip:
                batas.append(False)          # potongan internal -> hard cut
                continue
            if i == len(rencana) - 1:
                continue                      # tidak ada sambungan sesudahnya
            asli = item.get("asli") or ranges[-1][1]
            ekor = max(0.0, asli - ranges[-1][1])
            kepala = max(0.0, rencana[i + 1]["ranges"][0][0])
            batas.append(ekor + kepala >= TRANSITION_MIN_GAP)
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
                  potong=None, fade_in=True, fade_out=True):
    """Satu bahan mentah (gambar atau video) -> satu segmen 9:16 sepanjang `duration`.

    `keep_audio=True` (mode audio asli) mempertahankan suara asli video, dan
    memberi gambar trek audio SENYAP sepanjang durasinya. Trek senyap itu wajib:
    concat demuxer dengan -c copy menuntut semua segmen punya susunan stream yang
    sama, jadi satu segmen tanpa audio akan merusak penggabungan.
    """
    ext = os.path.splitext(asset_path)[1].lower()
    vf = scale_crop_filter()
    fade_v, fade_a = fade_filters(duration, keep_audio=keep_audio,
                                  fade_in=fade_in, fade_out=fade_out)
    if fade_v:
        vf = f"{vf},{fade_v}"
    audio_enc = ["-c:a", "aac", "-ar", AUDIO_RATE, "-ac", AUDIO_CHANNELS]
    if fade_a:
        audio_enc = ["-af", fade_a] + audio_enc
    # `potong` = (mulai, selesai) untuk mengambil sepotong klip saja (pemotongan
    # jeda). -ss diletakkan SEBELUM -i supaya ffmpeg mencari cepat ke posisi itu
    # alih-alih mendekode dari awal.
    iris = ["-ss", f"{potong[0]:.3f}", "-to", f"{potong[1]:.3f}"] if potong else []

    if ext in IMAGE_EXTENSIONS:
        args = ["-loop", "1", "-i", asset_path]
        if keep_audio:
            args += ["-f", "lavfi", "-i",
                     f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}"]
        args += ["-t", f"{duration:.3f}", "-vf", vf,
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
            args = ["-stream_loop", "-1", "-i", asset_path,
                    "-t", f"{duration:.3f}", "-vf", vf, "-an",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast"]
        run_ffmpeg(args + [segment_path], f"video {os.path.basename(asset_path)}")

    else:
        raise ValueError(f"Ekstensi tidak didukung untuk '{asset_path}': {ext}")


def concat_segments(segment_paths, output_path, workdir):
    """Gabung semua segmen jadi satu (hard cut, tanpa crossfade mahal)."""
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
    "putih-kotak":  {"color": "white",  "box": "black@0.55", "pad": 24},
    "kuning-kotak": {"color": "yellow", "box": "black@0.55", "pad": 24},
    "putih-tebal":  {"color": "white",  "border": 5},
    "kuning":       {"color": "yellow", "border": 4},  # gaya lama
}
SUBTITLE_STYLE = os.getenv("SUBTITLE_STYLE", "putih-kotak")


def subtitle_style():
    """Gaya terpilih. Nama tak dikenal -> peringatan + default, bukan diam-diam."""
    gaya = SUBTITLE_STYLES.get(SUBTITLE_STYLE)
    if gaya is None:
        print(f"[warn] SUBTITLE_STYLE tidak dikenal ({SUBTITLE_STYLE!r}), memakai "
              f"'putih-kotak'. Pilihan: {', '.join(sorted(SUBTITLE_STYLES))}.")
        return SUBTITLE_STYLES["putih-kotak"]
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


def _drawtext(teks, fs, y, gaya, enable, alpha=None):
    bagian = [
        "drawtext=",
        f"fontfile={FONT_PATH}:text='{teks}':fontsize={fs}:",
        f"fontcolor={gaya['color']}:line_spacing=8:x=(w-text_w)/2:y={y}",
    ]
    if gaya.get("box"):
        bagian.append(f":box=1:boxcolor={gaya['box']}:boxborderw={gaya.get('pad', 24)}")
    else:
        bagian.append(f":bordercolor=black:borderw={gaya.get('border', 4)}")
    if alpha:
        bagian.append(f":alpha='{alpha}'")
    bagian.append(f":enable='{enable}'")
    return "".join(bagian)


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

    filters = []
    for sc in scenes:
        text = sc.get("text")
        start, end = sc.get("start", 0), sc.get("end")
        if not text or end is None or end <= start:
            continue

        kata = sc.get("words") or []
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
            # Fade masuk 0,25 detik; dijepit ke 1 supaya tetap penuh setelahnya.
            alpha = f"min(1,(t-{start})/0.25)"
            filters.append(_drawtext(
                escape_drawtext(wrap_text(text, fs, W, max_lines=SUBTITLE_MAX_LINES)),
                fs, y, gaya, f"between(t,{start},{end})", alpha=alpha,
            ))
    return filters


def apply_text_overlay(input_path, scenes, output_path):
    filters = build_drawtext_chain(scenes, TARGET_H, TARGET_W)
    if not filters:
        os.replace(input_path, output_path)
        return
    run_ffmpeg(
        ["-i", input_path, "-vf", ",".join(filters), "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-preset", "fast", output_path],
        "overlay teks",
    )


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

        kata = per_kata.get(nama) or []
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



DURATION_FIX_MODEL = os.getenv("DURATION_FIX_MODEL", "gpt-4o")
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
        from common import chat_json
        return chat_json(
            [{"role": "user", "content": pesan}],
            model=DURATION_FIX_MODEL,
            label="koreksi durasi naskah",
            max_attempts=1,
            timeout=DURATION_FIX_TIMEOUT,
        )
    except Exception as e:
        print(f"[warn] koreksi durasi gagal ({e}); lanjut dengan naskah asli.")
        return None



def tambah_musik(video_path, track, out_path, durasi):
    """Campur musik latar ke audio video, dengan ducking otomatis.

    Musik di-loop kalau lebih pendek dari video, dan `-shortest` memastikan
    hasilnya tidak ikut memanjang mengikuti musik.
    `-c:v copy`: videonya tidak disentuh sama sekali, jadi tahap ini tidak
    menambah kerugian kualitas dan waktunya hanya beberapa detik.
    """
    punya = has_audio_stream(video_path)
    # Volume diukur dari loudness video ini, bukan angka tetap: gain tetap
    # membuat musik tenggelam di rekaman keras dan terlalu maju di rekaman pelan.
    vol = auto_volume(video_path, track) if punya else None
    run_ffmpeg(
        ["-i", video_path, "-stream_loop", "-1", "-i", track,
         "-filter_complex", music_filter(durasi, punya_ucapan=punya, volume=vol),
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

    audio_mode = (data.get("audio_mode") or "ai").strip().lower()
    pakai_audio_asli = audio_mode == "original"

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

    if pakai_audio_asli:
        # Durasi ditentukan bahan, bukan TTS: tiap klip main sepanjang aslinya
        # supaya ucapan user tidak terpotong di tengah kalimat.
        print("🔊 Memakai AUDIO ASLI dari video user (tanpa voice-over AI).")
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
            rencana.append({"path": path, "ranges": ranges, "durasi": baru,
                            "asli": asli})

        durasi_klip = [r["durasi"] for r in rencana]
        total_duration = sum(durasi_klip)
        if dibuang > 0.05:
            print(f"✂️ {dibuang:.1f} detik jeda dipotong dari {len(rencana)} bahan.")
        scenes = subtitle_scenes(data, rencana, TARGET_W, TARGET_H) or scenes
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

        existing_assets, durasi_klip = bagi_durasi(existing_assets, total_duration)
        per = durasi_klip[0] if durasi_klip else 0.0
        print(f"🖼️ Menyusun {len(existing_assets)} bahan mentah user ({per:.1f} detik per bahan)")

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
                          keep_audio=True, potong=(a, b),
                          fade_in=f_in, fade_out=f_out)
            segment_paths.append(seg_path)
    else:
        bendera = fade_flags(sambungan_scene(durasi_klip, scenes), len(existing_assets))
        for i, asset_path in enumerate(existing_assets):
            seg_path = os.path.join(output_dir, f"_segment_{i}.mp4")
            build_segment(asset_path, durasi_klip[i], seg_path, keep_audio=False,
                          fade_in=bendera[i][0], fade_out=bendera[i][1])
            segment_paths.append(seg_path)

    fade_dipakai = sum(1 for f_in, _ in bendera if f_in)
    print(f"✂️ {len(segment_paths)} potongan, {fade_dipakai} sambungan pakai fade, "
          f"sisanya hard cut.")

    silent_combined = os.path.join(output_dir, "_combined_silent.mp4")
    if len(segment_paths) > 1:
        concat_segments(segment_paths, silent_combined, output_dir)
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
    if music_wanted():
        try:
            track = pick_track(requested_mood(),
                               run_id=os.getenv("CONTENT_FACTORY_RUN_ID") or "")
        except MusicError as e:
            print(f"[warn] {e} — video dibuat TANPA musik.")
            track = None
        if track:
            sementara = os.path.join(output_dir, "_with_music.mp4")
            try:
                tambah_musik(output_video, track, sementara, total_duration)
                os.replace(sementara, output_video)
                musik_dipakai = os.path.basename(track)
                print(f"🎵 Musik latar: {musik_dipakai} "
                  f"(level menyesuaikan suara video + auto-ducking)")
            except Exception as e:
                # Musik itu hiasan: video yang sudah jadi jauh lebih berharga.
                print(f"[warn] gagal menambahkan musik ({e}); video tetap dipakai tanpa musik.")
                if os.path.exists(sementara):
                    os.remove(sementara)
        else:
            print("[info] belum ada track di assets/music/ — video dibuat tanpa musik.")

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

    for tmp in [*segment_paths, silent_combined, with_text, temp_audio]:
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
        music=musik_dipakai,
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
