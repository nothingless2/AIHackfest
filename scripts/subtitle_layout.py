"""Tata letak subtitle karaoke: posisi PIKSEL tiap kata.

Kenapa dihitung sendiri, bukan diserahkan ke drawtext:

1. drawtext tidak bisa mewarnai SATU kata di dalam kalimat. Jalan keluarnya: tiap
   kata digambar sebagai filter sendiri di posisi yang sudah pasti, dengan warna
   yang berganti menurut waktu (fontcolor_expr).
2. Gaya lama menggambar teks KUMULATIF yang di-center ulang tiap ada kata baru,
   sehingga kata-kata yang sudah tampil bergeser ke kiri setiap kali kata berikutnya
   muncul. Di sini seluruh frasa berdiri diam sejak awal; hanya warnanya yang
   berpindah.

Dua fakta hasil pengukuran ffmpeg 4.4 yang menentukan rancangan ini:
- text_w ffmpeg cocok dengan Pillow dalam 1 piksel (298 vs 297 pada "Hg Hg"),
  jadi lebar/posisi kata bisa dihitung di Python.
- `y` pada drawtext adalah PUNCAK TINTA string itu, bukan garis dasar. Kata tanpa
  huruf tinggi ("cara") akan berdiri lebih rendah daripada kata bertinggi
  ("banyak") kalau digambar terpisah dengan y yang sama. Karena itu penyusun
  filter memakai y=<garis_dasar>-ascent -- terukur: tiga kata dengan tinggi tinta
  berbeda semuanya bertumpu tepat di garis dasar yang sama.
"""

import os
from functools import lru_cache

from PIL import ImageFont

# Sama dengan subtitle_geometry(): tinggi blok = jumlah baris * fontsize * 1,25.
LINE_PITCH_RATIO = 1.25
# Lebar teks maksimum sebagai fraksi lebar kanvas. 0,74 (dulu 0,88): tombol kanan TikTok/Reels
# mulai di ±88% lebar; teks 0,88 yang di-tengahkan + padding kotak membentang ±5-95% dan tertutup
# tombol (terdeteksi pemeriksa mutu pada render nyata 25 Sep, detik 9-13 & 37-38). Dengan 0,74
# kotak subtitle berada di ±11-89%.
MAX_WIDTH_RATIO = float(os.getenv("SUBTITLE_MAX_WIDTH", "0.74"))


@lru_cache(maxsize=16)
def _font(path, ukuran):
    return ImageFont.truetype(path, ukuran)


def text_width(font_path, fs, teks):
    """Lebar maju (advance) teks dalam piksel, memakai metrik font yang sama."""
    return _font(font_path, fs).getlength(teks)


def cap_height(font_path, fs):
    """Tinggi huruf kapital di atas garis dasar."""
    return -_font(font_path, fs).getbbox("H", anchor="ls")[1]


def descender(font_path, fs):
    """Kedalaman huruf turun ('g') di bawah garis dasar."""
    return _font(font_path, fs).getbbox("g", anchor="ls")[3]


def wrap_words(kata, font_path, fs, lebar_maks):
    """Bungkus daftar kata jadi baris berdasarkan LEBAR PIKSEL NYATA.

    Satu kata yang sendirian lebih lebar dari batas tetap ditaruh apa adanya di
    barisnya sendiri: tidak ada gunanya menolaknya, dan ia tidak akan muat di
    mana pun.
    """
    baris = [[]]
    for w in kata:
        kandidat = baris[-1] + [w]
        lebar = text_width(font_path, fs, " ".join(x["word"] for x in kandidat))
        if baris[-1] and lebar > lebar_maks:
            baris.append([w])
        else:
            baris[-1] = kandidat
    return baris


def layout_group(kata, *, font_path, fs, canvas_w, max_lines, y_top,
                 pad_x=24, pad_y=18, max_width_ratio=MAX_WIDTH_RATIO):
    """Tata letak satu tampilan subtitle. Return dict, atau None kalau butuh
    lebih dari `max_lines` baris (pemanggil lalu memecah kelompoknya).

    Jangkar VERTIKAL di bawah: garis dasar baris terakhir tetap di tempat yang
    sama untuk kelompok satu-baris maupun dua-baris, dan yang bertambah hanya
    ke atas. Gaya lama menempel di atas, sehingga teks satu baris melayang.

    Hasil:
      {"lines": [{"baseline": int,
                  "words": [{"word", "x", "start", "end"}]}],
       "box": (x, y, w, h),
       "cap": int}
    """
    baris = wrap_words(kata, font_path, fs, canvas_w * max_width_ratio)
    if len(baris) > max_lines:
        return None

    pitch = round(fs * LINE_PITCH_RATIO)
    cap = cap_height(font_path, fs)
    turun = descender(font_path, fs)
    dasar_akhir = round(y_top + cap + (max_lines - 1) * pitch)
    n = len(baris)

    hasil, kiri, kanan = [], canvas_w, 0.0
    for i, b in enumerate(baris):
        dasar = dasar_akhir - (n - 1 - i) * pitch
        lebar = text_width(font_path, fs, " ".join(w["word"] for w in b))
        x0 = (canvas_w - lebar) / 2.0
        kiri, kanan = min(kiri, x0), max(kanan, x0 + lebar)
        posisi = []
        for k, w in enumerate(b):
            awalan = " ".join(x["word"] for x in b[:k]) + (" " if k else "")
            posisi.append({
                "word": w["word"],
                "x": round(x0 + text_width(font_path, fs, awalan)),
                "start": w.get("start"), "end": w.get("end"),
            })
        hasil.append({"baseline": dasar, "words": posisi})

    atas = hasil[0]["baseline"] - cap - pad_y
    bawah = hasil[-1]["baseline"] + turun + pad_y
    x_kotak = max(0, round(kiri - pad_x))
    lebar_kotak = min(canvas_w, round(kanan + pad_x)) - x_kotak
    return {
        "lines": hasil,
        "box": (x_kotak, max(0, round(atas)), lebar_kotak, round(bawah - atas)),
        "cap": cap,
    }
