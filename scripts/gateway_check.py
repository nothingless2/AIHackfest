"""Deteksi gateway OpenClaw yang sedang jalan.

Diekstrak dari agent4_approval.py supaya pipeline.py bisa memakainya juga tanpa
duplikasi: guard perlu dijalankan di DUA tempat, dan alasannya berbeda.

- Di awal pipeline.py: mencegah run CLI membakar waktu + kredit GPT-4o lalu baru
  ditolak di tahap approval di ujung.
- Di agent4_approval.py: tetap ada untuk pemakaian standalone
  (`python3 scripts/agent4_approval.py` langsung).
"""

import os
import socket

GATEWAY_HOST = os.getenv("OPENCLAW_GATEWAY_HOST", "127.0.0.1")
GATEWAY_PORT = int(os.getenv("OPENCLAW_GATEWAY_PORT", "18789"))

PESAN = (
    "Gateway OpenClaw terdeteksi jalan di port {port} dan memakai bot token yang SAMA.\n"
    "    Telegram hanya punya SATU antrian getUpdates per bot: siapa pun yang fetch\n"
    "    duluan menghabiskan antrian milik yang lain, sehingga balasan APPROVE/REVISI\n"
    "    user bisa hilang.\n"
    "    Pilihan: hentikan gateway (`openclaw gateway stop`) untuk approval CLI, ATAU\n"
    "    pakai jalur plugin OpenClaw (tool content_factory_run)."
)


def gateway_is_running():
    """True kalau ada yang mendengarkan di port gateway."""
    try:
        with socket.create_connection((GATEWAY_HOST, GATEWAY_PORT), timeout=2):
            return True
    except OSError:
        return False


def warn_if_gateway_polling():
    """Cetak peringatan kalau gateway jalan. Return True kalau jalan."""
    if gateway_is_running():
        print(f"⚠️  PERINGATAN: {PESAN.format(port=GATEWAY_PORT)}")
        return True
    return False
