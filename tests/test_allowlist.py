"""Allowlist chat: hanya chat terdaftar yang boleh memicu pipeline.

Sifat yang dijaga: GAGAL-TERTUTUP. Daftar kosong berarti tidak ada yang boleh,
bukan semua boleh — karena pipeline ini membakar kredit OpenAI dan mengirim
materi milik user.
"""

import os

import pytest

import common
import run_and_deliver

CHAT_BOLEH = "111111111"
CHAT_LAIN = "999999999"


# ---------- helper murni ----------

def test_daftar_kosong_menolak_semua(monkeypatch):
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "")
    assert common.chat_allowed(CHAT_BOLEH) is False
    assert common.chat_allowed(CHAT_LAIN) is False


def test_variabel_tidak_diset_menolak_semua(monkeypatch):
    monkeypatch.delenv("ALLOWED_CHAT_IDS", raising=False)
    assert common.chat_allowed(CHAT_BOLEH) is False


def test_chat_terdaftar_diizinkan(monkeypatch):
    monkeypatch.setenv("ALLOWED_CHAT_IDS", f"{CHAT_BOLEH},{CHAT_LAIN}")
    assert common.chat_allowed(CHAT_BOLEH) is True


def test_chat_tak_terdaftar_ditolak(monkeypatch):
    monkeypatch.setenv("ALLOWED_CHAT_IDS", CHAT_BOLEH)
    assert common.chat_allowed(CHAT_LAIN) is False


def test_spasi_dan_entri_kosong_diabaikan(monkeypatch):
    monkeypatch.setenv("ALLOWED_CHAT_IDS", f"  {CHAT_BOLEH} , , {CHAT_LAIN}  ,")
    assert common.chat_allowed(CHAT_BOLEH) is True
    assert common.chat_allowed(CHAT_LAIN) is True
    assert common.allowed_chat_ids() == {CHAT_BOLEH, CHAT_LAIN}


def test_chat_id_kosong_selalu_ditolak(monkeypatch):
    monkeypatch.setenv("ALLOWED_CHAT_IDS", CHAT_BOLEH)
    assert common.chat_allowed(None) is False
    assert common.chat_allowed("") is False


def test_perbandingan_sebagai_string_bukan_angka(monkeypatch):
    """chat_id bisa datang sebagai int dari sumber lain; jangan sampai meleset."""
    monkeypatch.setenv("ALLOWED_CHAT_IDS", CHAT_BOLEH)
    assert common.chat_allowed(int(CHAT_BOLEH)) is True


# ---------- integrasi: ditolak SEBELUM lock & sebelum LLM ----------

@pytest.fixture
def pipeline_terpantau(monkeypatch):
    """Pantau apakah tahap render (yang memanggil GPT-4o) sempat dijalankan."""
    jejak = {"render": [], "notify": [], "send_video": [], "events": []}

    monkeypatch.setattr(run_and_deliver, "install_signal_handlers", lambda: None)
    monkeypatch.setattr(run_and_deliver, "ensure_dirs", lambda: None)
    monkeypatch.setattr(
        run_and_deliver, "run_core_stages_locked",
        lambda *a, **k: jejak["render"].append(True),
    )
    monkeypatch.setattr(
        run_and_deliver, "notify",
        lambda k, m, *, chat_id, silent_fail=True: jejak["notify"].append((chat_id, m)),
    )
    monkeypatch.setattr(
        run_and_deliver, "send_video",
        lambda c, v, *, chat_id: jejak["send_video"].append(chat_id) or True,
    )
    monkeypatch.setattr(
        run_and_deliver, "log_event",
        lambda ev, run_id, **k: jejak["events"].append((ev, k.get("reason"))),
    )
    return jejak


def test_chat_tak_terdaftar_ditolak_sebelum_lock_dan_tanpa_llm(
    monkeypatch, pipeline_terpantau
):
    monkeypatch.setenv("ALLOWED_CHAT_IDS", CHAT_BOLEH)
    monkeypatch.setenv("CONTENT_FACTORY_CHAT_ID", CHAT_LAIN)

    rc = run_and_deliver.main()

    assert rc == os.EX_NOPERM
    assert pipeline_terpantau["render"] == [], "render/LLM tidak boleh dijalankan"
    assert pipeline_terpantau["send_video"] == [], "tidak boleh ada video terkirim"
    assert ("run_rejected", "chat_not_allowed") in pipeline_terpantau["events"]


def test_penolakan_dibalas_ke_peminta_tanpa_bocorkan_daftar(
    monkeypatch, pipeline_terpantau
):
    monkeypatch.setenv("ALLOWED_CHAT_IDS", CHAT_BOLEH)
    monkeypatch.setenv("CONTENT_FACTORY_CHAT_ID", CHAT_LAIN)

    run_and_deliver.main()

    assert len(pipeline_terpantau["notify"]) == 1
    chat_tujuan, pesan = pipeline_terpantau["notify"][0]
    assert chat_tujuan == CHAT_LAIN, "balasan ke peminta sendiri"
    assert CHAT_BOLEH not in pesan, "daftar allowlist tidak boleh bocor"
    assert "ALLOWED_CHAT_IDS" not in pesan


def test_daftar_kosong_menolak_bahkan_chat_yang_benar(monkeypatch, pipeline_terpantau):
    """Gagal-tertutup dari ujung ke ujung, bukan cuma di helper."""
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "")
    monkeypatch.setenv("CONTENT_FACTORY_CHAT_ID", CHAT_BOLEH)

    rc = run_and_deliver.main()

    assert rc == os.EX_NOPERM
    assert pipeline_terpantau["render"] == []
