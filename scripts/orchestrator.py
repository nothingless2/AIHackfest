"""Orkestrasi bersama pipeline: jalankan tahap sebagai subprocess dengan batas
waktu proses-grup (killpg saat timeout/sinyal), lock render eksklusif, dan
event log siklus-hidup run. Dipakai scripts/pipeline.py dan
scripts/run_and_deliver.py.
"""

import os
import shutil
import signal
import subprocess
import sys
import time

from common import (
    BRIEF_PATH,
    DRAFT_THUMB_PATH,
    DRAFT_VIDEO_PATH,
    PROJECT_ROOT,
    brief_path_for_run,
    draft_thumb_path_for_run,
    draft_video_path_for_run,
)
from run_lock import acquire_render_lock
from retention import clear_render_workspace
from run_log import log_event

SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")

# (script, label, kritis?, timeout_detik). Timeout dihitung dari rumus:
#   timeout_tahap > max_attempts x timeout_per_percobaan + total_backoff_terburuk + margin
# (lihat plan Fase 1). agent1_2_brief/agent3_render sudah diberi headroom untuk
# retry yang ditambahkan di Commit 2 (belum ada di 1a, tapi angka sudah konsisten
# supaya tidak perlu direvisi lagi nanti).
CORE_STAGES = [
    # 90 detik (naik dari 30): Fase 0 kini memanggil jaringan -- 1 panggilan vision
    # kecil utk menurunkan kata kunci, lalu YouTube API + RSS Google Trends.
    # Tetap NON-KRITIS: kalau sumber tren mati atau lambat, pipeline harus lanjut
    # tanpa data tren, bukan berhenti.
    ("agent5_insight.py", "ContentInsight", False, 90),
    # 450 detik (naik dari 400). Dihitung ulang setelah seleksi konten menambah
    # SATU panggilan LLM (opsional: gagal = lanjut tanpa seleksi). Terburuk:
    #   transkripsi (anggaran KERAS di transcribe.py)      = 180
    #   ekstraksi frame untuk vision (lokal)               =  15
    #   panggilan brief 3 percobaan x 45 dtk + backoff     = 138
    #   seleksi konten 1 percobaan x 45 dtk                =  45
    #   -------------------------------------------------------
    #                                                        378  -> margin ~19%
    ("agent1_2_brief.py", "TrendAnalysts & BrainIdea", True, 450),
    # 360 detik (naik dari 300) — dihitung ulang setelah 2.1 menambah kemungkinan
    # TTS KEDUA. Anggaran terburuk:
    #   TTS  3 percobaan x 30 dtk + backoff 3 dtk          =  93
    #   koreksi durasi 1 panggilan LLM x 25 dtk            =  25
    #   TTS kedua (naskah hasil koreksi)                   =  93
    #   render terberat yang diukur (16:9 blur)            =  67
    #   cover 2 panggilan ffmpeg                           =   3
    #   teks animasi Remotion (batas KERAS, lalu cadangan)  = 120
    #   -------------------------------------------------------
    #                                                        401  -> margin ~20%
    # (480 naik dari 360, 24 Sep: Remotion. Terukur 13 dtk untuk satu judul 9 dtk;
    #  120 adalah batas TEXT_ANIMATION_TIMEOUT, bukan perkiraan.)
    ("agent3_render.py", "ContentMakers", True, 480),
]

# Draf naskah (scripts/draf_naskah.py): ContentInsight + brief saja, TANPA render. Brief draf
# menulis 2 varian dan memeriksa masing-masing (maksimal 2 tulis ulang, bukan 1):
#   anggaran brief lama 378 + satu tulis ulang tambahan 60 dtk = 438 -> 540 (margin ~19%).
DRAFT_STAGES = [CORE_STAGES[0], ("agent1_2_brief.py", "TrendAnalysts & BrainIdea (draf)", True, 540)]
# Render dari draf yang disetujui user: brief TIDAK dibuat ulang.
RENDER_STAGES = [CORE_STAGES[2]]

_active_proc = None  # ditulis run_stage() tepat setelah Popen; dibaca signal handler


def _handle_terminate(signum, frame):
    """SIGINT (Ctrl+C) / SIGTERM (`kill` biasa) -> bunuh process group tahap
    yang sedang aktif supaya ffmpeg/edge-tts cucu tidak jadi yatim.

    kill -9 (SIGKILL) TIDAK bisa ditangkap handler apa pun (batasan OS, bukan
    kode ini) -- risiko sisa yang didokumentasikan di plan, bukan bug yang
    belum diperbaiki. flock tetap ter-auto-release dengan benar walau begitu.
    """
    if _active_proc is not None and _active_proc.poll() is None:
        try:
            os.killpg(os.getpgid(_active_proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    sys.exit(128 + signum)


def install_signal_handlers():
    signal.signal(signal.SIGINT, _handle_terminate)
    signal.signal(signal.SIGTERM, _handle_terminate)


def run_stage(script_name, *, run_id, capture_output, timeout=None):
    """Jalankan satu tahap sebagai subprocess dalam process group baru sendiri
    (start_new_session=True), kembalikan (exit_code, output).

    timeout=None: tunggu sampai selesai tanpa batas (dipakai untuk
    agent4_approval.py -- lock/timeout approval sendiri ada di 1b).
    timeout=N: proses DAN SELURUH process group-nya (termasuk ffmpeg/edge-tts
    yang di-spawn di dalamnya) dibunuh paksa kalau melebihi N detik --
    diverifikasi langsung bahwa os.killpg benar-benar menjangkau proses cucu.
    """
    global _active_proc
    env = {**os.environ, "CONTENT_FACTORY_RUN_ID": run_id}
    kwargs = dict(cwd=PROJECT_ROOT, env=env, start_new_session=True)
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    proc = subprocess.Popen([sys.executable, os.path.join(SCRIPTS_DIR, script_name)], **kwargs)
    _active_proc = proc
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # bunuh SELURUH process group
        proc.communicate()  # reap, kuras pipe sisa
        return 124, f"[timeout] tahap melebihi {timeout} detik, proses & anak-anaknya dihentikan paksa."
    finally:
        _active_proc = None

    output = (stderr or stdout or "").strip() if capture_output else ""
    return proc.returncode, output


def run_stages(stages, *, run_id, capture_output, on_noncritical_failure, on_stage_start=None):
    """Jalankan `stages` (list of (script_name, label, critical, timeout))
    berurutan.

    on_stage_start(label), kalau diberikan, dipanggil TEPAT SEBELUM tiap tahap
    dijalankan (perlu untuk pipeline.py: cetak "> {label}" interleaved dengan
    output live tahap itu, bukan semua header dicetak di depan).

    Tahap non-kritis yang gagal memanggil on_noncritical_failure(label, code)
    lalu lanjut -- pesan persis pemanggilan ini sengaja diserahkan ke caller.

    Return (0, None) kalau semua tahap kritis sukses.
    Return (code, (label, output)) di tahap kritis pertama yang gagal.
    """
    for script_name, label, critical, timeout in stages:
        if on_stage_start:
            on_stage_start(label)
        code, output = run_stage(script_name, run_id=run_id, capture_output=capture_output, timeout=timeout)
        if code == 0:
            continue
        if critical:
            return code, (label, output)
        on_noncritical_failure(label, code)
    return 0, None


def run_core_stages_locked(
    run_id, *,
    chat_id, capture_output, on_stage_start=None, on_noncritical_failure=None,
    stages=None, salin_video=True, sebelum_tahap=None,
):
    """Jalankan CORE_STAGES di dalam lock render. Kalau sukses, salin
    DRAFT_VIDEO_PATH/BRIEF_PATH ke path ber-run_id SEBELUM lock dilepas --
    supaya apa pun yang membaca setelah lock lepas (delivery, approval-wait)
    tidak pernah membaca file yang bisa tertimpa run berikutnya.

    Return ("SUCCESS", None) atau ("FAILED", (code, (label, output))).
    run_lock.FileLockBusyError menjalar ke pemanggil (TIDAK ditangkap di sini)
    kalau lock sedang dipegang proses lain -- caller yang memutuskan pesan &
    exit code untuk kasus itu.

    stages: daftar tahap (bawaan CORE_STAGES). salin_video=False untuk tahap tanpa render
    (draf): hanya brief yang disalin. sebelum_tahap(): dipanggil DI DALAM lock, setelah
    workspace dibersihkan -- tempat menulis brief draf & mengklaim draf secara atomik.
    Pengecualiannya menjalar ke pemanggil.
    """
    if on_noncritical_failure is None:
        def on_noncritical_failure(label, code):
            print(f"[warn] {label} gagal (exit {code}), tahap non-kritis — lanjut.")

    with acquire_render_lock(run_id):
        log_event("run_started", run_id, chat_id=chat_id)
        # DI DALAM lock: buang sisa file kerja render yang mati di tengah, supaya
        # render ini mulai dari keadaan bersih dan tidak ada draft lama yang bisa
        # terkirim tidak sengaja. Di luar lock, ini akan menghapus file milik
        # render lain yang sedang berjalan.
        clear_render_workspace()
        t0 = time.time()
        status = "ERROR"
        try:
            if sebelum_tahap:
                sebelum_tahap()
            code, failure = run_stages(
                CORE_STAGES if stages is None else stages,
                run_id=run_id,
                capture_output=capture_output,
                on_noncritical_failure=on_noncritical_failure,
                on_stage_start=on_stage_start,
            )
            if code == 0:
                if salin_video:
                    shutil.copy2(DRAFT_VIDEO_PATH, draft_video_path_for_run(run_id))
                shutil.copy2(BRIEF_PATH, brief_path_for_run(run_id))
                # Cover bersifat tambahan: ketiadaannya TIDAK menggagalkan run
                # yang videonya sudah jadi. Disalin di dalam lock, sama seperti
                # video, supaya tidak pernah tertimpa run berikutnya.
                if salin_video and os.path.exists(DRAFT_THUMB_PATH):
                    shutil.copy2(DRAFT_THUMB_PATH, draft_thumb_path_for_run(run_id))
                status = "SUCCESS"
                return status, None
            status = "FAILED"
            return status, (code, failure)
        finally:
            log_event(
                "run_finished",
                run_id,
                chat_id=chat_id,
                status=status,
                duration_seconds=time.time() - t0,
            )
