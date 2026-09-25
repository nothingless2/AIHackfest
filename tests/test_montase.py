"""Montase mengikuti ketukan musik (bahan tanpa ucapan): grid ketukan diukur dari musik, potongan
dibulatkan ke kelipatan ketukan, musik mulai di ketukan pertama -> sambungan jatuh di ketukan."""

import json
import subprocess

import numpy as np
import pytest

import auto_render as ar
import music as m
import music_mood as mm

SATU_FRAME = 1 / 24


def _klik(path, bpm, fase, detik=20, frek=1500):
    per = 60 / bpm
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc='if(lt(mod(t-{fase}+10*{per}\\,{per})\\,0.02)\\,sin(2*PI*{frek}*t)*0.9\\,0)'"
                    f":s=44100:d={detik}", str(path)], check=True, capture_output=True)
    return str(path)


def _selisih(a, b, per):
    return abs(((a - b + per / 2) % per) - per / 2)


@pytest.mark.parametrize("bpm", [90, 120, 140])
@pytest.mark.parametrize("fase", [0.1, 0.3])
def test_fase_ketukan_terukur_dalam_satu_frame(tmp_path, bpm, fase):
    k = mm.ketukan(_klik(tmp_path / "k.wav", bpm, fase))
    assert k["yakin"] and k["bpm"] == pytest.approx(bpm, abs=1)
    # Kalibrasi lebih ketat dari 1 frame: sisa kesalahan terukur setelah FASE_KOREKSI ±2 ms;
    # tanpa koreksi ±26 ms (masih < 1 frame, jadi batas 1 frame tidak akan menjaganya).
    assert _selisih(k["fase"], fase, k["periode"]) <= 0.012


def test_nada_datar_tidak_punya_grid(tmp_path):
    p = tmp_path / "nada.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                    str(p)], check=True, capture_output=True)
    assert mm.ketukan(str(p))["yakin"] is False


def test_rentang_dibulatkan_ke_kelipatan_unit(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "pick_track", lambda *a, **k: "t.mp3")
    monkeypatch.setattr(mm, "ketukan", lambda p: {"bpm": 120, "yakin": True, "periode": 0.5, "fase": 0.3})
    rencana = [{"path": "a", "ranges": [(0.0, 3.3)], "durasi": 3.3, "fade_masuk": True},
               {"path": "b", "ranges": [(1.0, 2.5), (4.0, 8.9)], "durasi": 6.4, "fade_setelah": {0}},
               {"path": "c", "ranges": [(0.0, 1.9)], "durasi": 1.9}]
    baru = ar.terapkan_montase(rencana)
    assert ar.MONTASE["unit_detik"] == pytest.approx(2.0), "2 ketukan = 1 dtk < minimal -> 4 ketukan"
    assert [r["ranges"] for r in baru] == [[(0.0, 2.0)], [(4.0, 8.0)]], "yang < 1 unit dibuang"
    assert all(r["fade_masuk"] is False and not r["fade_setelah"] for r in baru), "hard cut di ketukan"


def test_tempo_tidak_pasti_dilewati_dan_dicatat(monkeypatch):
    monkeypatch.setattr(ar, "pick_track", lambda *a, **k: "t.mp3")
    monkeypatch.setattr(mm, "ketukan", lambda p: {"bpm": None, "yakin": False})
    r = [{"path": "a", "ranges": [(0.0, 3.3)], "durasi": 3.3}]
    assert ar.terapkan_montase(r) is r and ar.MONTASE["alasan"] == "tempo musik tidak pasti"


# ------------------------------------------------------------------ render sungguhan

def _klip(path, warna, detik):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c={warna}:size=240x426:rate=24:duration={detik}", "-f", "lavfi", "-i",
                    f"anoisesrc=d={detik}:c=pink:a=0.02", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True, capture_output=True)
    return str(path)


def _sambungan(path):
    """Detik pergantian warna (frame demi frame)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", "scale=1:1", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    px = np.frombuffer(r, np.uint8).reshape(-1, 3).astype(int)
    return [i / 24 for i in range(1, len(px)) if np.abs(px[i] - px[i - 1]).max() > 60]


def _onset(path):
    """Detik ketukan di audio keluaran (puncak energi 1500 Hz)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                        "-af", "bandpass=f=1500:width_type=h:width=300", "-f", "s16le", "-"],
                       capture_output=True, check=True).stdout
    x = np.abs(np.frombuffer(r, np.int16).astype(float))
    blok = 160                                   # 10 ms
    e = x[: len(x) // blok * blok].reshape(-1, blok).mean(axis=1)
    ambang = e.max() * 0.3
    return [i * 0.01 for i in range(1, len(e)) if e[i] > ambang and e[i - 1] <= ambang]


@pytest.fixture
def render_montase(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "TARGET_W", 240)
    monkeypatch.setattr(ar, "TARGET_H", 426)
    monkeypatch.setattr(ar, "THUMBNAIL_ENABLED", False)
    monkeypatch.setattr(ar, "TRIM_SILENCE", False)
    monkeypatch.setattr(m, "MUSIC_DIR", str(tmp_path / "kosong"))
    monkeypatch.setenv("MONTASE", "1")
    lagu = _klik(tmp_path / "lagu.wav", 120, 0.3, detik=30)
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC_FILE", lagu)
    klip = [_klip(tmp_path / "a.mp4", "red", 3.3), _klip(tmp_path / "b.mp4", "lime", 2.9),
            _klip(tmp_path / "c.mp4", "blue", 3.7)]

    def jalankan():
        s = tmp_path / "s.json"
        s.write_text(json.dumps({"judul": "uji", "audio_mode": "mute", "full_voice_over": "x",
                                 "media_assets": klip, "scenes": []}), encoding="utf-8")
        out = tmp_path / "o.mp4"
        ar.render_from_agent_script(str(s), str(out))
        return out

    return jalankan


def test_render_sambungan_jatuh_di_ketukan(render_montase):
    out = render_montase()
    assert ar.MONTASE["dipakai"] is True, ar.MONTASE
    cut = _sambungan(out)
    assert cut == pytest.approx([2.0, 4.0], abs=SATU_FRAME + 1e-6), "potongan kelipatan 4 ketukan (2 dtk)"
    ketuk = _onset(out)
    assert len(ketuk) >= 8, "kontrol: ketukan musik memang terdengar di keluaran"
    for c in cut:
        assert min(abs(c - k) for k in ketuk) <= SATU_FRAME + 0.01, f"sambungan {c} tidak di ketukan"


def test_tempo_ganjil_tidak_menumpuk_meleset_di_akhir_lagu(tmp_path):
    """Tempo kasar berlangkah 0,5 BPM: salah 0,5 BPM = ±0,3 dtk meleset setelah 60 dtk.
    Tempo dihaluskan (0,05 BPM) -> ketukan ke-N di akhir lagu 40 dtk tetap dalam 1 frame."""
    bpm, fase = 97.3, 0.21
    k = mm.ketukan(_klik(tmp_path / "k.wav", bpm, fase, detik=40))
    assert k["bpm"] == pytest.approx(bpm, abs=0.1)
    per_asli = 60 / bpm
    n = int((38 - fase) / per_asli)
    asli = fase + n * per_asli
    tebak = k["fase"] + round((asli - k["fase"]) / k["periode"]) * k["periode"]
    assert abs(tebak - asli) <= SATU_FRAME, f"ketukan ke-{n} meleset {abs(tebak - asli) * 1000:.0f} ms"
