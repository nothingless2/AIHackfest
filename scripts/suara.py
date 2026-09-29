"""Pembersih suara ucapan (mode suara asli): rekaman HP jadi lebih jernih, jeda lebih senyap.

Diukur 29 Sep pada 6 video HP user + bising pink sintetis (lantai di jeda yang DIKETAHUI, selisih
spektrum ucapan 300-3.400 Hz terhadap rekaman bersih):
- video HP user sudah cukup bersih (SNR 31-36 dB) -- peredam bising (afftdn) di audio bersih
  justru mengubah spektrum ucapan 1,1 dB, jadi HANYA dipasang bila SNR < SNR_BERISIK;
- afftdn (ffmpeg 4.4) mentok ±7 dB berapa pun nr-nya; ditambah gerbang lembut (agate, maks -12 dB,
  ambang dari level ucapan video ini): lantai jeda -44,4 -> -61,8 dB, spektrum ucapan membaik
  (4,95 -> 4,33 dB dari bersih); di audio bersih gerbang mengubah ucapan hanya 0,05 dB;
- kompresor TANPA makeup: makeup menaikkan bising di jeda (SNR -3 dB). Kenyaringan akhir diurus
  pemeriksa mutu (loudnorm).
"""

import os
import subprocess

import numpy as np

SNR_BERISIK = float(os.getenv("SUARA_SNR_BERISIK", "25"))   # dB; di bawah ini peredam dipasang
GERBANG_DI_BAWAH_UCAPAN = 12.0                              # dB di bawah level ucapan (p90)
GERBANG_RANGE = 0.25                                        # maks redaman gerbang = -12 dB


def aktif():
    return (os.getenv("BERSIH_SUARA") or "1").strip().lower() not in ("0", "off", "mati", "false")


def ukur(path):
    """{"ucapan": dB, "lantai": dB, "snr": dB} dari RMS jendela 50 ms (p90 = ucapan, p10 = lantai).
    Persentil, bukan detektor jeda: di audio berisik detektor jeda tidak menemukan jeda sama sekali
    (terukur). None bila tak terbaca."""
    try:
        out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                              "-f", "s16le", "-"], capture_output=True, timeout=120).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    x = np.frombuffer(out, np.int16).astype(float) / 32768
    w = 800
    k = len(x) // w
    if k < 20:
        return None
    r = 20 * np.log10(np.sqrt((x[:k * w].reshape(k, w) ** 2).mean(1)) + 1e-9)
    ucapan, lantai = float(np.percentile(r, 90)), float(np.percentile(r, 10))
    return {"ucapan": round(ucapan, 1), "lantai": round(lantai, 1), "snr": round(ucapan - lantai, 1)}


def rantai(ukuran):
    """(filter audio, peredam_dipasang)."""
    bagian = ["highpass=f=80"]
    peredam = ukuran["snr"] < SNR_BERISIK
    if peredam:
        bagian.append("afftdn=nr=30:nf=-35:tn=1")
    ambang = 10 ** ((ukuran["ucapan"] - GERBANG_DI_BAWAH_UCAPAN) / 20)
    bagian.append(f"agate=threshold={max(0.001, min(0.3, ambang)):.4f}:ratio=2:range={GERBANG_RANGE}"
                  ":attack=5:release=150")
    bagian.append("equalizer=f=3200:t=q:w=1.0:g=2")
    bagian.append("acompressor=threshold=-24dB:ratio=2.5:attack=10:release=200:makeup=1")
    return ",".join(bagian), peredam


def bersihkan(video_masuk, video_keluar):
    """Satu pass audio (video di-copy). Return info untuk render_status; melempar bila ffmpeg gagal."""
    sebelum = ukur(video_masuk)
    if sebelum is None:
        return {"dipakai": False, "alasan": "audio tidak terbaca"}
    af, peredam = rantai(sebelum)
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video_masuk), "-af", af, "-c:v", "copy",
                        "-c:a", "aac", "-ar", "44100", "-ac", "2", str(video_keluar)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"pembersih suara gagal: {r.stderr[-300:]}")
    sesudah = ukur(video_keluar) or {}
    return {"dipakai": True, "peredam_bising": peredam, "snr_sebelum": sebelum["snr"],
            "snr_sesudah": sesudah.get("snr"),
            "jeda_lebih_senyap_db": round(sebelum["lantai"] - sesudah["lantai"], 1) if sesudah else None}
