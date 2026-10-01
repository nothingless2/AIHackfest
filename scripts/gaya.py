"""Preset gaya: satu nama -> satu paket tampilan (tema Remotion) + pengaturan editing.

Sebelum 1 Okt gaya tersebar di ±30 flag `hermes_render.py`, dan warna tertanam di tiap komponen
Remotion dengan palet yang berbeda-beda (ungu di motion, oranye di panggung). Preset menyatukan
keduanya supaya satu pilihan user ("hype", "elegan") konsisten di caption, kartu, panggung, dan
cover, dan bisa disimpan per pemilik supaya video berikutnya ikut gaya yang sama.

Urutan prioritas: flag eksplisit > preset (`--gaya`) > profil pemilik > bawaan ("klasik").
Preset HANYA mengisi knob yang SUDAH ada (env yang sama dengan flag-nya), jadi tidak ada jalur
render baru; nilai preset tidak pernah ditulis ke `args`, supaya draf yang disimpan tidak
mengunci knob ketika user berganti gaya.

Validasi sengaja ketat (gagal-tertutup): tema kelak bisa datang dari pengguna lain (SaaS), dan
nilai bebas yang masuk ke style React -- mis. `url(...)` -- bisa membuat Chromium renderer
mengambil URL sembarang. Warna hanya hex, font hanya nama dari daftar tertutup, angka berentang.

CLI untuk agent (satu baris JSON):
  python3 scripts/gaya.py daftar
  python3 scripts/gaya.py pratinjau
  python3 scripts/gaya.py pakai --chat-id "<label chat>" --gaya hype
  python3 scripts/gaya.py lihat --chat-id "<label chat>"
  python3 scripts/gaya.py lupakan --chat-id "<label chat>"
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PROJECT_ROOT, STATE_DIR, read_json, write_json  # noqa: E402
from overlay_remotion import ANIMASI  # noqa: E402
from style import COLOR_FILTERS, TEXT_FONTS, TEXT_POSITIONS, StyleError  # noqa: E402

GAYA_DIR = os.path.join(PROJECT_ROOT, "config", "gaya")
PROFIL_DIR = os.path.join(STATE_DIR, "profil")
PRATINJAU_DIR = os.path.join(STATE_DIR, "pratinjau_gaya")
BAWAAN = "klasik"
_NAMA = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")

# Harus sama dengan kunci auto_render.SUBTITLE_STYLES (dijaga tes; modul itu berat diimpor).
SUBTITLE_STYLES = ("karaoke", "karaoke-tebal", "karaoke-kapital", "capcut", "putih-kotak",
                   "kuning-kotak", "putih-tebal", "kuning", "kata", "dinamis")

_HEX6 = re.compile(r"^#[0-9A-Fa-f]{6}$")
_HEX8 = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
# Token warna -> pola. Yang digabung dengan alpha di komponen (`${aksen}AA`) wajib 6 digit.
WARNA = {"aksen": _HEX6, "aksen2": _HEX6, "sorot": _HEX6, "teks": _HEX6, "teks_sub": _HEX6,
         "latar": _HEX6, "kisi": _HEX6, "kartu": _HEX8, "garis": _HEX8}
GERAK = ("pegas", "halus", "tegas")
SUDUT = (0.0, 2.0)
KONTRAS_MIN = 4.5          # WCAG AA untuk teks kartu

# Kunci editing preset = dest flag hermes_render -> (env, nilai sah).
EDITING = {
    "subtitle_style": ("SUBTITLE_STYLE", SUBTITLE_STYLES),
    "color_filter": ("COLOR_FILTER", tuple(COLOR_FILTERS) + ("none",)),
    "text_font": ("TEXT_FONT", tuple(TEXT_FONTS)),
    "text_position": ("TEXT_POSITION", TEXT_POSITIONS),
    "text_animation": ("TEXT_ANIMATION", ANIMASI + ("none",)),
    "sfx": ("SFX", ("on", "off")),
    "zoom_wajah": ("ZOOM_WAJAH", ("on", "off")),
    "logo_merek": ("LOGO_MEREK", ("on", "off")),
    "cover": ("COVER", ("desain", "frame")),
    "motion": ("MOTION_GRAPHIC", ("sedang", "mati")),
}
# Suasana musik SENGAJA tidak termasuk: ia bergantung pada isi konten dan isi pustaka musik, bukan
# tampilan. Uji nyata 1 Okt: preset "hype" (energik) membuat render DITOLAK karena pustaka hanya
# punya lagu tenang, dan di revisi ia akan mengganti lagu video yang user cuma minta ganti gayanya.


class GayaError(StyleError):
    """Preset/profil gaya tidak dikenal atau tidak sah. Melempar, bukan jatuh ke bawaan."""


# ------------------------------------------------------------------ validasi

def _luminans(hex_):
    def kanal(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * kanal(r) + 0.7152 * kanal(g) + 0.0722 * kanal(b)


def kontras(a, b):
    """Rasio kontras WCAG dua warna hex (alpha diabaikan)."""
    la, lb = sorted((_luminans(a), _luminans(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def validasi_tema(tema):
    """Tema mentah -> tema bersih. Kunci asing, tipe salah, atau nilai di luar daftar -> GayaError."""
    if tema in (None, {}):
        return {}
    if not isinstance(tema, dict):
        raise GayaError("tema harus objek")
    asing = set(tema) - {"warna", "font", "sudut", "cahaya", "gerak"}
    if asing:
        raise GayaError(f"kunci tema tidak dikenal: {', '.join(sorted(asing))}")
    hasil = {}
    warna = tema.get("warna")
    if warna is not None:
        if not isinstance(warna, dict):
            raise GayaError("tema.warna harus objek")
        bersih = {}
        for k, v in warna.items():
            if k == "kunci":
                if (not isinstance(v, list) or not 1 <= len(v) <= 3
                        or not all(isinstance(c, str) and _HEX6.match(c) for c in v)):
                    raise GayaError("tema.warna.kunci harus 1-3 warna hex #RRGGBB")
                bersih[k] = list(v)
            elif k in WARNA:
                if not isinstance(v, str) or not WARNA[k].match(v):
                    raise GayaError(f"tema.warna.{k} harus warna hex, bukan {v!r}")
                bersih[k] = v
            else:
                raise GayaError(f"token warna tidak dikenal: {k}")
        if "teks" in bersih or "kartu" in bersih:
            # Bawaan komponen: teks putih di kartu kaca gelap rgb(14,11,28).
            r = kontras(bersih.get("teks", "#FFFFFF"), bersih.get("kartu", "#0E0B1C")[:7])
            if r < KONTRAS_MIN:
                raise GayaError(f"teks kartu kurang terbaca: kontras {r:.1f} < {KONTRAS_MIN}")
        hasil["warna"] = bersih
    font = tema.get("font")
    if font is not None:
        if not isinstance(font, dict) or set(font) - {"judul"}:
            raise GayaError("tema.font hanya boleh berisi 'judul'")
        if font.get("judul") not in TEXT_FONTS:
            raise GayaError(f"font {font.get('judul')!r} tidak dikenal. Pilihan: {', '.join(TEXT_FONTS)}")
        hasil["font"] = {"judul": font["judul"]}
    if "sudut" in tema:
        s = tema["sudut"]
        if isinstance(s, bool) or not isinstance(s, (int, float)) or not SUDUT[0] <= s <= SUDUT[1]:
            raise GayaError(f"tema.sudut harus angka {SUDUT[0]}-{SUDUT[1]}")
        hasil["sudut"] = float(s)
    if "cahaya" in tema:
        if not isinstance(tema["cahaya"], bool):
            raise GayaError("tema.cahaya harus true/false")
        hasil["cahaya"] = tema["cahaya"]
    if "gerak" in tema:
        if tema["gerak"] not in GERAK:
            raise GayaError(f"tema.gerak harus salah satu dari {', '.join(GERAK)}")
        hasil["gerak"] = tema["gerak"]
    return hasil


def validasi_editing(editing):
    if editing in (None, {}):
        return {}
    if not isinstance(editing, dict):
        raise GayaError("editing harus objek")
    hasil = {}
    for k, v in editing.items():
        if k not in EDITING:
            raise GayaError(f"pengaturan editing tidak dikenal: {k}")
        if v not in EDITING[k][1]:
            raise GayaError(f"editing.{k} {v!r} tidak sah. Pilihan: {', '.join(EDITING[k][1])}")
        hasil[k] = v
    return hasil


def validasi(data, nama):
    if not isinstance(data, dict):
        raise GayaError(f"preset {nama!r} bukan objek")
    asing = set(data) - {"label", "deskripsi", "tema", "editing"}
    if asing:
        raise GayaError(f"kunci preset tidak dikenal: {', '.join(sorted(asing))}")
    label, desk = data.get("label") or nama, data.get("deskripsi") or ""
    if not isinstance(label, str) or len(label) > 30 or not isinstance(desk, str) or len(desk) > 160:
        raise GayaError("label maks 30 huruf, deskripsi maks 160 huruf")
    return {"nama": nama, "label": label, "deskripsi": desk,
            "tema": validasi_tema(data.get("tema")), "editing": validasi_editing(data.get("editing"))}


# ------------------------------------------------------------------ preset

def daftar():
    """Nama preset; bawaan selalu pertama (pilihan A di pertanyaan inspect), sisanya urut abjad."""
    if not os.path.isdir(GAYA_DIR):
        return []
    nama = sorted(f[:-5] for f in os.listdir(GAYA_DIR) if f.endswith(".json") and _NAMA.match(f[:-5]))
    return ([BAWAAN] if BAWAAN in nama else []) + [n for n in nama if n != BAWAAN]


def muat(nama):
    n = (nama or "").strip().lower()
    if not _NAMA.match(n) or n not in daftar():
        raise GayaError(f"Gaya {nama!r} tidak dikenal. Pilihan: {', '.join(daftar())}.")
    try:
        with open(os.path.join(GAYA_DIR, n + ".json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise GayaError(f"preset {n!r} tidak terbaca: {e}")
    return validasi(data, n)


# ------------------------------------------------------------------ profil per pemilik

def _berkas_profil(pemilik):
    """Nama berkas aman untuk label pemilik apa pun (chat Telegram sekarang, user SaaS nanti)."""
    p = str(pemilik or "").strip()
    if not p:
        raise GayaError("pemilik profil tidak diketahui")
    aman = p if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", p) else "h_" + hashlib.sha256(p.encode()).hexdigest()[:32]
    return os.path.join(PROFIL_DIR, aman + ".json")


def profil(pemilik):
    """Profil pemilik ini, atau {}. Berkas milik label lain (tabrakan nama) -> tidak dipakai."""
    if not str(pemilik or "").strip():
        return {}
    data = read_json(_berkas_profil(pemilik), {}) or {}
    return data if data.get("pemilik") == str(pemilik).strip() else {}


def simpan_gaya(pemilik, nama):
    preset = muat(nama)
    data = {**profil(pemilik), "pemilik": str(pemilik).strip(), "gaya": preset["nama"],
            "diubah": dt.datetime.now(dt.timezone.utc).isoformat()}
    write_json(_berkas_profil(pemilik), data)
    return data


def lupakan_gaya(pemilik):
    data = profil(pemilik)
    if data.pop("gaya", None) is None:
        return False
    write_json(_berkas_profil(pemilik), data)
    return True


# ------------------------------------------------------------------ dipakai render

def pilih(eksplisit, pemilik):
    """(preset, sumber). sumber: 'diminta' | 'profil' | 'bawaan' (| 'bawaan_profil_tidak_berlaku').

    Nama eksplisit yang salah -> GayaError (user/agent harus tahu). Gaya di profil yang presetnya
    sudah dihapus TIDAK menggagalkan render: jatuh ke bawaan dan sumbernya menyebutkan itu."""
    if eksplisit:
        return muat(eksplisit), "diminta"
    di_profil = profil(pemilik).get("gaya")
    if di_profil:
        try:
            return muat(di_profil), "profil"
        except GayaError:
            return muat(BAWAAN), "bawaan_profil_tidak_berlaku"
    return muat(BAWAAN), "bawaan"


def env_editing(preset, args):
    """{ENV: nilai} untuk knob preset yang TIDAK diisi user (args.<knob> masih None)."""
    return {EDITING[k][0]: v for k, v in preset["editing"].items() if getattr(args, k, None) is None}


def pasang(preset, args, environ=None):
    """Pasang preset ke env proses render (diwarisi semua tahap)."""
    env = os.environ if environ is None else environ
    env.update(env_editing(preset, args))
    env["GAYA_NAMA"] = preset["nama"]
    if preset["tema"]:
        env["GAYA_TEMA"] = json.dumps(preset["tema"], ensure_ascii=False)
    else:
        env.pop("GAYA_TEMA", None)


def tema_aktif(environ=None):
    """Tema untuk props Remotion dari env, divalidasi ulang. Tidak ada -> None."""
    mentah = (os.environ if environ is None else environ).get("GAYA_TEMA")
    if not mentah:
        return None
    try:
        data = json.loads(mentah)
    except ValueError as e:
        raise GayaError(f"GAYA_TEMA bukan JSON: {e}")
    return validasi_tema(data) or None


# ------------------------------------------------------------------ pratinjau

def _sidik_pratinjau(nama_list):
    h = hashlib.sha256()
    for n in nama_list:
        with open(os.path.join(GAYA_DIR, n + ".json"), "rb") as f:
            h.update(n.encode() + b"\0" + f.read())
    src = os.path.join(PROJECT_ROOT, "remotion", "src")
    for f in sorted(os.listdir(src)):
        with open(os.path.join(src, f), "rb") as g:
            h.update(f.encode() + b"\0" + g.read())
    return h.hexdigest()[:16]


def pratinjau(keluar_dir=None):
    """Satu gambar berisi contoh SEMUA preset (caption + kartu + label), di-cache per isi preset dan
    kode komponen. Yang dikembalikan selalu SALINAN BARU: gateway Hermes hanya mengirim berkas yang
    baru dibuat (trust_recent_files_seconds)."""
    import overlay_remotion as orr
    nama_list = daftar()
    if not nama_list:
        raise GayaError("belum ada preset gaya")
    os.makedirs(PRATINJAU_DIR, exist_ok=True)
    cache = os.path.join(PRATINJAU_DIR, f"semua_{_sidik_pratinjau(nama_list)}.jpg")
    if not os.path.exists(cache):
        orr.render_pratinjau_gaya([muat(n) for n in nama_list], cache)
    keluar_dir = keluar_dir or PRATINJAU_DIR
    os.makedirs(keluar_dir, exist_ok=True)
    salinan = os.path.join(keluar_dir, f"pratinjau_gaya_{dt.datetime.now():%Y%m%d_%H%M%S_%f}.jpg")
    shutil.copyfile(cache, salinan)
    return salinan


# ------------------------------------------------------------------ CLI

def _ringkas(p):
    return {"nama": p["nama"], "label": p["label"], "deskripsi": p["deskripsi"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Preset gaya tampilan + editing")
    ap.add_argument("perintah", choices=["daftar", "pratinjau", "pakai", "lihat", "lupakan"])
    ap.add_argument("--chat-id", default="")
    ap.add_argument("--gaya", default="")
    a = ap.parse_args(argv)
    try:
        if a.perintah == "daftar":
            out = {"ok": True, "gaya": [_ringkas(muat(n)) for n in daftar()], "bawaan": BAWAAN}
        elif a.perintah == "pratinjau":
            out = {"ok": True, "gambar": pratinjau(), "gaya": [_ringkas(muat(n)) for n in daftar()]}
        elif a.perintah == "pakai":
            data = simpan_gaya(a.chat_id, a.gaya)
            out = {"ok": True, "gaya": _ringkas(muat(data["gaya"]))}
        elif a.perintah == "lihat":
            nama = profil(a.chat_id).get("gaya")
            out = {"ok": True, "gaya": _ringkas(muat(nama)) if nama else None, "bawaan": BAWAAN}
        else:
            out = {"ok": True, "dihapus": lupakan_gaya(a.chat_id)}
    except (GayaError, StyleError) as e:
        out = {"ok": False, "alasan": str(e)}
    except Exception as e:  # noqa: BLE001  (render pratinjau bisa gagal karena Chromium)
        out = {"ok": False, "alasan": f"{type(e).__name__}: {str(e)[:200]}"}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
