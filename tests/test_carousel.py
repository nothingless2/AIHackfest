"""Carousel (scripts/carousel.py + remotion/src/Carousel.jsx): LLM mengusulkan slide, KODE
memvalidasi (angka & kutipan harus ada di sumber) dan MENGUKUR hasil render (kotak aman, zona UI
TikTok, wajah, kontras)."""

import json
import os

import numpy as np
import pytest

import carousel as cr

SUMBER = ("Ganti API key di OpenClaw bikin pusing. Lewat dashboard error, lewat CLI error lagi. "
          "Di Hermes tinggal ganti lewat CLI, langsung beres tanpa error. Jatah gratis 50 permintaan per hari, "
          "biayanya 1.000 rupiah.")


def _naskah(**ubah):
    d = {"slide": [
        {"jenis": "hook", "judul": "Ganti API key kok error terus?", "sorot": "error", "sub": "Tiga cara gagal"},
        {"jenis": "daftar", "judul": "Yang sudah dicoba", "butir": ["Lewat dashboard", "Lewat CLI"]},
        {"jenis": "statistik", "angka": "50", "label": "permintaan gratis per hari"},
        {"jenis": "kutipan", "teks": "langsung beres tanpa error", "oleh": "pengalaman"},
        {"jenis": "cta", "judul": "Simpan biar nggak lupa", "sub": "Bagikan ke temanmu"}],
        "caption": "Cerita ganti API key.", "hashtags": ["#apikey", "#tutorial", "bukan tagar", "#a b"]}
    d.update(ubah)
    return d


# ------------------------------------------------------------------ validasi

def test_naskah_sah_diterima_dan_tagar_disaring():
    slides, caption, tag, masalah = cr.validasi(_naskah(), SUMBER)
    assert masalah == [] and [s["jenis"] for s in slides] == ["hook", "daftar", "statistik", "kutipan", "cta"]
    assert slides[0]["sorot"] == "error" and caption and tag == ["#apikey", "#tutorial"]


@pytest.mark.parametrize("angka, sah", [("50", True), ("1000", True), ("1.000", True), ("75", False), ("50%", True),
                                        ("lima puluh", False)])
def test_angka_statistik_harus_ada_di_sumber(angka, sah):
    n = _naskah()
    n["slide"][2]["angka"] = angka
    _, _, _, masalah = cr.validasi(n, SUMBER)
    assert (masalah == []) is sah, masalah


def test_kutipan_harus_persis_dari_sumber():
    n = _naskah()
    n["slide"][3]["teks"] = "Hermes jauh lebih cepat dan murah"
    assert any("kutipan" in m for m in cr.validasi(n, SUMBER)[3])


@pytest.mark.parametrize("ubah, kata_kunci", [
    (lambda s: s.insert(0, s.pop(1)), "pertama harus jenis hook"),
    (lambda s: s.pop(), "terakhir harus jenis cta"),
    (lambda s: s.__setitem__(0, {**s[0], "judul": " ".join(["kata"] * 12)}), "judul harus 2-9"),
    (lambda s: s.__setitem__(1, {**s[1], "butir": ["satu"]}), "butir"),
    (lambda s: s.__setitem__(1, {"jenis": "meme"}), "jenis tidak dikenal"),
    (lambda s: s.__delitem__(slice(1, 4)), "jumlah slide"),
])
def test_struktur_dan_batas_kata_ditegakkan(ubah, kata_kunci):
    n = _naskah()
    ubah(n["slide"])
    assert any(kata_kunci in m for m in cr.validasi(n, SUMBER)[3])


def test_sorot_yang_bukan_kata_judul_dibuang():
    n = _naskah()
    n["slide"][0]["sorot"] = "dashboard"
    slides, _, _, masalah = cr.validasi(n, SUMBER)
    assert masalah == [] and "sorot" not in slides[0]


def test_balasan_bukan_objek_tidak_meledak():
    assert cr.validasi(["bukan"], SUMBER)[3] and cr.validasi({"slide": "x"}, SUMBER)[3]


# ------------------------------------------------------------------ tulis ulang

def _chat(*balasan):
    panggil = []

    def f(pesan, model, label):
        panggil.append(pesan)
        return balasan[min(len(panggil), len(balasan)) - 1]
    f.panggil = panggil
    return f


def test_naskah_salah_ditulis_ulang_sekali():
    salah = _naskah()
    salah["slide"][2]["angka"] = "99"
    chat = _chat(salah, _naskah())
    slides, _, _, catatan = cr.minta_slide(SUMBER, 5, "ig", chat=chat)
    assert len(chat.panggil) == 2 and len(slides) == 5 and "ditulis ulang" in catatan[0]
    assert "tidak ada di sumber" in chat.panggil[1][-1]["content"], "masalahnya dikirim balik ke model"


def test_tetap_salah_tapi_struktur_utuh_slidenya_dibuang():
    salah = _naskah()
    salah["slide"][2]["angka"] = "99"
    slides, _, _, catatan = cr.minta_slide(SUMBER, 5, "ig", chat=_chat(salah, salah))
    assert [s["jenis"] for s in slides] == ["hook", "daftar", "kutipan", "cta"] and "dibuang" in catatan[0]


def test_tetap_salah_dan_struktur_rusak_ditolak():
    rusak = {"slide": [{"jenis": "isi", "judul": "x", "isi": "terlalu pendek"}]}
    with pytest.raises(cr.CarouselError) as e:
        cr.minta_slide(SUMBER, 5, "ig", chat=_chat(rusak, rusak))
    assert e.value.kode == "naskah_tidak_sah"


# ------------------------------------------------------------------ tata letak & QA

def test_kotak_tiktok_menjauhi_zona_ui():
    from qa_video import ZONA_UI
    W, H = cr.UKURAN["tiktok"]
    k = cr.kotak_teks("tiktok", W, H)
    for x0, x1, y0, y1 in ZONA_UI.values():
        tumpang_x = k["x"] < W * x1 and k["x"] + k["w"] > W * x0
        tumpang_y = k["y"] < H * y1 and k["y"] + k["h"] > H * y0
        assert not (tumpang_x and tumpang_y)


def test_teks_pindah_menjauhi_wajah():
    W, H = cr.UKURAN["ig"]
    atas = (400, 200, 300, 300)
    k = cr.kotak_teks("ig", W, H, atas)
    assert k["zona"] == "bawah" and k["y"] >= atas[1] + atas[3]
    bawah = (400, 900, 300, 300)
    k = cr.kotak_teks("ig", W, H, bawah)
    assert k["zona"] == "atas" and k["y"] + k["h"] <= bawah[1]


def _lapis(W=300, H=400):
    penuh = np.zeros((H, W, 3), np.uint8)
    teks = np.zeros((H, W, 4), np.uint8)
    return penuh, teks


def _tulis(penuh, teks, y0, y1, x0, x1, warna=(255, 255, 255)):
    teks[y0:y1, x0:x1] = (*warna, 255)
    penuh[y0:y1, x0:x1] = warna


KOTAK = {"x": 30, "y": 60, "w": 240, "h": 280, "zona": "tengah"}


def test_qa_slide_bersih_lolos():
    """Kontrol positif untuk tes masalah di bawah."""
    penuh, teks = _lapis()
    _tulis(penuh, teks, 100, 140, 40, 200)
    r = cr.periksa_slide(penuh, teks, KOTAK, "ig")
    assert r["masalah"] == [] and r["kontras"] > 15


def test_qa_teks_keluar_kotak():
    penuh, teks = _lapis()
    _tulis(penuh, teks, 100, 140, 40, 200)
    _tulis(penuh, teks, 360, 395, 40, 200)          # di bawah kotak aman
    assert any("keluar" in m for m in cr.periksa_slide(penuh, teks, KOTAK, "ig")["masalah"])


def test_qa_zona_ui_tiktok_dan_wajah():
    penuh, teks = _lapis(300, 534)
    k = {"x": 21, "y": 53, "w": 270, "h": 420, "zona": "tengah"}
    _tulis(penuh, teks, 300, 400, 268, 291)          # zona tombol kanan
    assert any("zona UI" in m for m in cr.periksa_slide(penuh, teks, k, "tiktok")["masalah"])
    assert not any("zona UI" in m for m in cr.periksa_slide(penuh, teks, k, "ig")["masalah"]), "IG tanpa zona itu"
    penuh, teks = _lapis()
    _tulis(penuh, teks, 100, 140, 40, 200)
    assert any("wajah" in m for m in cr.periksa_slide(penuh, teks, KOTAK, "ig", (50, 90, 100, 100))["masalah"])


def test_qa_kontras_rendah():
    penuh, teks = _lapis()
    penuh[:] = (250, 250, 250)
    _tulis(penuh, teks, 100, 140, 40, 200, warna=(245, 245, 245))
    assert any("kontras" in m for m in cr.periksa_slide(penuh, teks, KOTAK, "ig")["masalah"])


# ------------------------------------------------------------------ sumber, foto, gerbang

def _rekam_run(run_id, chat):
    import revisi
    import time
    os.makedirs(revisi.REVISI_DIR, exist_ok=True)
    rec = {"run_id": run_id, "chat_id": chat, "dibuat": time.time(), "bahan": ["a.mp4"], "prefix": "x",
           "brief": {"konteks_user": "topik api key", "full_voice_over": "naskah",
                     "transcript_segments": {"a.mp4": [{"start": 0, "end": 1, "text": "ucapan asli"}]}}}
    json.dump(rec, open(os.path.join(revisi.REVISI_DIR, f"{run_id}.json"), "w"))


def test_sumber_dari_run_memakai_ucapan_dan_menolak_chat_lain():
    _rekam_run("abc12345", "DM A")
    teks, video = cr.sumber_dari_run("abc12345", "DM A")
    assert "ucapan asli" in teks and "topik api key" in teks and video == []
    with pytest.raises(Exception) as e:
        cr.sumber_dari_run("abc12345", "DM B")
    assert getattr(e.value, "kode", "") == "revisi_chat_lain"


@pytest.fixture
def lingkungan(monkeypatch, tmp_path):
    import run_lock
    monkeypatch.setattr(run_lock, "RENDER_LOCK_PATH", str(tmp_path / "pipeline.lock"))
    monkeypatch.setattr(cr, "CAROUSEL_DIR", str(tmp_path / "carousel"))
    monkeypatch.setattr(cr, "UKURAN", {"ig": (270, 338), "tiktok": (270, 480)})
    return tmp_path


@pytest.mark.parametrize("arg, kode", [
    ({"chat_id": ""}, "chat_tidak_diketahui"),
    ({"teks": "terlalu pendek"}, "sumber_kurang"),
    ({"platform": "youtube"}, "argumen_invalid"),
    ({"jumlah": 11}, "argumen_invalid"),
    ({"foto": ["/etc/passwd"]}, "foto_invalid"),
])
def test_gerbang_sebelum_llm(lingkungan, arg, kode):
    chat = _chat(_naskah())
    with pytest.raises(cr.CarouselError) as e:
        cr.buat(**{"chat_id": "DM A", "teks": SUMBER, **arg}, chat=chat)
    assert e.value.kode == kode and chat.panggil == [], "ditolak SEBELUM panggilan LLM"


def test_render_lain_berjalan_ditolak(lingkungan):
    import run_lock
    with run_lock.acquire_render_lock("video_lain"):
        with pytest.raises(cr.CarouselError) as e:
            cr.buat(chat_id="DM A", teks=SUMBER, chat=_chat(_naskah()))
    assert e.value.kode == "render_sibuk"
    assert os.listdir(cr.CAROUSEL_DIR) == [], "folder hasil yang setengah jadi dibersihkan"


def test_foto_stok_hanya_dari_host_pexels(monkeypatch, tmp_path):
    import broll
    monkeypatch.setenv("PEXELS_API_KEY", "palsu")
    monkeypatch.setattr(broll, "_http_get_json", lambda url, h: {"photos": [
        {"id": 1, "src": {"portrait": "https://evil.example/x.jpg"}},
        {"id": 2, "photographer": "Ani", "url": "https://www.pexels.com/photo/2",
         "src": {"portrait": "https://images.pexels.com/photos/2/a.jpg"}}]})
    unduh = []
    monkeypatch.setattr(broll, "_unduh_ke", lambda u, t, m: unduh.append(u) or open(t, "wb").write(b"x"))
    path, kredit = cr.foto_stok("coffee", 270, 338, str(tmp_path))
    assert unduh == ["https://images.pexels.com/photos/2/a.jpg"] and kredit["fotografer"] == "Ani"
    monkeypatch.setenv("PEXELS_API_KEY", "")
    with pytest.raises(cr.CarouselError):
        cr.foto_stok("coffee", 270, 338, str(tmp_path))


def test_cli_error_jadi_json(capsys):
    assert cr.main(["--teks", SUMBER]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["kode"] == "chat_tidak_diketahui"


# ------------------------------------------------------------------ render sungguhan

def test_render_nyata_dua_ukuran_lolos_qa(lingkungan):
    h = cr.buat(chat_id="DM A", teks=SUMBER, platform="keduanya", jumlah=5, chat=_chat(_naskah()))
    assert set(h["slide"]) == {"ig", "tiktok"} and all(len(v) == 5 for v in h["slide"].values())
    from PIL import Image
    assert Image.open(h["slide"]["ig"][0]).size == (270, 338) and Image.open(h["slide"]["tiktok"][0]).size == (270, 480)
    assert h["qa"]["ig"]["lolos"] and h["qa"]["tiktok"]["lolos"], h["qa"]
    meta = json.load(open(os.path.join(cr.CAROUSEL_DIR, h["carousel_id"], "meta.json")))
    assert meta["chat_id"] == "DM A"


def test_render_nyata_tema_terang_angka_tetap_terbaca(lingkungan):
    """Regresi 1 Okt: preset bersih -> angka statistik putih di atas putih (kontras 1,0)."""
    h = cr.buat(chat_id="DM A", teks=SUMBER, nama_gaya="bersih", platform="ig", jumlah=5, chat=_chat(_naskah()))
    stat = h["qa"]["ig"]["per_slide"][2]
    assert stat["kontras"] >= cr.KONTRAS_MIN and not stat["masalah"]


def test_render_nyata_judul_tidak_menimpa_wajah(lingkungan, monkeypatch):
    """Regresi 1 Okt: judul hook di bawah wajah meluap ke atas sampai dagu."""
    from test_zoom_logo import _video_wajah
    import hermes_render
    _video_wajah(lingkungan / "wajah.mp4")
    _, kotak = cr.siapkan_gambar(str(lingkungan / "wajah.png"), 270, 338)
    assert kotak, "kontrol: wajah sintetis terdeteksi di ukuran slide (tanpa ini tes ini hampa)"
    monkeypatch.setattr(hermes_render, "MEDIA_ROOTS", [str(lingkungan)])
    h = cr.buat(chat_id="DM A", teks=SUMBER, nama_gaya="hype", platform="ig", jumlah=5,
                foto=[str(lingkungan / "wajah.png")], chat=_chat(_naskah()))
    hook = h["qa"]["ig"]["per_slide"][0]
    assert not hook["masalah"], hook


def test_render_nyata_judul_lebar_tidak_keluar_kotak(lingkungan):
    """Regresi 2 Okt (carousel user): "Perintah AI yang / Langsung Kepakai" -- dua baris sama-sama 16
    karakter, tapi baris kedua lebih LEBAR; ukuran huruf dihitung dari baris pertama saja, sehingga
    baris kedua terpotong di tepi kanan."""
    naskah = _naskah()
    naskah["slide"][1] = {"jenis": "isi", "judul": "Perintah AI yang Langsung Kepakai",
                          "isi": "Mulai dari perintah AI yang langsung kepakai."}
    h = cr.buat(chat_id="DM A", teks=SUMBER, nama_gaya="hype", platform="ig", jumlah=5, chat=_chat(naskah))
    slide = h["qa"]["ig"]["per_slide"][1]
    assert not slide["masalah"], slide


# ------------------------------------------------------------------ latar bersama (gambar user / stok)

def test_kata_kunci_latar_divalidasi():
    slides, *_ = cr.validasi(_naskah(kata_kunci_latar="minimal desk laptop"), SUMBER)
    assert slides[0]["cari_latar"] == "minimal desk laptop"
    for buruk in ("meja kerja estetik sekali banget pokoknya", "https://x.y/z.jpg", "日本"):
        slides, *_ = cr.validasi(_naskah(kata_kunci_latar=buruk), SUMBER)
        assert "cari_latar" not in slides[0]


def test_latar_di_luar_cache_ditolak_sebelum_llm(lingkungan):
    chat = _chat(_naskah())
    with pytest.raises(cr.CarouselError) as e:
        cr.buat(chat_id="DM A", teks=SUMBER, latar="/etc/passwd", chat=chat)
    assert e.value.kode == "foto_invalid" and chat.panggil == []


def _tangkap_render(monkeypatch):
    import overlay_remotion as orr
    asli, tangkap = orr.render_carousel, {}

    def render(slides, *a, **k):
        tangkap["slides"] = slides
        return asli(slides, *a, **k)
    monkeypatch.setattr(orr, "render_carousel", render)
    return tangkap


def _gambar_terang(path):
    from PIL import Image
    Image.new("RGB", (400, 500), (232, 226, 240)).save(path)
    return str(path)


def test_latar_user_dipakai_semua_slide_dan_tetap_terbaca(lingkungan, monkeypatch):
    """Termasuk regresi 2 Okt: angka statistik preset bersih di atas latar terang kontras 2,7."""
    import hermes_render
    monkeypatch.setattr(hermes_render, "MEDIA_ROOTS", [str(lingkungan)])
    tangkap = _tangkap_render(monkeypatch)
    h = cr.buat(chat_id="DM A", teks=SUMBER, nama_gaya="bersih", platform="ig", jumlah=5,
                latar=_gambar_terang(lingkungan / "latar.jpg"), chat=_chat(_naskah()))
    assert all(s["latar"] and s["gambar"] for s in tangkap["slides"]), "satu latar untuk semua slide"
    assert h["qa"]["ig"]["lolos"], h["qa"]["ig"]["per_slide"]


def test_latar_stok_memakai_kata_kunci_latar_dan_gagalnya_dilaporkan(lingkungan, monkeypatch):
    import broll
    dicari = []

    def stok(query, W, H, folder, pilih=0):
        dicari.append(query)
        return _gambar_terang(os.path.join(folder, "s.jpg")), {"id": 7, "fotografer": "Ani", "halaman": "", "query": query}
    monkeypatch.setattr(cr, "foto_stok", stok)
    tangkap = _tangkap_render(monkeypatch)
    h = cr.buat(chat_id="DM A", teks=SUMBER, platform="ig", jumlah=5, latar="stok",
                chat=_chat(_naskah(kata_kunci_latar="coffee desk")))
    assert dicari == ["coffee desk"] and all(s["latar"] for s in tangkap["slides"])
    assert h["kredit_foto"] == [{"id": 7, "fotografer": "Ani", "halaman": "", "query": "coffee desk", "latar": True}]
    monkeypatch.setattr(cr, "foto_stok", lambda *a, **k: (_ for _ in ()).throw(cr.CarouselError("stok_kosong", "tidak ada")))
    h = cr.buat(chat_id="DM A", teks=SUMBER, platform="ig", jumlah=5, latar="stok",
                chat=_chat(_naskah(kata_kunci_latar="coffee desk")))
    assert h["ok"] and any("foto latar gagal" in c for c in h["catatan"]), "carousel tetap jadi, gagalnya disebut"
    assert not any(s["latar"] for s in tangkap["slides"])


def test_foto_stok_bergilir_di_antara_hasil_yang_sah(monkeypatch, tmp_path):
    import broll
    monkeypatch.setenv("PEXELS_API_KEY", "palsu")
    monkeypatch.setattr(broll, "_http_get_json", lambda url, h: {"photos": [
        {"id": i, "src": {"portrait": f"https://images.pexels.com/photos/{i}/a.jpg"}} for i in (1, 2, 3)]})
    monkeypatch.setattr(broll, "_unduh_ke", lambda u, t, m: open(t, "wb").write(b"x"))
    assert [cr.foto_stok("x", 270, 338, str(tmp_path), pilih=n)[1]["id"] for n in (0, 1, 2, 3)] == [1, 2, 3, 1]
