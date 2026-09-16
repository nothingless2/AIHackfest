"""Orkestrator pipeline 5 agent.

Urutan: ContentInsight (konteks) -> TrendAnalysts + BrainIdea -> ContentMakers -> ApprovalPost.

Berbeda dari versi lama yang memakai os.system tanpa cek hasil: di sini setiap tahap
diperiksa exit code-nya dan pipeline BERHENTI kalau ada tahap kritis yang gagal,
supaya tidak lanjut memproses data basi.
"""

import os
import subprocess
import sys

from common import PROJECT_ROOT, ensure_dirs, log_error, notify

SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")

# (file, label, kritis?) — tahap non-kritis boleh gagal tanpa menghentikan pipeline.
STAGES = [
    ("agent5_insight.py", "Fase 0 — ContentInsight (konteks performa)", False),
    ("agent1_2_brief.py", "Fase 1 — TrendAnalysts + BrainIdea (riset & brief)", True),
    ("agent3_render.py", "Fase 2 — ContentMakers (render video)", True),
    ("agent4_approval.py", "Fase 3 — ApprovalPost (approval user)", True),
]


def run_stage(script_name):
    """Jalankan satu tahap sebagai subprocess, kembalikan exit code-nya."""
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, script_name)],
        cwd=PROJECT_ROOT,
    ).returncode


def main():
    ensure_dirs()
    print("=" * 60)
    print("🚀 PIPELINE 5 AGENT CONTENT FACTORY")
    print("=" * 60)

    for script_name, label, critical in STAGES:
        print(f"\n▶ {label}")
        code = run_stage(script_name)

        if code == 0:
            continue

        if critical:
            message = f"Pipeline dihentikan: {label} gagal (exit code {code})."
            print(f"\n❌ {message}")
            notify("pipeline", message)
            return code

        print(f"⚠️ {label} gagal (exit {code}), tahap ini tidak kritis — pipeline lanjut.")

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
