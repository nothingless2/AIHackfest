"""Orkestrator pipeline 5 agent.

Urutan: ContentInsight (konteks) -> TrendAnalysts + BrainIdea -> ContentMakers -> ApprovalPost.

Berbeda dari versi lama yang memakai os.system tanpa cek hasil: di sini setiap tahap
diperiksa exit code-nya dan pipeline BERHENTI kalau ada tahap kritis yang gagal,
supaya tidak lanjut memproses data basi.
"""

import sys

from common import ensure_dirs, log_error, notify
from orchestrator import run_stages

# (file, label, kritis?) — tahap non-kritis boleh gagal tanpa menghentikan pipeline.
STAGES = [
    ("agent5_insight.py", "Fase 0 — ContentInsight (konteks performa)", False),
    ("agent1_2_brief.py", "Fase 1 — TrendAnalysts + BrainIdea (riset & brief)", True),
    ("agent3_render.py", "Fase 2 — ContentMakers (render video)", True),
    ("agent4_approval.py", "Fase 3 — ApprovalPost (approval user)", True),
]


def _on_noncritical_failure(label, code):
    print(f"⚠️ {label} gagal (exit {code}), tahap ini tidak kritis — pipeline lanjut.")


def main():
    ensure_dirs()
    print("=" * 60)
    print("🚀 PIPELINE 5 AGENT CONTENT FACTORY")
    print("=" * 60)

    code, failure = run_stages(
        STAGES,
        capture_output=False,
        on_noncritical_failure=_on_noncritical_failure,
        on_stage_start=lambda label: print(f"\n▶ {label}"),
    )
    if code != 0:
        label, _output = failure
        message = f"Pipeline dihentikan: {label} gagal (exit code {code})."
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
