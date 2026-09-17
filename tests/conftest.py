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

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# Dikosongkan SEBELUM modul test (dan common.py) diimpor. Anak proses yang di-spawn
# test juga mewarisi os.environ ini, jadi mereka ikut aman.
_TELEGRAM_ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "CONTENT_FACTORY_CHAT_ID")
for _key in _TELEGRAM_ENV_KEYS:
    os.environ[_key] = ""


@pytest.fixture(autouse=True)
def _telegram_selalu_mati(monkeypatch):
    """Jaring pengaman kedua: paksa konstanta common menjadi kosong di tiap test,
    apa pun urutan importnya. Artefak tes pernah bocor ke Telegram user -- ini
    memastikan itu tidak bisa terulang lewat jalur test."""
    for key in _TELEGRAM_ENV_KEYS:
        monkeypatch.setenv(key, "")

    import common

    monkeypatch.setattr(common, "TELEGRAM_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(common, "TELEGRAM_CHAT_ID", "", raising=False)
    assert not common.telegram_configured(), "Telegram harus mati selama test"
    yield
