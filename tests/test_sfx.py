"""SFX (scripts/sfx.py): bunyi sintesis di momen visual, di bawah level ucapan, tepat waktu."""

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
    monkeypatch.setattr(sfx, "_gain", lambda path: 1.0)
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


def test_puncak_sfx_di_bawah_ucapan(tmp_path):
    from music import loudness
    v = _video(tmp_path / "keras.mp4", db=-12)
    lufs = loudness(v)
    puncak = -1 + 20 * np.log10(sfx._gain(v))
    assert puncak == pytest.approx(lufs - sfx.DI_BAWAH_UCAPAN_DB, abs=0.5)
    assert puncak < 20 * np.log10(np.max(np.abs(_pcm(v)))), "SFX tidak lebih keras dari ucapan"


def test_tanpa_peristiwa_tidak_menyentuh_video(tmp_path):
    info = sfx.tambah("tidak-ada.mp4", [], str(tmp_path / "o.mp4"), str(tmp_path))
    assert info["dipakai"] is False and not (tmp_path / "o.mp4").exists()


def _durasi(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                 "csv=p=0", str(p)], capture_output=True, text=True).stdout)
