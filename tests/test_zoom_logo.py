"""Zoom punch-in ke wajah + kartu logo merek (contoh video user 29 Sep, gambar 1).

Wajah: OpenCV Haar. Logo: daftar TERTUTUP config/merek_logo.json -- katalog simple-icons punya
"Hermes" milik myHermes (kurir Jerman), jadi pencocokan otomatis akan memasang logo yang salah."""

import json
import os
import subprocess

import numpy as np
import pytest

import logo as lg
import wajah
import zoom_wajah as z

KATA = "aku pakai Claude tiap hari buat bikin konten di Figma sama Notion terus upload".split()


def kw(per=0.5):
    return [{"word": w, "start": round(i * per, 3), "end": round(i * per + 0.4, 3)}
            for i, w in enumerate(KATA)]


# ------------------------------------------------------------------ deteksi wajah

def _video_wajah(path, detik=3.0, dengan_wajah=True):
    """Wajah sintetis: oval kulit + dua mata + mulut. Haar mengenalinya (dibuktikan tes kontrol)."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (270, 480), (40, 60, 90))
    d = ImageDraw.Draw(im)
    if dengan_wajah:
        d.ellipse([80, 120, 190, 270], fill=(222, 184, 150))
        for x in (108, 152):
            d.ellipse([x, 175, x + 14, 189], fill=(30, 30, 30))
        d.ellipse([120, 215, 150, 232], fill=(120, 60, 60))
        d.polygon([(135, 190), (128, 212), (142, 212)], fill=(200, 160, 128))
    p = str(path).replace(".mp4", ".png")
    im.save(p)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", p, "-t", str(detik),
                    "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   check=True, capture_output=True)
    return str(path)


def test_wajah_terdeteksi_dan_kontrol_tanpa_wajah(tmp_path):
    ada = wajah.kotak_wajah(_video_wajah(tmp_path / "a.mp4"), [1.0])[1.0]
    assert ada, "wajah sintetis harus terdeteksi"
    x, y, w, h = ada
    assert 60 < x + w / 2 < 210 and 100 < y + h / 2 < 300, f"pusat wajah meleset: {ada}"
    kosong = wajah.kotak_wajah(_video_wajah(tmp_path / "b.mp4", dengan_wajah=False), [1.0])[1.0]
    assert kosong is None, "kontrol: tanpa wajah harus None, bukan tebakan"


def test_median_mengabaikan_deteksi_kosong():
    assert wajah.median([(10, 20, 30, 30), None, (12, 22, 32, 32)]) == (11, 21, 31, 31)
    assert wajah.median([None, None]) is None


# ------------------------------------------------------------------ jadwal zoom

def test_zoom_hanya_di_kata_kunci_berjarak_dan_di_luar_panggung():
    pot = [{"mulai": 1.0, "selesai": 2.0, "kunci": 0}, {"mulai": 2.0, "selesai": 3.0, "kunci": 0},
           {"mulai": 6.0, "selesai": 7.0, "kunci": None}, {"mulai": 9.0, "selesai": 10.5, "kunci": 1},
           {"mulai": 14.0, "selesai": 15.5, "kunci": 0}]
    j = z.jadwal(pot, 20.0, hindari=[(8.5, 11.0)])
    assert [a for a, _ in j] == [1.0, 14.0], "rapat & di dalam panggung dilewati"
    assert all(b > a for a, b in j)


def test_zoom_batas_jumlah():
    pot = [{"mulai": i * 5.0, "selesai": i * 5.0 + 1.5, "kunci": 0} for i in range(8)]
    assert len(z.jadwal(pot, 60.0)) == z.MAKS


# ------------------------------------------------------------------ render zoom (piksel)

def _skala_terukur(dasar, uji, t):
    def frame(p):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", p, "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(480, 270).astype(float)
    a, b = frame(dasar), frame(uji)
    best = (0, -1)
    for s in (1.0, 1.03, 1.06, 1.10, 1.14):
        aa = np.array([[a[min(479, int(y / s))][min(269, int(x / s))] for x in range(270)]
                       for y in range(480)])
        c = float(np.corrcoef(aa.ravel(), b.ravel())[0, 1])
        if c > best[1]:
            best = (s, c)
    return best[0]


def test_render_zoom_hanya_di_jendelanya(tmp_path):
    dasar = str(tmp_path / "d.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=270x480:rate=24:duration=4", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", dasar], check=True, capture_output=True)
    out = str(tmp_path / "z.mp4")
    vf = z.filter_zoom([(1.0, 2.5)], 270, 480, (60, 140, 150, 150))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", dasar, "-vf", vf, "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", out], check=True, capture_output=True)
    assert _skala_terukur(dasar, out, 1.6) == pytest.approx(z.SKALA, abs=0.02), "membesar di jendela"
    for t in (0.5, 3.5):
        assert _skala_terukur(dasar, out, t) == 1.0, f"di luar jendela (t={t}) tidak berubah"


# ------------------------------------------------------------------ kartu logo

def test_merek_di_luar_daftar_tidak_jadi_logo():
    """29 Sep: simple-icons punya 'Hermes' = myHermes (kurir Jerman)."""
    assert "hermes" in {k for k in lg.katalog_ikon()}, "kontrol: katalog memang punya slug hermes"
    assert "hermes" not in lg.daftar_merek(), "daftar tertutup tidak memuatnya"
    kata = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4}
            for i, w in enumerate("aku pindah ke Hermes sekarang".split())]
    assert lg.cari(kata, 10.0) == []


def test_merek_terdaftar_jadi_kartu_dengan_ikon_nyata():
    t = lg.cari(kw(), 20.0, jarak=1.0)
    assert [x["merek"] for x in t] == ["Claude", "Figma", "Notion"]
    for x in t:
        assert len(x["path"]) > 100 and re.fullmatch(r"[0-9A-Fa-f]{6}", x["hex"])


def test_batas_jumlah_jarak_dan_jendela_panggung():
    t = lg.cari(kw(), 20.0, jarak=1.0, hindari=[(4.3, 5.2)])
    assert [x["merek"] for x in t] == ["Claude", "Notion"], "merek di jendela panggung dilewati"
    assert len(lg.cari(kw(), 20.0, jarak=10.0)) == 1, "jarak minimal ditegakkan"


def test_posisi_kartu_tidak_menimpa_wajah():
    W, H, kw_, kh = 1080, 1920, 300, 300
    for fx in (80, 600):
        kotak = (fx, 400, 360, 360)
        x, y = lg.posisi(kotak, W, H, kw_, kh)
        tumpang = max(0, min(x + kw_, fx + 360) - max(x, fx))
        assert tumpang == 0, f"kartu menimpa wajah (fx={fx}): x={x}"
        assert 0 <= y <= H - kh
    assert lg.posisi(None, W, H, kw_, kh)[0] + kw_ <= W, "tanpa wajah: tetap di dalam bingkai"


def test_ikon_tanpa_berkas_svg_tidak_jadi_kartu(tmp_path):
    kata = [{"word": "claude", "start": 0.5, "end": 0.9}]
    assert lg.cari(kata, 5.0, svg_folder=str(tmp_path)) == [], "tanpa SVG: tanpa kartu"


def test_bisa_dimatikan(monkeypatch):
    monkeypatch.setenv("SUBTITLE_STYLE", "dinamis")
    assert lg.aktif() and z.aktif()
    monkeypatch.setenv("LOGO_MEREK", "off")
    monkeypatch.setenv("ZOOM_WAJAH", "off")
    assert not lg.aktif() and not z.aktif()
    monkeypatch.delenv("LOGO_MEREK")
    monkeypatch.delenv("ZOOM_WAJAH")
    monkeypatch.setenv("SUBTITLE_STYLE", "karaoke")
    assert not lg.aktif() and not z.aktif(), "hanya untuk gaya dinamis"


import re  # noqa: E402


# ------------------------------------------------------------------ render kartu logo (piksel)

def test_render_kartu_logo_berwarna_merek_dan_tidak_menimpa_wajah(tmp_path):
    """Kontrol positif: video user 29 Sep tidak menyebut satu pun merek terdaftar, jadi "tidak ada
    logo" di render nyata TIDAK membuktikan kartunya bekerja. Di sini kartunya benar-benar dirender."""
    import overlay_remotion as orr
    W, H = 270, 480
    (t,) = lg.cari([{"word": "claude", "start": 0.4, "end": 0.8}], 4.0)
    kartu = int(W * 0.33 * 1.16)
    x, y = lg.posisi((20, 150, 120, 120), W, H, kartu, kartu)     # wajah di KIRI -> kartu ke kanan
    item = {"jenis": "logo", "mulai": 0.4, "selesai": 2.6, "teks": t["merek"], "merek": t["merek"],
            "hex": t["hex"], "path": t["path"], "x": x, "y": y}
    folder = tmp_path / "motion"
    folder.mkdir()
    kerja, info = orr.render_motion([item], str(folder), lebar=W, tinggi=H, fps=24, durasi=4.0)
    dasar = str(tmp_path / "d.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=0x203050:size={W}x{H}:rate=24:duration=4", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", dasar], check=True, capture_output=True)
    out = str(tmp_path / "o.mp4")
    orr.komposit(dasar, kerja, 24, out)

    def piksel(t_, x0, y0, w, h):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t_), "-i", out, "-frames:v", "1",
                              "-vf", f"crop={w}:{h}:{x0}:{y0}", "-f", "rawvideo", "-pix_fmt", "rgb24",
                              "-"], check=True, capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(int)

    r, g, b = (int(t["hex"][i:i + 2], 16) for i in (0, 2, 4))
    dalam = piksel(1.5, x, y, kartu, kartu)
    putih = ((dalam > 220).all(axis=1)).sum()
    merek = (np.abs(dalam - np.array([r, g, b])).sum(axis=1) < 90).sum()
    assert putih > 500, "kartu putih tampil"
    assert merek > 40, f"ikon berwarna merek ({t['hex']}) tampil: {merek} piksel"
    # Zona wajah (kiri) tetap video asli.
    wajah_px = piksel(1.5, 20, 150, 120, 120)
    assert (wajah_px[:, 2] > wajah_px[:, 0] + 20).mean() > 0.9, "kartu tidak menimpa wajah"
    # Sebelum kartu muncul: layar masih polos.
    assert ((piksel(0.1, x, y, kartu, kartu) > 220).all(axis=1)).sum() == 0, "kontrol: belum ada kartu"
