"""Cover/thumbnail video: satu frame dari hasil akhir + versi kecil untuk Telegram.

Modul terpisah dan ringan (hanya stdlib) karena dipakai dari dua sisi yang tidak
boleh saling mengimpor: `auto_render` (membuat coverny) dan `common.send_video`
(mengirimkannya).

BATAS TELEGRAM — DIVERIFIKASI dari dokumentasi resmi Bot API
(https://core.telegram.org/bots/api, diambil 2026-09-19), bukan dari ingatan:

    "The thumbnail should be in JPEG format and less than 200 kB in size.
     A thumbnail's width and height should not exceed 320. Ignored if the file
     is not uploaded using multipart/form-data. Thumbnails can't be reused and
     can be only uploaded as a new file..."

Empat kalimat itu langsung jadi empat keputusan di kode ini dan di send_video():
- JPEG          -> ekstraksi menulis .jpg, bukan .png
- < 200 kB      -> telegram_thumb() MENGUKUR hasilnya, tidak sekadar berharap
- sisi <= 320   -> diperkecil, sementara JPG ukuran kanvas disimpan sebagai artefak
- multipart     -> dilampirkan sebagai file di `files=`, bukan string di `data=`

TEMUAN DARI PENGIRIMAN NYATA (19 Sep 2026, bukan dari membaca kode):
untuk video MP4 kita, Telegram MENGABAIKAN thumbnail yang dikirim dan membuat
sendiri dari FRAME PERTAMA video. Diuji dua bentuk request -- field multipart
bernama `thumbnail` dan bentuk `attach://` yang didokumentasikan -- keduanya
menghasilkan thumbnail server 644 byte yang hitam, sementara cover kita 11 kB
dan berisi gambar yang benar. Itu cocok dengan kalimat pertama dokumentasi:
"can be ignored if thumbnail generation for the file is supported server-side".

Dua konsekuensi:
1. Yang benar-benar menentukan preview di Telegram adalah frame pertama video.
   Karena itu `fade_filters()` tidak lagi memasang fade masuk di segmen pertama
   (dulu frame 0 hitam, YAVG 16 -> preview hitam polos untuk SETIAP video).
   Setelah diperbaiki, thumbnail server berisi gambar asli (7.739 byte).
2. Cover tetap dibuat dan tetap dilampirkan: ia disimpan sebagai artefak di
   published/ (dipakai saat publikasi ke platform lain) dan lampirannya tidak
   merugikan kalau suatu saat Telegram menghormatinya.

Cover TIDAK PERNAH menggagalkan render atau pengiriman: semua fungsi di sini
mengembalikan None saat gagal, dan pemanggil melanjutkan tanpa cover.
"""

import os
import subprocess

# Angka dari kutipan dokumentasi di atas. "less than 200 kB" ditafsirkan ketat
# sebagai < 200*1024 byte, dan kita menyisakan sedikit ruang di bawahnya.
THUMB_MAX_SIDE = 320
THUMB_MAX_BYTES = 200 * 1024

# Kualitas JPEG ffmpeg (-q:v): 2 = terbaik, makin besar makin kecil filenya.
# Dicoba berurutan sampai hasilnya muat di bawah batas.
_QUALITY_LADDER = (2, 5, 10, 20)

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


def telegram_thumb(src_path, out_path, max_side=THUMB_MAX_SIDE, max_bytes=THUMB_MAX_BYTES):
    """Versi kecil yang memenuhi batas sendVideo. Return out_path atau None.

    `force_original_aspect_ratio=decrease` memuat gambar ke dalam kotak
    max_side x max_side, jadi KEDUA sisi dijamin <= 320 apa pun rasio kanvasnya.

    Ukuran file diperiksa sungguhan, dan kalau masih terlalu besar kualitas
    diturunkan bertahap. Kalau bahkan kualitas terendah masih melewati batas,
    return None -- lebih baik mengirim video tanpa cover daripada request yang
    ditolak Telegram.
    """
    if not src_path or not os.path.exists(src_path):
        return None
    for q in _QUALITY_LADDER:
        ok = _ffmpeg(
            ["-i", src_path,
             "-vf", f"scale={max_side}:{max_side}:force_original_aspect_ratio=decrease",
             "-frames:v", "1", "-q:v", str(q), out_path],
            f"perkecil cover (q={q})",
        )
        if not ok or not os.path.exists(out_path):
            return None
        if os.path.getsize(out_path) < max_bytes:
            return out_path
    print(f"[warn] thumbnail: cover masih >= {max_bytes} byte, dikirim tanpa cover.")
    return None
