"""Penyedia API: satu nama model, penyedia terpisah per layanan, JSON toleran.

Latar belakang nyata: chat dipindah ke Snifox, yang HANYA punya model chat
(tanpa Whisper dan tanpa TTS). Jadi transkripsi dan TTS harus bisa tetap di
penyedia lain, dan nama model tidak boleh lagi tertulis mati di kode.
"""

import json

import pytest

import common
import transcribe as t


# ---------- JSON toleran ----------

@pytest.mark.parametrize("teks,harap", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),           # perilaku Claude Haiku / Gemini
    ('```\n{"a": 1}\n```', {"a": 1}),
    ('Ini hasilnya: {"a": 1} semoga membantu', {"a": 1}),
    ('  \n{"a": [1, 2]}  ', {"a": [1, 2]}),
])
def test_json_berpagar_kode_tetap_terbaca(teks, harap):
    """Terukur: Claude Haiku dan Gemini membungkus jawaban dengan ```json meski
    diminta json_object, sehingga json.loads polos gagal padahal isinya benar."""
    assert common.parse_json_lenient(teks) == harap


@pytest.mark.parametrize("teks", ["", "bukan json sama sekali", "```json\nbukan json\n```"])
def test_isi_yang_memang_bukan_json_tetap_melempar(teks):
    """Yang dilonggarkan hanya pembungkusnya. Menelan isi yang salah akan
    menghasilkan brief kosong yang lolos diam-diam."""
    with pytest.raises(json.JSONDecodeError):
        common.parse_json_lenient(teks)


# ---------- penyedia per layanan ----------

def _tangkap_klien(monkeypatch):
    dibuat = {}

    class Palsu:
        def __init__(self, **kw):
            dibuat.update(kw)

    import openai
    monkeypatch.setattr(openai, "OpenAI", Palsu)
    return dibuat


def test_layanan_tanpa_penimpa_memakai_penyedia_utama(monkeypatch):
    dibuat = _tangkap_klien(monkeypatch)
    monkeypatch.setattr(common, "OPENAI_API_KEY", "kunci-utama")
    monkeypatch.setattr(common, "OPENAI_BASE_URL", "https://utama.example/v1")
    monkeypatch.delenv("TRANSCRIBE_API_KEY", raising=False)
    monkeypatch.delenv("TRANSCRIBE_BASE_URL", raising=False)

    common.make_openai_client(service="TRANSCRIBE")

    assert dibuat["api_key"] == "kunci-utama"
    assert dibuat["base_url"] == "https://utama.example/v1"


def test_transkripsi_bisa_memakai_penyedia_sendiri(monkeypatch):
    """Snifox tidak punya Whisper, jadi transkripsi harus bisa tetap di tempat lain."""
    dibuat = _tangkap_klien(monkeypatch)
    monkeypatch.setattr(common, "OPENAI_API_KEY", "kunci-chat")
    monkeypatch.setattr(common, "OPENAI_BASE_URL", "https://chat.example/v1")
    monkeypatch.setenv("TRANSCRIBE_API_KEY", "kunci-whisper")
    monkeypatch.setenv("TRANSCRIBE_BASE_URL", "https://whisper.example/v1")

    common.make_openai_client(service="TRANSCRIBE")

    assert dibuat["api_key"] == "kunci-whisper"
    assert dibuat["base_url"] == "https://whisper.example/v1"


def test_penimpa_satu_layanan_tidak_bocor_ke_chat(monkeypatch):
    dibuat = _tangkap_klien(monkeypatch)
    monkeypatch.setattr(common, "OPENAI_API_KEY", "kunci-chat")
    monkeypatch.setattr(common, "OPENAI_BASE_URL", "https://chat.example/v1")
    monkeypatch.setenv("TRANSCRIBE_API_KEY", "kunci-whisper")
    monkeypatch.setenv("TRANSCRIBE_BASE_URL", "https://whisper.example/v1")

    common.make_openai_client()          # chat biasa, tanpa service

    assert dibuat["api_key"] == "kunci-chat"
    assert dibuat["base_url"] == "https://chat.example/v1"


def test_retry_bawaan_sdk_tetap_mati_untuk_semua_layanan(monkeypatch):
    dibuat = _tangkap_klien(monkeypatch)
    common.make_openai_client(service="TTS")
    assert dibuat["max_retries"] == 0


# ---------- satu nama model ----------

def test_semua_pemanggil_memakai_llm_model_kalau_tidak_ditimpa():
    """Dulu 'gpt-4o' tertulis mati di empat tempat: pindah penyedia berarti
    mengedit kode."""
    import agent1_2_brief as brief
    import agent5_insight as insight

    assert brief.MODEL == (__import__("os").getenv("BRIEF_MODEL") or common.LLM_MODEL)
    assert insight.KEYWORD_MODEL == (__import__("os").getenv("KEYWORD_MODEL") or common.LLM_MODEL)


# ---------- alasan gagal transkripsi ----------

class _Err(Exception):
    def __init__(self, pesan, code=""):
        super().__init__(pesan)
        self.code = code


@pytest.mark.parametrize("exc,harap", [
    (_Err("Error code: 403 - {'code': 'local:insufficient_quota'}"), "kuota_habis"),
    (_Err("credit_balance_exhausted"), "kuota_habis"),
    (TimeoutError("timed out"), "timeout"),
    (_Err("Error code: 503 - model service temporarily unavailable"), "layanan_tidak_tersedia"),
    (_Err("sesuatu yang aneh"), "error_api"),
])
def test_klasifikasi_alasan_gagal(exc, harap):
    """Penyebabnya menentukan tindakan user: saldo habis berarti isi ulang,
    timeout berarti coba lagi. Sebelumnya semuanya sama-sama jadi baris [warn]
    yang tidak pernah dilihat siapa pun."""
    assert t.alasan_gagal(exc) == harap


def test_setiap_kode_alasan_punya_teks_untuk_user():
    for kode in ("tanpa_audio", "terlalu_panjang", "gagal_ekstrak", "tanpa_ucapan",
                 "kuota_habis", "timeout", "layanan_tidak_tersedia", "akses_ditolak",
                 "error_api", "tidak_selesai"):
        assert t.ALASAN_TEKS[kode]


def test_laporan_memisahkan_kuota_habis_dari_tanpa_ucapan(monkeypatch, tmp_path):
    """Inti perbaikannya: 'kuota habis' dan 'tidak ada ucapan' tampak sama di
    video (sama-sama tanpa subtitle) tapi tindak lanjutnya berlawanan."""
    monkeypatch.setattr(t, "has_audio", lambda p: True)
    monkeypatch.setattr(t, "media_duration", lambda p: 5.0)
    monkeypatch.setattr(t, "OPENAI_API_KEY", "kunci-palsu")
    monkeypatch.setattr(t, "TRANSCRIBE_ENABLED", True)

    def extract(src, dst):
        open(dst, "w").write(src[-5])
        return dst

    monkeypatch.setattr(t, "extract_audio", extract)

    def transkrip(path, *, durasi=0.0, vocab_prompt=None):
        nomor = open(path).read()
        if nomor == "0":
            return {"text": "ada ucapan", "segments": [], "words": []}
        if nomor == "1":
            return {"text": "", "segments": [], "words": []}
        raise _Err("Error code: 403 - local:insufficient_quota")

    monkeypatch.setattr(t, "transcribe_file_detailed", transkrip)

    hasil, gagal = t.transcribe_assets_report(
        [str(tmp_path / f"klip{i}.mp4") for i in range(3)])

    assert list(hasil) == ["klip0.mp4"]
    assert gagal == {"klip1.mp4": "tanpa_ucapan", "klip2.mp4": "kuota_habis"}


# ---------- alasan sampai ke user ----------

def test_caption_menyebut_penyebab_subtitle_hilang(monkeypatch, tmp_path):
    """Yang dulu terlihat user hanya 'subtitle hilang'. Sekarang: kenapa."""
    import run_and_deliver as rd

    brief = {"judul": "Uji", "hashtags": [],
             "transcript_coverage": {
                 "ditranskrip": 6, "total_bahan": 10,
                 "tanpa_subtitle": ["a", "b", "c", "d"],
                 "alasan": {"a": "kuota_habis", "b": "kuota_habis", "c": "kuota_habis",
                            "d": "tanpa_ucapan"}}}
    terkirim = {}
    monkeypatch.setattr(rd, "read_json", lambda *a, **k: brief)
    monkeypatch.setattr(rd, "ringkasan_biaya", lambda run_id: "")
    monkeypatch.setattr(rd, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(rd, "draft_video_path_for_run", lambda r: str(tmp_path / "v.mp4"))
    monkeypatch.setattr(rd, "draft_thumb_path_for_run", lambda r: str(tmp_path / "v.jpg"))
    monkeypatch.setattr(rd, "send_video",
                        lambda c, p, *, chat_id, thumb_path=None: terkirim.setdefault("caption", c) or True)

    rd.deliver_plugin("run1", "123")

    assert "4 dari 10" in terkirim["caption"]
    assert "saldo/kuota API habis (3)" in terkirim["caption"]
    assert "tidak ada ucapan terdeteksi (1)" in terkirim["caption"]
    # penyebab terbanyak tampil lebih dulu
    assert terkirim["caption"].index("saldo/kuota") < terkirim["caption"].index("tidak ada ucapan")
