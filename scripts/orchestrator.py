"""Orkestrasi bersama: jalankan daftar tahap sebagai subprocess, tangani
kritis/non-kritis. Dipakai scripts/pipeline.py dan scripts/run_and_deliver.py —
diekstrak dari duplikasi identik di kedua file.

Refactor murni (Commit 0, Fase 1 reliability/ops): tidak ada perubahan
perilaku dari versi sebelum file ini ada. Pesan/wording tetap milik masing-
masing caller (beda antara pipeline.py dan run_and_deliver.py), supaya output
CLI/Telegram tidak berubah sedikit pun dibanding sebelum refactor.
"""

import os
import subprocess
import sys

from common import PROJECT_ROOT

SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")


def run_stage(script_name, *, capture_output):
    """Jalankan satu tahap sebagai subprocess, kembalikan (exit_code, output).

    capture_output=True: tangkap stdout/stderr, `output` = stderr atau stdout
    (mana yang ada), dipotong whitespace — dipakai run_and_deliver.py untuk
    membangun tail pesan Telegram.
    capture_output=False: output streaming langsung ke terminal (perilaku CLI
    interaktif pipeline.py tidak berubah), `output` selalu string kosong.
    """
    if capture_output:
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, script_name)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        output = (result.stderr or result.stdout or "").strip()
    else:
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, script_name)],
            cwd=PROJECT_ROOT,
        )
        output = ""
    return result.returncode, output


def run_stages(stages, *, capture_output, on_noncritical_failure, on_stage_start=None):
    """Jalankan `stages` (list of (script_name, label, critical)) berurutan.

    on_stage_start(label), kalau diberikan, dipanggil TEPAT SEBELUM tiap tahap
    dijalankan (perlu untuk pipeline.py: cetak "▶ {label}" interleaved dengan
    output live tahap itu, bukan semua header dicetak di depan).

    Tahap non-kritis yang gagal memanggil on_noncritical_failure(label, code)
    lalu lanjut — pesan persis pemanggilan ini sengaja diserahkan ke caller
    (beda wording antara pipeline.py dan run_and_deliver.py di kode asli,
    dipertahankan di sini demi "tanpa perubahan perilaku").

    Return (0, None) kalau semua tahap kritis sukses.
    Return (code, (label, output)) di tahap kritis pertama yang gagal.
    """
    for script_name, label, critical in stages:
        if on_stage_start:
            on_stage_start(label)
        code, output = run_stage(script_name, capture_output=capture_output)
        if code == 0:
            continue
        if critical:
            return code, (label, output)
        on_noncritical_failure(label, code)
    return 0, None
