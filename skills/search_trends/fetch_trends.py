"""Implementasi skill `search_trends` (lihat SKILL.md).

Mengambil sinyal tren NYATA dari sumber publik. Dipanggil dari Fase 0
(`agent5_insight.py`), lalu hasilnya diserahkan ke TrendAnalysts di Fase 1 --
sesuai peran di agents/05_contentinsight.md ("menghasilkan rekomendasi strategi
untuk siklus tren berikutnya bagi TRENDANALYSTS").

Prinsip: modul ini HANYA melaporkan apa yang benar-benar dikembalikan server.
Tidak ada penilaian, tidak ada penyimpulan, tidak ada angka yang dikarang. LLM
di Fase 1 nanti hanya boleh MEMILIH dari kolam ini atau menyatakan tidak ada
yang relevan -- dan kode yang memverifikasi pilihannya.

Sumber:
- YOUTUBE_ID_30D    : judul video berperforma tinggi utk satu topik (butuh
                      YOUTUBE_API_KEY). Bisa ditanya per topik -- ini sumber utama.
- GOOGLE_TRENDS_ID  : tren pencarian harian Indonesia (tanpa key). Kolam tetap,
                      tidak bisa ditanya per topik, jadi sering tidak relevan
                      dengan bahan user. Dipakai sebagai pelengkap.

- GOOGLE_NEWS_ID    : berita Indonesia untuk satu topik (tanpa key). Ini pengganti
                      DuckDuckGo: sama-sama bisa ditanya per topik, tapi benar-benar
                      mengembalikan hasil.

DuckDuckGo TIDAK diimplementasikan: Instant Answer API hanya melayani entitas
ensiklopedis (query konten biasa mengembalikan kosong) dan endpoint HTML-nya
membalas halaman anti-bot (202) dari server. Wikipedia full-text search juga
ditolak setelah diuji: query "kolaborasi bisnis" mengembalikan "Friedrich Engels".
Antarmuka di bawah sengaja dibuat per-sumber supaya penyedia lain mudah
ditambahkan tanpa mengubah pemanggil.
"""

import html
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
TRENDS_GEO = os.getenv("TRENDS_GEO", "ID")
TRENDS_TIMEOUT = int(os.getenv("TRENDS_TIMEOUT_SECONDS", "15"))
YOUTUBE_WINDOW_DAYS = int(os.getenv("YOUTUBE_WINDOW_DAYS", "30"))

_RSS_NS = {"ht": "https://trends.google.com/trending/rss"}


def _get(url, *, timeout=None):
    """GET sederhana. Mengembalikan bytes, atau None kalau gagal apa pun.

    Kegagalan jaringan TIDAK boleh menggagalkan pipeline: Fase 0 bersifat
    non-kritis, dan tidak punya data tren jauh lebih baik daripada mengarangnya.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "content-factory/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout or TRENDS_TIMEOUT) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        print(f"[warn] search_trends: HTTP {e.code} dari {urllib.parse.urlsplit(url).netloc}")
    except Exception as e:
        print(f"[warn] search_trends: gagal mengambil data: {type(e).__name__}: {e}")
    return None


# ---------------------------------------------------------------- YouTube

def search_youtube(query, *, limit=5, api_key=None):
    """Judul video RELEVAN dan berperforma di Indonesia untuk satu topik.

    order="relevance", BUKAN "viewCount". Diuji langsung: dengan viewCount, query
    "kolaborasi bisnis" mengembalikan video viral global yang cuma cocok longgar
    (vlog off-grid, konten Korea soal diet). Dengan relevance, hasilnya konten
    kolaborasi bisnis Indonesia yang benar-benar nyambung.

    Popularitas tetap dipakai, tapi sebagai PENGURUTAN kita sendiri setelah
    YouTube menyaring relevansi -- bukan sebagai kriteria pencarian.

    Kuota: search.list = 100 unit, videos.list = 1 unit (jatah gratis 10.000/hari).
    """
    import json

    key = api_key if api_key is not None else YOUTUBE_API_KEY
    if not key:
        return []

    after = (datetime.now(timezone.utc) - timedelta(days=YOUTUBE_WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    q = urllib.parse.urlencode({
        "part": "snippet", "q": query, "type": "video", "order": "relevance",
        "publishedAfter": after, "regionCode": TRENDS_GEO, "relevanceLanguage": "id",
        "maxResults": str(limit), "key": key,
    })
    raw = _get(f"https://www.googleapis.com/youtube/v3/search?{q}")
    if not raw:
        return []

    try:
        items = json.loads(raw).get("items", [])
    except ValueError:
        print("[warn] search_trends: balasan YouTube bukan JSON valid")
        return []

    hasil, ids = [], []
    for it in items:
        vid = (it.get("id") or {}).get("videoId")
        judul = (it.get("snippet") or {}).get("title")
        if not vid or not judul:
            continue
        ids.append(vid)
        hasil.append({
            "source": "YOUTUBE_ID_30D",
            "term": html.unescape(judul),
            "query": query,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "metric": None,
        })

    # Satu panggilan tambahan (1 unit) untuk angka view yang SEBENARNYA,
    # lalu urutkan dari yang paling banyak ditonton DI ANTARA yang relevan.
    if ids:
        q2 = urllib.parse.urlencode({"part": "statistics", "id": ",".join(ids), "key": key})
        raw2 = _get(f"https://www.googleapis.com/youtube/v3/videos?{q2}")
        if raw2:
            try:
                stats = {
                    v["id"]: int((v.get("statistics") or {}).get("viewCount") or 0)
                    for v in json.loads(raw2).get("items", [])
                }
                for h, vid in zip(hasil, ids):
                    n = stats.get(vid, 0)
                    h["views"] = n
                    if n:
                        h["metric"] = f"{n:,} views".replace(",", ".")
                hasil.sort(key=lambda h: h.get("views", 0), reverse=True)
            except (ValueError, KeyError, TypeError):
                pass  # angka view opsional; judul relevannya tetap berguna

    return hasil


# ---------------------------------------------------- Google Trends (RSS)

def fetch_google_trends(*, geo=None, limit=10):
    """Tren pencarian harian Indonesia. Tanpa API key.

    Kolam TETAP -- tidak bisa ditanya per topik. Isinya didominasi berita dan
    hiburan, jadi sering tidak berhubungan dengan bahan user. Itu bukan cacat
    sumbernya, melainkan alasan kenapa Fase 1 harus boleh bilang "tidak relevan".
    """
    wilayah = geo or TRENDS_GEO
    raw = _get(f"https://trends.google.com/trending/rss?geo={urllib.parse.quote(wilayah)}")
    if not raw:
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"[warn] search_trends: RSS Google Trends tidak bisa diurai: {e}")
        return []

    hasil = []
    for item in root.findall(".//item")[:limit]:
        term = (item.findtext("title") or "").strip()
        if not term:
            continue
        berita = [
            (n.findtext("ht:news_item_title", namespaces=_RSS_NS) or "").strip()
            for n in item.findall("ht:news_item", _RSS_NS)
        ]
        hasil.append({
            "source": "GOOGLE_TRENDS_ID",
            "term": term,
            "query": None,
            "url": None,
            "metric": (item.findtext("ht:approx_traffic", namespaces=_RSS_NS) or "").strip() or None,
            "context": [b for b in berita if b][:3],
        })
    return hasil


# ------------------------------------------------- Google News (per topik)

def search_news(query, *, limit=5, geo=None):
    """Berita Indonesia terkini untuk satu topik. Tanpa API key.

    Pengganti DuckDuckGo: sama-sama pencarian per topik, tapi mengembalikan hasil
    nyata. Berguna untuk menangkap sudut yang sedang dibicarakan media tentang
    topik bahan user -- sesuatu yang tidak tertangkap tren pencarian harian.
    """
    wilayah = geo or TRENDS_GEO
    q = urllib.parse.urlencode({
        "q": query, "hl": "id", "gl": wilayah, "ceid": f"{wilayah}:id",
    })
    raw = _get(f"https://news.google.com/rss/search?{q}")
    if not raw:
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"[warn] search_trends: RSS Google News tidak bisa diurai: {e}")
        return []

    hasil = []
    for item in root.findall(".//item")[:limit]:
        judul = (item.findtext("title") or "").strip()
        if not judul:
            continue
        hasil.append({
            "source": "GOOGLE_NEWS_ID",
            "term": html.unescape(judul),
            "query": query,
            "url": (item.findtext("link") or "").strip() or None,
            "metric": (item.findtext("pubDate") or "").strip() or None,
        })
    return hasil


# ---------------------------------------------------------------- gabungan

def gather(queries=(), *, include_daily=True, per_query=5):
    """Kumpulkan kolam tren dari semua sumber yang tersedia.

    `queries` adalah kata kunci kandidat (idealnya diturunkan dari isi bahan
    user). Tanpa queries, hanya tren harian yang diambil.

    Selalu mengembalikan struktur yang sama, termasuk saat semua sumber gagal --
    `items` kosong berarti "tidak ada sinyal", dan itu jawaban yang sah.
    """
    items, sumber_aktif, sumber_gagal = [], [], []

    for q in queries:
        for nama, fn in (("YOUTUBE_ID_30D", search_youtube), ("GOOGLE_NEWS_ID", search_news)):
            hasil = fn(q, limit=per_query)
            if hasil:
                items.extend(hasil)
                if nama not in sumber_aktif:
                    sumber_aktif.append(nama)

    if queries:
        if "YOUTUBE_ID_30D" not in sumber_aktif:
            sumber_gagal.append("YOUTUBE_ID_30D" if YOUTUBE_API_KEY else "YOUTUBE_ID_30D(tanpa_key)")
        if "GOOGLE_NEWS_ID" not in sumber_aktif:
            sumber_gagal.append("GOOGLE_NEWS_ID")

    if include_daily:
        harian = fetch_google_trends()
        if harian:
            items.extend(harian)
            sumber_aktif.append("GOOGLE_TRENDS_ID")
        else:
            sumber_gagal.append("GOOGLE_TRENDS_ID")

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "geo": TRENDS_GEO,
        "queries": list(queries),
        "sources_ok": sumber_aktif,
        "sources_failed": sumber_gagal,
        "items": items,
    }
