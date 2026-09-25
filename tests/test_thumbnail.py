"""Cover video: pemilihan frame dan ekstraksi.

Test ekstraksi memanggil ffmpeg sungguhan: yang perlu dibuktikan adalah filenya
benar-benar jadi dengan ukuran yang benar, bukan bahwa kita menyusun daftar
argumen yang terlihat masuk akal.
"""

import subprocess

import pytest

import thumbnail


def _buat_video(path, w, h, detik=2):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=24:duration={detik}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return str(path)


def _ukuran(path):
    hasil = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        check=True, capture_output=True, text=True,
    )
    w, h = hasil.stdout.strip().split("x")
    return int(w), int(h)


# ---------- pemilihan frame ----------

def test_ambil_tengah_scene_pertama():
    """Di titik itu teks hook sudah terbakar ke frame — itulah gunanya."""
    scenes = [{"start": 1.0, "end": 3.0}, {"start": 3.0, "end": 6.0}]
    assert thumbnail.thumbnail_time(scenes, 20) == 2.0


def test_tanpa_scene_ambil_tengah_video():
    assert thumbnail.thumbnail_time([], 20) == 10.0


def test_scene_rusak_dilewati():
    scenes = [{"start": 5.0, "end": 5.0}, {"start": None, "end": 2.0},
              {"start": 2.0, "end": 4.0}]
    assert thumbnail.thumbnail_time(scenes, 20) == 3.0


def test_dijepit_ke_durasi_nyata():
    """Scene yang melewati akhir video menghasilkan frame kosong kalau tidak dijepit."""
    assert thumbnail.thumbnail_time([{"start": 30.0, "end": 40.0}], 10) == 9.9


def test_tidak_pernah_negatif():
    assert thumbnail.thumbnail_time([], 0) == 0.0


# ---------- ekstraksi nyata ----------

@pytest.fixture(scope="module")
def video_9_16(tmp_path_factory):
    return _buat_video(tmp_path_factory.mktemp("vid") / "v.mp4", 540, 960)


def test_cover_berukuran_kanvas_penuh(video_9_16, tmp_path):
    out = str(tmp_path / "cover.jpg")
    assert thumbnail.extract_thumbnail(video_9_16, out, 1.0) == out
    assert _ukuran(out) == (540, 960), "artefak disimpan seukuran kanvas, bukan dikecilkan"


def test_cover_benar_benar_jpeg(video_9_16, tmp_path):
    out = str(tmp_path / "cover.jpg")
    thumbnail.extract_thumbnail(video_9_16, out, 0.5)
    with open(out, "rb") as f:
        assert f.read(2) == b"\xff\xd8", "Bot API menuntut JPEG"


def test_video_tidak_ada_mengembalikan_none(tmp_path):
    """Cover bersifat tambahan — ketiadaannya tidak boleh melempar."""
    assert thumbnail.extract_thumbnail(str(tmp_path / "hilang.mp4"),
                                       str(tmp_path / "x.jpg"), 1.0) is None
