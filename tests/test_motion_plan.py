"""Validasi & penjadwalan motion graphic (scripts/motion_plan.py).

LLM mengusulkan; KODE menegakkan katalog, menolak angka karangan, dan memberi waktu dari kata
yang benar-benar diucapkan narasi (aturan #5)."""

import pytest

import motion_plan as mp

NASKAH = ("Kalian pernah lihat antrean seramai ini? Minggu depan kita donor darah bareng. "
          "Daftar lewat link di bio, terus datang ke aula. Yuk ikut!")


def kata_waktu(teks=NASKAH, per=0.4):
    return [{"word": w, "start": round(i * per, 3), "end": round((i + 1) * per, 3)}
            for i, w in enumerate(teks.split())]


DURASI = len(NASKAH.split()) * 0.4 + 0.5          # ~9,7 dtk


def rencana(**ubah):
    p = {"hook": "Ayo donor darah bareng!", "hook_sorot": "donor", "hook_emoji": "🩸",
         "elemen": [{"jenis": "sorot", "teks": "Minggu depan", "saat_kata": "Minggu"},
                    {"jenis": "langkah", "teks": "Daftar online", "sub": "link di bio", "saat_kata": "Daftar"},
                    {"jenis": "ikon", "teks": "Aula", "emoji": "📍", "saat_kata": "aula."}],
         "cta": "Yuk ikut donor!", "cta_sub": "link di bio", "cta_emoji": "👇"}
    p.update(ubah)
    return p


def bersih(p=None, sumber="ajak anak muda donor darah", jangkar=True):
    return mp.bersihkan(p or rencana(), naskah=NASKAH, sumber_fakta=sumber, pakai_jangkar=jangkar)


def test_rencana_sah_lolos_dan_dijadwalkan_ke_kata_narasi():
    b, cat = bersih()
    assert cat == []
    items, cat2 = mp.jadwal(b, kata_waktu(), DURASI)
    jenis = [i["jenis"] for i in items]
    assert jenis[0] == "kartu_hook" and jenis[-1] == "kartu_cta"
    kw = kata_waktu()
    minggu = next(k["start"] for k in kw if k["word"] == "Minggu")
    sorot = next(i for i in items if i["jenis"] == "sorot")
    assert sorot["mulai"] == pytest.approx(minggu), "waktu dari kata yang diucapkan, bukan tebakan"
    assert items[0]["sorot"] == "donor"


def test_tidak_ada_tumpang_tindih_dan_ada_celah():
    b, _ = bersih()
    items, _ = mp.jadwal(b, kata_waktu(), DURASI)
    for a, c in zip(items, items[1:]):
        assert c["mulai"] >= a["selesai"], f"{a['jenis']} bertumpuk dengan {c['jenis']}"


def test_jenis_di_luar_katalog_dibuang():
    b, cat = bersih(rencana(elemen=[{"jenis": "video_3d", "teks": "wow", "saat_kata": "donor"}]))
    assert b["elemen"] == [] and "katalog" in cat[0]


def test_angka_karangan_dibuang_angka_dari_user_boleh():
    p = rencana(elemen=[{"jenis": "sorot", "teks": "90% anak muda", "saat_kata": "Minggu"},
                        {"jenis": "sorot", "teks": "Tanggal 12", "saat_kata": "Daftar"}])
    b, cat = bersih(p, sumber="donor darah tanggal 12 di aula")
    assert [e["teks"] for e in b["elemen"]] == ["Tanggal 12"]
    assert any("90" in c for c in cat)


def test_angka_di_hook_dan_cta_juga_diperiksa():
    b, cat = bersih(rencana(hook="1000 orang sudah daftar", cta="Diskon 50%"))
    assert b["hook"] is None and b["cta"] is None and len(cat) == 2


def test_kata_jangkar_harus_ada_di_naskah():
    b, cat = bersih(rencana(elemen=[{"jenis": "sorot", "teks": "Gratis", "saat_kata": "gratis"}]))
    assert b["elemen"] == [] and "jangkar" in cat[0]


def test_teks_terlalu_panjang_dibuang_bukan_dipotong():
    b, cat = bersih(rencana(elemen=[{"jenis": "sorot", "teks": "ini teks yang jauh terlalu panjang",
                                     "saat_kata": "Minggu"}]))
    assert b["elemen"] == [] and "panjang" in cat[0]


def test_ikon_tanpa_emoji_sah_dibuang():
    b, _ = bersih(rencana(elemen=[{"jenis": "ikon", "teks": "Aula", "emoji": "gambar", "saat_kata": "aula"}]))
    assert b["elemen"] == []


def test_batas_tingkat_sedang_empat_elemen():
    el = [{"jenis": "sorot", "teks": f"kata {w}", "saat_kata": w}
          for w in ("Minggu", "donor", "Daftar", "link", "datang", "Yuk")]
    b, cat = bersih(rencana(elemen=el))
    assert len(b["elemen"]) == mp.MAKS_ELEMEN and any("batas" in c for c in cat)


def test_elemen_terlalu_rapat_dibuang():
    el = [{"jenis": "sorot", "teks": "Minggu", "saat_kata": "Minggu"},
          {"jenis": "sorot", "teks": "depan", "saat_kata": "depan"}]      # kata berikutnya, 0,4 dtk
    b, _ = bersih(rencana(elemen=el))
    items, cat = mp.jadwal(b, kata_waktu(), DURASI)
    assert [i["teks"] for i in items if i["jenis"] == "sorot"] == ["Minggu"]
    assert any("rapat" in c for c in cat)


def test_elemen_di_bawah_kartu_pembuka_dibuang():
    el = [{"jenis": "sorot", "teks": "Antrean", "saat_kata": "antrean"}]  # detik 1,2 < hook 2,2
    b, _ = bersih(rencana(elemen=el))
    items, cat = mp.jadwal(b, kata_waktu(), DURASI)
    assert all(i["jenis"] != "sorot" for i in items) and cat


def test_jangkar_tidak_terucap_dibuang_dan_dicatat():
    b, _ = bersih()
    tanpa = [w for w in kata_waktu() if w["word"] != "Daftar"]
    items, cat = mp.jadwal(b, tanpa, DURASI)
    assert all(i["jenis"] != "langkah" for i in items)
    assert any("tidak ditemukan" in c for c in cat)


def test_nomor_langkah_dihitung_kode():
    el = [{"jenis": "langkah", "teks": "Daftar", "saat_kata": "Daftar"},
          {"jenis": "langkah", "teks": "Datang", "saat_kata": "datang"}]
    b, _ = bersih(rencana(elemen=el))
    items, _ = mp.jadwal(b, kata_waktu(per=0.6), len(NASKAH.split()) * 0.6 + 0.5)
    langkah = [i for i in items if i["jenis"] == "langkah"]
    assert [(i["nomor"], i["total"]) for i in langkah] == [(1, 2), (2, 2)]


def test_mode_suara_asli_hanya_hook_dan_cta():
    b, cat = bersih(jangkar=False)
    assert b["elemen"] == [] and b["hook"] and b["cta"] and "voice-over AI" in cat[0]


def test_teks_statis_menggantikan_kartu_pembuka():
    b, _ = bersih()
    items, cat = mp.jadwal(b, kata_waktu(), DURASI, ada_teks_statis=True)
    assert items[0]["jenis"] != "kartu_hook" and any("statis" in c for c in cat)


def test_video_pendek_tanpa_kartu_ajakan():
    b, _ = bersih()
    items, cat = mp.jadwal(b, kata_waktu(), 6.0)
    assert all(i["jenis"] != "kartu_cta" for i in items)


def test_rencana_rusak_aman():
    assert mp.bersihkan("teks", naskah=NASKAH, sumber_fakta="", pakai_jangkar=True)[0]["hook"] is None
    assert mp.bersihkan(None, naskah=NASKAH, sumber_fakta="", pakai_jangkar=True)[1] == []


def test_tingkat(monkeypatch):
    from style import StyleError
    assert mp.tingkat("sedang") == "sedang" and mp.tingkat("mati") is None
    monkeypatch.delenv("MOTION_GRAPHIC", raising=False)
    assert mp.tingkat() == "sedang", "bawaan = sedang (keputusan user)"
    with pytest.raises(StyleError):
        mp.tingkat("heboh")
