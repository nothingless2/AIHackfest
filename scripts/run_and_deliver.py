"""Jalankan pipeline sampai selesai lalu kirim hasilnya sendiri ke Telegram.

Dipakai untuk mode asinkron: plugin OpenClaw memanggil script ini secara detached
lalu langsung kembali, supaya render panjang (2-4 menit) tidak dibunuh oleh batas
waktu eksekusi tool. Progress dan video dikirim langsung dari sini.

Sengaja TIDAK melakukan polling getUpdates: balasan APPROVE/REVISI user belum
punya handler di jalur ini sama sekali (lihat STATUS.md) -- caption di bawah
sekadar instruksi untuk user, bukan janji fungsional. File per-run
(video_{run_id}.mp4, creative_brief_{run_id}.json) sengaja TIDAK dihapus
langsung setelah terkirim, supaya jadi fondasi siap pakai kalau/ketika handler
APPROVE untuk jalur plugin ini dibangun -- dibersihkan lewat sweep retensi
(ditambahkan di Commit 1b), bukan di sini.
"""

import os
import sys

from common import (
    TELEGRAM_CHAT_ID,
    brief_path_for_run,
    draft_video_path_for_run,
    ensure_dirs,
    log_error,
    notify,
    read_json,
    send_video,
)
from orchestrator import install_signal_handlers, run_core_stages_locked
from run_lock import FileLockBusyError, RUN_LOCK_STALE_SECONDS, generate_run_id, sanitize_run_id
from run_log import log_event


def deliver_plugin(run_id):
    video_path = draft_video_path_for_run(run_id)
    brief = read_json(brief_path_for_run(run_id), {}) or {}
    judul = brief.get("judul", "Untitled")
    hashtags = " ".join(brief.get("hashtags", []))

    caption = (
        "✅ ApprovalPost\n\n"
        "Draf Konten Siap Direview!\n"
        f"Judul: {judul}\n"
        f"{hashtags}\n\n"
        "Balas APPROVE untuk menyetujui atau REVISI untuk perbaikan."
    )

    ok = send_video(caption, video_path)
    log_event("delivered" if ok else "delivery_failed", run_id, chat_id=TELEGRAM_CHAT_ID)
    if not ok:
        notify("pipeline", f"video selesai tapi gagal dikirim. File ada di {video_path}")
        return 1
    return 0


def _on_noncritical_failure(label, code):
    print(f"[warn] {label} gagal (exit {code}), tahap non-kritis — lanjut.")


def main():
    install_signal_handlers()
    ensure_dirs()
    run_id = sanitize_run_id(os.getenv("CONTENT_FACTORY_RUN_ID") or generate_run_id())

    try:
        status, detail = run_core_stages_locked(
            run_id, capture_output=True, on_noncritical_failure=_on_noncritical_failure
        )
    except FileLockBusyError as e:
        log_event(
            "run_rejected", run_id, chat_id=TELEGRAM_CHAT_ID,
            holder=e.holder, elapsed_seconds=e.elapsed_seconds,
        )
        if e.elapsed_seconds > RUN_LOCK_STALE_SECONDS:
            msg = (
                f"masih ada render berjalan, TAPI sudah {e.elapsed_seconds:.0f} detik "
                f"(lebih lama dari wajar ~{RUN_LOCK_STALE_SECONDS}s) — mungkin proses macet, cek manual."
            )
        else:
            msg = f"masih ada render yang berjalan ({e.elapsed_seconds:.0f} detik lalu). Coba lagi setelah selesai."
        notify("pipeline", msg)
        return os.EX_TEMPFAIL

    if status == "FAILED":
        code, (label, output) = detail
        tail = ("\n" + "\n".join(output.splitlines()[-4:])) if output else ""
        notify("pipeline", f"gagal di tahap {label}.{tail}")
        return code

    return deliver_plugin(run_id)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log_error("run_and_deliver failure", e)
        notify("pipeline", f"pipeline gagal — {e}")
        sys.exit(1)
