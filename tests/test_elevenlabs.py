"""Voice-over ElevenLabs + cadangan edge-tts. Jaringan TIDAK disentuh: `_elevenlabs_http`
dan edge-tts diganti. Kuota asli user tidak boleh terpakai oleh test."""

import asyncio
import io
import json
import urllib.error

import pytest

import auto_render as ar


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def eleven(monkeypatch):
    monkeypatch.setattr(ar, "TTS_PROVIDER", "elevenlabs")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "kunci-uji")
    panggilan = []

    def http(url, data=None):
        panggilan.append((url, json.loads(data) if data else None))
        if "subscription" in url:
            return _Resp(json.dumps({"character_limit": 10000, "character_count": 100}).encode())
        return _Resp(b"ID3-mp3-palsu")

    monkeypatch.setattr(ar, "_elevenlabs_http", http)
    edge = []

    async def edge_palsu(text, out):
        edge.append(text)
        open(out, "wb").write(b"edge")

    monkeypatch.setattr(ar, "_voice_edge", edge_palsu)
    monkeypatch.setattr(ar, "_catat_pemakaian_tts", lambda *a, **k: None)
    return panggilan, edge


def test_elevenlabs_dipakai_dengan_model_dan_bahasa_indonesia(eleven, tmp_path):
    panggilan, edge = eleven
    out = tmp_path / "v.mp3"
    asyncio.run(ar.generate_voice("Halo semua", str(out)))
    assert out.read_bytes() == b"ID3-mp3-palsu" and edge == []
    url, badan = [p for p in panggilan if "text-to-speech" in p[0]][0]
    assert badan == {"text": "Halo semua", "model_id": ar.ELEVENLABS_MODEL, "language_code": "id"}
    assert ar.TTS_CATATAN == {"mesin": "elevenlabs", "suara": "Bella"}


@pytest.mark.parametrize("kode", [401, 402, 403])
def test_kegagalan_permanen_langsung_ke_edge_tanpa_mengulang(eleven, monkeypatch, tmp_path, kode):
    panggilan, edge = eleven
    tts = []

    def http(url, data=None):
        if "subscription" in url:
            return _Resp(b'{"character_limit": 10000, "character_count": 0}')
        tts.append(url)
        raise urllib.error.HTTPError(url, kode, "x", {}, io.BytesIO(b'{"detail":"paid_plan_required"}'))

    monkeypatch.setattr(ar, "_elevenlabs_http", http)
    asyncio.run(ar.generate_voice("Halo", str(tmp_path / "v.mp3")))
    assert len(tts) == 1, "kegagalan permanen tidak boleh diulang"
    assert edge == ["Halo"]
    assert ar.TTS_CATATAN["cadangan"] is True and str(kode) in ar.TTS_CATATAN["alasan"]
    assert ar.TTS_CATATAN["mesin"] == "edge-tts", "hasil harus jujur menyebut mesin yang dipakai"


def test_kuota_tidak_cukup_tidak_mengirim_naskah(eleven, monkeypatch, tmp_path):
    panggilan, edge = eleven

    def http(url, data=None):
        panggilan.append(url)
        return _Resp(b'{"character_limit": 10000, "character_count": 9995}')

    monkeypatch.setattr(ar, "_elevenlabs_http", http)
    asyncio.run(ar.generate_voice("naskah yang lebih dari lima karakter", str(tmp_path / "v.mp3")))
    assert not any("text-to-speech" in u for u in panggilan)
    assert edge and "kuota" in ar.TTS_CATATAN["alasan"]


def test_tanpa_key_jatuh_ke_edge(eleven, monkeypatch, tmp_path):
    _, edge = eleven
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    asyncio.run(ar.generate_voice("Halo", str(tmp_path / "v.mp3")))
    assert edge == ["Halo"] and "ELEVENLABS_API_KEY" in ar.TTS_CATATAN["alasan"]


@pytest.mark.parametrize("gender,persona,nama", [
    ("wanita", "ramah", "Bella"), ("wanita", "energik", "Matilda"), ("pria", "energik", "Liam"),
    ("pria", "ramah", "Chris"), ("pria", "profesional", "George"), ("aneh", "ramah", "Bella"),
])
def test_pemilihan_suara(monkeypatch, gender, persona, nama):
    monkeypatch.setattr(ar, "TTS_VOICE_GENDER", gender)
    monkeypatch.setattr(ar, "TTS_PERSONA", persona)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    assert ar.suara_elevenlabs()[1] == nama


def test_cadangan_edge_mengikuti_jenis_suara():
    assert ar.EDGE_VOICES == {"wanita": "id-ID-GadisNeural", "pria": "id-ID-ArdiNeural"}
