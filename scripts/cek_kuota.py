"""Cek kuota model gratis OpenRouter SEKARANG, tanpa memakai kuota.

Keluaran: satu baris JSON. Dipakai agent Hermes sebelum menjawab "bisa buat konten sekarang?".

Asal: 25-26 Sep bot menjawab dua kali "kuota harian habis, reset besok" dari riwayat obrolan
(error 24 Sep, key lama) tanpa menjalankan apa pun -- padahal kuota key baru terpakai 1 dari 50.
Endpoint /key hanya membaca status key; tidak dihitung sebagai request model.

30 Sep: penyedia pindah ke router lokal, yang TIDAK punya /key -- pemeriksa lama selalu
menjawab "bukan_openrouter", jadi agent kembali buta. Di router, satu-satunya sumber angka
yang jujur adalah header rate-limit di badan error 429. Jadi dicoba satu permintaan
sekecil mungkin (1 token) dan hasilnya dibaca.
"""

import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402  (memuat .env)

WIB = dt.timezone(dt.timedelta(hours=7))

# Model ":free" mana pun mewakili jatah harian akun (jatahnya bersama).
MODEL_GRATIS_UJI = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"


def reset_berikutnya(sekarang):
    """Kuota harian model gratis OpenRouter direset 00:00 UTC (07:00 WIB)."""
    besok = (sekarang.astimezone(dt.timezone.utc) + dt.timedelta(days=1)).date()
    return dt.datetime.combine(besok, dt.time(0, 0), dt.timezone.utc).astimezone(WIB)


def ambil_status(basis, kunci, timeout=15):
    req = urllib.request.Request(basis.rstrip("/") + "/key",
                                 headers={"Authorization": f"Bearer {kunci}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# Batas harian model ":free" OpenRouter melekat pada AKUN, bukan model: satu jatah dipakai
# bersama semua model ":free". Terukur 30 Sep -- nemotron, qwen, gemma, dan 3 model ":free"
# lain menjawab 429 identik dalam 3 detik. Karena itu mengganti antar model ":free" tidak
# menambah ketahanan sama sekali; yang menolong hanya model di luar jatah itu.
POLA_DALAM = re.compile(r"\[(\d{3})\]:\s*(\{.*)", re.S)


def buka_bungkus(pesan):
    """Pesan error asli dari balik bungkus router.

    Router membungkus error penyedia sebagai STRING di dalam JSON, jadi JSON di dalamnya
    ter-escape. Lapisan luar harus dibuka dulu; tanpa ini raw_decode kena '{\\"error\\"'
    dan selalu gagal (terukur 30 Sep: pemeriksa menjawab "format_tak_dikenal" padahal
    angka batasnya ada di situ).
    """
    teks = (pesan or "").strip()
    try:
        luar, _ = json.JSONDecoder().raw_decode(teks)
    except ValueError:
        return teks
    return str(((luar or {}).get("error") or {}).get("message") or teks)


def baca_batas(pesan):
    """Angka batas dari badan error 429 router, atau None kalau bukan soal batas.

    Router membungkus error asli sebagai teks: '[model] [429]: {"error":{...}}'.
    Yang dicari: metadata.headers.X-RateLimit-{Limit,Remaining,Reset} dan limit_source.
    """
    m = POLA_DALAM.search(buka_bungkus(pesan))
    if not m:
        return None
    try:
        dalam, _ = json.JSONDecoder().raw_decode(m.group(2))
    except ValueError:
        return None
    err = (dalam or {}).get("error") or {}
    meta = err.get("metadata") or {}
    head = meta.get("headers") or {}
    hasil = {"sumber": meta.get("limit_source") or err.get("type") or err.get("code"),
             "pesan": str(err.get("message") or "")[:200]}
    for kunci, nama in (("X-RateLimit-Limit", "batas"), ("X-RateLimit-Remaining", "sisa")):
        if kunci in head:
            try:
                hasil[nama] = int(head[kunci])
            except (TypeError, ValueError):
                pass
    if "X-RateLimit-Reset" in head:
        try:
            hasil["reset_ms"] = int(head["X-RateLimit-Reset"])
        except (TypeError, ValueError):
            pass
    return hasil


def probe_model(model, timeout=60):
    """Satu permintaan 1 token ke `model`. -> {"hidup": bool, "pesan": str}.

    Dipakai sebagai pengukuran, bukan tebakan: model yang menjawab PASTI bisa dipakai
    sekarang (aturan #1 -- kontrol positif).
    """
    body = json.dumps({"model": model, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ok"}]}).encode()
    req = urllib.request.Request((common.OPENAI_BASE_URL or "").rstrip("/") + "/chat/completions",
                                 data=body, headers={"Authorization": f"Bearer {common.OPENAI_API_KEY}",
                                                     "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        return {"hidup": True, "pesan": ""}
    except urllib.error.HTTPError as e:
        return {"hidup": False, "pesan": e.read().decode("utf-8", "replace")[:4000]}
    except Exception as e:
        return {"hidup": False, "pesan": f"{type(e).__name__}: {e}"[:300]}


def cek_router(sekarang, probe=probe_model, cek_gratis=False, model_gratis=MODEL_GRATIS_UJI):
    """Status di router lokal. Model utama diprobe selalu; jatah ':free' hanya bila perlu
    (probe yang BERHASIL memakai 1 dari 50, jadi tidak dilakukan tanpa alasan)."""
    utama = common.LLM_MODEL
    hasil = {"waktu_cek": sekarang.astimezone(WIB).strftime("%d/%m/%Y %H:%M WIB"),
             "model": common.rantai_model(utama), "lewat": "router_lokal", "model_utama": utama}
    u = probe(utama)
    hasil["utama_hidup"] = u["hidup"]
    if not u["hidup"]:
        batas = baca_batas(u["pesan"])
        hasil["utama_alasan"] = batas or {"pesan": buka_bungkus(u["pesan"])[:300]}
    if u["hidup"] and not cek_gratis:
        return {**hasil, "ok": True, "bisa_jalan": True,
                "catatan": "Model utama menjawab, jadi bisa jalan sekarang. Jatah model gratis "
                           "tidak diperiksa supaya tidak terpakai."}
    g = probe(model_gratis)
    if g["hidup"]:
        hasil["kuota_gratis"] = {"habis": False, "model_diuji": model_gratis}
        hasil["catatan"] = "Jatah model gratis masih ada (pemeriksaan ini memakai 1 permintaan)."
    else:
        batas = baca_batas(g["pesan"])
        if batas is None:
            return {**hasil, "ok": False, "alasan": "format_tak_dikenal",
                    "bisa_jalan": hasil["utama_hidup"]}
        hasil["kuota_gratis"] = {"habis": True, "model_diuji": model_gratis, **batas}
        if "reset_ms" in batas:
            hasil["reset"] = dt.datetime.fromtimestamp(batas["reset_ms"] / 1000,
                                                       dt.timezone.utc).astimezone(WIB) \
                                        .strftime("%d/%m/%Y %H:%M WIB")
        else:
            hasil["reset"] = reset_berikutnya(sekarang).strftime("%d/%m/%Y %H:%M WIB")
        hasil["catatan"] = ("Semua model gratis memakai SATU jatah akun, jadi ganti model gratis "
                            "tidak menolong. Yang menolong: model di luar jatah itu.")
    hasil["ok"] = True
    hasil["bisa_jalan"] = bool(hasil["utama_hidup"] or not hasil["kuota_gratis"].get("habis"))
    return hasil


def cek(sekarang=None, ambil=ambil_status, cek_gratis=False):
    sekarang = sekarang or dt.datetime.now(WIB)
    hasil = {"waktu_cek": sekarang.astimezone(WIB).strftime("%d/%m/%Y %H:%M WIB"),
             "model": common.rantai_model(common.LLM_MODEL)}
    basis, kunci = common.OPENAI_BASE_URL or "", common.OPENAI_API_KEY
    if "openrouter.ai" not in basis:
        if basis:
            return cek_router(sekarang, cek_gratis=cek_gratis)
        return {**hasil, "ok": False, "alasan": "basis_kosong"}
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
    print(json.dumps(cek(cek_gratis="--gratis" in sys.argv), ensure_ascii=False))
