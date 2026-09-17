"""Scoping bahan mentah: satu run hanya boleh memakai file miliknya sendiri.

workspace/raw/ dipakai bersama semua run dan semua user dan tidak pernah dihapus,
jadi "pakai semua isi folder" adalah jalur kebocoran antar-user.
"""

import os

import pytest

import agent1_2_brief as brief
import common


@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    """RAW_DIR diarahkan ke tmp_path -- tidak pernah menyentuh workspace asli."""
    d = tmp_path / "raw"
    d.mkdir()
    monkeypatch.setattr(common, "RAW_DIR", str(d))
    monkeypatch.setattr(common, "ensure_dirs", lambda: None)
    return d


def buat(d, *names):
    for n in names:
        (d / n).write_bytes(b"konten palsu")


# ---------- jalur plugin/Telegram: ketat ----------

def test_run_plugin_hanya_memakai_bahan_miliknya(raw_dir, monkeypatch):
    buat(raw_dir, "aaaa1111_foto1.jpg", "aaaa1111_foto2.jpg", "bbbb2222_milik_orang_lain.jpg")
    monkeypatch.setenv("CONTENT_FACTORY_RUN_PREFIX", "aaaa1111")
    monkeypatch.setenv("CONTENT_FACTORY_ASSETS", "aaaa1111_foto1.jpg,aaaa1111_foto2.jpg")

    hasil = brief.select_assets()

    assert hasil == ["aaaa1111_foto1.jpg", "aaaa1111_foto2.jpg"]
    assert "bbbb2222_milik_orang_lain.jpg" not in hasil


def test_dua_run_berbeda_tidak_saling_memakai_bahan(raw_dir, monkeypatch):
    buat(raw_dir, "aaaa1111_a.jpg", "bbbb2222_b.jpg")

    monkeypatch.setenv("CONTENT_FACTORY_RUN_PREFIX", "aaaa1111")
    monkeypatch.setenv("CONTENT_FACTORY_ASSETS", "aaaa1111_a.jpg")
    run_a = brief.select_assets()

    monkeypatch.setenv("CONTENT_FACTORY_RUN_PREFIX", "bbbb2222")
    monkeypatch.setenv("CONTENT_FACTORY_ASSETS", "bbbb2222_b.jpg")
    run_b = brief.select_assets()

    assert run_a == ["aaaa1111_a.jpg"]
    assert run_b == ["bbbb2222_b.jpg"]
    assert set(run_a).isdisjoint(run_b)


def test_plugin_tanpa_daftar_bahan_GAGAL_bukan_pakai_semua(raw_dir, monkeypatch):
    """Inti perbaikannya: dulu ini diam-diam memakai SELURUH isi folder."""
    buat(raw_dir, "aaaa1111_a.jpg", "bbbb2222_milik_orang_lain.jpg")
    monkeypatch.setenv("CONTENT_FACTORY_RUN_PREFIX", "aaaa1111")
    monkeypatch.setenv("CONTENT_FACTORY_ASSETS", "")

    with pytest.raises(ValueError, match="TIDAK jatuh ke seluruh isi"):
        brief.select_assets()


def test_plugin_menolak_bahan_milik_run_lain(raw_dir, monkeypatch):
    buat(raw_dir, "aaaa1111_a.jpg", "bbbb2222_milik_orang_lain.jpg")
    monkeypatch.setenv("CONTENT_FACTORY_RUN_PREFIX", "aaaa1111")
    monkeypatch.setenv("CONTENT_FACTORY_ASSETS", "aaaa1111_a.jpg,bbbb2222_milik_orang_lain.jpg")

    with pytest.raises(ValueError, match="bukan milik run ini"):
        brief.select_assets()


# ---------- jalur CLI: perilaku lama dipertahankan ----------

def test_cli_tanpa_prefix_tetap_memakai_semua_isi_folder(raw_dir, monkeypatch, capsys):
    buat(raw_dir, "satu.jpg", "dua.mp4")
    monkeypatch.delenv("CONTENT_FACTORY_RUN_PREFIX", raising=False)
    monkeypatch.delenv("CONTENT_FACTORY_ASSETS", raising=False)

    hasil = brief.select_assets()

    assert sorted(hasil) == ["dua.mp4", "satu.jpg"]
    assert "[warn]" in capsys.readouterr().out, "harus tetap memperingatkan"


def test_folder_kosong_tetap_pesan_jelas(raw_dir, monkeypatch):
    monkeypatch.delenv("CONTENT_FACTORY_RUN_PREFIX", raising=False)
    monkeypatch.delenv("CONTENT_FACTORY_ASSETS", raising=False)

    with pytest.raises(ValueError, match="Belum ada bahan mentah"):
        brief.select_assets()


# ---------- resolve_assets: tahan path traversal ----------

def test_resolve_assets_menolak_path_traversal(raw_dir):
    with pytest.raises(ValueError, match="nama file polos"):
        common.resolve_assets(["../../../etc/passwd"])


def test_resolve_assets_menolak_path_absolut(raw_dir):
    with pytest.raises(ValueError, match="nama file polos"):
        common.resolve_assets(["/etc/passwd"])


def test_resolve_assets_menolak_subfolder(raw_dir):
    with pytest.raises(ValueError, match="nama file polos"):
        common.resolve_assets(["sub/berkas.jpg"])


def test_resolve_assets_menolak_symlink_keluar_folder(raw_dir, tmp_path):
    luar = tmp_path / "rahasia.jpg"
    luar.write_bytes(b"data di luar workspace")
    os.symlink(str(luar), str(raw_dir / "tampak_normal.jpg"))

    with pytest.raises(ValueError, match="menunjuk keluar"):
        common.resolve_assets(["tampak_normal.jpg"])


def test_resolve_assets_menerima_nama_wajar(raw_dir):
    buat(raw_dir, "aaaa1111_foto.jpg")
    paths = common.resolve_assets(["aaaa1111_foto.jpg"])
    assert paths[0].endswith("aaaa1111_foto.jpg")
    assert os.path.exists(paths[0])
