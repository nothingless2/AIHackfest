"""Mengenali suasana (mood) sebuah lagu dari isinya, bukan dari nama berkas.

Tiga ukuran, semuanya dari sinyal, dihitung dengan numpy (tanpa librosa/essentia --
terlalu berat untuk VPS ini):

- tempo (BPM): autokorelasi envelope onset (spectral flux) pada 60-180 BPM
- energi: RMS keseluruhan (dBFS)
- kecerahan: spectral centroid rata-rata (Hz), berbobot energi

KODE yang mengukur dan memberi label; LLM tidak dilibatkan (aturan #5 CLAUDE.md).

KEJUJURAN TENTANG AMBANG: ukuran tempo/energi/kecerahan diverifikasi terhadap sinyal
sintetis berparameter diketahui (klik 80/100/120/140 BPM, nada rendah vs derau terang).
Ambang label (`AMBANG`) adalah HEURISTIK -- tidak ada kumpulan lagu berlabel di mesin ini
untuk mengkalibrasinya. Karena itu label hanya dipakai untuk (a) memberi tahu user dan
(b) mencocokkan permintaan mood ke pustaka SETELAH nama berkas tidak cocok. Tempo yang
tidak yakin (`tempo_yakin=False`) tidak pernah dipakai untuk memberi label "energik".
"""

import hashlib
import json
import os
import subprocess

import numpy as np

SR = 22050
HOP = 512
NFFT = 1024
MAKS_DETIK = 90
BPM_MIN, BPM_MAX = 60, 180

# Ambang label. Heuristik (lihat catatan modul). Diurutkan dari yang paling khas.
AMBANG = {
    "tenang_tempo_maks": 95,        # BPM di bawah ini + terang rendah -> tenang
    "tenang_terang_maks": 2200,     # Hz
    "energik_tempo_min": 125,       # BPM
    "energik_energi_min": -22.0,    # dBFS RMS
    "santai_tempo_maks": 110,
}
MOODS = ("tenang", "santai", "upbeat", "energik")


class MoodError(RuntimeError):
    """Berkas tidak bisa didekode/dianalisis."""


def _decode(path, maks_detik=MAKS_DETIK):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-t", str(maks_detik), "-ac", "1",
         "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, timeout=120,
    )
    if r.returncode != 0 or not r.stdout:
        raise MoodError(f"tidak bisa mendekode audio: {r.stderr.decode(errors='replace')[-200:]}")
    x = np.frombuffer(r.stdout, dtype=np.float32)
    if len(x) < SR * 3:
        raise MoodError("audio terlalu pendek untuk dianalisis (< 3 detik)")
    return x


def _spektrum(x):
    """Magnitudo STFT (frame x bin), jendela Hann."""
    n = 1 + (len(x) - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    return np.abs(np.fft.rfft(x[idx] * np.hanning(NFFT), axis=1))


# Kriteria "yakin ada denyut", dikalibrasi pada kasus terukur (bukan tebakan):
#   FLUX_REL  = rata-rata spectral flux / rata-rata spektrum log ("seberapa banyak isi
#               spektrum yang BARU tiap frame"):
#     nada steady 0,005 | lagu demo drone 0,022 | derau 0,096 | beat berderau 0,137-0,65 |
#     lagu lofi asli 0,199
#   SKOR      = puncak skor harmonik autokorelasi: derau 0,034 | beat 0,56-0,74 | lofi asli 0,66
# Ambang 0,05 dan 0,25 berada di celah antar-kelompok. Metrik "kontras persentil" yang
# dicoba lebih dulu DIBUANG: nada steady (4,19) mengalahkan beat berderau (1,4).
FLUX_REL_MIN = 0.05
SKOR_MIN = 0.25


def tempo(mag):
    """(bpm, yakin).

    Envelope onset = spectral flux positif pada magnitudo log. Tempo dipilih lewat SKOR
    HARMONIK: jumlah autokorelasi pada 1x, 2x, 3x, 4x lag. Autokorelasi biasa tidak bisa
    membedakan tempo asli dari setengahnya (klik 120 BPM terbaca 60 -- terukur); skor
    harmonik menang untuk periode asli karena puncak kelipatannya juga ikut dihitung.

    `yakin` butuh dua hal: isi spektrum cukup sering BARU (FLUX_REL_MIN) DAN puncak skor
    periodisitas cukup tinggi (SKOR_MIN). Nada steady punya autokorelasi periodik semu
    dari hop STFT -- tanpa syarat pertama ia dibaca 'yakin 112 BPM' (terukur)."""
    log = np.log1p(mag * 10)
    flux_mentah = np.maximum(np.diff(log, axis=0), 0).sum(axis=1)
    flux_rel = float(flux_mentah.mean() / max(float(log.sum(axis=1).mean()), 1e-9))
    flux = flux_mentah - flux_mentah.mean()
    if flux.std() < 1e-6:
        return None, False
    fps = SR / HOP
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    ac = ac / (ac[0] + 1e-12)
    bpms = np.arange(BPM_MIN, BPM_MAX + 0.5, 0.5)
    lag = fps * 60 / bpms
    if 4 * lag.max() >= len(ac):
        # rekaman terlalu pendek untuk 4 harmonik: pakai 2
        harmonik = (1, 2)
    else:
        harmonik = (1, 2, 3, 4)
    if 2 * lag.max() >= len(ac):
        return None, False
    idx = np.arange(len(ac))
    skor = sum(np.interp(k * lag, idx, ac) for k in harmonik) / len(harmonik)
    terbaik = int(np.argmax(skor))
    # Pemecah seri antar-oktaf: kalau tempo DUA KALI LIPAT juga mencetak >= 90% skor,
    # peristiwanya memang muncul pada jarak itu, jadi denyutnya yang lebih cepat.
    # (Tanpa ini klik 140 terbaca 70 dan 160 terbaca 80 -- terukur.)
    while True:
        ganda = np.searchsorted(bpms, bpms[terbaik] * 2 - 1e-9)
        if ganda < len(bpms) and abs(bpms[ganda] - 2 * bpms[terbaik]) <= 1.5 \
                and skor[ganda] >= 0.9 * skor[terbaik]:
            terbaik = int(ganda)
        else:
            break
    yakin = bool(flux_rel >= FLUX_REL_MIN and skor[terbaik] >= SKOR_MIN)
    return round(float(bpms[terbaik]), 1), yakin


def label_mood(bpm, yakin, energi_db, terang_hz):
    if yakin and bpm is not None:
        if bpm >= AMBANG["energik_tempo_min"] and energi_db >= AMBANG["energik_energi_min"]:
            return "energik"
        if bpm >= AMBANG["santai_tempo_maks"]:
            return "upbeat"
        if bpm < AMBANG["tenang_tempo_maks"] and terang_hz < AMBANG["tenang_terang_maks"]:
            return "tenang"
        return "santai"
    # Tanpa denyut yang jelas: hanya bisa menilai dari kecerahan/energi, dan tidak
    # pernah mengaku energik/upbeat (butuh tempo).
    return "tenang" if terang_hz < AMBANG["tenang_terang_maks"] else "santai"


def analisis(path):
    """{tempo_bpm, tempo_yakin, energi_db, kecerahan_hz, mood}. Melempar MoodError."""
    x = _decode(path)
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    energi_db = round(float(20 * np.log10(max(rms, 1e-9))), 1)
    mag = _spektrum(x)
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    bobot = mag.sum(axis=0)
    terang = float((freqs * bobot).sum() / max(bobot.sum(), 1e-9))
    bpm, yakin = tempo(mag)
    return {
        "tempo_bpm": bpm, "tempo_yakin": yakin, "energi_db": energi_db,
        "kecerahan_hz": round(terang), "mood": label_mood(bpm, yakin, energi_db, terang),
    }


def ringkas(a):
    """Kalimat untuk user: jujur soal tempo yang tidak yakin."""
    tempo_txt = (f"tempo ~{a['tempo_bpm']:.0f} BPM" if a.get("tempo_yakin")
                 else "tanpa denyut yang jelas")
    return f"{tempo_txt}, suasana {a['mood']}"


# ----------------------------------------------------------------- cache pustaka

def _cache_path():
    from common import STATE_DIR
    return os.path.join(STATE_DIR, "music_mood_cache.json")


def _kunci(path):
    st = os.stat(path)
    return hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()


def analisis_cached(path):
    """Seperti analisis(), tapi pustaka besar tidak dianalisis ulang tiap run.
    Kunci memuat ukuran+mtime, jadi berkas yang diganti otomatis dianalisis ulang."""
    cp = _cache_path()
    try:
        with open(cp, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    k = _kunci(path)
    if k in cache:
        return cache[k]
    hasil = analisis(path)
    cache[k] = hasil
    try:
        os.makedirs(os.path.dirname(cp), exist_ok=True)
        tmp = cp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(tmp, cp)
    except OSError:
        pass          # cache hanya optimasi; gagal menulis tidak boleh menggagalkan apa pun
    return hasil
