"""Kamus istilah per pemilik: ejaan yang benar untuk nama, merek, dan istilah yang sering salah dengar.

Whisper menulis apa yang terdengar: "OpenClaw" bisa jadi "open cloud", nama orang jadi kata umum.
Bias kosakata (`transcribe.build_vocab_prompt`) menolong hanya untuk istilah yang kebetulan disebut
user di permintaannya. Kamus ini menetap per chat dan bekerja di dua lapis:
  1. istilah yang benar ikut dibiaskan ke Whisper;
  2. KODE mengoreksi hasil transkripsi secara pasti: rangkaian kata yang cocok dengan salah satu
     bentuk salah diganti ejaan benarnya (waktu kata pertama s.d. terakhir dipertahankan, jadi
     subtitle tetap sinkron). Ejaan benar yang salah huruf besar/kecil ikut dirapikan.

Diterapkan saat transkripsi (naskah & draf memakai ejaan benar) DAN saat render membaca brief
(revisi cepat video lama ikut terkoreksi setelah user menambah istilah).

CLI untuk agent (satu baris JSON):
  python3 scripts/kamus.py tambah --chat-id "<label>" --benar "OpenClaw" --salah "open cloud" --salah "opencloud"
  python3 scripts/kamus.py daftar --chat-id "<label>"
  python3 scripts/kamus.py hapus  --chat-id "<label>" --benar "OpenClaw"
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gaya  # noqa: E402  (penyimpanan profil per pemilik)
from common import write_json  # noqa: E402

MAKS_ENTRI = 60
MAKS_SALAH = 8
_BENAR = re.compile(r"^[^\s].{0,38}[^\s]$|^[^\s]$")
_TEPI = re.compile(r"^(\W*)(.*?)(\W*)$", re.S)


class KamusError(ValueError):
    pass


def _kunci(teks):
    """Bentuk pembanding: huruf kecil, tanpa tanda baca, spasi tunggal."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(teks or "").lower())).strip()


def validasi_entri(benar, salah=()):
    benar = str(benar or "").strip()
    if not _BENAR.match(benar) or not _kunci(benar):
        raise KamusError("ejaan benar harus 1-40 karakter")
    bentuk = []
    for s in salah or ():
        k = _kunci(s)
        if not k or k == _kunci(benar):
            continue
        if len(k) < 3 or len(k.split()) > 4:
            raise KamusError(f"bentuk salah {s!r} harus 3+ huruf dan paling banyak 4 kata")
        if k not in bentuk:
            bentuk.append(k)
    if len(bentuk) > MAKS_SALAH:
        raise KamusError(f"paling banyak {MAKS_SALAH} bentuk salah per istilah")
    return {"benar": benar, "salah": bentuk}


def validasi(entri):
    if not isinstance(entri, list) or len(entri) > MAKS_ENTRI:
        raise KamusError(f"kamus harus daftar, paling banyak {MAKS_ENTRI} istilah")
    return [validasi_entri(e.get("benar"), e.get("salah")) for e in entri if isinstance(e, dict)]


# ------------------------------------------------------------------ penyimpanan (profil pemilik)

def daftar(pemilik):
    try:
        return validasi(gaya.profil(pemilik).get("kamus") or [])
    except KamusError:
        return []


def _simpan(pemilik, entri):
    data = {**gaya.profil(pemilik), "pemilik": str(pemilik).strip(), "kamus": entri,
            "diubah": dt.datetime.now(dt.timezone.utc).isoformat()}
    write_json(gaya._berkas_profil(pemilik), data)


def tambah(pemilik, benar, salah=()):
    """Tambah/perbarui satu istilah (bentuk salah baru digabung dengan yang lama)."""
    if not str(pemilik or "").strip():
        raise KamusError("pemilik kamus tidak diketahui")
    baru = validasi_entri(benar, salah)
    entri = daftar(pemilik)
    lama = next((e for e in entri if _kunci(e["benar"]) == _kunci(baru["benar"])), None)
    if lama:
        baru = validasi_entri(baru["benar"], lama["salah"] + baru["salah"])
        entri = [baru if e is lama else e for e in entri]
    else:
        if len(entri) >= MAKS_ENTRI:
            raise KamusError(f"kamus penuh ({MAKS_ENTRI} istilah)")
        entri.append(baru)
    _simpan(pemilik, entri)
    return baru


def hapus(pemilik, benar):
    entri = daftar(pemilik)
    sisa = [e for e in entri if _kunci(e["benar"]) != _kunci(benar)]
    if len(sisa) == len(entri):
        return False
    _simpan(pemilik, sisa)
    return True


# ------------------------------------------------------------------ env (dipakai tahap pipeline)

def pasang(pemilik, environ=None):
    env = os.environ if environ is None else environ
    entri = daftar(pemilik) if str(pemilik or "").strip() else []
    if entri:
        env["KAMUS_ISTILAH"] = json.dumps(entri, ensure_ascii=False)
    else:
        env.pop("KAMUS_ISTILAH", None)
    return entri


def aktif(environ=None):
    """Kamus run ini dari env, divalidasi ulang; rusak -> kosong (tidak menggagalkan transkripsi)."""
    mentah = (os.environ if environ is None else environ).get("KAMUS_ISTILAH")
    if not mentah:
        return []
    try:
        return validasi(json.loads(mentah))
    except (ValueError, KamusError):
        print("[warn] kamus: KAMUS_ISTILAH tidak sah, diabaikan", file=sys.stderr)
        return []


def istilah_benar(entri):
    return [e["benar"] for e in entri]


# ------------------------------------------------------------------ koreksi

def _pola(entri):
    """[(tuple_token_kunci, benar)] terpanjang dulu; termasuk ejaan benarnya sendiri (rapikan kapital)."""
    pola = []
    for e in entri:
        for bentuk in [*e["salah"], _kunci(e["benar"])]:
            t = tuple(bentuk.split())
            if t:
                pola.append((t, e["benar"]))
    return sorted(set(pola), key=lambda p: -len(p[0]))


def koreksi_kata(words, entri):
    """(kata_baru, jumlah_ganti). Kata berupa {"word", "start", "end", ...}; rangkaian yang cocok
    digabung jadi SATU kata: start kata pertama, end kata terakhir, tanda baca tepi dipertahankan."""
    pola = _pola(entri)
    if not pola or not words:
        return list(words or []), 0
    kunci = [_kunci(w.get("word")) for w in words]
    hasil, i, ganti = [], 0, 0
    while i < len(words):
        cocok = None
        for t, benar in pola:
            n = len(t)
            if tuple(kunci[i:i + n]) == t:
                cocok = (n, benar)
                break
        if not cocok:
            hasil.append(words[i])
            i += 1
            continue
        n, benar = cocok
        depan = _TEPI.match(str(words[i].get("word") or "")).group(1)
        belakang = _TEPI.match(str(words[i + n - 1].get("word") or "")).group(3)
        baru = f"{depan}{benar}{belakang}"
        if n > 1 or baru != words[i].get("word"):
            ganti += 1
        hasil.append({**words[i], "word": baru, "end": words[i + n - 1].get("end", words[i].get("end"))})
        i += n
    return hasil, ganti


def koreksi_teks(teks, entri):
    """(teks_baru, jumlah_ganti) untuk teks segmen: batas kata, tak peka huruf besar, spasi lentur."""
    jumlah = 0
    for t, benar in _pola(entri):
        pola = re.compile(r"(?<!\w)" + r"[\s\-]+".join(re.escape(x) for x in t) + r"(?!\w)", re.I)

        def ganti(m, benar=benar):
            nonlocal jumlah
            if m.group(0) != benar:
                jumlah += 1
            return benar
        teks = pola.sub(ganti, str(teks or ""))
    return teks, jumlah


def koreksi_transkrip(d, entri):
    """Koreksi satu hasil transkripsi {"text", "segments", "words"} di tempat. -> jumlah ganti (kata)."""
    if not entri or not isinstance(d, dict):
        return 0
    if d.get("text"):
        d["text"], _ = koreksi_teks(d["text"], entri)
    for s in d.get("segments") or []:
        if isinstance(s, dict) and s.get("text"):
            s["text"], _ = koreksi_teks(s["text"], entri)
    n = 0
    if d.get("words"):
        d["words"], n = koreksi_kata(d["words"], entri)
    return n


def koreksi_brief(data, entri):
    """Koreksi transkrip yang tersimpan di brief (revisi cepat video lama). -> jumlah ganti."""
    if not entri or not isinstance(data, dict):
        return 0
    n = 0
    for nama, kata in (data.get("transcript_words") or {}).items():
        data["transcript_words"][nama], k = koreksi_kata(kata, entri)
        n += k
    for segs in (data.get("transcript_segments") or {}).values():
        for s in segs or []:
            if isinstance(s, dict) and s.get("text"):
                s["text"], _ = koreksi_teks(s["text"], entri)
    return n


# ------------------------------------------------------------------ CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="Kamus istilah per chat")
    ap.add_argument("perintah", choices=["tambah", "daftar", "hapus"])
    ap.add_argument("--chat-id", default="")
    ap.add_argument("--benar", default="")
    ap.add_argument("--salah", action="append", default=[])
    a = ap.parse_args(argv)
    try:
        if not a.chat_id.strip():
            raise KamusError("Label chat tidak diketahui.")
        if a.perintah == "tambah":
            out = {"ok": True, "istilah": tambah(a.chat_id, a.benar, a.salah), "jumlah": len(daftar(a.chat_id))}
        elif a.perintah == "hapus":
            out = {"ok": True, "dihapus": hapus(a.chat_id, a.benar)}
        else:
            out = {"ok": True, "kamus": daftar(a.chat_id)}
    except (KamusError, gaya.GayaError) as e:
        out = {"ok": False, "alasan": str(e)}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
