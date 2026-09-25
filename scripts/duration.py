"""Durasi video yang diminta user: parsing, penjepitan, dan target jumlah kata.

Modul ringan (hanya stdlib) supaya titik masuk bisa memvalidasi SEBELUM lock
render dan sebelum satu pun panggilan LLM -- sama alasannya dengan canvas.py.

Dua jalur deteksi, sengaja tidak cuma satu:
- UTAMA: flag `--duration-seconds` hermes_render (diisi agent dari jawaban user),
  diteruskan sebagai env CONTENT_FACTORY_DURATION. Model jauh lebih andal mengekstrak
  angka dari kalimat Bahasa Indonesia daripada regex.
- CADANGAN: regex atas CONTENT_FACTORY_USER_CONTEXT, untuk kasus agent lupa mengisi flag.

Di luar rentang TIDAK ditolak melainkan dijepit ke batas terdekat, dan nilai
yang dipakai dikembalikan sebagai pesan supaya user diberi tahu -- menolak
permintaan "90 detik" mentah-mentah lebih buruk daripada membuat 60 detik dan
mengatakannya.
"""

import os
import re

DURATION_MIN = int(os.getenv("DURATION_MIN", "10"))
DURATION_MAX = int(os.getenv("DURATION_MAX", "60"))
DURATION_TOLERANCE = float(os.getenv("DURATION_TOLERANCE", "0.20"))

# Rentang bawaan kalau user tidak meminta apa-apa: PERSIS seperti perilaku lama,
# supaya menambah fitur ini tidak diam-diam mengubah video orang yang tidak
# meminta durasi tertentu.
DEFAULT_RANGE = (20, 35)
DEFAULT_WORDS = (55, 95)   # kalimat prompt lama, dipertahankan kata per kata

# 2,7 kata/detik diturunkan dari angka yang sudah dipakai prompt lama
# (55-95 kata untuk 20-35 detik), bukan dikarang baru.
WORDS_PER_SECOND = 2.7
# Prompt lama memberi rentang +-25% di sekitar angka tengah; dipertahankan.
WORD_SPREAD = 0.25

_POLA = re.compile(r"(\d{1,3})\s*(detik|dtk|second[s]?|sec|s)\b", re.IGNORECASE)


def parse_duration(teks):
    """Angka detik pertama yang disebut user, atau None.

    Sengaja TIDAK mencocokkan kata seperti "pendek", "singkat", atau "panjang":
    itu selera, bukan angka, dan menebaknya berarti membuat video dengan durasi
    yang tidak pernah diminta.
    """
    if not teks:
        return None
    m = _POLA.search(str(teks))
    return int(m.group(1)) if m else None


def clamp_duration(detik):
    """(detik_dipakai, pesan). `pesan` None kalau tidak ada penjepitan."""
    if detik is None:
        return None, None
    if detik < DURATION_MIN:
        return DURATION_MIN, (
            f"Durasi {detik} detik di bawah batas; dipakai {DURATION_MIN} detik.")
    if detik > DURATION_MAX:
        return DURATION_MAX, (
            f"Durasi {detik} detik di atas batas; dipakai {DURATION_MAX} detik.")
    return detik, None


def requested_duration(explicit=None, context=None):
    """Durasi yang diminta untuk run ini: (detik|None, pesan|None).

    `explicit` (parameter tool) menang atas regex konteks. Keduanya kosong =
    tidak ada permintaan = perilaku lama, TANPA pengecekan durasi apa pun
    setelah TTS dan tanpa panggilan LLM tambahan.
    """
    if explicit is None:
        explicit = os.getenv("CONTENT_FACTORY_DURATION") or None
    if context is None:
        context = os.getenv("CONTENT_FACTORY_USER_CONTEXT") or ""

    detik = None
    if explicit is not None:
        try:
            detik = int(float(str(explicit).strip()))
        except (TypeError, ValueError):
            detik = None
    if detik is None:
        detik = parse_duration(context)
    return clamp_duration(detik)


def word_target(detik=None):
    """(min_kata, max_kata) untuk durasi target.

    Tanpa target: kembalikan rentang lama persis (55-95 kata untuk 20-35 detik).
    """
    if not detik:
        return DEFAULT_WORDS
    tengah = detik * WORDS_PER_SECOND
    return max(5, int(tengah * (1 - WORD_SPREAD))), int(tengah * (1 + WORD_SPREAD))


def duration_text(detik=None):
    """Kalimat durasi untuk prompt brief."""
    kmin, kmax = word_target(detik)
    if not detik:
        a, b = DEFAULT_RANGE
        return f"{a}-{b} detik (kira-kira {kmin}-{kmax} kata)"
    return f"TEPAT sekitar {detik} detik (kira-kira {kmin}-{kmax} kata)"


def off_target(aktual, target, tolerance=None):
    """True kalau durasi nyata meleset melebihi toleransi relatif."""
    if not target or not aktual:
        return False
    tol = DURATION_TOLERANCE if tolerance is None else tolerance
    return abs(float(aktual) - float(target)) / float(target) > tol
