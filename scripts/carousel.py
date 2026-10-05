"""Carousel Instagram (1080x1350) / TikTok (1080x1920) dari teks user atau transkrip video.

Alur: satu panggilan LLM menyusun slide (hook / isi / daftar / statistik / kutipan / cta) ->
KODE memvalidasi (jumlah kata per jenis, hook di depan & cta di belakang, 3-10 slide, angka dan
kutipan HARUS ada di sumber -- aturan #5) dan meminta tulis ulang paling banyak sekali -> Remotion
merender semua slide dalam satu sesi Chromium dengan tema preset gaya yang sama dengan video ->
KODE mengukur hasilnya dari lapisan teks yang dirender terpisah: teks tidak keluar dari kotak aman,
tidak masuk zona UI TikTok, tidak menimpa wajah, dan kontrasnya terbaca.

Satu baris JSON di stdout. Dipanggil agent di latar belakang (LLM + render ±1 menit):
  python3 scripts/carousel.py --chat-id "<label chat>" --teks "<topik / naskah user>" \\
      [--dari-run RUN_ID] [--foto PATH]... [--stok] [--gaya NAMA] [--platform ig|tiktok|keduanya] \\
      [--jumlah 3-10]
"""

import argparse
import base64
import io
import json
import os
import re
import secrets
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import LLM_MODEL, PROJECT_ROOT, RAW_DIR, chat_json, jalur_kirim, write_json  # noqa: E402
import gaya  # noqa: E402

CAROUSEL_DIR = os.path.join(PROJECT_ROOT, "workspace", "carousel")
UKURAN = {"ig": (1080, 1350), "tiktok": (1080, 1920)}
SLIDE_MIN, SLIDE_MAX = 3, 10          # 10 = batas carousel Instagram
JUMLAH_BAWAAN = 6
# jenis -> {field: (min_kata, maks_kata)}; min 0 = boleh kosong.
BATAS = {
    "hook": {"judul": (2, 9), "sub": (0, 16)},
    "isi": {"judul": (1, 8), "isi": (4, 32)},
    "daftar": {"judul": (1, 7)},
    "statistik": {"angka": (1, 2), "label": (2, 12)},
    "kutipan": {"teks": (4, 28), "oleh": (0, 5)},
    "cta": {"judul": (2, 8), "sub": (0, 14), "tombol": (0, 4)},
}
BUTIR = (2, 5)
BUTIR_KATA = 10
# Ikon: daftar TERTUTUP yang digambar komponen Remotion sebagai SVG inline (aturan #5 CLAUDE.md:
# model hanya MEMILIH nomor/nama dari daftar, kode yang menggambar). Tidak ada berkas gambar yang
# diunduh, jadi tidak ada masalah lisensi dan tidak ada permintaan jaringan saat render.
# Harus sama dengan kunci IKON di remotion/src/Carousel.jsx (dijaga tes).
IKON = ("lampu", "centang", "silang", "peringatan", "roket", "grafik", "jam", "uang",
        "bintang", "api", "orang", "obrolan", "target", "gembok", "hati", "kunci")
# Watermark: teks pendek milik user (mis. "@akunku"). Dibatasi ketat karena nilainya ikut ke render.
WATERMARK_MAKS = 24
_WATERMARK = re.compile(r"^[\w @.\-_&/|+']{1,%d}$" % WATERMARK_MAKS, re.UNICODE)
HASHTAG_MAKS = 5
CAPTION_MAKS = 2200                   # batas caption Instagram
KONTRAS_MIN = 3.0                     # WCAG AA untuk teks besar (semua teks slide >= 24 px)


class CarouselError(RuntimeError):
    def __init__(self, kode, pesan):
        super().__init__(pesan)
        self.kode = kode


# ------------------------------------------------------------------ validasi naskah

def _kata(t):
    return len(str(t or "").split())


def _norm(t):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(t or "").lower())).strip()


def _angka(t):
    """Bilangan di teks, dinormalkan: "1.000" -> "1000", "1,5" -> "1.5"."""
    hasil = set()
    for m in re.findall(r"\d+(?:[.,]\d+)*", str(t or "")):
        m = re.sub(r"[.,](?=\d{3}(?:\D|$))", "", m)
        hasil.add(m.replace(",", "."))
    return hasil


def validasi(data, sumber):
    """(slides, caption, hashtags, masalah). Yang dipakai hanya field yang dikenal kode."""
    masalah = []
    if not isinstance(data, dict) or not isinstance(data.get("slide"), list):
        return [], "", [], ["balasan harus objek dengan daftar 'slide'"]
    angka_sumber, teks_sumber = _angka(sumber), _norm(sumber)
    slides = []
    for i, s in enumerate(data["slide"], 1):
        if not isinstance(s, dict) or s.get("jenis") not in BATAS:
            masalah.append(f"slide {i}: jenis tidak dikenal ({(s or {}).get('jenis') if isinstance(s, dict) else s!r})")
            continue
        jenis = s["jenis"]
        bersih = {"jenis": jenis}
        salah = []
        for f, (lo, hi) in BATAS[jenis].items():
            v = str(s.get(f) or "").strip()
            if not lo <= _kata(v) <= hi:
                salah.append(f"{f} harus {lo}-{hi} kata (sekarang {_kata(v)})")
            elif v:
                bersih[f] = v
        if jenis == "daftar":
            butir = [str(b).strip() for b in (s.get("butir") or []) if str(b).strip()]
            if not BUTIR[0] <= len(butir) <= BUTIR[1] or any(_kata(b) > BUTIR_KATA for b in butir):
                salah.append(f"butir harus {BUTIR[0]}-{BUTIR[1]} poin, masing-masing maks {BUTIR_KATA} kata")
            bersih["butir"] = butir
        if jenis == "statistik":
            a = _angka(bersih.get("angka"))
            if not a:
                salah.append("angka tidak berisi bilangan")
            elif not a <= angka_sumber:
                salah.append(f"angka {bersih.get('angka')!r} tidak ada di sumber (jangan mengarang angka)")
        if jenis == "kutipan" and bersih.get("teks") and _norm(bersih["teks"]) not in teks_sumber:
            salah.append("kutipan harus kalimat yang PERSIS ada di sumber")
        if jenis == "hook":
            sorot = str(s.get("sorot") or "").strip()
            if sorot and _norm(sorot) in _norm(bersih.get("judul", "")).split():
                bersih["sorot"] = sorot
            latar = str(data.get("kata_kunci_latar") or "").strip()
            if latar and re.fullmatch(r"[A-Za-z][A-Za-z ]{1,40}", latar) and _kata(latar) <= 4:
                bersih["cari_latar"] = latar
        cari = str(s.get("kata_kunci_gambar") or "").strip()
        if cari and re.fullmatch(r"[A-Za-z][A-Za-z ]{1,40}", cari) and _kata(cari) <= 4:
            bersih["cari"] = cari
        # Ikon: nama di luar daftar DIABAIKAN diam-diam (slide tetap jadi, hanya tanpa ikon) --
        # ikon itu hiasan, tidak boleh menggagalkan carousel yang isinya sudah benar.
        ikon = str(s.get("ikon") or "").strip().lower()
        if ikon in IKON:
            bersih["ikon"] = ikon
        if salah:
            masalah.append(f"slide {i} ({jenis}): " + "; ".join(salah))
            continue
        slides.append(bersih)
    if not SLIDE_MIN <= len(slides) <= SLIDE_MAX:
        masalah.append(f"jumlah slide sah {len(slides)}, harus {SLIDE_MIN}-{SLIDE_MAX}")
    if slides and slides[0]["jenis"] != "hook":
        masalah.append("slide pertama harus jenis hook")
    if slides and slides[-1]["jenis"] != "cta":
        masalah.append("slide terakhir harus jenis cta")
    caption = str(data.get("caption") or "").strip()[:CAPTION_MAKS]
    tag = [h for h in (data.get("hashtags") or []) if isinstance(h, str)
           and re.fullmatch(r"#[0-9A-Za-z_]{2,30}", h.strip())][:HASHTAG_MAKS]
    return slides, caption, [h.strip() for h in tag], masalah


def _struktur_sah(slides):
    return (SLIDE_MIN <= len(slides) <= SLIDE_MAX and slides[0]["jenis"] == "hook"
            and slides[-1]["jenis"] == "cta")


PROMPT = """Kamu penulis carousel Instagram/TikTok berbahasa Indonesia. Susun TEPAT {n} slide dari SUMBER.

Aturan:
- Slide 1 jenis "hook": judul 2-9 kata yang bikin orang berhenti scroll; "sorot" = SATU kata terpenting dari judul itu; "sub" opsional maks 16 kata.
- Slide terakhir jenis "cta": ajakan (simpan/bagikan/ikuti/komentar), judul 2-8 kata, "sub" maks 14 kata, "tombol" maks 4 kata.
- Di antaranya pilih jenis yang paling cocok dengan isi:
  "isi" (judul maks 8 kata + isi 4-32 kata), "daftar" (judul maks 7 kata + butir 2-5 poin, tiap poin maks 10 kata),
  "statistik" (angka + label 2-12 kata) HANYA bila angkanya tertulis di SUMBER, salin persis,
  "kutipan" (teks 4-28 kata + oleh) HANYA kalimat yang benar-benar ada di SUMBER, salin persis.
- Jangan menambah fakta, angka, nama, atau klaim yang tidak ada di SUMBER.
- Bahasa Indonesia sehari-hari, kalimat pendek dan jelas. Tanpa tanda pagar di slide.
- "kata_kunci_gambar" opsional per slide: 1-4 kata BAHASA INGGRIS untuk mencari foto stok yang cocok.
- "kata_kunci_latar": 1-4 kata BAHASA INGGRIS untuk SATU foto latar estetik yang cocok dengan topik
  (suasana/benda, bukan teks), mis. "minimal desk laptop".
- "caption": teks postingan 1-3 kalimat; "hashtags": 3-5 tagar relevan.
- "ikon" opsional per slide: SATU nama dari daftar ini saja, yang paling cocok dengan isi slide itu —
  {ikon}. Nama di luar daftar diabaikan. Kosongkan kalau tidak ada yang cocok; jangan dipaksakan.
{platform}
Balas HANYA JSON:
{{"slide": [{{"jenis": "hook", "judul": "...", "sorot": "...", "sub": "...", "ikon": "...", "kata_kunci_gambar": "..."}},
            {{"jenis": "isi", "judul": "...", "isi": "...", "ikon": "..."}},
            {{"jenis": "daftar", "judul": "...", "butir": ["...", "..."]}},
            {{"jenis": "cta", "judul": "...", "sub": "...", "tombol": "..."}}],
 "kata_kunci_latar": "...", "caption": "...", "hashtags": ["#...", "#..."]}}

SUMBER:
\"\"\"{sumber}\"\"\"
"""


def minta_slide(sumber, jumlah, platform, chat=None):
    """(slides, caption, hashtags, catatan). Satu panggilan + paling banyak satu tulis ulang."""
    chat = chat or chat_json
    teks_platform = {"ig": "Untuk Instagram.", "tiktok": "Untuk TikTok (foto geser).",
                     "keduanya": "Untuk Instagram dan TikTok."}[platform]
    pesan = [{"role": "user", "content": PROMPT.format(n=jumlah, platform=teks_platform, ikon=", ".join(IKON),
                                                       sumber=sumber[:6000])}]
    data = chat(pesan, model=LLM_MODEL, label="naskah carousel")
    slides, caption, tag, masalah = validasi(data, sumber)
    if not masalah:
        return slides, caption, tag, []
    pesan += [{"role": "assistant", "content": json.dumps(data, ensure_ascii=False)[:6000]},
              {"role": "user", "content": "Perbaiki masalah ini, lalu kirim ulang JSON LENGKAP:\n- "
                                          + "\n- ".join(masalah)}]
    data2 = chat(pesan, model=LLM_MODEL, label="naskah carousel (tulis ulang)")
    slides2, caption2, tag2, masalah2 = validasi(data2, sumber)
    if not masalah2:
        return slides2, caption2, tag2, ["naskah ditulis ulang sekali: " + "; ".join(masalah)[:300]]
    if slides2 and _struktur_sah(slides2):
        # Slide yang tetap tidak sah dibuang; sisanya masih carousel utuh (hook ... cta).
        return slides2, caption2, tag2, ["slide yang tetap tidak sah dibuang: " + "; ".join(masalah2)[:300]]
    raise CarouselError("naskah_tidak_sah", "Naskah carousel dari model tetap tidak sah setelah ditulis "
                                            "ulang: " + "; ".join(masalah2)[:400])


# ------------------------------------------------------------------ sumber

def sumber_dari_run(run_id, chat_id):
    """(teks, [path video]) dari catatan run: ucapan asli (transkrip) + naskah + permintaan user.
    Milik chat lain / kedaluwarsa -> RevisiError (gagal-tertutup, sama dengan revisi)."""
    import revisi
    import kamus
    rec = revisi.muat(run_id, chat_id)
    brief = rec.get("brief") or {}
    kamus.koreksi_brief(brief, kamus.daftar(chat_id))       # ejaan istilah chat ini
    seg = brief.get("transcript_segments") or {}
    ucapan = " ".join(str(s.get("text") or "").strip() for n in rec.get("bahan", [])
                      for s in (seg.get(n) or []) if isinstance(s, dict))
    bagian = [brief.get("konteks_user") or "", ucapan, brief.get("full_voice_over") or ""]
    teks = "\n".join(b.strip() for b in bagian if b and b.strip())
    video = [os.path.join(RAW_DIR, n) for n in rec.get("bahan", [])
             if n.lower().endswith((".mp4", ".mov", ".m4v", ".webm", ".mkv"))
             and os.path.isfile(os.path.join(RAW_DIR, n))]
    return teks, video


# ------------------------------------------------------------------ gambar

def _cover(im, W, H):
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(im).convert("RGB")
    s = max(W / im.width, H / im.height)
    im = im.resize((max(W, round(im.width * s)), max(H, round(im.height * s))), Image.LANCZOS)
    x, y = (im.width - W) // 2, (im.height - H) // 2
    return im.crop((x, y, x + W, y + H))


def siapkan_stiker(path):
    """(data_url PNG) untuk stiker custom, mempertahankan transparansi alpha."""
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGBA")
        b = io.BytesIO()
        im.save(b, "PNG")
        return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def siapkan_gambar(path, W, H):
    """(data_url JPEG, kotak_wajah|None) untuk latar slide WxH."""
    import numpy as np
    from PIL import Image
    import wajah
    with Image.open(path) as im:
        kanvas = _cover(im, W, H)
    kecil = kanvas.convert("L").resize((360, round(360 * H / W)))
    k = wajah.cari(np.asarray(kecil))
    kotak = None
    if k:
        f = W / 360
        kotak = tuple(int(round(v * f)) for v in k)
    b = io.BytesIO()
    kanvas.save(b, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode(), kotak


def frame_terbaik(videos, W, H, folder):
    """Frame berwajah paling tajam dari video run (logika cover), sebagai JPEG WxH, atau None."""
    import sampul
    from vision import durasi_video as durasi
    for v in videos:
        try:
            calon = sampul.detik_kandidat(durasi(v) or 0)
            detik, _ = sampul.pilih_frame(v, calon)
            if detik is None:
                continue
            return sampul.ambil_frame_jpg(v, detik, os.path.join(folder, "frame.jpg"), W, H)
        except Exception as e:  # noqa: BLE001  (frame gagal != carousel gagal; dilaporkan)
            print(f"[warn] carousel: frame dari {os.path.basename(v)} gagal: {e}", file=sys.stderr)
    return None


def foto_stok(query, W, H, folder, pilih=0):
    """(path, kredit) foto Pexels untuk query, atau melempar. Jaringan lewat broll (tes menggantinya).
    `pilih`: geser pilihan di antara hasil yang sah, supaya carousel berbeda tidak selalu memakai
    foto yang sama untuk kata kunci yang sama."""
    import urllib.parse
    import broll
    if not broll.kunci():
        raise CarouselError("stok_tidak_siap", "PEXELS_API_KEY belum diisi")
    url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(
        {"query": query, "orientation": "portrait", "per_page": 8})
    data = broll._http_get_json(url, {"Authorization": broll.kunci(), "User-Agent": "content-factory/1.0"})
    sah = []
    for f in data.get("photos") or []:
        src = (f.get("src") or {}) if isinstance(f, dict) else {}
        u = src.get("portrait") or src.get("large2x") or src.get("large") or ""
        if broll.host_sah(u):
            sah.append((f, u))
    for f, u in (sah[pilih % len(sah):] + sah[:pilih % len(sah)]) if sah else []:
        tujuan = os.path.join(folder, f"stok_{f.get('id')}.jpg")
        broll._unduh_ke(u, tujuan, 12 * 1024 * 1024)
        return tujuan, {"id": f.get("id"), "fotografer": f.get("photographer") or "",
                        "halaman": f.get("url") or "", "query": query}
    raise CarouselError("stok_kosong", f"tidak ada foto stok untuk {query!r}")


# ------------------------------------------------------------------ tata letak & QA

def kotak_teks(platform, W, H, wajah=None):
    """Kotak aman teks (piksel). TikTok: hindari tombol kanan & caption bawah (qa_video.ZONA_UI).
    Ada wajah: teks pindah ke separuh yang tidak berisi wajah."""
    if platform == "tiktok":
        x0, x1, y0, y1 = 0.07, 0.84, 0.10, 0.80
    else:
        x0, x1, y0, y1 = 0.07, 0.93, 0.11, 0.92
    zona = "tengah"
    if wajah:
        tengah_y = (wajah[1] + wajah[3] / 2) / H
        if tengah_y < 0.5:
            y0, zona = max(y0, (wajah[1] + wajah[3]) / H + 0.03), "bawah"
        else:
            y1, zona = min(y1, wajah[1] / H - 0.03), "atas"
    return {"x": round(W * x0), "y": round(H * y0), "w": round(W * (x1 - x0)), "h": round(H * (y1 - y0)),
            "zona": zona}


def _luminans(rgb):
    """Luminans relatif WCAG per piksel (rgb float 0-255, bentuk (..., 3))."""
    import numpy as np
    c = rgb / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2]


def periksa_slide(penuh, teks, kotak, platform, wajah=None):
    """Ukur satu slide dari render penuh (RGB) dan lapisan teksnya (RGBA, latar transparan).
    -> {"masalah": [...], "kontras": float}"""
    import numpy as np
    H, W = teks.shape[:2]
    alfa = teks[..., 3] > 40
    masalah = []
    total = max(1, int(alfa.sum()))
    if total < 200:
        masalah.append("lapisan teks nyaris kosong")
    izin = np.zeros_like(alfa)
    pad = int(W * 0.015)
    izin[max(0, kotak["y"] - pad):kotak["y"] + kotak["h"] + pad, max(0, kotak["x"] - pad):kotak["x"] + kotak["w"] + pad] = True
    izin[int(H * 0.03):int(H * 0.09), kotak["x"]:kotak["x"] + int(W * 0.6)] = True      # nomor slide
    luar = int((alfa & ~izin).sum())
    if luar > total * 0.002:
        masalah.append(f"teks keluar dari kotak aman ({luar} piksel)")
    if platform == "tiktok":
        from qa_video import ZONA_UI
        for nama, (x0, x1, y0, y1) in ZONA_UI.items():
            z = alfa[int(H * y0):int(H * y1), int(W * x0):int(W * x1)]
            if z.size and z.mean() > 0.002:
                masalah.append(f"teks masuk zona UI TikTok ({nama})")
    if wajah:
        x, y, w, h = wajah
        if alfa[y:y + h, x:x + w].mean() > 0.01:
            masalah.append("teks menimpa wajah")
    # Kontras: median luminans piksel inti teks vs latar di sekitarnya (kotak tanpa teks).
    inti = teks[..., 3] > 220
    sekitar = np.zeros_like(alfa)
    sekitar[kotak["y"]:kotak["y"] + kotak["h"], kotak["x"]:kotak["x"] + kotak["w"]] = True
    sekitar &= teks[..., 3] == 0
    kontras = None
    if inti.sum() > 50 and sekitar.sum() > 50:
        lt = float(np.median(_luminans(penuh[..., :3][inti].astype(float))))
        lb = float(np.median(_luminans(penuh[..., :3][sekitar].astype(float))))
        a, b = max(lt, lb), min(lt, lb)
        kontras = round((a + 0.05) / (b + 0.05), 2)
        if kontras < KONTRAS_MIN:
            masalah.append(f"kontras teks {kontras} < {KONTRAS_MIN}")
    return {"masalah": masalah, "kontras": kontras}


# ------------------------------------------------------------------ utama

def bersihkan_watermark(teks):
    """Watermark sah, atau None kalau kosong. Tidak sah -> CarouselError (user salah ketik harus
    tahu, bukan diam-diam dibuang). Dibatasi daftar karakter: nilainya ikut ke props render."""
    t = re.sub(r"\s+", " ", str(teks or "")).strip()
    if not t:
        return None
    if not _WATERMARK.fullmatch(t):
        raise CarouselError("watermark_invalid",
                            f"Watermark maks {WATERMARK_MAKS} karakter, hanya huruf, angka, spasi, "
                            f"dan @ . - _ & / | + ' — bukan {teks!r}.")
    return t


def terapkan_gaya(preset, *, platform=None, jumlah=None, stok=None, latar=None, ikon=None, watermark=None):
    """(pengaturan, dari_gaya). Yang disebut user di run ini menang; yang tidak disebut (None) diisi
    bagian `carousel` gaya; sisanya bawaan. `latar="polos"` = tanpa latar, `watermark="off"` = tanpa
    watermark; keduanya cara user menolak pengaturan gayanya sendiri untuk satu run."""
    atur = preset.get("carousel") or {}
    dari_gaya = []

    def isi(nilai, kunci, bawaan):
        if nilai is None and kunci in atur:
            dari_gaya.append(kunci)
            return atur[kunci]
        return bawaan if nilai is None else nilai

    hasil = {
        "platform": isi(platform, "platform", "ig"),
        "jumlah": isi(jumlah, "jumlah", JUMLAH_BAWAAN),
        "stok": isi(None if stok is None else ("on" if stok else "off"), "foto_stok", "off") == "on",
        "latar": isi(latar, "latar", None),
        "ikon": isi(ikon, "ikon", "on") == "on",
        "watermark": isi(watermark, "watermark", None),
    }
    if hasil["latar"] == "polos":
        hasil["latar"] = None
    hasil["watermark"] = None if hasil["watermark"] == "off" else bersihkan_watermark(hasil["watermark"])
    return hasil, dari_gaya


def buat(*, chat_id, teks="", dari_run=None, foto=(), stiker=(), stok=None, latar=None, nama_gaya=None, platform=None,
         jumlah=None, ikon=None, watermark=None, chat=None):
    """Bangun carousel; kembalikan dict hasil (ok True) atau melempar CarouselError."""
    import overlay_remotion as orr
    from PIL import Image
    import numpy as np
    from run_lock import FileLockBusyError, acquire_render_lock

    if not str(chat_id or "").strip():
        raise CarouselError("chat_tidak_diketahui", "Label chat tidak diketahui.")
    preset, sumber_gaya = gaya.pilih(nama_gaya, chat_id)
    atur, dari_gaya = terapkan_gaya(preset, platform=platform, jumlah=jumlah, stok=stok, latar=latar,
                                    ikon=ikon, watermark=watermark)
    platform, jumlah, stok, latar = atur["platform"], atur["jumlah"], atur["stok"], atur["latar"]
    if platform not in ("ig", "tiktok", "keduanya"):
        raise CarouselError("argumen_invalid", "platform harus ig, tiktok, atau keduanya")
    if not SLIDE_MIN <= int(jumlah) <= SLIDE_MAX:
        raise CarouselError("argumen_invalid", f"jumlah slide harus {SLIDE_MIN}-{SLIDE_MAX}")
    videos = []
    sumber = (teks or "").strip()
    if dari_run:
        try:
            teks_run, videos = sumber_dari_run(dari_run, chat_id)
        except Exception as e:  # noqa: BLE001  (RevisiError: milik chat lain / kedaluwarsa)
            raise CarouselError(getattr(e, "kode", "run_tidak_ada"), str(e))
        sumber = (sumber + "\n" + teks_run).strip()
    if _kata(sumber) < 8:
        raise CarouselError("sumber_kurang", "Bahannya terlalu sedikit untuk carousel. Tulis topik atau poinnya, "
                                             "atau sebut video yang mau didaur ulang.")
    foto_sah = []
    stiker_sah = []
    latar_berkas = None
    if foto or stiker or (latar and latar != "stok"):
        from hermes_render import MediaPathError, _validate_media_paths
        try:
            foto_sah = _validate_media_paths(list(foto)) if foto else []
            stiker_sah = _validate_media_paths(list(stiker)) if stiker else []
            if latar and latar != "stok":
                (latar_berkas,) = _validate_media_paths([latar])
        except MediaPathError as e:
            raise CarouselError("foto_invalid", str(e))

    slides, caption, hashtags, catatan = minta_slide(sumber, int(jumlah), platform, chat=chat)

    cid = secrets.token_hex(4)
    keluar = os.path.join(CAROUSEL_DIR, cid)
    os.makedirs(keluar, exist_ok=True)
    kerja = tempfile.mkdtemp(prefix="_carousel_", dir=keluar)
    hasil = {"ok": True, "carousel_id": cid, "slide": {}, "jumlah_slide": len(slides),
             "jenis": [s["jenis"] for s in slides], "caption": caption, "hashtags": hashtags,
             "gaya_tampilan": {"nama": preset["nama"], "label": preset["label"], "sumber": sumber_gaya,
                               "pengaturan_carousel": dari_gaya},
             "watermark": atur["watermark"],
             "ikon": sorted({s["ikon"] for s in slides if s.get("ikon")}) if atur["ikon"] else [],
             "kredit_foto": [], "catatan": catatan, "qa": {}}
    if not atur["ikon"]:
        slides = [{k: v for k, v in s.items() if k != "ikon"} for s in slides]
    try:
        with acquire_render_lock(f"carousel_{cid}"):
            for plat in (("ig", "tiktok") if platform == "keduanya" else (platform,)):
                W, H = UKURAN[plat]
                gambar = [None] * len(slides)
                wajah = [None] * len(slides)
                sumber_gambar = list(foto_sah)
                if not sumber_gambar and videos:
                    f = frame_terbaik(videos, W, H, kerja)
                    if f:
                        sumber_gambar = [f]
                    else:
                        catatan.append("frame berwajah dari video tidak ditemukan; slide tanpa foto")
                # Foto user/video: hook dulu, lalu slide isi/daftar berikutnya (statistik & kutipan polos).
                urut = [i for i, s in enumerate(slides) if s["jenis"] in ("hook", "isi", "daftar", "cta")]
                for i, p in zip(urut, sumber_gambar):
                    gambar[i], wajah[i] = siapkan_gambar(p, W, H)
                if stok:
                    for i in urut:
                        if gambar[i] is None and slides[i].get("cari"):
                            try:
                                p, kredit = foto_stok(slides[i]["cari"], W, H, kerja)
                                gambar[i], wajah[i] = siapkan_gambar(p, W, H)
                                hasil["kredit_foto"].append({**kredit, "slide": i + 1})
                            except Exception as e:  # noqa: BLE001  (dilaporkan, slide tetap jadi)
                                catatan.append(f"foto stok slide {i + 1} gagal: {str(e)[:120]}")
                # Satu latar untuk SEMUA slide yang belum punya foto sendiri: gambar user atau satu
                # foto stok estetik sesuai topik. Diburamkan + ditimpa warna tema di komponen;
                # wajah tidak dihindari (latar, bukan subjek) dan kontrasnya tetap diukur QA.
                latar_url = None
                if latar_berkas:
                    latar_url, _ = siapkan_gambar(latar_berkas, W, H)
                elif latar == "stok":
                    kata = slides[0].get("cari_latar") or slides[0].get("cari")
                    try:
                        if not kata:
                            raise CarouselError("stok_kosong", "model tidak memberi kata kunci latar")
                        p, kredit = foto_stok(kata, W, H, kerja, pilih=int(cid, 16))
                        latar_url, _ = siapkan_gambar(p, W, H)
                        if not any(k.get("latar") and k["id"] == kredit["id"] for k in hasil["kredit_foto"]):
                            hasil["kredit_foto"].append({**kredit, "latar": True})
                    except Exception as e:  # noqa: BLE001  (dilaporkan, carousel tetap jadi polos)
                        catatan.append(f"foto latar gagal: {str(e)[:120]}")
                pakai_latar = [latar_url is not None and g is None for g in gambar]
                gambar = [latar_url if pl else g for pl, g in zip(pakai_latar, gambar)]
                kotak = [kotak_teks(plat, W, H, w) for w in wajah]
                stiker_urls = [siapkan_stiker(p) for p in stiker_sah]
                props_slide = [{**s, "gambar": g, "latar": pl, "stiker": stiker_urls[i % len(stiker_urls)] if stiker_urls else None} for i, (s, g, pl) in enumerate(zip(slides, gambar, pakai_latar))]
                png, png_teks = orr.render_carousel(props_slide, kotak, preset["tema"], plat, W, H,
                                                    os.path.join(kerja, plat), watermark=atur["watermark"])
                jalur, qa = [], []
                for i, (a, b) in enumerate(zip(png, png_teks)):
                    penuh = np.asarray(Image.open(a).convert("RGB"))
                    lapis = np.asarray(Image.open(b).convert("RGBA"))
                    ukur = periksa_slide(penuh, lapis, kotak[i], plat, wajah[i])
                    qa.append({"slide": i + 1, **ukur})
                    tujuan = os.path.join(keluar, f"{plat}_{i + 1:02d}.jpg")
                    Image.fromarray(penuh).save(tujuan, "JPEG", quality=92)
                    jalur.append(tujuan)
                hasil["slide"][plat] = jalur
                hasil["qa"][plat] = {"lolos": not any(q["masalah"] for q in qa), "per_slide": qa}
    except FileLockBusyError as e:
        shutil.rmtree(keluar, ignore_errors=True)
        raise CarouselError("render_sibuk", f"masih ada render lain berjalan ({e.elapsed_seconds:.0f} detik lalu).")
    except orr.OverlayError as e:
        shutil.rmtree(keluar, ignore_errors=True)
        raise CarouselError("render_gagal", str(e))
    finally:
        shutil.rmtree(kerja, ignore_errors=True)
    write_json(os.path.join(keluar, "meta.json"), {
        "chat_id": str(chat_id), "carousel_id": cid, "sumber": sumber[:6000], "dari_run": dari_run,
        "slides": slides, "caption": caption, "hashtags": hashtags, "gaya": preset["nama"],
        "slide_berkas": hasil["slide"]})
    return hasil


def main(argv=None):
    ap = argparse.ArgumentParser(description="Carousel IG/TikTok bergaya")
    ap.add_argument("--chat-id", default="")
    ap.add_argument("--teks", default="", help="Topik, poin, atau naskah dari user (apa adanya).")
    ap.add_argument("--dari-run", default=None, help="run_id video yang didaur ulang jadi carousel.")
    ap.add_argument("--foto", action="append", default=[], help="Foto user (path di cache Hermes).")
    ap.add_argument("--stiker", action="append", default=[], help="Stiker custom user (path di cache Hermes).")
    # Tanpa flag = None: diisi bagian `carousel` gaya chat ini, lalu bawaan (ig, 6 slide, tanpa stok).
    ap.add_argument("--stok", action="store_true", default=None, help="Isi slide tanpa foto dengan foto Pexels.")
    ap.add_argument("--tanpa-stok", dest="stok", action="store_false", help="Tanpa foto stok walau gayanya memintanya.")
    ap.add_argument("--latar", default=None,
                    help="Satu latar untuk semua slide: path gambar user, 'stok', atau 'polos' (tanpa latar).")
    ap.add_argument("--gaya", default=None)
    ap.add_argument("--platform", default=None, choices=["ig", "tiktok", "keduanya"])
    ap.add_argument("--jumlah", type=int, default=None)
    ap.add_argument("--watermark", default=None, help="Teks watermark pendek, misal '@username'.")
    a = ap.parse_args(argv)
    try:
        out = buat(chat_id=a.chat_id, teks=a.teks, dari_run=a.dari_run, foto=a.foto, stiker=a.stiker, stok=a.stok, latar=a.latar,
                   nama_gaya=a.gaya, platform=a.platform, jumlah=a.jumlah, watermark=a.watermark)
    except CarouselError as e:
        out = {"ok": False, "kode": e.kode, "alasan": str(e)}
    except gaya.GayaError as e:
        out = {"ok": False, "kode": "gaya_invalid", "alasan": str(e)}
    except Exception as e:  # noqa: BLE001  (LLM habis kuota, dll.: dilaporkan apa adanya)
        out = {"ok": False, "kode": "gagal", "alasan": f"{type(e).__name__}: {str(e)[:300]}"}
    if out["ok"]:
        out["kirim"] = jalur_kirim(out.get("slide"))      # urut per platform; bentuk yang bisa dikirim gateway
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
