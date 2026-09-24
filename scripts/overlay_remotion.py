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


def rencana(items, fps, animasi):
    """Item -> (items_untuk_props, pekerjaan_render).

    Pekerjaan: {"jenis": "klip", "dari": f0, "sampai": f1} (inklusif) atau
    {"jenis": "diam", "frame": f, "tahan": n_frame}. Tiap pekerjaan juga membawa "mulai"
    (frame global tempat ia ditempel)."""
    props_items, kerja = [], []
    for it in items:
        dari = int(round(float(it["mulai"]) * fps))
        dur = max(2, int(round((float(it["selesai"]) - float(it["mulai"])) * fps)))
        e = masuk_frames(it["text"], animasi)
        props_items.append({"text": it["text"], "mulai": dari / fps, "selesai": (dari + dur) / fps,
                            "masukFrames": e, "keluarFrames": KELUAR})
        if dur <= e + KELUAR + 2:
            kerja.append({"jenis": "klip", "dari": dari, "sampai": dari + dur - 1, "mulai": dari})
            continue
        kerja.append({"jenis": "klip", "dari": dari, "sampai": dari + e - 1, "mulai": dari})
        tahan = dur - e - KELUAR
        kerja.append({"jenis": "diam", "frame": dari + e, "tahan": tahan, "mulai": dari + e})
        kerja.append({"jenis": "klip", "dari": dari + dur - KELUAR, "sampai": dari + dur - 1,
                      "mulai": dari + dur - KELUAR})
    return props_items, kerja


def total_frame_chromium(kerja):
    return sum((k["sampai"] - k["dari"] + 1) if k["jenis"] == "klip" else 1 for k in kerja)


def _jalankan_node(props, kerja, folder):
    if not shutil.which("node"):
        raise OverlayError("node tidak terpasang")
    if not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules")):
        raise OverlayError("paket Remotion belum terpasang (npm install di remotion/)")
    pekerjaan = []
    for i, k in enumerate(kerja):
        out = os.path.join(folder, f"_overlay_{i}." + ("mov" if k["jenis"] == "klip" else "png"))
        pekerjaan.append({**k, "out": out})
    masukan = os.path.join(folder, "_overlay_props.json")
    with open(masukan, "w", encoding="utf-8") as f:
        json.dump({"props": props, "pekerjaan": pekerjaan}, f, ensure_ascii=False)
    proc = subprocess.Popen(["node", os.path.join(REMOTION_DIR, "render.mjs"), masukan],
                            cwd=REMOTION_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=BATAS_DETIK)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)       # Chromium ikut mati, bukan jadi yatim
        proc.communicate()
        raise OverlayError(f"render animasi melewati {BATAS_DETIK} detik")
    if proc.returncode != 0:
        raise OverlayError(f"render animasi gagal: {(err or out).strip()[-300:]}")
    for p in pekerjaan:
        if not os.path.exists(p["out"]):
            raise OverlayError(f"keluaran animasi tidak ada: {os.path.basename(p['out'])}")
    return pekerjaan


def komposit(video_masuk, pekerjaan, fps, video_keluar):
    """Tempel tiap potongan pada waktunya. Potongan diam di-loop ffmpeg sepanjang `tahan`."""
    args = ["ffmpeg", "-y", "-v", "error", "-i", video_masuk]
    for p in pekerjaan:
        if p["jenis"] == "diam":
            args += ["-loop", "1", "-framerate", str(fps), "-t", f"{p['tahan'] / fps:.4f}", "-i", p["out"]]
        else:
            args += ["-i", p["out"]]
    rantai, label = [], "0:v"
    for i, p in enumerate(pekerjaan, 1):
        t = p["mulai"] / fps
        rantai.append(f"[{i}:v]format=yuva420p,setpts=PTS-STARTPTS+{t:.4f}/TB[o{i}]")
        rantai.append(f"[{label}][o{i}]overlay=eof_action=pass:format=yuv420[v{i}]")
        label = f"v{i}"
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
                        posisi, font, animasi):
    """Render + tempel. Melempar OverlayError; berkas kerja selalu dibersihkan."""
    props_items, kerja = rencana(items, fps, animasi)
    props = {"lebar": lebar, "tinggi": tinggi, "fps": fps, "durasi": durasi, "posisi": posisi,
             "font": font, "animasi": animasi, "items": props_items}
    folder = tempfile.mkdtemp(prefix="_overlay_", dir=os.path.dirname(os.path.abspath(video_keluar)))
    try:
        pekerjaan = _jalankan_node(props, kerja, folder)
        komposit(video_masuk, pekerjaan, fps, video_keluar)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return {"animasi": animasi, "frame_chromium": total_frame_chromium(kerja), "item": len(items)}
