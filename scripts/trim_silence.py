"""Deteksi dan pemotongan jeda diam pada bahan video.

Alasannya terukur, bukan dugaan: pada 5 video user, ~6,4 detik dari 35,3 detik
adalah jeda (18%), dan hampir semua klip diawali ruang mati sebelum orangnya
mulai bicara.

Hal yang WAJIB benar: memotong jeda internal MENGGESER semua waktu sesudahnya.
Subtitle dibangun dari timestamp Whisper terhadap audio ASLI, jadi tanpa
pemetaan ulang (`map_time`) subtitle akan melenceng makin jauh ke belakang.

Catatan alat ukur: `silencedetect` menulis hasilnya di level log `info`.
Menjalankan ffmpeg dengan `-v error` membuatnya TIDAK muncul sama sekali, dan
hasil kosong itu mudah disalahartikan sebagai "tidak ada jeda". Diverifikasi
dengan kontrol positif (file 2 dtk bunyi + 3 dtk senyap + 2 dtk bunyi).
"""

import os
import re
import subprocess

TRIM_SILENCE = os.getenv("TRIM_SILENCE", "1") not in ("0", "false", "False")
SILENCE_NOISE_DB = os.getenv("SILENCE_NOISE_DB", "-35")
SILENCE_MIN_DURATION = float(os.getenv("SILENCE_MIN_DURATION", "0.35"))
# Sisa bantalan di kedua ujung potongan supaya suku kata pertama/terakhir tidak
# ikut terpotong -- pemotongan yang terlalu rapat terdengar seperti tergagap.
SILENCE_PAD = float(os.getenv("SILENCE_PAD", "0.12"))
# Potongan yang lebih pendek dari ini tidak layak jadi klip sendiri.
MIN_KEEP_DURATION = float(os.getenv("MIN_KEEP_DURATION", "0.4"))

_START = re.compile(r"silence_start:\s*([0-9.]+)")
_END = re.compile(r"silence_end:\s*([0-9.]+)")


def detect_silence(path, *, noise_db=None, min_duration=None, timeout=120):
    """Daftar (mulai, selesai) rentang diam, dalam detik.

    Memakai level log default ffmpeg — JANGAN diganti ke `-v error`, karena
    silencedetect menulis di level info dan hasilnya akan hilang.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", path, "-af",
             f"silencedetect=noise={noise_db or SILENCE_NOISE_DB}dB:"
             f"d={min_duration if min_duration is not None else SILENCE_MIN_DURATION}",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[warn] trim: deteksi jeda gagal untuk {os.path.basename(path)}: {e}")
        return []

    keluaran = proc.stderr or ""
    mulai = [float(x) for x in _START.findall(keluaran)]
    selesai = [float(x) for x in _END.findall(keluaran)]

    # silence_end bisa muncul lebih dulu kalau file DIAWALI jeda (tidak ada
    # silence_start untuknya) — kasus paling umum pada rekaman selfie.
    if selesai and (not mulai or selesai[0] < mulai[0]):
        mulai = [0.0] + mulai
    rentang = list(zip(mulai, selesai))
    # Jeda di ujung akhir tidak punya silence_end.
    if len(mulai) > len(selesai):
        rentang.append((mulai[-1], None))
    return rentang


def keep_ranges(durasi, senyap, *, pad=None, min_keep=None):
    """Ubah rentang DIAM jadi rentang yang DIPERTAHANKAN.

    Diberi bantalan di kedua ujung, lalu potongan yang terlalu pendek dibuang.
    Kalau hasilnya kosong (mis. seluruh klip terdeteksi diam), kembalikan klip
    utuh — lebih baik menyimpan terlalu banyak daripada menghasilkan video kosong.
    """
    pad = SILENCE_PAD if pad is None else pad
    min_keep = MIN_KEEP_DURATION if min_keep is None else min_keep

    hasil, posisi = [], 0.0
    for a, b in senyap:
        a = max(0.0, min(a, durasi))
        b = durasi if b is None else max(0.0, min(b, durasi))
        if a - posisi > 0:
            hasil.append((posisi, a + pad))
        posisi = max(posisi, b - pad)
    if durasi - posisi > 0:
        hasil.append((posisi, durasi))

    bersih = []
    for a, b in hasil:
        a, b = max(0.0, a), min(durasi, b)
        if b - a >= min_keep:
            bersih.append((round(a, 3), round(b, 3)))
    return bersih or [(0.0, durasi)]


def map_time(t, ranges):
    """Petakan waktu ASLI ke waktu SETELAH pemotongan.

    Tanpa ini, subtitle yang dibangun dari timestamp Whisper akan melenceng makin
    jauh setiap kali ada jeda yang dibuang.
    """
    baru = 0.0
    for a, b in ranges:
        if t < a:
            return round(baru, 3)   # jatuh di bagian yang dipotong -> awal potongan berikutnya
        if t <= b:
            return round(baru + (t - a), 3)
        baru += b - a
    return round(baru, 3)


def total_kept(ranges):
    return round(sum(b - a for a, b in ranges), 3)
