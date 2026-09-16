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
from moviepy import AudioFileClip

TARGET_W, TARGET_H = 1080, 1920
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SAFE_TOP_MARGIN_PX = 300
SAFE_BOTTOM_MARGIN_PX = 300

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}
MIN_CLIP_DURATION = 1.5
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


async def generate_voice(text, output_audio):
    communicate = edge_tts.Communicate(text, voice="id-ID-GadisNeural", rate="+5%")
    await communicate.save(output_audio)


def scale_crop_filter():
    """Filter ffmpeg: isi kanvas 1080x1920 tanpa distorsi, lalu crop tengah."""
    return (
        f"scale=-2:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},fps={FPS}"
    )


def build_segment(asset_path, duration, segment_path):
    """Satu bahan mentah (gambar atau video) -> satu segmen 9:16 sepanjang `duration`."""
    ext = os.path.splitext(asset_path)[1].lower()
    vf = scale_crop_filter()

    if ext in IMAGE_EXTENSIONS:
        run_ffmpeg(
            [
                "-loop", "1", "-i", asset_path,
                "-t", f"{duration:.3f}",
                "-vf", vf,
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
                segment_path,
            ],
            f"gambar {os.path.basename(asset_path)}",
        )
    elif ext in VIDEO_EXTENSIONS:
        # -stream_loop -1 mengulang video kalau lebih pendek dari `duration`;
        # -t memotongnya persis di durasi target baik untuk video pendek maupun panjang.
        run_ffmpeg(
            [
                "-stream_loop", "-1", "-i", asset_path,
                "-t", f"{duration:.3f}",
                "-vf", vf,
                "-an",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
                segment_path,
            ],
            f"video {os.path.basename(asset_path)}",
        )
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


def build_drawtext_chain(scenes, video_height):
    """Satu filter drawtext berantai per scene, tampil sesuai rentang waktu masing-masing."""
    y_pos = min(SAFE_TOP_MARGIN_PX, video_height - SAFE_BOTTOM_MARGIN_PX - 120)
    filters = []
    for sc in scenes:
        text = sc.get("text")
        start = sc.get("start", 0)
        end = sc.get("end")
        if not text or end is None or end <= start:
            continue
        safe_text = escape_drawtext(text)
        filters.append(
            "drawtext="
            f"fontfile={FONT_PATH}:text='{safe_text}':fontsize=52:fontcolor=yellow:"
            "bordercolor=black:borderw=4:line_spacing=6:"
            f"x=(w-text_w)/2:y={y_pos}:"
            f"enable='between(t,{start},{end})'"
        )
    return filters


def apply_text_overlay(input_path, scenes, output_path):
    filters = build_drawtext_chain(scenes, TARGET_H)
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


def render_from_agent_script(
    json_path="workspace/drafts/script.json",
    image_path="",
    output_video="workspace/drafts/video_output.mp4",
):
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"File {json_path} tidak ditemukan!")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    full_vo = data.get("full_voice_over", "")
    if not full_vo:
        raise ValueError(f"'full_voice_over' kosong di {json_path}, tidak ada narasi untuk di-render.")

    scenes = data.get("scenes", [])
    media_assets = data.get("media_assets", [])

    output_dir = os.path.dirname(output_video) or "."
    os.makedirs(output_dir, exist_ok=True)
    temp_audio = os.path.join(output_dir, "temp_vo.mp3")

    print(f"🎬 Judul Konten: {data.get('judul', 'Untitled')}")
    print("🎙️ Menghasilkan Voice-Over AI secara dinamis...")
    asyncio.run(generate_voice(full_vo, temp_audio))

    total_duration = AudioFileClip(temp_audio).duration
    print(f"⏱️ Durasi Voice-Over: {total_duration:.1f} detik")

    existing_assets = [p for p in media_assets if os.path.exists(p)]
    missing = [p for p in media_assets if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(f"Bahan mentah tidak ditemukan: {missing}")

    if not existing_assets:
        raise ValueError("Tidak ada bahan mentah (media_assets) untuk dirender.")

    per_clip = max(MIN_CLIP_DURATION, total_duration / len(existing_assets))
    print(f"🖼️ Menyusun {len(existing_assets)} bahan mentah user ({per_clip:.1f} detik per bahan)")

    segment_paths = []
    for i, asset_path in enumerate(existing_assets):
        seg_path = os.path.join(output_dir, f"_segment_{i}.mp4")
        build_segment(asset_path, per_clip, seg_path)
        segment_paths.append(seg_path)

    silent_combined = os.path.join(output_dir, "_combined_silent.mp4")
    if len(segment_paths) > 1:
        concat_segments(segment_paths, silent_combined, output_dir)
    else:
        os.replace(segment_paths[0], silent_combined)

    with_text = os.path.join(output_dir, "_combined_text.mp4")
    print("🎨 Menambahkan teks per-scene...")
    apply_text_overlay(silent_combined, scenes, with_text)

    print("🚀 Menggabungkan voice-over...")
    mux_audio(with_text, temp_audio, output_video)

    for tmp in [*segment_paths, silent_combined, with_text, temp_audio]:
        if os.path.exists(tmp):
            os.remove(tmp)

    print(f"✅ SUKSES! Video otomatis siap di: {output_video}")

    write_status(
        "SUCCESS",
        file_path=output_video,
        duration=total_duration,
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
