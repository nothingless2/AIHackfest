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


def test_basis_kosong_tidak_ditebak(monkeypatch):
    """Tanpa base_url tidak ada yang bisa diukur -> menyerah terang-terangan, bukan menebak.
    (Sebelum 30 Sep jawabannya "bukan_openrouter"; sekarang basis non-OpenRouter yang ADA
    diarahkan ke router lokal, lihat test_cek_mengarah_ke_router_bukan_menyerah.)"""
    monkeypatch.setattr(common, "OPENAI_BASE_URL", None)
    assert ck.cek(SEKARANG, ambil=_status(1))["alasan"] == "basis_kosong"


def test_key_tidak_bocor_ke_keluaran():
    assert "sk-palsu" not in str(ck.cek(SEKARANG, ambil=_status(1)))


# --- Router lokal (30 Sep): tidak ada /key, angka hanya ada di badan error 429 ---------------

def _bungkus(dalam):
    """Seperti router: error penyedia jadi STRING di dalam JSON (jadi ter-escape)."""
    import json
    return json.dumps({"error": {"message": dalam}})


BADAN_429 = _bungkus(
    '[openrouter/nvidia/nemotron-3-super-120b-a12b:free] [429]: {"error":{"message":'
    '"Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model '
    'requests per day","code":429,"metadata":{"headers":{"X-RateLimit-Limit":"50",'
    '"X-RateLimit-Remaining":"0","X-RateLimit-Reset":"1790812800000"},'
    '"limit_source":"openrouter_free_tier_daily"}},"user_id":"user_x"} (reset after 2s)')

BADAN_LAIN = _bungkus('[openrouter/x:free] [404]: {"error":{"message":"This model is not available"}}')


@pytest.fixture
def _router(monkeypatch):
    monkeypatch.setattr(common, "OPENAI_BASE_URL", "http://127.0.0.1:20128/v1")
    monkeypatch.setattr(common, "OPENAI_API_KEY", "sk-palsu")
    monkeypatch.setattr(common, "LLM_MODEL", "penyedia/utama")
    monkeypatch.setenv("LLM_FALLBACK", "")


def test_buka_bungkus_membuka_satu_lapisan():
    """Tanpa ini raw_decode kena '{\\"error\\"' dan angka batas tak pernah terbaca."""
    assert ck.buka_bungkus(BADAN_429).startswith("[openrouter/nvidia/")
    assert ck.buka_bungkus("bukan json") == "bukan json"


def test_baca_batas_dapat_angka_dari_badan_bersarang():
    b = ck.baca_batas(BADAN_429)
    assert b["batas"] == 50 and b["sisa"] == 0
    assert b["sumber"] == "openrouter_free_tier_daily" and b["reset_ms"] == 1790812800000


@pytest.mark.parametrize("badan", [BADAN_LAIN, "", None, "{}", "Connection refused"])
def test_baca_batas_tidak_menebak(badan):
    """Kontrol negatif: error yang BUKAN soal batas tidak boleh jadi angka karangan."""
    b = ck.baca_batas(badan)
    assert b is None or ("batas" not in b and "sisa" not in b)


def _probe(hidup_utama, hidup_gratis=False, catat=None):
    def p(model, timeout=60):
        if catat is not None:
            catat.append(model)
        hidup = hidup_utama if model == "penyedia/utama" else hidup_gratis
        return {"hidup": hidup, "pesan": "" if hidup else BADAN_429}
    return p


def test_utama_hidup_jatah_gratis_tidak_disentuh(_router):
    """Probe yang berhasil memakai 1 dari 50, jadi jangan diperiksa tanpa alasan."""
    catat = []
    h = ck.cek_router(SEKARANG, probe=_probe(True, catat=catat))
    assert h["ok"] and h["bisa_jalan"] and h["utama_hidup"]
    assert catat == ["penyedia/utama"]
    assert "kuota_gratis" not in h


def test_utama_mati_jatah_gratis_habis_maka_tidak_bisa_jalan(_router):
    """Kontrol positif: pemeriksa BISA melaporkan habis, dengan angka dan jam reset."""
    h = ck.cek_router(SEKARANG, probe=_probe(False, False))
    assert h["ok"] and not h["bisa_jalan"]
    assert h["kuota_gratis"]["habis"] and h["kuota_gratis"]["sisa"] == 0
    assert h["reset"] == "01/10/2026 07:00 WIB"   # dari X-RateLimit-Reset, bukan dugaan


def test_utama_mati_tapi_jatah_gratis_ada_maka_masih_bisa_jalan(_router):
    h = ck.cek_router(SEKARANG, probe=_probe(False, True))
    assert h["ok"] and h["bisa_jalan"] and h["kuota_gratis"]["habis"] is False


def test_cek_mengarah_ke_router_bukan_menyerah(_router, monkeypatch):
    """Sebelum 30 Sep cek() selalu balas 'bukan_openrouter' di router -- agent jadi buta."""
    monkeypatch.setattr(ck, "probe_model", _probe(True))
    h = ck.cek(SEKARANG, cek_gratis=False)
    assert h["lewat"] == "router_lokal" and h.get("alasan") != "bukan_openrouter"


def test_key_tidak_bocor_dari_jalur_router(_router):
    assert "sk-palsu" not in str(ck.cek_router(SEKARANG, probe=_probe(False, False)))
