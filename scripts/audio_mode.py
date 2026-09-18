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

# DEFAULT: pakai suara asli video. Voice-over AI hanya kalau user memintanya,
# ATAU kalau bahan ternyata tidak ada ucapannya sama sekali (mis. hanya foto) --
# di situ "suara asli" berarti video sunyi, jadi jatuh ke AI adalah yang benar.
AUDIO_MODE_DEFAULT = (os.getenv("AUDIO_MODE") or MODE_ORIGINAL).strip().lower()


def mode_eksplisit():
    """True kalau mode datang dari permintaan user (parameter tool), bukan default."""
    return bool((os.getenv("CONTENT_FACTORY_AUDIO_MODE") or "").strip())


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


def resolve_audio_mode(diminta, transkrip, *, eksplisit=False):
    """Putuskan mode final. Return (mode, alasan).

    `transkrip` kosong berarti tidak ada ucapan yang terdeteksi di bahan.
    `eksplisit` menandai apakah mode datang dari permintaan user atau dari
    default — supaya alasannya jujur dan tidak mengaku "user minta" padahal tidak.
    """
    ada_ucapan = bool(transkrip)
    asal = "diminta user" if eksplisit else "default"

    if diminta == MODE_ORIGINAL:
        if ada_ucapan:
            return MODE_ORIGINAL, f"pakai suara asli video ({asal}); ada ucapan di bahan"
        return MODE_AI, (
            f"suara asli ({asal}), TAPI tidak ada ucapan yang terdeteksi di bahan — "
            "memakai voice-over AI supaya videonya tidak sunyi"
        )

    return MODE_AI, f"voice-over AI ({asal})"
