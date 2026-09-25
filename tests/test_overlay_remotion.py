"""Teks tulisan beranimasi lewat Remotion (scripts/overlay_remotion.py + remotion/).

Tiga lapis:
1. Perencana (murni, cepat): frame mana dirender Chromium, mana gambar diam.
2. Cadangan: Remotion gagal -> teks statis drawtext + kegagalan DICATAT (aturan #7).
3. Render sungguhan (Chromium, lambat ~20 dtk): diukur dari PIKSEL -- teks muncul bertahap,
   emoji berwarna tampil (keluhan user 24 Sep: emoji jadi '...'), bagian diam identik dengan
   akhir animasi (tidak ada lompatan).
"""

import os
import subprocess

import numpy as np
import pytest

import auto_render as ar
import overlay_remotion as orr
import style as st

W, H, FPS = 540, 960, 24
ADA_REMOTION = os.path.isdir(os.path.join(orr.REMOTION_DIR, "node_modules"))


# ------------------------------------------------------------------ pilihan

def test_default_pop_dan_bisa_dimatikan(monkeypatch):
    monkeypatch.delenv("TEXT_ANIMATION", raising=False)
    assert orr.animasi_diminta() == "pop"
    for mati in ("none", "off", "", "statis"):
        assert orr.animasi_diminta(mati) is None


def test_animasi_tidak_dikenal_ditolak():
    with pytest.raises(st.StyleError, match="Animasi teks"):
        orr.animasi_diminta("goyang")


# ------------------------------------------------------------------ perencana

def _cakup(kerja):
    """Frame global yang ditutupi rencana (klip + diam x tahan)."""
    frames = []
    for k in kerja:
        if k["jenis"] == "klip":
            frames += list(range(k["dari"], k["sampai"] + 1))
        else:
            frames += list(range(k["frame"], k["frame"] + k["tahan"]))
    return frames


def test_item_panjang_jadi_masuk_diam_keluar_dan_menutup_durasi_tanpa_celah():
    props, kerja = orr.rencana([{"text": "Aksi Merah Laksamana Muda 🩸", "mulai": 0.2, "selesai": 8.8}],
                               24, "pop")
    assert [k["jenis"] for k in kerja] == ["klip", "diam", "klip"]
    dari, dur = 5, 206
    assert _cakup(kerja) == list(range(dari, dari + dur)), "tidak boleh ada celah/tumpang tindih"
    e = orr.masuk_frames("Aksi Merah Laksamana Muda 🩸", "pop")
    assert kerja[0]["sampai"] == dari + e - 1 and kerja[1]["frame"] == dari + e
    assert props[0]["masukFrames"] == e and props[0]["keluarFrames"] == orr.KELUAR
    assert orr.total_frame_chromium(kerja) == e + 1 + orr.KELUAR < dur / 4


def test_item_pendek_dirender_utuh_tanpa_gambar_diam():
    _, kerja = orr.rencana([{"text": "Halo", "mulai": 0, "selesai": 1.0}], 24, "pop")
    assert [k["jenis"] for k in kerja] == ["klip"] and _cakup(kerja) == list(range(0, 24))


def test_banyak_item_masing_masing_tertutup():
    items = [{"text": "Satu dua", "mulai": 0, "selesai": 3}, {"text": "tiga empat lima", "mulai": 3, "selesai": 7}]
    _, kerja = orr.rencana(items, 24, "geser")
    assert _cakup(kerja) == list(range(0, 168))


@pytest.mark.parametrize("animasi,kata,harap", [("pop", 5, 32), ("loncat", 1, 20), ("fade", 9, 20), ("geser", 9, 20)])
def test_masuk_frames(animasi, kata, harap):
    assert orr.masuk_frames(" ".join(["k"] * kata), animasi) == harap


# ------------------------------------------------------------------ cadangan

def _video(path, detik=3, warna="black"):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c={warna}:size={W}x{H}:rate={FPS}:duration={detik}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)


def _frame(mp4, detik=None, nomor=None):
    """Satu frame RGB. `nomor` = indeks frame persis. `-ss detik` membulatkan ke frame
    SESUDAHNYA -- versi awal test lompatan mengambil dua gambar diam dan lolos walau
    gambar diamnya sengaja dirusak (mutasi), jadi perbandingan frame wajib pakai `nomor`."""
    if nomor is not None:
        pilih = ["-vf", f"select=eq(n\\,{nomor})", "-vsync", "0"]
        masuk = ["-i", str(mp4)]
    else:
        pilih, masuk = [], ["-ss", f"{detik}", "-i", str(mp4)]
    r = subprocess.run(["ffmpeg", "-v", "error", *masuk, *pilih, "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.uint8).reshape(H, W, 3).astype(int)


@pytest.fixture
def kanvas_kecil(monkeypatch):
    monkeypatch.setattr(ar, "TARGET_W", W)
    monkeypatch.setattr(ar, "TARGET_H", H)


def test_remotion_gagal_jatuh_ke_teks_statis_dan_dicatat(kanvas_kecil, monkeypatch, tmp_path):
    monkeypatch.setenv("TEXT_ANIMATION", "pop")
    monkeypatch.setattr(orr, "_jalankan_node", lambda *a, **k: (_ for _ in ()).throw(orr.OverlayError("chromium mogok")))
    src, out = tmp_path / "s.mp4", tmp_path / "o.mp4"
    _video(src)
    ar.apply_text_overlay(str(src), [{"start": 0, "end": 3, "text": "Halo dunia", "statis": True}], str(out))
    assert out.exists()
    assert (_frame(out, 1.5) > 150).any(), "teks statis cadangan harus tetap tampil"
    assert ar.TEKS_ANIMASI == {"animasi": "pop", "dipakai": False, "gagal": "chromium mogok"}


def test_animasi_mati_memakai_drawtext_tanpa_node(kanvas_kecil, monkeypatch, tmp_path):
    monkeypatch.setenv("TEXT_ANIMATION", "none")
    monkeypatch.setattr(orr, "_jalankan_node", lambda *a, **k: pytest.fail("node tidak boleh dipanggil"))
    src, out = tmp_path / "s.mp4", tmp_path / "o.mp4"
    _video(src)
    ar.apply_text_overlay(str(src), [{"start": 0, "end": 3, "text": "Halo", "statis": True}], str(out))
    assert out.exists() and ar.TEKS_ANIMASI == {}


def test_subtitle_ucapan_tidak_lewat_remotion(kanvas_kecil, monkeypatch, tmp_path):
    monkeypatch.setenv("TEXT_ANIMATION", "pop")
    monkeypatch.setattr(orr, "_jalankan_node", lambda *a, **k: pytest.fail("ucapan tidak boleh ke Remotion"))
    src, out = tmp_path / "s.mp4", tmp_path / "o.mp4"
    _video(src)
    kata = [{"word": "halo", "start": 0.2, "end": 0.8}, {"word": "semua", "start": 0.8, "end": 1.4}]
    ar.apply_text_overlay(str(src), [{"start": 0.2, "end": 1.5, "text": "halo semua", "words": kata}], str(out))
    assert out.exists()


# ------------------------------------------------------------------ render sungguhan

@pytest.mark.skipif(not ADA_REMOTION, reason="paket Remotion belum terpasang")
def test_render_nyata_bertahap_emoji_berwarna_dan_tanpa_lompatan(kanvas_kecil, monkeypatch, tmp_path):
    monkeypatch.setenv("TEXT_ANIMATION", "pop")
    monkeypatch.setenv("TEXT_POSITION", "tengah")
    monkeypatch.setenv("TEXT_FONT", "santai")
    src, out = tmp_path / "s.mp4", tmp_path / "o.mp4"
    _video(src, detik=4)
    teks = "Aksi Merah Laksamana Muda 🩸"
    ar.apply_text_overlay(str(src), [{"start": 0, "end": 4, "text": teks}], str(out))
    assert ar.TEKS_ANIMASI.get("dipakai") is True, ar.TEKS_ANIMASI

    terang = lambda f: int(((f.sum(axis=2) / 3) > 180).sum())
    merah = lambda f: int(((f[:, :, 0] > 150) & (f[:, :, 1] < 80) & (f[:, :, 2] < 80)).sum())
    awal, tengah = _frame(out, 0.05), _frame(out, 2.0)
    assert terang(awal) < terang(tengah) * 0.2, "animasi masuk: frame awal jauh lebih sedikit teks"
    assert terang(tengah) > 500, "teks harus tampil setelah animasi masuk"
    assert merah(tengah) > 80, "emoji 🩸 harus tampil BERWARNA (merah), bukan '...' atau kotak"
    # Kontrol negatif untuk pengukur merah: frame sebelum emoji muncul tidak merah.
    assert merah(awal) < 10

    e = orr.masuk_frames(teks, "pop")
    akhir_animasi = _frame(out, nomor=e - 1)     # frame terakhir yang dirender Chromium
    mulai_diam = _frame(out, nomor=e + 1)        # sudah gambar diam hasil loop ffmpeg
    selisih = np.abs(akhir_animasi - mulai_diam).mean()
    assert selisih < 1.0, f"lompatan antara animasi dan gambar diam: {selisih:.2f}"
    # Kontrol positif pengukur: frame tengah animasi JELAS berbeda dari gambar diam.
    assert np.abs(_frame(out, nomor=e // 2) - mulai_diam).mean() > 1.0
    tutup = _frame(out, 3.93)   # frame ke-94 dari 96, di dalam jendela keluar
    assert terang(tutup) < terang(tengah) * 0.5, "animasi keluar: teks memudar di akhir"
