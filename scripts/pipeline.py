"""Orkestrator pipeline 5 agent (jalur CLI).

Urutan: ContentInsight (konteks) -> TrendAnalysts + BrainIdea -> ContentMakers
-> ApprovalPost.

1a: tahap render (3 pertama) dilindungi lock eksklusif + timeout proses-grup.
Fase 3 (ApprovalPost) BELUM pakai lock approval/guard gateway sendiri -- itu
ditambahkan di Commit 1b. Untuk sekarang aman dipakai selama tidak ada 2 mode
approval CLI berjalan bersamaan (risiko itu yang ditutup 1b).
"""

import os
import sys

from canvas import CanvasError, resolve_canvas
from duration import requested_duration
from music import MusicError, music_wanted, pick_track, requested_mood
from common import CLI_CHAT_ID, chat_allowed, ensure_dirs, log_error, notify
from gateway_check import warn_if_gateway_polling
from orchestrator import install_signal_handlers, run_core_stages_locked, run_stage
from run_lock import FileLockBusyError, RUN_LOCK_STALE_SECONDS, generate_run_id, sanitize_run_id
from retention import sweep_old_run_files
from run_log import log_event


def _on_stage_start(label):
    print(f"\n▶ {label}")


def _on_noncritical_failure(label, code):
    print(f"⚠️ {label} gagal (exit {code}), tahap ini tidak kritis — pipeline lanjut.")


def main():
    install_signal_handlers()
    ensure_dirs()
    sweep_old_run_files()  # jaring pengaman retensi, murah & aman dipanggil tiap run
    print("=" * 60)
    print("🚀 PIPELINE 5 AGENT CONTENT FACTORY")
    print("=" * 60)

    # Guard gateway DI SINI, sebelum render. Fase 3 (approval CLI) akan ditolak
    # kalau gateway hidup; menolaknya di ujung berarti sudah terlanjur membakar
    # waktu render dan kredit GPT-4o untuk hasil yang tidak bisa di-approve.
    if warn_if_gateway_polling():
        print(
            "\n❌ Gateway OpenClaw aktif — mode approval CLI tidak aman. "
            "Hentikan gateway, atau pakai jalur plugin (tool content_factory_run)."
        )
        return os.EX_TEMPFAIL

    run_id = sanitize_run_id(os.getenv("CONTENT_FACTORY_RUN_ID") or generate_run_id())

    # Jalur CLI: .env boleh jadi sumber chat tujuan (di sinilah satu-satunya
    # tempat TELEGRAM_CHAT_ID sah dipakai). Nilainya lalu diteruskan ke semua
    # tahap lewat CONTENT_FACTORY_CHAT_ID, supaya agent anak punya satu sumber
    # kebenaran yang sama dengan jalur Telegram -- tanpa fallback tersembunyi.
    chat_id = CLI_CHAT_ID
    if chat_id:
        os.environ["CONTENT_FACTORY_CHAT_ID"] = chat_id
    else:
        print("ℹ️  TELEGRAM_CHAT_ID kosong di .env — pipeline tetap jalan, "
              "tapi tidak ada notifikasi/hasil yang dikirim ke Telegram.")

    # Konsisten dengan jalur Telegram: allowlist dicek sebelum lock & GPT-4o.
    # chat_id kosong tetap diizinkan -- itu run lokal murni yang tidak mengirim
    # apa pun ke siapa pun, jadi tidak ada yang perlu dilindungi allowlist.
    if chat_id and not chat_allowed(chat_id):
        log_event("run_rejected", run_id, chat_id=chat_id, reason="chat_not_allowed")
        print(
            f"\n❌ chat {chat_id} (TELEGRAM_CHAT_ID di .env) tidak ada di ALLOWED_CHAT_IDS. "
            "Tambahkan ke ALLOWED_CHAT_IDS, atau kosongkan TELEGRAM_CHAT_ID untuk run lokal tanpa kirim."
        )
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
        return os.EX_CONFIG

    # Mood musik divalidasi di sini juga: minta "lofi" padahal pustaka hanya
    # berisi "akustik" harus ketahuan SEKARANG, bukan setelah render selesai.
    try:
        if music_wanted():
            pick_track(requested_mood(), run_id=run_id)
    except MusicError as e:
        print(f"[error] {e}")
        log_event("run_rejected", run_id, chat_id=chat_id, reason="music_mood_invalid")
        return os.EX_CONFIG

    # Sama seperti jalur plugin: di luar rentang dijepit, bukan ditolak.
    target_durasi, pesan_durasi = requested_duration()
    if pesan_durasi:
        print(f"[info] {pesan_durasi}")
    if target_durasi:
        os.environ["CONTENT_FACTORY_DURATION"] = str(target_durasi)

    try:
        status, detail = run_core_stages_locked(
            run_id,
            chat_id=chat_id,
            capture_output=False,
            on_stage_start=_on_stage_start,
            on_noncritical_failure=_on_noncritical_failure,
        )
    except FileLockBusyError as e:
        log_event(
            "run_rejected", run_id, chat_id=chat_id, reason="render_locked",
            holder_run_id=(e.holder or {}).get("holder_id"),
            elapsed_seconds=e.elapsed_seconds,
        )
        if e.elapsed_seconds > RUN_LOCK_STALE_SECONDS:
            msg = (
                f"masih ada render berjalan, TAPI sudah {e.elapsed_seconds:.0f} detik "
                f"(lebih lama dari wajar ~{RUN_LOCK_STALE_SECONDS}s) — mungkin proses macet, cek manual."
            )
        else:
            msg = f"masih ada render yang berjalan ({e.elapsed_seconds:.0f} detik lalu). Coba lagi setelah selesai."
        print(f"\n❌ {msg}")
        notify("pipeline", msg, chat_id=chat_id)
        return os.EX_TEMPFAIL

    if status == "FAILED":
        code, (label, _output) = detail
        message = f"Pipeline dihentikan: {label} gagal (exit code {code})."
        print(f"\n❌ {message}")
        notify("pipeline", message, chat_id=chat_id)
        return code

    print("\n▶ Fase 3 — ApprovalPost (approval user)")
    code, _output = run_stage(
        "agent4_approval.py", run_id=run_id, capture_output=False, timeout=None
    )
    if code != 0:
        message = f"Pipeline dihentikan: Fase 3 — ApprovalPost (approval user) gagal (exit code {code})."
        print(f"\n❌ {message}")
        notify("pipeline", message, chat_id=chat_id)
        return code

    print("\n" + "=" * 60)
    print("✅ SIKLUS SELESAI.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log_error("pipeline orchestrator failure", e)
        print(f"❌ Pipeline error: {e}")
        sys.exit(1)
