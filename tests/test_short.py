"""Beberapa short dari video panjang: SATU panggilan LLM membagi kandidat ucapan; kode menjamin
potongan tidak dipakai dua short dan tiap short dalam batas durasi; render dari satu draf."""

import json
import os

import pytest

import draf_naskah as dn
import edit_plan as ep
import hermes_render as hr
from test_draft import CHAT, buat_draf, env, jalan

__all__ = ["env"]


def _bahan(n=12, detik=6.0):
    """n klip @6 dtk, tiap klip satu kalimat ucapan (kandidat)."""
    nama = [f"k{i}.mp4" for i in range(n)]
    rinci = {x: {"text": f"kalimat nomor {i} tentang topik {i // 4}",
                 "segments": [{"start": 0.2, "end": detik - 0.2, "text": f"kalimat nomor {i} tentang topik {i // 4}"}],
                 "words": [{"word": w, "start": 0.2 + j * 0.8, "end": 0.9 + j * 0.8}
                           for j, w in enumerate(f"kalimat nomor {i} tentang topik {i // 4}".split())],
                 "duration": detik} for i, x in enumerate(nama)}
    return nama, rinci


def _llm(shorts):
    return lambda prompt: {"shorts": shorts}


def _short(ids, judul="S"):
    return {"judul": judul, "deskripsi": "d", "hashtags": ["#a"], "pilih": [{"id": i, "skor": 8} for i in ids],
            "motion_plan": {"hook": "Hook"}, "broll": []}


def test_short_dibagi_dan_divalidasi(monkeypatch):
    nama, rinci = _bahan()
    k = ep.bangun_kandidat(nama, rinci, {n: 6.0 for n in nama})
    ids = [c["id"] for c in k]
    shorts, st = ep.buat_rencana_banyak(nama, rinci, {}, {n: 6.0 for n in nama}, jumlah=2,
                                        panggil_llm=_llm([_short(ids[:4], "A"), _short(ids[4:8], "B")]))
    assert st["status"] == "applied" and [s["judul"] for s in shorts] == ["A", "B"]
    a = {p["id"] for p in shorts[0]["edit_plan"]["picks"]}
    b = {p["id"] for p in shorts[1]["edit_plan"]["picks"]}
    assert a and b and not (a & b)
    for s in shorts:
        assert ep.SHORT_MIN_DETIK <= s["edit_plan"]["detik_dipilih"] <= ep.SHORT_MAKS_DETIK


def test_potongan_bentrok_dibuang_dari_short_belakangan():
    nama, rinci = _bahan()
    ids = [c["id"] for c in ep.bangun_kandidat(nama, rinci, {n: 6.0 for n in nama})]
    shorts, st = ep.buat_rencana_banyak(nama, rinci, {}, {n: 6.0 for n in nama}, jumlah=2,
                                        panggil_llm=_llm([_short(ids[:4]), _short(ids[2:7])]))
    a = {p["id"] for p in shorts[0]["edit_plan"]["picks"]}
    b = {p["id"] for p in shorts[1]["edit_plan"]["picks"]}
    assert not (a & b) and any("sudah dipakai" in c for c in st["catatan"])


def test_short_terlalu_pendek_dibuang_dan_dicatat():
    nama, rinci = _bahan()
    ids = [c["id"] for c in ep.bangun_kandidat(nama, rinci, {n: 6.0 for n in nama})]
    shorts, st = ep.buat_rencana_banyak(nama, rinci, {}, {n: 6.0 for n in nama}, jumlah=2,
                                        panggil_llm=_llm([_short(ids[:4]), _short(ids[4:5])]))
    assert len(shorts) == 1 and any("terlalu pendek" in c for c in st["catatan"])


def test_ucapan_kurang_untuk_jumlah_short_dilewati():
    nama, rinci = _bahan(n=3)
    shorts, st = ep.buat_rencana_banyak(nama, rinci, {}, {n: 6.0 for n in nama}, jumlah=3,
                                        panggil_llm=lambda p: pytest.fail("LLM tidak boleh dipanggil"))
    assert shorts == [] and "kurang" in st["alasan"]


def test_pilih_short():
    assert dn.pilih_short("semua", 3) == ["A", "B", "C"]
    assert dn.pilih_short("c, a", 3) == ["A", "C"]
    with pytest.raises(dn.DrafError):
        dn.pilih_short("D", 3)


# ------------------------------------------------------------------ draf & render

def _jadikan_draf_short(draft_id):
    p = os.path.join(dn.DRAF_DIR, draft_id + ".json")
    d = json.load(open(p))
    d["brief"]["jumlah_short"] = 3
    d["brief"]["audio_mode"] = "original"
    d["varian"] = [{**d["varian"][0], "gaya": f"Short {i}", "judul": f"Short ke-{i}",
                    "full_voice_over": f"ucapan short {i}",
                    "edit_plan": {"status": "applied", "picks": [], "detik_dipilih": 20 + i, "dipilih": 3}}
                   for i in (1, 2, 3)]
    json.dump(d, open(p, "w"))
    return d


def test_pesan_draf_short(env, capsys):
    out = buat_draf(env, capsys)
    d = _jadikan_draf_short(out["draft_id"])
    pesan = dn.susun_pesan(d)
    assert "A. Short 1" in pesan and "C. Short 3" in pesan and "±23 dtk" in pesan
    assert 'Balas "semua"' in pesan and "ucapan short 2" in pesan


def test_render_semua_short_dari_satu_draf(env, capsys):
    out = buat_draf(env, capsys)
    _jadikan_draf_short(out["draft_id"])
    kode, hasil = jalan(capsys, "--chat-id", CHAT, "--draft-id", out["draft_id"], "--short", "semua")
    assert kode == 0 and [s["short"] for s in hasil["shorts"]] == ["A", "B", "C"]
    dirender = [c["brief"]["judul"] for c in env["calls"][1:]]
    assert dirender == ["Short ke-1", "Short ke-2", "Short ke-3"]
    assert len({s["run_id"] for s in hasil["shorts"]}) == 3, "tiap short punya run_id sendiri"
    kode, lagi = jalan(capsys, "--chat-id", CHAT, "--draft-id", out["draft_id"], "--short", "A")
    assert lagi["kode"] == "draf_sudah_dipakai", "draf diklaim sekali untuk semua short"


def test_satu_short_gagal_yang_lain_tetap_jadi(env, capsys, monkeypatch):
    out = buat_draf(env, capsys)
    _jadikan_draf_short(out["draft_id"])
    asli = hr.run_core_stages_locked

    def kadang_gagal(run_id, **kw):
        kw["sebelum_tahap"]()
        import json as _j
        if "Short ke-2" in _j.load(open(hr.BRIEF_PATH))["judul"]:
            return "FAILED", (1, ("ContentMakers", "ffmpeg meledak"))
        kw["sebelum_tahap"] = lambda: None
        return asli(run_id, **kw)

    monkeypatch.setattr(hr, "run_core_stages_locked", kadang_gagal)
    kode, hasil = jalan(capsys, "--chat-id", CHAT, "--draft-id", out["draft_id"], "--short", "A,B,C")
    assert kode == 0 and [s["short"] for s in hasil["shorts"]] == ["A", "C"]
    assert hasil["gagal"][0]["short"] == "B"


def test_short_tanpa_draf_dan_dengan_voiceover_ditolak(env, capsys):
    args = ["--chat-id", CHAT, "--no-require-inspect", "--music", "off", "--jumlah-short", "2",
            "--media-path", env["bahan"][0]]
    assert jalan(capsys, *args)[1]["kode"] == "short_butuh_draf"
    assert jalan(capsys, *args, "--draft", "--audio-mode", "ai")[1]["kode"] == "short_butuh_suara_asli"


@pytest.fixture
def brief_short(monkeypatch, tmp_path):
    import agent1_2_brief as ab
    nama, rinci = _bahan()
    paths = [str(tmp_path / n) for n in nama]
    disimpan, label = {}, []
    for k, v in {"ensure_dirs": lambda: None, "resolve_chat_id": lambda: "1", "select_assets": lambda: nama,
                 "resolve_assets": lambda n: paths, "notify": lambda *a, **k: True,
                 "OPENAI_API_KEY": "k", "build_image_parts": lambda p, **k: [],
                 "read_json": lambda *a, **k: {}, "media_duration": lambda p: 6.0,
                 "write_json": lambda path, data: disimpan.__setitem__(path, data),
                 "transcribe_assets_report": lambda p, konteks="": (rinci, {})}.items():
        monkeypatch.setattr(ab, k, v)
    monkeypatch.setenv("CONTENT_FACTORY_AUDIO_MODE", "original")
    monkeypatch.setenv("CONTENT_FACTORY_DRAFT", "1")
    monkeypatch.setenv("CONTENT_FACTORY_SHORT", "2")
    ids = [c["id"] for c in ep.bangun_kandidat(nama, rinci, {n: 6.0 for n in nama})]

    def llm(messages, **kw):
        label.append(kw.get("label"))
        if kw.get("label") == "seleksi beberapa short":
            return {"shorts": [_short(ids[:4], "Pertama"), _short(ids[4:8], "Kedua")]}
        return {"trend_report": {"content_angle": "x", "trend_index": None},
                "creative_brief": {"judul": "J", "deskripsi": "d", "full_voice_over": "draf caption",
                                   "scenes": [], "hashtags": []}}

    monkeypatch.setattr(ab, "chat_json", llm)
    import common
    monkeypatch.setattr(common, "chat_json", llm)
    return (lambda: (ab.run(), disimpan[ab.BRIEF_PATH])[1]), label


def test_brief_mode_short_menghasilkan_varian_short(brief_short):
    jalankan, label = brief_short
    b = jalankan()
    assert b["jumlah_short"] == 2 and [v["gaya"] for v in b["varian"]] == ["Short 1", "Short 2"]
    assert [v["judul"] for v in b["varian"]] == ["Pertama", "Kedua"]
    assert all(v["edit_plan"]["status"] == "applied" for v in b["varian"])
    assert "kalimat nomor 0" in b["varian"][0]["full_voice_over"], "ucapan MILIK short itu"
    assert "seleksi beberapa short" in label and "seleksi konten" not in label


def test_brief_gagal_membagi_jatuh_ke_satu_video(brief_short, monkeypatch):
    import agent1_2_brief as ab
    jalankan, label = brief_short
    monkeypatch.setattr(ab, "buat_rencana_banyak", lambda *a, **k: ([], {"status": "dilewati", "alasan": "x"}))
    b = jalankan()
    assert "jumlah_short" not in b and len(b["varian"]) == 1 and b["edit_status"]["short_gagal"] == "x"


def test_inspect_menawarkan_beberapa_short_bila_ucapan_panjang():
    import inspect_media as im
    ringk = {"n_video": 6, "n_gambar": 0, "n_berucap": 6, "n_suasana": 0, "n_tanpa_suara": 0,
             "n_horizontal": 0, "n_musik": 0, "total_detik": 150, "detik_ucapan": 140}
    q = im.susun_pertanyaan(ringk, im.dari_konteks("edit ya"))
    tawaran = next(x for x in q if x["kode"] == "short")
    assert tawaran["param"] == {"B": {"jumlahShort": 2}, "C": {"jumlahShort": 3}}
    pendek = {**ringk, "detik_ucapan": 50}
    assert "short" not in [x["kode"] for x in im.susun_pertanyaan(pendek, im.dari_konteks("edit ya"))]
    assert "short" not in [x["kode"] for x in im.susun_pertanyaan(ringk, im.dari_konteks("jadi 2 short ya"))]


def test_pesan_draf_jujur_bila_short_kurang_dari_permintaan(env, capsys):
    out = buat_draf(env, capsys)
    d = _jadikan_draf_short(out["draft_id"])
    d["varian"] = d["varian"][:1]
    d["brief"]["edit_status"] = {"diminta": 2, "jadi": 1,
                                 "catatan": ["short 2 dibuang: hasil terlalu pendek (13.1 dtk < 15 dtk)"]}
    pesan = dn.susun_pesan(d)
    assert "hanya 1 dari 2 short" in pesan and "13.1 dtk" in pesan and 'Balas "A"' in pesan
