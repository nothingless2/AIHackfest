"""Menentukan sumber audio video: suara asli user, atau voice-over AI.

Aturan yang diminta user:

    user minta "tanpa suara AI"  DAN  videonya ada ucapan  ->  pakai suara asli
    selain itu                                             ->  voice-over AI

Bagian "ada ucapan" TIDAK ditebak: pendeteksinya adalah hasil transkripsi yang
sudah kita jalankan. Kalau Whisper menghasilkan teks, berarti memang ada yang
bicara. Kalau Whisper BERHASIL berjalan dan tidak menemukan ucapan (video bisu,
musik saja, hanya foto), jatuh ke voice-over AI adalah perilaku yang benar.

TAPI "transkripsi GAGAL" bukan "tidak ada ucapan", dan versi awal file ini
menyamakannya. Akibat nyata (19 Sep): saldo Whisper habis -> 0 transkrip ->
disimpulkan "tidak ada ucapan" -> naskah dikarang dari gambar saja ("Halo
semuanya! Aku di sini dengan energi positif...") lalu suara AI ditempel di atas
video user yang sebenarnya berbicara. Sekarang kegagalan menghentikan run
dengan pesan yang jelas (TranscriptionUnavailable), sebelum ada panggilan LLM
berbiaya dan sebelum suara user diganti.

Mode:
  ai       : voice-over AI, audio asli dibuang. Perilaku lama, tetap default.
  original : audio asli dipertahankan, tanpa TTS.
  auto     : ikut permintaan user; tanpa permintaan berarti "ai".
"""

import os

# Alasan kegagalan yang PASTI berarti "memang tidak ada ucapan". Selain ini
# (kuota habis, timeout, layanan mati, akses ditolak, audio gagal diekstrak)
# transkrip TIDAK lengkap, dan tidak ada yang tahu apakah ada yang bicara.
ALASAN_PASTI_TANPA_UCAPAN = {"tanpa_audio", "tanpa_ucapan"}


# Rata-rata volume (dB) di atas ini = ADA SUARA (keramaian, musik, mesin), bukan hening.
# Diukur: klip suasana food court -15 dB dan -27 dB; ruangan sepi ~ -50 dB ke bawah.
AMBANG_ADA_SUARA_DB = float(os.getenv("AUDIO_ADA_SUARA_DB", "-45"))


def bahan_punya_suara(paths):
    """True kalau ada bahan VIDEO yang terdengar (rata-rata volume di atas ambang).

    Hanya video dengan trek audio yang bisa terdengar; gambar tidak pernah.
    Return None kalau tidak ada satu pun bahan video (tidak ada yang bisa diukur),
    supaya pemanggil membedakan "hening" (False) dari "tidak bisa diukur" (None).
    """
    import re
    import subprocess

    ada_video, terdengar = False, False
    for p in paths:
        if os.path.splitext(p)[1].lower() not in (".mp4", ".mov", ".mkv", ".avi", ".webm"):
            continue
        ada_video = True
        try:
            o = subprocess.run(["ffmpeg", "-hide_banner", "-i", p, "-af", "volumedetect",
                                "-vn", "-f", "null", "-"], capture_output=True, text=True,
                               timeout=60)
        except (subprocess.SubprocessError, OSError):
            continue
        m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", o.stderr)
        if m and float(m.group(1)) > AMBANG_ADA_SUARA_DB:
            terdengar = True
    return terdengar if ada_video else None


class TranscriptionUnavailable(RuntimeError):
    """Suara asli diminta, tapi transkripsi gagal sehingga tidak diketahui apakah
    ada ucapan. Pesannya ditulis untuk DIBACA USER (ikut tampil di chat)."""


MODE_AI = "ai"
MODE_ORIGINAL = "original"
# Suara asli video DIBUANG seluruhnya; yang terdengar hanya musik (bawaan pustaka atau
# berkas yang diunggah user). Transkripsi tetap dipakai untuk subtitle dan seleksi
# konten -- membisukan video tidak berarti menghapus apa yang diucapkan dari layar.
MODE_MUTE = "mute"
MODE_AUTO = "auto"
VALID_MODES = {MODE_AI, MODE_ORIGINAL, MODE_MUTE, MODE_AUTO}

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


def resolve_audio_mode(diminta, transkrip, *, eksplisit=False, gagal=None, ada_suara=None):
    """Putuskan mode final. Return (mode, alasan).

    `transkrip` kosong berarti tidak ada ucapan yang terdeteksi di bahan.
    `eksplisit` menandai apakah mode datang dari permintaan user atau dari
    default — supaya alasannya jujur dan tidak mengaku "user minta" padahal tidak.
    `gagal`: {nama: kode_alasan} dari transcribe_assets_report. Kalau ada bahan
    yang gagal karena SEBAB SELAIN "memang tanpa ucapan" dan tidak satu pun bahan
    berhasil ditranskrip, melempar TranscriptionUnavailable -- bukan diam-diam
    memakai voice-over AI. (Kalau sebagian berhasil, ucapan sudah terbukti ada
    dan run lanjut dengan suara asli; bahan yang gagal dilaporkan di caption.)
    """
    ada_ucapan = bool(transkrip)
    asal = "diminta user" if eksplisit else "default"

    if diminta == MODE_MUTE:
        # Tidak ada yang bisa gagal di sini: suara asli memang tidak dipakai. Kegagalan
        # transkripsi hanya berarti tanpa subtitle, bukan run yang dihentikan.
        return MODE_MUTE, f"video dibisukan ({asal}); suara asli tidak dipakai"

    if diminta == MODE_ORIGINAL and not ada_ucapan and gagal:
        tak_pasti = {n: k for n, k in gagal.items() if k not in ALASAN_PASTI_TANPA_UCAPAN}
        if tak_pasti:
            from transcribe import ALASAN_TEKS
            sebab = ", ".join(sorted({ALASAN_TEKS.get(k, k) for k in tak_pasti.values()}))
            raise TranscriptionUnavailable(
                f"Transkripsi gagal ({sebab}), jadi saya tidak bisa mendengar isi videomu. "
                "Video TIDAK dibuat: tanpa transkrip, naskah hanya bisa dikarang dari gambar "
                "dan suaramu akan diganti suara AI. Perbaiki layanan transkripsi lalu kirim ulang."
            )

    if diminta == MODE_ORIGINAL:
        if ada_ucapan:
            return MODE_ORIGINAL, f"pakai suara asli video ({asal}); ada ucapan di bahan"
        if ada_suara:
            # Tidak ada ucapan, tapi bahan BERSUARA (keramaian, musik, suasana). Versi
            # awal mengganti ini dengan voice-over AI "supaya tidak sunyi" -- padahal
            # videonya tidak sunyi, dan default user adalah suara asli tanpa AI.
            return MODE_ORIGINAL, (
                f"suara asli ({asal}); tidak ada ucapan, tapi bahan memiliki suara "
                "suasana/musik — dipertahankan tanpa subtitle dan tanpa voice-over AI")
        return MODE_AI, (
            f"suara asli ({asal}), TAPI tidak ada ucapan yang terdeteksi di bahan — "
            "memakai voice-over AI supaya videonya tidak sunyi"
        )

    return MODE_AI, f"voice-over AI ({asal})"
