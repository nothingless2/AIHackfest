"""Jalankan pipeline sampai selesai lalu kirim hasilnya sendiri ke Telegram.

Dipakai untuk mode asinkron: plugin OpenClaw memanggil script ini secara detached
lalu langsung kembali, supaya render panjang (2-4 menit) tidak dibunuh oleh batas
waktu eksekusi tool. Progress dan video dikirim langsung dari sini.

ROUTING (gagal-tertutup): tujuan pengiriman HANYA dari CONTENT_FACTORY_CHAT_ID
yang diisi plugin dari nativeChannelId. Tidak ada fallback ke TELEGRAM_CHAT_ID di
.env -- fallback itulah yang dulu membuat hasil run siapa pun terkirim ke satu
chat tetap, sehingga materi milik user A bisa sampai ke user B. Kalau chat pemicu
tidak dapat ditentukan, run ditolak dan TIDAK ada yang dikirim ke siapa pun.

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

from canvas import CanvasError, resolve_canvas
from common import (
    brief_path_for_run,
    chat_allowed,
    draft_thumb_path_for_run,
    draft_video_path_for_run,
    ensure_dirs,
    log_error,
    notify,
    read_json,
    resolve_chat_id,
    send_video,
)
from cost_estimate import ringkasan_biaya
from orchestrator import install_signal_handlers, run_core_stages_locked
from run_lock import FileLockBusyError, RUN_LOCK_STALE_SECONDS, generate_run_id, sanitize_run_id
from retention import sweep_old_run_files
from run_log import log_event


def deliver_plugin(run_id, chat_id):
    video_path = draft_video_path_for_run(run_id)
    thumb_path = draft_thumb_path_for_run(run_id)
    brief = read_json(brief_path_for_run(run_id), {}) or {}
    judul = brief.get("judul", "Untitled")
    hashtags = " ".join(brief.get("hashtags", []))

    deskripsi = (brief.get("deskripsi") or "").strip()
    biaya = ringkasan_biaya(run_id)

    bagian = ["✅ ApprovalPost", "", "Draf Konten Siap Direview!", f"Judul: {judul}"]
    if deskripsi:
        bagian += ["", f"Deskripsi: {deskripsi}"]
    if hashtags:
        bagian += ["", hashtags]
    if biaya:
        bagian += ["", biaya]
    bagian += ["", "Balas APPROVE untuk menyetujui atau REVISI untuk perbaikan."]

    caption = "\n".join(bagian)
    # Batas caption Telegram 1024 karakter. Dipotong di sini, bukan dibiarkan
    # gagal kirim -- video yang sudah jadi jauh lebih berharga daripada caption utuh.
    if len(caption) > 1000:
        caption = caption[:997] + "..."

    ok = send_video(caption, video_path, chat_id=chat_id, thumb_path=thumb_path)
    log_event("delivered" if ok else "delivery_failed", run_id, chat_id=chat_id)
    if not ok:
        notify(
            "pipeline",
            f"video selesai tapi gagal dikirim. File ada di {video_path}",
            chat_id=chat_id,
        )
        return 1
    return 0


def _on_noncritical_failure(label, code):
    print(f"[warn] {label} gagal (exit {code}), tahap non-kritis — lanjut.")


def main():
    install_signal_handlers()
    ensure_dirs()
    sweep_old_run_files()  # jaring pengaman retensi, murah & aman dipanggil tiap run
    run_id = sanitize_run_id(os.getenv("CONTENT_FACTORY_RUN_ID") or generate_run_id())

    chat_id = resolve_chat_id()
    if not chat_id:
        # Gagal-tertutup: tanpa chat pemicu yang jelas, tidak ada tujuan yang aman.
        # Mengirim ke chat cadangan berarti membocorkan materi ke orang yang salah.
        msg = (
            "chat pemicu tidak dapat ditentukan (CONTENT_FACTORY_CHAT_ID kosong) — "
            "run dibatalkan, tidak ada yang dikirim ke siapa pun."
        )
        print(f"[error] {msg}")
        log_error("run_and_deliver routing", RuntimeError(msg))
        log_event("run_rejected", run_id, chat_id=None, reason="chat_unknown")
        return 1

    # Allowlist dicek PALING AWAL: sebelum lock render dan sebelum satu pun
    # panggilan GPT-4o, supaya chat yang tidak berhak tidak pernah membakar
    # kredit maupun menahan giliran render orang lain.
    if not chat_allowed(chat_id):
        log_event("run_rejected", run_id, chat_id=chat_id, reason="chat_not_allowed")
        print(f"[error] chat {chat_id} tidak ada di ALLOWED_CHAT_IDS — run ditolak.")
        # Balasan singkat & netral ke peminta sendiri: cukup memberi tahu bahwa
        # aksesnya tidak diizinkan, tanpa membocorkan detail sistem/daftar chat.
        notify("pipeline", "akses tidak diizinkan untuk chat ini.", chat_id=chat_id)
        return os.EX_NOPERM

    # Rasio & fit mode divalidasi DI SINI: sebelum lock render dan sebelum satu
    # pun panggilan GPT-4o. Nilai salah ketik tidak boleh membakar kredit lalu
    # baru ditolak di tahap render, dan TIDAK boleh diam-diam jatuh ke default.
    try:
        lebar, tinggi, fit = resolve_canvas()
        print(f"[info] kanvas: {lebar}x{tinggi} ({fit})")
    except CanvasError as e:
        print(f"[error] {e}")
        log_event("run_rejected", run_id, chat_id=chat_id, reason="canvas_invalid")
        notify("pipeline", f"konfigurasi video tidak valid: {e}", chat_id=chat_id)
        return os.EX_CONFIG

    try:
        status, detail = run_core_stages_locked(
            run_id,
            chat_id=chat_id,
            capture_output=True,
            on_noncritical_failure=_on_noncritical_failure,
        )
    except FileLockBusyError as e:
        log_event(
            "run_rejected", run_id, chat_id=chat_id, reason="render_locked",
            holder_run_id=(e.holder or {}).get("holder_id"), elapsed_seconds=e.elapsed_seconds,
        )
        if e.elapsed_seconds > RUN_LOCK_STALE_SECONDS:
            msg = (
                f"masih ada render berjalan, TAPI sudah {e.elapsed_seconds:.0f} detik "
                f"(lebih lama dari wajar ~{RUN_LOCK_STALE_SECONDS}s) — mungkin proses macet, cek manual."
            )
        else:
            msg = f"masih ada render yang berjalan ({e.elapsed_seconds:.0f} detik lalu). Coba lagi setelah selesai."
        # Dikirim ke chat PEMINTA yang ditolak, dan sengaja tidak menyebut apa pun
        # tentang isi/judul run yang sedang memegang lock.
        notify("pipeline", msg, chat_id=chat_id)
        return os.EX_TEMPFAIL

    if status == "FAILED":
        code, (label, output) = detail
        tail = ("\n" + "\n".join(output.splitlines()[-4:])) if output else ""
        notify("pipeline", f"gagal di tahap {label}.{tail}", chat_id=chat_id)
        return code

    return deliver_plugin(run_id, chat_id)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log_error("run_and_deliver failure", e)
        notify("pipeline", f"pipeline gagal — {e}", chat_id=resolve_chat_id())
        sys.exit(1)
