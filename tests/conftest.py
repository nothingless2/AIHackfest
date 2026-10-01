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
# Motion graphic juga lewat Chromium: dimatikan; tests/test_motion_render.py menyalakannya.
os.environ["MOTION_GRAPHIC"] = "mati"
# Pemeriksa mutu menambah beberapa pass ffmpeg per render; tests/test_qa_video.py menyalakannya.
os.environ["QA_VIDEO"] = "0"
# Montase ketukan menganalisis musik; tests/test_montase.py menyalakannya.
os.environ["MONTASE"] = "0"
# Storyboard draf membuka Chromium & TTS; tests/test_storyboard.py menyalakannya.
os.environ["STORYBOARD"] = "0"
# Pemotongan bagian goyang menganalisis tiap video; test lama memakai sumber sintetis dan
# tidak menguji ini. Test-nya sendiri menyalakan (tests/test_visual_quality.py).
os.environ["VISUAL_CUT"] = "0"
# TTS & key ElevenLabs asli di .env tidak boleh mengubah test (dan tidak boleh memakai
# kuota): test TTS menyetelnya sendiri.
os.environ["TTS_PROVIDER"] = ""
# Rantai model cadangan dari .env asli tidak boleh mengubah perilaku test (test_rantai_model.py
# menyetelnya sendiri).
os.environ["LLM_FALLBACK"] = ""
# Cache voice-over menulis ke workspace/state asli; tes cache mengarahkannya ke tmp_path sendiri.
os.environ["TTS_CACHE"] = "0"
# Pembersih suara mengubah audio (gerbang/EQ/kompresor): tes lama membandingkan audio dengan sumbernya.
# Dinyalakan di tests/test_suara.py sendiri.
os.environ["BERSIH_SUARA"] = "0"
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

    assert common.resolve_chat_id() is None, "chat tujuan harus kosong selama test"
    yield


@pytest.fixture(autouse=True)
def _cache_mood_musik_di_tmp(monkeypatch, tmp_path):
    """Cache analisis mood musik ditulis ke workspace/state/ -- di test harus ke tmp
    (aturan #6): pick_track dengan label mood bisa memicu analisis + penulisan cache."""
    import music_mood

    monkeypatch.setattr(music_mood, "_cache_path", lambda: str(tmp_path / "music_mood_cache.json"))
    import visual_quality

    monkeypatch.setattr(visual_quality, "_cache_path", lambda: str(tmp_path / "visual_quality_cache.json"))
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


@pytest.fixture(autouse=True)
def _raw_asli_tidak_tersentuh(monkeypatch, tmp_path):
    """hermes_render menyalin lampiran ke RAW_DIR. Tes yang lupa mengalihkannya menulis ke
    workspace/raw ASLI (terukur 27 Sep: `*_k.mp4` dari test_broll muncul tiap suite dijalankan).
    Jaring pengaman: semua tes memakai folder raw sementara kecuali menimpanya sendiri."""
    import hermes_render
    monkeypatch.setattr(hermes_render, "RAW_DIR", str(tmp_path / "raw_uji"))
    yield


@pytest.fixture(autouse=True)
def _status_render_di_tmp(monkeypatch, tmp_path):
    """auto_render.write_status menulis workspace/state/render_status.json ASLI saat tes render
    (terukur 27 Sep). Diarahkan ke tmp_path; tes yang membacanya memakai ar.STATUS_PATH."""
    import auto_render
    monkeypatch.setattr(auto_render, "STATUS_PATH", str(tmp_path / "render_status.json"))
    yield


@pytest.fixture(autouse=True)
def _profil_gaya_di_tmp(monkeypatch, tmp_path):
    """Profil & cache pratinjau gaya ada di workspace/state/ -- di test harus ke tmp (aturan #6):
    hermes_render.main() membaca profil chat di SETIAP run, dan `gaya.py pakai` menulisnya."""
    import gaya
    monkeypatch.setattr(gaya, "PROFIL_DIR", str(tmp_path / "profil"))
    monkeypatch.setattr(gaya, "PRATINJAU_DIR", str(tmp_path / "pratinjau_gaya"))
    yield


@pytest.fixture(autouse=True)
def _catatan_revisi_di_tmp(monkeypatch, tmp_path):
    """hermes_render._catat_revisi menulis ke workspace/state/revisi/. Terukur 1 Okt: setiap kali
    tests/test_draft.py dijalankan, 7 catatan "DM with Uji" masuk ke state ASLI (187 menumpuk dari
    210 berkas). Semua tes ke tmp kecuali menimpanya sendiri (test_revisi.py)."""
    import revisi
    monkeypatch.setattr(revisi, "REVISI_DIR", str(tmp_path / "revisi_state"))
    yield
