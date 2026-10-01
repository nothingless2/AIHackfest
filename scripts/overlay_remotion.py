"""Teks di layar yang BERANIMASI, dirender Remotion (Chromium) sebagai lapisan transparan
lalu ditempel ke video oleh ffmpeg.

Kenapa Remotion hanya untuk teks TULISAN (bukan subtitle ucapan/karaoke, bukan seluruh
video): drawtext ffmpeg tidak bisa emoji berwarna, font web, atau animasi per kata -- itulah
keluhan user (24 Sep). Seluruh video lewat Chromium akan terlalu lambat di VPS 4 core tanpa
GPU: terukur 0,22 dtk/frame pada 1080x1920.

OPTIMASI (terukur): teks DIAM setelah animasi masuk selesai. Hanya frame yang BERGERAK yang
dirender Chromium (masuk + keluar); bagian tengah memakai SATU gambar diam yang di-loop
ffmpeg. Judul 9 dtk: 216 frame (46 dtk) -> ~40 frame. `rencana()` yang memutuskan -- di sini,
bukan di Node, supaya bisa dites. Komponen React menerima `masukFrames` dari sini sehingga
frame terakhir animasi dan gambar diam identik (tidak ada lompatan).

Kegagalan (node tidak ada, Chromium gagal, lewat batas waktu) melempar OverlayError; pemanggil
jatuh ke teks statis drawtext dan MELAPORKANNYA (aturan #7) -- video tetap jadi.
"""

import json
import os
import shutil
import signal
import subprocess
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTION_DIR = os.path.join(PROJECT_ROOT, "remotion")
ANIMASI = ("pop", "loncat", "geser", "fade")
JEDA_KATA = 3            # frame antar kata saat masuk (harus sama dengan TextOverlay.jsx)
SETTLE = 20              # frame sampai pegas kata terakhir diam
KELUAR = 9               # frame fade keluar (harus sama dengan TextOverlay.jsx)
BATAS_DETIK = int(os.getenv("TEXT_ANIMATION_TIMEOUT", "120"))


class OverlayError(RuntimeError):
    """Lapisan animasi tidak bisa dibuat; pemanggil jatuh ke teks statis."""


def animasi_diminta(nilai=None):
    """Nama animasi, atau None bila dimatikan. Default 'pop' (keputusan user 24 Sep)."""
    n = (nilai if nilai is not None else os.getenv("TEXT_ANIMATION", "pop")).strip().lower()
    if n in ("", "none", "off", "0", "false", "mati", "statis"):
        return None
    if n not in ANIMASI:
        from style import StyleError
        raise StyleError(f"Animasi teks {n!r} tidak dikenal. Pilihan: {', '.join(ANIMASI)}, none.")
    return n


def jumlah_kata(teks):
    return len((teks or "").split())


def masuk_frames(teks, animasi):
    if animasi in ("fade", "geser"):
        return SETTLE
    return JEDA_KATA * max(0, jumlah_kata(teks) - 1) + SETTLE


def _kerja_item(dari, dur, e, k):
    """Pekerjaan render satu item: masuk (klip) + tahan (SATU gambar diam) + keluar (klip).
    Item terlalu pendek untuk dipecah dirender utuh."""
    if dur <= e + k + 2:
        return [{"jenis": "klip", "dari": dari, "sampai": dari + dur - 1, "mulai": dari}]
    return [
        {"jenis": "klip", "dari": dari, "sampai": dari + e - 1, "mulai": dari},
        {"jenis": "diam", "frame": dari + e, "tahan": dur - e - k, "mulai": dari + e},
        {"jenis": "klip", "dari": dari + dur - k, "sampai": dari + dur - 1, "mulai": dari + dur - k},
    ]


def _frame(it, fps):
    dari = int(round(float(it["mulai"]) * fps))
    dur = max(2, int(round((float(it["selesai"]) - float(it["mulai"])) * fps)))
    return dari, dur


def rencana(items, fps, animasi):
    """Item -> (items_untuk_props, pekerjaan_render).

    Pekerjaan: {"jenis": "klip", "dari": f0, "sampai": f1} (inklusif) atau
    {"jenis": "diam", "frame": f, "tahan": n_frame}. Tiap pekerjaan juga membawa "mulai"
    (frame global tempat ia ditempel)."""
    props_items, kerja = [], []
    for it in items:
        dari, dur = _frame(it, fps)
        e = masuk_frames(it["text"], animasi)
        props_items.append({"text": it["text"], "mulai": dari / fps, "selesai": (dari + dur) / fps,
                            "masukFrames": e, "keluarFrames": KELUAR})
        kerja += _kerja_item(dari, dur, e, KELUAR)
    return props_items, kerja


# Motion graphic (MotionOverlay.jsx): frame masuk/keluar HARUS sama dengan di komponen, yang
# menerimanya lewat props (masukFrames/keluarFrames) -- satu sumber angka, di sini.
MOTION_MASUK = 12        # 0,5 dtk pada 24 fps; 16 terukur +0,3 dtk Chromium per frame
MOTION_KELUAR = 8
MOTION_AKSEN = "#8B5CF6"


def rencana_motion(items, fps):
    """Seperti rencana(), untuk elemen motion graphic -- tapi animasi KELUAR tidak dirender
    Chromium: ia hanya pudar (opasitas), jadi dibuat ffmpeg dari gambar diam ("pudar").
    Terukur: 150 -> 102 frame Chromium, 18 -> 12 masukan komposit untuk 6 elemen.

    Item WAJIB tidak tumpang tindih (dijamin motion_plan.jadwal): tiap potongan dirender dari
    seluruh komposisi pada frame-nya, jadi item yang bertumpuk akan tertempel dua kali."""
    props_items, kerja = [], []
    batas = -1
    for it in sorted(items, key=lambda x: float(x["mulai"])):
        dari, dur = _frame(it, fps)
        if dari <= batas:
            raise OverlayError(f"elemen motion bertumpuk pada frame {dari}")
        batas = dari + dur - 1
        props_items.append({**it, "mulai": dari / fps, "selesai": (dari + dur) / fps,
                            "masukFrames": MOTION_MASUK, "keluarFrames": MOTION_KELUAR})
        if dur <= MOTION_MASUK + 2:
            kerja.append({"jenis": "klip", "dari": dari, "sampai": dari + dur - 1, "mulai": dari})
            continue
        kerja.append({"jenis": "klip", "dari": dari, "sampai": dari + MOTION_MASUK - 1, "mulai": dari})
        kerja.append({"jenis": "diam", "frame": dari + MOTION_MASUK, "tahan": dur - MOTION_MASUK,
                      "pudar": min(MOTION_KELUAR, dur - MOTION_MASUK), "mulai": dari + MOTION_MASUK})
    return props_items, kerja


def total_frame_chromium(kerja):
    return sum((k["sampai"] - k["dari"] + 1) if k["jenis"] == "klip" else 1 for k in kerja)


def _tema_aktif():
    """Tema preset gaya dari env (scripts/gaya.py), divalidasi ulang; tidak ada -> None."""
    from gaya import GayaError, tema_aktif
    try:
        return tema_aktif()
    except GayaError as e:
        raise OverlayError(f"tema gaya tidak sah: {e}")


def _jalankan_node(props, kerja, folder, komposisi="TextOverlay", batas=None):
    # Satu pintu untuk SEMUA komposisi: tema preset disuntik di sini, jadi caption, kartu motion,
    # panggung, dan cover selalu memakai palet yang sama. Props yang sudah membawa `tema`
    # (pratinjau gaya) tidak ditimpa.
    if "tema" not in props:
        tema = _tema_aktif()
        if tema:
            props = {**props, "tema": tema}
    if not shutil.which("node"):
        raise OverlayError("node tidak terpasang")
    if not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules")):
        raise OverlayError("paket Remotion belum terpasang (npm install di remotion/)")
    pekerjaan = []
    for i, k in enumerate(kerja):
        out = k.get("out_dir") or os.path.join(folder, f"_overlay_{i}." + ("mov" if k["jenis"] == "klip" else "png"))
        pekerjaan.append({**k, "out": out})
    masukan = os.path.join(folder, "_overlay_props.json")
    with open(masukan, "w", encoding="utf-8") as f:
        json.dump({"props": props, "pekerjaan": pekerjaan, "komposisi": komposisi}, f,
                  ensure_ascii=False)
    proc = subprocess.Popen(["node", os.path.join(REMOTION_DIR, "render.mjs"), masukan],
                            cwd=REMOTION_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=batas or BATAS_DETIK)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)       # Chromium ikut mati, bukan jadi yatim
        proc.communicate()
        raise OverlayError(f"render animasi melewati {batas or BATAS_DETIK} detik")
    if proc.returncode != 0:
        raise OverlayError(f"render animasi gagal: {(err or out).strip()[-300:]}")
    for p in pekerjaan:
        if not os.path.exists(p["out"]):
            raise OverlayError(f"keluaran animasi tidak ada: {os.path.basename(p['out'])}")
    return pekerjaan


def komposit(video_masuk, pekerjaan, fps, video_keluar, filter_akhir=None):
    """Tempel tiap potongan pada waktunya. Potongan diam di-loop ffmpeg sepanjang `tahan`.

    filter_akhir: rantai filter video (mis. drawtext teks narasi) yang dijalankan SETELAH
    semua tempelan, dalam encode yang SAMA -- satu encode 1080x1920 terukur 18 dtk untuk video
    14 dtk, jadi motion graphic tidak boleh menambah encode sendiri."""
    args = ["ffmpeg", "-y", "-v", "error", "-i", video_masuk]
    for p in pekerjaan:
        if p["jenis"] == "diam":
            args += ["-loop", "1", "-framerate", str(fps), "-t", f"{p['tahan'] / fps:.4f}", "-i", p["out"]]
        elif p["jenis"] == "concat":
            args += ["-f", "concat", "-safe", "0", "-i", p["out"]]
        else:
            args += ["-i", p["out"]]
    rantai, label = [], "0:v"
    for i, p in enumerate(pekerjaan, 1):
        t = p["mulai"] / fps
        pudar = ""
        if p.get("pudar_masuk"):
            # Cutaway B-roll (klip opak): muncul dengan pudar alpha, bukan potongan kasar.
            pudar += f",fade=t=in:st=0:d={p['pudar_masuk'] / fps:.4f}:alpha=1"
        if p.get("pudar"):
            # Animasi keluar = pudar alpha di ujung potongan (gambar diam motion / klip cutaway).
            lama = p.get("tahan") or p.get("lama")
            pudar += (f",fade=t=out:st={(lama - p['pudar']) / fps:.4f}"
                      f":d={p['pudar'] / fps:.4f}:alpha=1")
        rantai.append(f"[{i}:v]format=yuva420p{pudar},setpts=PTS-STARTPTS+{t:.4f}/TB[o{i}]")
        rantai.append(f"[{label}][o{i}]overlay=eof_action=pass:format=yuv420[v{i}]")
        label = f"v{i}"
    if filter_akhir:
        rantai.append(f"[{label}]" + ",".join(filter_akhir) + "[vakhir]")
        label = "vakhir"
    skrip = video_keluar + ".filter.txt"
    with open(skrip, "w", encoding="utf-8") as f:
        f.write(";".join(rantai))
    try:
        r = subprocess.run(args + ["-filter_complex_script", skrip, "-map", f"[{label}]",
                                   "-map", "0:a?", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                                   "-preset", "fast", "-c:a", "copy", video_keluar],
                           capture_output=True, text=True)
    finally:
        os.path.exists(skrip) and os.remove(skrip)
    if r.returncode != 0:
        raise OverlayError(f"penempelan animasi gagal: {r.stderr[-300:]}")


def tempel_teks_animasi(video_masuk, items, video_keluar, *, lebar, tinggi, fps, durasi,
                        posisi, font, animasi, tambahan=None):
    """Render + tempel. Melempar OverlayError; berkas kerja selalu dibersihkan.
    tambahan: pekerjaan motion graphic yang sudah dirender, ditempel DI BAWAH teks dalam
    komposit yang sama."""
    props_items, kerja = rencana(items, fps, animasi)
    props = {"lebar": lebar, "tinggi": tinggi, "fps": fps, "durasi": durasi, "posisi": posisi,
             "font": font, "animasi": animasi, "items": props_items}
    folder = tempfile.mkdtemp(prefix="_overlay_", dir=os.path.dirname(os.path.abspath(video_keluar)))
    try:
        pekerjaan = _jalankan_node(props, kerja, folder)
        komposit(video_masuk, list(tambahan or []) + pekerjaan, fps, video_keluar)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return {"animasi": animasi, "frame_chromium": total_frame_chromium(kerja), "item": len(items)}


def render_motion(items, folder, *, lebar, tinggi, fps, durasi, aksen=MOTION_AKSEN, tata="atas"):
    """Render potongan motion graphic ke `folder` (belum ditempel). Return (pekerjaan, info).
    Penempelan dilakukan komposit() -- bersama encode teks supaya tidak ada encode tambahan."""
    props_items, kerja = rencana_motion(items, fps)
    if not kerja:
        raise OverlayError("tidak ada elemen motion")
    props = {"lebar": lebar, "tinggi": tinggi, "fps": fps, "durasi": durasi, "aksen": aksen,
             "items": props_items, "tata": tata}
    pekerjaan = _jalankan_node(props, kerja, folder, komposisi="MotionOverlay")
    return pekerjaan, {"frame_chromium": total_frame_chromium(kerja), "item": len(items)}


# Caption dinamis (CaptionDinamis.jsx): frame masuk HARUS sama dengan yang dipakai komponen (props).
CAPTION_MASUK = 8        # 0,33 dtk pada 24 fps: pop kata kunci
CAPTION_BATAS_DETIK = int(os.getenv("CAPTION_TIMEOUT", "240"))


def rencana_caption(potongan, fps):
    """Potongan caption -> (items_props, jadwal). items_props untuk timeline RINGKAS
    (CaptionDinamis ringkas=true): potongan ke-j = frame ringkas [j*(M+1), j*(M+1)+M], yaitu M frame
    masuk + 1 frame diam. jadwal: (frame_mulai_asli, lama_frame) per potongan, tak bertumpuk
    (dijamin caption_dinamis.potong; pembulatan frame dirapikan di sini)."""
    props_items, jadwal, batas = [], [], 0
    for p in potongan:
        dari = max(int(round(float(p["mulai"]) * fps)), batas)
        dur = int(round(float(p["selesai"]) * fps)) - dari
        if dur < 1:
            continue
        batas = dari + dur
        props_items.append({"kata": p["kata"], "kunci": p.get("kunci"), "mulai": dari / fps,
                            "selesai": (dari + dur) / fps, "masukFrames": CAPTION_MASUK,
                            "varian": p.get("varian") or "biasa"})
        jadwal.append((dari, dur))
    return props_items, jadwal


def daftar_concat(jadwal, frame_png, kosong, fps):
    """Baris daftar concat ffmpeg: celah = PNG transparan, M frame masuk masing-masing 1/fps, lalu
    frame diam selama sisa potongan. `frame_png` = PNG timeline ringkas terurut."""
    m1 = CAPTION_MASUK + 1
    baris, t = [], 0

    def tambah(path, frames):
        baris.append(f"file '{path}'")
        baris.append(f"duration {frames / fps:.9f}")

    for j, (dari, dur) in enumerate(jadwal):
        if dari > t:
            tambah(kosong, dari - t)
        for m in range(min(CAPTION_MASUK, dur)):
            tambah(frame_png[j * m1 + m], 1)
        if dur > CAPTION_MASUK:
            tambah(frame_png[j * m1 + CAPTION_MASUK], dur - CAPTION_MASUK)
        t = dari + dur
    tambah(kosong, 1)
    baris.append(f"file '{kosong}'")         # entri terakhir diulang: durasinya dihormati concat
    return baris


def render_caption(potongan, folder, *, lebar, tinggi, fps, durasi, y=0.7, lebar_maks=0.74):
    """Render caption jadi SATU pekerjaan komposit (daftar concat PNG). Return (pekerjaan, info).

    Asal (27 Sep, render nyata 42 potongan): satu input ffmpeg per potongan (klip ProRes + gambar
    diam di-loop) -> komposit ~100 input -> ffmpeg 3,8 GB RSS -> DIBUNUH OOM killer (8 GB RAM).
    Sekarang satu sesi Chromium (timeline ringkas) + satu input concat: memori tidak tumbuh
    dengan jumlah potongan."""
    props_items, jadwal = rencana_caption(potongan, fps)
    if not jadwal:
        raise OverlayError("tidak ada potongan caption")
    n = len(props_items) * (CAPTION_MASUK + 1)
    props = {"lebar": lebar, "tinggi": tinggi, "fps": fps, "durasi": n / fps, "items": props_items,
             "y": y, "lebarMaks": lebar_maks, "ringkas": True}
    folder_png = os.path.join(folder, "_caption_png")
    _jalankan_node(props, [{"jenis": "urutan", "dari": 0, "sampai": n - 1, "mulai": 0,
                            "out_dir": folder_png}], folder, komposisi="CaptionDinamis",
                   batas=CAPTION_BATAS_DETIK)
    frame_png = sorted(os.path.join(folder_png, f) for f in os.listdir(folder_png) if f.endswith(".png"))
    if len(frame_png) != n:
        raise OverlayError(f"frame caption {len(frame_png)} dari {n}")
    from PIL import Image
    kosong = os.path.join(folder, "_caption_kosong.png")
    Image.new("RGBA", (lebar, tinggi), (0, 0, 0, 0)).save(kosong)
    daftar = os.path.join(folder, "_caption.txt")
    with open(daftar, "w", encoding="utf-8") as f:
        f.write("\n".join(daftar_concat(jadwal, frame_png, kosong, fps)) + "\n")
    return ([{"jenis": "concat", "out": daftar, "mulai": 0}],
            {"frame_chromium": n, "potongan": len(props_items)})


# ---------------------------------------------------------------- tata letak "panggung"

PANGGUNG_BATAS_DETIK = int(os.getenv("PANGGUNG_TIMEOUT", "240"))


def kartu_panggung(lebar, tinggi):
    """Kotak kartu video pembicara (piksel, genap untuk yuv420p): 86% lebar, 42%-94% tinggi."""
    genap = lambda v: int(round(v / 2)) * 2      # noqa: E731
    w, h = genap(lebar * 0.86), genap(tinggi * 0.52)
    return {"x": genap((lebar - w) / 2), "y": genap(tinggi * 0.42), "w": w, "h": h,
            "r": genap(lebar * 0.045)}


def render_panggung(jendela, folder, *, lebar, tinggi, fps):
    """Latar opak tiap jendela panggung -> SATU pekerjaan concat PNG (frame asli di waktunya,
    PNG transparan di luar jendela). Return (daftar_concat, info). Melempar OverlayError."""
    items, frames, t = [], [], 0
    for j in jendela:
        dari = int(round(float(j["mulai"]) * fps))
        dur = int(round(float(j["selesai"]) * fps)) - dari
        if dur < 2:
            continue
        items.append({"ilustrasi": j["ilustrasi"], "teks": j.get("teks") or "", "dari": t, "dur": dur})
        frames.append((dari, dur))
        t += dur
    if not items:
        raise OverlayError("tidak ada jendela panggung")
    props = {"lebar": lebar, "tinggi": tinggi, "fps": fps, "durasi": t / fps, "items": items,
             "kartu": kartu_panggung(lebar, tinggi)}
    folder_png = os.path.join(folder, "_panggung_png")
    _jalankan_node(props, [{"jenis": "urutan", "dari": 0, "sampai": t - 1, "mulai": 0,
                            "out_dir": folder_png}], folder, komposisi="Panggung",
                   batas=PANGGUNG_BATAS_DETIK)
    png = sorted(os.path.join(folder_png, f) for f in os.listdir(folder_png) if f.endswith(".png"))
    if len(png) != t:
        raise OverlayError(f"frame panggung {len(png)} dari {t}")
    from PIL import Image
    # RGB, bukan RGBA: frame Remotion latar opak (RGB). Satu daftar concat dengan PNG campuran
    # RGBA+RGB membuat ffmpeg 4.4 menampilkan latar TRANSPARAN (terukur 29 Sep: latar tidak muncul
    # sama sekali). Hitam opak aman: latar hanya ditempel di dalam jendela (enable=between).
    kosong = os.path.join(folder, "_panggung_kosong.png")
    Image.new("RGB", (lebar, tinggi), (0, 0, 0)).save(kosong)
    baris, pos, k = [], 0, 0
    for dari, dur in frames:
        if dari > pos:
            baris += [f"file '{kosong}'", f"duration {(dari - pos) / fps:.9f}"]
        for _ in range(dur):
            baris += [f"file '{png[k]}'", f"duration {1 / fps:.9f}"]
            k += 1
        pos = dari + dur
    baris += [f"file '{kosong}'", f"duration {1 / fps:.9f}", f"file '{kosong}'"]
    daftar = os.path.join(folder, "_panggung.txt")
    with open(daftar, "w", encoding="utf-8") as f:
        f.write("\n".join(baris) + "\n")
    return daftar, {"frame_chromium": t, "jumlah_jendela": len(items)}


# ---------------------------------------------------------------- cover didesain

SAMPUL_BATAS_DETIK = int(os.getenv("SAMPUL_TIMEOUT", "120"))


def render_sampul(jpg_masuk, judul, emas, out_jpg, folder, *, lebar, tinggi, y_judul=0.72):
    """Frame terbaik + judul -> cover JPG. Gambar dikirim sebagai data URL (tanpa berkas di
    public/). Melempar OverlayError."""
    import base64
    with open(jpg_masuk, "rb") as f:
        data_url = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
    props = {"lebar": lebar, "tinggi": tinggi, "fps": 24, "durasi": 1 / 24,
             "gambar": data_url, "judul": judul, "emas": emas, "yJudul": y_judul}
    kerja = [{"jenis": "diam", "frame": 0, "tahan": 1, "mulai": 0}]
    (p,) = _jalankan_node(props, kerja, folder, komposisi="Sampul", batas=SAMPUL_BATAS_DETIK)
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", p["out"], "-q:v", "3", out_jpg],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out_jpg):
        raise OverlayError(f"cover gagal disimpan: {r.stderr[-200:]}")
    return out_jpg


# ---------------------------------------------------------------- pratinjau preset gaya

PRATINJAU_W, PRATINJAU_H, PRATINJAU_KOLOM = 540, 960, 3
PRATINJAU_FRAME = 20       # semua animasi masuk (maks 12 frame) sudah diam


def _latar_pratinjau():
    """Latar SINTETIS (gradien), bukan bahan user: pratinjau di-cache untuk semua pengguna."""
    import base64
    import io
    from PIL import Image
    im = Image.linear_gradient("L").resize((PRATINJAU_W, PRATINJAU_H))
    warna = Image.merge("RGB", (im.point(lambda v: 40 + v // 3), im.point(lambda v: 70 + v // 4),
                                im.point(lambda v: 110 - v // 6)))
    b = io.BytesIO()
    warna.save(b, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def render_pratinjau_gaya(presets, out_jpg):
    """Satu kartu per preset (komposisi PratinjauGaya), lalu disusun jadi satu grid JPEG."""
    from PIL import Image
    folder = tempfile.mkdtemp(prefix="_pratinjau_", dir=os.path.dirname(os.path.abspath(out_jpg)))
    try:
        latar = _latar_pratinjau()
        kartu = []
        for p in presets:
            props = {"lebar": PRATINJAU_W, "tinggi": PRATINJAU_H, "fps": 24, "durasi": 2.0,
                     "gambar": latar, "label": p["label"], "tema": p["tema"]}
            sub = os.path.join(folder, p["nama"])
            os.makedirs(sub)
            (hasil,) = _jalankan_node(props, [{"jenis": "diam", "frame": PRATINJAU_FRAME, "tahan": 1, "mulai": 0}], sub,
                                      komposisi="PratinjauGaya", batas=SAMPUL_BATAS_DETIK)
            kartu.append(hasil["out"])
        baris = (len(kartu) + PRATINJAU_KOLOM - 1) // PRATINJAU_KOLOM
        grid = Image.new("RGB", (PRATINJAU_W * PRATINJAU_KOLOM, PRATINJAU_H * baris), (16, 16, 20))
        for i, path in enumerate(kartu):
            with Image.open(path) as im:
                grid.paste(im.convert("RGB"), ((i % PRATINJAU_KOLOM) * PRATINJAU_W, (i // PRATINJAU_KOLOM) * PRATINJAU_H))
        sementara = out_jpg + ".tmp.jpg"
        grid.save(sementara, "JPEG", quality=85)
        os.replace(sementara, out_jpg)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return out_jpg
