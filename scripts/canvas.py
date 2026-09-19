"""Rasio kanvas dan cara memuat bahan ke dalamnya.

Modul terpisah dan SENGAJA ringan (tanpa ffmpeg/TTS/moviepy) supaya titik masuk
pipeline bisa memvalidasi rasio & fit mode SEBELUM mengambil lock render dan
sebelum satu pun panggilan GPT-4o -- nilai yang salah ketik tidak boleh membakar
kredit lalu baru ditolak di tahap render.
"""

import os

ASPECT_PRESETS = {
    "9:16": (1080, 1920),   # Reels / TikTok / Shorts
    "1:1": (1080, 1080),    # feed Instagram
    "16:9": (1920, 1080),   # YouTube
}

# crop      : isi kanvas penuh, tepi terpotong (default, perilaku lama)
# blur      : seluruh frame dipertahankan, latar diisi versi blur gambar itu
# letterbox : seluruh frame dipertahankan, latar hitam
FIT_MODES = ("crop", "blur", "letterbox")

VIDEO_ASPECT = (os.getenv("VIDEO_ASPECT") or "9:16").strip()
FIT_MODE = (os.getenv("FIT_MODE") or "crop").strip().lower()


class CanvasError(ValueError):
    """Rasio atau fit mode tidak dikenal.

    Sengaja melempar, BUKAN diam-diam jatuh ke default: nilai salah ketik harus
    terlihat, bukan menghasilkan video berbentuk lain dari yang diminta.
    """


def resolve_canvas(aspect=None, fit=None):
    """(lebar, tinggi, fit_mode). Melempar CanvasError kalau tidak dikenal."""
    a = (aspect or VIDEO_ASPECT).strip()
    f = (fit or FIT_MODE).strip().lower()
    if a not in ASPECT_PRESETS:
        raise CanvasError(
            f"Rasio {a!r} tidak dikenal. Pilihan: {', '.join(ASPECT_PRESETS)}.")
    if f not in FIT_MODES:
        raise CanvasError(
            f"Fit mode {f!r} tidak dikenal. Pilihan: {', '.join(FIT_MODES)}.")
    w, h = ASPECT_PRESETS[a]
    return w, h, f
