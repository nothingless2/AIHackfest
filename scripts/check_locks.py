"""Periksa apakah lock render sedang dipegang, SEBELUM me-restart gateway Hermes.

Keluar 0 kalau bebas, 1 kalau dipegang (dengan detail pemegangnya).

Render/draf berjalan sebagai proses latar belakang milik gateway (hermes-gateway.service).
Restart service mengirim sinyal ke seluruh cgroup-nya, jadi render yang sedang berjalan ikut
mati -- pernah terjadi: satu render hilang satu langkah sebelum selesai, tanpa run_finished dan
tanpa pesan ke user.
"""

import fcntl
import json
import os
import sys
import time

from run_lock import RENDER_LOCK_PATH


def lock_holder(path):
    """(dipegang, info). `info` dibaca best-effort — lock tetap dilaporkan
    dipegang walau isinya tidak terbaca."""
    if not os.path.exists(path):
        return False, None
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False, None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False, None
    except OSError:
        try:
            with open(path, encoding="utf-8") as f:
                return True, json.load(f)
        except Exception:
            return True, None
    finally:
        os.close(fd)


def main():
    sibuk = []
    for nama, path in (("render", RENDER_LOCK_PATH),):
        dipegang, info = lock_holder(path)
        if not dipegang:
            continue
        info = info or {}
        umur = ""
        if info.get("started_at_epoch"):
            umur = f", berjalan {time.time() - info['started_at_epoch']:.0f} detik"
        sibuk.append(
            f"  lock {nama.upper()} dipegang — run_id={info.get('holder_id', '?')} "
            f"pid={info.get('pid', '?')}{umur}"
        )

    if not sibuk:
        print("Lock render bebas.")
        return 0

    print("DITOLAK: ada pekerjaan yang sedang berjalan.")
    print("\n".join(sibuk))
    print(
        "\nRestart gateway sekarang berisiko membunuhnya (restart service mengirim\n"
        "sinyal ke seluruh cgroup-nya). Tunggu sampai render selesai."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
