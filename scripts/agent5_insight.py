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

import sys

from common import (
    ensure_dirs,
    log_error,
    notify,
    now_iso,
    PERFORMANCE_PATH,
    PUBLISH_HISTORY_PATH,
    read_json,
    resolve_chat_id,
    write_json,
)


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
    notify("contentinsight", pesan, chat_id=resolve_chat_id())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as e:
        log_error("Agent 5 (insight) failure", e)
        notify("contentinsight", f"gagal menyusun laporan performa — {e}", chat_id=resolve_chat_id())
        sys.exit(1)
