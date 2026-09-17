"""Kunci: sistem tidak boleh mengarang angka performa.

Sebelumnya agent5 memakai random.randint() dan bisa melaporkan "HIGH_PERFORMING,
13.899 views" padahal post_id-nya "no_published_post_yet". Test ini memastikan
jalur itu tidak bisa hidup lagi.
"""

import json

import pytest

import agent1_2_brief as brief
import agent5_insight as insight


@pytest.fixture
def paths(tmp_path, monkeypatch):
    perf = tmp_path / "performance_summary.json"
    hist = tmp_path / "publish_history.json"
    monkeypatch.setattr(insight, "PERFORMANCE_PATH", str(perf))
    monkeypatch.setattr(insight, "PUBLISH_HISTORY_PATH", str(hist))
    monkeypatch.setattr(insight, "ensure_dirs", lambda: None)
    monkeypatch.setattr(insight, "notify", lambda *a, **k: True)
    return {"perf": perf, "hist": hist}


def baca(p):
    return json.loads(p.read_text())


# ---------- agent5: jujur saat tidak ada data ----------

def test_tanpa_post_terbit_melaporkan_NO_DATA_bukan_angka(paths):
    paths["hist"].write_text("[]")

    insight.run()
    hasil = baca(paths["perf"])

    assert hasil["data_source"] == "NO_DATA"
    assert hasil["metrics"] is None, "tidak boleh ada angka yang dikarang"
    assert hasil["performance_status"] == "UNKNOWN"
    assert "belum ada post" in hasil["reason"]


def test_tidak_pernah_mengklaim_HIGH_PERFORMING_tanpa_data(paths):
    """Regresi persis yang pernah terjadi."""
    paths["hist"].write_text("[]")

    insight.run()
    hasil = baca(paths["perf"])

    assert hasil["performance_status"] != "HIGH_PERFORMING"
    assert "views" not in json.dumps(hasil)


def test_hasil_konsisten_bukan_acak(paths):
    """Dulu dua panggilan memberi angka berbeda karena random. Sekarang harus sama."""
    paths["hist"].write_text("[]")

    insight.run()
    a = baca(paths["perf"])
    insight.run()
    b = baca(paths["perf"])

    a.pop("report_date"), b.pop("report_date")
    assert a == b, "keluaran tidak boleh berubah-ubah tanpa data baru"


def test_modul_random_tidak_dipakai_lagi():
    """Penjaga struktural: kalau seseorang menghidupkan lagi angka acak,
    test ini gagal walau perilakunya belum sempat terlihat."""
    import inspect

    src = inspect.getsource(insight)
    assert "import random" not in src, "modul random tidak boleh diimpor lagi"
    assert not hasattr(insight, "estimate_metrics"), "estimate_metrics() harus tetap hilang"


def test_dengan_analitik_asli_melaporkan_REAL_API(paths, monkeypatch):
    paths["hist"].write_text(json.dumps([{
        "status": "PUBLISHED", "timestamp": "2026-01-01T00:00:00Z",
        "platform": "Instagram", "publish_id": "abc123",
    }]))
    monkeypatch.setattr(
        insight, "fetch_real_analytics",
        lambda platform, pid: {"views": 100, "likes": 5, "comments": 1,
                                "shares": 0, "engagement_rate": 6.0},
    )

    insight.run()
    hasil = baca(paths["perf"])

    assert hasil["data_source"] == "REAL_API"
    assert hasil["metrics"]["views"] == 100
    assert hasil["performance_status"] == "HIGH_PERFORMING"


# ---------- prompt: tidak menyuapkan data palsu ke LLM ----------

def test_prompt_melarang_mengarang_saat_tidak_ada_data():
    note = brief.build_performance_note(
        {"data_source": "NO_DATA", "reason": "belum ada post berstatus PUBLISHED",
         "metrics": None}
    )
    assert "TIDAK ADA data performa asli" in note
    assert "JANGAN mengarang" in note


def test_prompt_tidak_tertipu_dict_NO_DATA_yang_truthy():
    """Dict NO_DATA tetap truthy — pengecekan harus pada data_source, bukan kosong/isi."""
    note = brief.build_performance_note({"data_source": "NO_DATA", "metrics": None})
    assert "JANGAN mengarang" in note
    assert '"metrics"' not in note, "isi mentah tidak boleh bocor sbg kalau-kalau ada data"


def test_prompt_memakai_data_asli_kalau_memang_ada():
    note = brief.build_performance_note(
        {"data_source": "REAL_API", "metrics": {"engagement_rate": 4.2}}
    )
    assert "4.2" in note
    assert "JANGAN mengarang" not in note


def test_prompt_final_tidak_lagi_menyebut_closed_loop_palsu():
    p = brief.build_prompt(
        ["a.jpg"], {"data_source": "NO_DATA", "metrics": None}, jumlah_gambar=1
    )
    assert "pertimbangkan apa yang berhasil" not in p
    assert "JANGAN mengarang" in p


# ---------- prompt: tidak mengaku meneliti tren ----------

def test_prompt_menyatakan_model_tidak_tahu_tren_terkini():
    p = brief.build_prompt(["a.jpg"], None, jumlah_gambar=3)
    assert "TIDAK punya akses internet" in p
    assert "TIDAK tahu tren yang sedang ramai" in p
    assert "DILARANG mengarang statistik" in p


def test_prompt_dengan_gambar_menyuruh_model_melihat():
    p = brief.build_prompt(["a.jpg"], None, jumlah_gambar=3)
    assert "DIBERI 3 gambar" in p
    assert "WAJIB berakar pada" in p


def test_prompt_tanpa_gambar_memperingatkan_model_buta(capsys):
    """Kalau tidak ada gambar yang bisa diproses, model harus TAHU ia tidak tahu."""
    p = brief.build_prompt(["a.jpg"], None, jumlah_gambar=0)
    assert "TIDAK tahu isinya" in p
    assert "JANGAN menyebut objek" in p


def test_prompt_melarang_menyebut_yang_tidak_terlihat():
    p = brief.build_prompt(["a.jpg"], None, jumlah_gambar=2)
    assert "DILARANG menyebut objek, orang, tempat, atau aktivitas yang TIDAK terlihat" in p


def test_skema_tidak_lagi_meminta_field_karangan():
    p = brief.build_prompt(["a.jpg"], None, jumlah_gambar=2)
    assert "confidence_score" not in p, "angka keyakinan atas tren karangan"
    assert "public_sentiment" not in p, "klaim sentimen publik yang tak pernah diukur"
    assert "observed_material" in p, "model harus menyatakan apa yang benar-benar dilihat"
