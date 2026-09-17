"""Publikasi Instagram Reels lewat Graph API.

Tidak ada panggilan Meta sungguhan: _panggil di-monkeypatch.
"""

import pytest

import publish


@pytest.fixture
def siap(monkeypatch):
    monkeypatch.setattr(publish, "IG_USER_ID", "ig-123")
    monkeypatch.setattr(publish, "IG_ACCESS_TOKEN", "token-xyz")
    monkeypatch.setattr(publish, "PUBLIC_MEDIA_BASE_URL", "https://contoh.test/media")
    monkeypatch.setattr(publish, "CONTAINER_POLL_INTERVAL", 0)


# ---------- prasyarat diperiksa SEBELUM mencoba ----------

def test_kredensial_kurang_terdeteksi_awal(monkeypatch):
    monkeypatch.setattr(publish, "IG_USER_ID", None)
    monkeypatch.setattr(publish, "IG_ACCESS_TOKEN", None)
    monkeypatch.setattr(publish, "PUBLIC_MEDIA_BASE_URL", "")

    ok, alasan = publish.instagram_siap()
    assert ok is False
    for wajib in ("IG_USER_ID", "IG_ACCESS_TOKEN", "PUBLIC_MEDIA_BASE_URL"):
        assert wajib in alasan, "pesan harus menyebut apa yang kurang"


def test_url_publik_wajib_meski_kredensial_lengkap(monkeypatch):
    """Meta MENARIK video dari URL; tanpa hosting publik, Graph API pasti gagal."""
    monkeypatch.setattr(publish, "IG_USER_ID", "ig-123")
    monkeypatch.setattr(publish, "IG_ACCESS_TOKEN", "t")
    monkeypatch.setattr(publish, "PUBLIC_MEDIA_BASE_URL", "")

    ok, alasan = publish.instagram_siap()
    assert ok is False
    assert "PUBLIC_MEDIA_BASE_URL" in alasan


def test_publish_tanpa_kredensial_mengembalikan_None_bukan_melempar(monkeypatch, capsys):
    """Konten sudah disetujui user dan sudah diarsipkan; gagal publish tidak
    boleh menggagalkan alur approval."""
    monkeypatch.setattr(publish, "IG_USER_ID", None)

    assert publish.publish("/x/v.mp4", "cap") is None
    assert "dilewati" in capsys.readouterr().out


def test_url_publik_dibentuk_dari_nama_file(siap):
    assert publish.public_url_for("/a/b/run-1.mp4") == "https://contoh.test/media/run-1.mp4"


# ---------- alur sukses ----------

def test_alur_lengkap_empat_langkah(siap, monkeypatch):
    dipanggil = []

    def fake(path, *, params=None, data=None):
        dipanggil.append(path)
        if path.endswith("/media"):
            return {"id": "creation-1"}
        if path == "creation-1":
            return {"status_code": "FINISHED"}
        if path.endswith("/media_publish"):
            return {"id": "media-9"}
        return {"permalink": "https://instagram.com/reel/abc"}

    monkeypatch.setattr(publish, "_panggil", fake)

    assert publish.publish("/a/run-1.mp4", "caption") == "https://instagram.com/reel/abc"
    assert dipanggil == ["ig-123/media", "creation-1", "ig-123/media_publish", "media-9"]


def test_video_url_yang_dikirim_ke_meta_benar(siap, monkeypatch):
    terkirim = {}

    def fake(path, *, params=None, data=None):
        if path.endswith("/media"):
            terkirim.update(data)
            return {"id": "c1"}
        if path == "c1":
            return {"status_code": "FINISHED"}
        if path.endswith("/media_publish"):
            return {"id": "m1"}
        return {"permalink": "https://x/y"}

    monkeypatch.setattr(publish, "_panggil", fake)
    publish.publish("/a/run-7.mp4", "cap")

    assert terkirim["video_url"] == "https://contoh.test/media/run-7.mp4"
    assert terkirim["media_type"] == "REELS"
    assert terkirim["caption"] == "cap"


def test_menunggu_container_selesai_sebelum_publish(siap, monkeypatch):
    """Publish sebelum FINISHED akan ditolak Meta."""
    status = iter(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    urutan = []

    def fake(path, *, params=None, data=None):
        urutan.append(path)
        if path.endswith("/media"):
            return {"id": "c1"}
        if path == "c1":
            return {"status_code": next(status)}
        if path.endswith("/media_publish"):
            return {"id": "m1"}
        return {"permalink": "https://x/y"}

    monkeypatch.setattr(publish, "_panggil", fake)
    publish.publish("/a/v.mp4")

    assert urutan.count("c1") == 3, "harus polling sampai FINISHED"
    assert urutan.index("ig-123/media_publish") > urutan.index("c1")


def test_permalink_gagal_diambil_tetap_dianggap_terbit(siap, monkeypatch):
    """Sudah TERBIT; gagal mengambil permalink bukan alasan menyatakan gagal."""
    def fake(path, *, params=None, data=None):
        if path.endswith("/media"):
            return {"id": "c1"}
        if path == "c1":
            return {"status_code": "FINISHED"}
        if path.endswith("/media_publish"):
            return {"id": "m1"}
        raise RuntimeError("permalink tidak terbaca")

    monkeypatch.setattr(publish, "_panggil", fake)
    assert publish.publish("/a/v.mp4") == "https://www.instagram.com/p/m1/"


# ---------- kegagalan ----------

def test_container_ERROR_dilaporkan_jelas(siap, monkeypatch, capsys):
    def fake(path, *, params=None, data=None):
        if path.endswith("/media"):
            return {"id": "c1"}
        return {"status_code": "ERROR", "status": "format tidak didukung"}

    monkeypatch.setattr(publish, "_panggil", fake)

    assert publish.publish("/a/v.mp4") is None
    keluaran = capsys.readouterr().out
    assert "ERROR" in keluaran and "format tidak didukung" in keluaran


def test_container_tidak_pernah_selesai_menyerah(siap, monkeypatch, capsys):
    monkeypatch.setattr(publish, "CONTAINER_POLL_TIMEOUT", 0)

    def fake(path, *, params=None, data=None):
        if path.endswith("/media"):
            return {"id": "c1"}
        return {"status_code": "IN_PROGRESS"}

    monkeypatch.setattr(publish, "_panggil", fake)
    assert publish.publish("/a/v.mp4") is None
    assert "belum selesai memproses" in capsys.readouterr().out


def test_meta_tidak_mengembalikan_creation_id(siap, monkeypatch, capsys):
    monkeypatch.setattr(publish, "_panggil", lambda p, **k: {"tanpa": "id"})
    assert publish.publish("/a/v.mp4") is None
    assert "creation_id" in capsys.readouterr().out


def test_exception_tak_terduga_tetap_None(siap, monkeypatch, capsys):
    def meledak(path, **k):
        raise ZeroDivisionError("bug")

    monkeypatch.setattr(publish, "_panggil", meledak)
    assert publish.publish("/a/v.mp4") is None
    assert "tak terduga" in capsys.readouterr().out
