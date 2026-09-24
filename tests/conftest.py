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

# Transkripsi di test SELALU lewat jalur API yang di-stub. Default produksi ("auto")
# memilih Whisper LOKAL kalau venv-nya ada, dan itu berarti test benar-benar
# memuat model 460 MB lalu men-transkrip audio (suite melambat jadi 75 detik) --
# menyentuh layanan nyata, melanggar aturan #6 CLAUDE.md.
os.environ["TRANSCRIBE_PROVIDER"] = "api"

# Key Pexels asli (dimuat dari .env) tidak boleh mengubah hasil test: ia menentukan apakah
# tawaran B-roll muncul di inspect_media. Test yang butuh key mengisinya sendiri.
os.environ["PEXELS_API_KEY"] = ""
# Teks animasi (Remotion) membuka Chromium: lambat dan tidak dibutuhkan test drawtext.
# Test Remotion menyalakannya sendiri (tests/test_overlay_remotion.py).
os.environ["TEXT_ANIMATION"] = "none"
# Pemotongan bagian goyang menganalisis tiap video; test lama memakai sumber sintetis dan
# tidak menguji ini. Test-nya sendiri menyalakan (tests/test_visual_quality.py).
os.environ["VISUAL_CUT"] = "0"
# TTS & key ElevenLabs asli di .env tidak boleh mengubah test (dan tidak boleh memakai
# kuota): test TTS menyetelnya sendiri.
os.environ["TTS_PROVIDER"] = ""
os.environ["ELEVENLABS_API_KEY"] = ""
os.environ["TTS_VOICE_GENDER"] = ""


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
def _whisper_lokal_tidak_boleh_jalan(monkeypatch):
    """Jaring pengaman kedua: worker Whisper asli tidak boleh bisa dijalankan dari
    test. Test yang menguji jalur lokal harus menunjuk worker PALSU secara eksplisit
    (transcribe.LOCAL_PYTHON / LOCAL_WORKER), tidak pernah yang asli."""
    import transcribe

    monkeypatch.setattr(transcribe, "TRANSCRIBE_PROVIDER", "api", raising=False)
    monkeypatch.setattr(transcribe, "LOCAL_PYTHON", "/tidak/ada/python-whisper", raising=False)
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


@pytest.fixture(autouse=True)
def _cache_mood_musik_di_tmp(monkeypatch, tmp_path):
    """Cache analisis mood musik ditulis ke workspace/state/ -- di test harus ke tmp
    (aturan #6): pick_track dengan label mood bisa memicu analisis + penulisan cache."""
    import music_mood

    monkeypatch.setattr(music_mood, "_cache_path", lambda: str(tmp_path / "music_mood_cache.json"))
    yield


@pytest.fixture(autouse=True)
def _lingkungan_dipulihkan():
    """Titik masuk seperti hermes_render._apply_env menulis ke os.environ GLOBAL (wajar di
    proses sekali-jalan). Di test itu bocor ke test berikutnya -- terukur: BROLL=1 tersisa
    dan menggagalkan test_duration. Snapshot + pulihkan tiap test."""
    import os as _os
    sebelum = dict(_os.environ)
    yield
    _os.environ.clear()
    _os.environ.update(sebelum)
