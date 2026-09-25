"""Mengenali bagian video yang TIDAK layak tampil: kamera oleng/goyang, buram karena
gerakan, atau terlalu gelap. KODE yang mengukur (aturan #5) -- tanpa LLM, tanpa GPU.

Ukuran per frame (dekode 10 fps, lebar 160 px, grayscale):
- gerak   : pergeseran global antar-frame (phase correlation), dalam % lebar per detik
- cocok   : tinggi puncak phase correlation (0..1). Rendah = frame berurutan tidak
            bisa disejajarkan = kamera berputar/oleng, atau gambar kabur total
- tajam   : variansi Laplacian, relatif terhadap median klip itu sendiri
- terang  : rata-rata luma (0..255)

Hasil: rentang BURUK -> rentang LAYAK (dipakai renderer sama seperti potongan jeda).
Ambang dikalibrasi pada guncangan sintetis berparameter diketahui dan klip nyata user;
lihat tests/test_visual_quality.py.
"""

import hashlib
import json
import os
import subprocess

import numpy as np

FPS_ANALISIS = 10
LEBAR = 160
# Dikalibrasi (lihat tes): klip stabil / pan pelan < 25 %lebar/dtk; guncangan tangan >= 60.
GERAK_MAKS = float(os.getenv("VISUAL_MOTION_MAX", "45"))       # % lebar per detik
COCOK_MIN = float(os.getenv("VISUAL_MATCH_MIN", "0.08"))
TAJAM_REL_MIN = float(os.getenv("VISUAL_SHARP_REL_MIN", "0.35"))
BURUK_MIN_DETIK = 0.3        # gangguan lebih pendek dari ini diabaikan (kedipan)
GABUNG_CELAH = 0.4           # dua rentang buruk yang berjarak < ini digabung
PINGGIR = 0.15               # rentang buruk diperlebar sedikit ke dua sisi


class VisualError(RuntimeError):
    pass


def _frames(path, maks_detik=None):
    args = ["ffmpeg", "-v", "error", "-i", path]
    if maks_detik:
        args += ["-t", str(maks_detik)]
    args += ["-vf", f"fps={FPS_ANALISIS},scale={LEBAR}:-2,format=gray", "-f", "rawvideo", "-"]
    r = subprocess.run(args, capture_output=True, timeout=300)
    if r.returncode != 0:
        raise VisualError(r.stderr.decode(errors="replace")[-200:])
    probe = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1", "-vf",
                            f"scale={LEBAR}:-2", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                           capture_output=True, timeout=60)
    n_px = len(probe.stdout)
    if not n_px or len(r.stdout) < n_px:
        raise VisualError("tidak ada frame video")
    tinggi = n_px // LEBAR
    x = np.frombuffer(r.stdout[: (len(r.stdout) // n_px) * n_px], np.uint8)
    return x.reshape(-1, tinggi, LEBAR).astype(np.float32)


def _geser(a, b, jendela):
    """(dx, dy, puncak) phase correlation antara dua frame."""
    A = np.fft.rfft2((a - a.mean()) * jendela)
    B = np.fft.rfft2((b - b.mean()) * jendela)
    R = A * np.conj(B)
    R /= np.abs(R) + 1e-9
    c = np.fft.irfft2(R, s=a.shape)
    iy, ix = np.unravel_index(int(np.argmax(c)), c.shape)
    h, w = a.shape
    dy = iy - h if iy > h // 2 else iy
    dx = ix - w if ix > w // 2 else ix
    return dx, dy, float(c.max())


def _laplace_var(f):
    lap = -4 * f[1:-1, 1:-1] + f[:-2, 1:-1] + f[2:, 1:-1] + f[1:-1, :-2] + f[1:-1, 2:]
    return float(lap.var())


def ukur(path, maks_detik=None):
    """Deret ukuran per frame analisis. Frame i mewakili waktu i / FPS_ANALISIS."""
    fr = _frames(path, maks_detik)
    h, w = fr.shape[1:]
    jendela = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    gerak, cocok = [0.0], [1.0]
    for i in range(1, len(fr)):
        dx, dy, p = _geser(fr[i - 1], fr[i], jendela)
        gerak.append(float(np.hypot(dx, dy)) / w * 100 * FPS_ANALISIS)
        cocok.append(p)
    tajam = np.array([_laplace_var(f) for f in fr])
    return {
        "gerak": np.array(gerak), "cocok": np.array(cocok),
        "tajam_rel": tajam / max(float(np.median(tajam)), 1e-6),
        "terang": fr.mean(axis=(1, 2)),
        "durasi": len(fr) / FPS_ANALISIS,
    }


def _alasan_frame(m, i):
    # Gelap SENGAJA tidak dihitung: video malam/panggung adalah konten sah, bukan cacat.
    if m["gerak"][i] > GERAK_MAKS:
        return "goyang"
    if m["cocok"][i] < COCOK_MIN:
        return "oleng"
    if m["tajam_rel"][i] < TAJAM_REL_MIN:
        return "buram"
    return None


def rentang_buruk(m):
    """[(mulai, selesai, alasan)] dalam detik."""
    n = len(m["gerak"])
    tanda = [_alasan_frame(m, i) for i in range(n)]
    # Median 3 frame: satu frame aneh (kedipan/kompresi) tidak dihitung sebagai guncangan.
    halus = [tanda[i] if sum(1 for j in (i - 1, i, i + 1) if 0 <= j < n and tanda[j]) >= 2 else None
             for i in range(n)]
    hasil, i = [], 0
    while i < n:
        if not halus[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and halus[j + 1]:
            j += 1
        alasan = max(set(halus[i:j + 1]) - {None}, key=halus[i:j + 1].count)
        hasil.append([i / FPS_ANALISIS, (j + 1) / FPS_ANALISIS, alasan])
        i = j + 1
    gabung = []
    for r in hasil:
        if gabung and r[0] - gabung[-1][1] < GABUNG_CELAH:
            gabung[-1][1] = r[1]
        else:
            gabung.append(r)
    dur = m["durasi"]
    return [(max(0.0, a - PINGGIR), min(dur, b + PINGGIR), al) for a, b, al in gabung
            if b - a >= BURUK_MIN_DETIK]


def kurangi(ranges, buruk, min_keep=0.8):
    """Rentang layak = `ranges` dikurangi rentang buruk. Potongan < min_keep dibuang."""
    hasil = []
    for a, b in ranges:
        potong = [(a, b)]
        for x, y, _ in buruk:
            baru = []
            for p, q in potong:
                if y <= p or x >= q:
                    baru.append((p, q))
                    continue
                if x > p:
                    baru.append((p, x))
                if y < q:
                    baru.append((y, q))
            potong = baru
        hasil.extend((p, q) for p, q in potong if q - p >= min_keep)
    return hasil


def rentang_layak(durasi, buruk, min_keep=1.5):
    """(rentang, dikembalikan). SEMUA rentang layak [(a, b)] sebuah klip -- dipakai brief
    (panjang bahan untuk naskah) dan renderer (alokasi) supaya angkanya sama. Tidak ada bagian
    layak yang cukup panjang -> seluruh klip dipakai (dikembalikan=True): lebih baik tampil
    goyang daripada klip hilang tanpa kabar."""
    if durasi <= 0:
        return [], False
    layak = kurangi([(0.0, durasi)], buruk or [], min_keep=min_keep)
    if layak:
        return layak, False
    return [(0.0, durasi)], bool(buruk)


def analisis(path):
    """{durasi, buruk:[(a,b,alasan)], layak_detik} -- atau melempar VisualError."""
    m = ukur(path)
    buruk = rentang_buruk(m)
    return {"durasi": m["durasi"], "buruk": buruk,
            "buruk_detik": round(sum(b - a for a, b, _ in buruk), 2)}


def _cache_path():
    from common import STATE_DIR
    return os.path.join(STATE_DIR, "visual_quality_cache.json")


def _kunci(path):
    st = os.stat(path)
    ambang = f"{GERAK_MAKS}|{COCOK_MIN}|{TAJAM_REL_MIN}"
    return hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|{ambang}"
                        .encode()).hexdigest()


def analisis_cached(path):
    """Seperti analisis(), tapi hasilnya dipakai bersama tahap brief (lembar kontak untuk
    BrainIdea) dan tahap render -- klip yang sama tidak didekode dua kali per run.
    Kunci memuat path, ukuran, mtime, dan ambang: berkas/ambang berubah = diukur ulang."""
    cp = _cache_path()
    try:
        with open(cp, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    k = _kunci(path)
    if k in cache:
        c = cache[k]
        return {**c, "buruk": [tuple(r) for r in c["buruk"]]}
    hasil = analisis(path)
    cache[k] = hasil
    if len(cache) > 500:        # batas kasar; cache hanya optimasi
        cache = dict(list(cache.items())[-500:])
    try:
        os.makedirs(os.path.dirname(cp), exist_ok=True)
        tmp = cp + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(tmp, cp)
    except OSError:
        pass
    return hasil


def aktif():
    return (os.getenv("VISUAL_CUT") or "1").strip().lower() not in ("0", "false", "off", "no")
