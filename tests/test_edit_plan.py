"""Seleksi konten: LLM mengusulkan, KODE yang membangun kandidat dan memutuskan.

Yang diuji di sini bukan apakah LLM-nya pintar (itu tidak bisa diuji tanpa
jaringan), melainkan bahwa KODE tetap benar ketika LLM salah: nomor karangan,
duplikat, skor aneh, waktu yang ditulis sendiri, atau gagal total.
"""

import json
import os
import subprocess

import pytest

import auto_render as ar
import edit_plan as ep
from test_duration import _durasi


def _warna(path, detik):
    """Kanal merah rata-rata 30% BAGIAN ATAS frame. Bagian bawah dihindari karena
    di sanalah subtitle (kotak hitam + teks putih) digambar dan ikut mengubah
    rata-ratanya."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{detik:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", "crop=iw:ih*0.3:0:0,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True)
    return out.stdout[0] if out.stdout else None


# ---------- bahan uji ----------

@pytest.fixture(autouse=True)
def _minimum_untuk_kalimat_uji_pendek(monkeypatch):
    """Kalimat uji hanya ~4 detik, jadi batas minimum bawaan (8 dtk) menolak hampir
    semua rencana. Ambang itu sendiri diuji terpisah dengan min_total eksplisit."""
    monkeypatch.setattr(ep, "EDIT_MIN_SECONDS", 1.0)


def _kata(teks, mulai, per_kata=0.4):
    """Daftar kata Whisper-style untuk sebuah kalimat mulai dari `mulai`."""
    out, t = [], mulai
    for w in teks.split():
        out.append({"word": w, "start": round(t, 2), "end": round(t + per_kata - 0.05, 2)})
        t += per_kata
    return out


def _klip(teks, mulai=0.0, **metrik):
    kata = _kata(teks, mulai)
    seg = {"start": kata[0]["start"], "end": kata[-1]["end"], "text": teks, **metrik}
    return {"segments": [seg], "words": kata}


@pytest.fixture
def bahan():
    urutan = ["a.mp4", "b.mp4", "c.mp4"]
    transkrip = {
        "a.mp4": _klip("Berapa banyak calon pembeli yang hilang setiap bulan"),
        "b.mp4": _klip("Berapa banyak calon pembeli yang hilang tiap bulan"),   # take ulang a
        "c.mp4": _klip("Website ini membantu tim Anda mengelola listing", mulai=0.5),
    }
    durasi = {"a.mp4": 6.0, "b.mp4": 6.0, "c.mp4": 6.0}
    return urutan, transkrip, durasi


# ---------- kandidat dibangun KODE ----------

def test_nomor_kandidat_berurutan_mengikuti_urutan_upload(bahan):
    kand = ep.bangun_kandidat(*bahan[:1], bahan[1], bahan[2])
    assert [c["id"] for c in kand] == [1, 2, 3]
    assert [c["klip"] for c in kand] == [1, 2, 3]
    assert [c["file"] for c in kand] == ["a.mp4", "b.mp4", "c.mp4"]


def test_batas_ucapan_diambil_dari_kata_bukan_dari_segmen(bahan):
    urutan, transkrip, durasi = bahan
    # segmen Whisper melebar ke jeda di awalnya; kata pertama baru mulai di 0.5
    transkrip["c.mp4"]["segments"][0]["start"] = 0.0
    kand = ep.bangun_kandidat(urutan, transkrip, durasi)
    assert kand[2]["mulai"] == 0.5


def test_take_ulang_ditandai_kode_bukan_ditebak_llm(bahan):
    kand = ep.bangun_kandidat(*bahan)
    assert kand[1].get("mirip_dengan") == 1, "b adalah take ulang a"
    assert "mirip_dengan" not in kand[0]
    assert "mirip_dengan" not in kand[2], "c isinya lain"


@pytest.mark.parametrize("metrik,ditandai", [
    ({"avg_logprob": -1.4}, True),           # ambang bawaan Whisper: -1.0
    ({"avg_logprob": -0.4}, False),
    ({"no_speech_prob": 0.8}, True),         # ambang bawaan Whisper: 0.6
    ({"compression_ratio": 3.0}, True),      # ambang bawaan Whisper: 2.4
    ({}, False),                              # tanpa metrik -> tidak menuduh apa pun
])
def test_ucapan_tidak_jelas_pakai_ambang_bawaan_whisper(metrik, ditandai):
    tr = {"a.mp4": _klip("kalimat uji yang cukup panjang", **metrik)}
    kand = ep.bangun_kandidat(["a.mp4"], tr, {"a.mp4": 5.0})
    assert bool(kand[0].get("ragu")) is ditandai


def test_segmen_panjang_dipecah_di_jeda_terbesar():
    kata = _kata("satu dua tiga empat lima", 0.0, per_kata=0.4) \
        + _kata("enam tujuh delapan sembilan sepuluh", 14.0, per_kata=0.4)  # jeda 12 dtk; total > 12 dtk
    seg = {"start": 0.0, "end": kata[-1]["end"], "text": "teks utuh"}
    kand = ep.bangun_kandidat(["a.mp4"], {"a.mp4": {"segments": [seg], "words": kata}},
                              {"a.mp4": 18.0})
    assert len(kand) == 2
    assert kand[0]["selesai"] < 3 and kand[1]["mulai"] >= 14.0


def test_segmen_yang_masih_muat_tidak_dipecah():
    """Memecah kalimat yang wajar hanya membuka peluang membuang separuhnya."""
    kata = _kata("satu dua tiga empat lima enam", 0.0, per_kata=0.4)
    seg = {"start": 0.0, "end": kata[-1]["end"], "text": "satu kalimat utuh"}
    kand = ep.bangun_kandidat(["a.mp4"], {"a.mp4": {"segments": [seg], "words": kata}},
                              {"a.mp4": 6.0})
    assert len(kand) == 1


def test_bantalan_dijepit_ke_titik_tengah_jeda_tetangga():
    """Bantalan penuh akan menyertakan ekor kalimat sebelumnya atau kepala
    kalimat berikutnya — ucapan yang mungkin justru dibuang."""
    c = {"mulai": 5.0, "selesai": 8.0, "sebelum": 4.9, "sesudah": 8.1, "durasi_file": 20.0}
    a, b = ep.rentang_kandidat(c)
    assert a >= (4.9 + 5.0) / 2 - 1e-6
    assert b <= (8.0 + 8.1) / 2 + 1e-6


def test_rentang_tidak_keluar_dari_berkas():
    c = {"mulai": 0.0, "selesai": 5.9, "sebelum": 0.0, "sesudah": 6.5, "durasi_file": 6.0}
    a, b = ep.rentang_kandidat(c)
    assert a >= 0.0 and b <= 6.0


def test_prompt_tidak_memuat_nama_berkas_dan_menandai_take_ulang(bahan):
    """LLM hanya boleh melihat NOMOR — nama berkas dan timestamp tidak ada di
    prompt, jadi tidak ada yang bisa ia karang."""
    kand = ep.bangun_kandidat(*bahan)
    prompt = ep.bangun_prompt(kand, max_total=60, min_total=8)
    assert "a.mp4" not in prompt and "b.mp4" not in prompt
    assert "MIRIP #1" in prompt


# ---------- verifikasi usulan LLM ----------

@pytest.fixture
def kand(bahan):
    return ep.bangun_kandidat(*bahan)


def test_nomor_karangan_dan_duplikat_dibuang_dan_dihitung(kand):
    usulan = {"pilih": [{"id": 1, "skor": 9}, {"id": 999, "skor": 10},
                        {"id": 1, "skor": 8}, {"id": "abc"}, "bukan dict", {"id": 3, "skor": 7}]}
    r, alasan = ep.susun_rencana(kand, usulan, max_total=60)
    assert alasan is None
    assert [p["id"] for p in r["picks"]] == [1, 3]
    assert r["nomor_tidak_valid"] == 4


def test_id_berupa_string_berpagar_tetap_terbaca(kand):
    r, _ = ep.susun_rencana(kand, {"pilih": [{"id": "#3", "skor": 5}, {"id": " 1 ", "skor": 5}]},
                            max_total=60)
    assert [p["id"] for p in r["picks"]] == [3, 1]


def test_waktu_yang_ditulis_llm_diabaikan(kand):
    """Rentang dihitung ulang dari kandidat. Kalau LLM menulis waktunya sendiri,
    itu tidak boleh sampai ke render."""
    usulan = {"pilih": [{"id": 1, "skor": 9, "mulai": 100, "selesai": 200, "range": [50, 60]}]}
    r, _ = ep.susun_rencana(kand, usulan, max_total=60, min_total=1)
    a, b = r["picks"][0]["range"]
    assert (a, b) == ep.rentang_kandidat(kand[0])


def test_tanpa_nomor_valid_rencana_ditolak(kand):
    r, alasan = ep.susun_rencana(kand, {"pilih": [{"id": 77}]}, max_total=60)
    assert r is None and "tidak ada nomor valid" in alasan


@pytest.mark.parametrize("usulan", [None, {}, {"pilih": "semua"}, {"pilih": None}, {"salah": 1}])
def test_usulan_berbentuk_aneh_ditolak_bukan_meledak(kand, usulan):
    r, alasan = ep.susun_rencana(kand, usulan, max_total=60)
    assert r is None and alasan


def test_kelebihan_durasi_dipangkas_dari_skor_terendah(kand):
    usulan = {"pilih": [{"id": 1, "skor": 9}, {"id": 2, "skor": 2}, {"id": 3, "skor": 6}]}
    r, _ = ep.susun_rencana(kand, usulan, max_total=9, min_total=1)
    ids = [p["id"] for p in r["picks"]]
    assert 2 not in ids, "skor terendah dibuang lebih dulu"
    assert 1 in ids, "skor tertinggi dipertahankan"
    assert any(d["sebab"] == "anggaran durasi" and d["id"] == 2 for d in r["dibuang"])
    assert r["detik_dipilih"] <= 9


def test_pangkas_seri_membuang_yang_paling_akhir(kand):
    """Seri skor: pertahankan yang lebih awal (hook)."""
    usulan = {"pilih": [{"id": 1, "skor": 5}, {"id": 2, "skor": 5}, {"id": 3, "skor": 5}]}
    r, _ = ep.susun_rencana(kand, usulan, max_total=9, min_total=1)
    assert 1 in [p["id"] for p in r["picks"]]
    assert 3 not in [p["id"] for p in r["picks"]]


def test_hasil_terlalu_pendek_ditolak(kand):
    r, alasan = ep.susun_rencana(kand, {"pilih": [{"id": 1, "skor": 9}]}, max_total=60, min_total=30)
    assert r is None and "terlalu pendek" in alasan


def test_fade_hanya_antar_klip_dan_dibatasi_sepertiga(kand):
    usulan = {"pilih": [{"id": 1, "skor": 9, "transisi": "fade"},   # pertama: tidak ada sambungan
                        {"id": 2, "skor": 9, "transisi": "fade"},
                        {"id": 3, "skor": 9, "transisi": "fade"}]}
    r, _ = ep.susun_rencana(kand, usulan, max_total=60, min_total=1)
    fade = [p["fade_masuk"] for p in r["picks"]]
    assert fade[0] is False, "potongan pertama tidak punya sambungan masuk"
    assert sum(fade) <= 1, "3 potongan -> maksimal 1 fade"


def test_potongan_dari_klip_yang_sama_tidak_pernah_fade():
    """Bagian yang dibuang di dalam satu klip = hard cut, bukan fade."""
    kata = _kata("satu dua tiga empat", 0.0, 1.0) + _kata("lima enam tujuh delapan", 9.0, 1.0)
    tr = {"a.mp4": {"segments": [{"start": 0.0, "end": kata[-1]["end"], "text": "dua kalimat"}],
                    "words": kata}}
    kand = ep.bangun_kandidat(["a.mp4"], tr, {"a.mp4": 14.0})
    assert len(kand) == 2
    r, _ = ep.susun_rencana(
        kand, {"pilih": [{"id": 1, "skor": 8, "transisi": "fade"},
                         {"id": 2, "skor": 8, "transisi": "fade"}]}, max_total=60, min_total=1)
    assert [p["fade_masuk"] for p in r["picks"]] == [False, False]


# ---------- buat_rencana: gagal-aman ----------

def _llm_baik(ids):
    return lambda prompt: {"pilih": [{"id": i, "skor": 8} for i in ids]}


def test_transkrip_tidak_lengkap_membatalkan_seleksi(bahan):
    """Kuota habis / timeout berarti sebagian ucapan tidak pernah dilihat LLM.
    Memotong berdasarkan itu membuang konten tanpa ada yang memutuskannya."""
    urutan, transkrip, durasi = bahan
    urutan = urutan + ["d.mp4"]
    r, st = ep.buat_rencana(urutan, transkrip, {"d.mp4": "kuota_habis"}, durasi,
                            panggil_llm=_llm_baik([1]))
    assert r is None and st["status"] == "dilewati"
    assert "tidak lengkap" in st["alasan"]


def test_bahan_yang_tidak_dijelaskan_dianggap_tidak_lengkap(bahan):
    """Tidak ada di transkrip DAN tidak ada di daftar gagal = tidak diketahui,
    bukan aman."""
    urutan, transkrip, durasi = bahan
    r, st = ep.buat_rencana(urutan + ["hantu.mp4"], transkrip, {}, durasi,
                            panggil_llm=_llm_baik([1]))
    assert r is None and "tidak lengkap" in st["alasan"]


def test_hanya_tanpa_ucapan_yang_pasti_boleh_dilewati(bahan):
    urutan, transkrip, durasi = bahan
    r, st = ep.buat_rencana(urutan + ["b_roll.mp4"], transkrip, {"b_roll.mp4": "tanpa_ucapan"},
                            durasi, panggil_llm=_llm_baik([1, 3]))
    assert st["status"] == "applied"
    assert r["tanpa_ucapan"] == ["b_roll.mp4"]


def test_terlalu_banyak_bahan_tanpa_ucapan_bukan_talking_head(bahan):
    urutan, transkrip, durasi = bahan
    lain = {"x.mp4": "tanpa_ucapan", "y.mp4": "tanpa_audio"}
    r, st = ep.buat_rencana(urutan + list(lain), transkrip, lain, durasi,
                            panggil_llm=_llm_baik([1]))
    assert r is None and "talking-head" in st["alasan"]


def test_llm_gagal_tidak_menggagalkan_apa_pun(bahan):
    def meledak(prompt):
        raise RuntimeError("insufficient_quota")
    r, st = ep.buat_rencana(*bahan[:1], bahan[1], {}, bahan[2], panggil_llm=meledak)
    assert r is None and st["status"] == "dilewati" and "LLM gagal" in st["alasan"]


def test_usulan_tidak_valid_ditolak_dengan_alasan(bahan):
    r, st = ep.buat_rencana(bahan[0], bahan[1], {}, bahan[2],
                            panggil_llm=lambda p: {"pilih": [{"id": 404}]})
    assert r is None and "ditolak" in st["alasan"]


def test_seleksi_bisa_dimatikan(bahan, monkeypatch):
    monkeypatch.setattr(ep, "EDIT_SELECTION", False)
    r, st = ep.buat_rencana(bahan[0], bahan[1], {}, bahan[2], panggil_llm=_llm_baik([1]))
    assert r is None and st["status"] == "dimatikan"


def test_permintaan_durasi_user_menjadi_batas_atas():
    assert ep.batas_durasi(None) == ep.EDIT_DEFAULT_MAX
    assert ep.batas_durasi(30) == pytest.approx(30 * (1 + ep.EDIT_TARGET_TOLERANCE))


def test_ringkasan_dihitung_dari_data(bahan):
    r, _ = ep.buat_rencana(*bahan[:1], bahan[1], {}, bahan[2],
                           panggil_llm=lambda p: {"pilih": [{"id": 1, "skor": 9}, {"id": 3, "skor": 7}],
                                                  "dibuang": [{"id": 2, "alasan": "take ulang"}]})
    teks = ep.ringkas(r)
    assert "dipilih 2 dari 3" in teks and "take ulang" in teks
    assert ep.ringkas(None) == ""


# ---------- renderer: rencana dari plan ----------

def _pick(file, a, b, fade=False, teks="x"):
    return {"file": file, "range": [a, b], "fade_masuk": fade, "teks": teks}


@pytest.fixture
def aset_palsu(tmp_path, monkeypatch):
    """Berkas kosong; media_duration di-stub supaya tidak perlu ffprobe."""
    paths = []
    for n in ("a.mp4", "b.mp4", "c.mp4"):
        p = tmp_path / n
        p.write_bytes(b"x")
        paths.append(str(p))
    monkeypatch.setattr(ar, "media_duration", lambda p: 10.0)
    monkeypatch.setattr(ar, "TRIM_SILENCE", False)
    return paths


def test_pick_dari_klip_yang_sama_jadi_satu_item_dengan_dua_rentang(aset_palsu):
    plan = {"picks": [_pick("a.mp4", 0.0, 2.0), _pick("a.mp4", 6.0, 8.0)]}
    r = ar.rencana_dari_plan(plan, aset_palsu)
    assert len(r) == 1 and r[0]["ranges"] == [(0.0, 2.0), (6.0, 8.0)]
    assert r[0]["durasi"] == pytest.approx(4.0)


def test_rentang_yang_nyaris_bersambung_digabung(aset_palsu):
    """Memotong lalu menyambung kembali dengan jarak 0,4 dtk cuma menghasilkan loncatan."""
    plan = {"picks": [_pick("a.mp4", 0.0, 2.0), _pick("a.mp4", 2.4, 4.0)]}
    r = ar.rencana_dari_plan(plan, aset_palsu)
    assert r[0]["ranges"] == [(0.0, 4.0)]


def test_klip_berbeda_jadi_item_terpisah_dengan_urutan_dipertahankan(aset_palsu):
    plan = {"picks": [_pick("c.mp4", 0, 2), _pick("a.mp4", 0, 3), _pick("c.mp4", 5, 7)]}
    r = ar.rencana_dari_plan(plan, aset_palsu)
    assert [os.path.basename(i["path"]) for i in r] == ["c.mp4", "a.mp4", "c.mp4"]


def test_nama_berkas_yang_tidak_ada_di_bahan_dilewati(aset_palsu):
    """Diverifikasi ULANG di renderer, meski brief sudah memverifikasi."""
    plan = {"picks": [_pick("hantu.mp4", 0, 3), _pick("a.mp4", 0, 3)]}
    r = ar.rencana_dari_plan(plan, aset_palsu)
    assert [os.path.basename(i["path"]) for i in r] == ["a.mp4"]


def test_rentang_dijepit_ke_durasi_nyata(aset_palsu):
    r = ar.rencana_dari_plan({"picks": [_pick("a.mp4", 8.0, 99.0)]}, aset_palsu)
    assert r[0]["ranges"] == [(8.0, 10.0)]


def test_semua_pick_tidak_valid_menghasilkan_rencana_kosong(aset_palsu):
    assert ar.rencana_dari_plan({"picks": [_pick("hantu.mp4", 0, 3)]}, aset_palsu) == []


def test_sambungan_plan_hard_cut_di_dalam_klip_fade_hanya_kalau_ditandai(aset_palsu):
    plan = {"picks": [_pick("a.mp4", 0, 2), _pick("a.mp4", 6, 8),
                      _pick("b.mp4", 0, 2, fade=True), _pick("c.mp4", 0, 2, fade=False)]}
    r = ar.rencana_dari_plan(plan, aset_palsu)
    # potongan: a[0-2] a[6-8] | b | c   -> 3 sambungan
    assert ar.sambungan_audio_asli(r) == [False, True, False]


# ---------- renderer: subtitle hanya dari yang dipertahankan ----------

def test_subtitle_tidak_membocorkan_kalimat_yang_dibuang():
    """Bug yang PASTI muncul begitu seleksi ada: kelompok kata dibentuk dari
    SELURUH ucapan klip lalu waktunya dipetakan ke potongan terpilih, sehingga
    teks kalimat yang dibuang ikut tampil di layar."""
    kata = _kata("alfa bravo charlie", 0.2) + _kata("delta echo foxtrot", 3.0)
    data = {"transcript_words": {"a.mp4": kata}, "transcript_segments": {}}
    # hanya kalimat kedua yang dipertahankan
    rencana = [{"path": "/x/a.mp4", "ranges": [(2.8, 4.4)], "durasi": 1.6, "asli": 6.0}]

    scenes = ar.subtitle_scenes(data, rencana, 1080, 1920)
    teks = " ".join(s["text"] for s in scenes)

    assert "delta" in teks and "foxtrot" in teks
    assert "alfa" not in teks and "bravo" not in teks and "charlie" not in teks


def test_subtitle_mode_segmen_juga_disaring():
    """Jalur segmen TIDAK memakai penyaring _di_dalam: segmen di area yang dibuang
    runtuh jadi durasi nol lewat map_time() lalu dilewati. (Penyaring titik-tengah
    pernah dicoba di sini dan salah membuang segmen panjang yang hanya sebagian
    masuk rentang -- lihat test_audio_mode.test_subtitle_dijepit_ke_panjang_klip.)
    Tes ini menjaga PERILAKU akhirnya; penyaring jalur kata dijaga tes di atas."""
    data = {"transcript_words": {},
            "transcript_segments": {"a.mp4": [
                {"start": 0.2, "end": 1.4, "text": "kalimat yang dibuang"},
                {"start": 3.0, "end": 4.2, "text": "kalimat yang dipakai"}]}}
    rencana = [{"path": "/x/a.mp4", "ranges": [(2.8, 4.4)], "durasi": 1.6, "asli": 6.0}]
    teks = " ".join(s["text"] for s in ar.subtitle_scenes(data, rencana, 1080, 1920))
    assert "dipakai" in teks and "dibuang" not in teks


def test_subtitle_tanpa_seleksi_tetap_utuh():
    """Perilaku lama: tanpa pemotongan, semua kata tampil."""
    kata = _kata("alfa bravo charlie", 0.2)
    data = {"transcript_words": {"a.mp4": kata}, "transcript_segments": {}}
    rencana = [{"path": "/x/a.mp4", "ranges": [(0.0, 6.0)], "durasi": 6.0, "asli": 6.0}]
    teks = " ".join(s["text"] for s in ar.subtitle_scenes(data, rencana, 1080, 1920))
    assert all(k in teks for k in ("alfa", "bravo", "charlie"))


# ---------- render nyata ----------

def _klip_nyata(path, merah, detik=3):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"color=c=0x{merah:02x}8040:size=240x426:rate=24:duration={detik}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={detik}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-shortest", str(path)],
        check=True, capture_output=True)
    return str(path)


@pytest.fixture
def render_nyata(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "TARGET_W", 240)
    monkeypatch.setattr(ar, "TARGET_H", 426)
    monkeypatch.setattr(ar, "THUMBNAIL_ENABLED", False)
    monkeypatch.setattr(ar, "TRIM_SILENCE", False)
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC", "off")
    aset = {f"{n}.mp4": _klip_nyata(tmp_path / f"{n}.mp4", m)
            for n, m in (("a", 0x40), ("b", 0x90), ("c", 0xE0))}

    def jalankan(plan):
        data = {"judul": "uji seleksi", "audio_mode": "original", "full_voice_over": "x",
                "media_assets": list(aset.values()), "scenes": [],
                "transcript_words": {
                    "a.mp4": _kata("alfa bravo", 0.3), "b.mp4": _kata("bekas dibuang", 0.3),
                    "c.mp4": _kata("charlie delta", 0.3)},
                "transcript_segments": {}, "edit_plan": plan}
        skrip = tmp_path / "s.json"
        skrip.write_text(json.dumps(data), encoding="utf-8")
        keluaran = tmp_path / "hasil.mp4"
        ar.render_from_agent_script(str(skrip), "", str(keluaran))
        return keluaran

    return jalankan, aset


def test_render_hanya_memutar_yang_dipilih_dengan_urutan_dari_plan(render_nyata):
    """Bukti akhirnya adalah VIDEO: klip c diputar dulu lalu a, klip b tidak
    muncul sama sekali — diukur dari warna tiap slot, bukan dari daftar argumen."""
    jalankan, _ = render_nyata
    plan = {"status": "applied", "dipilih": 2, "kandidat": 3,
            "picks": [_pick("c.mp4", 0.0, 2.0), _pick("a.mp4", 1.0, 2.5)]}

    hasil = jalankan(plan)

    assert _durasi(hasil) == pytest.approx(3.5, abs=0.25)
    assert abs(_warna(hasil, 1.0) - 0xE0) <= 10, "slot pertama harus klip c"
    assert abs(_warna(hasil, 2.75) - 0x40) <= 10, "slot kedua harus klip a"
    for t in (0.5, 1.5, 2.5, 3.0):
        assert abs(_warna(hasil, t) - 0x90) > 20, f"klip b (dibuang) muncul di detik {t}"


def test_tanpa_plan_semua_klip_dipakai_seperti_dulu(render_nyata):
    jalankan, _ = render_nyata
    hasil = jalankan(None)
    assert _durasi(hasil) == pytest.approx(9.0, abs=0.4)


def test_plan_yang_semua_pick_nya_tidak_valid_jatuh_ke_semua_klip(render_nyata):
    """Plan rusak tidak boleh menghasilkan video kosong."""
    jalankan, _ = render_nyata
    plan = {"status": "applied", "picks": [_pick("hantu.mp4", 0, 2)]}
    assert _durasi(jalankan(plan)) == pytest.approx(9.0, abs=0.4)


def test_plan_berstatus_dilewati_diabaikan(render_nyata):
    jalankan, _ = render_nyata
    plan = {"status": "dilewati", "picks": [_pick("a.mp4", 0, 1)]}
    assert _durasi(jalankan(plan)) == pytest.approx(9.0, abs=0.4)


# ---------- tahap brief: rencana tersambung ke brief ----------

@pytest.fixture
def brief_siap(monkeypatch, tmp_path):
    """Jalankan agent1_2_brief.run() penuh tanpa jaringan/file asli.

    Semua ketergantungan luar diganti; yang diuji adalah SAMBUNGANNYA: rencana
    seleksi dibuat dari transkrip, disimpan di brief, dan dilaporkan.
    """
    import agent1_2_brief as brief

    nama = ["a.mp4", "b.mp4", "c.mp4"]
    paths = [str(tmp_path / n) for n in nama]
    disimpan, panggilan = {}, []

    monkeypatch.setattr(brief, "ensure_dirs", lambda: None)
    monkeypatch.setattr(brief, "resolve_chat_id", lambda: "111")
    monkeypatch.setattr(brief, "select_assets", lambda: nama)
    monkeypatch.setattr(brief, "resolve_assets", lambda n: paths)
    monkeypatch.setattr(brief, "notify", lambda *a, **k: True)
    monkeypatch.setattr(brief, "OPENAI_API_KEY", "kunci-palsu")
    monkeypatch.setattr(brief, "build_image_parts", lambda p: [])
    monkeypatch.setattr(brief, "read_json", lambda *a, **k: {})
    monkeypatch.setattr(brief, "write_json", lambda path, data: disimpan.__setitem__(path, data))
    monkeypatch.setattr(ep, "EDIT_MIN_SECONDS", 1.0)

    bahan_ = {n: {**_klip(t), "text": t, "duration": 6.0, "language": "indonesian"}
              for n, t in (("a.mp4", "Berapa banyak calon pembeli yang hilang setiap bulan"),
                           ("b.mp4", "Berapa banyak calon pembeli yang hilang tiap bulan"),
                           ("c.mp4", "Website ini membantu tim Anda mengelola listing"))}
    gagal = {}
    monkeypatch.setattr(brief, "transcribe_assets_report",
                        lambda p, konteks="": (bahan_, gagal))

    def fake_chat(messages, **kw):
        panggilan.append(kw.get("label"))
        if kw.get("label") == "seleksi konten":
            return {"pilih": [{"id": 1, "skor": 9}, {"id": 3, "skor": 7}],
                    "dibuang": [{"id": 2, "alasan": "take ulang"}]}
        return {"trend_report": {"content_angle": "sudut uji", "trend_index": None},
                "creative_brief": {"judul": "Judul Uji", "deskripsi": "d", "full_voice_over": "naskah",
                                   "scenes": [], "hashtags": []}}

    monkeypatch.setattr(brief, "chat_json", fake_chat)
    import common
    monkeypatch.setattr(common, "chat_json", fake_chat)   # jalur lazy di edit_plan

    def jalankan():
        brief.run()
        return disimpan[brief.BRIEF_PATH]

    return jalankan, panggilan, bahan_, gagal


def test_brief_menyimpan_rencana_dan_ringkasannya(brief_siap):
    jalankan, panggilan, _, _ = brief_siap
    brief_json = jalankan()

    assert brief_json["edit_plan"]["status"] == "applied"
    assert [p["file"] for p in brief_json["edit_plan"]["picks"]] == ["a.mp4", "c.mp4"]
    assert brief_json["edit_status"]["status"] == "applied"
    assert "dipilih 2 dari 3" in brief_json["edit_summary"]
    assert "seleksi konten" in panggilan


def test_brief_mode_full_tidak_memanggil_llm_seleksi(brief_siap, monkeypatch):
    jalankan, panggilan, _, _ = brief_siap
    monkeypatch.setenv("CONTENT_FACTORY_EDIT", "full")
    brief_json = jalankan()

    assert brief_json["edit_plan"] is None
    assert "apa adanya" in brief_json["edit_status"]["alasan"]
    assert "seleksi konten" not in panggilan, "user minta semua bahan: tidak boleh ada biaya LLM tambahan"


def test_brief_transkrip_gagal_membatalkan_seleksi_dengan_alasan(brief_siap):
    """Jalur nyata dari insiden 19 Sep: saldo API habis -> sebagian klip tanpa
    transkrip. Seleksi HARUS dibatalkan, bukan memotong dari data separuh."""
    jalankan, panggilan, bahan_, gagal = brief_siap
    bahan_.pop("c.mp4")
    gagal["c.mp4"] = "kuota_habis"

    brief_json = jalankan()

    assert brief_json["edit_plan"] is None
    assert "tidak lengkap" in brief_json["edit_status"]["alasan"]
    assert "seleksi konten" not in panggilan
    assert brief_json["transcript_coverage"]["alasan"] == {"c.mp4": "kuota_habis"}


def test_brief_mode_ai_tidak_menyeleksi(brief_siap, monkeypatch):
    jalankan, panggilan, _, _ = brief_siap
    monkeypatch.setenv("CONTENT_FACTORY_AUDIO_MODE", "ai")
    brief_json = jalankan()
    assert brief_json["edit_plan"] is None
    assert "seleksi konten" not in panggilan


def test_kata_pertama_klip_tidak_hilang_meski_waktu_whisper_mulai_di_nol():
    """Regresi dari klip nyata: Whisper mencatat kata pertama 0,00-0,96 padahal
    suaranya baru mulai ~0,66, dan rentang yang dipertahankan dimulai 0,54.
    Penyaring titik-tengah membuang 'Berapa' dari subtitle padahal audionya ada
    -- kata pertama hilang di hampir setiap klip."""
    kata = [{"word": "Berapa", "start": 0.0, "end": 0.96},
            {"word": "banyak", "start": 0.96, "end": 1.4},
            {"word": "calon", "start": 1.4, "end": 1.9}]
    data = {"transcript_words": {"a.mp4": kata}, "transcript_segments": {}}
    rencana = [{"path": "/x/a.mp4", "ranges": [(0.54, 4.98)], "durasi": 4.44, "asli": 5.6}]

    teks = " ".join(s["text"] for s in ar.subtitle_scenes(data, rencana, 1080, 1920))

    assert "Berapa" in teks, "kata pertama klip hilang dari subtitle"


def test_kata_yang_sangat_pendek_tidak_dibuang_karena_durasinya():
    assert ar._di_dalam(1.00, 1.06, [(0.5, 3.0)]) is True


@pytest.mark.parametrize("t0,t1,harap", [
    (0.0, 0.96, True),     # menjorok 0,42 dtk ke dalam rentang
    (0.0, 0.50, False),    # selesai sebelum rentang mulai
    (0.0, 0.58, False),    # menjorok hanya 0,04 dtk: sisa ekor kalimat sebelumnya
    (5.0, 5.9, False),     # mulai setelah rentang berakhir
])
def test_batas_tumpang_tindih_kata(t0, t1, harap):
    assert ar._di_dalam(t0, t1, [(0.54, 4.98)]) is harap
