"""Parameter gaya editing tambahan: filter warna, speed ramp, zoom otomatis.

Modul terpisah dan ringan (tanpa ffmpeg) supaya nilainya bisa divalidasi
SEBELUM lock render dan sebelum panggilan LLM -- pola yang sama dengan
canvas.py. Nilai salah ketik harus terlihat, bukan diam-diam jatuh ke
default atau diabaikan.
"""

import os

# Filter warna: chain filter ffmpeg (eq/colorbalance/hue), TIDAK mengubah
# durasi atau stream audio -- aman dipakai di mode audio apa pun.
COLOR_FILTERS = {
    "natural": "eq=contrast=1.05:saturation=1.12:brightness=0.01",
    "vivid": "eq=contrast=1.15:saturation=1.4:brightness=0.02,unsharp=5:5:0.5",
    "warm": "eq=contrast=1.05:saturation=1.15:brightness=0.01,"
            "colorbalance=rs=0.08:gs=0.02:bs=-0.08",
    "cool": "eq=contrast=1.05:saturation=1.1,"
            "colorbalance=rs=-0.06:gs=0.0:bs=0.08",
    "bw": "hue=s=0,eq=contrast=1.12",
}

SPEED_MIN, SPEED_MAX = 0.5, 2.0


class StyleError(ValueError):
    """Parameter gaya editing tidak dikenal atau di luar jangkauan.

    Sengaja melempar -- lihat canvas.CanvasError untuk alasannya: nilai salah
    ketik harus ketahuan sebelum render, bukan diam-diam diabaikan atau
    dipaksakan ke ffmpeg lalu gagal di tengah render.
    """


def resolve_color_filter(nama=None):
    """(nama_ternormalisasi, chain_filter_ffmpeg) atau (None, None) kalau tidak diminta."""
    n = (nama if nama is not None else os.getenv("COLOR_FILTER", "")).strip().lower()
    if not n or n == "none":
        return None, None
    if n not in COLOR_FILTERS:
        raise StyleError(
            f"Filter warna {n!r} tidak dikenal. Pilihan: "
            f"{', '.join(sorted(COLOR_FILTERS))}, atau none."
        )
    return n, COLOR_FILTERS[n]


def resolve_speed_factor(nilai=None):
    """Kelipatan kecepatan klip. 1.0 = tidak diubah (default).

    HANYA berlaku aman untuk klip TANPA audio tersinkron (mode voice-over AI):
    lihat catatan di auto_render.render_from_agent_script -- mode audio asli/mute
    tetap menampilkan orang bicara, jadi mengubah kecepatan klipnya akan membuat
    gerak bibir lepas dari subtitle yang sudah dipatok ke transkrip asli.
    Pemanggil bertanggung jawab TIDAK memanggil ini di luar mode voice-over AI.
    """
    raw = nilai if nilai is not None else os.getenv("SPEED_FACTOR", "")
    raw = (str(raw) if raw is not None else "").strip()
    if not raw:
        return 1.0
    try:
        f = float(raw)
    except ValueError:
        raise StyleError(f"speedFactor {raw!r} bukan angka.")
    if not (SPEED_MIN <= f <= SPEED_MAX):
        raise StyleError(
            f"speedFactor {f} di luar jangkauan {SPEED_MIN}-{SPEED_MAX}."
        )
    return f


def auto_zoom_enabled(nilai=None):
    """True kalau efek Ken Burns (zoom perlahan) dipakai untuk bahan GAMBAR.

    Hanya gambar -- lihat catatan di scale_crop_filter kenapa video tidak
    disentuh. Default False: tidak menyalakan efek visual yang tidak diminta
    user untuk video yang sudah ada.
    """
    raw = nilai if nilai is not None else os.getenv("AUTO_ZOOM", "")
    return str(raw).strip().lower() in ("1", "true", "on", "ya", "yes")


# Posisi & font TEKS ON-SCREEN (teks tulisan/statis). Subtitle dari ucapan (karaoke)
# TIDAK ikut: ia tetap di sepertiga bawah dengan gayanya sendiri (subtitleStyle).
# Font dipilih dari daftar KECIL yang sudah diuji, dengan nama Indonesia -- bukan nama
# font bebas dari model, yang bisa jatuh diam-diam ke font lain (fc-match selalu
# mengembalikan sesuatu).
TEXT_POSITIONS = ("atas", "tengah", "bawah")
TEXT_FONTS = {
    "standar": {},                                   # DejaVu Sans Bold (bawaan renderer)
    "tegas": {"file": "Montserrat-ExtraBold.ttf"},
    "modern": {"file": "BebasNeue-Regular.ttf"},
    "elegan": {"file": "PlayfairDisplay-Variable.ttf"},
    "santai": {"file": "Pacifico-Regular.ttf"},
    "bersih": {"family": "Inter"},
}


def resolve_text_position(nilai=None):
    n = (nilai if nilai is not None else os.getenv("TEXT_POSITION", "")).strip().lower()
    if not n:
        return "bawah"
    if n not in TEXT_POSITIONS:
        raise StyleError(f"Posisi teks {n!r} tidak dikenal. Pilihan: {', '.join(TEXT_POSITIONS)}.")
    return n


def resolve_text_font(nilai=None):
    """(nama, spesifikasi). Kosong -> ("standar", {})."""
    n = (nilai if nilai is not None else os.getenv("TEXT_FONT", "")).strip().lower()
    if not n:
        return "standar", TEXT_FONTS["standar"]
    if n not in TEXT_FONTS:
        raise StyleError(f"Font teks {n!r} tidak dikenal. Pilihan: {', '.join(TEXT_FONTS)}.")
    return n, TEXT_FONTS[n]
