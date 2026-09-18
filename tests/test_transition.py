"""Transisi antar klip.

Dipilih "fade lewat hitam", BUKAN xfade. Alasannya terukur:
    hard cut : 8,02 dtk durasi
    xfade    : 7,67 dtk  <- MENYUSUT karena klip tumpang tindih
    fade     : 8,06 dtk  <- utuh

xfade memendekkan video sebesar durasi overlap di TIAP sambungan. Dengan 7
sambungan itu ~2,8 detik pergeseran, dan semua subtitle sesudahnya melenceng --
padahal sinkronisasi itu baru dibangun lewat map_time().
"""

import pytest

import auto_render as ar


def test_transisi_bisa_dimatikan(monkeypatch):
    monkeypatch.setattr(ar, "TRANSITION", "none")
    assert ar.fade_filters(4.0, keep_audio=True) == ("", "")


def test_klip_normal_dapat_fade_video_dan_audio():
    vf, af = ar.fade_filters(4.0, keep_audio=True)
    assert "fade=t=in:st=0" in vf and "fade=t=out" in vf
    assert "afade=t=in" in af and "afade=t=out" in af


def test_tanpa_audio_hanya_fade_video():
    """Mode voice-over AI: segmen tidak punya audio sendiri."""
    vf, af = ar.fade_filters(4.0, keep_audio=False)
    assert vf and af == ""


def test_fade_out_dimulai_sebelum_klip_habis():
    vf, _ = ar.fade_filters(4.0, keep_audio=False)
    mulai = float(vf.split("fade=t=out:st=")[1].split(":")[0])
    durasi = float(vf.split("fade=t=out:")[1].split("d=")[1])
    assert mulai + durasi == pytest.approx(4.0, abs=0.01)


def test_klip_pendek_fade_ikut_diperpendek():
    """Fade 0,2 dtk pada klip 0,5 dtk membuat klip nyaris tidak pernah terang."""
    vf, _ = ar.fade_filters(0.5, keep_audio=False)
    d = float(vf.split("fade=t=in:st=0:d=")[1].split(",")[0])
    assert d <= 0.5 / 3 + 0.001


def test_klip_sangat_pendek_dilewati():
    assert ar.fade_filters(0.1, keep_audio=True) == ("", "")


def test_fade_audio_jauh_lebih_pendek_dari_video():
    """0,2 dtk pada audio akan memotong suku kata; tujuannya cuma cegah bunyi klik."""
    vf, af = ar.fade_filters(4.0, keep_audio=True)
    dv = float(vf.split("fade=t=in:st=0:d=")[1].split(",")[0])
    da = float(af.split("afade=t=in:st=0:d=")[1].split(",")[0])
    assert da < dv


def test_fade_tidak_pernah_melebihi_sepertiga_durasi():
    for d in [0.2, 0.5, 1.0, 3.0, 10.0]:
        vf, _ = ar.fade_filters(d, keep_audio=False)
        if not vf:
            continue
        dur = float(vf.split("fade=t=in:st=0:d=")[1].split(",")[0])
        assert dur <= d / 3 + 0.001, f"durasi {d}"


def test_filter_fade_masuk_ke_rantai_segmen(monkeypatch):
    """Penjaga: fade yang tidak ikut ke perintah ffmpeg tidak menghasilkan apa pun."""
    terlihat = {}

    def fake(args, konteks):
        terlihat["args"] = args

    monkeypatch.setattr(ar, "run_ffmpeg", fake)
    ar.build_segment("/x/klip.mp4", 4.0, "/x/out.mp4", keep_audio=True)

    gabung = " ".join(terlihat["args"])
    assert "fade=t=in" in gabung, "fade video harus ikut terkirim"
    assert "afade=t=in" in gabung, "fade audio harus ikut terkirim"
