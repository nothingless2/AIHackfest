"""Pelacakan biaya: token nyata, harga dari config, tidak pernah menebak.

Kesalahan yang dijaga agar tidak terulang: estimate_metrics() dulu mengarang
angka. Di sini, model yang tidak dikenal harus menghasilkan None -- bukan 0,
bukan tebakan.
"""

import json

import pytest

import cost_estimate as ce

HARGA = {
    "llm": {"model-uji": {"input_per_1m": 2.0, "output_per_1m": 10.0}},
    "tts": {"mesin-uji": {"per_1k_chars": 4.0}},
}


# ---------- estimasi LLM ----------

def test_hitungan_benar():
    # 1jt token masuk * 2.0 = 2.0 ; 0.5jt keluar * 10.0 = 5.0
    assert ce.estimate_llm_cost("model-uji", 1_000_000, 500_000, pricing=HARGA) == 7.0


def test_hitungan_kecil_dibulatkan_wajar():
    assert ce.estimate_llm_cost("model-uji", 1000, 500, pricing=HARGA) == round(
        (1000 / 1e6) * 2.0 + (500 / 1e6) * 10.0, 6)


def test_model_tak_dikenal_mengembalikan_None_bukan_nol():
    """None berarti 'tidak tahu'. Nol berarti 'gratis' -- dua hal yang sangat berbeda."""
    assert ce.estimate_llm_cost("model-asing", 1000, 500, pricing=HARGA) is None


def test_entri_harga_tidak_lengkap_mengembalikan_None(capsys):
    rusak = {"llm": {"model-uji": {"input_per_1m": 2.0}}}  # output_per_1m hilang
    assert ce.estimate_llm_cost("model-uji", 10, 10, pricing=rusak) is None
    assert "tidak lengkap" in capsys.readouterr().out


def test_entri_harga_bukan_angka_mengembalikan_None():
    rusak = {"llm": {"model-uji": {"input_per_1m": "mahal", "output_per_1m": 1}}}
    assert ce.estimate_llm_cost("model-uji", 10, 10, pricing=rusak) is None


def test_token_None_dianggap_nol():
    """usage kadang tidak lengkap; jangan meledak karenanya."""
    assert ce.estimate_llm_cost("model-uji", None, None, pricing=HARGA) == 0.0


# ---------- estimasi TTS ----------

def test_tts_hitungan_benar():
    assert ce.estimate_tts_cost("mesin-uji", 2000, pricing=HARGA) == 8.0


def test_tts_tak_dikenal_None():
    assert ce.estimate_tts_cost("mesin-asing", 2000, pricing=HARGA) is None


# ---------- memuat tabel harga ----------

def test_file_harga_hilang_tidak_melempar(tmp_path, capsys):
    assert ce.load_pricing(str(tmp_path / "tidak-ada.json")) == {}
    assert "tidak ada" in capsys.readouterr().out


def test_file_harga_rusak_tidak_melempar(tmp_path, capsys):
    p = tmp_path / "rusak.json"
    p.write_text("{ ini bukan json")
    assert ce.load_pricing(str(p)) == {}
    assert "tidak terbaca" in capsys.readouterr().out


def test_file_harga_bukan_objek_diabaikan(tmp_path):
    p = tmp_path / "list.json"
    p.write_text("[1, 2, 3]")
    assert ce.load_pricing(str(p)) == {}


def test_pipeline_tetap_jalan_walau_harga_hilang(tmp_path):
    """Biaya tidak terhitung != pipeline gagal."""
    kosong = ce.load_pricing(str(tmp_path / "nihil.json"))
    assert ce.estimate_llm_cost("gpt-4o", 100, 100, pricing=kosong) is None


# ---------- config nyata di repo ----------

def test_pricing_json_repo_valid_dan_punya_metadata():
    harga = ce.load_pricing()
    assert harga, "config/pricing.json harus terbaca"
    assert "updated_at" in harga, "harga berubah; tanggalnya wajib tercatat"
    assert harga.get("currency") == "USD"
    assert "gpt-4o" in (harga.get("llm") or {}), "model yang dipakai harus ada di tabel"


def test_model_yang_dipakai_pipeline_punya_harga():
    """Kalau MODEL di agent diganti tanpa memperbarui pricing.json, biaya diam-diam
    berhenti terhitung. Test ini yang menangkapnya."""
    import agent1_2_brief

    harga = ce.load_pricing()
    assert agent1_2_brief.MODEL in (harga.get("llm") or {}), (
        f"{agent1_2_brief.MODEL} tidak ada di config/pricing.json"
    )
