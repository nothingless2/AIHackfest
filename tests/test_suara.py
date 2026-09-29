"""Pembersih suara (scripts/suara.py): diuji pada UCAPAN SUNGGUHAN (tests/data/ucapan_uji.wav, jeda
2,5 dtk di detik ±4,8-7,3) dengan bising pink sintetis. Lantai diukur di jeda yang DIKETAHUI dari
versi bersih; ucapan dibandingkan dengan rekaman bersih (selisih spektrum 300-3.400 Hz)."""

import json
import os
import subprocess

import numpy as np
import pytest

import suara

UCAPAN = os.path.join(os.path.dirname(__file__), "data", "ucapan_uji.wav")
JEDA = (5.1, 7.0)


def _video(path, bising=0.0):
    f = "[1:a]aformat=channel_layouts=stereo,loudnorm=I=-24:TP=-2[u]"
    if bising:
        f += (f";[2:a]aformat=channel_layouts=stereo,highpass=f=60[n];"
              "[u][n]amix=inputs=2:duration=first:normalize=0[o]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=gray:size=160x284:rate=24:duration=11.5", "-i", UCAPAN,
                    *(["-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude={bising}:duration=12"] if bising else []),
                    "-filter_complex", f, "-map", "0:v", "-map", "[o]" if bising else "[u]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-shortest",
                    str(path)], check=True, capture_output=True)
    return str(path)


def _pcm(p):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-vn", "-ac", "1", "-ar", "16000", "-f",
                          "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.int16).astype(float) / 32768


def _lantai(x):
    j = x[int(JEDA[0] * 16000):int(JEDA[1] * 16000)]
    return 20 * np.log10(np.sqrt((j ** 2).mean()) + 1e-9)


def _selisih_spektrum(ref, uji):
    n = min(len(ref), len(uji))
    w = 512

    def spek(x):
        k = n // w
        f = np.abs(np.fft.rfft(x[:k * w].reshape(k, w) * np.hanning(w), axis=1))
        b = np.fft.rfftfreq(w, 1 / 16000)
        return 20 * np.log10(f[:, (b >= 300) & (b <= 3400)] + 1e-9)
    sr, su = spek(ref[:n]), spek(uji[:n])
    e = (10 ** (sr / 10)).sum(1)
    u = e > np.percentile(e, 70)
    return float(np.abs((su[u] - su[u].mean()) - (sr[u] - sr[u].mean())).mean())


@pytest.fixture(scope="module")
def bahan(tmp_path_factory):
    d = tmp_path_factory.mktemp("suara")
    # bising 0,06: SNR < SUARA_SNR_BERISIK (0,02 memberi SNR 31,5 -- setara video HP user yang bersih)
    return {"bersih": _video(d / "bersih.mp4"), "berisik": _video(d / "berisik.mp4", bising=0.06), "d": d}


def test_audio_berisik_dibersihkan_dan_ucapan_membaik(bahan):
    out = str(bahan["d"] / "o_berisik.mp4")
    info = suara.bersihkan(bahan["berisik"], out)
    assert info["dipakai"] and info["peredam_bising"], info
    # Pembanding adil: rekaman bersih melewati rantai yang sama (EQ/gerbang/kompresor) tanpa peredam.
    ref_out = str(bahan["d"] / "o_ref.mp4")
    suara.bersihkan(bahan["bersih"], ref_out)
    ref, sebelum, sesudah = _pcm(ref_out), _pcm(bahan["berisik"]), _pcm(out)
    assert _lantai(sebelum) - _lantai(sesudah) >= 10, "jeda >= 10 dB lebih senyap"
    assert _selisih_spektrum(ref, sesudah) < _selisih_spektrum(_pcm(bahan["bersih"]), sebelum), \
        "ucapan lebih dekat ke rekaman bersih daripada sebelum dibersihkan"
    assert np.abs(sesudah).max() < 0.999, "tidak clip"


def test_audio_bersih_tanpa_peredam_dan_ucapan_utuh(bahan):
    out = str(bahan["d"] / "o_bersih.mp4")
    info = suara.bersihkan(bahan["bersih"], out)
    assert info["dipakai"] and not info["peredam_bising"], "SNR tinggi: afftdn tidak dipasang"
    ref, sesudah = _pcm(bahan["bersih"]), _pcm(out)
    # ±1,3 dB = penegas vokal +2 dB di 3,2 kHz yang DISENGAJA (terukur); lebih dari 2 dB = rusak.
    assert _selisih_spektrum(ref, sesudah) < 2.0, "bentuk spektrum ucapan hanya berubah sebatas EQ"
    assert _lantai(sesudah) <= _lantai(ref) + 0.5, "jeda tidak jadi lebih bising"


def test_ambang_gerbang_mengikuti_level_ucapan():
    pelan, _ = suara.rantai({"ucapan": -40.0, "lantai": -70.0, "snr": 30.0})
    keras, _ = suara.rantai({"ucapan": -20.0, "lantai": -50.0, "snr": 30.0})
    amb = lambda f: float(f.split("agate=threshold=")[1].split(":")[0])   # noqa: E731
    assert amb(keras) == pytest.approx(10 * amb(pelan), rel=0.01), "ucapan pelan tidak ikut digerbang"


def test_bisa_dimatikan(monkeypatch):
    monkeypatch.setenv("BERSIH_SUARA", "off")
    assert not suara.aktif()
    monkeypatch.setenv("BERSIH_SUARA", "1")
    assert suara.aktif()


# ------------------------------------------------------------------ di pipeline

def _render(monkeypatch, tmp_path, dengan_kata):
    import auto_render as ar
    monkeypatch.setenv("BERSIH_SUARA", "1")
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC", "off")
    monkeypatch.setattr(ar, "TARGET_W", 160)
    monkeypatch.setattr(ar, "TARGET_H", 284)
    monkeypatch.setattr(ar, "THUMBNAIL_ENABLED", False)
    monkeypatch.setattr(ar, "TRIM_SILENCE", False)
    v = _video(tmp_path / "v.mp4", bising=0.06)
    kata = [{"word": w, "start": 0.5 + i * 0.5, "end": 0.9 + i * 0.5} for i, w in enumerate("halo semua apa kabar".split())]
    data = {"judul": "uji", "audio_mode": "original", "full_voice_over": "x", "media_assets": [v], "scenes": [],
            "transcript_words": {"v.mp4": kata} if dengan_kata else {},
            "transcript_segments": {"v.mp4": [{"start": 0.5, "end": 2.5, "text": "halo semua apa kabar"}]}
            if dengan_kata else {}}
    s = tmp_path / "s.json"
    s.write_text(json.dumps(data), encoding="utf-8")
    ar.render_from_agent_script(str(s), str(tmp_path / "h.mp4"))
    return json.load(open(ar.STATUS_PATH))


def test_pipeline_ucapan_dibersihkan(monkeypatch, tmp_path):
    st = _render(monkeypatch, tmp_path, True)
    assert st["suara_bersih"]["dipakai"] is True


def test_pipeline_tanpa_ucapan_suasana_utuh(monkeypatch, tmp_path):
    """Bahan tanpa ucapan (suasana/keramaian, kasus food court 24 Sep): tidak disentuh."""
    st = _render(monkeypatch, tmp_path, False)
    assert st.get("suara_bersih") is None
