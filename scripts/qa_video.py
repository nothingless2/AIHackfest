"""Pemeriksa mutu video SEBELUM dikirim ke user -- supaya cacat teknis tidak baru ketahuan
setelah diposting (permintaan user 25 Sep: "tinggal terima video tanpa revisi").

Semua DIUKUR kode (aturan #5/#8), tidak ditebak:
- kenyaringan (ebur128: LUFS terintegrasi + true peak) -> di luar target DIPERBAIKI otomatis
  (loudnorm, video disalin apa adanya), lalu diukur ulang;
- frame hitam (blackdetect) dan frame beku (freezedetect, hanya bila bahan tanpa foto --
  foto memang diam);
- teks/grafik terpotong di tepi & masuk zona UI platform: frame AKHIR dibandingkan dengan
  video SEBELUM ditempeli overlay pada waktu yang sama; piksel yang berubah = overlay;
- durasi video vs audio.
Hasil: {"lolos", "masalah": [...], "peringatan": [...], "diperbaiki": [...], "ukur": {...}}.
Pemeriksaan yang GAGAL berjalan dicatat sebagai peringatan, bukan dianggap lolos (aturan #7).
"""

import os
import re
import subprocess

import numpy as np

TARGET_LUFS = float(os.getenv("QA_TARGET_LUFS", "-16"))
TOL_LUFS = float(os.getenv("QA_TOLERANSI_LUFS", "3"))
TP_MAKS = float(os.getenv("QA_TRUE_PEAK_MAKS", "-1.0"))
HITAM_MIN = 0.4           # detik
BEKU_MIN = 1.5            # detik
TEPI = 0.03               # lebar kolom tepi (fraksi lebar) untuk uji "terpotong"; subtitle maks 6-94%
# Zona UI TikTok/Reels (fraksi kanvas 9:16): tombol kanan (like/komentar/bagikan) dan area
# caption/username di bawah. Overlay yang masuk sini tertutup UI saat diposting.
ZONA_UI = {"tombol kanan": (0.88, 1.0, 0.45, 0.82), "caption bawah": (0.0, 1.0, 0.88, 1.0)}
LEBAR_UJI, TINGGI_UJI = 108, 192
AMBANG_PIKSEL = 40        # beda kanal (0-255) yang dihitung "berubah karena overlay"
FRAKSI_ZONA = 0.02        # >2% piksel zona berubah = ada overlay di sana
PIKSEL_TEPI_MIN = 4       # piksel terang di kolom tepi (resolusi uji) = teks keluar kanvas


def _ffmpeg_stderr(args, timeout=300):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args], capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, r.stderr


def _durasi(path, stream):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries",
                        "stream=duration", "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def ada_audio(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                        "stream=index", "-of", "csv=p=0", path], capture_output=True, text=True)
    return bool(r.stdout.strip())


def ukur_loudness(path):
    """{"lufs", "tp"} atau None bila tak terukur."""
    kode, err = _ffmpeg_stderr(["-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    ringkasan = err[err.rfind("Summary:"):] if "Summary:" in err else ""
    i = re.search(r"I:\s*(-?\d+(?:\.\d+)?) LUFS", ringkasan)
    tp = re.search(r"Peak:\s*(-?(?:\d+(?:\.\d+)?|inf)) dBFS", ringkasan)
    if kode != 0 or not i:
        return None
    return {"lufs": float(i.group(1)), "tp": float(tp.group(1)) if tp and tp.group(1) != "-inf" else -99.0}


def perbaiki_loudness(path, keluar):
    kode, err = _ffmpeg_stderr(["-y", "-i", path, "-c:v", "copy", "-af",
                                # loudnorm satu pass tidak menjamin true peak (terukur -0,2 dBTP
                                # di render nyata); alimiter menjepit puncak di bawah TP_MAKS.
                                f"loudnorm=I={TARGET_LUFS}:TP={TP_MAKS - 0.5}:LRA=11,"
                                f"alimiter=limit={10 ** ((TP_MAKS - 2.0) / 20):.3f}:level=disabled",   # sisa 2 dB untuk puncak antar-sampel AAC
                                "-ar", "44100", "-c:a", "aac", "-b:a", "192k", keluar])
    return kode == 0 and os.path.exists(keluar)


def rentang_hitam(path, min_detik=HITAM_MIN):
    kode, err = _ffmpeg_stderr(["-i", path, "-an", "-vf",
                                f"blackdetect=d={min_detik}:pix_th=0.10", "-f", "null", "-"])
    if kode != 0:
        return None
    return [(float(a), float(b)) for a, b in
            re.findall(r"black_start:(\d+(?:\.\d+)?)\s+black_end:(\d+(?:\.\d+)?)", err)]


def rentang_beku(path, min_detik=BEKU_MIN):
    kode, err = _ffmpeg_stderr(["-i", path, "-an", "-vf",
                                f"freezedetect=n=0.003:d={min_detik}", "-f", "null", "-"])
    if kode != 0:
        return None
    mulai = [float(x) for x in re.findall(r"freeze_start:\s*(\d+(?:\.\d+)?)", err)]
    akhir = [float(x) for x in re.findall(r"freeze_end:\s*(\d+(?:\.\d+)?)", err)]
    dur = _durasi(path, "v:0") or 0.0
    return [(a, akhir[i] if i < len(akhir) else dur) for i, a in enumerate(mulai)]


FPS_UJI = 24
GESER_MAKS = 2            # frame: pencuplikan dua encode bisa meleset beberapa frame


def _semua_frame(path):
    """Seluruh video pada resolusi uji, SEKALI dekode (deterministik per indeks frame; seek
    -ss per cuplikan pada dua encode berbeda terukur meleset >1 frame)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-an", "-vf",
                        f"fps={FPS_UJI},scale={LEBAR_UJI}:{TINGGI_UJI}", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=300)
    n = LEBAR_UJI * TINGGI_UJI * 3
    if r.returncode != 0 or len(r.stdout) < n:
        return None
    return np.frombuffer(r.stdout[: len(r.stdout) // n * n], np.uint8).reshape(-1, TINGGI_UJI, LEBAR_UJI, 3)


def overlay_di_zona(akhir, dasar, durasi, *, lewati=(), langkah=0.5):
    """{"terpotong": [detik], "zona": {nama: [detik]}} -- piksel yang jadi LEBIH TERANG di video
    akhir dibanding video sebelum overlay (teks/grafik), dicicip tiap `langkah` detik.
    `lewati`: jendela cutaway B-roll (seluruh frame memang berganti).
    - Hanya "lebih terang": lapisan peredup kartu pembuka selebar layar bukan teks terpotong
      (salah tanda pertama di render nyata 25 Sep, detik 0-2).
    - Harus lebih terang dari SEMUA frame dasar dalam ±GESER_MAKS: gerakan (tangan di tepi)
      pada encode yang meleset beberapa frame bukan overlay (salah tanda kedua, detik 6-13)."""
    hasil = {"terpotong": [], "zona": {k: [] for k in ZONA_UI}}
    fa_all, fd_all = _semua_frame(akhir), _semua_frame(dasar)
    if fa_all is None or fd_all is None:
        raise RuntimeError("video tidak bisa didekode untuk uji zona")
    fa_all, fd_all = fa_all.astype(np.int16), fd_all.astype(np.int16)
    kiri, kanan = int(LEBAR_UJI * TEPI) + 1, LEBAR_UJI - int(LEBAR_UJI * TEPI) - 1
    t = 0.25
    while t < durasi - 0.25:
        i = int(round(t * FPS_UJI))
        if i < len(fa_all) and not any(a - 0.2 <= t <= b + 0.2 for a, b in lewati):
            fa = fa_all[i]
            dasar_k = [fd_all[j] for j in range(i - GESER_MAKS, i + GESER_MAKS + 1) if 0 <= j < len(fd_all)]
            beda = np.logical_and.reduce([(fa - fd).max(axis=2) > AMBANG_PIKSEL for fd in dasar_k])
            # Jumlah ABSOLUT, bukan rata-rata kolom: goresan huruf tipis tenggelam bila dirata-
            # ratakan ke seluruh tinggi layar (terukur: teks jelas terpotong tidak tertangkap).
            if beda[:, :kiri].sum() >= PIKSEL_TEPI_MIN or beda[:, kanan:].sum() >= PIKSEL_TEPI_MIN:
                hasil["terpotong"].append(round(t, 2))
            for nama, (x0, x1, y0, y1) in ZONA_UI.items():
                z = beda[int(y0 * TINGGI_UJI):int(y1 * TINGGI_UJI), int(x0 * LEBAR_UJI):int(x1 * LEBAR_UJI)]
                if z.size and z.mean() > FRAKSI_ZONA:
                    hasil["zona"][nama].append(round(t, 2))
        t += langkah
    return hasil


def _rentang_teks(rentang):
    return ", ".join(f"{a:.1f}-{b:.1f} dtk" for a, b in rentang[:4])


def periksa(path, *, dasar=None, ada_foto=False, harus_bersuara=True, lewati=(), perbaiki=True):
    """Periksa (dan bila perlu perbaiki kenyaringan) berkas akhir `path` DI TEMPAT."""
    masalah, peringatan, diperbaiki, ukur = [], [], [], {}
    durasi = _durasi(path, "v:0") or 0.0

    if not ada_audio(path):
        if harus_bersuara:
            masalah.append("video TIDAK bersuara padahal seharusnya ada suara")
    else:
        lu = ukur_loudness(path)
        if lu is None:
            peringatan.append("kenyaringan tidak terukur")
        else:
            ukur["loudness"] = lu
            meleset = abs(lu["lufs"] - TARGET_LUFS) > TOL_LUFS or lu["tp"] > TP_MAKS
            if meleset and perbaiki:
                sementara = path + ".qa.mp4"
                if perbaiki_loudness(path, sementara) and (baru := ukur_loudness(sementara)):
                    os.replace(sementara, path)
                    diperbaiki.append(f"kenyaringan {lu['lufs']:.1f} LUFS (puncak {lu['tp']:.1f} dBTP) "
                                      f"-> {baru['lufs']:.1f} LUFS")
                    ukur["loudness_setelah"] = baru
                else:
                    if os.path.exists(sementara):
                        os.remove(sementara)
                    masalah.append(f"kenyaringan {lu['lufs']:.1f} LUFS di luar target dan gagal diperbaiki")
            elif meleset:
                masalah.append(f"kenyaringan {lu['lufs']:.1f} LUFS di luar target {TARGET_LUFS:.0f}±{TOL_LUFS:.0f}")
        da = _durasi(path, "a:0")
        if da is not None and durasi and abs(da - durasi) > 0.25:
            masalah.append(f"durasi audio {da:.2f} dtk tidak sama dengan video {durasi:.2f} dtk")

    hitam = rentang_hitam(path)
    if hitam is None:
        peringatan.append("deteksi frame hitam gagal berjalan")
    else:
        # Fade masuk/keluar di ujung video wajar; yang dilaporkan hanya di tengah.
        tengah = [(a, b) for a, b in hitam if a > 0.3 and b < durasi - 0.3]
        if tengah:
            masalah.append(f"frame hitam di {_rentang_teks(tengah)}")

    if not ada_foto:
        beku = rentang_beku(path)
        if beku is None:
            peringatan.append("deteksi frame beku gagal berjalan")
        elif beku:
            masalah.append(f"gambar beku di {_rentang_teks(beku)}")

    if dasar and os.path.exists(dasar):
        try:
            o = overlay_di_zona(path, dasar, durasi, lewati=lewati)
        except Exception as e:
            peringatan.append(f"uji tepi/zona gagal berjalan ({type(e).__name__})")
            o = {"terpotong": [], "zona": {}}
        if o["terpotong"]:
            masalah.append(f"teks/grafik menyentuh tepi layar (terpotong) di detik "
                           f"{', '.join(map(str, o['terpotong'][:4]))}")
        for nama, t in o["zona"].items():
            if len(t) >= 2:
                peringatan.append(f"teks/grafik masuk area {nama} TikTok/Reels di detik "
                                  f"{', '.join(map(str, t[:4]))}")
    return {"lolos": not masalah, "masalah": masalah, "peringatan": peringatan,
            "diperbaiki": diperbaiki, "ukur": ukur}
