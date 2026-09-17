"""Agent 5 (ContentInsight): laporkan performa konten yang sudah tayang.

Aturan: laporkan apa adanya. Kalau data performa asli belum tersedia, katakan
TIDAK ADA DATA -- jangan mengarang angka.

Versi sebelumnya memanggil random.randint()/random.uniform() lalu menuliskannya
sebagai "metrics" dengan penanda ESTIMATED_PLACEHOLDER. Hasilnya: walau belum ada
satu pun post terbit (post_id "no_published_post_yet"), sistem bisa melaporkan
"HIGH_PERFORMING, 13.899 views". Angka dadu itu lalu diumpankan ke prompt Agent
1&2 sebagai "data performa konten sebelumnya, pertimbangkan apa yang berhasil" --
sehingga LLM mengarang tren "berdasarkan" kebisingan. Seluruh jalur itu dihapus.
"""

import json
import os
import sys

from common import (
    ensure_dirs,
    log_error,
    notify,
    now_iso,
    PERFORMANCE_PATH,
    PUBLISH_HISTORY_PATH,
    read_json,
    resolve_assets,
    resolve_chat_id,
    write_json,
    PROJECT_ROOT,
    TREND_POOL_PATH,
    OPENAI_API_KEY,
)

sys.path.insert(0, os.path.join(PROJECT_ROOT, "skills", "search_trends"))
from fetch_trends import gather  # noqa: E402

sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
from vision import build_image_parts  # noqa: E402

KEYWORD_MODEL = os.getenv("KEYWORD_MODEL", "gpt-4o")
KEYWORD_MAX_ASSETS = int(os.getenv("KEYWORD_MAX_ASSETS", "3"))


def fetch_real_analytics(platform, publish_id):
    """TODO: ganti dengan API analitik asli, misalnya:
    - Instagram/Meta Graph API: GET /{ig-media-id}/insights
    - TikTok Display API: POST /v2/video/query/
    - YouTube Data API v3: videos.list?part=statistics
    Butuh kredensial platform yang belum ada di .env. Return None = belum tersedia."""
    return None


def latest_published_post():
    history = read_json(PUBLISH_HISTORY_PATH, []) or []
    published = [p for p in history if p.get("status") == "PUBLISHED"]
    if not published:
        return None
    return sorted(published, key=lambda p: p["timestamp"])[-1]


def classify(engagement_rate):
    if engagement_rate >= 6.0:
        return "HIGH_PERFORMING"
    if engagement_rate >= 2.5:
        return "MODERATE_PERFORMING"
    return "LOW_PERFORMING"


def _asset_paths():
    """Bahan run ini, kalau memang disebutkan. Fase 0 non-kritis, jadi kegagalan
    apa pun di sini hanya berarti 'tidak tahu topiknya'."""
    daftar = (os.getenv("CONTENT_FACTORY_ASSETS") or "").strip()
    if not daftar:
        return []
    names = [n.strip() for n in daftar.split(",") if n.strip()]
    try:
        return resolve_assets(names)
    except Exception as e:
        print(f"[warn] Agent 5: bahan tidak bisa dipakai untuk menebak topik: {e}")
        return []


def derive_queries(asset_paths):
    """Turunkan kata kunci pencarian DARI ISI bahan, bukan dari nama file.

    Tanpa ini tren yang diambil cuma tren harian umum -- sepak bola dan berita --
    yang hampir tidak pernah berhubungan dengan materi user. Panggilan ini sengaja
    kecil: maksimal 3 gambar, hanya diminta mengembalikan kata kunci.
    """
    if not asset_paths or not OPENAI_API_KEY:
        return []

    parts = build_image_parts(asset_paths, max_assets=KEYWORD_MAX_ASSETS)
    if not parts:
        return []

    from openai import OpenAI

    prompt = (
        "Lihat gambar-gambar ini. Sebutkan 2-3 kata kunci pencarian Bahasa Indonesia "
        "yang menggambarkan TOPIK materi ini, untuk mencari video sejenis di YouTube. "
        "Deskripsikan hanya yang benar-benar terlihat. Jangan menambah topik yang tidak ada.\n"
        'Balas HANYA JSON: {"queries": ["kata kunci 1", "kata kunci 2"]}'
    )
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=KEYWORD_MODEL,
            messages=[{"role": "user",
                       "content": [{"type": "text", "text": prompt}, *parts]}],
            response_format={"type": "json_object"},
        )
        queries = json.loads(resp.choices[0].message.content).get("queries") or []
    except Exception as e:
        print(f"[warn] Agent 5: gagal menurunkan kata kunci dari bahan: {e}")
        return []

    bersih = [str(q).strip() for q in queries if str(q).strip()][:3]
    if bersih:
        print(f"[info] Agent 5: kata kunci dari isi bahan -> {bersih}")
    return bersih


def run():
    ensure_dirs()

    post = latest_published_post()
    metrics = fetch_real_analytics(post["platform"], post["publish_id"]) if post else None

    if metrics:
        summary = {
            "report_date": now_iso(),
            "post_id": post["publish_id"],
            "data_source": "REAL_API",
            "metrics": metrics,
            "performance_status": classify(float(metrics["engagement_rate"])),
        }
        pesan = (
            f"laporan performa siap (sumber: REAL_API, "
            f"engagement {metrics['engagement_rate']}%, status {summary['performance_status']})."
        )
    else:
        # Tidak ada data = katakan tidak ada data. Bukan menebak, bukan mengarang.
        alasan = (
            "belum ada post berstatus PUBLISHED"
            if not post
            else "API analitik platform belum dikonfigurasi"
        )
        summary = {
            "report_date": now_iso(),
            "post_id": post["publish_id"] if post else None,
            "data_source": "NO_DATA",
            "reason": alasan,
            "metrics": None,
            "performance_status": "UNKNOWN",
        }
        pesan = f"belum ada data performa ({alasan}). Tidak ada angka yang bisa dilaporkan."

    write_json(PERFORMANCE_PATH, summary)

    # Sinyal tren NYATA untuk diserahkan ke TrendAnalysts (Fase 1).
    # Sesuai agents/05_contentinsight.md: Agent 5 yang menyiapkan bahan strategi
    # bagi TRENDANALYSTS. Modul ini hanya MELAPORKAN apa yang dikembalikan server;
    # penilaian relevansi dilakukan Fase 1 dan diverifikasi kode.
    queries = derive_queries(_asset_paths())
    pool = gather(queries)
    write_json(TREND_POOL_PATH, pool)
    print(
        f"[info] Agent 5: {len(pool['items'])} sinyal tren dari {pool['sources_ok'] or 'tidak ada sumber'}"
        + (f", gagal: {pool['sources_failed']}" if pool["sources_failed"] else "")
    )

    notify("contentinsight", pesan, chat_id=resolve_chat_id())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as e:
        log_error("Agent 5 (insight) failure", e)
        notify("contentinsight", f"gagal menyusun laporan performa — {e}", chat_id=resolve_chat_id())
        sys.exit(1)
