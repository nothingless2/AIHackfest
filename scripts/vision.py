"""Siapkan bahan mentah user menjadi input visual untuk model multimodal.

Kenapa ada: sebelumnya Agent 1&2 hanya mengirim NAMA FILE ke GPT-4o --
["exec-adc_input-8eda9a2b-....jpg", ...] -- yaitu UUID tanpa makna. Model tidak
pernah melihat isi gambar, jadi tren, judul, dan naskah yang dihasilkan mustahil
nyambung dengan bahan user. Itu sumber utama "konten tidak sesuai bahan".

Semua pengolahan gambar memakai ffmpeg yang memang sudah jadi dependensi render,
sehingga tidak perlu menambah Pillow/opencv.

Gambar diperkecil dulu (sisi terpanjang VISION_MAX_DIM) sebelum dikirim: biaya
token gambar naik bersama ukurannya, dan untuk menilai "ini foto apa" resolusi
penuh tidak memberi manfaat tambahan.
"""

import base64
import os
import subprocess

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}

VISION_MAX_DIM = int(os.getenv("VISION_MAX_DIM", "768"))
VISION_MAX_ASSETS = int(os.getenv("VISION_MAX_ASSETS", "6"))
VISION_TIMEOUT_SECONDS = int(os.getenv("VISION_TIMEOUT_SECONDS", "30"))

# Perkecil agar sisi terpanjang <= VISION_MAX_DIM, TANPA memperbesar gambar kecil.
_SCALE = (
    f"scale='if(gt(iw,ih),min({VISION_MAX_DIM},iw),-2)'"
    f":'if(gt(iw,ih),-2,min({VISION_MAX_DIM},ih))'"
)


def _jpeg_bytes(path):
    """Satu frame JPEG kecil dari gambar ATAU video, lewat ffmpeg ke stdout.

    Untuk video diambil frame pada detik ke-1 (bukan 0) karena frame pertama
    sering hitam/transisi. Kalau video lebih pendek dari 1 detik, ffmpeg gagal
    dan kita coba lagi dari awal.
    """
    ext = os.path.splitext(path)[1].lower()

    def jalankan(args):
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", *args, "-vframes", "1", "-vf", _SCALE,
             "-f", "image2", "-vcodec", "mjpeg", "-"],
            capture_output=True, timeout=VISION_TIMEOUT_SECONDS,
        )
        return proc.stdout if proc.returncode == 0 and proc.stdout else None

    if ext in VIDEO_EXTENSIONS:
        return jalankan(["-ss", "1", "-i", path]) or jalankan(["-i", path])
    return jalankan(["-i", path])


def build_image_parts(paths, *, max_assets=None):
    """Ubah daftar path bahan jadi bagian pesan 'image_url' untuk API multimodal.

    Bahan yang gagal diproses DILEWATI dengan peringatan, bukan menggagalkan run:
    lebih baik brief dibuat dari 3 dari 4 gambar daripada pipeline mati total.
    Pemanggil bisa memeriksa panjang hasilnya untuk tahu berapa yang berhasil.
    """
    batas = VISION_MAX_ASSETS if max_assets is None else max_assets
    dipakai = paths[:batas]
    if len(paths) > batas:
        print(f"[info] vision: {len(paths)} bahan, {batas} pertama yang dikirim ke model.")

    parts = []
    for path in dipakai:
        ext = os.path.splitext(path)[1].lower()
        if ext not in IMAGE_EXTENSIONS and ext not in VIDEO_EXTENSIONS:
            print(f"[warn] vision: ekstensi tidak didukung, dilewati: {path}")
            continue
        try:
            data = _jpeg_bytes(path)
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[warn] vision: gagal memproses {os.path.basename(path)}: {e}")
            continue
        if not data:
            print(f"[warn] vision: tidak ada frame yang bisa diambil dari {os.path.basename(path)}")
            continue
        b64 = base64.b64encode(data).decode("ascii")
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    return parts
