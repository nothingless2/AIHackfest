"""Routing per-chat: tiap user hanya menerima output di chat-nya sendiri.

Tidak pernah menyentuh Telegram asli — send_video/notify diganti perekam.
"""

import os

import pytest

import run_and_deliver
from run_lock import FileLockBusyError

CHAT_A = "111111111"
CHAT_B = "222222222"


@pytest.fixture
def izinkan_ab(monkeypatch):
    """Lolos allowlist untuk kedua chat uji (conftest mengosongkannya by default)."""
    monkeypatch.setenv("ALLOWED_CHAT_IDS", f"{CHAT_A},{CHAT_B}")


@pytest.fixture
def rekam(monkeypatch, tmp_path):
    """Rekam setiap panggilan send_video/notify beserta chat tujuannya."""
    calls = {"send_video": [], "notify": []}

    def fake_send_video(caption, video_path, *, chat_id, thumb_path=None):
        calls["send_video"].append(chat_id)
        return True

    def fake_notify(agent_key, message, *, chat_id, silent_fail=True):
        calls["notify"].append(chat_id)
        return True

    monkeypatch.setattr(run_and_deliver, "send_video", fake_send_video)
    monkeypatch.setattr(run_and_deliver, "notify", fake_notify)
    monkeypatch.setattr(run_and_deliver, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(run_and_deliver, "install_signal_handlers", lambda: None)
    monkeypatch.setattr(run_and_deliver, "ensure_dirs", lambda: None)
    monkeypatch.setattr(run_and_deliver, "read_json", lambda *a, **k: {})
    return calls


def test_video_hanya_dikirim_ke_chat_pemicu(rekam):
    run_and_deliver.deliver_plugin("run-a", CHAT_A)
    assert rekam["send_video"] == [CHAT_A]
    assert CHAT_B not in rekam["send_video"]


def test_dua_run_berbeda_tidak_saling_bocor(rekam):
    run_and_deliver.deliver_plugin("run-a", CHAT_A)
    run_and_deliver.deliver_plugin("run-b", CHAT_B)
    assert rekam["send_video"] == [CHAT_A, CHAT_B]


def test_chat_tidak_diketahui_ditolak_tanpa_mengirim_apa_pun(rekam, monkeypatch):
    """Gagal-tertutup: tanpa chat pemicu, TIDAK ada yang dikirim ke siapa pun,
    dan tahap render (yang memakai kredit GPT-4o) tidak pernah dijalankan."""
    monkeypatch.setenv("CONTENT_FACTORY_CHAT_ID", "")

    dipanggil = []
    monkeypatch.setattr(
        run_and_deliver, "run_core_stages_locked",
        lambda *a, **k: dipanggil.append(True),
    )
    monkeypatch.setattr(run_and_deliver, "log_error", lambda *a, **k: None)

    rc = run_and_deliver.main()

    assert rc == 1
    assert rekam["send_video"] == [], "tidak boleh ada video terkirim"
    assert rekam["notify"] == [], "tidak boleh ada notifikasi terkirim"
    assert dipanggil == [], "render tidak boleh dijalankan"


def test_lock_sibuk_pesan_ke_peminta_bukan_ke_pemegang(rekam, monkeypatch, izinkan_ab):
    """Render B ditolak karena A memegang lock -> pesan penolakan harus ke B."""
    monkeypatch.setenv("CONTENT_FACTORY_CHAT_ID", CHAT_B)

    def busy(*a, **k):
        raise FileLockBusyError(12.0, {"holder_id": "run-milik-A"})

    monkeypatch.setattr(run_and_deliver, "run_core_stages_locked", busy)

    rc = run_and_deliver.main()

    assert rc == os.EX_TEMPFAIL
    assert rekam["notify"] == [CHAT_B], "penolakan harus ke peminta (B), bukan pemegang (A)"
    assert CHAT_A not in rekam["notify"]
    assert rekam["send_video"] == []


def test_pesan_penolakan_tidak_membocorkan_isi_run_pemegang(monkeypatch, izinkan_ab):
    """Pesan ke peminta tidak boleh menyebut judul/isi run milik orang lain."""
    monkeypatch.setenv("CONTENT_FACTORY_CHAT_ID", CHAT_B)
    pesan = []
    monkeypatch.setattr(
        run_and_deliver, "notify",
        lambda k, m, *, chat_id, silent_fail=True: pesan.append(m),
    )
    monkeypatch.setattr(run_and_deliver, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(run_and_deliver, "install_signal_handlers", lambda: None)
    monkeypatch.setattr(run_and_deliver, "ensure_dirs", lambda: None)

    def busy(*a, **k):
        raise FileLockBusyError(12.0, {"holder_id": "run-A", "judul": "Rahasia Milik A"})

    monkeypatch.setattr(run_and_deliver, "run_core_stages_locked", busy)
    run_and_deliver.main()

    assert len(pesan) == 1
    assert "Rahasia Milik A" not in pesan[0]
    assert "run-A" not in pesan[0]


def test_wait_for_reply_abaikan_approve_dari_chat_lain(monkeypatch):
    """APPROVE dari chat lain tidak boleh menyetujui konten milik chat pemicu."""
    import agent4_approval as a4

    updates = [
        {"update_id": 1, "message": {"chat": {"id": int(CHAT_B)}, "text": "APPROVE"}},
        {"update_id": 2, "message": {"chat": {"id": int(CHAT_A)}, "text": "REVISI"}},
    ]

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"result": updates}

    monkeypatch.setattr(a4.requests, "get", lambda *a, **k: FakeResp())
    monkeypatch.setattr(a4, "TELEGRAM_BOT_TOKEN", "token-palsu", raising=False)

    # Chat pemicu = A. APPROVE dari B harus dilewati, REVISI dari A yang dipakai.
    assert a4.wait_for_reply(0, chat_id=CHAT_A) == "REJECTED"
