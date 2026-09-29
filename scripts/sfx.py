"""SFX pendek (pop, whoosh) di momen visual: kata kunci caption dinamis, kartu motion, cutaway.

Asal (27 Sep): contoh video user memakai SFX di tiap efek. Bunyi DISINTESIS di sini (numpy),
bukan diunduh: pustaka SFX daring (mis. remotion.media) tidak menyebut lisensinya, dan
berkas tanpa lisensi jelas tidak boleh masuk video user.

Level diukur dari kenyaringan audio video itu sendiri (pola music.auto_volume): SFX berada
di bawah ucapan, tidak menutupinya.
"""

import os
import subprocess
import wave

import numpy as np

RATE = 48000
JARAK_MIN = 0.6              # dtk antar SFX: yang lebih awal menang
# Pop lebih jarang dari whoosh: render nyata 27 Sep = 16 pop dalam 45 dtk (tiap ±2,8 dtk) terasa
# seperti ketukan terus-menerus. Pop hanya bila >= JARAK_POP dari pop sebelumnya.
JARAK_POP = float(os.getenv("SFX_JARAK_POP", "4.0"))
DI_BAWAH_UCAPAN_DB = float(os.getenv("SFX_BELOW_SPEECH_DB", "4"))   # puncak SFX vs LUFS ucapan
# Batas bawah kecil sekali: 0,05 (versi pertama) membuat SFX LEBIH KERAS dari ucapan pelan
# (terukur di tes: ucapan -30 dBFS, SFX -27 dBFS).
GAIN_MIN, GAIN_MAX = 0.002, 1.0


def aktif(gaya_subtitle=None):
    """SFX env: on/off. Bawaan: on hanya untuk gaya caption dinamis (tahap uji 27 Sep)."""
    v = (os.getenv("SFX") or "").strip().lower()
    if v in ("on", "1", "ya"):
        return True
    if v in ("off", "0", "mati"):
        return False
    return (gaya_subtitle or os.getenv("SUBTITLE_STYLE") or "").strip().lower() == "dinamis"


def _tulis(path, sinyal):
    sinyal = sinyal / (np.max(np.abs(sinyal)) or 1.0) * 0.89          # puncak -1 dBFS
    data = (np.clip(sinyal, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(data.tobytes())
    return path


def buat_pop(path):
    """Nada singkat menurun (±70 ms) dengan serangan cepat -- 'pop' kata kunci."""
    t = np.arange(int(RATE * 0.07)) / RATE
    frek = 900 * np.exp(-t * 28) + 220
    fase = 2 * np.pi * np.cumsum(frek) / RATE
    amp = np.minimum(1, t / 0.003) * np.exp(-t * 55)
    return _tulis(path, np.sin(fase) * amp)


def buat_whoosh(path, seed=7):
    """Derau yang disaring & menyapu naik-turun (±350 ms) -- 'whoosh' transisi."""
    n = int(RATE * 0.35)
    rng = np.random.default_rng(seed)
    derau = rng.standard_normal(n)
    # Low-pass satu kutub yang frekuensinya menyapu: terang di tengah, redup di ujung.
    t = np.linspace(0, 1, n)
    alfa = 0.04 + 0.5 * np.sin(np.pi * t) ** 2
    y = np.zeros(n)
    for i in range(1, n):
        y[i] = y[i - 1] + alfa[i] * (derau[i] - y[i - 1])
    y -= np.convolve(y, np.ones(64) / 64, mode="same")        # buang dengung rendah
    return _tulis(path, y * np.sin(np.pi * t) ** 1.5)


def jadwal(pop_detik=(), whoosh_detik=(), durasi=None):
    """[(detik, jenis)] terurut, berjarak >= JARAK_MIN (yang lebih awal menang), di dalam durasi."""
    semua = sorted([(float(d), "pop") for d in pop_detik] + [(float(d), "whoosh") for d in whoosh_detik])
    hasil = []
    for d, jenis in semua:
        if d < 0 or (durasi is not None and d > durasi - 0.1):
            continue
        if hasil and d - hasil[-1][0] < JARAK_MIN:
            continue
        pop_lalu = [x for x, j in hasil if j == "pop"]
        if jenis == "pop" and pop_lalu and d - pop_lalu[-1] < JARAK_POP:
            continue
        hasil.append((round(d, 3), jenis))
    return hasil


def _gain(video_path, puncak_sfx_dbfs=-1.0):
    from music import loudness
    ucapan = loudness(video_path)
    if ucapan is None:
        return 0.25
    gain_db = (ucapan - DI_BAWAH_UCAPAN_DB) - puncak_sfx_dbfs
    return max(GAIN_MIN, min(GAIN_MAX, 10 ** (gain_db / 20.0)))


def tambah(video_path, peristiwa, out_path, folder):
    """Campur SFX ke audio video (video di-copy). Return info untuk render_status."""
    if not peristiwa:
        return {"dipakai": False, "alasan": "tidak ada momen visual untuk SFX"}
    berkas = {"pop": buat_pop(os.path.join(folder, "_sfx_pop.wav")),
              "whoosh": buat_whoosh(os.path.join(folder, "_sfx_whoosh.wav"))}
    gain = _gain(video_path)
    args = ["ffmpeg", "-y", "-v", "error", "-i", video_path]
    rantai = []
    for i, (d, jenis) in enumerate(peristiwa, 1):
        args += ["-i", berkas[jenis]]
        ms = int(round(d * 1000))
        rantai.append(f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                      f"volume={gain:.3f},adelay={ms}|{ms}[s{i}]")
    masuk = "".join(f"[s{i}]" for i in range(1, len(peristiwa) + 1))
    rantai.append("[0:a]aformat=sample_rates=44100:channel_layouts=stereo[a0]")
    rantai.append(f"[a0]{masuk}amix=inputs={len(peristiwa) + 1}:duration=first:normalize=0[aout]")
    r = subprocess.run(args + ["-filter_complex", ";".join(rantai), "-map", "0:v", "-map", "[aout]",
                               "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-ac", "2", out_path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"SFX gagal dicampur: {r.stderr[-300:]}")
    return {"dipakai": True, "jumlah": len(peristiwa),
            "pop": sum(1 for _, j in peristiwa if j == "pop"),
            "whoosh": sum(1 for _, j in peristiwa if j == "whoosh"), "gain": round(gain, 3)}
