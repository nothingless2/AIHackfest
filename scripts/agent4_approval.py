"""Agent 4 (ApprovalPost): kirim draft ke Telegram, tunggu APPROVE/REVISI dari user.

Tidak ada jalur kode apa pun yang mempublikasikan konten tanpa persetujuan eksplisit user.
"""

import os
import sys
import time

import shutil

import requests

from common import (
    BRIEF_PATH,
    DRAFT_VIDEO_PATH,
    PUBLISHED_DIR,
    brief_path_for_run,
    draft_video_path_for_run,
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

from gateway_check import warn_if_gateway_polling  # noqa: E402
from retention import cleanup_run_files, sweep_old_run_files  # noqa: E402
from run_lock import FileLockBusyError, acquire_approval_lock, sanitize_run_id  # noqa: E402
from run_log import log_event  # noqa: E402

APPROVE_WORDS = {"APPROVE", "YES", "Y", "OK", "SETUJU"}
REJECT_WORDS = {"REVISI", "NO", "N", "TOLAK", "REJECT"}


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


def record_history(status, live_url=None, *, video_path=None, brief_path=None):
    """Catat satu entri riwayat.

    `video_path`/`brief_path` WAJIB diberikan pemanggil, bukan diambil dari
    konstanta global: nilainya berbeda tergantung momen. Sebelum APPROVE ia path
    per-run; sesudahnya ia sudah pindah ke workspace/published/. Mencatat path
    yang salah berarti file_path di publish_history.json menunjuk ke file yang
    sudah/akan tidak ada.
    """
    history = read_json(PUBLISH_HISTORY_PATH, []) or []
    brief = read_json(brief_path or BRIEF_PATH, {}) or {}
    history.append(
        {
            "publish_id": f"pub_{len(history) + 1:03d}",
            "timestamp": now_iso(),
            "platform": TARGET_PLATFORM,
            "file_path": video_path or DRAFT_VIDEO_PATH,
            "judul": brief.get("judul"),
            "brief_id": brief.get("brief_id"),
            "status": status,
            "live_url": live_url,
        }
    )
    write_json(PUBLISH_HISTORY_PATH, history)
    return history[-1]


def publish_to_platforms(video_path, caption=""):
    """Publikasikan video yang SUDAH disetujui. Return live_url atau None.

    `video_path` harus sudah berada di workspace/published/, karena Meta menarik
    file dari URL publik yang menyajikan direktori itu -- bukan dari path per-run
    yang tidak terekspos.
    """
    from publish import publish as publish_ke_platform

    return publish_ke_platform(video_path, caption)


def run():
    ensure_dirs()
    sweep_old_run_files()

    # run_id dan kedua path didefinisikan PALING AWAL -- sebelum guard apa pun,
    # karena setiap guard di bawah memanggil record_history() yang membutuhkannya.
    mentah = (os.getenv("CONTENT_FACTORY_RUN_ID") or "").strip()
    run_id = sanitize_run_id(mentah) if mentah else None
    video_path = draft_video_path_for_run(run_id)
    brief_path = brief_path_for_run(run_id)
    riwayat = {"video_path": video_path, "brief_path": brief_path}

    if not os.path.exists(video_path):
        raise FileNotFoundError(
            f"Draft video tidak ada di {video_path}. Jalankan Agent 3 dahulu."
        )

    chat_id = resolve_chat_id()
    if not chat_id:
        record_history("REJECTED_CHAT_UNKNOWN", **riwayat)
        raise ValueError(
            "Chat pemicu tidak dapat ditentukan (CONTENT_FACTORY_CHAT_ID kosong) — "
            "draft TIDAK dikirim ke siapa pun."
        )

    if not telegram_configured(chat_id):
        record_history("PENDING_NO_TELEGRAM", **riwayat)
        raise ValueError(
            "TELEGRAM_BOT_TOKEN belum diisi di .env — "
            "draft tidak bisa dikirim untuk approval."
        )

    # Guard keras, bukan sekadar peringatan: dengan gateway hidup, polling
    # getUpdates di bawah akan rebutan antrian dan balasan user bisa hilang.
    if warn_if_gateway_polling():
        record_history("REJECTED_GATEWAY_ACTIVE", **riwayat)
        raise RuntimeError(
            "Gateway OpenClaw aktif — approval lewat CLI tidak aman sekarang. "
            "Hentikan gateway atau pakai jalur plugin."
        )

    brief = read_json(brief_path, {}) or {}
    caption = (
        "✅ ApprovalPost\n\n"
        "Draf Konten Berdasarkan Tren Siap Direview!\n"
        f"Judul: {brief.get('judul', 'Untitled')}\n"
        f"{' '.join(brief.get('hashtags', []))}\n\n"
        "Balas APPROVE untuk menyetujui atau REVISI untuk perbaikan."
    )

    try:
        # Lock KEDUA, terpisah dari lock render. Dua proses yang sama-sama
        # polling getUpdates dengan token yang sama akan saling mencuri balasan.
        with acquire_approval_lock(run_id or "cli"):
            after_update_id = get_latest_update_id()
            terkirim = send_video(caption, video_path, chat_id=chat_id)
            log_event("delivered" if terkirim else "delivery_failed", run_id, chat_id=chat_id)
            if not terkirim:
                record_history("PENDING_SEND_FAILED", **riwayat)
                raise RuntimeError("Gagal mengirim draft video ke Telegram.")

            print(f"[Agent 4] Menunggu balasan APPROVE/REVISI (timeout {POLL_TIMEOUT_SECONDS} detik)...")
            decision = wait_for_reply(after_update_id, chat_id=chat_id)
    except FileLockBusyError as e:
        # SENGAJA tidak cleanup: file per-run dibiarkan utuh supaya bisa dicoba
        # lagi tanpa render ulang (yang berarti membakar kredit GPT-4o lagi).
        log_event("approval_rejected", run_id, chat_id=chat_id,
                  reason="approval_locked", elapsed_seconds=e.elapsed_seconds)
        perintah = (
            f"CONTENT_FACTORY_RUN_ID={run_id} python3 scripts/agent4_approval.py"
            if run_id else "python3 scripts/agent4_approval.py"
        )
        notify(
            "approvalpost",
            f"ada approval lain sedang menunggu balasan Telegram "
            f"({e.elapsed_seconds:.0f} detik lalu). File run ini MASIH UTUH, tidak perlu "
            f"render ulang.\nRun id: {run_id or '(tanpa run_id)'}\nCoba lagi:\n{perintah}",
            chat_id=chat_id,
        )
        return os.EX_TEMPFAIL

    # --- Di luar lock approval. record_history() dan cleanup PALING AKHIR,
    # --- setelah semua pembacaan video_path/brief_path selesai.
    if decision == "APPROVED":
        # Video DIPINDAH lebih dulu, bukan dihapus. Dua alasan:
        # 1. file_path di publish_history.json disimpan permanen; kalau filenya
        #    dibuang retensi, riwayatnya jadi rujukan mati. published/ di luar
        #    DRAFTS_DIR sehingga tidak pernah kena sweep.
        # 2. Meta MENARIK video dari URL publik yang menyajikan published/.
        #    Mempublikasikan sebelum file pindah berarti Meta menarik dari alamat
        #    yang belum berisi apa-apa.
        nama = f"{run_id}.mp4" if run_id else f"cli_{now_iso().replace(':', '-')}.mp4"
        published_path = os.path.join(PUBLISHED_DIR, nama)
        try:
            shutil.move(video_path, published_path)
        except OSError as e:
            print(f"[warn] gagal memindah video ke published/: {e}")
            published_path = video_path

        live_url = publish_to_platforms(published_path, caption)

        if live_url:
            record_history("PUBLISHED", live_url,
                           video_path=published_path, brief_path=brief_path)
            notify("approvalpost", f"konten sudah dipublikasikan: {live_url}", chat_id=chat_id)
        else:
            record_history("APPROVED_AWAITING_MANUAL_UPLOAD",
                           video_path=published_path, brief_path=brief_path)
            notify(
                "approvalpost",
                "disetujui! Kredensial API platform belum dikonfigurasi, jadi upload "
                f"otomatis belum bisa dilakukan. Silakan upload manual: {published_path}",
                chat_id=chat_id,
            )
        # Video sudah pindah; tinggal brief per-run yang perlu dibuang.
        cleanup_run_files(run_id, video=False)
        return 0

    if decision == "REJECTED":
        record_history("REJECTED", **riwayat)
        notify("approvalpost", "draf ditolak (REVISI). Tidak ada yang dipublikasikan.",
               chat_id=chat_id)
        cleanup_run_files(run_id)
        return 0

    record_history("TIMEOUT_NO_REPLY", **riwayat)
    notify("approvalpost", "tidak ada balasan dalam batas waktu. Tidak ada yang dipublikasikan.",
           chat_id=chat_id)
    cleanup_run_files(run_id)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as e:
        log_error("Agent 4 (approval) failure", e)
        notify("approvalpost", f"gagal memproses approval — {e}", chat_id=resolve_chat_id())
        sys.exit(1)
