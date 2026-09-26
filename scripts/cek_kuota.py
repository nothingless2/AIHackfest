"""Cek kuota model gratis OpenRouter SEKARANG, tanpa memakai kuota.

Keluaran: satu baris JSON. Dipakai agent Hermes sebelum menjawab "bisa buat konten sekarang?".

Asal: 25-26 Sep bot menjawab dua kali "kuota harian habis, reset besok" dari riwayat obrolan
(error 24 Sep, key lama) tanpa menjalankan apa pun -- padahal kuota key baru terpakai 1 dari 50.
Endpoint /key hanya membaca status key; tidak dihitung sebagai request model.
"""

import datetime as dt
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402  (memuat .env)

WIB = dt.timezone(dt.timedelta(hours=7))


def reset_berikutnya(sekarang):
    """Kuota harian model gratis OpenRouter direset 00:00 UTC (07:00 WIB)."""
    besok = (sekarang.astimezone(dt.timezone.utc) + dt.timedelta(days=1)).date()
    return dt.datetime.combine(besok, dt.time(0, 0), dt.timezone.utc).astimezone(WIB)


def ambil_status(basis, kunci, timeout=15):
    req = urllib.request.Request(basis.rstrip("/") + "/key",
                                 headers={"Authorization": f"Bearer {kunci}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def cek(sekarang=None, ambil=ambil_status):
    sekarang = sekarang or dt.datetime.now(WIB)
    hasil = {"waktu_cek": sekarang.astimezone(WIB).strftime("%d/%m/%Y %H:%M WIB"),
             "model": common.rantai_model(common.LLM_MODEL)}
    basis, kunci = common.OPENAI_BASE_URL or "", common.OPENAI_API_KEY
    if "openrouter.ai" not in basis:
        return {**hasil, "ok": False, "alasan": "bukan_openrouter"}
    if not kunci:
        return {**hasil, "ok": False, "alasan": "key_kosong"}
    try:
        data = ambil(basis, kunci).get("data") or {}
    except Exception as e:
        # Gagal mengecek BUKAN berarti kuota habis (aturan #7).
        return {**hasil, "ok": False, "alasan": "cek_gagal", "detail": str(e)[:200]}
    harian = data.get("free_model_daily_requests")
    if not isinstance(harian, dict) or "remaining" not in harian:
        return {**hasil, "ok": False, "alasan": "format_tak_dikenal"}
    sisa = harian.get("remaining")
    hasil.update(ok=True, kuota_gratis={"terpakai": harian.get("used"), "batas": harian.get("limit"),
                                        "sisa": sisa},
                 bisa_jalan=bool(sisa and sisa > 0),
                 reset=reset_berikutnya(sekarang).strftime("%d/%m/%Y %H:%M WIB"),
                 catatan=("Kuota ada. Model gratis yang bisa melihat video kadang penuh sesaat; "
                          "itu baru diketahui saat dijalankan, dan draf akan menyebutnya."))
    return hasil


if __name__ == "__main__":
    print(json.dumps(cek(), ensure_ascii=False))
