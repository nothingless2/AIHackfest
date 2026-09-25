"""Pembersihan file kerja render dan retensi file per-run.

Dua hal berbeda yang sengaja dipisah:

1. `clear_render_workspace()` — file KERJA render (_segment_*.mp4, _combined_*.mp4,
   _concat_list.txt, temp_vo.mp3) plus draft lama `video_output.mp4`. auto_render.py
   sudah menghapus file kerjanya sendiri di akhir render yang SUKSES, jadi sisa file
   ini hanya muncul kalau render mati di tengah (timeout/killpg/SIGINT/kill -9).
   Dipanggil di AWAL render, DI DALAM lock render.

2. `sweep_old_run_files()` — file HASIL per-run
   (video_{run_id}.mp4, video_{run_id}.jpg, creative_brief_{run_id}.json) yang
   umurnya panjang. Cover sengaja diberi awalan nama yang sama dengan videonya
   supaya tidak ada pola retensi yang perlu diingat terpisah.

`workspace/raw/` TIDAK PERNAH disentuh modul ini: bahan mentah milik user adalah
masukan, bukan sampah kerja, dan menghapusnya bisa menggagalkan run yang sedang
berjalan maupun percobaan ulang.
"""

import glob
import shutil
import os
import time

from common import BRIEF_PATH, DRAFT_THUMB_PATH, DRAFT_VIDEO_PATH, DRAFTS_DIR, STATE_DIR

RUN_FILE_RETENTION_DAYS = int(os.getenv("RUN_FILE_RETENTION_DAYS", "7"))

# Nama file tetap yang TIDAK boleh ikut tersapu retensi: keduanya adalah path
# kerja milik run yang sedang berjalan, bukan artefak lama.
_LINDUNGI = {
    os.path.basename(DRAFT_VIDEO_PATH),
    os.path.basename(DRAFT_THUMB_PATH),
    os.path.basename(BRIEF_PATH),
}


def _hapus(path, label):
    """Hapus satu file. Kegagalan hanya diperingatkan -- pembersihan tidak boleh
    menggagalkan pipeline."""
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except OSError as e:
        print(f"[warn] retention: gagal menghapus {label} {path}: {e}")
    return False


def clear_render_workspace():
    """Buang sisa file kerja render sebelum render baru mulai.

    WAJIB dipanggil di dalam lock render: di luar lock, ini bisa menghapus file
    milik render lain yang sedang berjalan.

    Juga menghapus `video_output.mp4` lama. Pengaman ini ada di versi pra-1a
    ("buang sisa file rusak dari run sebelumnya supaya tidak terkirim tidak
    sengaja") lalu hilang saat 1a menulis ulang main() -- dipulihkan di sini,
    sekaligus di tempat yang lebih tepat karena kini dilindungi lock.
    """
    jumlah = 0
    pola = ["_segment_*.mp4", "_combined_*.mp4", "_concat_list.txt", "_filter_teks.txt",
            "_with_music.mp4", "temp_vo.mp3", "_broll_*.mp4"]
    for p in pola:
        for path in glob.glob(os.path.join(DRAFTS_DIR, p)):
            jumlah += _hapus(path, "file kerja")

    # Folder kerja animasi teks (Remotion) dari render yang mati di tengah jalan.
    for d in glob.glob(os.path.join(DRAFTS_DIR, "_overlay_*")):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            jumlah += 1

    if _hapus(DRAFT_VIDEO_PATH, "draft lama"):
        jumlah += 1
    # Cover run sebelumnya ikut dibuang: kalau render baru gagal membuat cover,
    # cover lama akan terkirim bersama video baru -- gambar milik konten lain.
    if _hapus(DRAFT_THUMB_PATH, "cover lama"):
        jumlah += 1

    if jumlah:
        print(f"[info] retention: {jumlah} sisa file render dibersihkan sebelum mulai.")
    return jumlah


def sweep_old_run_files(max_age_days=None):
    """Jaring pengaman: buang file per-run yang lebih tua dari N hari.

    File hasil sengaja tidak dihapus begitu terkirim (agent Hermes yang mengirimnya,
    setelah skrip selesai), jadi retensi berdasarkan umur yang membersihkannya.

    Yang DILINDUNGI dan tidak pernah ikut tersapu:
    - `video_output.mp4` dan `creative_brief.json` (path kerja run berjalan)
    - seluruh isi `workspace/raw/` (bahan mentah user, bukan sampah kerja)
    - `workspace/published/` (arsip permanen yang dirujuk publish_history.json)
    """
    days = RUN_FILE_RETENTION_DAYS if max_age_days is None else max_age_days
    batas = time.time() - days * 86400
    jumlah = 0

    kandidat = (
        glob.glob(os.path.join(DRAFTS_DIR, "video_*.mp4"))
        + glob.glob(os.path.join(DRAFTS_DIR, "video_*.jpg"))
        + glob.glob(os.path.join(STATE_DIR, "creative_brief_*.json"))
        # Status pemeriksaan bahan (scripts/inspect_media.py). Valid hanya 24 jam
        # (INSPECT_TTL_HOURS); sisanya sampah -- dan memuat cuplikan permintaan user.
        + glob.glob(os.path.join(STATE_DIR, "inspect", "*.json"))
        # Draf naskah (scripts/draf_naskah.py): berlaku 24 jam, memuat naskah & konteks user.
        + glob.glob(os.path.join(STATE_DIR, "draf_naskah", "*.json"))
        + glob.glob(os.path.join(STATE_DIR, "draf_naskah", "*.dipakai"))
        # Musik yang diunggah user untuk satu run (workspace/music_user/). Hak ciptanya
        # milik user, dan berkasnya besar -- tidak untuk disimpan selamanya.
        + glob.glob(os.path.join(os.path.dirname(STATE_DIR), "music_user", "*"))
    )
    for path in kandidat:
        if os.path.basename(path) in _LINDUNGI:
            continue
        try:
            if os.path.getmtime(path) >= batas:
                continue
        except OSError:
            continue
        jumlah += _hapus(path, "file per-run kedaluwarsa")

    if jumlah:
        print(f"[info] retention: {jumlah} file per-run lebih tua dari {days} hari dibuang.")
    return jumlah
