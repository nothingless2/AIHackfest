"""Skill search_trends + penegakan pilihan tren di Fase 1.

Tidak ada panggilan jaringan nyata: _get di-monkeypatch. Yang diuji adalah
perilaku parsing dan — yang terpenting — bahwa LLM TIDAK BISA mengarang tren.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "skills", "search_trends"))

import json


import agent1_2_brief as brief
import fetch_trends

RSS = b"""<?xml version="1.0"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>tren satu</title><ht:approx_traffic>2000+</ht:approx_traffic>
  <ht:news_item><ht:news_item_title>Berita A</ht:news_item_title></ht:news_item>
  <ht:news_item><ht:news_item_title>Berita B</ht:news_item_title></ht:news_item>
</item>
<item><title>tren dua</title><ht:approx_traffic>100+</ht:approx_traffic></item>
</channel></rss>"""


# ---------- Google Trends RSS ----------

def test_rss_diurai_jadi_item(monkeypatch):
    monkeypatch.setattr(fetch_trends, "_get", lambda url, **k: RSS)
    items = fetch_trends.fetch_google_trends()

    assert [i["term"] for i in items] == ["tren satu", "tren dua"]
    assert items[0]["metric"] == "2000+"
    assert items[0]["context"] == ["Berita A", "Berita B"]
    assert all(i["source"] == "GOOGLE_TRENDS_ID" for i in items)


def test_jaringan_mati_mengembalikan_kosong_bukan_melempar(monkeypatch):
    monkeypatch.setattr(fetch_trends, "_get", lambda url, **k: None)
    assert fetch_trends.fetch_google_trends() == []


def test_rss_rusak_tidak_melempar(monkeypatch):
    monkeypatch.setattr(fetch_trends, "_get", lambda url, **k: b"<rss><bukan")
    assert fetch_trends.fetch_google_trends() == []


# ---------- YouTube ----------

def test_youtube_tanpa_key_mengembalikan_kosong(monkeypatch):
    monkeypatch.setattr(fetch_trends, "YOUTUBE_API_KEY", None)
    assert fetch_trends.search_youtube("apa saja") == []


def test_youtube_memakai_order_relevance_bukan_viewcount(monkeypatch):
    """Diuji langsung dgn API nyata: viewCount mengembalikan video viral global
    yang cuma cocok longgar. relevance memberi konten yang benar-benar nyambung."""
    dilihat = {}

    def fake_get(url, **k):
        dilihat["url"] = url
        return json.dumps({"items": []}).encode()

    monkeypatch.setattr(fetch_trends, "_get", fake_get)
    fetch_trends.search_youtube("kolaborasi", api_key="kunci-palsu")

    assert "order=relevance" in dilihat["url"]
    assert "order=viewCount" not in dilihat["url"]
    assert "regionCode=ID" in dilihat["url"]


def test_youtube_entitas_html_di_judul_dibersihkan(monkeypatch):
    payload = {"items": [{"id": {"videoId": "abc"},
                          "snippet": {"title": "Let&#39;s Go &amp; Grow"}}]}
    monkeypatch.setattr(
        fetch_trends, "_get",
        lambda url, **k: json.dumps(payload).encode() if "search?" in url else None,
    )
    hasil = fetch_trends.search_youtube("x", api_key="k")
    assert hasil[0]["term"] == "Let's Go & Grow"


def test_youtube_balasan_bukan_json_tidak_melempar(monkeypatch):
    monkeypatch.setattr(fetch_trends, "_get", lambda url, **k: b"<html>error</html>")
    assert fetch_trends.search_youtube("x", api_key="k") == []


# ---------- gather ----------

def test_gather_semua_sumber_gagal_tetap_struktur_sama(monkeypatch):
    monkeypatch.setattr(fetch_trends, "_get", lambda url, **k: None)
    hasil = fetch_trends.gather(["a"])

    assert hasil["items"] == []
    assert hasil["sources_ok"] == []
    assert hasil["sources_failed"], "kegagalan harus tercatat, bukan disembunyikan"
    assert "fetched_at" in hasil


# ---------- PENEGAKAN: LLM tidak bisa mengarang tren ----------

KOLAM = {"items": [
    {"source": "YOUTUBE_ID_30D", "term": "Video Nyata A", "metric": "100 views",
     "url": "https://y/1", "query": "q"},
    {"source": "GOOGLE_TRENDS_ID", "term": "tren nyata b", "metric": "50+",
     "url": None, "query": None},
]}


def test_memilih_nomor_valid_mengambil_item_asli():
    assert brief.pilih_tren(KOLAM, 1)["term"] == "tren nyata b"


def test_null_berarti_tidak_memakai_tren():
    assert brief.pilih_tren(KOLAM, None) is None


def test_nomor_di_luar_jangkauan_ditolak(capsys):
    """LLM mengarang nomor -> diperlakukan sbg 'tidak memilih', bukan crash."""
    assert brief.pilih_tren(KOLAM, 99) is None
    assert "di luar jangkauan" in capsys.readouterr().out


def test_nomor_negatif_ditolak():
    assert brief.pilih_tren(KOLAM, -1) is None


def test_bukan_angka_ditolak(capsys):
    """LLM membalas nama tren karangan alih-alih nomor."""
    assert brief.pilih_tren(KOLAM, "Tren Yang Saya Karang") is None
    assert "bukan angka" in capsys.readouterr().out


def test_kolam_kosong_selalu_tidak_memilih():
    assert brief.pilih_tren({"items": []}, 0) is None
    assert brief.pilih_tren(None, 0) is None


def test_catatan_tren_kosong_menyuruh_set_null():
    note, items = brief.build_trend_note({"items": []})
    assert "TIDAK ADA data tren" in note
    assert "null" in note
    assert items == []


def test_catatan_tren_bernomor_dan_menyertakan_metrik():
    note, items = brief.build_trend_note(KOLAM)
    assert note.startswith("0. (YOUTUBE_ID_30D) Video Nyata A [100 views]")
    assert "1. (GOOGLE_TRENDS_ID) tren nyata b" in note
    assert len(items) == 2


def test_prompt_melarang_menulis_tren_di_luar_daftar():
    p = brief.build_prompt(["a.jpg"], None, jumlah_gambar=1, pool=KOLAM)
    assert "HANYA boleh memilih dari daftar" in p
    assert "DILARANG menulis nama tren lain" in p
    assert "Video Nyata A" in p, "kolam nyata harus ikut dikirim"


# ---------- Google News (pengganti DuckDuckGo) ----------

NEWS_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Kolaborasi UMKM &amp; Bank - Media A</title>
  <link>https://contoh/1</link><pubDate>Wed, 17 Sep 2026 01:00:00 GMT</pubDate></item>
<item><title>Warung Go Digital - Media B</title>
  <link>https://contoh/2</link><pubDate>Tue, 16 Sep 2026 02:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_news_diurai_dan_bisa_ditanya_per_topik(monkeypatch):
    dilihat = {}

    def fake_get(url, **k):
        dilihat["url"] = url
        return NEWS_RSS

    monkeypatch.setattr(fetch_trends, "_get", fake_get)
    hasil = fetch_trends.search_news("umkm digital", limit=5)

    assert [h["term"] for h in hasil] == [
        "Kolaborasi UMKM & Bank - Media A",  # entitas HTML sudah dibersihkan
        "Warung Go Digital - Media B",
    ]
    assert all(h["source"] == "GOOGLE_NEWS_ID" for h in hasil)
    assert hasil[0]["url"] == "https://contoh/1"
    assert "umkm+digital" in dilihat["url"] or "umkm%20digital" in dilihat["url"]
    assert "hl=id" in dilihat["url"]


def test_news_gagal_tidak_melempar(monkeypatch):
    monkeypatch.setattr(fetch_trends, "_get", lambda url, **k: None)
    assert fetch_trends.search_news("apa saja") == []


def test_gather_mencatat_news_sebagai_sumber(monkeypatch):
    monkeypatch.setattr(fetch_trends, "search_youtube", lambda q, **k: [])
    monkeypatch.setattr(
        fetch_trends, "search_news",
        lambda q, **k: [{"source": "GOOGLE_NEWS_ID", "term": "x", "query": q,
                          "url": None, "metric": None}],
    )
    monkeypatch.setattr(fetch_trends, "fetch_google_trends", lambda **k: [])

    hasil = fetch_trends.gather(["topik"])

    assert "GOOGLE_NEWS_ID" in hasil["sources_ok"]
    assert "YOUTUBE_ID_30D" in " ".join(hasil["sources_failed"])


# ---------- konteks user tidak lagi dibuang ----------

def test_prompt_memakai_permintaan_user_dan_memenangkannya_atas_tren():
    p = brief.build_prompt(
        ["a.jpg"], None, jumlah_gambar=1, pool=KOLAM,
        konteks="konten promo untuk pemilik warung kecil",
    )
    assert "konten promo untuk pemilik warung kecil" in p
    assert "WAJIB melayani" in p
    assert "MENANGKAN" in p, "permintaan user harus mengalahkan tren yang bertentangan"


def test_prompt_tanpa_konteks_melarang_mengarang_maksud_user():
    p = brief.build_prompt(["a.jpg"], None, jumlah_gambar=1, pool=KOLAM)
    assert "tidak ada" in p
    assert "jangan mengarang maksud user" in p
