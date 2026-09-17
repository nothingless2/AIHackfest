"""Agent 3 (ContentMakers): render brief jadi video vertikal 9:16 + voice-over."""

import os
import sys

from common import (
    BRIEF_PATH,
    DRAFT_VIDEO_PATH,
    ensure_dirs,
    log_error,
    notify,
    PROJECT_ROOT,
    read_json,
    RENDER_STATUS_PATH,
    resolve_chat_id,
    write_json,
)

sys.path.insert(0, os.path.join(PROJECT_ROOT, "skills", "video_generator"))
from auto_render import render_from_agent_script  # noqa: E402


def run():
    ensure_dirs()

    brief = read_json(BRIEF_PATH)
    if not brief:
        raise FileNotFoundError(
            f"{BRIEF_PATH} tidak ada. Jalankan agent1_2_brief.py (Agent 1 & 2) dahulu."
        )

    chat_id = resolve_chat_id()
    assets = brief.get("media_assets", [])
    notify(
        "contentmakers",
        f"merender video 9:16 dari {len(assets)} bahan + voice-over AI...",
        chat_id=chat_id,
    )

    render_from_agent_script(
        json_path=BRIEF_PATH,
        image_path="",  # tidak dipakai: bahan visual datang dari media_assets
        output_video=DRAFT_VIDEO_PATH,
    )

    status = read_json(RENDER_STATUS_PATH, {})
    if status.get("status") != "SUCCESS" or not os.path.exists(DRAFT_VIDEO_PATH):
        raise RuntimeError(f"Render tidak menghasilkan video valid. Status: {status}")

    notify(
        "contentmakers",
        f"draft selesai ({status.get('duration', 0):.1f} detik) — diserahkan ke ApprovalPost.",
        chat_id=chat_id,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as e:
        log_error("Agent 3 (render) failure", e)
        write_json(RENDER_STATUS_PATH, {"status": "FAILED", "error": str(e)})
        notify("contentmakers", f"gagal merender video — {e}", chat_id=resolve_chat_id())
        sys.exit(1)
