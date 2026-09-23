"""Validasi parameter gaya editing: filter warna, speed ramp, zoom otomatis.

Sama seperti canvas.py/music.py: nilai salah harus melempar SEBELUM render,
bukan diam-diam diabaikan atau membuat ffmpeg gagal di tengah jalan.
"""

import pytest

import style as st


def test_tanpa_permintaan_filter_warna_kosong(monkeypatch):
    monkeypatch.delenv("COLOR_FILTER", raising=False)
    assert st.resolve_color_filter() == (None, None)


def test_none_eksplisit_dianggap_kosong():
    assert st.resolve_color_filter("none") == (None, None)


def test_filter_dikenal_mengembalikan_chain():
    nama, chain = st.resolve_color_filter("vivid")
    assert nama == "vivid"
    assert "saturation" in chain


def test_filter_tidak_dikenal_ditolak():
    with pytest.raises(st.StyleError, match="warna"):
        st.resolve_color_filter("neon-cyberpunk")


def test_filter_dari_env(monkeypatch):
    monkeypatch.setenv("COLOR_FILTER", "bw")
    nama, chain = st.resolve_color_filter()
    assert nama == "bw"
    assert "hue" in chain


def test_semua_preset_punya_chain_tidak_kosong():
    for nama in st.COLOR_FILTERS:
        _, chain = st.resolve_color_filter(nama)
        assert chain


def test_speed_factor_default_1(monkeypatch):
    monkeypatch.delenv("SPEED_FACTOR", raising=False)
    assert st.resolve_speed_factor() == 1.0


def test_speed_factor_dari_argumen():
    assert st.resolve_speed_factor("1.5") == 1.5


def test_speed_factor_dari_env(monkeypatch):
    monkeypatch.setenv("SPEED_FACTOR", "0.75")
    assert st.resolve_speed_factor() == 0.75


def test_speed_factor_bukan_angka_ditolak():
    with pytest.raises(st.StyleError, match="bukan angka"):
        st.resolve_speed_factor("cepat sekali")


@pytest.mark.parametrize("nilai", ["0.1", "0.49", "2.01", "5", "-1"])
def test_speed_factor_di_luar_jangkauan_ditolak(nilai):
    with pytest.raises(st.StyleError, match="jangkauan"):
        st.resolve_speed_factor(nilai)


@pytest.mark.parametrize("nilai", ["0.5", "1.0", "2.0"])
def test_speed_factor_batas_jangkauan_diterima(nilai):
    assert st.resolve_speed_factor(nilai) == float(nilai)


def test_auto_zoom_default_mati(monkeypatch):
    monkeypatch.delenv("AUTO_ZOOM", raising=False)
    assert st.auto_zoom_enabled() is False


@pytest.mark.parametrize("nilai", ["1", "true", "True", "on", "yes", "ya"])
def test_auto_zoom_nilai_nyala(nilai):
    assert st.auto_zoom_enabled(nilai) is True


@pytest.mark.parametrize("nilai", ["0", "false", "off", "", "tidak"])
def test_auto_zoom_nilai_mati(nilai):
    assert st.auto_zoom_enabled(nilai) is False
