"""Menentukan sumber audio video: suara asli user, atau voice-over AI.

Aturan yang diminta user:

    user minta "tanpa suara AI"  DAN  videonya ada ucapan  ->  pakai suara asli
    selain itu                                             ->  voice-over AI

Bagian "ada ucapan" TIDAK ditebak: pendeteksinya adalah hasil transkripsi yang
sudah kita jalankan. Kalau Whisper menghasilkan teks, berarti memang ada yang
bicara. Kalau tidak ada teks sama sekali (video bisu, musik saja, atau
transkripsi gagal), memakai "suara asli" akan menghasilkan video tanpa narasi —
jadi jatuh ke voice-over AI adalah perilaku yang benar, bukan kompromi.

Mode:
  ai       : voice-over AI, audio asli dibuang. Perilaku lama, tetap default.
  original : audio asli dipertahankan, tanpa TTS.
  auto     : ikut permintaan user; tanpa permintaan berarti "ai".
"""

import os

MODE_AI = "ai"
MODE_ORIGINAL = "original"
MODE_AUTO = "auto"
VALID_MODES = {MODE_AI, MODE_ORIGINAL, MODE_AUTO}

AUDIO_MODE_DEFAULT = (os.getenv("AUDIO_MODE") or MODE_AUTO).strip().lower()


def requested_mode():
    """Mode yang diminta untuk run ini: parameter tool, lalu default .env.

    Nilai tak dikenal diperlakukan sebagai 'auto' dengan peringatan — salah ketik
    di .env tidak boleh diam-diam mengubah perilaku audio.
    """
    diminta = (os.getenv("CONTENT_FACTORY_AUDIO_MODE") or "").strip().lower()
    if not diminta:
        diminta = AUDIO_MODE_DEFAULT
    if diminta not in VALID_MODES:
        print(f"[warn] AUDIO_MODE tidak dikenal ({diminta!r}), dianggap '{MODE_AUTO}'. "
              f"Pilihan: {', '.join(sorted(VALID_MODES))}.")
        return MODE_AUTO
    return diminta


def resolve_audio_mode(diminta, transkrip):
    """Putuskan mode final. Return (mode, alasan).

    `transkrip` adalah hasil transkripsi ({nama: ...}); kosong berarti tidak ada
    ucapan yang terdeteksi di bahan.
    """
    ada_ucapan = bool(transkrip)

    if diminta == MODE_ORIGINAL:
        if ada_ucapan:
            return MODE_ORIGINAL, "user minta tanpa voice-over AI dan ada ucapan di bahan"
        return MODE_AI, (
            "user minta tanpa voice-over AI, TAPI tidak ada ucapan yang terdeteksi "
            "di bahan — memakai voice-over AI supaya videonya tidak sunyi"
        )

    if diminta == MODE_AI:
        return MODE_AI, "user meminta voice-over AI"

    return MODE_AI, "tidak ada permintaan khusus — voice-over AI (default)"
