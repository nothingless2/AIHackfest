"""Caption dinamis (gaya "dinamis"): potongan 1-3 kata, satu kata kunci tampil BESAR.

Asal (27 Sep): user mengirim contoh video Claude + Remotion -- kata kunci ("Remotion") besar
bergradasi emas dengan kata sambung kecil di atasnya, berganti mengikuti ucapan. Subtitle kita
drawtext polos.

Semua keputusan di sini dibuat KODE dari waktu kata yang terdengar (aturan #5): LLM hanya boleh
mengusulkan kata kunci, dan usulan yang tidak diucapkan dibuang (kata_kunci_bersih).
"""

import re

JEDA_PUTUS = 0.35          # dtk: jeda ucapan lebih dari ini = potongan baru
MAKS_KATA = 3
MAKS_HURUF = 18
EKOR = 0.4                 # potongan terakhir sebelum jeda panjang hilang setelah ini
MIN_TAMPIL = 0.25
PORSI_KUNCI = 0.4          # maksimal bagian potongan yang diberi kata kunci besar
JARAK_KUNCI = 1.2          # dtk antar kata kunci hasil heuristik: tidak monoton
MAKS_USULAN = 8
MAKS_ULANG = 2             # kata yang sama paling banyak 2 kali ditekankan per video
SKOR_USULAN = 5            # usulan LLM yang terucap: di atas semua skor heuristik

# Kata sambung / fungsi: tidak pernah dijadikan kata kunci.
KATA_SAMBUNG = set("""
yang dan di ke dari ini itu untuk dengan pada dalam juga atau tapi tetapi karena jadi
agar supaya kalau jika sudah akan bisa ada adalah ya aja saja sih kok dong deh lah nih kan
aku saya kamu kalian kita kami dia mereka anda gue lo lu nya tuh gitu gini banget sangat
lebih paling sama buat biar terus lagi udah belum masih cuma hanya semua setiap para
sebelum sesudah setelah ketika seperti bahwa tentang
kenapa mengapa bagaimana gimana memang pernah mungkin sekarang nanti tadi kemudian
lalu makanya soalnya padahal walaupun meskipun apalagi misalnya contohnya intinya
sebenarnya beberapa banyak sedikit kayak bener benar emang aduh oke okay yaudah sekali
the a an and or to of in on for is are was it this that with you i we they be
""".split())


def norm(kata):
    return re.sub(r"[^\w]", "", str(kata or "").lower())


def _akhir_kalimat(kata):
    return str(kata or "").rstrip().endswith((".", "!", "?"))


def tampilan(kata):
    """Teks yang digambar: tanda baca ringan di ujung dibuang (koma, titik) dan tanda hubung/kutip
    di depan (token Whisper "-error" terlihat di render nyata 27 Sep)."""
    return str(kata or "").strip().lstrip("-–—\"'“‘(").rstrip(",.;:\"'”’)")


def kata_kunci_bersih(usulan, teks_terdengar):
    """Usulan LLM -> kata (ter-normalisasi) yang BENAR-BENAR diucapkan. Frasa dipecah per kata."""
    terdengar = {norm(w) for w in str(teks_terdengar or "").split()}
    hasil = []
    for u in usulan if isinstance(usulan, list) else []:
        for w in str(u or "").split():
            n = norm(w)
            if n and n in terdengar and n not in KATA_SAMBUNG and n not in hasil:
                hasil.append(n)
    return hasil[:MAKS_USULAN]


def _skor(kata, awal_kalimat):
    n = norm(kata)
    if len(n) < 3 or n in KATA_SAMBUNG:
        return 0
    if any(c.isdigit() for c in n):
        return 3
    if str(kata).strip()[:1].isupper() and not awal_kalimat:
        return 2               # nama / merek di tengah kalimat
    return 1 if len(n) >= 6 else 0


def potong(kata_waktu, kata_kunci=()):
    """[{word,start,end}] -> [{mulai, selesai, kata:[teks], kunci: indeks|None}], urut & tak
    bertumpuk; semua kata tercakup berurutan."""
    kata = [w for w in kata_waktu or [] if str(w.get("word") or "").strip()]
    kelompok, cur = [], []
    for w in kata:
        if cur:
            teks = " ".join(tampilan(x["word"]) for x in cur + [w])
            if (float(w["start"]) - float(cur[-1]["end"]) > JEDA_PUTUS or len(cur) >= MAKS_KATA
                    or len(teks) > MAKS_HURUF or _akhir_kalimat(cur[-1]["word"])):
                kelompok.append(cur)
                cur = []
        cur.append(w)
    if cur:
        kelompok.append(cur)

    hasil = []
    for i, g in enumerate(kelompok):
        mulai = float(g[0]["start"])
        selesai = float(g[-1]["end"]) + EKOR
        if i + 1 < len(kelompok):
            selesai = min(selesai, float(kelompok[i + 1][0]["start"]))
        hasil.append({"mulai": round(mulai, 3), "selesai": round(max(selesai, mulai + 0.04), 3),
                      "kata": [tampilan(x["word"]) for x in g], "kunci": None,
                      "_asli": [x["word"] for x in g],
                      "_awal": i == 0 or _akhir_kalimat(kelompok[i - 1][-1]["word"])})
    # Potongan yang terlalu singkat tetap ada (kata tidak boleh hilang); hanya dilaporkan lewat
    # durasi. Tampil minimal MIN_TAMPIL bila tidak ada potongan berikutnya yang menghalangi.
    for i, p in enumerate(hasil):
        batas = hasil[i + 1]["mulai"] if i + 1 < len(hasil) else float("inf")
        if p["selesai"] - p["mulai"] < MIN_TAMPIL:
            p["selesai"] = round(min(p["mulai"] + MIN_TAMPIL, batas), 3)

    # Kata kunci: usulan LLM yang terucap diutamakan (skor tertinggi), tapi SEMUA kandidat tunduk
    # pada porsi, jarak, dan batas pengulangan -- render nyata 27 Sep: tanpa batas, 21 dari 42
    # potongan ditekankan dan "OpenClaw" 7 kali (monoton, tidak lagi terasa "penting").
    kk = {norm(k) for k in kata_kunci or ()}
    kandidat = []
    for i, p in enumerate(hasil):
        skor = [(SKOR_USULAN if norm(w) in kk else _skor(w, p["_awal"] and j == 0), j)
                for j, w in enumerate(p["_asli"])]
        s, j = max(skor, key=lambda x: (x[0], -x[1]))
        if s > 0:
            kandidat.append((s, i, j))
    maks = max(1, int(len(hasil) * PORSI_KUNCI)) if kandidat else 0
    dipilih, ulang = [], {}
    for s, i, j in sorted(kandidat, key=lambda x: (-x[0], x[1])):
        if len(dipilih) >= maks:
            break
        n = norm(hasil[i]["_asli"][j])
        if ulang.get(n, 0) >= MAKS_ULANG:
            continue
        if all(abs(hasil[i]["mulai"] - hasil[k]["mulai"]) >= JARAK_KUNCI for k in dipilih):
            hasil[i]["kunci"] = j
            dipilih.append(i)
            ulang[n] = ulang.get(n, 0) + 1
    for p in hasil:
        del p["_asli"], p["_awal"]
    return hasil
