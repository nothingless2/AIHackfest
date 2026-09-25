"""Log event JSONL untuk siklus hidup tiap run (1a: run_started/run_rejected/
run_finished/delivered/delivery_failed; diperluas 1b & Commit 3 dengan event
lain). Satu file, satu titik baca untuk laporan gabungan nanti.
"""

import json

from common import RUN_LOG_PATH, ensure_dirs, now_iso


def log_event(event, run_id, chat_id=None, **fields):
    """Append 1 baris JSON. TIDAK PERNAH melempar exception — kegagalan tulis
    cuma di-print sebagai warning, tidak boleh menggagalkan pipeline."""
    try:
        ensure_dirs()
        entry = {"ts": now_iso(), "event": event, "run_id": run_id, "chat_id": chat_id, **fields}
        with open(RUN_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[warn] run_log: gagal mencatat event '{event}' (run_id={run_id}): {e}")
