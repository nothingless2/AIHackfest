"""Cover didesain (scripts/sampul.py + remotion/src/Sampul.jsx): frame terbaik (wajah besar &
tajam) + judul besar, bukan frame acak yang subtitle-nya sudah terbakar."""

import json
import os
import subprocess

import numpy as np
import pytest

import sampul as sp
from test_zoom_logo import _video_wajah     # noqa: F401  (wajah sintetis)


def test_kandidat_melewati_jendela_dan_ujung():
    t = sp.detik_kandidat(20.0, hindari=[(5.0, 9.0)])
    assert all(0.5 <= x <= 19.5 for x in t) and not any(5.0 <= x <= 9.0 for x in t)
    assert sp.detik_kandidat(1.0) == [0.5], "video sangat pendek tetap memberi satu titik"


def test_judul_dari_kartu_pembuka_dan_kata_emas():
    teks, emas = sp.judul_sampul({"motion_plan": {"hook": "Ganti API Key Bikin Pusing"}}, ["pusing"])
    assert teks == "Ganti API Key Bikin Pusing" and teks.split()[emas] == "Pusing"
    teks, emas = sp.judul_sampul({"judul": "Satu dua tiga empat lima enam tujuh delapan"})
    assert len(teks.split()) == sp.MAKS_KATA, "judul dipotong, tidak dipaksa muat"
    assert sp.judul_sampul({}) == ("", None)


def test_frame_terbaik_memilih_wajah_terbesar(tmp_path):
    """Kontrol: klip berisi frame BURAM berwajah dan frame tajam TANPA wajah -> yang dipilih
    tetap yang berwajah (wajah lebih penting untuk cover), lalu yang paling tajam bila tak ada."""
    wajah_v = _video_wajah(tmp_path / "w.mp4", detik=2.0)
    detik, kotak = sp.pilih_frame(wajah_v, [0.5, 1.0, 1.5])
    assert kotak is not None and 0.5 <= detik <= 1.5
    polos = _video_wajah(tmp_path / "p.mp4", detik=2.0, dengan_wajah=False)
    detik2, kotak2 = sp.pilih_frame(polos, [0.5, 1.0])
    assert kotak2 is None and detik2 in (0.5, 1.0), "tanpa wajah: tetap memberi frame, bukan gagal"


def test_ambil_frame_ukuran_kanvas(tmp_path):
    v = _video_wajah(tmp_path / "v.mp4", detik=2.0)
    out = sp.ambil_frame_jpg(v, 1.0, str(tmp_path / "f.jpg"), 270, 480)
    assert out and os.path.exists(out)
    wh = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                         "stream=width,height", "-of", "csv=p=0", out], capture_output=True,
                        text=True).stdout.strip()
    assert wh == "270,480"


def test_bisa_dimatikan(monkeypatch):
    assert sp.aktif()
    monkeypatch.setenv("COVER", "frame")
    assert not sp.aktif()


# ------------------------------------------------------------------ render cover (piksel)

def _rgb(path, x, y, w, h):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"crop={w}:{h}:{x}:{y}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True,
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(int)


def test_render_cover_judul_tampil_dan_wajah_tidak_tertutup(tmp_path):
    import overlay_remotion as orr
    W, H = 270, 480
    v = _video_wajah(tmp_path / "v.mp4", detik=2.0)
    detik, kotak = sp.pilih_frame(v, [0.5, 1.0])
    assert kotak, "kontrol: wajah sintetis terdeteksi"
    mentah = sp.ambil_frame_jpg(v, detik, str(tmp_path / "f.jpg"), W, H)
    y = min(0.86, max(0.62, (kotak[1] + kotak[3]) / H + 0.12))
    out = str(tmp_path / "cover.jpg")
    orr.render_sampul(mentah, "Ganti API Key", 1, out, str(tmp_path), lebar=W, tinggi=H, y_judul=y)
    assert os.path.exists(out)
    zona = _rgb(out, 0, int(H * y) - 30, W, 60)
    assert ((zona > 225).all(axis=1)).sum() > 150, "judul putih tampil di zonanya"
    emas = (np.abs(zona - np.array([247, 204, 85])).sum(axis=1) < 120).sum()
    assert emas > 20, f"kata kunci emas tampil: {emas} piksel"
    fx, fy, fw, fh = kotak
    wajah_px = _rgb(out, fx, fy, fw, int(fh * 0.7))
    assert ((wajah_px > 225).all(axis=1)).mean() < 0.02, "wajah tidak tertutup teks"


def test_render_gagal_cover_lama_dipakai(monkeypatch, tmp_path):
    """Cover tidak pernah menggagalkan render; gagal -> frame biasa + alasan dilaporkan."""
    import auto_render as ar
    import overlay_remotion as orr
    monkeypatch.setattr(orr, "render_sampul", lambda *a, **k: (_ for _ in ()).throw(orr.OverlayError("chromium mati")))
    monkeypatch.setattr(ar, "TARGET_W", 270)
    monkeypatch.setattr(ar, "TARGET_H", 480)
    monkeypatch.setattr(ar, "TRIM_SILENCE", False)
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC", "off")
    v = _video_wajah(tmp_path / "v.mp4", detik=3.0)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", v, "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=3", "-c:v", "copy", "-c:a", "aac", "-ac", "2",
                    "-shortest", str(tmp_path / "va.mp4")], check=True, capture_output=True)
    data = {"judul": "Uji cover", "audio_mode": "original", "full_voice_over": "x",
            "media_assets": [str(tmp_path / "va.mp4")], "scenes": [],
            "motion_plan": {"hook": "Uji cover gagal"},
            "transcript_words": {"va.mp4": [{"word": "halo", "start": 0.5, "end": 0.9}]},
            "transcript_segments": {"va.mp4": [{"start": 0.5, "end": 1.0, "text": "halo"}]}}
    s = tmp_path / "s.json"
    s.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "h.mp4"
    ar.render_from_agent_script(str(s), str(out))
    st = json.load(open(ar.STATUS_PATH))
    assert st["sampul"]["dipakai"] is False and "chromium mati" in st["sampul"]["gagal"]
    assert os.path.exists(str(out).replace(".mp4", ".jpg")), "cover lama tetap dibuat"
