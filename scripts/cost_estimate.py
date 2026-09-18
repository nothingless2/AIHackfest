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


# --- Ringkasan biaya untuk dilaporkan ke user -------------------------------
#
# CATATAN PENTING: OpenAI TIDAK mengekspos sisa saldo lewat API. Diuji langsung
# dengan API key proyek ini: dashboard/billing/credit_grants -> 403,
# v1/dashboard/billing/subscription -> 403, v1/organization/costs -> 403
# (yang terakhir butuh Admin key, dan itu pun hanya memberi PEMAKAIAN, bukan
# SISA). Jadi yang bisa dilaporkan adalah biaya dari catatan kita sendiri plus
# budget yang ditetapkan user -- bukan sisa credit sebenarnya.

MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "0") or 0)


def _baca_biaya(sejak=None, run_id=None):
    """Jumlahkan cost_usd dari run_log. Return (total, jumlah_tanpa_harga)."""
    from common import RUN_LOG_PATH

    total, tanpa_harga = 0.0, 0
    if not os.path.exists(RUN_LOG_PATH):
        return total, tanpa_harga
    try:
        with open(RUN_LOG_PATH, encoding="utf-8") as f:
            for baris in f:
                baris = baris.strip()
                if not baris:
                    continue
                try:
                    e = json.loads(baris)
                except json.JSONDecodeError:
                    continue
                if e.get("event") not in ("llm_call", "tts_call", "transcribe_call"):
                    continue
                if run_id and e.get("run_id") != run_id:
                    continue
                if sejak and (e.get("ts") or "")[:10] < sejak:
                    continue
                biaya = e.get("cost_usd")
                if biaya is None:
                    tanpa_harga += 1
                else:
                    total += float(biaya)
    except OSError:
        pass
    return round(total, 6), tanpa_harga


def run_cost(run_id):
    """Biaya satu run (USD), dan berapa panggilan yang tidak punya harga."""
    return _baca_biaya(run_id=run_id)


def month_to_date_cost():
    """Biaya bulan berjalan (USD)."""
    from datetime import datetime, timezone

    awal_bulan = datetime.now(timezone.utc).strftime("%Y-%m-01")
    return _baca_biaya(sejak=awal_bulan)


def ringkasan_biaya(run_id):
    """Satu baris ringkasan untuk dikirim ke user. None kalau tidak ada data."""
    biaya, tanpa_harga = run_cost(run_id)
    bulan, _ = month_to_date_cost()
    if biaya <= 0 and bulan <= 0:
        return None

    baris = f"💰 Biaya konten ini: ${biaya:.4f} (estimasi)"
    if MONTHLY_BUDGET_USD > 0:
        persen = bulan / MONTHLY_BUDGET_USD * 100
        baris += f"\nBulan ini: ${bulan:.4f} dari budget ${MONTHLY_BUDGET_USD:.2f} ({persen:.0f}%)"
    else:
        baris += f"\nTotal bulan ini: ${bulan:.4f}"
    if tanpa_harga:
        baris += f"\n({tanpa_harga} panggilan tanpa harga di pricing.json, tidak ikut dihitung)"
    return baris
