"""Deteksi wajah di video (OpenCV Haar, frontal): pusat zoom punch-in, posisi kartu logo, pilihan
frame cover. Tanpa wajah -> None; pemakai jatuh ke posisi aman bawaan, bukan tebakan.

Frame diperkecil ke lebar KECIL sebelum dideteksi (cepat; wajah pembicara di video tegak besar).
Kotak dikembalikan dalam piksel video ASLI.
"""

import subprocess

import numpy as np

KECIL = 360                  # lebar frame saat deteksi
UKURAN_MIN = 0.12            # wajah < 12% lebar frame diabaikan (bukan pembicara utama)


def _detektor():
    import cv2
    return cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def _ukuran(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, timeout=30).stdout.strip()
    w, h = (int(v) for v in out.split(",")[:2])
    return w, h


def frame_abu(video, detik, lebar=KECIL):
    """Satu frame abu-abu (numpy uint8) diperkecil ke `lebar`, atau None."""
    w, h = _ukuran(video)
    tinggi = int(round(h * lebar / w / 2)) * 2
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, detik):.3f}", "-i", str(video),
                          "-frames:v", "1", "-vf", f"scale={lebar}:{tinggi}", "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], capture_output=True, timeout=60).stdout
    if len(raw) != lebar * tinggi:
        return None
    return np.frombuffer(raw, np.uint8).reshape(tinggi, lebar)


def cari(gambar_abu, detektor=None):
    """Kotak wajah terbesar (x, y, w, h) dalam piksel gambar ini, atau None."""
    det = detektor or _detektor()
    kotak = det.detectMultiScale(gambar_abu, scaleFactor=1.1, minNeighbors=5,
                                 minSize=(int(gambar_abu.shape[1] * UKURAN_MIN),) * 2)
    if len(kotak) == 0:
        return None
    x, y, w, h = max(kotak, key=lambda k: k[2] * k[3])
    return int(x), int(y), int(w), int(h)


def kotak_wajah(video, detik_list):
    """{detik: (x, y, w, h) | None} dalam piksel video ASLI."""
    w_asli, _ = _ukuran(video)
    skala = w_asli / KECIL
    det = _detektor()
    hasil = {}
    for t in detik_list:
        g = frame_abu(video, t)
        k = cari(g, det) if g is not None else None
        hasil[t] = tuple(int(round(v * skala)) for v in k) if k else None
    return hasil


def median(kotak_list):
    """Kotak median dari beberapa deteksi (mengabaikan None); None bila tak ada."""
    ada = [k for k in kotak_list if k]
    if not ada:
        return None
    return tuple(int(v) for v in np.median(np.array(ada), axis=0))


def ketajaman(gambar_abu):
    """Varians Laplacian: makin besar makin tajam (pilihan frame cover)."""
    import cv2
    return float(cv2.Laplacian(gambar_abu, cv2.CV_64F).var())
