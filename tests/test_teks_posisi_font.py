"""Posisi & font teks on-screen (TEXT_POSITION / TEXT_FONT).

Diukur dari PIKSEL hasil render (bounding box teks lewat cropdetect), bukan dari
string filter -- pola yang sama dengan test_zoom.py.
"""

import os
import subprocess

import pytest

import auto_render as ar
import style as st

W, H = 1080, 1920


def test_default_bawah_dan_standar(monkeypatch):
    monkeypatch.delenv("TEXT_POSITION", raising=False)
    monkeypatch.delenv("TEXT_FONT", raising=False)
    assert st.resolve_text_position() == "bawah"
    assert st.resolve_text_font()[0] == "standar"


@pytest.mark.parametrize("nilai", ["atas", "tengah", "bawah", "TENGAH"])
def test_posisi_valid(nilai):
    assert st.resolve_text_position(nilai) == nilai.lower()


def test_posisi_dan_font_tidak_dikenal_ditolak():
    with pytest.raises(st.StyleError, match="Posisi"):
        st.resolve_text_position("miring")
    with pytest.raises(st.StyleError, match="Font teks"):
        st.resolve_text_font("comic-sans")


def test_semua_font_bundel_ada_di_disk():
    for nama, spec in st.TEXT_FONTS.items():
        if spec.get("file"):
            assert os.path.exists(os.path.join(ar.ASSETS_FONTS, spec["file"])), nama


def test_font_bundel_hilang_menggagalkan_render_bukan_ganti_diam_diam(monkeypatch):
    monkeypatch.setenv("TEXT_FONT", "santai")
    monkeypatch.setattr(ar, "ASSETS_FONTS", "/tidak/ada")
    with pytest.raises(FileNotFoundError, match="santai"):
        ar.text_font_path()


def _bbox(mp4, tmp_path):
    """(x, y, w, h) piksel terang (teks) pada latar hitam, dihitung numpy dari frame
    mentah. (cropdetect dicoba lebih dulu dan tidak andal untuk lebar: x1 > x2.)"""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.0", "-i", mp4, "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=True, capture_output=True)
    gambar = np.frombuffer(r.stdout, dtype=np.uint8).reshape(H, W)
    ys, xs = np.where(gambar > 100)
    assert len(xs), "tidak ada teks terdeteksi di frame"
    return int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)


@pytest.fixture
def render_teks(tmp_path, monkeypatch):
    def _r(posisi, font, teks="AKSI MERAH LAKSAMANA MUDA"):
        monkeypatch.setenv("TEXT_POSITION", posisi)
        monkeypatch.setenv("TEXT_FONT", font)
        # latar HITAM murni + tanpa kotak: yang tersisa hanya teks
        monkeypatch.setattr(ar, "SUBTITLE_STYLE", "karaoke-tebal")
        src = str(tmp_path / f"s_{posisi}_{font}.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"color=c=black:size={W}x{H}:rate=24:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", src],
                       check=True, capture_output=True)
        out = str(tmp_path / f"o_{posisi}_{font}.mp4")
        ar.apply_text_overlay(src, [{"start": 0, "end": 2, "text": teks, "statis": True}], out)
        return _bbox(out, tmp_path)
    return _r


def test_posisi_tengah_atas_bawah_terukur(render_teks):
    _, y_t, _, h_t = render_teks("tengah", "standar")
    _, y_a, _, h_a = render_teks("atas", "standar")
    _, y_b, _, h_b = render_teks("bawah", "standar")
    assert abs((y_t + h_t / 2) - H / 2) < 60, "teks tengah harus di tengah vertikal"
    assert (y_a + h_a) < H * 0.4, "teks atas harus di sepertiga atas"
    assert y_b > H * 0.6, "teks bawah (perilaku lama) harus di bawah"
    assert y_a < y_t < y_b


def test_font_berbeda_menghasilkan_lebar_berbeda(render_teks):
    """Kontrol positif: bukan cuma nama font yang diterima -- rupa hurufnya benar-benar
    berubah. Bebas Neue jauh lebih sempit daripada Montserrat ExtraBold."""
    _, _, w_tegas, _ = render_teks("tengah", "tegas")
    _, _, w_modern, _ = render_teks("tengah", "modern")
    _, _, w_std, _ = render_teks("tengah", "standar")
    assert w_modern < w_tegas * 0.85
    assert w_std != w_tegas


def test_subtitle_ucapan_tidak_ikut_posisi_teks(monkeypatch, tmp_path):
    """Subtitle karaoke (scene ber-`words`) tetap di bawah walau TEXT_POSITION=tengah."""
    monkeypatch.setenv("TEXT_POSITION", "tengah")
    kata = [{"word": w, "start": 0.2 + i * 0.4, "end": 0.55 + i * 0.4}
            for i, w in enumerate("halo semua ini tes".split())]
    rantai = ar.build_drawtext_chain(
        [{"start": 0.2, "end": 2.0, "text": "halo semua ini tes", "words": kata}], H, W)
    assert rantai and not any("(h-text_h)/2" in f for f in rantai)


@pytest.mark.parametrize("font", list(st.TEXT_FONTS))
@pytest.mark.parametrize("teks", ["AKSI MERAH LAKSAMANA MUDA",
                                  "Ratusan orang berkumpul dalam diskusi aktif hari ini"])
def test_teks_tidak_terpotong_di_tepi_untuk_semua_font(render_teks, font, teks):
    """Regresi: judul tengah yang dibesarkan sempat terpotong di kedua tepi pada font
    lebar, karena lebar diperkirakan dengan satu rasio karakter."""
    x, _, w, _ = render_teks("tengah", font, teks)
    assert x >= 20 and x + w <= W - 20, f"{font}: x={x} w={w}"
