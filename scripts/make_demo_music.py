"""Buat satu track ambient sederhana untuk MENCOBA fitur musik latar.

Ini BUKAN pengganti musik sungguhan. Isinya beberapa nada sinus pelan dengan
tremolo dan lowpass -- cukup untuk membuktikan pipeline musik + ducking bekerja
tanpa menyeret berkas berhak cipta ke dalam repo.

Untuk konten yang dipublikasikan, taruh musik berlisensi sendiri di
assets/music/ (lihat README di folder itu).

Pakai:
    python3 scripts/make_demo_music.py [durasi_detik]
"""

import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(PROJECT_ROOT, "assets", "music")
# Nama berkas menyebut nuansanya karena pencocokan mood memakai nama berkas,
# dan diawali "demo_" supaya jelas ini bukan musik produksi.
OUT = os.path.join(OUT_DIR, "demo_lofi_tenang.mp3")

# Akor minor sederhana (A3-C4-E4) + bass, semuanya pelan.
NADA = [(220.0, 0.18), (261.63, 0.14), (329.63, 0.12), (110.0, 0.10)]


def main(durasi=60):
    os.makedirs(OUT_DIR, exist_ok=True)
    masukan, filter_bagian = [], []
    for i, (freq, vol) in enumerate(NADA):
        masukan += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={durasi}"]
        # tremolo pelan supaya tidak terdengar seperti nada uji yang mati
        filter_bagian.append(
            f"[{i}:a]volume={vol},tremolo=f={3.5 + i * 0.7}:d=0.35[n{i}]")
    gabung = "".join(f"[n{i}]" for i in range(len(NADA)))
    filter_kompleks = (
        ";".join(filter_bagian)
        + f";{gabung}amix=inputs={len(NADA)}:normalize=0,"
        f"lowpass=f=1200,afade=t=in:st=0:d=3,afade=t=out:st={durasi - 3}:d=3[out]"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *masukan,
         "-filter_complex", filter_kompleks, "-map", "[out]",
         "-c:a", "libmp3lame", "-b:a", "128k", OUT],
        check=True,
    )
    print(f"Track demo dibuat: {OUT} ({durasi} detik)")
    print("Ini contoh untuk mencoba saja — taruh musik berlisensimu sendiri di "
          f"{OUT_DIR} untuk konten yang dipublikasikan.")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 60)
