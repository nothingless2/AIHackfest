"""Penyedia voice-over dan pemilihan font.

TTS: gpt-4o-mini-tts (suara nova, gaya diarahkan lewat `instructions`)
menggantikan edge-tts yang terdengar kaku. edge-tts DIPERTAHANKAN sebagai
cadangan -- ia gratis dan tetap jalan saat kredit habis.
"""

import asyncio
import subprocess

import pytest

import auto_render as ar
import cost_estimate as ce


# ---------- pemilihan penyedia ----------

def test_penyedia_default_openai():
    assert ar.TTS_PROVIDER == "openai"
    assert ar.TTS_VOICE == "nova"
    assert ar.TTS_MODEL.startswith("gpt-4o")


def test_instruksi_gaya_ada_isinya():
    """Itu yang membedakan gpt-4o-mini-tts dari TTS lama yang membaca datar."""
    assert len(ar.TTS_INSTRUCTIONS) > 40
    assert "natural" in ar.TTS_INSTRUCTIONS.lower()


def test_openai_gagal_jatuh_ke_edge(tmp_path, monkeypatch):
    """Kredit habis atau relay bermasalah tidak boleh menggagalkan render kalau
    masih ada jalur gratis yang bekerja."""
    jejak = []

    async def openai_meledak(text, out):
        jejak.append("openai")
        raise RuntimeError("relay mati")

    async def edge_ok(text, out):
        jejak.append("edge")
        open(out, "wb").write(b"audio")

    monkeypatch.setattr(ar, "TTS_PROVIDER", "openai")
    monkeypatch.setattr(ar, "_voice_openai", openai_meledak)
    monkeypatch.setattr(ar, "_voice_edge", edge_ok)
    monkeypatch.setattr(ar, "_catat_pemakaian_tts", lambda *a, **k: None)

    asyncio.run(ar.generate_voice("halo", str(tmp_path / "v.mp3")))
    assert jejak == ["openai", "edge"], "harus mencoba OpenAI dulu, lalu jatuh ke edge"


def test_penyedia_edge_tidak_menyentuh_openai(tmp_path, monkeypatch):
    jejak = []

    async def openai_(text, out):
        jejak.append("openai")

    async def edge_(text, out):
        jejak.append("edge")

    monkeypatch.setattr(ar, "TTS_PROVIDER", "edge")
    monkeypatch.setattr(ar, "_voice_openai", openai_)
    monkeypatch.setattr(ar, "_voice_edge", edge_)
    monkeypatch.setattr(ar, "_catat_pemakaian_tts", lambda *a, **k: None)

    asyncio.run(ar.generate_voice("halo", str(tmp_path / "v.mp3")))
    assert jejak == ["edge"]


def test_mesin_yang_dipakai_ikut_tercatat(tmp_path, monkeypatch):
    """Biaya berbeda jauh antar mesin; mencatat mesin yang salah membuat laporan
    biaya ikut salah."""
    dicatat = []

    async def edge_(text, out):
        pass

    monkeypatch.setattr(ar, "TTS_PROVIDER", "edge")
    monkeypatch.setattr(ar, "_voice_edge", edge_)
    monkeypatch.setattr(ar, "_catat_pemakaian_tts",
                        lambda t, mesin="edge-tts": dicatat.append(mesin))

    asyncio.run(ar.generate_voice("halo", str(tmp_path / "v.mp3")))
    assert dicatat == ["edge-tts"]


# ---------- biaya ----------

def test_harga_tts_openai_terdaftar():
    tabel = ce.load_pricing().get("tts") or {}
    for m in ("gpt-4o-mini-tts", "tts-1-hd", "edge-tts"):
        assert m in tabel, m


def test_model_tts_yang_dipakai_punya_harga():
    """Kalau TTS_MODEL diganti tanpa memperbarui pricing.json, biaya diam-diam
    berhenti terhitung."""
    assert ar.TTS_MODEL in (ce.load_pricing().get("tts") or {})


def test_biaya_openai_lebih_mahal_dari_edge():
    h = ce.load_pricing()
    assert ce.estimate_tts_cost("gpt-4o-mini-tts", 1000, pricing=h) > 0
    assert ce.estimate_tts_cost("edge-tts", 1000, pricing=h) == 0.0


# ---------- font ----------

def test_font_diselesaikan_dari_nama_keluarga():
    """Path font berbeda antar distribusi; nama keluarga stabil."""
    p = ar.resolve_font("DejaVu Sans")
    assert p.endswith((".ttf", ".otf"))


def test_font_terpasang_benar_benar_ditemukan():
    hasil = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True)
    for nama in ("Inter", "Roboto"):
        if nama in hasil.stdout:
            assert nama.lower() in ar.resolve_font(nama).lower()


def test_font_tidak_terpasang_tidak_menggagalkan_render(capsys):
    """fc-match selalu mengembalikan font terdekat — jadi salah ketik memberi
    hasil yang berbeda, bukan crash. Tapi user harus DIBERITAHU."""
    p = ar.resolve_font("FontYangJelasTidakAda")
    assert p.endswith((".ttf", ".otf"))
    assert "tidak terpasang" in capsys.readouterr().out


def test_font_path_dipakai_di_filter_drawtext():
    f = ar.build_drawtext_chain([{"start": 0, "end": 2, "text": "halo"}], 1920, 1080)[0]
    assert "fontfile=" in f
    assert f.split("fontfile=")[1].split(":")[0].endswith((".ttf", ".otf"))
