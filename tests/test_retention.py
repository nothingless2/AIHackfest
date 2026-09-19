"""Retensi & pembersihan file kerja render.

Semua path diarahkan ke tmp_path — tidak pernah menyentuh workspace asli.
"""

import os
import time

import pytest

import common
import retention


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    drafts = tmp_path / "drafts"
    state = tmp_path / "state"
    raw = tmp_path / "raw"
    published = tmp_path / "published"
    for d in (drafts, state, raw, published):
        d.mkdir()

    monkeypatch.setattr(retention, "DRAFTS_DIR", str(drafts))
    monkeypatch.setattr(retention, "STATE_DIR", str(state))
    monkeypatch.setattr(retention, "DRAFT_VIDEO_PATH", str(drafts / "video_output.mp4"))
    monkeypatch.setattr(retention, "DRAFT_THUMB_PATH", str(drafts / "video_output.jpg"))
    monkeypatch.setattr(retention, "BRIEF_PATH", str(state / "creative_brief.json"))
    monkeypatch.setattr(
        retention,
        "_LINDUNGI",
        {"video_output.mp4", "video_output.jpg", "creative_brief.json"},
    )
    return {"drafts": drafts, "state": state, "raw": raw, "published": published}


def tulis(path, umur_hari=0):
    path.write_bytes(b"x")
    if umur_hari:
        lama = time.time() - umur_hari * 86400
        os.utime(path, (lama, lama))
    return path


# ---------- clear_render_workspace ----------

def test_membersihkan_sisa_file_kerja_render(dirs):
    d = dirs["drafts"]
    tulis(d / "_segment_0.mp4")
    tulis(d / "_segment_1.mp4")
    tulis(d / "_combined_silent.mp4")
    tulis(d / "_combined_text.mp4")
    tulis(d / "_concat_list.txt")
    tulis(d / "temp_vo.mp3")

    retention.clear_render_workspace()

    assert list(d.iterdir()) == [], "semua file kerja harus hilang"


def test_membuang_draft_lama_agar_tidak_terkirim_tidak_sengaja(dirs):
    """Pengaman pra-1a yang sempat hilang, dipulihkan."""
    lama = tulis(dirs["drafts"] / "video_output.mp4")
    retention.clear_render_workspace()
    assert not lama.exists()


def test_tidak_menyentuh_bahan_mentah_user(dirs):
    bahan = tulis(dirs["raw"] / "aaaa1111_foto.jpg")
    tulis(dirs["drafts"] / "_segment_0.mp4")

    retention.clear_render_workspace()

    assert bahan.exists(), "workspace/raw/ tidak boleh pernah disentuh"


def test_tidak_menghapus_video_per_run(dirs):
    """Hasil per-run bukan file kerja; hanya retensi yang boleh membuangnya."""
    hasil = tulis(dirs["drafts"] / "video_abc123.mp4")
    retention.clear_render_workspace()
    assert hasil.exists()


def test_aman_dipanggil_saat_folder_kosong(dirs):
    assert retention.clear_render_workspace() == 0


# ---------- cleanup_run_files ----------

def test_cleanup_menghapus_video_dan_brief_run(dirs):
    v = tulis(dirs["drafts"] / "video_run1.mp4")
    b = tulis(dirs["state"] / "creative_brief_run1.json")

    retention.cleanup_run_files("run1")

    assert not v.exists() and not b.exists()


def test_cleanup_video_false_hanya_buang_brief(dirs):
    """Dipakai setelah video dipindah ke published/ — video tidak boleh dihapus."""
    v = tulis(dirs["drafts"] / "video_run1.mp4")
    b = tulis(dirs["state"] / "creative_brief_run1.json")

    retention.cleanup_run_files("run1", video=False)

    assert v.exists(), "video sudah dipindah, jangan dihapus"
    assert not b.exists()


def test_cleanup_idempotent(dirs):
    retention.cleanup_run_files("tidak-ada")
    retention.cleanup_run_files("tidak-ada")  # tidak boleh melempar


# ---------- sweep_old_run_files ----------

def test_sweep_membuang_yang_tua_menyisakan_yang_baru(dirs):
    tua = tulis(dirs["drafts"] / "video_tua.mp4", umur_hari=30)
    baru = tulis(dirs["drafts"] / "video_baru.mp4", umur_hari=1)

    retention.sweep_old_run_files(max_age_days=7)

    assert not tua.exists()
    assert baru.exists()


def test_sweep_TIDAK_menyentuh_video_output(dirs):
    """video_output.mp4 cocok dengan pola video_*.mp4 tapi itu path kerja run
    berjalan — kalau ikut tersapu, render yang sedang jalan bisa kehilangan hasilnya."""
    tetap = tulis(dirs["drafts"] / "video_output.mp4", umur_hari=90)

    retention.sweep_old_run_files(max_age_days=7)

    assert tetap.exists(), "video_output.mp4 harus dilindungi dari sweep"


def test_sweep_TIDAK_menyentuh_creative_brief_tetap(dirs):
    tetap = tulis(dirs["state"] / "creative_brief.json", umur_hari=90)
    retention.sweep_old_run_files(max_age_days=7)
    assert tetap.exists()


def test_sweep_TIDAK_menyentuh_bahan_mentah(dirs):
    bahan = tulis(dirs["raw"] / "aaaa1111_foto.jpg", umur_hari=365)
    retention.sweep_old_run_files(max_age_days=7)
    assert bahan.exists(), "bahan mentah user tidak boleh disapu retensi"


def test_sweep_TIDAK_menyentuh_arsip_published(dirs):
    arsip = tulis(dirs["published"] / "run1.mp4", umur_hari=365)
    retention.sweep_old_run_files(max_age_days=7)
    assert arsip.exists(), "published/ adalah arsip permanen"


def test_sweep_membuang_brief_per_run_yang_tua(dirs):
    tua = tulis(dirs["state"] / "creative_brief_lama.json", umur_hari=30)
    retention.sweep_old_run_files(max_age_days=7)
    assert not tua.exists()


# ---------- cover (.jpg) ikut diurus retensi ----------

def test_clear_membuang_cover_lama(dirs):
    """Cover run sebelumnya harus hilang: kalau render baru gagal membuat cover,
    cover lama akan terkirim bersama video baru — gambar milik konten lain."""
    lama = tulis(dirs["drafts"] / "video_output.jpg")
    retention.clear_render_workspace()
    assert not lama.exists()


def test_cleanup_menghapus_cover_per_run(dirs):
    v = tulis(dirs["drafts"] / "video_run1.mp4")
    c = tulis(dirs["drafts"] / "video_run1.jpg")

    retention.cleanup_run_files("run1")

    assert not v.exists() and not c.exists()


def test_cleanup_video_false_TIDAK_menghapus_cover(dirs):
    """video=False berarti keduanya sudah dipindah ke published/."""
    c = tulis(dirs["drafts"] / "video_run1.jpg")
    retention.cleanup_run_files("run1", video=False)
    assert c.exists()


def test_sweep_membuang_cover_per_run_yang_tua(dirs):
    """Tanpa ini cover jadi file yatim: tidak ada pola lama yang cocok dengan .jpg."""
    tua = tulis(dirs["drafts"] / "video_tua.jpg", umur_hari=30)
    baru = tulis(dirs["drafts"] / "video_baru.jpg", umur_hari=1)

    retention.sweep_old_run_files(max_age_days=7)

    assert not tua.exists()
    assert baru.exists()


def test_sweep_TIDAK_menyentuh_cover_run_berjalan(dirs):
    tetap = tulis(dirs["drafts"] / "video_output.jpg", umur_hari=90)
    retention.sweep_old_run_files(max_age_days=7)
    assert tetap.exists()


# ---------- status pemeriksaan bahan ----------

def test_sweep_membuang_status_pemeriksaan_yang_tua(dirs):
    (dirs["state"] / "inspect").mkdir()
    tua = tulis(dirs["state"] / "inspect" / "aaaaaaaaaaaa.json", umur_hari=30)
    baru = tulis(dirs["state"] / "inspect" / "bbbbbbbbbbbb.json", umur_hari=1)
    retention.sweep_old_run_files(max_age_days=7)
    assert not tua.exists() and baru.exists()
