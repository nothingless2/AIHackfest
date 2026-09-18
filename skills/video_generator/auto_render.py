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
from retry import with_retry_async  # noqa: E402
from moviepy import AudioFileClip

TARGET_W, TARGET_H = 1080, 1920
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
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


def _tts_retriable(exc):
    """edge-tts memakai layanan Microsoft tanpa autentikasi: tidak ada kategori
    kegagalan permanen yang jelas selain bug pemakaian. Kegagalan jaringan/protokol
    diperlakukan sementara; TypeError/ValueError (salah pakai API) tidak."""
    return not isinstance(exc, (TypeError, ValueError, KeyError))


async def generate_voice(text, output_audio):
    """TTS dgn retry. Instance Communicate DIBUAT BARU tiap percobaan -- objek
    yang sudah gagal menyimpan state koneksi dan tidak aman dipakai ulang."""

    async def sekali():
        communicate = edge_tts.Communicate(text, voice="id-ID-GadisNeural", rate="+5%")
        await communicate.save(output_audio)

    await with_retry_async(
        sekali,
        is_retriable=_tts_retriable,
        attempt_timeout=TTS_ATTEMPT_TIMEOUT,
        label="voice-over edge-tts",
    )
    _catat_pemakaian_tts(text)


def _catat_pemakaian_tts(text):
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
            engine="edge-tts",
            chars=chars,
            cost_usd=estimate_tts_cost("edge-tts", chars),
        )
    except Exception as e:
        print(f"[warn] cost: gagal mencatat pemakaian TTS: {type(e).__name__}: {e}")


def scale_crop_filter():
    """Filter ffmpeg: isi kanvas 1080x1920 tanpa distorsi, lalu crop tengah."""
    return (
        f"scale=-2:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},fps={FPS}"
    )


def build_segment(asset_path, duration, segment_path, *, keep_audio=False):
    """Satu bahan mentah (gambar atau video) -> satu segmen 9:16 sepanjang `duration`.

    `keep_audio=True` (mode audio asli) mempertahankan suara asli video, dan
    memberi gambar trek audio SENYAP sepanjang durasinya. Trek senyap itu wajib:
    concat demuxer dengan -c copy menuntut semua segmen punya susunan stream yang
    sama, jadi satu segmen tanpa audio akan merusak penggabungan.
    """
    ext = os.path.splitext(asset_path)[1].lower()
    vf = scale_crop_filter()
    audio_enc = ["-c:a", "aac", "-ar", AUDIO_RATE, "-ac", AUDIO_CHANNELS]

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
            args = ["-i", asset_path, "-vf", vf,
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
MAX_SUBTITLE_LINES = 3


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


def subtitle_scenes(data, assets, durasi_klip, video_width=None, video_height=None):
    """Ubah transkrip jadi scene subtitle dengan waktu ABSOLUT.

    Whisper memberi timestamp relatif terhadap awal TIAP klip. Di video gabungan,
    klip ke-i mulai pada jumlah durasi klip sebelumnya — jadi tiap potongan harus
    digeser sebesar offset itu. Tanpa penggeseran, semua subtitle menumpuk di
    detik-detik awal video.

    Kalau ada timestamp per KATA, scene dibangun dari kata (lebih akurat dan
    memungkinkan animasi per-kata). Kalau hanya ada potongan kalimat, kalimat
    panjang dipecah dengan durasi dibagi menurut jumlah kata.

    Return [] kalau tidak ada transkrip; pemanggil lalu memakai scenes dari brief.
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
    for i, path in enumerate(assets):
        nama = os.path.basename(path)
        batas = durasi_klip[i]
        kata = per_kata.get(nama) or []

        if kata:
            for kelompok in chunk_words(kata, fs, W):
                mulai = min(float(kelompok[0].get("start", 0)), batas)
                selesai = min(float(kelompok[-1].get("end", 0)), batas)
                if selesai <= mulai:
                    continue
                hasil.append({
                    "start": round(offset + mulai, 2),
                    "end": round(offset + selesai, 2),
                    "text": " ".join(k["word"] for k in kelompok),
                    "words": [
                        {"word": k["word"],
                         "start": round(offset + min(float(k.get("start", 0)), batas), 2),
                         "end": round(offset + min(float(k.get("end", 0)), batas), 2)}
                        for k in kelompok
                    ],
                })
        else:
            for seg in per_segmen.get(nama, []):
                teks = (seg.get("text") or "").strip()
                mulai, selesai = float(seg.get("start", 0)), float(seg.get("end", 0))
                if not teks or selesai <= mulai:
                    continue
                # Dijepit ke panjang klip: transkrip bisa sedikit melewati batas,
                # dan subtitle yang muncul setelah klipnya berganti menyesatkan.
                mulai, selesai = min(mulai, batas), min(selesai, batas)
                if selesai <= mulai:
                    continue
                bagian = split_for_subtitle(teks, fs, W)
                total_kata = sum(len(b.split()) for b in bagian) or 1
                jalan, rentang = offset + mulai, selesai - mulai
                for b in bagian:
                    porsi = rentang * (len(b.split()) / total_kata)
                    hasil.append({
                        "start": round(jalan, 2),
                        "end": round(jalan + porsi, 2),
                        "text": b,
                    })
                    jalan += porsi
        offset += batas

    if hasil:
        berkata = sum(1 for h in hasil if h.get("words"))
        print(f"📝 {len(hasil)} subtitle dari ucapan asli"
              + (f" ({berkata} dengan animasi per-kata)" if berkata else ""))
    return hasil


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
        durasi_klip = [
            media_duration(p) if os.path.splitext(p)[1].lower() in VIDEO_EXTENSIONS
            else IMAGE_CLIP_SECONDS
            for p in existing_assets
        ]
        durasi_klip = [d if d > 0 else IMAGE_CLIP_SECONDS for d in durasi_klip]
        total_duration = sum(durasi_klip)
        scenes = subtitle_scenes(data, existing_assets, durasi_klip,
                                 TARGET_W, TARGET_H) or scenes
        print(f"⏱️ Durasi total dari {len(existing_assets)} bahan: {total_duration:.1f} detik")
    else:
        print("🎙️ Menghasilkan Voice-Over AI secara dinamis...")
        asyncio.run(generate_voice(full_vo, temp_audio))
        total_duration = AudioFileClip(temp_audio).duration
        print(f"⏱️ Durasi Voice-Over: {total_duration:.1f} detik")
        per = max(MIN_CLIP_DURATION, total_duration / len(existing_assets))
        durasi_klip = [per] * len(existing_assets)
        print(f"🖼️ Menyusun {len(existing_assets)} bahan mentah user ({per:.1f} detik per bahan)")

    segment_paths = []
    for i, asset_path in enumerate(existing_assets):
        seg_path = os.path.join(output_dir, f"_segment_{i}.mp4")
        build_segment(asset_path, durasi_klip[i], seg_path, keep_audio=pakai_audio_asli)
        segment_paths.append(seg_path)

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

    for tmp in [*segment_paths, silent_combined, with_text, temp_audio]:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)

    print(f"✅ SUKSES! Video otomatis siap di: {output_video}")

    write_status(
        "SUCCESS",
        file_path=output_video,
        duration=total_duration,
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
