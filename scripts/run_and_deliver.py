"""Jalankan pipeline sampai selesai lalu kirim hasilnya sendiri ke Telegram.

Dipakai untuk mode asinkron: plugin OpenClaw memanggil script ini secara detached
lalu langsung kembali, supaya render panjang (2-4 menit) tidak dibunuh oleh batas
waktu eksekusi tool. Progress dan video dikirim langsung dari sini.

Sengaja TIDAK melakukan polling getUpdates: balasan APPROVE/REVISI user ditangani
oleh gateway OpenClaw di chat, jadi tidak ada rebutan antrian update Telegram.
"""

import os
import subprocess
import sys

from common import (
    BRIEF_PATH,
    DRAFT_VIDEO_PATH,
    PROJECT_ROOT,
    ensure_dirs,
    log_error,
    notify,
    read_json,
    send_video,
)

SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")

STAGES = [
    ("agent5_insight.py", "ContentInsight", False),
    ("agent1_2_brief.py", "TrendAnalysts & BrainIdea", True),
    ("agent3_render.py", "ContentMakers", True),
]


def run_stage(script_name):
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, script_name)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode, (result.stderr or result.stdout or "").strip()


def main():
    ensure_dirs()

    # Buang sisa file rusak dari run sebelumnya supaya tidak terkirim tidak sengaja.
    if os.path.exists(DRAFT_VIDEO_PATH):
        os.remove(DRAFT_VIDEO_PATH)

    for script_name, label, critical in STAGES:
        code, output = run_stage(script_name)
        if code == 0:
            continue

        if critical:
            tail = "\n".join(output.splitlines()[-4:])
            notify("pipeline", f"gagal di tahap {label}.\n{tail}")
            return code

        print(f"[warn] {label} gagal (exit {code}), tahap non-kritis — lanjut.")

    if not os.path.exists(DRAFT_VIDEO_PATH):
        notify("pipeline", "render selesai tapi file video tidak ditemukan.")
        return 1

    brief = read_json(BRIEF_PATH, {}) or {}
    judul = brief.get("judul", "Untitled")
    hashtags = " ".join(brief.get("hashtags", []))

    caption = (
        "✅ ApprovalPost\n\n"
        "Draf Konten Siap Direview!\n"
        f"Judul: {judul}\n"
        f"{hashtags}\n\n"
        "Balas APPROVE untuk menyetujui atau REVISI untuk perbaikan."
    )

    if not send_video(caption, DRAFT_VIDEO_PATH):
        notify("pipeline", f"video selesai tapi gagal dikirim. File ada di {DRAFT_VIDEO_PATH}")
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log_error("run_and_deliver failure", e)
        notify("pipeline", f"pipeline gagal — {e}")
        sys.exit(1)
