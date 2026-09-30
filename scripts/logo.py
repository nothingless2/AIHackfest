"""Kartu logo merek yang DIUCAPKAN (contoh video user 29 Sep, gambar 1).

Merek diambil dari DAFTAR TERTUTUP config/merek_logo.json, bukan dicocokkan otomatis ke katalog
simple-icons. Alasannya terukur: katalog itu punya "Hermes" milik myHermes (kurir Jerman), jadi
pencocokan otomatis akan memasang logo perusahaan kurir di video tentang Hermes Agent (aturan #5:
kode tidak boleh menebak fakta). Merek di luar daftar tidak jadi logo -- tanpa kartu, bukan kartu
dengan logo salah.
"""

import json
import os
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAFTAR_PATH = os.path.join(PROJECT_ROOT, "config", "merek_logo.json")
IKON_DIR = os.path.join(PROJECT_ROOT, "remotion", "node_modules", "simple-icons")
IKON_PATH = os.path.join(IKON_DIR, "data", "simple-icons.json")
SVG_DIR = os.path.join(IKON_DIR, "icons")
MAKS = 3
JARAK = 4.0        # dtk antar-mulai kartu logo
LAMA = 2.2


def aktif():
    if (os.getenv("LOGO_MEREK") or "1").strip().lower() in ("0", "off", "mati", "false"):
        return False
    return (os.getenv("SUBTITLE_STYLE") or "").strip().lower() == "dinamis"


def _norm(t):
    return re.sub(r"[^\w ]", "", str(t or "").lower()).strip()


def daftar_merek(path=None):
    try:
        with open(path or DAFTAR_PATH, encoding="utf-8") as f:
            return {_norm(k): v for k, v in (json.load(f).get("merek") or {}).items()}
    except (OSError, ValueError):
        return {}


def svg_path(slug, folder=None):
    """Atribut d= dari berkas SVG ikon (data/simple-icons.json hanya memuat warna & nama)."""
    try:
        with open(os.path.join(folder or SVG_DIR, f"{slug}.svg"), encoding="utf-8") as f:
            isi = f.read()
    except OSError:
        return ""
    m = re.search(r'\sd="([^"]+)"', isi)
    return m.group(1) if m else ""


def katalog_ikon(path=None, svg_folder=None):
    """{slug: {"hex", "title"}} dari data simple-icons (path SVG dibaca terpisah saat dipakai)."""
    try:
        with open(path or IKON_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    hasil = {}
    for i in data if isinstance(data, list) else []:
        slug = i.get("slug") or _norm(i.get("title")).replace(" ", "")
        if slug:
            hasil[slug] = {"hex": i.get("hex") or "111111", "title": i.get("title") or slug,
                           "slug": slug}
    return hasil


def cari(kata_waktu, durasi, *, hindari=(), merek=None, ikon=None, maks=MAKS, jarak=JARAK,
         svg_folder=None):
    """[{mulai, selesai, merek, hex, path}] untuk merek yang BENAR-BENAR diucapkan, urut & berjarak."""
    merek = daftar_merek() if merek is None else merek
    ikon = katalog_ikon() if ikon is None else ikon
    if not merek or not ikon:
        return []
    kata = [(_norm(w.get("word")), float(w["start"]), float(w["end"])) for w in kata_waktu or []]
    hasil, dipakai = [], set()
    i = 0
    while i < len(kata):
        for n in (2, 1):                       # frasa dua kata dulu ("google drive")
            if i + n > len(kata):
                continue
            frasa = " ".join(k for k, _, _ in kata[i:i + n]).strip()
            slug = merek.get(frasa)
            ikn = ikon.get(slug) if slug else None
            if not ikn or frasa in dipakai:
                continue
            svg = svg_path(ikn["slug"], svg_folder)
            if not svg:                        # ikon tanpa berkas SVG: tanpa kartu, bukan kartu kosong
                continue
            a = kata[i][1]
            b = min(a + LAMA, durasi)
            if b - a < 0.8:
                break
            if any(a < y and b > x for x, y in hindari):
                break
            if hasil and a - hasil[-1]["mulai"] < jarak:
                break
            hasil.append({"mulai": round(a, 3), "selesai": round(b, 3), "merek": ikn["title"],
                          "hex": ikn["hex"], "path": svg})
            dipakai.add(frasa)
            i += n - 1
            break
        if len(hasil) >= maks:
            break
        i += 1
    return hasil


def posisi(kotak_wajah, lebar, tinggi, kartu_w, kartu_h):
    """(x, y) kartu: sisi dengan ruang lebih lebar dari wajah, sejajar mata; tanpa wajah -> kanan atas."""
    tepi = int(lebar * 0.04)
    if not kotak_wajah:
        return lebar - kartu_w - tepi, int(tinggi * 0.22)
    fx, fy, fw, fh = kotak_wajah
    kiri, kanan = fx, lebar - (fx + fw)
    x = tepi if kiri >= kanan else lebar - kartu_w - tepi
    y = max(tepi, min(int(fy + fh * 0.25), tinggi - kartu_h - tepi))
    return int(x), int(y)
