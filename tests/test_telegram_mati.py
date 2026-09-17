"""Test penjaga: memastikan fixture autouse di conftest benar-benar mematikan
Telegram. Artefak tes pernah dua kali bocor ke Telegram user; test ini yang
menjaga supaya jalur itu tertutup dan tetap tertutup."""

import os
import subprocess
import sys


def test_telegram_tidak_terkonfigurasi_selama_test():
    import common

    assert common.TELEGRAM_BOT_TOKEN == ""
    assert common.TELEGRAM_CHAT_ID == ""
    assert common.telegram_configured() is False


def test_send_video_menolak_kirim_dan_tidak_melempar(tmp_path):
    """send_video harus mengembalikan False (bukan menembak API) saat Telegram mati."""
    import common

    dummy = tmp_path / "dummy.mp4"
    dummy.write_bytes(b"bukan video sungguhan")
    assert common.send_video("caption tes", str(dummy)) is False


def test_anak_proses_mewarisi_env_telegram_kosong():
    """Test lain men-spawn subprocess; pastikan anak juga tidak punya kredensial."""
    code = (
        "import os,sys;"
        "sys.path.insert(0, sys.argv[1]);"
        "import common;"
        "print('OK' if not common.telegram_configured() else 'BOCOR')"
    )
    scripts_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, scripts_dir], capture_output=True, text=True
    )
    assert out.stdout.strip() == "OK", f"anak proses masih punya kredensial: {out.stdout!r}"
