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

from visual_quality import kurangi

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


# Lembar kontak: satu gambar per VIDEO berisi KONTAK_FRAME momen (2x2). Asal: brief 24 Sep
# hanya melihat frame detik ke-1 tiap klip -- model tidak tahu apa yang terjadi sesudahnya,
# lalu naskahnya mendeskripsikan satu gambar diam ("hambar"). Tetap SATU gambar per klip,
# jadi jumlah gambar yang dikirim ke model tidak bertambah.
KONTAK_FRAME = 4
KONTAK_LAYOUT = "0_0|w0_0|0_h0|w0_h0"


def durasi_video(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=VISION_TIMEOUT_SECONDS)
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def titik_kontak(durasi, buruk=None, n=KONTAK_FRAME):
    """n waktu (detik) tersebar merata di bagian LAYAK klip.

    Bagian goyang/oleng (`buruk`, dari visual_quality) dilewati: renderer akan
    membuangnya, jadi model tidak boleh menulis naskah tentang isinya. Kalau seluruh
    klip buruk, dipakai seluruh klip (lebih baik melihat sesuatu daripada tidak sama sekali).
    """
    ujung = max(0.0, durasi - 0.05)
    layak = kurangi([(0.0, ujung)], buruk or [], min_keep=0.0) or [(0.0, ujung)]
    total = sum(b - a for a, b in layak)
    hasil = []
    for i in range(n):
        target = (i + 0.5) / n * total
        acc = 0.0
        for a, b in layak:
            if target <= acc + (b - a) + 1e-9:
                hasil.append(round(a + target - acc, 3))
                break
            acc += b - a
    return hasil


def _kontak_bytes(path, buruk=None):
    """JPEG lembar kontak 2x2 dari satu video, atau None bila gagal."""
    durasi = durasi_video(path)
    if not durasi or durasi <= 0:
        return None
    titik = titik_kontak(durasi, buruk)
    sisi = max(64, VISION_MAX_DIM // 2)
    skala = (f"scale='if(gt(iw,ih),min({sisi},iw),-2)':'if(gt(iw,ih),-2,min({sisi},ih))',setsar=1")
    args = ["ffmpeg", "-v", "error"]
    for t in titik:
        args += ["-ss", f"{t:.3f}", "-i", path]
    graf = ";".join(f"[{i}:v]{skala}[k{i}]" for i in range(len(titik)))
    graf += ";" + "".join(f"[k{i}]" for i in range(len(titik)))
    graf += f"xstack=inputs={len(titik)}:layout={KONTAK_LAYOUT}"
    args += ["-filter_complex", graf, "-frames:v", "1", "-f", "image2", "-vcodec", "mjpeg", "-"]
    proc = subprocess.run(args, capture_output=True, timeout=VISION_TIMEOUT_SECONDS)
    return proc.stdout if proc.returncode == 0 and proc.stdout else None


def build_image_parts(paths, *, max_assets=None, kontak=False, buruk=None):
    """Ubah daftar path bahan jadi bagian pesan 'image_url' untuk API multimodal.

    Bahan yang gagal diproses DILEWATI dengan peringatan, bukan menggagalkan run:
    lebih baik brief dibuat dari 3 dari 4 gambar daripada pipeline mati total.
    Pemanggil bisa memeriksa panjang hasilnya untuk tahu berapa yang berhasil.

    kontak=True: tiap VIDEO dikirim sebagai lembar kontak 2x2 (4 momen berurutan),
    melewati bagian tak layak di `buruk` ({path: [(a, b, alasan)]}). Gagal membuat
    lembar kontak -> jatuh ke satu frame, dengan peringatan.
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
            data = None
            if kontak and ext in VIDEO_EXTENSIONS:
                data = _kontak_bytes(path, (buruk or {}).get(path))
                if not data:
                    print(f"[warn] vision: lembar kontak gagal untuk {os.path.basename(path)}; "
                          "memakai satu frame.")
            data = data or _jpeg_bytes(path)
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
