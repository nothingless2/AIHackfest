"""Validasi lampiran untuk jalur Hermes (hermes_render.py).

Sama seperti verifyInbound() di openclaw-plugin: path dari luar root yang
diizinkan harus ditolak lewat realpath, termasuk lewat symlink -- bukan
sekadar dicek prefix string.
"""

import os

import pytest

import hermes_render as hr


@pytest.fixture(autouse=True)
def roots_di_tmp(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    root.mkdir()
    luar = tmp_path / "bukan-cache"
    luar.mkdir()
    monkeypatch.setattr(hr, "MEDIA_ROOTS", [str(root)])
    return root, luar


def test_path_di_dalam_root_diterima(roots_di_tmp):
    root, _ = roots_di_tmp
    f = root / "video.mp4"
    f.write_bytes(b"x")
    hasil = hr._validate_media_paths([str(f)])
    assert hasil == [os.path.realpath(str(f))]


def test_path_di_luar_root_ditolak(roots_di_tmp):
    _, luar = roots_di_tmp
    f = luar / "rahasia.mp4"
    f.write_bytes(b"x")
    with pytest.raises(hr.MediaPathError, match="di luar folder cache"):
        hr._validate_media_paths([str(f)])


def test_symlink_di_dalam_root_menunjuk_keluar_ditolak(roots_di_tmp):
    root, luar = roots_di_tmp
    asli = luar / "rahasia.mp4"
    asli.write_bytes(b"x")
    tautan = root / "tampak-normal.mp4"
    tautan.symlink_to(asli)
    with pytest.raises(hr.MediaPathError, match="di luar folder cache"):
        hr._validate_media_paths([str(tautan)])


def test_path_tidak_ada_ditolak(roots_di_tmp):
    root, _ = roots_di_tmp
    with pytest.raises(hr.MediaPathError, match="tidak ditemukan"):
        hr._validate_media_paths([str(root / "hantu.mp4")])


def test_daftar_kosong_ditolak(roots_di_tmp):
    with pytest.raises(hr.MediaPathError, match="tidak ada lampiran"):
        hr._validate_media_paths([])


def test_stage_assets_memberi_prefix(tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    raw_dir = tmp_path / "raw"
    f1 = src_dir / "a.mp4"
    f1.write_bytes(b"x")
    monkeypatch.setattr(hr, "RAW_DIR", str(raw_dir))
    nama = hr._stage_assets([str(f1)], "abc12345")
    assert nama == ["abc12345_a.mp4"]
    assert (raw_dir / "abc12345_a.mp4").exists()


def test_parse_args_default():
    args = hr._parse_args(["--media-path", "/a.mp4"])
    assert args.media_paths == ["/a.mp4"]
    assert args.require_inspect is True
    assert args.audio_mode is None


def test_parse_args_no_require_inspect():
    args = hr._parse_args(["--media-path", "/a.mp4", "--no-require-inspect"])
    assert args.require_inspect is False


def test_label_chat_non_numerik_tidak_ditolak_allowlist(monkeypatch, capsys):
    """Regresi run nyata 24 Sep: agent mengirim --chat-id 'DM with Stringless'
    (label dari Hermes), dan dulu ditolak 'chat_tidak_diizinkan'. Sekarang lolos
    gerbang chat; kegagalan berikutnya harus soal lampiran, bukan chat."""
    import json
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "")
    monkeypatch.setattr(hr, "install_signal_handlers", lambda: None)
    monkeypatch.setattr(hr, "sweep_old_run_files", lambda: None)
    monkeypatch.setattr(hr, "ensure_dirs", lambda: None)
    monkeypatch.setattr(hr, "log_event", lambda *a, **k: None)
    rc = hr.main(["--media-path", "/etc/passwd", "--chat-id", "DM with Stringless"])
    hasil = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 1
    assert hasil["kode"] == "lampiran_invalid"
