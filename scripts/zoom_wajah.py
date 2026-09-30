"""Zoom punch-in halus ke wajah pada kata kunci (contoh video user 29 Sep, gambar 1).

Semua waktu berasal dari potongan caption berkata-kunci (caption_dinamis.potong): jendela zoom
dipilih KODE, bukan LLM. Pusat zoom = wajah bila terdeteksi, selain itu tengah atas bingkai
(kepala pembicara di video tegak jarang di tengah vertikal).
"""

import os

SKALA = float(os.getenv("ZOOM_SKALA", "1.10"))
NAIK = 0.25              # dtk: masuk zoom
TURUN = 0.25             # dtk: kembali
LAMA_MAKS = 1.5          # dtk ditahan (di luar naik/turun)
JARAK_MIN = 4.0          # dtk antar-mulai zoom
MAKS = 3


def aktif():
    if (os.getenv("ZOOM_WAJAH") or "1").strip().lower() in ("0", "off", "mati", "false"):
        return False
    return (os.getenv("SUBTITLE_STYLE") or "").strip().lower() == "dinamis"


def jadwal(potongan, durasi, hindari=(), jarak=JARAK_MIN, maks=MAKS):
    """[(mulai, selesai)] pada potongan berkata-kunci, berjarak, di luar `hindari` [(a,b)]."""
    hasil = []
    for p in potongan or []:
        if p.get("kunci") is None:
            continue
        a = float(p["mulai"])
        b = min(a + NAIK + LAMA_MAKS + TURUN, float(p["selesai"]) + TURUN, durasi)
        if b - a < NAIK + TURUN + 0.1:
            continue
        if any(a < y and b > x for x, y in hindari):
            continue
        if hasil and a - hasil[-1][0] < jarak:
            continue
        if len(hasil) >= maks:
            break
        hasil.append((round(a, 3), round(b, 3)))
    return hasil


def _pusat(kotak, lebar, tinggi):
    if kotak:
        x, y, w, h = kotak
        return x + w / 2, y + h / 2
    return lebar / 2, tinggi * 0.38


def filter_zoom(jendela, lebar, tinggi, kotak_wajah=None, skala=SKALA):
    """Rantai filter ffmpeg: skala membesar per frame lalu dipotong kembali ke ukuran kanvas,
    dipusatkan ke wajah. Di luar jendela skalanya TEPAT 1 (crop tidak mengubah apa pun)."""
    if not jendela:
        return None
    naik = "+".join(
        f"if(between(t,{a:.3f},{b:.3f}),"
        f"(min((t-{a:.3f})/{NAIK},1)-max((t-({b:.3f}-{TURUN}))/{TURUN},0))*{skala - 1:.4f},0)"
        for a, b in jendela)
    s = f"(1+{naik})"
    cx, cy = _pusat(kotak_wajah, lebar, tinggi)
    # Titik wajah tetap di tempatnya saat membesar: offset = (pusat * skala) - pusat, dijepit
    # supaya bingkai tidak keluar dari gambar yang diperbesar.
    x = f"clip(iw*{cx / lebar:.4f}-{lebar // 2},0,iw-{lebar})"
    y = f"clip(ih*{cy / tinggi:.4f}-{tinggi // 2},0,ih-{tinggi})"
    # Koma di dalam ekspresi WAJIB di-escape: ffmpeg memakai koma sebagai pemisah filter, jadi
    # "between(t,1,2)" tanpa escape memotong filter di tengah ("Error reinitializing filters").
    e = lambda t: t.replace(",", "\\,")      # noqa: E731
    return (f"scale=w=iw*{e(s)}:h=ih*{e(s)}:eval=frame,"
            f"crop={lebar}:{tinggi}:{e(x)}:{e(y)}")
