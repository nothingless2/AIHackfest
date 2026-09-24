"""Musik di atas bahan TANPA ucapan (suara keramaian). Regresi 24 Sep: user tidak bisa
mendengar musik -- ducking terpicu terus oleh keramaian sehingga musik hilang total.

Diukur dari energi pita sempit nada uji (440 Hz) di trek musik relatif terhadap sisa
spektrum: tanpa musik ~-20 dB, musik terdengar jauh di atas itu."""

import subprocess

import numpy as np
import pytest

import auto_render as ar


def _gen(args, out):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args, str(out)], check=True, capture_output=True)


@pytest.fixture
def bahan(tmp_path):
    video = tmp_path / "keramaian.mp4"
    _gen(["-f", "lavfi", "-i", "color=c=gray:size=160x284:rate=24:duration=6",
          "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.25:duration=6",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest"], video)
    nada = tmp_path / "musik.mp3"
    _gen(["-f", "lavfi", "-i", "sine=f=440:d=8", "-af", "volume=-12dB", "-ac", "2", "-ar", "44100"], nada)
    return video, nada


def _nada_db(path, f=440):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000",
                        "-f", "f32le", "-"], capture_output=True, check=True)
    x = np.frombuffer(r.stdout, np.float32)
    X, fr = np.abs(np.fft.rfft(x)), np.fft.rfftfreq(len(x), 1 / 16000)
    pita = (fr > f - 5) & (fr < f + 5)
    lain = (fr > 100) & (fr < 4000) & ~((fr > f - 30) & (fr < f + 30))
    return 10 * np.log10((X[pita] ** 2).sum() / (X[lain] ** 2).sum())


def test_tanpa_ucapan_musik_terdengar(bahan, tmp_path):
    video, nada = bahan
    kontrol = _nada_db(video)
    out = tmp_path / "o.mp4"
    ar.tambah_musik(str(video), str(nada), str(out), 6, ada_ucapan=False)
    assert _nada_db(out) > kontrol + 20, "musik harus jelas terdengar di atas keramaian"


def test_kontrol_positif_ducking_pada_keramaian_memang_menenggelamkan_musik(bahan, tmp_path):
    """Membuktikan pengukur bisa melihat masalahnya: dengan ducking (perilaku lama untuk
    bahan tanpa ucapan) musik tenggelam jauh lebih dalam."""
    video, nada = bahan
    dengan, tanpa = tmp_path / "d.mp4", tmp_path / "t.mp4"
    ar.tambah_musik(str(video), str(nada), str(dengan), 6, ada_ucapan=True)
    ar.tambah_musik(str(video), str(nada), str(tanpa), 6, ada_ucapan=False)
    assert _nada_db(tanpa) > _nada_db(dengan) + 10


def test_ada_ucapan_ditentukan_dari_timestamp_kata():
    """render_from_agent_script: `ada_ucapan` = ada scene ber-`words`. Dijaga lewat sumber
    supaya tidak diganti lagi dengan 'punya trek audio' (penyebab bug)."""
    import inspect
    src = inspect.getsource(ar.render_from_agent_script)
    assert 'ada_ucapan = any(s.get("words") for s in scenes)' in src
    assert "ada_ucapan=ada_ucapan" in src
