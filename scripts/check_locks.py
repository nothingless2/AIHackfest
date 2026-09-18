"""Periksa apakah lock render/approval sedang dipegang.

Keluar 0 kalau SEMUA bebas, 1 kalau ada yang dipegang (dengan detail pemegangnya).

Dipakai install_plugin.sh sebelum me-restart gateway. Alasannya konkret dan mahal:
gateway berjalan sebagai service systemd dengan KillMode=mixed, jadi restart
mengirim SIGKILL ke seluruh cgroup-nya. Sebelum perbaikan spawn-scope, itu
membunuh render yang sedang berjalan — satu render nyata hilang persis begitu,
satu langkah sebelum selesai, tanpa run_finished dan tanpa pesan ke user.

Tetap berguna setelah perbaikan itu: render yang berjalan lewat spawn cadangan
(kalau systemd-run tidak tersedia) masih rentan, dan approval CLI yang sedang
menunggu balasan user tetap akan terputus oleh restart.
"""

import fcntl
import json
import os
import sys
import time

from run_lock import APPROVAL_LOCK_PATH, RENDER_LOCK_PATH


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
    for nama, path in (("render", RENDER_LOCK_PATH), ("approval", APPROVAL_LOCK_PATH)):
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
        print("Lock render & approval bebas.")
        return 0

    print("DITOLAK: ada pekerjaan yang sedang berjalan.")
    print("\n".join(sibuk))
    print(
        "\nRestart gateway sekarang berisiko membunuhnya (systemd KillMode=mixed\n"
        "mengirim SIGKILL ke seluruh cgroup service). Tunggu sampai selesai, atau\n"
        "paksa dengan SKIP_LOCK_CHECK=1 kalau kamu memang bermaksud menghentikannya."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
