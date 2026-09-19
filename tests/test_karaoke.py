"""Subtitle karaoke: seluruh frasa diam di tempat, hanya kata aktif yang berganti warna.

Yang diuji bukan string filternya, tapi PIKSEL hasil render ffmpeg sungguhan --
karena tiga hal yang paling mudah salah hanya kelihatan di gambar:
  1. frasa tidak boleh bergeser saat kata berikutnya menyala (gaya lama bergeser),
  2. hanya SATU kata yang menyala pada satu waktu,
  3. kata dengan tinggi huruf berbeda harus bertumpu di garis dasar yang sama
     (y pada drawtext = puncak tinta string itu, bukan garis dasar).
"""

import subprocess

import numpy as np
import pytest
from PIL import Image

import auto_render as ar
import subtitle_layout as lay

W, H = 1080, 1920
KUNING = np.array([255, 212, 0])


# ---------- bahan ----------

def _kata(teks, mulai=0.2, per=0.5):
    return [{"word": w, "start": round(mulai + i * per, 3), "end": round(mulai + i * per + per - 0.05, 3)}
            for i, w in enumerate(teks.split())]


def _scene(teks, mulai=0.2, per=0.5):
    kata = _kata(teks, mulai, per)
    return {"start": kata[0]["start"], "end": kata[-1]["end"] + 0.1, "text": teks, "words": kata}


@pytest.fixture(scope="module")
def latar(tmp_path_factory):
    """Latar abu-abu DATAR: piksel putih/kuning/hitam gampang dipisahkan darinya."""
    p = str(tmp_path_factory.mktemp("kar") / "latar.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color=c=0x707070:size={W}x{H}:rate=24:duration=5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", p], check=True, capture_output=True)
    return p


def _render(latar, scenes, tmp_path):
    keluaran = str(tmp_path / "hasil.mp4")
    ar.apply_text_overlay(latar, scenes, keluaran)
    return keluaran


def _bingkai(video, detik):
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{detik:.3f}", "-i", video, "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(H, W, 3).astype(int)


def _kuning(bingkai):
    return np.all(np.abs(bingkai - KUNING) < 40, axis=2)


def _putih(bingkai):
    return np.all(bingkai > 235, axis=2)


def _kolom(mask):
    xs = np.where(mask.any(axis=0))[0]
    return (xs.min(), xs.max()) if len(xs) else None


def _tinta(bingkai):
    """Semua piksel teks (putih ATAU kuning)."""
    return _putih(bingkai) | _kuning(bingkai)


# ---------- tata letak (murni Python) ----------

def test_frasa_terpusat_dan_kata_berurutan_dari_kiri():
    t = lay.layout_group(_kata("Berapa banyak"), font_path=ar.FONT_PATH, fs=86,
                         canvas_w=W, max_lines=2, y_top=1400)
    xs = [w["x"] for w in t["lines"][0]["words"]]
    assert xs == sorted(xs) and xs[1] > xs[0]
    kiri = xs[0]
    kanan = xs[1] + lay.text_width(ar.FONT_PATH, 86, "banyak")
    assert abs(kiri - (W - kanan)) <= 2, "baris harus terpusat"


def test_jangkar_bawah_satu_baris_dan_dua_baris_sama():
    """Garis dasar baris TERAKHIR tetap di tempat yang sama; yang bertambah hanya
    ke atas. Gaya lama menempel di atas sehingga teks satu baris melayang."""
    satu = lay.layout_group(_kata("yang hilang"), font_path=ar.FONT_PATH, fs=86,
                            canvas_w=W, max_lines=2, y_top=1400)
    dua = lay.layout_group(_kata("Berapa banyak calon pembeli"), font_path=ar.FONT_PATH, fs=86,
                           canvas_w=W, max_lines=2, y_top=1400)
    assert len(satu["lines"]) == 1 and len(dua["lines"]) == 2
    assert satu["lines"][-1]["baseline"] == dua["lines"][-1]["baseline"]


def test_kelompok_yang_kepanjangan_ditolak_bukan_dipangkas():
    panjang = lay.layout_group(_kata("informasi unit yang tidak pernah diperbarui oleh tim"),
                               font_path=ar.FONT_PATH, fs=86, canvas_w=W, max_lines=2, y_top=1400)
    assert panjang is None


def test_kotak_membungkus_seluruh_teks_dalam_kanvas():
    t = lay.layout_group(_kata("informasi unit yang tidak update"), font_path=ar.FONT_PATH,
                         fs=86, canvas_w=W, max_lines=2, y_top=1400)
    x, y, w, h = t["box"]
    assert x >= 0 and x + w <= W and h > 0


# ---------- pembangun filter ----------

def test_gaya_bawaan_adalah_karaoke():
    assert ar.SUBTITLE_STYLE == "karaoke"
    assert ar.SUBTITLE_STYLES["karaoke"].get("highlight")


def test_satu_filter_per_kata_ditambah_satu_kotak():
    f = ar.build_drawtext_chain([_scene("satu dua tiga empat")], H, W)
    assert sum(x.startswith("drawtext") for x in f) == 4
    assert sum(x.startswith("drawbox") for x in f) == 1


def test_gaya_tanpa_kotak_memakai_outline_dan_tidak_ada_drawbox(monkeypatch):
    monkeypatch.setattr(ar, "SUBTITLE_STYLE", "karaoke-tebal")
    f = ar.build_drawtext_chain([_scene("satu dua")], H, W)
    assert not any(x.startswith("drawbox") for x in f)
    assert all("borderw=" in x for x in f if x.startswith("drawtext"))


def test_gaya_kapital_mengubah_teks_jadi_huruf_besar(monkeypatch):
    monkeypatch.setattr(ar, "SUBTITLE_STYLE", "karaoke-kapital")
    f = ar.build_drawtext_chain([_scene("halo dunia")], H, W)
    assert "text='HALO'" in f[1] and "text='DUNIA'" in f[2]


def test_kelompok_terlalu_panjang_dipecah_dua_tanpa_membuang_kata():
    teks = "informasi unit yang tidak pernah diperbarui oleh tim"
    f = ar.build_drawtext_chain([_scene(teks)], H, W)
    kata = [x.split("text='")[1].split("'")[0] for x in f if x.startswith("drawtext")]
    assert kata == teks.split(), "tidak ada kata yang hilang"
    assert sum(x.startswith("drawbox") for x in f) == 2, "dua tampilan, dua kotak"


def test_scene_tanpa_kata_tetap_memakai_jalur_lama():
    """Naskah tulisan LLM (mode voice-over AI) tidak punya timestamp kata."""
    f = ar.build_drawtext_chain([{"start": 0, "end": 2, "text": "teks tanpa kata"}], H, W)
    assert len(f) == 1 and "alpha=" in f[0]


def test_karakter_khusus_di_kata_di_escape():
    sc = _scene("100% ok: siap")
    sc["words"][0]["word"] = "100%"
    sc["words"][1]["word"] = "ok:"
    f = ar.build_drawtext_chain([sc], H, W)
    assert "100\\%" in f[1] and "ok\\:" in f[2]


# ---------- piksel nyata ----------

def test_hanya_satu_kata_menyala_dan_berpindah_sesuai_waktu(latar, tmp_path):
    sc = _scene("Berapa banyak calon pembeli", mulai=0.2, per=0.6)
    v = _render(latar, [sc], tmp_path)
    t = lay.layout_group(sc["words"], font_path=ar.FONT_PATH, fs=86, canvas_w=W, max_lines=2,
                         y_top=ar.subtitle_geometry(W, H)[1])
    posisi = [w for ln in t["lines"] for w in ln["words"]]

    for i, w in enumerate(sc["words"]):
        tengah = (w["start"] + w["end"]) / 2
        kuning = _kuning(_bingkai(v, tengah))
        assert kuning.sum() > 500, f"kata ke-{i + 1} tidak menyala"
        xs = np.where(kuning.any(axis=0))[0]
        px = posisi[i]["x"]
        lebar = lay.text_width(ar.FONT_PATH, 86, w["word"])
        assert px - 6 <= xs.min() and xs.max() <= px + lebar + 6, (
            f"warna kuning kata ke-{i + 1} jatuh di luar posisinya ({xs.min()}..{xs.max()})")


def test_frasa_tidak_bergeser_saat_kata_berikutnya_menyala(latar, tmp_path):
    """INTI perubahannya. Gaya lama menggambar teks kumulatif yang di-center ulang
    tiap kata baru, jadi kata sebelumnya bergeser ke kiri."""
    sc = _scene("Berapa banyak calon", mulai=0.2, per=0.6)
    v = _render(latar, [sc], tmp_path)
    kolom = []
    for w in sc["words"]:
        b = _bingkai(v, (w["start"] + w["end"]) / 2)
        kolom.append(_kolom(_tinta(b) & (np.arange(H)[:, None] > 1300)))
    kiri = [int(k[0]) for k in kolom]
    kanan = [int(k[1]) for k in kolom]
    # Toleransi 2 px: piksel tepi anti-aliasing bisa terbaca putih di satu bingkai
    # dan kuning di bingkai lain (terukur 185 vs 186). Pergeseran gaya lama puluhan px.
    assert max(kiri) - min(kiri) <= 2 and max(kanan) - min(kanan) <= 2, (
        f"frasa bergeser antar kata: kiri={kiri} kanan={kanan}")


def test_kontrol_positif_gaya_lama_memang_bergeser(latar, tmp_path, monkeypatch):
    """Kontrol untuk tes di atas: dengan gaya kumulatif lama alat ukur yang sama
    HARUS melihat pergeseran. Tanpa ini 'tidak bergeser' bisa berarti alat ukurnya
    yang tidak bekerja."""
    monkeypatch.setattr(ar, "SUBTITLE_STYLE", "putih-tebal")
    sc = _scene("Berapa banyak calon", mulai=0.2, per=0.6)
    v = _render(latar, [sc], tmp_path)
    kolom = []
    for w in sc["words"]:
        b = _bingkai(v, (w["start"] + w["end"]) / 2)
        kolom.append(_kolom(_putih(b) & (np.arange(H)[:, None] > 1300)))
    kiri = [int(k[0]) for k in kolom]
    assert max(kiri) - min(kiri) > 20, (
        f"gaya lama seharusnya bergeser jauh lebih dari toleransi 2 px; kiri={kiri}")


def test_kata_dengan_tinggi_huruf_berbeda_bertumpu_di_garis_dasar_sama(latar, tmp_path):
    """'aa' tidak punya huruf tinggi, 'Hl' punya. Tanpa y=garis_dasar-ascent,
    drawtext menaruh PUNCAK TINTA di y yang sama sehingga 'aa' berdiri lebih rendah."""
    sc = _scene("Hl aa Hl", mulai=0.2, per=0.6)
    v = _render(latar, [sc], tmp_path)
    b = _bingkai(v, 0.4)          # semua kata tampil (satu menyala)
    tinta = _tinta(b)
    t = lay.layout_group(sc["words"], font_path=ar.FONT_PATH, fs=86, canvas_w=W, max_lines=2,
                         y_top=ar.subtitle_geometry(W, H)[1])
    dasar = t["lines"][0]["baseline"]
    ymax = []
    for w in t["lines"][0]["words"]:
        lebar = int(lay.text_width(ar.FONT_PATH, 86, w["word"]))
        blok = tinta[:, w["x"]: w["x"] + lebar]
        ys = np.where(blok.any(axis=1))[0]
        ymax.append(ys.max())
    assert max(ymax) - min(ymax) <= 1, f"dasar kata tidak sejajar: {ymax}"
    assert abs(ymax[0] - dasar) <= 2, f"dasar menyimpang dari yang dihitung ({ymax[0]} vs {dasar})"


def test_kotak_hitam_benar_benar_membungkus_teks(latar, tmp_path):
    sc = _scene("Berapa banyak", mulai=0.2, per=0.6)
    v = _render(latar, [sc], tmp_path)
    b = _bingkai(v, 0.5)
    tinta = _tinta(b)
    ys, xs = np.where(tinta & (np.arange(H)[:, None] > 1300))
    gelap = (b.sum(axis=2) < 200)               # kotak black@0.55 di atas 0x70 -> ~0x32
    yk, xk = np.where(gelap & (np.arange(H)[:, None] > 1300))
    assert xk.min() < xs.min() - 10 and xk.max() > xs.max() + 10
    assert yk.min() < ys.min() - 5 and yk.max() > ys.max() + 5


# ---------- teks on-screen TANPA timestamp: dipecah, tidak dipangkas ----------

def test_teks_tanpa_timestamp_tidak_dipangkas_dengan_titik_tiga():
    """Terlihat di video nyata: 'Ratusan orang berkumpul, diskusi aktif' tampil
    sebagai 'Ratusan orang berkumpul,...' karena jalur ini memakai wrap_text langsung."""
    teks = "Ratusan orang berkumpul, diskusi aktif"
    f = ar.build_drawtext_chain([{"start": 10, "end": 15, "text": teks}], H, W)
    assert f, "harus ada filter"
    assert not any("..." in x for x in f), "tidak boleh ada teks yang dipangkas"
    tampil = " ".join(x.split("text='")[1].split("':")[0] for x in f).replace("\n", " ")
    assert all(k in tampil for k in teks.replace(",", "").split()), f"kata hilang: {tampil!r}"


def test_pecahan_teks_membagi_waktu_dan_berurutan_tanpa_tumpang_tindih():
    f = ar.build_drawtext_chain(
        [{"start": 10.0, "end": 15.0, "text": "Ratusan orang berkumpul, diskusi aktif sekali"}], H, W)
    import re
    rentang = [tuple(map(float, re.search(r"between\(t,([\d.]+),([\d.]+)\)", x).groups())) for x in f]
    assert len(rentang) >= 2
    assert rentang[0][0] == pytest.approx(10.0) and rentang[-1][1] == pytest.approx(15.0, abs=0.01)
    for (a1, b1), (a2, b2) in zip(rentang, rentang[1:]):
        assert b1 == pytest.approx(a2, abs=0.01), "pecahan harus bersambung tanpa celah/tumpang tindih"


def test_teks_pendek_tetap_satu_tampilan():
    f = ar.build_drawtext_chain([{"start": 0, "end": 3, "text": "Halo dunia"}], H, W)
    assert len(f) == 1


# ---------- prompt brief: durasi klip nyata ----------

def test_durasi_bahan_ikut_ke_prompt_sebagai_fakta_terukur():
    import agent1_2_brief as brief
    p = brief.build_prompt(["a.mp4", "b.mp4", "c.mp4"], {}, jumlah_gambar=3,
                           transkrip={}, durasi_bahan=[3.6, 6.4, 5.5])
    assert "bahan 1 = 3.6 dtk" in p and "bahan 3 = 5.5 dtk" in p and "total 15.5 dtk" in p
    assert "TIDAK dibacakan" in p, "mode suara asli: naskah bukan untuk dibacakan"


def test_tanpa_durasi_bahan_prompt_lama_tidak_berubah():
    import agent1_2_brief as brief
    p = brief.build_prompt(["a.mp4"], {}, jumlah_gambar=1)
    assert "DURASI NYATA" not in p
    assert "natural saat dibacakan, 20-35 detik (kira-kira 55-95 kata)," in p


def test_kalimat_naskah_tidak_rusak_saat_ada_durasi_bahan():
    """Regresi dari kesalahan saya sendiri: variabel durasi_note sempat tertimpa
    catatan klip sehingga kalimat '...natural saat dibacakan, {durasi}' jadi kacau."""
    import agent1_2_brief as brief
    p = brief.build_prompt(["a.mp4"], {}, jumlah_gambar=1, durasi_bahan=[5.0])
    assert "natural saat dibacakan, 20-35 detik (kira-kira 55-95 kata)," in p


def test_prompt_tanpa_ucapan_melarang_menebak_jenis_acara():
    import agent1_2_brief as brief
    note = brief.build_transcript_note({})
    assert "jenis acara" in note and "kata netral" in note


# ---------- caption jujur soal batas ----------

def test_caption_mengingatkan_teks_dibuat_dari_tampilan_saja(monkeypatch, tmp_path):
    import run_and_deliver as rd
    brief = {"judul": "Uji", "hashtags": [], "audio_mode": "original",
             "transcript_coverage": {"ditranskrip": 0, "total_bahan": 3,
                                     "tanpa_subtitle": ["a", "b", "c"],
                                     "alasan": {"a": "tanpa_ucapan", "b": "tanpa_ucapan", "c": "tanpa_ucapan"}}}
    terkirim = {}
    monkeypatch.setattr(rd, "read_json", lambda *a, **k: brief)
    monkeypatch.setattr(rd, "ringkasan_biaya", lambda run_id: "")
    monkeypatch.setattr(rd, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(rd, "draft_video_path_for_run", lambda r: str(tmp_path / "v.mp4"))
    monkeypatch.setattr(rd, "draft_thumb_path_for_run", lambda r: str(tmp_path / "v.jpg"))
    monkeypatch.setattr(rd, "send_video",
                        lambda c, p, *, chat_id, thumb_path=None: terkirim.setdefault("caption", c) or True)
    rd.deliver_plugin("run1", "123")
    assert "TAMPILAN saja" in terkirim["caption"]
