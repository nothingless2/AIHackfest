"""Revisi cepat (scripts/revisi.py + hermes_render --revisi): render ulang video yang sudah jadi
tanpa LLM; hanya yang diminta berubah (B-roll & lagu lain dikunci), gagal-tertutup untuk chat lain,
kedaluwarsa, bahan hilang."""

import json
import os

import pytest

import broll as b
import hermes_render as hr
import music
import revisi
from orchestrator import RENDER_STAGES
from test_draft import CHAT, buat_draf, env, jalan  # noqa: F401  (fixture)

STATUS_PALSU = {"status": "SUCCESS", "music": "lagu_a.mp3", "naskah_koreksi": None,
                "broll": {"cara": "sisip", "dipakai": [
                    {"id": 11, "kredit": "x", "halaman": "h"}, {"id": 22, "kredit": "x", "halaman": "h"},
                    {"id": 33, "kredit": "x", "halaman": "h"}]}}


@pytest.fixture
def rv(env, tmp_path, monkeypatch):
    monkeypatch.setattr(revisi, "REVISI_DIR", str(tmp_path / "revisi"))
    monkeypatch.setattr(hr, "status_path_for_run", lambda rid: str(tmp_path / f"status_{rid}.json"))
    lagu = tmp_path / "musik"
    lagu.mkdir()
    (lagu / "lagu_a.mp3").write_bytes(b"")
    monkeypatch.setattr(music, "MUSIC_DIR", str(lagu))
    asli = hr.run_core_stages_locked
    status = {"isi": STATUS_PALSU}

    def bungkus(run_id, **kw):
        hasil = asli(run_id, **kw)
        env["calls"][-1]["tetap"] = os.environ.get("MUSIC_TRACK_TETAP")
        env["calls"][-1]["hindari"] = os.environ.get("MUSIC_TRACK_HINDARI")
        if hasil[0] == "SUCCESS" and kw.get("stages") is RENDER_STAGES:
            with open(hr.status_path_for_run(run_id), "w", encoding="utf-8") as f:
                json.dump(status["isi"], f)
        return hasil

    monkeypatch.setattr(hr, "run_core_stages_locked", bungkus)
    return {**env, "lagu": lagu, "status": status}


def render_awal(rv, capsys):
    d = buat_draf(rv, capsys)
    kode, out = jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "A")
    assert kode == 0 and out["revisi_gagal"] is None, out
    return out


# ------------------------------------------------------------------ integrasi hermes_render

def test_revisi_hanya_render_brief_sama_tanpa_llm(rv, capsys):
    awal = render_awal(rv, capsys)
    brief_awal = rv["calls"][-1]["brief"]
    kode, out = jalan(capsys, "--chat-id", CHAT, "--revisi", awal["run_id"], "--text-font", "tegas")
    assert kode == 0, out
    c = rv["calls"][-1]
    assert c["stages"] is RENDER_STAGES, "revisi tidak boleh memanggil tahap brief (LLM)"
    assert c["text_font"] == "tegas" and c["audio_mode"] == "ai", "pengaturan lama + flag baru"
    beda = {k for k in set(brief_awal) | set(c["brief"]) if brief_awal.get(k) != c["brief"].get(k)}
    assert beda <= {"revisi", "brief_id", "target_duration"}, beda
    assert c["brief"]["target_duration"] is None, "koreksi durasi (LLM) mati saat revisi"
    assert out["revisi_dari"] == awal["run_id"] and out["perubahan"] == ["font teks tulisan: tegas"]
    assert c["tetap"] == "lagu_a.mp3", "lagu lama dikunci"


def test_revisi_bertingkat(rv, capsys):
    awal = render_awal(rv, capsys)
    _, r1 = jalan(capsys, "--chat-id", CHAT, "--revisi", awal["run_id"], "--text-font", "tegas")
    kode, r2 = jalan(capsys, "--chat-id", CHAT, "--revisi", r1["run_id"], "--color-filter", "warm")
    assert kode == 0, r2
    assert rv["calls"][-1]["text_font"] == "tegas", "revisi kedua mewarisi revisi pertama"


def test_hapus_broll_mengunci_sisanya(rv, capsys):
    awal = render_awal(rv, capsys)
    kode, out = jalan(capsys, "--chat-id", CHAT, "--revisi", awal["run_id"], "--hapus-broll", "2")
    assert kode == 0, out
    rev = rv["calls"][-1]["brief"]["revisi"]
    assert [x["id"] for x in rev["broll_pakai"]] == [11, 33] and rev["broll_tolak"] == [22]
    assert rev["broll_jumlah"] == 2 and out["perubahan"] == ["B-roll ke-2 dihapus"]


def test_ganti_broll_jumlah_tetap(rv, capsys):
    awal = render_awal(rv, capsys)
    kode, out = jalan(capsys, "--chat-id", CHAT, "--revisi", awal["run_id"], "--ganti-broll", "1")
    rev = rv["calls"][-1]["brief"]["revisi"]
    assert kode == 0 and rev["broll_tolak"] == [11] and rev["broll_jumlah"] == 3


@pytest.mark.parametrize("argv, kode", [
    (["--hapus-broll", "4"], "revisi_nomor_invalid"),
    (["--hapus-broll", "1", "--ganti-broll", "1"], "revisi_nomor_invalid"),
    (["--audio-mode", "original"], "draf_terkunci"),
    ([], "revisi_kosong"),
    (["--ganti-musik"], "musik_tidak_ada_pilihan"),         # pustaka hanya berisi lagu yang sama
])
def test_revisi_ditolak(rv, capsys, argv, kode):
    awal = render_awal(rv, capsys)
    n = len(rv["calls"])
    k, out = jalan(capsys, "--chat-id", CHAT, "--revisi", awal["run_id"], *argv)
    assert k == 1 and out["kode"] == kode, out
    assert len(rv["calls"]) == n, "ditolak SEBELUM render"


def test_revisi_chat_lain_dan_id_asing_ditolak(rv, capsys):
    awal = render_awal(rv, capsys)
    _, out = jalan(capsys, "--chat-id", "DM with Lain", "--revisi", awal["run_id"], "--text-font", "tegas")
    assert out["kode"] == "revisi_chat_lain"
    _, out = jalan(capsys, "--chat-id", CHAT, "--revisi", "tidakada", "--text-font", "tegas")
    assert out["kode"] == "revisi_tidak_ada"
    _, out = jalan(capsys, "--chat-id", CHAT, "--revisi", "../x", "--text-font", "tegas")
    assert out["kode"] == "revisi_id_invalid"


def test_bahan_hilang_ditolak(rv, capsys):
    awal = render_awal(rv, capsys)
    for n in os.listdir(rv["raw"]):
        os.remove(os.path.join(rv["raw"], n))
    _, out = jalan(capsys, "--chat-id", CHAT, "--revisi", awal["run_id"], "--text-font", "tegas")
    assert out["kode"] == "revisi_bahan_hilang"


def test_ganti_musik_menghindari_lagu_lama(rv, capsys):
    (rv["lagu"] / "lagu_b.mp3").write_bytes(b"")
    awal = render_awal(rv, capsys)
    kode, out = jalan(capsys, "--chat-id", CHAT, "--revisi", awal["run_id"], "--ganti-musik")
    c = rv["calls"][-1]
    assert kode == 0 and c["hindari"] == "lagu_a.mp3" and c["tetap"] is None
    assert out["perubahan"] == ["musik TIDAK berubah (tidak ada lagu lain yang cocok)"], \
        "status palsu tetap lagu_a: laporan harus jujur"


def test_naskah_baru_di_revisi_voice_over(rv, capsys):
    awal = render_awal(rv, capsys)
    kode, out = jalan(capsys, "--chat-id", CHAT, "--revisi", awal["run_id"],
                      "--naskah", "Donor darah minggu depan. Yuk datang!")
    assert kode == 0, out
    assert rv["calls"][-1]["brief"]["full_voice_over"] == "Donor darah minggu depan. Yuk datang!"


def test_flag_revisi_tanpa_revisi_ditolak(rv, capsys):
    _, out = jalan(capsys, "--chat-id", CHAT, "--hapus-broll", "1", "--media-path", rv["bahan"][0])
    assert out["kode"] == "argumen_invalid"


# ------------------------------------------------------------------ modul revisi

def _rec(**ubah):
    rec = {"run_id": "r1", "chat_id": CHAT, "dibuat": 1000.0, "prefix": "p", "bahan": [], "args": {},
           "brief": {"audio_mode": "ai", "full_voice_over": "lama", "target_duration": 30},
           "status": revisi.ringkas_status(STATUS_PALSU)}
    rec.update(ubah)
    return rec


def test_naskah_koreksi_dipakai_lagi():
    rec = _rec(status={**revisi.ringkas_status(STATUS_PALSU),
                       "naskah_koreksi": {"full_voice_over": "hasil koreksi", "voice_over_spoken": "hasil",
                                          "scenes": [{"start": 0, "end": 2, "text": "x"}]}})
    brief, _ = revisi.brief_revisi(rec)
    assert brief["full_voice_over"] == "hasil koreksi" and brief["target_duration"] is None


def test_cutaway_tidak_mengunci_jumlah():
    st = revisi.ringkas_status({"broll": {"cara": "cutaway", "dipakai": [
        {"id": 5, "query": "blood bag", "saat_kata": "donor"}]}})
    brief, _ = revisi.brief_revisi(_rec(status=st), hapus_broll="1")
    assert "broll_jumlah" not in brief["revisi"]
    assert brief["revisi"]["broll_hapus"] == [{"id": 5, "query": "blood bag", "saat_kata": "donor"}]


def test_naskah_di_suara_asli_ditolak():
    with pytest.raises(revisi.RevisiError) as e:
        revisi.brief_revisi(_rec(brief={"audio_mode": "original"}), naskah="halo")
    assert e.value.kode == "revisi_naskah_suara_asli"


def test_kedaluwarsa(tmp_path, monkeypatch):
    monkeypatch.setattr(revisi, "REVISI_DIR", str(tmp_path))
    revisi.simpan("r1", chat_id=CHAT, prefix="p", bahan=[], args={}, brief={}, status={})
    assert revisi.muat("r1", CHAT)["run_id"] == "r1"                       # kontrol
    with pytest.raises(revisi.RevisiError) as e:
        revisi.muat("r1", CHAT, sekarang=10 ** 12)
    assert e.value.kode == "revisi_kedaluwarsa"


# ------------------------------------------------------------------ penguncian klip & lagu

def _pexels(monkeypatch, tmp_path, n=12):
    hasil = [{"id": i, "halaman": f"https://www.pexels.com/video/random-{i}/", "durasi": 10,
              "berkas": {"link": "https://videos.pexels.com/x.mp4"}, "kreator": "A"} for i in range(n)]
    monkeypatch.setattr(b, "cari", lambda q, o, per_page=15: hasil)
    monkeypatch.setattr(b, "_unduh_ke", lambda url, tujuan, maks: open(tujuan, "wb").close())
    monkeypatch.setattr(b, "_sah_video", lambda p: True)


def test_ambil_pakai_mengunci_klip_lama_walau_di_luar_lima_teratas(monkeypatch, tmp_path):
    _pexels(monkeypatch, tmp_path)
    tanpa, _ = b.ambil(["kopi"], 2, "portrait", str(tmp_path / "a"), "run-baru")
    assert [k["id"] for k in tanpa] != [9, 2], "kontrol: tanpa kunci, klip lain terpilih"
    klip, _ = b.ambil(["kopi"], 2, "portrait", str(tmp_path / "b"), "run-baru", pakai=[9, 2])
    assert [k["id"] for k in klip] == [9, 2]


def test_ambil_tolak_tidak_pernah_dipilih(monkeypatch, tmp_path):
    _pexels(monkeypatch, tmp_path)
    for run in ("r1", "r2", "r3"):
        klip, _ = b.ambil(["kopi"], 3, "portrait", str(tmp_path / run), run, tolak=[0, 1])
        assert not {0, 1} & {k["id"] for k in klip}


def test_lagu_tetap_dan_hindari(tmp_path, monkeypatch):
    for n in ("a.mp3", "b.mp3", "c.mp3"):
        (tmp_path / n).write_bytes(b"")
    pilih = {os.path.basename(music.pick_track(folder=str(tmp_path), run_id=r)) for r in "abcdefgh"}
    assert len(pilih) > 1, "kontrol: tanpa kunci, run berbeda memilih lagu berbeda"
    monkeypatch.setenv("MUSIC_TRACK_TETAP", "b.mp3")
    assert {os.path.basename(music.pick_track(folder=str(tmp_path), run_id=r)) for r in "abcdefgh"} == {"b.mp3"}
    monkeypatch.delenv("MUSIC_TRACK_TETAP")
    monkeypatch.setenv("MUSIC_TRACK_HINDARI", "b.mp3")
    assert "b.mp3" not in {os.path.basename(music.pick_track(folder=str(tmp_path), run_id=r)) for r in "abcdefgh"}


# ------------------------------------------------------------------ cutaway (mode suara asli)

def test_cutaway_revisi_hapus_satu_sisanya_sama(monkeypatch, tmp_path):
    import auto_render as ar
    kata = [{"word": w, "start": i * 1.0, "end": i * 1.0 + 0.4}
            for i, w in enumerate("halo semua hari ini kita ikut donor darah di aula kampus bersama "
                                  "relawan komunitas yang ramai sekali pagi ini".split())]
    data = {"broll": [{"query": "blood bag", "saat_kata": "donor"},
                      {"query": "campus hall", "saat_kata": "kampus"}]}
    monkeypatch.setenv("BROLL", "1")
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setattr(ar, "detail_klip", lambda p, d: 99)
    monkeypatch.setattr(ar, "build_segment", lambda *a, **k: None)
    panggil = []

    def ambil(q, jumlah, o, folder, run_id, awalan="", saring=True, pakai=(), tolak=()):
        panggil.append((q[0], list(pakai), list(tolak)))
        return [{"path": "x", "id": {"blood bag": 7, "campus hall": 8}[q[0]] if not tolak else 99,
                 "durasi": 5, "kredit": "k", "halaman": "h"}], None
    monkeypatch.setattr(ar._broll, "ambil", ambil)

    _, awal = ar.siapkan_cutaway(data, kata, 20.0, [], str(tmp_path))
    assert [d["id"] for d in awal["dipakai"]] == [7, 8]                  # kontrol: dua cutaway
    st = revisi.ringkas_status({"broll": awal})
    brief, _ = revisi.brief_revisi(_rec(brief={**data, "audio_mode": "original"}, status=st),
                                   hapus_broll="1")
    panggil.clear()
    _, info = ar.siapkan_cutaway(brief, kata, 20.0, [], str(tmp_path))
    assert [(d["query"], d["mulai"]) for d in info["dipakai"]] == \
        [(d["query"], d["mulai"]) for d in awal["dipakai"][1:]], "cutaway sisa di waktu yang sama"
    assert panggil == [("campus hall", [8], [7])], "klip lama dikunci, klip terhapus ditolak"


# ------------------------------------------------------------------ cache voice-over

@pytest.fixture
def tts(monkeypatch, tmp_path):
    import asyncio

    import auto_render as ar
    monkeypatch.setattr(ar, "TTS_CACHE_DIR", str(tmp_path / "tts"))
    monkeypatch.setenv("TTS_CACHE", "1")
    panggil = []
    kontrol = {"cadangan": False}

    async def langsung(teks, out):
        panggil.append(teks)
        with open(out, "wb") as f:
            f.write(f"audio-{len(panggil)}".encode())
        ar.TTS_KATA[:] = [{"word": "halo", "start": 0.1 * len(panggil), "end": 0.5}]
        ar.TTS_CATATAN.clear()
        ar.TTS_CATATAN.update(mesin="elevenlabs", suara="Bella")
        if kontrol["cadangan"]:
            ar.TTS_CATATAN.update(mesin="edge-tts", cadangan=True)
    monkeypatch.setattr(ar, "_generate_voice_langsung", langsung)

    def buat(teks):
        out = str(tmp_path / f"o{len(panggil)}_{teks[:3]}.mp3")
        asyncio.run(ar.generate_voice(teks, out))
        return open(out, "rb").read(), list(ar.TTS_KATA)
    return buat, panggil, kontrol, monkeypatch


def test_tts_naskah_sama_dipakai_ulang(tts):
    buat, panggil, _, _ = tts
    a = buat("Halo semua")
    assert buat("Halo semua") == a and len(panggil) == 1, "audio & waktu kata identik, tanpa TTS"
    buat("Halo kalian")
    assert len(panggil) == 2, "kontrol: naskah beda = TTS baru"


def test_tts_pengaturan_suara_beda_tidak_memakai_cache(tts):
    buat, panggil, _, mp = tts
    buat("Halo semua")
    mp.setenv("TTS_VOICE_GENDER", "pria")
    buat("Halo semua")
    assert len(panggil) == 2


def test_tts_cadangan_tidak_disimpan(tts):
    buat, panggil, kontrol, _ = tts
    kontrol["cadangan"] = True
    buat("Halo semua")
    kontrol["cadangan"] = False
    buat("Halo semua")
    assert len(panggil) == 2, "suara darurat tidak boleh terkunci untuk revisi"


def test_tts_cache_mati(tts):
    buat, panggil, _, mp = tts
    mp.setenv("TTS_CACHE", "0")
    buat("Halo semua")
    buat("Halo semua")
    assert len(panggil) == 2
