"""Commit 1b: lock approval, arsip published/, dan urutan cleanup.

Titik paling rawan: record_history() harus dipanggil dengan path yang BENAR
untuk momennya, dan cleanup harus terjadi PALING AKHIR — setelah semua pembacaan
video_path/brief_path selesai.
"""

import json
import os

import pytest

import agent4_approval as a4

CHAT = "111222333"


@pytest.fixture
def lingkungan(tmp_path, monkeypatch):
    drafts = tmp_path / "drafts"
    state = tmp_path / "state"
    published = tmp_path / "published"
    for d in (drafts, state, published):
        d.mkdir()

    run_id = "run-uji"
    video = drafts / f"video_{run_id}.mp4"
    video.write_bytes(b"video palsu")
    brief = state / f"creative_brief_{run_id}.json"
    brief.write_text(json.dumps({"judul": "Judul Uji", "hashtags": ["#a"], "brief_id": "b1"}))
    riwayat = state / "publish_history.json"

    monkeypatch.setenv("CONTENT_FACTORY_RUN_ID", run_id)
    monkeypatch.setattr(a4, "PUBLISHED_DIR", str(published))
    monkeypatch.setattr(a4, "PUBLISH_HISTORY_PATH", str(riwayat))
    monkeypatch.setattr(a4, "draft_video_path_for_run", lambda rid: str(video))
    monkeypatch.setattr(a4, "brief_path_for_run", lambda rid: str(brief))
    monkeypatch.setattr(a4, "ensure_dirs", lambda: None)
    monkeypatch.setattr(a4, "sweep_old_run_files", lambda *a, **k: 0)
    monkeypatch.setattr(a4, "resolve_chat_id", lambda: CHAT)
    monkeypatch.setattr(a4, "telegram_configured", lambda c=None: True)
    monkeypatch.setattr(a4, "warn_if_gateway_polling", lambda: False)
    monkeypatch.setattr(a4, "get_latest_update_id", lambda: 0)
    monkeypatch.setattr(a4, "send_video", lambda *a, **k: True)
    monkeypatch.setattr(a4, "log_event", lambda *a, **k: None)

    pesan = []
    monkeypatch.setattr(a4, "notify", lambda k, m, **kw: pesan.append(m))

    dibersihkan = []
    monkeypatch.setattr(
        a4, "cleanup_run_files",
        lambda rid, **kw: dibersihkan.append((rid, kw.get("video", True))),
    )

    return {"video": video, "brief": brief, "riwayat": riwayat,
            "published": published, "pesan": pesan, "dibersihkan": dibersihkan,
            "run_id": run_id}


def riwayat_terakhir(p):
    return json.loads(p.read_text())[-1]


# ---------- APPROVED: video diarsipkan, bukan dihapus ----------

def test_approved_memindah_video_ke_published(lingkungan, monkeypatch):
    monkeypatch.setattr(a4, "wait_for_reply", lambda *a, **k: "APPROVED")

    assert a4.run() == 0

    arsip = lingkungan["published"] / f"{lingkungan['run_id']}.mp4"
    assert arsip.exists(), "video disetujui harus diarsipkan"
    assert not lingkungan["video"].exists(), "file lama sudah pindah"


def test_approved_file_path_di_riwayat_menunjuk_arsip_yang_ADA(lingkungan, monkeypatch):
    """publish_history.json menyimpan file_path permanen. Kalau ia menunjuk ke
    path per-run yang lalu dibuang retensi, riwayatnya jadi rujukan mati."""
    monkeypatch.setattr(a4, "wait_for_reply", lambda *a, **k: "APPROVED")

    a4.run()

    entri = riwayat_terakhir(lingkungan["riwayat"])
    assert entri["status"] == "APPROVED_AWAITING_MANUAL_UPLOAD"
    assert "published" in entri["file_path"]
    assert os.path.exists(entri["file_path"]), "file_path harus benar-benar ada"


def test_approved_hanya_membersihkan_brief_bukan_video(lingkungan, monkeypatch):
    monkeypatch.setattr(a4, "wait_for_reply", lambda *a, **k: "APPROVED")

    a4.run()

    assert lingkungan["dibersihkan"] == [("run-uji", False)], "video=False wajib"


def test_approved_membaca_judul_dari_brief_sebelum_dibersihkan(lingkungan, monkeypatch):
    """Bug urutan yang dihindari: cleanup di finally akan menghapus brief SEBELUM
    record_history sempat membacanya."""
    monkeypatch.setattr(a4, "wait_for_reply", lambda *a, **k: "APPROVED")

    a4.run()

    assert riwayat_terakhir(lingkungan["riwayat"])["judul"] == "Judul Uji"


# ---------- REJECTED / TIMEOUT: dibersihkan ----------

@pytest.mark.parametrize("keputusan,status", [
    ("REJECTED", "REJECTED"),
    ("TIMEOUT", "TIMEOUT_NO_REPLY"),
])
def test_ditolak_atau_timeout_membersihkan_semuanya(lingkungan, monkeypatch, keputusan, status):
    monkeypatch.setattr(a4, "wait_for_reply", lambda *a, **k: keputusan)

    assert a4.run() == 0

    assert riwayat_terakhir(lingkungan["riwayat"])["status"] == status
    assert lingkungan["dibersihkan"] == [("run-uji", True)]
    assert not (lingkungan["published"] / "run-uji.mp4").exists(), "tidak diarsipkan"


def test_ditolak_riwayat_menunjuk_path_per_run(lingkungan, monkeypatch):
    monkeypatch.setattr(a4, "wait_for_reply", lambda *a, **k: "REJECTED")
    a4.run()
    assert riwayat_terakhir(lingkungan["riwayat"])["file_path"] == str(lingkungan["video"])


# ---------- lock approval sibuk ----------

def test_lock_sibuk_TIDAK_membersihkan_apa_pun(lingkungan, monkeypatch):
    """File dibiarkan utuh supaya bisa dicoba lagi tanpa render ulang —
    render ulang berarti membakar kredit GPT-4o lagi."""
    from run_lock import FileLockBusyError

    def sibuk(*a, **k):
        raise FileLockBusyError(42.0, {"holder_id": "run-lain"})

    monkeypatch.setattr(a4, "acquire_approval_lock", sibuk)

    rc = a4.run()

    assert rc == os.EX_TEMPFAIL
    assert lingkungan["dibersihkan"] == [], "JANGAN cleanup saat lock sibuk"
    assert lingkungan["video"].exists()
    assert lingkungan["brief"].exists()


def test_pesan_lock_sibuk_menyebut_run_id_dan_perintah_ulang(lingkungan, monkeypatch):
    from run_lock import FileLockBusyError

    def sibuk(*a, **k):
        raise FileLockBusyError(42.0, {"holder_id": "run-lain"})

    monkeypatch.setattr(a4, "acquire_approval_lock", sibuk)
    a4.run()

    pesan = lingkungan["pesan"][0]
    assert "run-uji" in pesan
    assert "CONTENT_FACTORY_RUN_ID=run-uji python3 scripts/agent4_approval.py" in pesan
    assert "MASIH UTUH" in pesan


# ---------- guard gateway ----------

def test_gateway_aktif_ditolak_dan_tercatat(lingkungan, monkeypatch):
    monkeypatch.setattr(a4, "warn_if_gateway_polling", lambda: True)

    with pytest.raises(RuntimeError, match="Gateway OpenClaw aktif"):
        a4.run()

    assert riwayat_terakhir(lingkungan["riwayat"])["status"] == "REJECTED_GATEWAY_ACTIVE"
    assert lingkungan["dibersihkan"] == []


def test_chat_tidak_diketahui_ditolak_sebelum_mengirim(lingkungan, monkeypatch):
    monkeypatch.setattr(a4, "resolve_chat_id", lambda: None)
    terkirim = []
    monkeypatch.setattr(a4, "send_video", lambda *a, **k: terkirim.append(1) or True)

    with pytest.raises(ValueError, match="Chat pemicu tidak dapat ditentukan"):
        a4.run()

    assert terkirim == []
    assert riwayat_terakhir(lingkungan["riwayat"])["status"] == "REJECTED_CHAT_UNKNOWN"


# ---------- record_history path-agnostik ----------

def test_record_history_memakai_path_yang_diberikan(lingkungan):
    a4.record_history("UJI", video_path="/x/video.mp4", brief_path=str(lingkungan["brief"]))
    entri = riwayat_terakhir(lingkungan["riwayat"])
    assert entri["file_path"] == "/x/video.mp4"
    assert entri["judul"] == "Judul Uji"


# ---------- urutan: pindah DULU, baru publish ----------

def test_video_dipindah_SEBELUM_publish_dipanggil(lingkungan, monkeypatch):
    """Meta MENARIK video dari URL publik yang menyajikan published/.
    Mempublikasikan sebelum file pindah berarti Meta menarik dari alamat kosong."""
    monkeypatch.setattr(a4, "wait_for_reply", lambda *a, **k: "APPROVED")
    arsip = lingkungan["published"] / f"{lingkungan['run_id']}.mp4"
    terlihat = {}

    def fake_publish(video_path, caption=""):
        terlihat["path"] = video_path
        terlihat["ada_saat_publish"] = os.path.exists(video_path)
        return "https://instagram.com/reel/xyz"

    monkeypatch.setattr(a4, "publish_to_platforms", fake_publish)

    a4.run()

    assert terlihat["path"] == str(arsip), "publish harus memakai path published/"
    assert terlihat["ada_saat_publish"] is True, "file wajib sudah ada saat publish"


def test_live_url_tercatat_saat_publish_berhasil(lingkungan, monkeypatch):
    monkeypatch.setattr(a4, "wait_for_reply", lambda *a, **k: "APPROVED")
    monkeypatch.setattr(a4, "publish_to_platforms",
                        lambda v, c="": "https://instagram.com/reel/xyz")

    a4.run()

    entri = riwayat_terakhir(lingkungan["riwayat"])
    assert entri["status"] == "PUBLISHED"
    assert entri["live_url"] == "https://instagram.com/reel/xyz"


def test_publish_gagal_tetap_APPROVED_dan_file_aman(lingkungan, monkeypatch):
    """Gagal publish bukan alasan kehilangan konten yang sudah disetujui."""
    monkeypatch.setattr(a4, "wait_for_reply", lambda *a, **k: "APPROVED")
    monkeypatch.setattr(a4, "publish_to_platforms", lambda v, c="": None)

    a4.run()

    entri = riwayat_terakhir(lingkungan["riwayat"])
    assert entri["status"] == "APPROVED_AWAITING_MANUAL_UPLOAD"
    assert os.path.exists(entri["file_path"])
