"""Fixture bersama untuk seluruh test suite.

Dua tanggung jawab:
1. Pastikan scripts/ ada di sys.path (agar `import common` dst bisa dipakai dari tests/).
2. Pastikan test TIDAK PERNAH bisa mengirim apa pun ke Telegram.

Soal (2): `common.py` membaca env menjadi konstanta modul PADA SAAT IMPORT
(TELEGRAM_BOT_TOKEN = os.getenv(...)), dan ia juga memanggil load_dotenv() yang
akan memuat kredensial asli dari .env. Jadi mengosongkan env di dalam fixture saja
TIDAK cukup -- kalau common sudah terlanjur diimpor, konstantanya sudah berisi
token asli. Karena itu env dikosongkan di level modul conftest (dieksekusi pytest
sebelum modul test mengimpor apa pun), DAN konstanta modulnya ditimpa ulang lewat
fixture autouse sebagai jaring pengaman kedua.

load_dotenv() default-nya override=False, sehingga key yang sudah ada di
os.environ (termasuk yang bernilai string kosong) tidak akan ditimpa oleh .env.
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(_ROOT, "scripts")
RENDER_DIR = os.path.join(_ROOT, "skills", "video_generator")
for _d in (SCRIPTS_DIR, RENDER_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

# Dikosongkan SEBELUM modul test (dan common.py) diimpor. Anak proses yang di-spawn
# test juga mewarisi os.environ ini, jadi mereka ikut aman.
# ALLOWED_CHAT_IDS ikut dikosongkan supaya daftar chat ASLI di .env tidak pernah
# mempengaruhi hasil test. Kosong = gagal-tertutup, jadi test yang memang perlu
# lolos allowlist harus menyetelnya sendiri secara eksplisit.
_TELEGRAM_ENV_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "CONTENT_FACTORY_CHAT_ID",
    "ALLOWED_CHAT_IDS",
)
for _key in _TELEGRAM_ENV_KEYS:
    os.environ[_key] = ""


@pytest.fixture(autouse=True)
def _tanpa_jaringan(monkeypatch):
    """Tidak ada test yang boleh menembak jaringan sungguhan.

    Dipasang setelah ketahuan test memanggil agent5.run() tanpa mengalihkan
    TREND_POOL_PATH, sehingga ia benar-benar meminta data ke Google Trends DAN
    menimpa workspace/state/ asli. Kegagalannya sengaja berisik: lebih baik test
    gagal dengan pesan jelas daripada diam-diam bergantung pada internet.
    """
    import urllib.request

    def tolak(*a, **k):
        raise AssertionError(
            "Test mencoba mengakses jaringan. Mock-lah pemanggilannya "
            "(mis. monkeypatch fetch_trends._get atau common.chat_json)."
        )

    monkeypatch.setattr(urllib.request, "urlopen", tolak)
    try:
        import requests
        for nama in ("get", "post", "request"):
            monkeypatch.setattr(requests, nama, tolak)
    except ImportError:
        pass
    yield


@pytest.fixture(autouse=True)
def _telegram_selalu_mati(monkeypatch):
    """Jaring pengaman kedua: paksa konstanta common menjadi kosong di tiap test,
    apa pun urutan importnya. Artefak tes pernah bocor ke Telegram user -- ini
    memastikan itu tidak bisa terulang lewat jalur test."""
    for key in _TELEGRAM_ENV_KEYS:
        monkeypatch.setenv(key, "")

    import common

    monkeypatch.setattr(common, "TELEGRAM_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(common, "CLI_CHAT_ID", None, raising=False)
    assert common.resolve_chat_id() is None, "chat tujuan harus kosong selama test"
    assert not common.telegram_configured(), "Telegram harus mati selama test"
    yield
