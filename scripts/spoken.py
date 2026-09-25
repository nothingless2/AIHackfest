"""Naskah TULIS vs naskah UCAP.

Sebabnya nyata, bukan teoretis: suara Indonesia edge-tts membaca "leads" menjadi
"lid". Memperbaikinya dengan mengubah `full_voice_over` berarti subtitle dan
caption ikut salah eja. Jadi brief menyimpan DUA versi:

- `full_voice_over`   : ejaan benar. Dipakai subtitle, caption, dan deskripsi.
- `voice_over_spoken` : versi untuk TTS. Kata asing ditulis fonetis Indonesia
                        ("leads" -> "liids"), angka dieja, singkatan dibuka.

Keduanya TIDAK boleh tertukar. Brief lama yang belum punya `voice_over_spoken`
tetap jalan: yang dibacakan jatuh kembali ke `full_voice_over`.

`SPOKEN_REWRITE=0` mematikan seluruh mekanisme ini -- disediakan karena ejaan
fonetis yang pas untuk edge-tts bisa terdengar aneh di mesin TTS lain.
"""

import os

SPOKEN_REWRITE = (os.getenv("SPOKEN_REWRITE") or "1").strip().lower() not in (
    "0", "false", "no", "off",
)


def spoken_text(data):
    """Naskah yang DIBACAKAN TTS untuk brief ini."""
    tulis = (data.get("full_voice_over") or "").strip()
    if not SPOKEN_REWRITE:
        return tulis
    return (data.get("voice_over_spoken") or "").strip() or tulis


def prompt_rule():
    """Aturan lafal untuk prompt brief; kosong kalau mekanismenya dimatikan."""
    if not SPOKEN_REWRITE:
        return ""
    return (
        '- "voice_over_spoken" adalah versi "full_voice_over" untuk DIBACAKAN mesin TTS '
        "Bahasa Indonesia. Isinya sama persis maknanya dan panjang katanya harus "
        "hampir sama, hanya ejaannya disesuaikan supaya terdengar benar:\n"
        '  kata asing ditulis fonetis Bahasa Indonesia ("leads" -> "liids", '
        '"content" -> "konten", "engagement" -> "engejmen"), angka dieja '
        '("2024" -> "dua ribu dua puluh empat"), singkatan dibuka '
        '("CTA" -> "si ti ei").\n'
        "  Pakai ejaan Latin yang wajar dibaca orang Indonesia, BUKAN IPA atau "
        "simbol fonetik.\n"
        '  "full_voice_over" sendiri TETAP ejaan yang benar — ia dipakai untuk '
        "subtitle dan caption."
    )
