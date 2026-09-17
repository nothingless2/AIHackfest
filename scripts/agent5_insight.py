"""Agent 5 (ContentInsight): laporkan performa konten yang sudah tayang.

Aturan: laporkan apa adanya, jangan dipermanis. Selama API analitik asli belum
tersedia, angka ditandai jelas sebagai estimasi (data_source) dan TIDAK selalu positif.
"""

import random
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


def estimate_metrics():
    """Estimasi acak dalam rentang wajar — sengaja bisa jelek, supaya sinyal ke
    TrendAnalysts jujur dan bervariasi, bukan selalu 'HIGH_PERFORMING'."""
    views = random.randint(500, 60000)
    likes = int(views * random.uniform(0.01, 0.09))
    comments = int(views * random.uniform(0.001, 0.01))
    shares = int(views * random.uniform(0.0, 0.02))
    rate = round((likes + comments + shares) / views * 100, 2) if views else 0.0
    return {
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "engagement_rate": rate,
    }


def run():
    ensure_dirs()

    post = latest_published_post()
    metrics = fetch_real_analytics(post["platform"], post["publish_id"]) if post else None

    if metrics:
        data_source = "REAL_API"
        post_id = post["publish_id"]
    else:
        metrics = estimate_metrics()
        data_source = "ESTIMATED_PLACEHOLDER"
        post_id = post["publish_id"] if post else "no_published_post_yet"
        if not post:
            print("[Agent 5] Belum ada post berstatus PUBLISHED di publish_history.json.")

    summary = {
        "report_date": now_iso(),
        "post_id": post_id,
        "data_source": data_source,
        "metrics": metrics,
        "performance_status": classify(float(metrics["engagement_rate"])),
    }
    write_json(PERFORMANCE_PATH, summary)

    notify(
        "contentinsight",
        f"laporan performa siap (sumber: {data_source}, "
        f"engagement {metrics['engagement_rate']}%, status {summary['performance_status']}).",
        chat_id=resolve_chat_id(),
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as e:
        log_error("Agent 5 (insight) failure", e)
        notify("contentinsight", f"gagal menyusun laporan performa — {e}", chat_id=resolve_chat_id())
        sys.exit(1)
