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

from common import ensure_dirs, log_error, notify
from orchestrator import install_signal_handlers, run_core_stages_locked, run_stage
from run_lock import FileLockBusyError, RUN_LOCK_STALE_SECONDS, generate_run_id, sanitize_run_id
from run_log import log_event


def _on_stage_start(label):
    print(f"\n▶ {label}")


def _on_noncritical_failure(label, code):
    print(f"⚠️ {label} gagal (exit {code}), tahap ini tidak kritis — pipeline lanjut.")


def main():
    install_signal_handlers()
    ensure_dirs()
    print("=" * 60)
    print("🚀 PIPELINE 5 AGENT CONTENT FACTORY")
    print("=" * 60)

    run_id = sanitize_run_id(os.getenv("CONTENT_FACTORY_RUN_ID") or generate_run_id())

    try:
        status, detail = run_core_stages_locked(
            run_id,
            capture_output=False,
            on_stage_start=_on_stage_start,
            on_noncritical_failure=_on_noncritical_failure,
        )
    except FileLockBusyError as e:
        log_event(
            "run_rejected", run_id, holder=e.holder, elapsed_seconds=e.elapsed_seconds
        )
        if e.elapsed_seconds > RUN_LOCK_STALE_SECONDS:
            msg = (
                f"masih ada render berjalan, TAPI sudah {e.elapsed_seconds:.0f} detik "
                f"(lebih lama dari wajar ~{RUN_LOCK_STALE_SECONDS}s) — mungkin proses macet, cek manual."
            )
        else:
            msg = f"masih ada render yang berjalan ({e.elapsed_seconds:.0f} detik lalu). Coba lagi setelah selesai."
        print(f"\n❌ {msg}")
        notify("pipeline", msg)
        return os.EX_TEMPFAIL

    if status == "FAILED":
        code, (label, _output) = detail
        message = f"Pipeline dihentikan: {label} gagal (exit code {code})."
        print(f"\n❌ {message}")
        notify("pipeline", message)
        return code

    print("\n▶ Fase 3 — ApprovalPost (approval user)")
    code, _output = run_stage(
        "agent4_approval.py", run_id=run_id, capture_output=False, timeout=None
    )
    if code != 0:
        message = f"Pipeline dihentikan: Fase 3 — ApprovalPost (approval user) gagal (exit code {code})."
        print(f"\n❌ {message}")
        notify("pipeline", message)
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
