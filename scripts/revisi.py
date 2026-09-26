"""Revisi cepat: render ulang video yang SUDAH jadi dengan perubahan kecil, tanpa panggilan LLM.

Asal (26 Sep): revisi kecil ("ganti musik", "hapus B-roll ke-2", "font lebih tegas") dulu berarti
draf baru -- ±4 menit + 2-3 panggilan LLM dari kuota gratis 50/hari -- dan hasilnya bisa berbeda di
bagian yang TIDAK diminta berubah (naskah ditulis ulang, B-roll & lagu terpilih ulang).

Yang dijaga kode:
- brief yang dirender dipakai ulang persis; naskah hasil koreksi durasi (LLM) ikut disimpan dan
  koreksi durasi dimatikan, jadi kalimat yang sudah dilihat user tidak berubah diam-diam;
- klip B-roll yang tidak disebut user DIKUNCI (id Pexels yang sama), yang dihapus/diganti tidak
  pernah dipilih lagi; lagu dikunci kecuali user minta ganti;
- catatan milik chat lain / kedaluwarsa / tidak ada -> DITOLAK (aturan #4).
"""

import copy
import json
import os
import re
import time

from common import STATE_DIR

REVISI_DIR = os.path.join(STATE_DIR, "revisi")
BERLAKU_HARI = int(os.getenv("RUN_FILE_RETENTION_DAYS", "7"))
_ID_SAH = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class RevisiError(ValueError):
    def __init__(self, kode, pesan):
        super().__init__(pesan)
        self.kode = kode


def _path(run_id):
    if not _ID_SAH.match(str(run_id or "")):
        raise RevisiError("revisi_id_invalid", "Id video untuk revisi tidak sah.")
    return os.path.join(REVISI_DIR, f"{run_id}.json")


def ringkas_status(status):
    """Bagian render_status yang menentukan hasil dan tidak ada di brief."""
    status = status or {}
    broll = status.get("broll") or {}
    return {
        "broll": {"cara": broll.get("cara") or "sisip",
                  "dipakai": [{k: d.get(k) for k in ("id", "query", "saat_kata")}
                              for d in broll.get("dipakai") or [] if d.get("id") is not None]},
        "music": status.get("music"),
        "naskah_koreksi": status.get("naskah_koreksi"),
    }


def simpan(run_id, *, chat_id, prefix, bahan, args, brief, status, revisi_dari=None):
    os.makedirs(REVISI_DIR, exist_ok=True)
    catatan = {"run_id": run_id, "chat_id": chat_id or "", "dibuat": time.time(), "prefix": prefix,
               "bahan": list(bahan), "args": args, "brief": brief,
               "status": ringkas_status(status), "revisi_dari": revisi_dari}
    tujuan = _path(run_id)
    sementara = tujuan + ".tmp"
    with open(sementara, "w", encoding="utf-8") as f:
        json.dump(catatan, f, ensure_ascii=False)
    os.replace(sementara, tujuan)
    return tujuan


def muat(run_id, chat_id, sekarang=None):
    path = _path(run_id)
    try:
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
    except FileNotFoundError:
        raise RevisiError("revisi_tidak_ada",
                          "Catatan video ini tidak ditemukan (lebih dari "
                          f"{BERLAKU_HARI} hari, atau dibuat sebelum fitur revisi ada). "
                          "Buat lewat draf baru.") from None
    if (rec.get("chat_id") or "") != (chat_id or ""):
        raise RevisiError("revisi_chat_lain", "Video itu bukan milik chat ini.")
    if (sekarang or time.time()) - float(rec.get("dibuat") or 0) > BERLAKU_HARI * 86400:
        raise RevisiError("revisi_kedaluwarsa",
                          f"Video itu sudah lebih dari {BERLAKU_HARI} hari. Buat lewat draf baru.")
    return rec


def nomor(teks, total, label):
    """'2' / '1,3' -> {indeks 0-based}. Nomor di luar 1..total ditolak dengan pesan jelas."""
    if not teks:
        return set()
    hasil = set()
    for bagian in str(teks).replace(" ", "").split(","):
        if not bagian.isdigit():
            raise RevisiError("revisi_nomor_invalid", f"Nomor {label} '{bagian}' tidak sah.")
        n = int(bagian)
        if not 1 <= n <= total:
            ada = f"video ini memakai {total}" if total else "video ini tidak memakai"
            raise RevisiError("revisi_nomor_invalid", f"{label} ke-{n} tidak ada ({ada} {label}).")
        hasil.add(n - 1)
    return hasil


def brief_revisi(rec, *, hapus_broll=None, ganti_broll=None, naskah=None, broll_bebas=False):
    """(brief siap render, daftar perubahan untuk user). Tanpa LLM.

    broll_bebas: user meminta jumlah B-roll baru (--broll-count) -> jumlah tidak dikunci."""
    brief = copy.deepcopy(rec["brief"])
    status = rec.get("status") or {}
    perubahan = []
    koreksi = status.get("naskah_koreksi")
    if koreksi:
        brief.update({k: v for k, v in koreksi.items() if v})
    # Revisi tidak pernah menulis ulang naskah: koreksi durasi (LLM) dimatikan.
    brief["target_duration"] = None

    dipakai = (status.get("broll") or {}).get("dipakai") or []
    cara = (status.get("broll") or {}).get("cara")
    hapus = nomor(hapus_broll, len(dipakai), "B-roll")
    ganti = nomor(ganti_broll, len(dipakai), "B-roll")
    if hapus & ganti:
        raise RevisiError("revisi_nomor_invalid", "B-roll yang sama tidak bisa dihapus sekaligus diganti.")
    rev = {"dari": rec["run_id"]}
    if dipakai:
        rev["broll_pakai"] = [d for i, d in enumerate(dipakai) if i not in hapus | ganti]
        rev["broll_tolak"] = [d["id"] for i, d in enumerate(dipakai) if i in hapus | ganti]
        rev["broll_hapus"] = [d for i, d in enumerate(dipakai) if i in hapus]
        if cara != "cutaway" and not broll_bebas:
            rev["broll_jumlah"] = len(dipakai) - len(hapus)
        for i in sorted(hapus):
            perubahan.append(f"B-roll ke-{i + 1} dihapus")
        for i in sorted(ganti):
            perubahan.append(f"B-roll ke-{i + 1} diganti klip lain")
    if naskah is not None:
        if brief.get("audio_mode") != "ai":
            raise RevisiError("revisi_naskah_suara_asli",
                              "Naskah hanya bisa diubah di video ber-voice-over AI; di video suara "
                              "asli, teksnya adalah ucapanmu sendiri.")
        from draf_naskah import pakai_naskah_user
        brief = pakai_naskah_user(brief, naskah)
        perubahan.append("naskah diganti dengan tulisanmu")
    brief["revisi"] = rev
    return brief, perubahan
