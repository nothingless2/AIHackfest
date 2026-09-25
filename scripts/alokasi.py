"""Membagi panjang narasi ke bahan user TANPA memutar potongan yang sama berulang-ulang.

Asal (24 Sep): mode voice-over AI membagi narasi SAMA RATA per klip lalu memutar klip yang
lebih pendek dari jatahnya (`-stream_loop`). Klip dengan 1,75 dtk bagian layak mendapat jatah
7,38 dtk -> terulang ±4 kali, terlihat jelas oleh user.

Aturan di sini (murni, tanpa ffmpeg, supaya bisa dites):
1. Tiap klip menyumbang SEMUA rentang layaknya (bagian goyang sudah dibuang), bukan hanya satu.
2. Bahan cukup: jatah "isi air" -- semua potongan mendapat bagian yang sama, KECUALI potongan
   pendek yang hanya mendapat sepanjang miliknya. Tidak ada potongan yang melebihi sumbernya.
3. Bahan kurang: foto menyerap (Ken Burns bisa sepanjang apa pun); tanpa foto, video
   diperlambat merata sampai LAMBAT_MIN; masih kurang -> potongan dipakai ulang bergiliran,
   tidak pernah berturut-turut bila ada pilihan lain, dan tidak pernah di-loop di dalam segmen.
Semua pengisian dilaporkan (info), bukan disembunyikan.
"""

import math

LAMBAT_MIN = 0.8          # paling lambat 0,8x: di bawah itu gerak lambatnya mulai terasa aneh
SISA_ABAIKAN = 0.25       # sisa sekecil ini ditutup renderer dengan menahan frame terakhir


def _isi_air(kapasitas, total):
    """x sehingga sum(min(k, x)) == total (kapasitas boleh inf). Anggap sum(k) >= total."""
    urut = sorted(kapasitas)
    sisa, n = total, len(urut)
    for i, k in enumerate(urut):
        bagian = sisa / (n - i)
        if k >= bagian:
            return bagian
        sisa -= k
    return urut[-1] if urut else 0.0


def _pilih(potongan, maks):
    """Terlalu banyak potongan untuk durasi ini: utamakan SATU potongan terpanjang per bahan
    (urutan bahan), lalu potongan lain terpanjang dulu. Urutan asli dipertahankan."""
    if len(potongan) <= maks:
        return potongan, 0
    per_bahan = {}
    for i, p in enumerate(potongan):
        j = per_bahan.get(p["bahan"])
        if j is None or p["kap"] > potongan[j]["kap"]:
            per_bahan[p["bahan"]] = i
    pilih = [per_bahan[b] for b in sorted(per_bahan)][:maks]
    sisa = sorted((i for i in range(len(potongan)) if i not in pilih),
                  key=lambda i: -potongan[i]["kap"])
    pilih += sisa[:maks - len(pilih)]
    return [potongan[i] for i in sorted(pilih)], len(potongan) - maks


def susun_potongan(bahan, total, *, speed=1.0, min_klip=1.5):
    """bahan: [{"path", "foto": bool, "rentang": [(a, b), ...]}] berurutan.
    Return (potongan, info). potongan: [{"path", "potong": (a, b) | None, "durasi",
    "speed", "ulang": bool}] berurutan; sum(durasi) == total."""
    kandidat = []
    for i, b in enumerate(bahan):
        if b.get("foto"):
            kandidat.append({"bahan": i, "path": b["path"], "a": None, "kap": math.inf})
            continue
        for a, z in b.get("rentang") or []:
            if z - a > 0:
                kandidat.append({"bahan": i, "path": b["path"], "a": a, "b": z,
                                 "kap": (z - a) / speed})
    info = {"narasi": round(total, 2),
            "bahan_layak": round(sum(p["b"] - p["a"] for p in kandidat if p["a"] is not None), 2),
            "lambat": None, "dipakai_ulang_detik": 0.0, "potongan_dibuang": 0}
    if not kandidat or total <= 0:
        return [], info

    kandidat, dibuang = _pilih(kandidat, max(1, int(total // min_klip)))
    info["potongan_dibuang"] = dibuang
    ada_foto = any(p["a"] is None for p in kandidat)
    pasokan = sum(p["kap"] for p in kandidat)

    lambat = 1.0
    if not ada_foto and pasokan < total:
        lambat = max(LAMBAT_MIN, pasokan / total)
        info["lambat"] = round(lambat, 3)
        for p in kandidat:
            p["kap"] /= lambat
        pasokan = sum(p["kap"] for p in kandidat)

    def buat(p, durasi, ulang=False):
        eff = speed * lambat
        if p["a"] is None:
            potong = None
        elif ulang:     # dipakai ulang: ambil dari UJUNG rentang, beda dari tampilan pertamanya
            potong = (max(p["a"], p["b"] - durasi * eff), p["b"])
        else:
            potong = (p["a"], min(p["b"], p["a"] + durasi * eff))
        return {"path": p["path"], "potong": potong, "durasi": durasi, "speed": eff, "ulang": ulang}

    if pasokan >= total - 1e-9:
        x = _isi_air([p["kap"] for p in kandidat], total)
        hasil = [buat(p, min(p["kap"], x)) for p in kandidat]
    else:
        # Bahan habis: semua potongan dipakai penuh, lalu dipakai ulang bergiliran. Giliran
        # mulai dari potongan pertama; bila hanya itu satu-satunya, pengulangan tak terhindar.
        hasil = [buat(p, p["kap"]) for p in kandidat]
        kurang = total - pasokan
        n = len(kandidat)
        i = 0
        while kurang > SISA_ABAIKAN:
            p = kandidat[i % n]
            d = min(p["kap"], kurang)
            hasil.append(buat(p, d, ulang=True))
            kurang -= d
            info["dipakai_ulang_detik"] += d
            i += 1
        info["dipakai_ulang_detik"] = round(info["dipakai_ulang_detik"], 2)
    # Jumlah durasi WAJIB sama dengan narasi (video = audio). Sisa kecil/pembulatan masuk ke
    # potongan terakhir; bila melebihi sumbernya, renderer menahan frame terakhir (tpad).
    hasil[-1]["durasi"] += total - sum(h["durasi"] for h in hasil)
    return hasil, info
