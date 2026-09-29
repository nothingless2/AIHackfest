"""SFX (scripts/sfx.py): bunyi sintesis di momen visual, di bawah level ucapan, tepat waktu."""

import os
import subprocess

import numpy as np
import pytest

import sfx


def _video(path, detik=3.0, db=-40):
    amp = 10 ** (db / 20)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=gray:size=160x284:rate=24:duration={detik}", "-f", "lavfi", "-i",
                    f"sine=frequency=150:duration={detik},volume={amp}", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-ac", "2",  # stereo = pipeline
                    "-shortest", str(path)],
                   check=True, capture_output=True)
    return str(path)


def _pcm(path, rate=16000):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate),
                          "-f", "s16le", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(out, np.int16).astype(float) / 32768


def _rms_per(x, rate=16000, win=0.01):
    n = int(rate * win)
    k = len(x) // n
    return np.sqrt((x[:k * n].reshape(k, n) ** 2).mean(axis=1))


def test_pop_jarang():
    j = sfx.jadwal(pop_detik=[1.0, 3.0, 5.5, 6.0], durasi=10.0)
    assert j == [(1.0, "pop"), (5.5, "pop")]


def test_jadwal_berjarak_dan_di_dalam_durasi():
    j = sfx.jadwal(pop_detik=[1.0, 1.3, 5.0], whoosh_detik=[0.0, 2.0, 9.95], durasi=10.0)
    assert j == [(0.0, "whoosh"), (1.0, "pop"), (2.0, "whoosh"), (5.0, "pop")]


def test_aktif_bawaan_hanya_gaya_dinamis(monkeypatch):
    monkeypatch.delenv("SFX", raising=False)
    monkeypatch.setenv("SUBTITLE_STYLE", "karaoke")
    assert not sfx.aktif()
    monkeypatch.setenv("SUBTITLE_STYLE", "dinamis")
    assert sfx.aktif()
    monkeypatch.setenv("SFX", "off")
    assert not sfx.aktif()


def test_bunyi_sintesis_pendek_dan_berpuncak_minus_1_dbfs(tmp_path):
    for buat, lama in ((sfx.buat_pop, 0.07), (sfx.buat_whoosh, 0.35)):
        x = _pcm(buat(str(tmp_path / f"{buat.__name__}.wav")), rate=48000)
        assert len(x) / 48000 == pytest.approx(lama, abs=0.01)
        assert 20 * np.log10(np.max(np.abs(x))) == pytest.approx(-1, abs=0.3)


def test_sfx_muncul_tepat_di_detiknya_dan_sisanya_utuh(tmp_path, monkeypatch):
    # Waktu diuji terpisah dari level (level: test_puncak_sfx_di_bawah_ucapan).
    monkeypatch.setattr(sfx, "_gain", lambda path: {"pop": 1.0, "whoosh": 1.0})
    v = _video(tmp_path / "v.mp4")
    out = str(tmp_path / "o.mp4")
    info = sfx.tambah(v, [(1.0, "pop"), (2.0, "whoosh")], out, str(tmp_path))
    assert info["dipakai"] and info["pop"] == 1 and info["whoosh"] == 1
    a, b = _rms_per(_pcm(v)), _rms_per(_pcm(out))
    n = min(len(a), len(b))
    naik = np.where(b[:n] > a[:n] * 4 + 1e-4)[0]
    assert naik.size, "kontrol: SFX harus terdeteksi"
    assert naik[0] * 0.01 == pytest.approx(1.0, abs=1 / 24), "onset pop ±1 frame"
    # Whoosh sengaja masuk berangsur (sapuan): mulai terdengar dalam 0,1 dtk, tidak sebelumnya.
    setelah_pop = [i * 0.01 for i in naik if i * 0.01 > 1.5]
    assert setelah_pop and 2.0 - 1e-9 <= setelah_pop[0] <= 2.1, "onset whoosh"
    # Di luar jendela SFX (0-0,95 dtk) audio tetap sama level (encode ulang AAC: toleransi 1 dB).
    assert 20 * np.log10(b[5:90].mean() / a[5:90].mean()) == pytest.approx(0, abs=1.0)
    assert _durasi(out) == pytest.approx(_durasi(v), abs=0.05)


UCAPAN_UJI = os.path.join(os.path.dirname(__file__), "data", "ucapan_uji.wav")


@pytest.mark.parametrize("lufs", [-24, -16])
def test_sfx_terdengar_tapi_di_bawah_puncak_ucapan_nyata(tmp_path, lufs):
    """29 Sep, user: "SFX suaranya kecil" -- versi LUFS memberi puncak SFX ±18 dB di bawah puncak
    ucapan. Diukur pada ucapan sungguhan di dua kenyaringan: puncak SFX di hasil campuran
    harus 2-7 dB di bawah puncak ucapan (terdengar, tidak menutupi)."""
    v = str(tmp_path / "u.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=gray:size=160x284:rate=24:duration=11.5", "-i", UCAPAN_UJI,
                    "-af", f"loudnorm=I={lufs}:TP=-1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", v], check=True, capture_output=True)
    ucap = sfx.puncak_ucapan(v)
    assert ucap is not None and ucap < -1
    # Pop di tengah jeda 2,5 dtk fixture (detik ±5,6): puncaknya terukur tanpa tertutup ucapan.
    out = str(tmp_path / "o.mp4")
    sfx.tambah(v, [(5.5, "pop")], out, str(tmp_path))
    x = _pcm(out)[int(5.45 * 16000):int(5.65 * 16000)]
    puncak_pop = 20 * np.log10(np.max(np.abs(x)))
    assert ucap - 7 < puncak_pop < ucap - 2, f"pop {puncak_pop:.1f} dBFS vs puncak ucapan {ucap:.1f}"


def test_puncak_tak_terukur_pakai_cadangan(monkeypatch):
    monkeypatch.setattr(sfx, "puncak_ucapan", lambda p: None)
    g = sfx._gain("x.mp4")
    assert g["pop"] == pytest.approx(10 ** ((sfx.PUNCAK_CADANGAN_DBFS - 3 + 1) / 20), rel=1e-6)
    assert g["whoosh"] < g["pop"]


def test_tanpa_peristiwa_tidak_menyentuh_video(tmp_path):
    info = sfx.tambah("tidak-ada.mp4", [], str(tmp_path / "o.mp4"), str(tmp_path))
    assert info["dipakai"] is False and not (tmp_path / "o.mp4").exists()


def _durasi(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                 "csv=p=0", str(p)], capture_output=True, text=True).stdout)
