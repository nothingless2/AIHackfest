"""Gaya, tata letak, dan animasi subtitle.

Ukuran font adalah FRAKSI tinggi kanvas, bukan piksel tetap — itu yang membuat
teks proporsional otomatis di 9:16, 1:1, dan 16:9 tanpa disetel ulang.
"""

import pytest

import auto_render as ar


# ---------- tata letak proporsional ----------

def test_font_menskala_terhadap_tinggi_kanvas():
    fs_tinggi, _ = ar.subtitle_geometry(1080, 1920)
    fs_pendek, _ = ar.subtitle_geometry(1920, 1080)
    assert fs_tinggi > fs_pendek
    assert fs_tinggi == round(1920 * ar.SUBTITLE_SIZE_RATIO)
    assert fs_pendek == round(1080 * ar.SUBTITLE_SIZE_RATIO)


def test_ukuran_lama_52px_memang_terlalu_kecil():
    """52 px pada 1920 = 2,7% tinggi; patokan caption sosial media 4-5%."""
    fs, _ = ar.subtitle_geometry(1080, 1920)
    assert fs / 1920 >= 0.04
    assert fs > 52


def test_teks_berada_di_atas_zona_aman_bawah():
    for w, h in [(1080, 1920), (1080, 1080), (1920, 1080)]:
        fs, y = ar.subtitle_geometry(w, h)
        blok = ar.SUBTITLE_MAX_LINES * fs * 1.25
        assert y >= 0
        assert y + blok <= h - h * ar.SAFE_BOTTOM_RATIO + 1


def test_y_tidak_pernah_negatif_di_kanvas_sangat_pendek():
    _, y = ar.subtitle_geometry(1920, 200)
    assert y >= 0


# ---------- gaya ----------

def test_gaya_default_putih_berkotak():
    assert ar.subtitle_style()["color"] == "white"
    assert ar.subtitle_style().get("box")


def test_gaya_tak_dikenal_pakai_default_dengan_peringatan(monkeypatch, capsys):
    """Salah ketik tidak boleh diam-diam mengubah tampilan."""
    monkeypatch.setattr(ar, "SUBTITLE_STYLE", "warna-karangan")
    assert ar.subtitle_style()["color"] == "white"
    assert "tidak dikenal" in capsys.readouterr().out


def test_semua_gaya_punya_warna():
    for nama, g in ar.SUBTITLE_STYLES.items():
        assert g.get("color"), nama
        assert g.get("box") or g.get("border"), f"{nama} harus punya kotak atau garis tepi"


def test_gaya_berkotak_menghasilkan_box_di_filter(monkeypatch):
    monkeypatch.setattr(ar, "SUBTITLE_STYLE", "putih-kotak")
    f = ar.build_drawtext_chain([{"start": 0, "end": 2, "text": "halo"}], 1920, 1080)[0]
    assert "box=1" in f and "boxcolor=" in f


def test_gaya_tanpa_kotak_memakai_garis_tepi(monkeypatch):
    monkeypatch.setattr(ar, "SUBTITLE_STYLE", "putih-tebal")
    f = ar.build_drawtext_chain([{"start": 0, "end": 2, "text": "halo"}], 1920, 1080)[0]
    assert "box=1" not in f
    assert "bordercolor=black" in f


# ---------- animasi ----------

def test_satu_filter_per_kata():
    """Tiap keadaan adalah drawtext berisi kata yang sudah terucap."""
    sc = [{"start": 0, "end": 3, "text": "a b c", "words": [
        {"word": "a", "start": 0.0, "end": 0.5},
        {"word": "b", "start": 0.6, "end": 1.0},
        {"word": "c", "start": 1.2, "end": 2.0},
    ]}]
    assert len(ar.build_drawtext_chain(sc, 1920, 1080)) == 3


def test_kata_menumpuk_bukan_berganti():
    sc = [{"start": 0, "end": 3, "text": "satu dua", "words": [
        {"word": "satu", "start": 0.0, "end": 0.5},
        {"word": "dua", "start": 0.6, "end": 1.0},
    ]}]
    f = ar.build_drawtext_chain(sc, 1920, 1080)
    assert "text='satu'" in f[0]
    assert "text='satu dua'" in f[1], "kata kedua harus menambah, bukan mengganti"


def test_kata_aktif_sampai_kata_berikutnya_muncul():
    sc = [{"start": 0, "end": 5, "text": "a b", "words": [
        {"word": "a", "start": 1.0, "end": 1.4},
        {"word": "b", "start": 2.0, "end": 2.5},
    ]}]
    f = ar.build_drawtext_chain(sc, 1920, 1080)
    assert "between(t,1.0,2.0)" in f[0], "bukan berhenti di akhir kata, tapi saat kata berikutnya"
    assert "between(t,2.0,5.0)" in f[1], "kata terakhir bertahan sampai akhir scene"


def test_tanpa_kata_satu_filter_dengan_fade():
    f = ar.build_drawtext_chain([{"start": 1, "end": 3, "text": "halo"}], 1920, 1080)
    assert len(f) == 1
    assert "alpha=" in f[0], "scene tanpa timestamp kata tetap dapat fade"


def test_scene_tidak_valid_dilewati():
    sc = [
        {"start": 0, "end": None, "text": "tanpa akhir"},
        {"start": 2, "end": 1, "text": "terbalik"},
        {"start": 0, "end": 1, "text": ""},
    ]
    assert ar.build_drawtext_chain(sc, 1920, 1080) == []


# ---------- pengelompokan kata ----------

def test_kata_dikelompokkan_agar_muat():
    kata = [{"word": "kata", "start": i * 0.3, "end": i * 0.3 + 0.2} for i in range(40)]
    kel = ar.chunk_words(kata, 86, 1080)
    assert len(kel) > 1
    assert sum(len(k) for k in kel) == 40, "tidak boleh ada kata yang hilang"


def test_kelompok_pendek_tetap_satu():
    kata = [{"word": "halo", "start": 0, "end": 1}]
    assert len(ar.chunk_words(kata, 86, 1080)) == 1
