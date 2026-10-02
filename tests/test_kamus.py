"""Kamus istilah per chat (scripts/kamus.py): ejaan benar untuk nama/merek yang salah dengar,
dikoreksi KODE di hasil transkripsi tanpa menggeser waktu subtitle."""

import json
import os

import pytest

import kamus as km

ENTRI = [km.validasi_entri("OpenClaw", ["open cloud", "opencloud", "open claw"])]


def _kata(*pasang):
    t, hasil = 0.0, []
    for w in pasang:
        hasil.append({"word": w, "start": round(t, 2), "end": round(t + 0.4, 2)})
        t += 0.5
    return hasil


# ------------------------------------------------------------------ koreksi

def test_dua_kata_digabung_dan_waktunya_dipertahankan():
    kata = _kata("ganti", "API", "di", "open", "cloud,", "error")
    baru, n = km.koreksi_kata(kata, ENTRI)
    assert [w["word"] for w in baru] == ["ganti", "API", "di", "OpenClaw,", "error"] and n == 1
    assert (baru[3]["start"], baru[3]["end"]) == (kata[3]["start"], kata[4]["end"]), "subtitle tetap sinkron"
    assert baru[4] == kata[5], "kata sesudahnya tidak bergeser"


def test_satu_kata_dan_kapital_dirapikan():
    baru, n = km.koreksi_kata(_kata("pakai", "opencloud", "dan", "openclaw", "lalu", "OpenClaw"), ENTRI)
    assert [w["word"] for w in baru] == ["pakai", "OpenClaw", "dan", "OpenClaw", "lalu", "OpenClaw"]
    assert n == 2, "yang sudah benar tidak dihitung sebagai koreksi"


def test_tidak_mengoreksi_di_dalam_kata_lain_dan_tanpa_kamus_tidak_berubah():
    kata = _kata("opencloudy", "open", "source", "cloud")
    assert km.koreksi_kata(kata, ENTRI) == (kata, 0), "kontrol negatif"
    assert km.koreksi_kata(kata, []) == (kata, 0)
    assert km.koreksi_teks("opencloudy dan open source", ENTRI) == ("opencloudy dan open source", 0)


def test_bentuk_terpanjang_menang():
    entri = [km.validasi_entri("OpenClaw", ["open"]), km.validasi_entri("OpenClaw Pro", ["open pro"])]
    baru, _ = km.koreksi_kata(_kata("coba", "open", "pro", "sekarang"), entri)
    assert [w["word"] for w in baru] == ["coba", "OpenClaw Pro", "sekarang"]


def test_teks_segmen_batas_kata_dan_spasi_lentur():
    teks, n = km.koreksi_teks("Di Open  Cloud error, di open-cloud juga. opencloud!", ENTRI)
    assert teks == "Di OpenClaw error, di OpenClaw juga. OpenClaw!" and n == 3


def test_koreksi_transkrip_dan_brief():
    d = {"text": "pakai open cloud", "segments": [{"text": "pakai open cloud"}], "words": _kata("pakai", "open", "cloud")}
    assert km.koreksi_transkrip(d, ENTRI) == 1
    assert d["text"] == "pakai OpenClaw" and d["segments"][0]["text"] == "pakai OpenClaw" and len(d["words"]) == 2
    brief = {"transcript_words": {"a.mp4": _kata("di", "opencloud")}, "transcript_segments": {"a.mp4": [{"text": "di opencloud"}]}}
    assert km.koreksi_brief(brief, ENTRI) == 1 and brief["transcript_words"]["a.mp4"][1]["word"] == "OpenClaw"
    assert km.koreksi_brief(brief, ENTRI) == 0, "idempoten: revisi berulang tidak mengubah lagi"


# ------------------------------------------------------------------ validasi & penyimpanan

@pytest.mark.parametrize("benar, salah", [("", ["x"]), ("x" * 41, []), ("OpenClaw", ["oc"]),
                                          ("OpenClaw", ["satu dua tiga empat lima"]), ("OpenClaw", [str(i) * 3 for i in range(9)])])
def test_entri_tidak_sah_ditolak(benar, salah):
    with pytest.raises(km.KamusError):
        km.validasi_entri(benar, salah)


def test_bentuk_salah_yang_sama_dengan_benar_dibuang():
    assert km.validasi_entri("OpenClaw", ["openclaw", "Open Cloud"])["salah"] == ["open cloud"]


def test_kamus_per_chat_tambah_gabung_hapus():
    km.tambah("DM A", "OpenClaw", ["open cloud"])
    km.tambah("DM A", "openclaw", ["opencloud"])
    assert km.daftar("DM A") == [{"benar": "openclaw", "salah": ["open cloud", "opencloud"]}], "bentuk salah digabung"
    assert km.daftar("DM B") == [], "kamus chat lain tidak berlaku"
    assert km.hapus("DM A", "OPENCLAW") is True and km.daftar("DM A") == [] and km.hapus("DM A", "x") is False
    with pytest.raises(km.KamusError):
        km.tambah("", "OpenClaw", ["open cloud"])


def test_kamus_tidak_menghapus_gaya_di_profil_yang_sama():
    import gaya
    gaya.simpan_gaya("DM A", "hype")
    km.tambah("DM A", "OpenClaw", ["open cloud"])
    assert gaya.profil("DM A")["gaya"] == "hype" and km.daftar("DM A")
    gaya.simpan_gaya("DM A", "elegan")
    assert km.daftar("DM A"), "ganti gaya tidak menghapus kamus"


# ------------------------------------------------------------------ jalur pipeline

def test_env_dipasang_dibersihkan_dan_divalidasi_ulang():
    km.tambah("DM A", "OpenClaw", ["open cloud"])
    env = {}
    assert km.pasang("DM A", env) and km.aktif(env)[0]["benar"] == "OpenClaw"
    km.pasang("DM B", env)
    assert "KAMUS_ISTILAH" not in env, "kamus chat sebelumnya tidak boleh tersisa"
    assert km.aktif({"KAMUS_ISTILAH": "{rusak"}) == [] and km.aktif({"KAMUS_ISTILAH": json.dumps([{"benar": ""}])}) == []


def test_istilah_kamus_ikut_dibiaskan_ke_whisper(monkeypatch):
    import transcribe
    monkeypatch.delenv("KAMUS_ISTILAH", raising=False)
    assert "OpenClaw" not in transcribe.build_vocab_prompt("edit ya"), "kontrol"
    monkeypatch.setenv("KAMUS_ISTILAH", json.dumps(ENTRI))
    p = transcribe.build_vocab_prompt("edit ya")
    assert p.startswith("Nama dan istilah: OpenClaw.") and len(p) <= transcribe.TRANSCRIBE_PROMPT_MAX_CHARS


def test_render_memasang_kamus_chat_ke_tahap_pipeline(monkeypatch, tmp_path):
    import hermes_render as hr
    km.tambah("DM A", "OpenClaw", ["open cloud"])
    root = tmp_path / "cache"
    root.mkdir()
    (root / "v.mp4").write_bytes(b"x")
    monkeypatch.setattr(hr, "MEDIA_ROOTS", [str(root)])
    for n in ("install_signal_handlers", "sweep_old_run_files", "ensure_dirs", "log_event"):
        monkeypatch.setattr(hr, n, lambda *a, **k: None)
    tangkap = {}

    def palsu(*a, **k):
        tangkap["kamus"] = os.environ.get("KAMUS_ISTILAH")
        raise RuntimeError("berhenti")
    monkeypatch.setattr(hr, "run_core_stages_locked", palsu)
    monkeypatch.setattr(hr, "pick_track", lambda *a, **k: None)
    monkeypatch.setattr(hr, "_stage_assets", lambda paths, pre: [f"{pre}_x.mp4"])
    for chat, harap in (("DM A", True), ("DM B", False)):
        tangkap.clear()
        monkeypatch.delenv("KAMUS_ISTILAH", raising=False)
        with pytest.raises(RuntimeError):
            hr.main(["--media-path", str(root / "v.mp4"), "--no-require-inspect", "--chat-id", chat])
        assert bool(tangkap["kamus"]) is harap


def test_carousel_dari_video_memakai_ejaan_kamus():
    import carousel
    import revisi
    import time
    os.makedirs(revisi.REVISI_DIR, exist_ok=True)
    json.dump({"run_id": "abc12345", "chat_id": "DM A", "dibuat": time.time(), "bahan": ["a.mp4"], "prefix": "x",
               "brief": {"transcript_segments": {"a.mp4": [{"text": "ganti API di open cloud"}]}}},
              open(os.path.join(revisi.REVISI_DIR, "abc12345.json"), "w"))
    assert "open cloud" in carousel.sumber_dari_run("abc12345", "DM A")[0], "kontrol: tanpa kamus apa adanya"
    km.tambah("DM A", "OpenClaw", ["open cloud"])
    assert "OpenClaw" in carousel.sumber_dari_run("abc12345", "DM A")[0]


def test_cli(capsys):
    assert km.main(["tambah", "--chat-id", "DM A", "--benar", "OpenClaw", "--salah", "open cloud"]) == 0
    assert json.loads(capsys.readouterr().out)["jumlah"] == 1
    assert km.main(["daftar", "--chat-id", "DM A"]) == 0
    assert json.loads(capsys.readouterr().out)["kamus"][0]["benar"] == "OpenClaw"
    assert km.main(["tambah", "--chat-id", "", "--benar", "X"]) == 1
    assert km.main(["tambah", "--chat-id", "DM A", "--benar", "OpenClaw", "--salah", "oc"]) == 1


def test_revisi_ejaan_saja_sah_dan_butuh_kamus_berisi(monkeypatch, tmp_path, capsys):
    """"Betulkan ejaan di video tadi" tidak mengubah flag apa pun; tanpa --kamus revisi itu ditolak
    sebagai revisi_kosong."""
    import hermes_render as hr
    import revisi
    import time
    os.makedirs(revisi.REVISI_DIR, exist_ok=True)
    json.dump({"run_id": "abc12345", "chat_id": "DM A", "dibuat": time.time(), "bahan": [], "prefix": "x",
               "args": {}, "status": {}, "brief": {"scenes": []}}, open(os.path.join(revisi.REVISI_DIR, "abc12345.json"), "w"))
    for n in ("install_signal_handlers", "sweep_old_run_files", "ensure_dirs", "log_event"):
        monkeypatch.setattr(hr, n, lambda *a, **k: None)
    monkeypatch.setattr(hr.revisi, "brief_revisi", lambda rec, **k: ({}, []))
    sampai = []
    monkeypatch.setattr(hr, "run_core_stages_locked", lambda *a, **k: sampai.append(1) or (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(hr, "pick_track", lambda *a, **k: None)

    def jalan(*argv):
        try:
            rc = hr.main(["--chat-id", "DM A", "--revisi", "abc12345", *argv])
        except RuntimeError:
            return "render", None
        return rc, json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert jalan()[1]["kode"] == "revisi_kosong", "kontrol: tanpa perubahan ditolak"
    assert jalan("--kamus")[1]["kode"] == "kamus_kosong"
    km.tambah("DM A", "OpenClaw", ["open cloud"])
    assert jalan("--kamus")[0] == "render" and sampai, "dengan kamus berisi, revisi berjalan"
