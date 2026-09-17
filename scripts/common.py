"""Fondasi bersama semua agent: path, state, logging, notifikasi Telegram, persona."""

import json
import os
import traceback
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

# --- Root proyek dihitung dari lokasi file ini, bukan dari cwd pemanggil.
# Ini penting: pipeline bisa dipanggil dari mana saja (plugin OpenClaw, cron, CLI)
# tanpa path menjadi salah arah.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

RAW_DIR = os.path.join(PROJECT_ROOT, "workspace", "raw")
DRAFTS_DIR = os.path.join(PROJECT_ROOT, "workspace", "drafts")
STATE_DIR = os.path.join(PROJECT_ROOT, "workspace", "state")
PUBLISHED_DIR = os.path.join(PROJECT_ROOT, "workspace", "published")  # dipakai mulai 1b

BRIEF_PATH = os.path.join(STATE_DIR, "creative_brief.json")
TREND_REPORT_PATH = os.path.join(STATE_DIR, "trend_report.json")
RENDER_STATUS_PATH = os.path.join(STATE_DIR, "render_status.json")
PUBLISH_HISTORY_PATH = os.path.join(STATE_DIR, "publish_history.json")
PERFORMANCE_PATH = os.path.join(STATE_DIR, "performance_summary.json")
TREND_POOL_PATH = os.path.join(STATE_DIR, "trend_pool.json")
ERROR_LOG_PATH = os.path.join(STATE_DIR, "error.log")
RUN_LOG_PATH = os.path.join(STATE_DIR, "run_log.jsonl")

DRAFT_VIDEO_PATH = os.path.join(DRAFTS_DIR, "video_output.mp4")


def draft_video_path_for_run(run_id):
    """Path video hasil akhir milik satu run spesifik — dibaca oleh apa pun
    yang berjalan SETELAH lock render dilepas (delivery, approval-wait),
    supaya tidak pernah membaca file yang bisa tertimpa run berikutnya.
    Fallback ke path tetap kalau run_id kosong (pemakaian standalone lama)."""
    if not run_id:
        return DRAFT_VIDEO_PATH
    return os.path.join(DRAFTS_DIR, f"video_{run_id}.mp4")


def brief_path_for_run(run_id):
    """Padanan draft_video_path_for_run() untuk creative_brief.json."""
    if not run_id:
        return BRIEF_PATH
    return os.path.join(STATE_DIR, f"creative_brief_{run_id}.json")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Chat tujuan untuk RUN INI. Satu-satunya sumber: CONTENT_FACTORY_CHAT_ID, yang
# diisi oleh titik masuk (plugin OpenClaw dari nativeChannelId, atau pipeline.py
# dari .env untuk pemakaian CLI) lalu diwariskan ke semua tahap sebagai env.
#
# TIDAK ADA fallback ke TELEGRAM_CHAT_ID di sini. Versi lama memakai
# `CONTENT_FACTORY_CHAT_ID or TELEGRAM_CHAT_ID`, sehingga ketika chat pemicu tidak
# dapat ditentukan, hasil run SIAPA PUN dikirim ke satu chat tetap di .env --
# materi milik user A bisa sampai ke user B. Sekarang gagal-tertutup: tidak tahu
# tujuannya berarti tidak mengirim.
def resolve_chat_id():
    """Chat tujuan run ini, atau None kalau tidak dapat ditentukan."""
    return (os.getenv("CONTENT_FACTORY_CHAT_ID") or "").strip() or None


# HANYA untuk jalur CLI (pipeline.py) sebagai nilai awal yang lalu diteruskan
# lewat CONTENT_FACTORY_CHAT_ID. Jangan pernah dipakai langsung sebagai tujuan.
CLI_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip() or None


def allowed_chat_ids():
    """Himpunan chat yang boleh memicu pipeline, dari ALLOWED_CHAT_IDS di .env.

    Dibaca per-panggilan (bukan konstanta modul) supaya perubahan .env tidak
    butuh restart, dan supaya test bisa mengubahnya lewat monkeypatch env.
    """
    raw = os.getenv("ALLOWED_CHAT_IDS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def chat_allowed(chat_id):
    """True kalau chat_id boleh memicu pipeline.

    GAGAL-TERTUTUP: ALLOWED_CHAT_IDS kosong/tidak diset berarti TIDAK ADA yang
    diizinkan -- bukan "semua boleh". Pipeline ini membakar kredit OpenAI dan
    mengirim materi user, jadi daftar kosong harus berarti berhenti, bukan
    terbuka lebar untuk siapa pun yang kebetulan sudah paired di Telegram.
    """
    if not chat_id:
        return False
    return str(chat_id) in allowed_chat_ids()

# Persona sesuai agent yang terdaftar di OpenClaw (agents.entries).
PERSONA = {
    "trendanalysts": "🔍 TrendAnalysts",
    "brainidea": "💡 BrainIdea",
    "contentmakers": "✍️ ContentMakers",
    "approvalpost": "✅ ApprovalPost",
    "contentinsight": "📊 ContentInsight",
    "pipeline": "⚙️ Pipeline",
}

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp",
    ".mp4", ".mov", ".avi", ".mkv",
}


def ensure_dirs():
    for path in (RAW_DIR, DRAFTS_DIR, STATE_DIR, PUBLISHED_DIR):
        os.makedirs(path, exist_ok=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def log_error(context, exc):
    """Catat error lengkap ke error.log tanpa menghentikan proses pemanggil."""
    ensure_dirs()
    entry = f"[{now_iso()}] {context}: {exc}\n{traceback.format_exc()}\n"
    with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry)


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, payload):
    ensure_dirs()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)


def telegram_configured(chat_id=None):
    """Siap mengirim? Butuh token DAN chat tujuan yang eksplisit.

    chat_id=None berarti "pakai tujuan run ini" (resolve_chat_id()).
    """
    target = chat_id if chat_id is not None else resolve_chat_id()
    return bool(TELEGRAM_BOT_TOKEN and target)


def notify(agent_key, message, *, chat_id, silent_fail=True):
    """Kirim pesan berlabel persona ke SATU chat tertentu. Selalu ikut dicetak ke stdout.

    `chat_id` sengaja wajib dan keyword-only: tujuan pengiriman tidak boleh datang
    dari konstanta global/ambient. chat_id kosong -> hanya dicetak, tidak dikirim.

    Kalau CONTENT_FACTORY_QUIET diset (mis. saat dijalankan lewat plugin OpenClaw),
    pesan hanya dicetak — pengiriman ke chat diserahkan ke pemanggil supaya tidak dobel.
    """
    label = PERSONA.get(agent_key, agent_key)
    text = f"{label}: {message}"
    print(text)

    if os.getenv("CONTENT_FACTORY_QUIET"):
        return False

    if not chat_id:
        print(f"[warn] notify({agent_key}): chat tujuan tidak diketahui, pesan tidak dikirim.")
        return False

    if not telegram_configured(chat_id):
        return False

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as e:
        if not silent_fail:
            raise
        log_error(f"notify({agent_key})", e)
        return False


def send_video(caption, video_path, *, chat_id):
    """Kirim file video ke SATU chat tertentu. Return True kalau benar-benar terkirim.

    `chat_id` wajib dan keyword-only: ini jalur yang mengirim materi milik user,
    jadi tujuannya harus disebut eksplisit oleh pemanggil, tidak pernah ditebak.
    """
    if not chat_id:
        print(f"[warn] chat tujuan tidak diketahui, video TIDAK dikirim: {video_path}")
        return False

    if not telegram_configured(chat_id):
        print(f"[warn] Telegram belum dikonfigurasi, video tidak dikirim: {video_path}")
        return False

    if not os.path.exists(video_path):
        print(f"[warn] Video tidak ditemukan: {video_path}")
        return False

    try:
        with open(video_path, "rb") as vf:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo",
                data={"chat_id": chat_id, "caption": caption},
                files={"video": vf},
                timeout=120,
            )
        if resp.status_code != 200:
            print(f"[warn] Gagal kirim video ke Telegram: {resp.text}")
            return False
        return True
    except Exception as e:
        log_error("send_video", e)
        return False


def list_raw_assets():
    """Semua bahan mentah yang benar-benar ada di workspace/raw/."""
    ensure_dirs()
    return sorted(
        name
        for name in os.listdir(RAW_DIR)
        if os.path.splitext(name)[1].lower() in MEDIA_EXTENSIONS
    )


def resolve_assets(names):
    """Ubah nama file jadi path absolut + pastikan semuanya benar-benar ada.

    Zero Hallucination on Assets: kalau ada nama yang tidak ada di disk, gagal
    terang-terangan alih-alih diam-diam melanjutkan dengan bahan karangan.

    Nama WAJIB berupa nama file polos di dalam RAW_DIR. Pemisah path dan '..'
    ditolak, dan hasil akhirnya diperiksa ulang dengan realpath supaya symlink
    pun tidak bisa menunjuk keluar folder. Tanpa ini, satu nama berisi '../'
    cukup untuk membaca file di luar workspace.
    """
    bad = [
        n for n in names
        if not n
        or os.path.basename(n) != n
        or n in (".", "..")
        or os.path.isabs(n)
    ]
    if bad:
        raise ValueError(f"Nama bahan mentah tidak valid (harus nama file polos): {bad}")

    raw_root = os.path.realpath(RAW_DIR)
    paths = [os.path.join(RAW_DIR, name) for name in names]

    escaped = [
        n for n, p in zip(names, paths)
        if os.path.commonpath([raw_root, os.path.realpath(p)]) != raw_root
    ]
    if escaped:
        raise ValueError(f"Bahan mentah menunjuk keluar {RAW_DIR}: {escaped}")

    missing = [n for n, p in zip(names, paths) if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(f"Bahan mentah tidak ada di {RAW_DIR}: {missing}")
    return paths

# ---------------------------------------------------------------- OpenAI

# Timeout per percobaan. Default SDK adalah read=600 detik -- satu panggilan yang
# menggantung akan menahan tahapnya selama 10 menit dan baru dibunuh paksa oleh
# timeout orchestrator. 45 detik jauh di atas latensi normal GPT-4o (termasuk
# dengan gambar), tapi cukup pendek untuk gagal cepat dan mencoba lagi.
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "45"))


def openai_is_retriable(exc):
    """Kegagalan sementara vs permanen.

    URUTAN PENTING: AuthenticationError/BadRequestError dsb adalah TURUNAN dari
    APIStatusError, jadi kelas permanen harus diperiksa SEBELUM aturan 5xx --
    kalau dibalik, kunci API yang salah akan diulang tiga kali sia-sia.
    """
    import openai

    if isinstance(exc, (openai.RateLimitError, openai.InternalServerError,
                        openai.APIConnectionError)):
        return True  # APITimeoutError turunan APIConnectionError, ikut tercakup
    if isinstance(exc, (openai.AuthenticationError, openai.BadRequestError,
                        openai.PermissionDeniedError, openai.NotFoundError,
                        openai.UnprocessableEntityError)):
        return False
    if isinstance(exc, openai.APIStatusError):
        return (getattr(exc, "status_code", 0) or 0) >= 500
    return False


def openai_retry_after(exc):
    """Detik yang diminta server lewat header Retry-After, kalau ada."""
    resp = getattr(exc, "response", None)
    nilai = getattr(resp, "headers", {}).get("Retry-After") if resp is not None else None
    try:
        return float(nilai) if nilai else None
    except (TypeError, ValueError):
        return None


def chat_json(messages, *, model, label="panggilan LLM"):
    """Panggil chat completion yang mengembalikan JSON, dengan retry.

    max_retries=0 mematikan retry BAWAAN SDK (defaultnya 2). Tanpa itu, retry
    bersarang: SDK mencoba 3x di dalam tiap percobaan kita, jadi total 9 kali
    dengan jeda yang tidak kita kendalikan dan melewati timeout tahap.
    """
    import json as _json

    from openai import OpenAI

    from retry import with_retry

    client = OpenAI(api_key=OPENAI_API_KEY, max_retries=0,
                    timeout=OPENAI_TIMEOUT_SECONDS)

    def sekali():
        resp = client.chat.completions.create(
            model=model, messages=messages,
            response_format={"type": "json_object"},
        )
        _catat_pemakaian_llm(model, resp, label)
        return _json.loads(resp.choices[0].message.content)

    return with_retry(
        sekali,
        is_retriable=openai_is_retriable,
        extract_retry_after=openai_retry_after,
        label=label,
    )


def _catat_pemakaian_llm(model, resp, label):
    """Catat pemakaian token NYATA dari response.usage.

    Dicatat per PANGGILAN, bukan per run: percobaan yang gagal lalu diulang tetap
    menghabiskan token di sisi server kalau sempat diproses, dan satu run punya
    lebih dari satu panggilan LLM.

    Seluruh badan fungsi ini dibungkus try/except: pelacakan biaya adalah
    pengamatan dan TIDAK BOLEH menggagalkan pipeline.
    """
    try:
        from cost_estimate import estimate_llm_cost
        from run_log import log_event  # impor tertunda: run_log mengimpor common

        usage = getattr(resp, "usage", None)
        masuk = getattr(usage, "prompt_tokens", None)
        keluar = getattr(usage, "completion_tokens", None)
        log_event(
            "llm_call",
            (os.getenv("CONTENT_FACTORY_RUN_ID") or "").strip() or None,
            chat_id=resolve_chat_id(),
            label=label,
            model=model,
            prompt_tokens=masuk,
            completion_tokens=keluar,
            total_tokens=getattr(usage, "total_tokens", None),
            cost_usd=estimate_llm_cost(model, masuk, keluar),
        )
    except Exception as e:
        print(f"[warn] cost: gagal mencatat pemakaian LLM: {type(e).__name__}: {e}")
