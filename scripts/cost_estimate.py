"""Estimasi biaya dari pemakaian yang BENAR-BENAR terjadi.

Pembagian yang dijaga ketat:
- Jumlah token adalah DATA NYATA, dibaca dari `response.usage` milik API.
- Harga per token adalah KONFIGURASI di config/pricing.json, bukan hardcode.
- Hasil perkaliannya adalah ESTIMASI, dan selalu dilabeli begitu.

Model atau mesin TTS yang tidak ada di pricing.json TIDAK ditebak: biayanya
dilaporkan None. Angka biaya yang dikarang lebih berbahaya daripada tidak ada
angka sama sekali -- persis kesalahan yang dulu dilakukan estimate_metrics().

Tidak ada fungsi di modul ini yang boleh melempar exception ke pemanggil:
pelacakan biaya adalah pengamatan, dan tidak boleh menggagalkan pipeline.
"""

import json
import os

from common import PROJECT_ROOT

PRICING_PATH = os.path.join(PROJECT_ROOT, "config", "pricing.json")


def load_pricing(path=None):
    """Baca tabel harga. File hilang/rusak -> {} + peringatan, bukan exception."""
    berkas = path or PRICING_PATH
    try:
        with open(berkas, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        print(f"[warn] cost: {berkas} tidak ada — biaya tidak diestimasi.")
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] cost: {berkas} tidak terbaca ({e}) — biaya tidak diestimasi.")
    return {}


def estimate_llm_cost(model, prompt_tokens, completion_tokens, *, pricing=None):
    """USD untuk satu panggilan LLM, atau None kalau modelnya tidak dikenal."""
    tabel = (pricing if pricing is not None else load_pricing()).get("llm") or {}
    harga = tabel.get(model)
    if not harga:
        return None
    try:
        masuk = (float(prompt_tokens or 0) / 1_000_000) * float(harga["input_per_1m"])
        keluar = (float(completion_tokens or 0) / 1_000_000) * float(harga["output_per_1m"])
    except (KeyError, TypeError, ValueError):
        print(f"[warn] cost: entri harga '{model}' tidak lengkap/valid.")
        return None
    return round(masuk + keluar, 6)


def estimate_tts_cost(engine, chars, *, pricing=None):
    """USD untuk satu panggilan TTS, atau None kalau mesinnya tidak dikenal."""
    tabel = (pricing if pricing is not None else load_pricing()).get("tts") or {}
    harga = tabel.get(engine)
    if not harga:
        return None
    try:
        return round((float(chars or 0) / 1000) * float(harga["per_1k_chars"]), 6)
    except (KeyError, TypeError, ValueError):
        print(f"[warn] cost: entri harga TTS '{engine}' tidak lengkap/valid.")
        return None


def estimate_transcribe_cost(model, audio_seconds, *, pricing=None):
    """USD untuk satu transkripsi, atau None kalau modelnya tidak dikenal.

    Ditagih per MENIT audio, bukan per token — jadi rumusnya berbeda dari
    estimate_llm_cost() dan tidak boleh disatukan dengannya.
    """
    tabel = (pricing if pricing is not None else load_pricing()).get("transcribe") or {}
    harga = tabel.get(model)
    if not harga:
        return None
    try:
        return round((float(audio_seconds or 0) / 60.0) * float(harga["per_minute"]), 6)
    except (KeyError, TypeError, ValueError):
        print(f"[warn] cost: entri harga transkripsi '{model}' tidak lengkap/valid.")
        return None
