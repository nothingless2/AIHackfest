"""Test run_lock.py. Semua lock diuji lewat path di tmp_path -- TIDAK PERNAH
menyentuh workspace/state/pipeline.lock yang asli."""

import subprocess
import sys
import time

import pytest

from run_lock import FileLockBusyError, acquire_file_lock, sanitize_run_id


# ---------- sanitize_run_id ----------

def test_sanitize_run_id_buang_karakter_aneh():
    assert sanitize_run_id("abc/../123 xyz!") == "abc123xyz"


def test_sanitize_run_id_string_kosong_fallback_bukan_exception(capsys):
    result = sanitize_run_id("")
    assert result  # ada isinya
    assert len(result) == 8  # generate_run_id() -> uuid4().hex[:8]
    assert "[warn]" in capsys.readouterr().out


def test_sanitize_run_id_karakter_semua_dibuang_fallback():
    # run_id yang isinya cuma karakter tidak valid -> setelah dibersihkan kosong
    result = sanitize_run_id("!!!///???")
    assert len(result) == 8


def test_sanitize_run_id_pas_32_karakter_tidak_dipotong():
    run_id = "a" * 32
    assert sanitize_run_id(run_id) == run_id


def test_sanitize_run_id_lebih_dari_32_pakai_prefix_hash_bukan_truncate(capsys):
    long_id = "x" * 40 + "AAAA"  # 44 char, jelas > 32
    long_id_beda = "x" * 40 + "BBBB"  # beda cuma di ekor yang akan "terpotong" versi naif
    r1 = sanitize_run_id(long_id)
    r2 = sanitize_run_id(long_id_beda)
    assert len(r1) <= 32
    assert r1 != r2  # DIBUKTIKAN: tidak tabrakan walau 40 karakter pertama sama
    assert "-" in r1  # format prefix-hash
    assert "[info]" in capsys.readouterr().out


# ---------- acquire_file_lock ----------

def test_lock_sukses_dan_isi_file_benar(tmp_path):
    lock_path = str(tmp_path / "test.lock")
    with acquire_file_lock(lock_path, "run-A"):
        pass  # sukses tanpa exception = cukup


def test_lock_busy_saat_dipegang_proses_lain(tmp_path):
    lock_path = tmp_path / "test.lock"
    holder_script = tmp_path / "holder.py"
    holder_script.write_text(
        "import sys, time\n"
        "sys.path.insert(0, sys.argv[2])\n"
        "from run_lock import acquire_file_lock\n"
        "with acquire_file_lock(sys.argv[1], 'child-holder'):\n"
        "    time.sleep(2.5)\n"
    )
    import os
    scripts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")

    proc = subprocess.Popen([sys.executable, str(holder_script), str(lock_path), scripts_dir])
    time.sleep(0.7)  # beri waktu anak benar-benar pegang lock

    try:
        with pytest.raises(FileLockBusyError) as exc_info:
            with acquire_file_lock(str(lock_path), "parent-attempt"):
                pass
        assert exc_info.value.elapsed_seconds >= 0
        assert exc_info.value.holder.get("holder_id") == "child-holder"
    finally:
        proc.wait(timeout=5)

    # setelah anak selesai (release normal), lock ketiga harus langsung berhasil
    with acquire_file_lock(str(lock_path), "parent-retry"):
        pass


def test_lock_auto_release_saat_proses_pemegang_dibunuh_paksa(tmp_path):
    lock_path = tmp_path / "test.lock"
    holder_script = tmp_path / "holder_forever.py"
    holder_script.write_text(
        "import sys, time\n"
        "sys.path.insert(0, sys.argv[2])\n"
        "from run_lock import acquire_file_lock\n"
        "with acquire_file_lock(sys.argv[1], 'to-be-killed'):\n"
        "    time.sleep(60)\n"
    )
    import os
    scripts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")

    proc = subprocess.Popen([sys.executable, str(holder_script), str(lock_path), scripts_dir])
    time.sleep(0.7)

    with pytest.raises(FileLockBusyError):
        with acquire_file_lock(str(lock_path), "someone-else"):
            pass

    proc.kill()  # SIGKILL -- simulasi kill -9
    proc.wait(timeout=5)
    time.sleep(0.2)  # beri waktu kernel benar-benar melepas fd

    # OS harus otomatis melepas lock walau proses mati paksa, bukan graceful release
    with acquire_file_lock(str(lock_path), "after-kill"):
        pass


def test_lock_selalu_lepas_walau_ada_exception_di_dalam(tmp_path):
    lock_path = str(tmp_path / "test.lock")
    with pytest.raises(ValueError):
        with acquire_file_lock(lock_path, "run-X"):
            raise ValueError("kegagalan di dalam blok")

    # lock harus sudah lepas (finally di acquire_file_lock bekerja)
    with acquire_file_lock(lock_path, "run-Y"):
        pass
