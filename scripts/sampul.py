"""Cover didesain: frame terbaik (wajah besar & tajam) + judul besar, dirender Remotion.

Cover lama = satu frame di tengah scene pertama. Di situ subtitle sudah terbakar dan wajahnya
belum tentu menghadap kamera. Di sini frame dipilih dengan MENGUKUR (ukuran wajah x ketajaman
Laplacian) dari video SEBELUM teks ditempel, lalu judul digambar di zona yang tidak menutupi wajah.

Gagal di titik mana pun -> pemanggil memakai cover lama; cover tidak pernah menggagalkan render.
"""

import os
import subprocess

KANDIDAT = int(os.getenv("SAMPUL_KANDIDAT", "12"))
MAKS_KATA = 6


def aktif():
    return (os.getenv("COVER") or "desain").strip().lower() not in ("frame", "lama", "off")


def _di_dalam(t, jendela):
    return any(a <= t <= b for a, b in jendela or ())


def detik_kandidat(durasi, hindari=(), jumlah=KANDIDAT):
    """Titik sampel merata, melewati 0,5 dtk pertama/terakhir dan jendela `hindari`."""
    durasi = float(durasi or 0)
    if durasi <= 1.2:
        return [round(max(0.0, durasi / 2), 2)]
    awal, akhir = 0.5, durasi - 0.5
    langkah = (akhir - awal) / max(1, jumlah - 1)
    return [round(t, 2) for i in range(jumlah)
            if not _di_dalam(t := awal + i * langkah, hindari)]


def pilih_frame(video, detik_list, wajah_mod=None):
    """(detik, kotak_wajah) terbaik: skor = luas wajah x ketajaman. Tanpa wajah -> paling tajam."""
    import wajah as _w
    w = wajah_mod or _w
    det = None
    try:
        det = w._detektor()
    except Exception:
        return (detik_list[0] if detik_list else 0.0), None
    lebar_asli = w._ukuran(video)[0]
    skala = lebar_asli / w.KECIL
    terbaik, terbaik_tajam = None, None
    for t in detik_list:
        g = w.frame_abu(video, t)
        if g is None:
            continue
        tajam = w.ketajaman(g)
        if terbaik_tajam is None or tajam > terbaik_tajam[1]:
            terbaik_tajam = (t, tajam, None)
        k = w.cari(g, det)
        if k:
            skor = (k[2] * k[3]) * tajam
            if terbaik is None or skor > terbaik[1]:
                terbaik = (t, skor, tuple(int(round(v * skala)) for v in k))
    pilih = terbaik or terbaik_tajam
    return (pilih[0], pilih[2]) if pilih else ((detik_list[0] if detik_list else 0.0), None)


def judul_sampul(data, kata_kunci=()):
    """(teks, indeks_kata_emas). Dari kartu pembuka (sudah divalidasi) atau judul, maks 6 kata."""
    plan = data.get("motion_plan") if isinstance(data.get("motion_plan"), dict) else {}
    teks = str((plan or {}).get("hook") or data.get("judul") or "").strip()
    kata = [k for k in teks.split() if k][:MAKS_KATA]
    if not kata:
        return "", None
    import caption_dinamis as cd
    kk = {cd.norm(k) for k in kata_kunci or ()}
    emas = next((i for i, k in enumerate(kata) if cd.norm(k) in kk), None)
    if emas is None:
        emas = max(range(len(kata)), key=lambda i: (len(cd.norm(kata[i])), -i))
        if len(cd.norm(kata[emas])) < 4:
            emas = None
    return " ".join(kata), emas


def ambil_frame_jpg(video, detik, out_path, lebar, tinggi):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{detik:.3f}", "-i", str(video),
                        "-frames:v", "1", "-vf", f"scale={lebar}:{tinggi}", "-q:v", "3",
                        str(out_path)], capture_output=True, text=True)
    return out_path if r.returncode == 0 and os.path.exists(out_path) else None
