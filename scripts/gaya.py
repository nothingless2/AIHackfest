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
  python3 scripts/gaya.py pilihan
  python3 scripts/gaya.py buat --chat-id "<label chat>" --nama kopi-senja [--dasar hype] [--aksen "#B45309"] \\
      [--subtitle-style kata] [--musik on] [--suasana-musik tenang] [--carousel-jumlah 7] ...
  python3 scripts/gaya.py hapus --chat-id "<label chat>" --nama kopi-senja
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
from common import PROJECT_ROOT, STATE_DIR, jalur_kirim, read_json, write_json  # noqa: E402
from overlay_remotion import ANIMASI  # noqa: E402
from style import COLOR_FILTERS, TEXT_FONTS, TEXT_POSITIONS, StyleError  # noqa: E402

GAYA_DIR = os.path.join(PROJECT_ROOT, "config", "gaya")
PROFIL_DIR = os.path.join(STATE_DIR, "profil")
PRATINJAU_DIR = os.path.join(STATE_DIR, "pratinjau_gaya")
BAWAAN = "klasik"
KUSTOM = "kustom"            # nama cadangan: gaya racikan user, disimpan di profilnya (bukan berkas preset)
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
    "potong_pengisi": ("POTONG_PENGISI", ("on", "off")),
}
# Suasana musik SENGAJA tidak termasuk: ia bergantung pada isi konten dan isi pustaka musik, bukan
# tampilan. Uji nyata 1 Okt: preset "hype" (energik) membuat render DITOLAK karena pustaka hanya
# punya lagu tenang, dan di revisi ia akan mengganti lagu video yang user cuma minta ganti gayanya.
#
# Gaya BUATAN USER boleh mengatur audio (bagian `audio`, bukan `editing`), karena dua sebab di atas
# ditangani di sana: suasana dicek terhadap pustaka SAAT gaya disimpan (ditolak di depan, bukan saat
# render), dan di revisi suasana gaya tidak dipasang kecuali user memang minta ganti lagu.
MOODS = ("tenang", "santai", "upbeat", "energik")     # = music_mood.MOODS (dijaga tes; modul itu butuh numpy)
# Level musik = berapa dB di bawah ucapan. 10 adalah bawaan yang DIUKUR (lihat music.py); 14 dan 7
# adalah langkah di sekitarnya dan BELUM diuji dengar -- music.py mencatat 18 dB sudah tak terdengar.
LEVEL_MUSIK = {"pelan": 14.0, "sedang": 10.0, "keras": 7.0}
# kunci -> (env, nilai sah, dest flag hermes_render yang mengalahkannya | None)
AUDIO = {
    "musik": ("CONTENT_FACTORY_MUSIC", ("on", "off"), "music"),
    "suasana_musik": ("CONTENT_FACTORY_MUSIC_MOOD", MOODS, "music_mood"),
    "level_musik": ("MUSIC_BELOW_SPEECH_DB", tuple(LEVEL_MUSIK), None),
    "bersih_suara": ("BERSIH_SUARA", ("on", "off"), "bersih_suara"),
    "suara_narasi": ("TTS_VOICE_GENDER", ("pria", "wanita"), "voice"),
}
# Bawaan carousel untuk gaya ini. Batas slide = carousel.SLIDE_MIN/MAX (dijaga tes; carousel
# mengimpor modul ini, jadi tidak bisa diimpor balik).
CAROUSEL_SLIDE = (3, 10)
CAROUSEL = {
    "platform": ("ig", "tiktok", "keduanya"),
    "latar": ("stok", "polos"),
    "foto_stok": ("on", "off"),
}
MAKS_GAYA_SAYA = 12          # per pemilik: profil tetap kecil dan daftar tetap terbaca di chat


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


def validasi_audio(audio):
    if audio in (None, {}):
        return {}
    if not isinstance(audio, dict):
        raise GayaError("audio harus objek")
    hasil = {}
    for k, v in audio.items():
        if k not in AUDIO:
            raise GayaError(f"pengaturan audio tidak dikenal: {k}")
        if v not in AUDIO[k][1]:
            raise GayaError(f"audio.{k} {v!r} tidak sah. Pilihan: {', '.join(AUDIO[k][1])}")
        hasil[k] = v
    return hasil


def validasi_carousel(carousel):
    if carousel in (None, {}):
        return {}
    if not isinstance(carousel, dict):
        raise GayaError("carousel harus objek")
    hasil = {}
    for k, v in carousel.items():
        if k == "jumlah":
            if isinstance(v, bool) or not isinstance(v, int) or not CAROUSEL_SLIDE[0] <= v <= CAROUSEL_SLIDE[1]:
                raise GayaError(f"carousel.jumlah harus bilangan {CAROUSEL_SLIDE[0]}-{CAROUSEL_SLIDE[1]}")
        elif k not in CAROUSEL:
            raise GayaError(f"pengaturan carousel tidak dikenal: {k}")
        elif v not in CAROUSEL[k]:
            raise GayaError(f"carousel.{k} {v!r} tidak sah. Pilihan: {', '.join(CAROUSEL[k])}")
        hasil[k] = v
    return hasil


def _cek_suasana_musik(suasana):
    """Suasana gaya harus ADA di pustaka saat gaya disimpan: ditolak di sini dengan alasan yang bisa
    dibaca user, bukan nanti saat render (uji 1 Okt). Aturan cocoknya sama dengan music.pick_track."""
    import music
    tracks = music.list_tracks()
    if not tracks:
        raise GayaError("Pustaka musik masih kosong, jadi suasana musik belum bisa dipasang ke gaya. "
                        "Tambahkan lagu ke pustaka dulu.")
    if any(suasana in os.path.basename(t).lower() for t in tracks) or music._cocok_lewat_analisis(tracks, suasana):
        return
    raise GayaError(f"Pustaka musik belum punya lagu bernuansa {suasana}. Tersedia: "
                    f"{', '.join(os.path.basename(t) for t in tracks[:8])}.")


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
    nama = sorted(f[:-5] for f in os.listdir(GAYA_DIR) if f.endswith(".json") and _NAMA.match(f[:-5])
                  and f[:-5] != KUSTOM)
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
    n = str(nama or "").strip().lower()
    data = profil(pemilik)
    if n == KUSTOM or n in _milik(data):
        # Kembali ke gaya buatan sendiri yang sudah pernah dibuat (bukan membuat baru).
        _preset_milik(data, n)
        data.update(gaya=n, diubah=dt.datetime.now(dt.timezone.utc).isoformat())
        write_json(_berkas_profil(pemilik), data)
        return data
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

# ------------------------------------------------------------------ gaya kustom per pemilik

def _hex_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _campur(h, ke, t):
    """Campur warna hex dengan `ke` (rgb) sebesar t (0-1)."""
    return "#" + "".join(f"{round(c + (k - c) * t):02X}" for c, k in zip(_hex_rgb(h), ke))


def palet_dari_aksen(aksen):
    """Satu warna merek -> warna pendamping yang serasi. KODE yang menurunkan (bukan model), supaya
    user cukup menyebut satu warna dan hasilnya tetap satu keluarga."""
    if not isinstance(aksen, str) or not _HEX6.match(aksen):
        raise GayaError(f"warna aksen harus hex #RRGGBB, bukan {aksen!r}")
    putih, hitam = (255, 255, 255), (0, 0, 0)
    return {"aksen": aksen.upper(), "aksen2": _campur(aksen, hitam, 0.28), "sorot": _campur(aksen, putih, 0.55),
            # Kata kunci caption tampil DI ATAS VIDEO: dibuat terang supaya warna merek yang tua
            # (marun, biru dongker) tetap terbaca. Gradien terang -> sedang -> warna merek.
            "kunci": [_campur(aksen, putih, 0.88), _campur(aksen, putih, 0.5), aksen.upper()]}


def _gabung_tema(dasar, ubahan):
    hasil = {**dasar, **{k: v for k, v in ubahan.items() if k != "warna"}}
    if "warna" in dasar or "warna" in ubahan:
        hasil["warna"] = {**dasar.get("warna", {}), **ubahan.get("warna", {})}
    return hasil


def _milik(data):
    """Nama gaya buatan pemilik profil ini (tanpa `kustom` lama, yang punya kuncinya sendiri)."""
    g = data.get("gaya_saya")
    return sorted(n for n in g if _NAMA.match(str(n))) if isinstance(g, dict) else []


def _catatan(data, nama):
    return data.get("kustom") if nama == KUSTOM else (data.get("gaya_saya") or {}).get(nama)


def _label_milik(nama):
    return nama.replace("-", " ").replace("_", " ").title()


def _preset_milik(data, nama):
    """Preset dari catatan gaya buatan user: preset dasar + ubahan user, SEMUA bagian divalidasi
    ULANG (profil adalah berkas di disk; isinya tidak dipercaya begitu saja)."""
    k = _catatan(data, nama)
    if not isinstance(k, dict):
        if nama == KUSTOM:
            raise GayaError("Chat ini belum punya gaya kustom. Buat dulu (gaya.py kustom).")
        raise GayaError(f"Chat ini belum punya gaya bernama {nama!r}.")
    dasar = muat(k.get("dasar") or BAWAAN)
    tema = validasi_tema(_gabung_tema(dasar["tema"], k.get("tema") or {}))
    editing = validasi_editing({**dasar["editing"], **(k.get("editing") or {})})
    label = f"Kustom (dasar {dasar['label']})" if nama == KUSTOM else _label_milik(nama)
    return {"nama": nama, "label": label, "deskripsi": f"Gaya racikanmu sendiri (dasar {dasar['label']}).",
            "tema": tema, "editing": editing, "audio": validasi_audio(k.get("audio")),
            "carousel": validasi_carousel(k.get("carousel")), "dasar": dasar["nama"], "milik_sendiri": True}


def _preset_kustom(data):
    return _preset_milik(data, KUSTOM)


def _timpa(lama, baru):
    """Gabung pengaturan datar; nilai None di `baru` MENGHAPUS kuncinya (kembali ke gaya dasar)."""
    hasil = {**(lama or {}), **(baru or {})}
    return {k: v for k, v in hasil.items() if v is not None}


def simpan_gaya_saya(pemilik, nama, dasar=None, ubahan=None):
    """Buat atau ubah gaya buatan pemilik, lalu jadikan gayanya. `ubahan` = {tema, editing, audio,
    carousel}, masing-masing parsial: yang tidak disebut tetap seperti sebelumnya. Di tema,
    `warna.aksen` saja sudah cukup (pendampingnya diturunkan kode). Tidak sah -> GayaError, profil
    TIDAK berubah."""
    if not str(pemilik or "").strip():
        raise GayaError("pemilik profil tidak diketahui")
    n = str(nama or "").strip().lower()
    if not _NAMA.match(n):
        raise GayaError("Nama gaya harus 2-31 karakter: huruf kecil, angka, '-' atau '_', diawali huruf.")
    if n != KUSTOM and n in daftar():
        raise GayaError(f"Nama {n!r} sudah dipakai gaya bawaan. Pilih nama lain.")
    data = profil(pemilik)
    lama = _catatan(data, n) or {}
    if n != KUSTOM and not lama and len(_milik(data)) >= MAKS_GAYA_SAYA:
        raise GayaError(f"Chat ini sudah punya {MAKS_GAYA_SAYA} gaya buatan sendiri. Hapus salah satu dulu.")
    # Dasar: yang disebut > dasar gaya ini sebelumnya > preset yang sedang dipakai > bawaan.
    # Dasar yang disebut atau tersimpan tapi sudah tidak ada -> GayaError (bukan diam-diam ganti dasar).
    if dasar or lama.get("dasar"):
        nama_dasar = muat(dasar or lama["dasar"])["nama"]
    else:
        aktif = str(data.get("gaya") or "").lower()
        nama_dasar = aktif if aktif in daftar() else BAWAAN
    ubahan = dict(ubahan or {})
    asing = set(ubahan) - {"tema", "editing", "audio", "carousel"}
    if asing:
        raise GayaError(f"bagian gaya tidak dikenal: {', '.join(sorted(asing))}")
    tema = dict(ubahan.get("tema") or {})
    warna = dict(tema.get("warna") or {})
    if "aksen" in warna:
        warna = {**palet_dari_aksen(warna["aksen"]), **warna}
    catatan = {"dasar": nama_dasar,
               "tema": _gabung_tema(lama.get("tema") or {}, {**tema, **({"warna": warna} if warna else {})})}
    for bagian in ("editing", "audio", "carousel"):
        isi = _timpa(lama.get(bagian), ubahan.get(bagian))
        if isi:
            catatan[bagian] = isi
    preset = _preset_milik({"kustom": catatan} if n == KUSTOM else {"gaya_saya": {n: catatan}}, n)
    suasana = (ubahan.get("audio") or {}).get("suasana_musik")
    if suasana:
        _cek_suasana_musik(suasana)
    data = {**data, "pemilik": str(pemilik).strip(), "gaya": n,
            "diubah": dt.datetime.now(dt.timezone.utc).isoformat()}
    if n == KUSTOM:
        data["kustom"] = catatan
    else:
        data["gaya_saya"] = {**{m: (data.get("gaya_saya") or {})[m] for m in _milik(data)}, n: catatan}
    write_json(_berkas_profil(pemilik), data)
    return preset


def simpan_kustom(pemilik, dasar=None, ubahan=None):
    """Gaya kustom tanpa nama (satu per chat). `ubahan` = tema parsial."""
    return simpan_gaya_saya(pemilik, KUSTOM, dasar, {"tema": ubahan or {}})


def hapus_gaya_saya(pemilik, nama):
    """(dihapus, gaya_aktif_dilepas). Gaya yang sedang dipakai dilepas: chat kembali ke bawaan."""
    n = str(nama or "").strip().lower()
    data = profil(pemilik)
    if n == KUSTOM:
        ada = data.pop("kustom", None) is not None
    else:
        sisa = {m: data["gaya_saya"][m] for m in _milik(data) if m != n}
        ada = n in _milik(data)
        if ada:
            data["gaya_saya"] = sisa
    if not ada:
        return False, False
    dilepas = data.get("gaya") == n
    if dilepas:
        data.pop("gaya")
    write_json(_berkas_profil(pemilik), data)
    return True, dilepas


def daftar_milik(pemilik):
    """Gaya buatan pemilik yang MASIH sah (yang rusak/dasarnya hilang tidak ditawarkan)."""
    data = profil(pemilik)
    hasil = []
    for n in _milik(data) + ([KUSTOM] if isinstance(data.get("kustom"), dict) else []):
        try:
            hasil.append(_preset_milik(data, n))
        except GayaError:
            continue
    return hasil


def pilih(eksplisit, pemilik):
    """(preset, sumber). sumber: 'diminta' | 'profil' | 'bawaan' (| 'bawaan_profil_tidak_berlaku').

    Nama eksplisit yang salah -> GayaError (user/agent harus tahu). Gaya di profil yang presetnya
    sudah dihapus (atau buatan sendiri yang tak lagi sah) TIDAK menggagalkan render: jatuh ke bawaan
    dan sumbernya menyebutkan itu. Gaya buatan sendiri hanya terlihat oleh pemiliknya."""
    data = profil(pemilik)
    if eksplisit:
        n = str(eksplisit).strip().lower()
        if n == KUSTOM or n in _milik(data):
            return _preset_milik(data, n), "diminta"
        try:
            return muat(eksplisit), "diminta"
        except GayaError:
            milik = [p["nama"] for p in daftar_milik(pemilik)]
            raise GayaError(f"Gaya {eksplisit!r} tidak dikenal. Pilihan: {', '.join(daftar() + milik)}.")
    di_profil = data.get("gaya")
    if di_profil:
        try:
            if di_profil == KUSTOM or di_profil in _milik(data):
                return _preset_milik(data, di_profil), "profil"
            return muat(di_profil), "profil"
        except GayaError:
            return muat(BAWAAN), "bawaan_profil_tidak_berlaku"
    return muat(BAWAAN), "bawaan"


def env_editing(preset, args):
    """{ENV: nilai} untuk knob preset yang TIDAK diisi user (args.<knob> masih None)."""
    return {EDITING[k][0]: v for k, v in preset["editing"].items() if getattr(args, k, None) is None}


def audio_terpasang(preset, args):
    """{kunci: nilai} pengaturan audio gaya yang BERLAKU di run ini (yang tidak diisi user sendiri).

    Dua pengecualian, keduanya supaya gaya tidak mengalahkan permintaan user yang lebih spesifik:
    - user mengirim lagunya sendiri (`music_file`): `musik` dan `suasana_musik` gaya tidak dipasang;
    - revisi tanpa "ganti lagu": `suasana_musik` tidak dipasang, supaya lagu video yang direvisi
      tidak berganti hanya karena gayanya diganti (lihat catatan di EDITING)."""
    hasil = {}
    for k, v in (preset.get("audio") or {}).items():
        dest = AUDIO[k][2]
        if dest and getattr(args, dest, None) is not None:
            continue
        if k in ("musik", "suasana_musik") and getattr(args, "music_file", None):
            continue
        if k == "suasana_musik" and getattr(args, "revisi", None) and not getattr(args, "ganti_musik", False):
            continue
        hasil[k] = v
    return hasil


def env_audio(preset, args):
    """{ENV: nilai} dari audio_terpasang()."""
    return {AUDIO[k][0]: (f"{LEVEL_MUSIK[v]:g}" if k == "level_musik" else v)
            for k, v in audio_terpasang(preset, args).items()}


def pasang(preset, args, environ=None):
    """Pasang preset ke env proses render (diwarisi semua tahap)."""
    env = os.environ if environ is None else environ
    env.update(env_editing(preset, args))
    env.update(env_audio(preset, args))
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

def pratinjau_satu(preset, keluar_dir=None):
    """Satu kartu contoh untuk SATU preset (dipakai gaya kustom: tidak di-cache bersama)."""
    import overlay_remotion as orr
    keluar_dir = keluar_dir or PRATINJAU_DIR
    os.makedirs(keluar_dir, exist_ok=True)
    out = os.path.join(keluar_dir, f"pratinjau_{preset['nama']}_{dt.datetime.now():%Y%m%d_%H%M%S_%f}.jpg")
    return orr.render_pratinjau_gaya([preset], out)


def _ringkas(p):
    return {"nama": p["nama"], "label": p["label"], "deskripsi": p["deskripsi"]}


def _rinci(p):
    """Isi lengkap gaya buatan sendiri, untuk dibacakan agent ke user."""
    return {"gaya": _ringkas(p), "dasar": p["dasar"], "tema": p["tema"], "editing": p["editing"],
            "audio": p["audio"], "carousel": p["carousel"]}


LEPAS = "bawaan"             # nilai flag: hapus pengaturan ini dari gaya (kembali ke gaya dasar)


def _flag(nama):
    return "--" + nama.replace("_", "-")


def _bagian_dari_argumen(a, tabel, awalan=""):
    hasil = {}
    for k in tabel:
        v = getattr(a, (awalan + k), None)
        if v is not None:
            hasil[k] = None if str(v).strip().lower() == LEPAS else str(v).strip().lower()
    return hasil


def _ubahan_dari_argumen(a):
    """Flag CLI `buat`/`kustom` -> ubahan per bagian. Nilai dicek validasi_* saat disimpan."""
    warna = {k: v for k, v in (("aksen", a.aksen), ("aksen2", a.aksen2), ("sorot", a.sorot), ("teks", a.teks),
                               ("teks_sub", a.teks_sub), ("kartu", a.kartu), ("latar", a.latar)) if v}
    if a.kunci:
        warna["kunci"] = [w.strip() for w in a.kunci.split(",") if w.strip()]
    tema = {"warna": warna} if warna else {}
    if a.font:
        tema["font"] = {"judul": a.font.strip().lower()}
    if a.sudut is not None:
        tema["sudut"] = a.sudut
    if a.cahaya:
        tema["cahaya"] = a.cahaya == "on"
    if a.gerak:
        tema["gerak"] = a.gerak
    carousel = _bagian_dari_argumen(a, CAROUSEL, "carousel_")
    if a.carousel_jumlah is not None:
        if str(a.carousel_jumlah).strip().lower() == LEPAS:
            carousel["jumlah"] = None
        else:
            try:
                carousel["jumlah"] = int(a.carousel_jumlah)
            except ValueError:
                raise GayaError(f"carousel.jumlah harus bilangan {CAROUSEL_SLIDE[0]}-{CAROUSEL_SLIDE[1]}")
    ubahan = {"tema": tema, "editing": _bagian_dari_argumen(a, EDITING),
              "audio": _bagian_dari_argumen(a, AUDIO), "carousel": carousel}
    return {k: v for k, v in ubahan.items() if v}


def pilihan():
    """Semua nilai yang sah, supaya agent tidak menebak (nilai di luar daftar ini ditolak)."""
    return {"dasar": daftar(), "font": list(TEXT_FONTS), "gerak": list(GERAK), "sudut": list(SUDUT),
            "editing": {k: list(v[1]) for k, v in EDITING.items()},
            "audio": {k: list(v[1]) for k, v in AUDIO.items()},
            "carousel": {**{k: list(v) for k, v in CAROUSEL.items()}, "jumlah": list(CAROUSEL_SLIDE)},
            "lepas": LEPAS, "maks_gaya_saya": MAKS_GAYA_SAYA}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Preset gaya tampilan + editing")
    ap.add_argument("perintah", choices=["daftar", "pratinjau", "pakai", "lihat", "lupakan", "kustom",
                                         "buat", "hapus", "pilihan"])
    ap.add_argument("--chat-id", default="")
    ap.add_argument("--gaya", default="")
    ap.add_argument("--nama", default="", help="buat/hapus: nama gaya buatan sendiri.")
    # kustom/buat: racik gaya sendiri di atas preset --dasar. Warna hex #RRGGBB; --aksen saja sudah cukup.
    ap.add_argument("--dasar", default=None)
    for nama in ("aksen", "aksen2", "sorot", "teks", "teks-sub", "kartu", "latar", "kunci", "font", "gerak"):
        ap.add_argument(f"--{nama}", default=None)
    ap.add_argument("--sudut", type=float, default=None)
    ap.add_argument("--cahaya", choices=["on", "off"], default=None)
    # Editing, audio, carousel: nilainya dicek validasi_* (pesan tolak menyebut pilihan yang sah).
    for k in list(EDITING) + list(AUDIO):
        ap.add_argument(_flag(k), default=None)
    for k in list(CAROUSEL) + ["jumlah"]:
        ap.add_argument(_flag("carousel_" + k), default=None)
    a = ap.parse_args(argv)
    try:
        if a.perintah == "daftar":
            out = {"ok": True, "gaya": [_ringkas(muat(n)) for n in daftar()], "bawaan": BAWAAN}
            if a.chat_id.strip():
                out["gaya_saya"] = [{**_ringkas(p), "dasar": p["dasar"]} for p in daftar_milik(a.chat_id)]
                out["aktif"] = profil(a.chat_id).get("gaya")
        elif a.perintah == "pilihan":
            out = {"ok": True, **pilihan()}
        elif a.perintah == "pratinjau":
            out = {"ok": True, "gambar": pratinjau(), "gaya": [_ringkas(muat(n)) for n in daftar()]}
        elif a.perintah == "pakai":
            simpan_gaya(a.chat_id, a.gaya)
            out = {"ok": True, "gaya": _ringkas(pilih(None, a.chat_id)[0])}
        elif a.perintah in ("kustom", "buat"):
            nama = KUSTOM if a.perintah == "kustom" else a.nama
            ubahan = _ubahan_dari_argumen(a)
            if not ubahan and not a.dasar:
                raise GayaError("Sebutkan yang mau diubah, mis. --aksen \"#B91C1C\" atau --font elegan.")
            preset = simpan_gaya_saya(a.chat_id, nama, a.dasar, ubahan)
            out = {"ok": True, **_rinci(preset), "gambar": None}
            try:
                out["gambar"] = pratinjau_satu(preset)
            except Exception as e:  # noqa: BLE001  (gaya sudah tersimpan; pratinjau gagal dilaporkan)
                out["pratinjau_gagal"] = f"{type(e).__name__}: {str(e)[:160]}"
        elif a.perintah == "hapus":
            dihapus, dilepas = hapus_gaya_saya(a.chat_id, a.nama)
            if not dihapus:
                raise GayaError(f"Chat ini tidak punya gaya buatan sendiri bernama {a.nama!r}.")
            out = {"ok": True, "dihapus": a.nama.strip().lower(), "gaya_aktif_dilepas": dilepas}
        elif a.perintah == "lihat":
            data = profil(a.chat_id)
            nama = data.get("gaya")
            if nama == KUSTOM or nama in _milik(data):
                out = {"ok": True, **_rinci(_preset_milik(data, nama))}
            else:
                out = {"ok": True, "gaya": _ringkas(muat(nama)) if nama else None, "bawaan": BAWAAN}
        else:
            out = {"ok": True, "dihapus": lupakan_gaya(a.chat_id)}
    except (GayaError, StyleError) as e:
        out = {"ok": False, "alasan": str(e)}
    except Exception as e:  # noqa: BLE001  (render pratinjau bisa gagal karena Chromium)
        out = {"ok": False, "alasan": f"{type(e).__name__}: {str(e)[:200]}"}
    if out["ok"] and out.get("gambar"):
        out["kirim"] = jalur_kirim(out["gambar"])
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
