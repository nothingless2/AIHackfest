"""Storyboard pratinjau di pesan draf: tampilan disetujui SEBELUM render penuh (±2 menit).
Permintaan user 25 Sep: kurangi revisi -- user melihat teks, grafik, dan B-roll pada posisinya,
bukan hanya membaca naskah.

Per varian satu JPG berisi 8 panel (4x2). Tiap panel = frame bahan pada perkiraan detiknya +
contoh teks di zona subtitle + elemen motion yang aktif (still Remotion SUNGGUHAN, komponen yang
sama dengan render) + thumbnail B-roll bila detik itu jatuh di cutaway. Waktu adalah PERKIRAAN
(narasi AI: kata / 2,7 dtk; suara asli: waktu transkrip, tanpa pemotongan jeda) dan diberi label.

Gagal = draf tetap terkirim tanpa storyboard; alasannya dikembalikan (aturan #7).
"""

import os
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

import motion_plan as mp
from alokasi import susun_potongan
from duration import WORDS_PER_SECOND

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = os.path.join(PROJECT_ROOT, "assets", "fonts", "Montserrat-ExtraBold.ttf")
PANEL_W, PANEL_H = 270, 480
KOLOM, BARIS = 4, 2
JUMLAH_PANEL = KOLOM * BARIS
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


class StoryboardError(RuntimeError):
    pass


def _durasi(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                        path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _ucapan(brief):
    return " ".join(str(s.get("text") or "").strip() for segs in (brief.get("transcript_segments") or {}).values()
                    for s in (segs or []) if isinstance(s, dict)).strip()


# ------------------------------------------------------------------ linimasa perkiraan

def linimasa(brief, varian):
    """(total, potongan[{path, mulai, selesai, sumber_a, speed}], kata_waktu[{word,start,end}])."""
    aset = brief.get("media_assets") or []
    if brief.get("audio_mode") == "ai":
        kata = (varian.get("full_voice_over") or "").split()
        total = max(3.0, len(kata) / WORDS_PER_SECOND)
        kata_waktu = [{"word": w, "start": i / WORDS_PER_SECOND, "end": (i + 1) / WORDS_PER_SECOND}
                      for i, w in enumerate(kata)]
        bahan = []
        for p in aset:
            if os.path.splitext(p)[1].lower() in IMAGE_EXT:
                bahan.append({"path": p, "foto": True})
            else:
                bahan.append({"path": p, "rentang": [(0.0, _durasi(p))]})
        pot, _ = susun_potongan(bahan, total)
        hasil, t = [], 0.0
        for q in pot:
            a = q["potong"][0] if q["potong"] else 0.0
            hasil.append({"path": q["path"], "mulai": t, "selesai": t + q["durasi"], "sumber_a": a,
                          "speed": q["speed"]})
            t += q["durasi"]
        return total, hasil, kata_waktu
    # Suara asli: potongan terpilih (seleksi) atau seluruh klip berurutan; kata dari transkrip.
    plan = brief.get("edit_plan") or {}
    peta = {os.path.basename(p): p for p in aset}
    rentang = [(peta[x["file"]], float(x["range"][0]), float(x["range"][1]))
               for x in (plan.get("picks") or []) if x.get("file") in peta] if plan.get("status") == "applied" else []
    if not rentang:
        rentang = [(p, 0.0, _durasi(p) if os.path.splitext(p)[1].lower() not in IMAGE_EXT else 3.0) for p in aset]
    hasil, kata_waktu, t = [], [], 0.0
    kata_per = brief.get("transcript_words") or {}
    for path, a, b in rentang:
        hasil.append({"path": path, "mulai": t, "selesai": t + (b - a), "sumber_a": a, "speed": 1.0})
        for w in kata_per.get(os.path.basename(path)) or []:
            s = float(w.get("start", 0))
            if a <= s < b:
                kata_waktu.append({"word": w.get("word", ""), "start": t + s - a, "end": t + float(w.get("end", s)) - a})
        t += b - a
    return t, hasil, kata_waktu


def pilih_waktu(total, motion_items, cutaway):
    """8 detik panel: kartu pembuka, tiap elemen motion & cutaway (di tengahnya), kartu ajakan,
    lalu diisi merata. Dikembalikan terurut dan tidak berdempetan (>= 0,4 dtk)."""
    wajib = [(i["mulai"] + i["selesai"]) / 2 for i in motion_items]
    wajib += [(c["mulai"] + c["selesai"]) / 2 for c in cutaway]
    t = sorted(x for x in wajib if 0 <= x < total)[:JUMLAH_PANEL]
    k = 1
    while len(t) < JUMLAH_PANEL and k < 40:
        kandidat = total * k / (JUMLAH_PANEL + 1)
        if all(abs(kandidat - x) >= 0.4 for x in t):
            t.append(kandidat)
        k += 1
    return sorted(t)[:JUMLAH_PANEL]


# ------------------------------------------------------------------ gambar

def _frame(path, detik, keluar):
    if os.path.splitext(path)[1].lower() in IMAGE_EXT:
        args = ["-i", path]
    else:
        args = ["-ss", f"{max(0.0, detik):.3f}", "-i", path]
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args, "-frames:v", "1", "-vf",
                    f"scale={PANEL_W}:{PANEL_H}:force_original_aspect_ratio=increase,crop={PANEL_W}:{PANEL_H}",
                    keluar], capture_output=True, timeout=60)
    return keluar if os.path.exists(keluar) else None


def _isi(img, path):
    try:
        with Image.open(path) as g:
            g = g.convert("RGB")
            skala = max(PANEL_W / g.width, PANEL_H / g.height)
            g = g.resize((max(PANEL_W, int(g.width * skala)), max(PANEL_H, int(g.height * skala))))
            x, y = (g.width - PANEL_W) // 2, (g.height - PANEL_H) // 2
            img.paste(g.crop((x, y, x + PANEL_W, y + PANEL_H)))
        return True
    except Exception:
        return False


def _teks_tengah(draw, teks, y, ukuran, isi="white"):
    f = ImageFont.truetype(FONT, ukuran)
    w = draw.textlength(teks, font=f)
    while w > PANEL_W * 0.9 and ukuran > 10:
        ukuran -= 2
        f = ImageFont.truetype(FONT, ukuran)
        w = draw.textlength(teks, font=f)
    draw.text(((PANEL_W - w) / 2, y), teks, font=f, fill=isi, stroke_width=2, stroke_fill="black")


def _stills_motion(items, total, waktu, folder, tata="atas"):
    """{detik: png} still Remotion untuk detik panel yang jatuh di elemen motion."""
    import overlay_remotion as ovr
    aktif = [(t, next((i for i in items if i["mulai"] <= t < i["selesai"]), None)) for t in waktu]
    aktif = [(t, i) for t, i in aktif if i]
    if not aktif:
        return {}
    fps = 24
    props_items, _ = ovr.rencana_motion(items, fps)
    props = {"lebar": 540, "tinggi": 960, "fps": fps, "durasi": total, "aksen": ovr.MOTION_AKSEN,
             "items": props_items, "tata": tata}
    # Still pada detik panel, tapi minimal setelah animasi masuk selesai (elemen utuh).
    kerja = []
    for t, i in aktif:
        f = max(int(round(t * fps)), int(round(i["mulai"] * fps)) + ovr.MOTION_MASUK)
        kerja.append({"jenis": "diam", "frame": f, "tahan": 1, "mulai": f, "_t": t})
    pek = ovr._jalankan_node(props, [{k: v for k, v in x.items() if k != "_t"} for x in kerja],
                             folder, komposisi="MotionOverlay")
    return {x["_t"]: p["out"] for x, p in zip(kerja, pek)}


def buat(brief, varian, keluar, *, pratinjau_broll=None):
    """Tulis storyboard JPG ke `keluar`. Return {"path", "catatan": [...]} atau melempar
    StoryboardError. pratinjau_broll(query, tujuan) -> path|None (bisa dipalsukan di tes)."""
    catatan = []
    total, potongan, kata_waktu = linimasa(brief, varian)
    if not potongan:
        raise StoryboardError("tidak ada bahan untuk dipratinjau")
    ai = brief.get("audio_mode") == "ai"
    naskah = varian.get("full_voice_over") if ai else " ".join(w["word"] for w in kata_waktu)
    bersih, c1 = mp.bersihkan(varian.get("motion_plan"), naskah=naskah,
                              sumber_fakta=" ".join([brief.get("konteks_user") or "", _ucapan(brief)]),
                              pakai_jangkar=bool(kata_waktu))
    items, c2 = mp.jadwal(bersih, kata_waktu, total)
    cutaway = []
    if not ai and varian.get("broll"):
        cutaway, _ = mp.jadwal_broll(varian["broll"], kata_waktu, total)
    waktu = pilih_waktu(total, items, cutaway)
    folder = tempfile.mkdtemp(prefix="_storyboard_")
    try:
        try:
            stills = _stills_motion(items, total, waktu, folder, tata="atas" if ai else "bawah")
        except Exception as e:
            stills = {}
            catatan.append(f"grafik tidak dipratinjau ({type(e).__name__})")
        thumb = {}
        for k, c in enumerate(cutaway):
            if pratinjau_broll:
                p = pratinjau_broll(c["query"], os.path.join(folder, f"broll_{k}.jpg"))
                if p:
                    thumb[k] = p
        lembar = Image.new("RGB", (PANEL_W * KOLOM, PANEL_H * BARIS), "black")
        for n, t in enumerate(waktu):
            img = Image.new("RGB", (PANEL_W, PANEL_H), (30, 30, 30))
            cut = next((k for k, c in enumerate(cutaway) if c["mulai"] <= t < c["selesai"]), None)
            if cut is not None and cut in thumb:
                _isi(img, thumb[cut])
                label_broll = f"B-roll: {cutaway[cut]['query']}"
            else:
                label_broll = None
                q = next((x for x in potongan if x["mulai"] <= t < x["selesai"]), potongan[-1])
                fr = _frame(q["path"], q["sumber_a"] + (t - q["mulai"]) * q["speed"],
                            os.path.join(folder, f"f{n}.png"))
                if fr:
                    _isi(img, fr)
            if t in stills:
                try:
                    with Image.open(stills[t]) as ov:
                        img.paste(ov.convert("RGBA").resize((PANEL_W, PANEL_H)), (0, 0),
                                  ov.convert("RGBA").resize((PANEL_W, PANEL_H)))
                except Exception:
                    pass
            draw = ImageDraw.Draw(img)
            dekat = [w["word"] for w in kata_waktu if t - 0.6 <= w["start"] <= t + 0.6][:4]
            if dekat:
                _teks_tengah(draw, " ".join(dekat).upper() if ai else " ".join(dekat),
                             int(PANEL_H * (0.64 if ai else 0.78)), 22 if ai else 16,
                             isi="white" if ai else (255, 212, 0))
            draw.rectangle((0, 0, PANEL_W, 22), fill=(0, 0, 0))
            draw.text((6, 3), f"~{int(t // 60)}:{int(t % 60):02d}" + (f"  {label_broll}" if label_broll else ""),
                      font=ImageFont.truetype(FONT, 13), fill="white")
            lembar.paste(img, ((n % KOLOM) * PANEL_W, (n // KOLOM) * PANEL_H))
        lembar.save(keluar, "JPEG", quality=82)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    catatan += [c for c in c1 + c2 if c]
    return {"path": keluar, "panel": len(waktu), "catatan": catatan[:5]}


def kalimat_pertama(teks, maks=140):
    t = " ".join(str(teks or "").split())
    posisi = [i for i in (t.find(x) for x in ".!?") if 0 < i < maks]
    if posisi:
        return t[:min(posisi) + 1]
    return t[:maks].rsplit(" ", 1)[0] if len(t) > maks else t


def impor_auto_render():
    """auto_render ada di skills/video_generator, TIDAK di jalur impor hermes_render -- tanpa ini
    contoh suara di draf nyata 25 Sep gagal "No module named 'auto_render'"."""
    import sys
    jalur = os.path.join(PROJECT_ROOT, "skills", "video_generator")
    if jalur not in sys.path:
        sys.path.insert(0, jalur)
    import auto_render
    return auto_render


def contoh_suara(teks, keluar):
    """Cuplikan suara narasi (kalimat pertama) dengan mesin & suara terpilih. Return
    (path, catatan). Mesin utama gagal -> cadangan edge-tts dicatat oleh auto_render."""
    import asyncio

    ar = impor_auto_render()
    asyncio.run(ar.generate_voice(kalimat_pertama(teks), keluar))
    return keluar, dict(ar.TTS_CATATAN) or None


def untuk_draf(d, folder):
    """Storyboard tiap varian (+ contoh suara varian A di mode AI) untuk pesan draf.
    Return {"storyboard": [path], "contoh_suara": path|None, "gagal": [..]}. Tidak melempar."""
    import broll as _broll
    from canvas import resolve_canvas
    hasil = {"storyboard": [], "contoh_suara": None, "gagal": []}
    brief = d["brief"]
    try:
        lebar, tinggi, _ = resolve_canvas()
        orientasi = _broll.orientasi_untuk(lebar, tinggi)
    except Exception:
        orientasi = "portrait"
    def _thumb(query, tujuan):
        return _broll.pratinjau(query, orientasi, tujuan)[0]
    ambil_thumb = _thumb if (_broll.aktif() and _broll.tersedia()) else None
    for i, v in enumerate(d["varian"]):
        keluar = os.path.join(folder, f"{d['draft_id']}_{'ABCDEFGH'[i]}.jpg")
        try:
            buat(brief, v, keluar, pratinjau_broll=ambil_thumb)
            hasil["storyboard"].append(keluar)
        except Exception as e:
            hasil["gagal"].append(f"storyboard {'ABCDEFGH'[i]}: {type(e).__name__}: {str(e)[:80]}")
    if brief.get("audio_mode") == "ai" and d["varian"] and \
            (os.getenv("STORYBOARD_SUARA") or "1").strip().lower() not in ("0", "off", "mati"):
        keluar = os.path.join(folder, f"{d['draft_id']}_suara.mp3")
        try:
            hasil["contoh_suara"], catatan = contoh_suara(d["varian"][0].get("full_voice_over"), keluar)
            if catatan and catatan.get("cadangan"):
                hasil["gagal"].append(f"contoh suara memakai cadangan: {catatan.get('alasan')}")
        except Exception as e:
            hasil["gagal"].append(f"contoh suara: {type(e).__name__}: {str(e)[:80]}")
    return hasil
