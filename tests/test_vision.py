"""Vision: bahan user dikirim sebagai GAMBAR, bukan nama file.

Sebelumnya model hanya menerima UUID di nama file, jadi mustahil menghasilkan
konten yang nyambung dengan bahan. Test ini memakai gambar/video sintetis yang
dibuat ffmpeg di tmp_path — tidak menyentuh bahan user.
"""

import base64
import subprocess

import pytest

import vision


def buat_gambar(path, w=1600, h=900):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc2=size={w}x{h}:duration=1:rate=1", "-frames:v", "1", str(path)],
        check=True,
    )
    return str(path)


def buat_video(path, detik=3):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc2=size=640x360:duration={detik}:rate=10",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return str(path)


def jpeg_dari(part):
    url = part["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    return base64.b64decode(url.split(",", 1)[1])


def test_gambar_jadi_data_url_jpeg_valid(tmp_path):
    p = buat_gambar(tmp_path / "a.jpg")
    parts = vision.build_image_parts([p])

    assert len(parts) == 1
    data = jpeg_dari(parts[0])
    assert data[:3] == b"\xff\xd8\xff", "harus JPEG valid"


def test_gambar_besar_diperkecil(tmp_path):
    """Biaya token gambar naik bersama ukurannya; resolusi penuh tidak menambah
    kemampuan model menilai 'ini foto apa'."""
    p = buat_gambar(tmp_path / "besar.jpg", w=3000, h=2000)
    data = jpeg_dari(vision.build_image_parts([p])[0])

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", "-"],
        input=data, capture_output=True,
    )
    w, h = (int(x) for x in out.stdout.decode().strip().split(","))
    assert max(w, h) <= vision.VISION_MAX_DIM


def test_gambar_kecil_tidak_diperbesar(tmp_path):
    p = buat_gambar(tmp_path / "kecil.jpg", w=320, h=240)
    data = jpeg_dari(vision.build_image_parts([p])[0])

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", "-"],
        input=data, capture_output=True,
    )
    w, h = (int(x) for x in out.stdout.decode().strip().split(","))
    assert (w, h) == (320, 240), "jangan memperbesar, itu hanya menambah biaya"


def test_video_diambil_satu_frame(tmp_path):
    p = buat_video(tmp_path / "klip.mp4")
    parts = vision.build_image_parts([p])

    assert len(parts) == 1
    assert jpeg_dari(parts[0])[:3] == b"\xff\xd8\xff"


def test_video_sangat_pendek_tetap_dapat_frame(tmp_path):
    """Frame diambil di detik ke-1; video <1 detik harus jatuh ke percobaan dari awal."""
    p = buat_video(tmp_path / "pendek.mp4", detik=1)
    assert len(vision.build_image_parts([p])) == 1


def test_ekstensi_tidak_didukung_dilewati(tmp_path, capsys):
    f = tmp_path / "catatan.txt"
    f.write_text("bukan media")

    assert vision.build_image_parts([str(f)]) == []
    assert "[warn]" in capsys.readouterr().out


def test_file_rusak_dilewati_tanpa_menggagalkan_sisanya(tmp_path, capsys):
    """Lebih baik brief dibuat dari 1 dari 2 gambar daripada pipeline mati total."""
    rusak = tmp_path / "rusak.jpg"
    rusak.write_bytes(b"ini bukan jpeg sama sekali")
    bagus = buat_gambar(tmp_path / "bagus.jpg")

    parts = vision.build_image_parts([str(rusak), bagus])

    assert len(parts) == 1, "yang bagus harus tetap lolos"
    assert "[warn]" in capsys.readouterr().out


def test_file_hilang_dilewati(tmp_path):
    assert vision.build_image_parts([str(tmp_path / "tidak-ada.jpg")]) == []


def test_jumlah_bahan_dibatasi(tmp_path, capsys):
    paths = [buat_gambar(tmp_path / f"{i}.jpg", 320, 240) for i in range(5)]

    parts = vision.build_image_parts(paths, max_assets=2)

    assert len(parts) == 2
    assert "2 pertama" in capsys.readouterr().out


def test_daftar_kosong_aman(tmp_path):
    assert vision.build_image_parts([]) == []
