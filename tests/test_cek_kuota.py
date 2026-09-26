"""cek_kuota: status kuota NYATA untuk agent, supaya ia tidak menjawab "kuota habis" dari riwayat
obrolan (26 Sep). Endpoint /key dipalsukan -- tanpa jaringan."""

import datetime as dt

import pytest

import cek_kuota as ck
import common

WIB = ck.WIB
SEKARANG = dt.datetime(2026, 9, 26, 10, 8, tzinfo=WIB)


@pytest.fixture(autouse=True)
def _openrouter(monkeypatch):
    monkeypatch.setattr(common, "OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(common, "OPENAI_API_KEY", "sk-palsu")


def _status(used, limit=50):
    return lambda basis, kunci: {"data": {"free_model_daily_requests":
                                          {"used": used, "limit": limit, "remaining": limit - used}}}


def test_kuota_tersisa_bisa_jalan():
    h = ck.cek(SEKARANG, ambil=_status(1))
    assert h["ok"] and h["bisa_jalan"] and h["kuota_gratis"] == {"terpakai": 1, "batas": 50, "sisa": 49}


def test_kuota_habis_tidak_bisa_jalan():
    """Kontrol positif: pemeriksa BISA melaporkan habis."""
    h = ck.cek(SEKARANG, ambil=_status(50))
    assert h["ok"] and not h["bisa_jalan"] and h["kuota_gratis"]["sisa"] == 0


@pytest.mark.parametrize("jam, reset", [
    (dt.datetime(2026, 9, 26, 10, 8, tzinfo=WIB), "27/09/2026 07:00 WIB"),
    (dt.datetime(2026, 9, 26, 6, 59, tzinfo=WIB), "26/09/2026 07:00 WIB"),   # sebelum reset hari ini
    (dt.datetime(2026, 9, 25, 23, 40, tzinfo=WIB), "26/09/2026 07:00 WIB"),
])
def test_reset_07_wib_berikutnya(jam, reset):
    assert ck.cek(jam, ambil=_status(50))["reset"] == reset


def test_cek_gagal_bukan_kuota_habis():
    def rusak(basis, kunci):
        raise OSError("jaringan putus")
    h = ck.cek(SEKARANG, ambil=rusak)
    assert not h["ok"] and h["alasan"] == "cek_gagal" and "bisa_jalan" not in h


def test_format_asing_tidak_ditebak():
    h = ck.cek(SEKARANG, ambil=lambda b, k: {"data": {}})
    assert not h["ok"] and h["alasan"] == "format_tak_dikenal"


def test_bukan_openrouter(monkeypatch):
    monkeypatch.setattr(common, "OPENAI_BASE_URL", None)
    assert ck.cek(SEKARANG, ambil=_status(1))["alasan"] == "bukan_openrouter"


def test_key_tidak_bocor_ke_keluaran():
    assert "sk-palsu" not in str(ck.cek(SEKARANG, ambil=_status(1)))
