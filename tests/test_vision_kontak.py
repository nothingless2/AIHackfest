"""BrainIdea 'menonton' klip: lembar kontak 2x2 (4 momen berurutan) per video + fakta per
bahan di prompt. Asal: brief 24 Sep hanya melihat frame detik ke-1 tiap klip, lalu naskahnya
mendeskripsikan satu gambar diam."""

import base64
import subprocess

import numpy as np
import pytest

import agent1_2_brief as brief
import vision

WARNA = ["red", "lime", "blue", "white"]
RGB = {"red": (255, 0, 0), "lime": (0, 255, 0), "blue": (0, 0, 255), "white": (255, 255, 255),
       "magenta": (255, 0, 255)}


def _video_warna(path, warna, detik_per=1.0):
    """Video berurutan: satu warna polos per potongan -- isi tiap momen diketahui pasti."""
    args = ["ffmpeg", "-y", "-v", "error"]
    for w in warna:
        args += ["-f", "lavfi", "-i", f"color=c={w}:size=320x568:rate=10:duration={detik_per}"]
    graf = "".join(f"[{i}:v]" for i in range(len(warna))) + f"concat=n={len(warna)}:v=1:a=0"
    args += ["-filter_complex", graf, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True, capture_output=True)
    return str(path)


def _decode(part):
    data = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", "-"], input=data, capture_output=True)
    w, h = (int(x) for x in probe.stdout.decode().strip().split(","))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         input=data, capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(int)


def _kuadran(img):
    h, w, _ = img.shape
    pusat = [(h // 4, w // 4), (h // 4, 3 * w // 4), (3 * h // 4, w // 4), (3 * h // 4, 3 * w // 4)]
    hasil = []
    for y, x in pusat:
        px = img[y - 5:y + 5, x - 5:x + 5].reshape(-1, 3).mean(axis=0)
        hasil.append(min(RGB, key=lambda k: sum((a - b) ** 2 for a, b in zip(RGB[k], px))))
    return hasil


def test_lembar_kontak_berisi_4_momen_berurutan(tmp_path):
    p = _video_warna(tmp_path / "v.mp4", WARNA)
    (part,) = vision.build_image_parts([p], kontak=True)
    img = _decode(part)
    assert _kuadran(img) == WARNA, "kiri-atas -> kanan-bawah harus urutan waktu"
    assert max(img.shape[:2]) <= vision.VISION_MAX_DIM


def test_tanpa_kontak_tetap_satu_frame_lama(tmp_path):
    """Kontrol: jalur lama (agent5) tidak berubah -- satu frame, satu warna."""
    p = _video_warna(tmp_path / "v.mp4", WARNA)
    img = _decode(vision.build_image_parts([p])[0])
    assert len(set(_kuadran(img))) == 1


def test_bagian_goyang_dilewati(tmp_path):
    """Detik 1-3 (magenta) ditandai buruk: tidak boleh muncul di lembar kontak.
    Kontrol positif: tanpa daftar buruk, magenta MUNCUL."""
    p = _video_warna(tmp_path / "v.mp4", ["red", "magenta", "magenta", "blue"])
    tanpa = _kuadran(_decode(vision.build_image_parts([p], kontak=True)[0]))
    assert "magenta" in tanpa
    dengan = _kuadran(_decode(vision.build_image_parts(
        [p], kontak=True, buruk={p: [(1.0, 3.0, "goyang")]})[0]))
    assert "magenta" not in dengan
    assert dengan[0] == "red" and dengan[-1] == "blue"


def test_titik_kontak_merata_dan_melompati_buruk():
    assert vision.titik_kontak(8.0) == pytest.approx([1, 3, 5, 7], abs=0.05)
    t = vision.titik_kontak(8.0, [(2.0, 6.0, "oleng")])
    assert all(not (2.0 <= x < 6.0) for x in t)
    # seluruh klip buruk -> tetap 4 titik (lebih baik melihat sesuatu)
    assert len(vision.titik_kontak(4.0, [(0, 4, "goyang")])) == 4


def test_kontak_gagal_jatuh_ke_satu_frame(tmp_path, monkeypatch, capsys):
    p = _video_warna(tmp_path / "v.mp4", WARNA)
    monkeypatch.setattr(vision, "_kontak_bytes", lambda *a, **k: None)
    assert len(vision.build_image_parts([p], kontak=True)) == 1
    assert "lembar kontak gagal" in capsys.readouterr().out


def test_foto_tidak_dijadikan_kontak(tmp_path, monkeypatch):
    foto = tmp_path / "f.jpg"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:size=64x64",
                    "-frames:v", "1", str(foto)], check=True)
    monkeypatch.setattr(vision, "_kontak_bytes", lambda *a, **k: pytest.fail("foto bukan video"))
    assert len(vision.build_image_parts([str(foto)], kontak=True)) == 1


# ------------------------------------------------------------------ fakta per bahan

def test_fakta_per_bahan():
    nama = ["a.mp4", "b.mp4", "c.mp4", "d.jpg"]
    paths = ["/x/a.mp4", "/x/b.mp4", "/x/c.mp4", "/x/d.jpg"]
    teks = brief.build_klip_note(
        nama, paths, {"a.mp4": "halo teman teman ini acara kami"},
        {"b.mp4": "tanpa_ucapan", "c.mp4": "kuota_habis"},
        {"/x/b.mp4": [(2.0, 3.5, "goyang")]},
        {"/x/a.mp4": 5.0, "/x/b.mp4": 8.2, "/x/c.mp4": 3.0})
    baris = teks.splitlines()[1:]
    assert baris[0].startswith("1. VIDEO 5.0 dtk") and "halo teman teman" in baris[0]
    assert "tanpa ucapan" in baris[1] and "detik 2.0-3.5 goyang" in baris[1]
    # GAGAL transkripsi bukan 'tanpa ucapan' (aturan #7)
    assert "tidak diketahui" in baris[2] and "tanpa ucapan" not in baris[2]
    assert baris[3] == "4. FOTO."


def test_cuplikan_ucapan_dibatasi():
    panjang = " ".join(["kata"] * 100)
    teks = brief.build_klip_note(["a.mp4"], ["/a.mp4"], {"a.mp4": panjang}, {}, {}, {"/a.mp4": 9})
    assert teks.count("kata") == brief.MAKS_KATA_CUPLIKAN and "..." in teks


def test_contoh_gaya_dimuat_tanpa_komentar():
    teks = brief.contoh_gaya()
    assert "Kalian lihat" in teks
    assert "# Sumber" not in teks, "baris komentar tidak boleh masuk prompt"


def test_prompt_memuat_fakta_gaya_dan_kontak():
    p = brief.build_prompt(["a.mp4"], {}, jumlah_gambar=1, klip_fakta="FAKTA PER BAHAN: X",
                           gaya_contoh="CONTOH CARA BICARA: Y", kontak=True)
    assert "FAKTA PER BAHAN: X" in p and "CONTOH CARA BICARA: Y" in p
    assert "lembar 2x2" in p and "pemahaman_bahan" in p
