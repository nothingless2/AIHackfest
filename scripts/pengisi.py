"""Buang kata pengisi ("eee", "emm", "hmm") dan ulangan gagap ("aku pakai aku pakai") dari ucapan asli.

Diukur 29 Sep (faster-whisper small, kontrol positif edge-tts ber-"eee"): Whisper MENULISKAN kata
pengisi yang ada -- "e -e", "Hi -mmmm", "em" (prompt biasa); "eee", "memememem", "em" (prompt
verbatim) -- dan ulangan ("Aku pakai aku pakai"). 6 video user: 0 kata pengisi & 0 suara bersuara
yang tidak tertranskrip -- mereka memang lancar, bukan terlewat.

Semua keputusan dari WAKTU KATA transkrip. Yang TIDAK dibuang:
- pengulangan dengan jeda/koma ("setiap hari, setiap hari") -- penekanan yang disengaja;
- reduplikasi 1 kata di luar kata fungsi ("pelan pelan", "sama sama") -- bahasa, bukan gagap.
"""

import re

_POLA_PENGISI = re.compile(r"^(e+|e+h+|e+m+|eh+m+|h+m+|m{2,}|(me){2,}m*|u+h*m*)$")
FUNGSI = set("""aku saya kamu kita kami dia mereka yang di ke dari dan atau tapi jadi kalau terus
ini itu mau bisa udah sudah lagi aja the a and i to""".split())
JEDA_GAGAP = 0.25          # dtk: ulangan lebih rapat dari ini = gagap, bukan penekanan
MARGIN = 0.02
MIN_POTONG = 0.12          # potongan lebih pendek tidak sepadan (dan berisiko memotong kata)
# >25% klip terbuang = transkrip mencurigakan -> klip dibiarkan utuh. (15% semula menolak kalimat
# kontrol nyata ber-3 pengisi dalam 7 dtk = 16%; pembicara ragu memang sepadat itu.)
MAKS_PORSI = 0.25


def _norm(w):
    return re.sub(r"[^\w]", "", str(w or "").lower())


def adalah_pengisi(kata):
    n = _norm(kata)
    return bool(n) and bool(_POLA_PENGISI.match(n))


def _tanda_akhir(w):
    return str(w.get("word") or "").rstrip().endswith((",", ".", "!", "?", ";", ":"))


def rentang_buang(kata, durasi=None):
    """[(a, b, alasan)] dalam waktu klip ASLI, urut & tak bertumpuk."""
    kata = [w for w in kata or [] if str(w.get("word") or "").strip()]
    hasil = []

    def batas(i, a, b):
        kiri = float(kata[i - 1]["end"]) if i > 0 else 0.0
        kanan = float(kata[i + 1]["start"]) if i + 1 < len(kata) else (durasi or b + MARGIN)
        return max(a - MARGIN, kiri), min(b + MARGIN, kanan)

    for i, w in enumerate(kata):
        a, b = float(w["start"]), float(w["end"])
        if adalah_pengisi(w["word"]):
            hasil.append((*batas(i, a, b), f"pengisi \"{_norm(w['word'])}\""))
        elif (_norm(w["word"]) in ("hi", "he", "ha") and i + 1 < len(kata)
              and adalah_pengisi(kata[i + 1]["word"]) and float(kata[i + 1]["start"]) - b < 0.05):
            # Whisper memecah "Hmmm" jadi "Hi" + "-mmmm" (terukur): bagian depannya ikut dibuang.
            hasil.append((*batas(i, a, b), "pengisi \"hmm\""))

    i = 0
    while i < len(kata):
        cocok = False
        for n in (3, 2, 1):
            if i + 2 * n > len(kata):
                continue
            a_, b_ = kata[i:i + n], kata[i + n:i + 2 * n]
            if [_norm(x["word"]) for x in a_] != [_norm(x["word"]) for x in b_]:
                continue
            if n == 1 and _norm(a_[0]["word"]) not in FUNGSI:
                continue
            if _tanda_akhir(a_[-1]) or float(b_[0]["start"]) - float(a_[-1]["end"]) >= JEDA_GAGAP:
                continue
            hasil.append((float(a_[0]["start"]) - MARGIN, float(b_[0]["start"]) - MARGIN,
                          "ulangan \"" + " ".join(_norm(x["word"]) for x in a_) + "\""))
            i += n
            cocok = True
            break
        if not cocok:
            i += 1

    hasil.sort()
    gabung = []
    for a, b, al in hasil:
        if gabung and a <= gabung[-1][1] + 0.05:
            pa, pb, pal = gabung[-1]
            gabung[-1] = (pa, max(pb, b), pal if pal == al else f"{pal}; {al}")
        else:
            gabung.append((max(0.0, a), b, al))
    return [(round(a, 3), round(b, 3), al) for a, b, al in gabung if b - a >= MIN_POTONG]


def terapkan(ranges, kata, durasi):
    """(ranges_baru, dibuang). Klip yang >MAKS_PORSI terbuang dibiarkan utuh (dibuang = None)."""
    from visual_quality import kurangi
    buang = rentang_buang(kata, durasi)
    if not buang:
        return ranges, []
    total = sum(b - a for a, b, _ in buang)
    if durasi and total > MAKS_PORSI * durasi:
        return ranges, None
    baru = kurangi(ranges, buang, min_keep=MIN_POTONG)
    return (baru or ranges), buang
