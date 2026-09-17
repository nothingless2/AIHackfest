"""Retry helper: hanya kegagalan sementara, backoff berjitter, Retry-After dibatasi."""

import asyncio
import random

import pytest

import common
import retry


class Sementara(Exception):
    pass


class Permanen(Exception):
    pass


def sementara_saja(exc):
    return isinstance(exc, Sementara)


@pytest.fixture(autouse=True)
def tanpa_tidur(monkeypatch):
    """Jangan benar-benar tidur; rekam jedanya supaya bisa diperiksa."""
    jeda = []
    monkeypatch.setattr(retry.time, "sleep", lambda d: jeda.append(d))

    async def fake_asleep(d):
        jeda.append(d)

    monkeypatch.setattr(retry.asyncio, "sleep", fake_asleep)
    return jeda


# ---------- perilaku dasar ----------

def test_sukses_langsung_tanpa_retry(tanpa_tidur):
    assert retry.with_retry(lambda: "ok", is_retriable=sementara_saja) == "ok"
    assert tanpa_tidur == []


def test_gagal_dua_kali_lalu_sukses(tanpa_tidur):
    n = {"i": 0}

    def fn():
        n["i"] += 1
        if n["i"] < 3:
            raise Sementara("belum")
        return "akhirnya"

    assert retry.with_retry(fn, is_retriable=sementara_saja) == "akhirnya"
    assert n["i"] == 3
    assert len(tanpa_tidur) == 2, "2 kali ulang = 2 jeda"


def test_kegagalan_permanen_gagal_langsung_tanpa_menunggu(tanpa_tidur):
    n = {"i": 0}

    def fn():
        n["i"] += 1
        raise Permanen("kunci salah")

    with pytest.raises(Permanen):
        retry.with_retry(fn, is_retriable=sementara_saja)

    assert n["i"] == 1, "tidak boleh diulang sama sekali"
    assert tanpa_tidur == []


def test_exception_asli_diteruskan_bukan_dibungkus(tanpa_tidur):
    def fn():
        raise Sementara("pesan asli")

    with pytest.raises(Sementara, match="pesan asli"):
        retry.with_retry(fn, is_retriable=sementara_saja)


def test_percobaan_habis_melempar_exception_terakhir(tanpa_tidur):
    n = {"i": 0}

    def fn():
        n["i"] += 1
        raise Sementara("terus gagal")

    with pytest.raises(Sementara):
        retry.with_retry(fn, is_retriable=sementara_saja, max_attempts=3)
    assert n["i"] == 3


# ---------- backoff ----------

def test_backoff_eksponensial_dengan_jitter():
    random.seed(42)
    d1 = retry.compute_delay(1, base_delay=1.0)
    d2 = retry.compute_delay(2, base_delay=1.0)
    d3 = retry.compute_delay(3, base_delay=1.0)

    assert 1.0 <= d1 <= 2.0
    assert 2.0 <= d2 <= 3.0
    assert 4.0 <= d3 <= 5.0


def test_jitter_membuat_jeda_tidak_identik():
    """Tanpa jitter, beberapa proses yang gagal bersamaan akan mencoba ulang
    pada detik yang sama persis dan saling menabrak lagi."""
    nilai = {retry.compute_delay(1) for _ in range(20)}
    assert len(nilai) > 1


# ---------- Retry-After ----------

def test_retry_after_server_dihormati(tanpa_tidur):
    n = {"i": 0}

    def fn():
        n["i"] += 1
        if n["i"] == 1:
            raise Sementara("429")
        return "ok"

    retry.with_retry(fn, is_retriable=sementara_saja,
                     extract_retry_after=lambda e: 7.0)
    assert tanpa_tidur == [7.0]


def test_retry_after_dibatasi_max(tanpa_tidur):
    """Server boleh minta tunggu 15 menit; tahap pemanggil punya timeout sendiri
    dan akan dibunuh paksa lebih dulu."""
    def fn():
        raise Sementara("429")

    with pytest.raises(Sementara):
        retry.with_retry(fn, is_retriable=sementara_saja, max_attempts=2,
                         extract_retry_after=lambda e: 900.0, max_retry_after=30.0)
    assert tanpa_tidur == [30.0]


def test_extract_retry_after_error_jatuh_ke_backoff(tanpa_tidur):
    def fn():
        raise Sementara("x")

    def peledak(e):
        raise RuntimeError("header aneh")

    with pytest.raises(Sementara):
        retry.with_retry(fn, is_retriable=sementara_saja, max_attempts=2,
                         extract_retry_after=peledak)
    assert len(tanpa_tidur) == 1, "tetap menunggu pakai backoff biasa"


# ---------- versi async ----------

def test_async_retry_sampai_sukses(tanpa_tidur):
    n = {"i": 0}

    async def fn():
        n["i"] += 1
        if n["i"] < 2:
            raise Sementara("belum")
        return "ok"

    assert asyncio.run(retry.with_retry_async(fn, is_retriable=sementara_saja)) == "ok"
    assert n["i"] == 2


def test_async_timeout_per_percobaan_bukan_total(tanpa_tidur):
    """Satu percobaan yang menggantung tidak boleh menahan seluruh tahap."""
    n = {"i": 0}

    async def menggantung():
        n["i"] += 1
        # Event yang tidak pernah di-set. asyncio.sleep tidak dipakai di sini
        # karena fixture men-patch-nya, sehingga "menggantung" justru selesai.
        await asyncio.Event().wait()

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(retry.with_retry_async(
            menggantung, is_retriable=lambda e: isinstance(e, asyncio.TimeoutError),
            max_attempts=2, attempt_timeout=0.05))
    assert n["i"] == 2, "tiap percobaan dibatasi sendiri-sendiri"


# ---------- klasifikasi OpenAI ----------

def test_klasifikasi_openai_urutan_permanen_sebelum_5xx():
    """AuthenticationError adalah TURUNAN APIStatusError. Kalau aturan 5xx
    diperiksa lebih dulu, kunci API salah akan diulang tiga kali sia-sia."""
    import openai

    for kelas in (openai.RateLimitError, openai.InternalServerError,
                  openai.APIConnectionError, openai.APITimeoutError):
        assert common.openai_is_retriable(kelas.__new__(kelas)) is True, kelas.__name__

    for kelas in (openai.AuthenticationError, openai.BadRequestError,
                  openai.PermissionDeniedError, openai.NotFoundError,
                  openai.UnprocessableEntityError):
        assert common.openai_is_retriable(kelas.__new__(kelas)) is False, kelas.__name__


def test_klasifikasi_openai_exception_asing_tidak_diulang():
    assert common.openai_is_retriable(ValueError("bukan error openai")) is False


def test_retry_after_openai_dibaca_dari_header():
    class Resp:
        headers = {"Retry-After": "12"}

    exc = Exception()
    exc.response = Resp()
    assert common.openai_retry_after(exc) == 12.0


def test_retry_after_openai_header_aneh_jadi_none():
    class Resp:
        headers = {"Retry-After": "sebentar lagi"}

    exc = Exception()
    exc.response = Resp()
    assert common.openai_retry_after(exc) is None
    assert common.openai_retry_after(Exception()) is None
