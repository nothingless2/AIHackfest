"""Motion graphic penjelas konteks (kartu pembuka, sorot kata kunci, ikon, langkah, label,
kartu ajakan) -- permintaan user 24 Sep, meniru video referensi yang dibuat dengan Remotion.

Pembagian kerja (aturan #5): LLM (BrainIdea) hanya MENGUSULKAN `motion_plan` berisi jenis,
teks, dan kata jangkar. KODE di sini yang:
- menegakkan katalog tertutup dan batas panjang teks,
- menolak ANGKA yang tidak ada di permintaan user / ucapan asli (statistik karangan),
- memberi WAKTU dari kata yang benar-benar diucapkan TTS (bukan waktu tebakan LLM),
- menjaga tingkat "sedang": kartu pembuka + kartu ajakan + maksimal 4 elemen, tidak
  tumpang tindih, berjarak.
Semua yang dibuang dicatat alasannya (dilaporkan, bukan hilang diam-diam).
"""

import os
import re

JENIS_ELEMEN = ("sorot", "ikon", "langkah", "label")
TINGKAT = ("sedang",)
MAKS_ELEMEN = 4

HOOK_DETIK = 2.2
CTA_DETIK = 2.4
LAMA = {"sorot": 1.6, "ikon": 1.8, "langkah": 2.2, "label": 2.4}
JARAK_MIN = 1.2          # jarak minimal antar-mulai elemen
CELAH = 0.15             # jeda kosong minimal antar-elemen (tidak ada tumpang tindih)
LAMA_MIN = 0.9           # elemen yang terpotong lebih pendek dari ini dibuang
DURASI_MIN_CTA = 8.0     # video lebih pendek: tidak ada kartu ajakan (hook sudah cukup)

BATAS = {"hook": (7, 48), "teks": (4, 30), "sub": (6, 44), "cta": (7, 48)}   # (kata, karakter)

_EMOJI = re.compile(r"^[\U0001F000-\U0001FAFF☀-➿⬀-⯿‍️⃣]{1,8}$")
_ANGKA = re.compile(r"\d+(?:[.,]\d+)*")


def tingkat(nilai=None):
    """'sedang' (bawaan, keputusan user 24 Sep) atau None bila dimatikan."""
    n = (nilai if nilai is not None else os.getenv("MOTION_GRAPHIC", "sedang")).strip().lower()
    if n in ("", "none", "off", "0", "false", "mati"):
        return None
    if n not in TINGKAT:
        from style import StyleError
        raise StyleError(f"Motion graphic {n!r} tidak dikenal. Pilihan: {', '.join(TINGKAT)}, mati.")
    return n


def norm(kata):
    return re.sub(r"[^\w]", "", str(kata or "").lower())


def _teks(nilai, batas):
    """Teks bersih, atau (None, alasan) bila melewati batas. TIDAK dipotong: potongan bisa
    mengubah makna."""
    t = " ".join(str(nilai or "").split())
    if not t:
        return None, None
    kata, karakter = batas
    if len(t.split()) > kata or len(t) > karakter:
        return None, f"terlalu panjang ({len(t.split())} kata)"
    return t, None


def _emoji(nilai):
    e = str(nilai or "").strip()
    return e if _EMOJI.match(e) else ""


def angka_sah(sumber):
    return {a.replace(",", ".") for a in _ANGKA.findall(sumber or "")}


def _angka_asing(teks, sah):
    return [a for a in _ANGKA.findall(teks or "") if a.replace(",", ".") not in sah]


def bersihkan(plan, *, naskah, sumber_fakta, pakai_jangkar):
    """(bersih, catatan). bersih = {"hook": {...}|None, "elemen": [...], "cta": {...}|None}.

    sumber_fakta: teks yang boleh menjadi asal angka (permintaan user + ucapan asli).
    pakai_jangkar=False (mode suara asli): hanya kartu pembuka & ajakan -- elemen butuh
    waktu kata narasi yang hanya ada di mode voice-over AI."""
    catatan = []
    bersih = {"hook": None, "elemen": [], "cta": None}
    if not isinstance(plan, dict):
        return bersih, (["rencana grafik bukan objek"] if plan else [])
    sah = angka_sah(sumber_fakta)

    def cek_angka(label, *teks):
        asing = [a for t in teks for a in _angka_asing(t, sah)]
        if asing:
            catatan.append(f"{label} dibuang: angka {', '.join(asing)} tidak ada di permintaan user")
            return False
        return True

    hook, alasan = _teks(plan.get("hook"), BATAS["hook"])
    if alasan:
        catatan.append(f"kartu pembuka dibuang: {alasan}")
    elif hook and cek_angka("kartu pembuka", hook):
        sorot = norm(plan.get("hook_sorot"))
        bersih["hook"] = {"teks": hook, "emoji": _emoji(plan.get("hook_emoji")),
                          "sorot": sorot if sorot and sorot in {norm(w) for w in hook.split()} else ""}

    cta, alasan = _teks(plan.get("cta"), BATAS["cta"])
    sub_cta, _ = _teks(plan.get("cta_sub"), BATAS["sub"])
    if alasan:
        catatan.append(f"kartu ajakan dibuang: {alasan}")
    elif cta and cek_angka("kartu ajakan", cta, sub_cta or ""):
        bersih["cta"] = {"teks": cta, "sub": sub_cta or "", "emoji": _emoji(plan.get("cta_emoji"))}

    elemen = plan.get("elemen") or []
    if not isinstance(elemen, list):
        elemen = []
    if elemen and not pakai_jangkar:
        catatan.append(f"{len(elemen)} elemen dilewati: hanya untuk mode voice-over AI "
                       "(butuh waktu kata narasi)")
        elemen = []
    kata_naskah = {norm(w) for w in (naskah or "").split()}
    for i, el in enumerate(elemen, 1):
        if not isinstance(el, dict):
            continue
        jenis = str(el.get("jenis") or "").strip().lower()
        if jenis not in JENIS_ELEMEN:
            catatan.append(f"elemen {i} dibuang: jenis {jenis!r} tidak ada di katalog")
            continue
        teks, alasan = _teks(el.get("teks"), BATAS["teks"])
        if not teks:
            catatan.append(f"elemen {i} ({jenis}) dibuang: {alasan or 'teks kosong'}")
            continue
        sub, _ = _teks(el.get("sub"), BATAS["sub"])
        if not cek_angka(f"elemen {i} ({jenis})", teks, sub or ""):
            continue
        jangkar = norm(el.get("saat_kata"))
        if not jangkar or jangkar not in kata_naskah:
            catatan.append(f"elemen {i} ({jenis}) dibuang: kata jangkar {el.get('saat_kata')!r} "
                           "tidak ada di naskah")
            continue
        emoji = _emoji(el.get("emoji"))
        if jenis == "ikon" and not emoji:
            catatan.append(f"elemen {i} (ikon) dibuang: tanpa emoji yang sah")
            continue
        bersih["elemen"].append({"jenis": jenis, "teks": teks, "sub": sub or "", "emoji": emoji,
                                 "jangkar": jangkar})
    if len(bersih["elemen"]) > MAKS_ELEMEN:
        catatan.append(f"{len(bersih['elemen']) - MAKS_ELEMEN} elemen dibuang: batas tingkat sedang "
                       f"{MAKS_ELEMEN}")
        bersih["elemen"] = bersih["elemen"][:MAKS_ELEMEN]
    return bersih, catatan


def jadwal(bersih, kata_waktu, durasi, *, ada_teks_statis=False):
    """(items, catatan). Waktu tiap elemen = saat kata jangkarnya DIUCAPKAN (kata_waktu dari
    TTS: [{word, start, end}]). Jangkar dicari berurutan: kata yang sama dua kali di naskah
    dipasangkan ke kemunculan berikutnya."""
    catatan, items = [], []
    akhir_hook = 0.0
    if bersih.get("hook"):
        if ada_teks_statis:
            catatan.append("kartu pembuka dilewati: teks statis sudah menjadi judul")
        else:
            akhir_hook = min(HOOK_DETIK, durasi * 0.3)
            items.append({"jenis": "kartu_hook", "mulai": 0.0, "selesai": round(akhir_hook, 3),
                          **bersih["hook"]})
    mulai_cta = durasi
    if bersih.get("cta"):
        if durasi < DURASI_MIN_CTA:
            catatan.append(f"kartu ajakan dilewati: video hanya {durasi:.1f} dtk")
        else:
            mulai_cta = durasi - CTA_DETIK
    waktu = [(norm(w.get("word")), float(w["start"])) for w in (kata_waktu or [])]
    pos, akhir_prev, mulai_prev = -1, akhir_hook, None
    elemen = []
    for el in bersih.get("elemen") or []:
        j = next((k for k in range(pos + 1, len(waktu)) if waktu[k][0] == el["jangkar"]), None)
        if j is None:
            catatan.append(f"{el['jenis']} \"{el['teks']}\" dibuang: kata \"{el['jangkar']}\" "
                           "tidak ditemukan di suara narasi")
            continue
        pos = j
        a = waktu[j][1]
        b = min(a + LAMA[el["jenis"]], mulai_cta - CELAH)
        if a < akhir_prev + CELAH or (mulai_prev is not None and a - mulai_prev < JARAK_MIN):
            catatan.append(f"{el['jenis']} \"{el['teks']}\" dibuang: terlalu rapat dengan elemen sebelumnya")
            continue
        if b - a < LAMA_MIN:
            catatan.append(f"{el['jenis']} \"{el['teks']}\" dibuang: tidak cukup waktu sebelum kartu ajakan")
            continue
        elemen.append({"jenis": el["jenis"], "mulai": round(a, 3), "selesai": round(b, 3),
                       "teks": el["teks"], "sub": el["sub"], "emoji": el["emoji"]})
        akhir_prev, mulai_prev = b, a
    langkah = [e for e in elemen if e["jenis"] == "langkah"]
    for n, e in enumerate(langkah, 1):
        e.update(nomor=n, total=len(langkah))
    items += elemen
    if bersih.get("cta") and mulai_cta < durasi:
        items.append({"jenis": "kartu_cta", "mulai": round(mulai_cta, 3), "selesai": round(durasi, 3),
                      **bersih["cta"]})
    return items, catatan
