"""Musik latar + auto-ducking.

Ducking dikerjakan `sidechaincompress`: level musik ditekan oleh trek UCAPAN
yang dipakai sebagai sidechain, lalu keduanya dicampur. Jadi musik otomatis
mengecil saat ada yang bicara dan kembali naik saat jeda -- tanpa perlu tahu
lebih dulu di detik berapa ucapannya, dan tanpa bergantung pada transkrip.

TRACK MUSIK TIDAK DISERTAKAN di repo ini, dan itu keputusan sadar: musik di
konten yang dipublikasikan butuh lisensi, dan menaruh berkas sembarangan di
sini berarti menyerahkan masalah hak cipta ke user tanpa dia tahu. Taruh
berkasmu sendiri di `assets/music/`.

Kalau tidak ada track sama sekali, atau mood yang diminta tidak ketemu, video
tetap dibuat TANPA musik dan alasannya dilaporkan -- musik itu hiasan, tidak
boleh menggagalkan render yang videonya sudah jadi.
"""

import os
import re
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MUSIC_DIR = os.getenv("MUSIC_DIR") or os.path.join(PROJECT_ROOT, "assets", "music")
MUSIC_ENABLED = (os.getenv("MUSIC_ENABLED") or "1").strip().lower() not in (
    "0", "false", "no", "off",
)

# Level musik RELATIF terhadap ucapan, bukan angka mati. Sebabnya terukur:
# dengan gain tetap 0.15, pada material nyata musik hanya mengubah level berkas
# 0,3 dB -- alias tidak terdengar sama sekali. Gain tetap tidak bisa benar untuk
# dua hal sekaligus: rekaman yang pelan dan rekaman yang keras.
#
# 10 dB di bawah ucapan. Angka ini DIUKUR, bukan selera: pada 18 dB musik hanya
# mengubah level berkas 0,2-0,5 dB pada material nyata -- tidak terdengar.
# Pada 10 dB musik hadir sebagai latar di jeda, lalu DUCKING yang menekannya
# ~15 dB saat ada yang bicara. Itulah pembagian kerja yang benar: level dasar
# mengurus "terdengar", ducking mengurus "tidak mengganggu".
MUSIC_BELOW_SPEECH_DB = float(os.getenv("MUSIC_BELOW_SPEECH_DB", "10"))
# Dipakai hanya kalau loudness tidak bisa diukur (video tanpa audio, ffmpeg
# tanpa ebur128). Bukan default yang diharapkan, melainkan jaring pengaman.
MUSIC_VOLUME = float(os.getenv("MUSIC_VOLUME", "0.15"))
# Batas kewarasan supaya track yang sangat pelan/keras tidak menghasilkan gain
# ekstrem kalau pengukurannya meleset.
# Batas atas 4.0, bukan 1.0: track yang direkam pelan (track demo di repo ini
# -35 LUFS) butuh dikuatkan untuk mencapai target, dan dengan batas 1.0 seluruh
# logika penyesuaian diam-diam mentok di situ. Puncaknya tetap dijaga limiter.
MUSIC_GAIN_MIN, MUSIC_GAIN_MAX = 0.01, 4.0

# Ambang rendah (0.02) supaya ucapan yang pelan pun tetap memicu ducking.
MUSIC_DUCK_THRESHOLD = float(os.getenv("MUSIC_DUCK_THRESHOLD", "0.02"))
MUSIC_DUCK_RATIO = float(os.getenv("MUSIC_DUCK_RATIO", "12"))
# Serang cepat (musik langsung turun begitu orang mulai bicara), lepas lambat
# (musik naik perlahan di jeda, tidak memompa di sela-sela kata).
MUSIC_DUCK_ATTACK = float(os.getenv("MUSIC_DUCK_ATTACK_MS", "20"))
MUSIC_DUCK_RELEASE = float(os.getenv("MUSIC_DUCK_RELEASE_MS", "600"))

# Penguatan sinyal pemicu. 8, bukan 1 (bawaan ffmpeg), dan angkanya DIUKUR:
#   ucapan -6 dBFS   -> level_sc=1 menurunkan musik  5,6 dB;  level_sc=8: 15,9 dB
#   ucapan -14 dBFS  -> level_sc=1 menurunkan musik  0,4 dB;  level_sc=8: 14,8 dB
# Dengan bawaan ffmpeg, ducking praktis tidak bekerja untuk orang yang bicara
# pelan -- persis kasus rekaman ponsel. 8 membuatnya konsisten apa pun level
# ucapannya.
MUSIC_DUCK_SIDECHAIN_GAIN = float(os.getenv("MUSIC_DUCK_SIDECHAIN_GAIN", "8"))
MUSIC_FADE = float(os.getenv("MUSIC_FADE_SECONDS", "1.5"))

# Batas puncak setelah pencampuran. BUKAN hiasan: ucapan yang sudah dekat skala
# penuh ditambah musik akan MELEWATI skala penuh, lalu ter-clip keras oleh
# encoder. Terukur pada sinyal uji 0 dBFS: ucapan turun 3 dB dan terdistorsi
# hanya karena musik ditambahkan. Limiter menurunkan gain secara halus alih-alih
# memotong gelombang. level=disabled wajib -- 'auto level' bawaan ffmpeg justru
# MENAIKKAN kembali level ke maksimum, kebalikan dari yang kita mau.
MUSIC_LIMIT = float(os.getenv("MUSIC_PEAK_LIMIT", "0.95"))

AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac", ".opus")


class MusicError(ValueError):
    """Mood diminta tapi tidak ada track yang cocok."""


def list_tracks(folder=None):
    """Semua track yang benar-benar ada, terurut supaya pemilihannya deterministik."""
    d = folder or MUSIC_DIR
    if not os.path.isdir(d):
        return []
    return sorted(
        os.path.join(d, n) for n in os.listdir(d)
        if os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS
    )


def pick_track(mood=None, folder=None, run_id=""):
    """Path track, atau None kalau memang tidak ada track sama sekali.

    `mood` dicocokkan sebagai substring nama berkas ("lofi", "akustik"), jadi
    pustakanya cukup diberi nama yang masuk akal tanpa perlu metadata.
    Mood diminta tapi tidak ada yang cocok -> MusicError, BUKAN diam-diam
    memakai track lain: musik yang salah suasana lebih buruk daripada tanpa musik.
    """
    tracks = list_tracks(folder)
    if not tracks:
        return None
    if mood:
        kata = str(mood).strip().lower()
        cocok = [t for t in tracks if kata in os.path.basename(t).lower()]
        if not cocok:
            raise MusicError(
                f"Tidak ada musik bernuansa {mood!r} di {folder or MUSIC_DIR}. "
                f"Tersedia: {', '.join(os.path.basename(t) for t in tracks[:8])}")
        tracks = cocok
    # Deterministik per run: run yang sama selalu memilih track yang sama
    # (bisa diulang kalau hasilnya perlu diperiksa), run berbeda bervariasi.
    return tracks[sum(ord(c) for c in str(run_id)) % len(tracks)]


def has_audio_stream(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            check=True, capture_output=True, text=True,
        )
        return bool(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False


def loudness(path):
    """Loudness terintegrasi (LUFS) berkas audio, atau None kalau tidak terukur.

    LUFS, bukan puncak: yang menentukan "terdengar seberapa keras" adalah
    loudness, dan itu yang perlu disamakan antara musik dan ucapan.
    """
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
    cocok = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", out.stderr)
    return float(cocok[-1]) if cocok else None


def auto_volume(video_path, track_path, *, below_db=None):
    """Gain linier untuk musik supaya ia duduk `below_db` di bawah ucapan.

    Gagal diukur -> MUSIC_VOLUME sebagai jaring pengaman, bukan diam-diam
    memakai gain penuh.
    """
    turun = MUSIC_BELOW_SPEECH_DB if below_db is None else below_db
    ucapan, musik = loudness(video_path), loudness(track_path)
    if ucapan is None or musik is None:
        return MUSIC_VOLUME
    gain_db = (ucapan - turun) - musik
    return max(MUSIC_GAIN_MIN, min(MUSIC_GAIN_MAX, 10 ** (gain_db / 20.0)))


def build_filter(durasi, *, punya_ucapan=True, volume=None, fade=None):
    """filter_complex untuk mencampur musik ke audio video.

    `punya_ucapan=False` (video memang tanpa trek audio): musik dipasang apa
    adanya tanpa sidechain, karena tidak ada yang bisa menekannya.
    """
    vol = MUSIC_VOLUME if volume is None else volume
    f = MUSIC_FADE if fade is None else fade
    f = max(0.0, min(f, max(0.0, durasi / 4)))

    bagian = [f"[1:a]volume={vol:.3f}"]
    if f > 0.05:
        bagian.append(f"afade=t=in:st=0:d={f:.2f}")
        bagian.append(f"afade=t=out:st={max(0.0, durasi - f):.2f}:d={f:.2f}")
    musik = ",".join(bagian) + "[m]"

    if not punya_ucapan:
        return f"{musik};[m]alimiter=limit={MUSIC_LIMIT}:level=disabled[aout]"

    return (
        f"{musik};"
        f"[m][0:a]sidechaincompress="
        f"threshold={MUSIC_DUCK_THRESHOLD}:ratio={MUSIC_DUCK_RATIO}:"
        f"attack={MUSIC_DUCK_ATTACK}:release={MUSIC_DUCK_RELEASE}:"
        f"level_sc={MUSIC_DUCK_SIDECHAIN_GAIN}[md];"
        # normalize=0 WAJIB: default amix membagi tiap input dengan jumlah input,
        # jadi suara asli video ikut turun separuh hanya karena musik ditambahkan.
        f"[md][0:a]amix=inputs=2:duration=shortest:dropout_transition=0:normalize=0[mix];"
        f"[mix]alimiter=limit={MUSIC_LIMIT}:level=disabled[aout]"
    )


def requested_mood():
    """Mood yang diminta user untuk run ini (parameter tool), atau None."""
    return (os.getenv("CONTENT_FACTORY_MUSIC_MOOD") or "").strip() or None


def music_wanted():
    """Apakah run ini memang ingin musik? Permintaan per-run menang atas default."""
    v = (os.getenv("CONTENT_FACTORY_MUSIC") or "").strip().lower()
    if v in ("0", "off", "no", "tanpa", "false"):
        return False
    if v in ("1", "on", "yes", "ya", "true"):
        return True
    return MUSIC_ENABLED
