"""common.jalur_kirim: path hasil yang bisa dikirim gateway sebagai `MEDIA:`.

Latar belakang (4 Okt): gateway Hermes di Windows membuang SEMUA path container Linux ("not found on
this host"), jadi hasil render tidak sampai ke Telegram walau berkasnya ada di workspace."""

import json
import os

import pytest

import carousel
import common
import gaya


@pytest.fixture
def ws(tmp_path, monkeypatch):
    akar = tmp_path / "workspace"
    (akar / "carousel" / "abc").mkdir(parents=True)
    monkeypatch.setattr(common, "WORKSPACE_DIR", str(akar))
    monkeypatch.delenv("KLIPA_HOST_WORKSPACE", raising=False)
    return akar


def _berkas(p, isi=b"x"):
    p.write_bytes(isi)
    return str(p)


def test_tanpa_pemetaan_path_apa_adanya_dan_berurutan(ws):
    a, b = _berkas(ws / "carousel" / "abc" / "ig_02.jpg"), _berkas(ws / "carousel" / "abc" / "ig_01.jpg")
    data = {"slide": {"ig": [a, b], "tiktok": [a]}, "x": "bukan path"}
    assert common.jalur_kirim(data) == [os.path.realpath(a), os.path.realpath(b)], "urut muncul, tanpa duplikat"


def test_pemetaan_ke_path_komputer(ws, monkeypatch):
    p = _berkas(ws / "carousel" / "abc" / "ig_01.jpg")
    monkeypatch.setenv("KLIPA_HOST_WORKSPACE", "D:/AIHackfest/workspace/")           # garis miring akhir dibuang
    assert common.jalur_kirim({"a": [p]}) == ["D:/AIHackfest/workspace/carousel/abc/ig_01.jpg"]
    monkeypatch.setenv("KLIPA_HOST_WORKSPACE", "D:\\AIHackfest\\workspace")           # gaya Windows
    assert common.jalur_kirim(p) == ["D:/AIHackfest/workspace/carousel/abc/ig_01.jpg"]


def test_berkas_tidak_ada_atau_bukan_media_tidak_ikut(ws):
    ada = _berkas(ws / "carousel" / "abc" / "ig_01.jpg")
    _berkas(ws / "catatan.txt")
    data = [ada, str(ws / "carousel" / "abc" / "hilang.jpg"), str(ws / "catatan.txt"), "relatif/ig.jpg", 5, None]
    assert common.jalur_kirim(data) == [os.path.realpath(ada)], "kontrol: yang sah tetap lolos"


def test_di_luar_workspace_ditolak(ws, tmp_path, monkeypatch):
    """Agent tidak boleh membuat gateway mengirim berkas sembarang lewat hasil skrip."""
    luar = _berkas(tmp_path / "rahasia.jpg")
    monkeypatch.setenv("KLIPA_HOST_WORKSPACE", "D:/AIHackfest/workspace")
    dalam = _berkas(ws / "carousel" / "abc" / "ig_01.jpg")
    assert common.jalur_kirim([luar, dalam]) == ["D:/AIHackfest/workspace/carousel/abc/ig_01.jpg"]
    assert common.jalur_kirim([str(ws / ".." / "rahasia.jpg")]) == [], "naik lewat .. ditolak"
    # Awalan nama sama tapi folder lain: "workspace_lain" bukan di dalam "workspace".
    lain = tmp_path / "workspace_lain"
    lain.mkdir()
    assert common.jalur_kirim([_berkas(lain / "x.jpg")]) == []


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="symlink butuh hak khusus di Windows")
def test_symlink_keluar_workspace_ditolak(ws, tmp_path):
    luar = tmp_path / "rahasia.jpg"
    luar.write_bytes(b"x")
    os.symlink(luar, ws / "carousel" / "abc" / "tautan.jpg")
    assert common.jalur_kirim([str(ws / "carousel" / "abc" / "tautan.jpg")]) == []


def test_carousel_dan_gaya_mencetak_kirim(ws, monkeypatch, capsys):
    p = _berkas(ws / "carousel" / "abc" / "ig_01.jpg")
    monkeypatch.setenv("KLIPA_HOST_WORKSPACE", "D:/AIHackfest/workspace")
    monkeypatch.setattr(carousel, "buat", lambda **k: {"ok": True, "slide": {"ig": [p]}, "qa": {}})
    assert carousel.main(["--chat-id", "DM A", "--teks", "x"]) == 0
    assert json.loads(capsys.readouterr().out)["kirim"] == ["D:/AIHackfest/workspace/carousel/abc/ig_01.jpg"]

    monkeypatch.setattr(carousel, "buat", lambda **k: (_ for _ in ()).throw(carousel.CarouselError("x", "gagal")))
    assert carousel.main(["--chat-id", "DM A", "--teks", "x"]) == 1
    assert "kirim" not in json.loads(capsys.readouterr().out), "gagal tidak membawa daftar kirim"

    import overlay_remotion as orr
    monkeypatch.setattr(orr, "render_pratinjau_gaya", lambda presets, out: _berkas(__import__("pathlib").Path(out)) and out)
    monkeypatch.setattr(gaya, "PRATINJAU_DIR", str(ws / "pratinjau"))
    assert gaya.main(["kustom", "--chat-id", "DM A", "--dasar", "hype", "--aksen", "#0EA5E9"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kirim"] == ["D:/AIHackfest/workspace/pratinjau/" + os.path.basename(out["gambar"])]
