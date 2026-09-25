"""Cover/thumbnail video: satu frame dari hasil akhir (JPG ukuran kanvas penuh).

Cover disimpan sebagai artefak di samping videonya. Pengiriman ke Telegram dilakukan agent
Hermes; untuk video MP4, Telegram membuat preview sendiri dari FRAME PERTAMA video (temuan
pengiriman nyata 19 Sep 2026). Karena itu `fade_filters()` tidak memasang fade masuk di segmen
pertama: frame 0 hitam berarti preview hitam untuk setiap video.

Cover TIDAK PERNAH menggagalkan render: semua fungsi di sini mengembalikan None saat gagal, dan
pemanggil melanjutkan tanpa cover.
"""

import os
import subprocess

THUMBNAIL_ENABLED = (os.getenv("THUMBNAIL_ENABLED") or "1").strip().lower() not in (
    "0", "false", "no", "off",
)


def thumbnail_time(scenes, total_duration):
    """Detik frame yang diambil: TENGAH scene pertama.

    Alasannya bukan estetika: di titik itu teks hook sudah terbakar ke frame,
    jadi cover otomatis memuat hook tanpa perlu overlay terpisah. Kalau tidak ada
    scene sama sekali (mis. video tanpa subtitle), ambil tengah video.

    Selalu dijepit ke durasi nyata -- scene yang melewati akhir video akan
    menghasilkan frame kosong.
    """
    total = float(total_duration or 0)
    titik = None
    for sc in scenes or []:
        mulai, selesai = sc.get("start"), sc.get("end")
        if mulai is None or selesai is None:
            continue
        mulai, selesai = float(mulai), float(selesai)
        if selesai <= mulai:
            continue
        titik = (mulai + selesai) / 2.0
        break

    if titik is None:
        titik = total / 2.0 if total > 0 else 0.0
    if total > 0:
        titik = min(titik, max(0.0, total - 0.1))
    return round(max(titik, 0.0), 2)


def _ffmpeg(args, label):
    """True kalau ffmpeg sukses. Kegagalan hanya diperingatkan -- cover bersifat
    tambahan, bukan syarat."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", *args],
            check=True, capture_output=True, text=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        pesan = getattr(e, "stderr", "") or str(e)
        print(f"[warn] thumbnail: {label} gagal: {pesan.strip()[:200]}")
        return False


def extract_thumbnail(video_path, out_path, at_seconds=0.0):
    """Satu frame JPG berukuran kanvas penuh, disimpan sebagai artefak.

    Diambil dari video HASIL AKHIR (setelah teks dibakar dan audio di-mux), bukan
    dari bahan mentah: hanya file itu yang punya teks, rasio, dan encoding final.
    Return out_path, atau None kalau gagal.
    """
    if not os.path.exists(video_path):
        return None
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    ok = _ffmpeg(
        ["-ss", f"{max(0.0, float(at_seconds)):.2f}", "-i", video_path,
         "-frames:v", "1", "-q:v", "2", out_path],
        "ekstraksi frame",
    )
    if ok and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path
    return None

