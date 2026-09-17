"""Agent 4 (ApprovalPost): kirim draft ke Telegram, tunggu APPROVE/REVISI dari user.

Tidak ada jalur kode apa pun yang mempublikasikan konten tanpa persetujuan eksplisit user.
"""

import os
import socket
import sys
import time

import requests

from common import (
    BRIEF_PATH,
    DRAFT_VIDEO_PATH,
    PUBLISH_HISTORY_PATH,
    TELEGRAM_BOT_TOKEN,
    resolve_chat_id,
    ensure_dirs,
    log_error,
    notify,
    now_iso,
    read_json,
    send_video,
    telegram_configured,
    write_json,
)

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
POLL_TIMEOUT_SECONDS = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "600"))
POLL_INTERVAL_SECONDS = 5
TARGET_PLATFORM = os.getenv("TARGET_PLATFORM", "Instagram Reels")

APPROVE_WORDS = {"APPROVE", "YES", "Y", "OK", "SETUJU"}
REJECT_WORDS = {"REVISI", "NO", "N", "TOLAK", "REJECT"}


def warn_if_gateway_polling():
    """Gateway OpenClaw memakai bot token yang sama dan ikut long-polling getUpdates.

    Telegram hanya punya SATU antrian update per bot: siapa pun yang fetch duluan
    akan menghabiskan antrian milik yang lain. Kalau gateway sedang jalan, balasan
    APPROVE/REVISI user bisa "hilang" ke salah satu sisi.
    """
    try:
        with socket.create_connection(("127.0.0.1", 18789), timeout=2):
            print(
                "⚠️  PERINGATAN: Gateway OpenClaw terdeteksi jalan di port 18789 dan memakai\n"
                "    bot token yang sama. Polling getUpdates akan REBUTAN dan balasan user\n"
                "    bisa hilang. Untuk approval lewat CLI, hentikan gateway dulu\n"
                "    (`openclaw gateway stop`), ATAU gunakan jalur plugin OpenClaw\n"
                "    (tool content_factory_run) yang memakai delivery bawaan gateway."
            )
            return True
    except OSError:
        return False


def get_latest_update_id():
    """Update_id terakhir, supaya balasan lama tidak terhitung sebagai jawaban."""
    resp = requests.get(f"{API_BASE}/getUpdates", params={"limit": 1, "offset": -1}, timeout=15)
    resp.raise_for_status()
    results = resp.json().get("result", [])
    return results[-1]["update_id"] if results else 0


def wait_for_reply(after_update_id, *, chat_id):
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    offset = after_update_id + 1

    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{API_BASE}/getUpdates",
                params={"offset": offset, "timeout": POLL_INTERVAL_SECONDS},
                timeout=POLL_INTERVAL_SECONDS + 15,
            )
            resp.raise_for_status()
            updates = resp.json().get("result", [])
        except Exception as e:
            log_error("wait_for_reply polling", e)
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message") or {}
            # Hanya balasan dari chat yang MEMICU run ini yang dihitung.
            # APPROVE dari chat lain diabaikan: satu user tidak boleh menyetujui
            # (atau menolak) konten milik user lain.
            if str(message.get("chat", {}).get("id")) != str(chat_id):
                continue
            text = (message.get("text") or "").strip().upper()
            if text in APPROVE_WORDS:
                return "APPROVED"
            if text in REJECT_WORDS:
                return "REJECTED"

    return "TIMEOUT"


def record_history(status, live_url=None):
    history = read_json(PUBLISH_HISTORY_PATH, []) or []
    brief = read_json(BRIEF_PATH, {}) or {}
    history.append(
        {
            "publish_id": f"pub_{len(history) + 1:03d}",
            "timestamp": now_iso(),
            "platform": TARGET_PLATFORM,
            "file_path": DRAFT_VIDEO_PATH,
            "judul": brief.get("judul"),
            "brief_id": brief.get("brief_id"),
            "status": status,
            "live_url": live_url,
        }
    )
    write_json(PUBLISH_HISTORY_PATH, history)
    return history[-1]


def publish_to_platforms():
    """TODO: belum ada kredensial API platform (Meta Graph API / TikTok Content Posting
    API / YouTube Data API v3) di .env, jadi upload otomatis belum bisa dilakukan.
    Setelah kredensial tersedia, panggil API upload di sini dan kembalikan live_url.
    Return None berarti belum bisa publish otomatis."""
    return None


def run():
    ensure_dirs()

    if not os.path.exists(DRAFT_VIDEO_PATH):
        raise FileNotFoundError(
            f"Draft video tidak ada di {DRAFT_VIDEO_PATH}. Jalankan Agent 3 dahulu."
        )

    chat_id = resolve_chat_id()
    if not chat_id:
        record_history("REJECTED_CHAT_UNKNOWN")
        raise ValueError(
            "Chat pemicu tidak dapat ditentukan (CONTENT_FACTORY_CHAT_ID kosong) — "
            "draft TIDAK dikirim ke siapa pun."
        )

    if not telegram_configured(chat_id):
        record_history("PENDING_NO_TELEGRAM")
        raise ValueError(
            "TELEGRAM_BOT_TOKEN belum diisi di .env — "
            "draft tidak bisa dikirim untuk approval."
        )

    brief = read_json(BRIEF_PATH, {}) or {}
    judul = brief.get("judul", "Untitled")
    hashtags = " ".join(brief.get("hashtags", []))

    warn_if_gateway_polling()
    after_update_id = get_latest_update_id()

    caption = (
        f"✅ ApprovalPost\n\n"
        f"Draf Konten Berdasarkan Tren Siap Direview!\n"
        f"Judul: {judul}\n"
        f"{hashtags}\n\n"
        f"Balas APPROVE untuk menyetujui atau REVISI untuk perbaikan."
    )

    if not send_video(caption, DRAFT_VIDEO_PATH, chat_id=chat_id):
        record_history("PENDING_SEND_FAILED")
        raise RuntimeError("Gagal mengirim draft video ke Telegram.")

    print(f"[Agent 4] Menunggu balasan APPROVE/REVISI (timeout {POLL_TIMEOUT_SECONDS} detik)...")
    decision = wait_for_reply(after_update_id, chat_id=chat_id)

    if decision == "APPROVED":
        live_url = publish_to_platforms()
        if live_url:
            record_history("PUBLISHED", live_url)
            notify("approvalpost", f"konten sudah dipublikasikan: {live_url}", chat_id=chat_id)
        else:
            record_history("APPROVED_AWAITING_MANUAL_UPLOAD")
            notify(
                "approvalpost",
                "disetujui! Kredensial API platform belum dikonfigurasi, jadi upload "
                f"otomatis belum bisa dilakukan. Silakan upload manual: {DRAFT_VIDEO_PATH}",
                chat_id=chat_id,
            )
        return 0

    if decision == "REJECTED":
        record_history("REJECTED")
        notify("approvalpost", "draf ditolak (REVISI). Tidak ada yang dipublikasikan.", chat_id=chat_id)
        return 0

    record_history("TIMEOUT_NO_REPLY")
    notify("approvalpost", "tidak ada balasan dalam batas waktu. Tidak ada yang dipublikasikan.", chat_id=chat_id)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as e:
        log_error("Agent 4 (approval) failure", e)
        notify("approvalpost", f"gagal memproses approval — {e}", chat_id=resolve_chat_id())
        sys.exit(1)
