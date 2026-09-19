"""Rasio kanvas dan fit mode.

Memperbaiki bug laten: versi lama memakai
    scale=-2:{H}:force_original_aspect_ratio=increase
yang hanya menyebut SATU dimensi. `increase` tidak punya pembanding lebar, jadi
untuk sumber yang lebih sempit dari kanvas hasilnya lebih kecil dari lebar target
dan crop gagal. Tidak pernah terlihat selama kanvas selalu 9:16.
"""

import subprocess

import pytest

import auto_render as ar
from canvas import ASPECT_PRESETS, FIT_MODES, CanvasError, resolve_canvas


# ---------- validasi ----------

def test_ketiga_rasio_dikenal():
    for a in ("9:16", "1:1", "16:9"):
        w, h, _ = resolve_canvas(a, "crop")
        assert (w, h) == ASPECT_PRESETS[a]


def test_rasio_tak_dikenal_ditolak():
    """Jangan diam-diam jatuh ke default — video berbentuk lain dari yang diminta
    lebih buruk daripada penolakan yang jelas."""
    with pytest.raises(CanvasError, match="tidak dikenal"):
        resolve_canvas("4:3", "crop")


def test_fit_mode_tak_dikenal_ditolak():
    with pytest.raises(CanvasError, match="tidak dikenal"):
        resolve_canvas("9:16", "zoom")


def test_pesan_error_menyebut_pilihan_yang_ada():
    try:
        resolve_canvas("21:9", "crop")
    except CanvasError as e:
        for a in ASPECT_PRESETS:
            assert a in str(e)


def test_default_tetap_9_16_crop():
    """Perilaku lama tidak boleh berubah untuk user yang tidak minta apa-apa."""
    w, h, f = resolve_canvas()
    assert (w, h, f) == (1080, 1920, "crop")


# ---------- bentuk filter ----------

def test_crop_menyebut_KEDUA_dimensi():
    """Inti perbaikan bug: satu dimensi saja membuat crop gagal untuk sumber
    yang lebih sempit dari kanvas."""
    vf = ar.scale_crop_filter(1920, 1080, "crop")
    assert "scale=1920:1080:force_original_aspect_ratio=increase" in vf
    assert "scale=-2:" not in vf


def test_letterbox_memakai_pad_bukan_crop():
    vf = ar.scale_crop_filter(1080, 1080, "letterbox")
    assert "force_original_aspect_ratio=decrease" in vf
    assert "pad=1080:1080" in vf
    assert "crop=" not in vf


def test_blur_mengecilkan_dulu_sebelum_memblur():
    """gblur berbiaya kuadratik terhadap luas; memblur 1080x1920 langsung jauh
    lebih mahal daripada memblur versi kecil lalu meregangkannya."""
    vf = ar.scale_crop_filter(1080, 1920, "blur")
    posisi_kecil = vf.index(f"scale={ar.BLUR_SMALL_W}:")
    posisi_blur = vf.index("gblur=")
    assert posisi_kecil < posisi_blur, "blur harus SETELAH pengecilan"


def test_blur_mempertahankan_seluruh_frame():
    vf = ar.scale_crop_filter(1080, 1920, "blur")
    assert "force_original_aspect_ratio=decrease" in vf, "frame depan tidak boleh dipotong"
    assert "overlay=" in vf


def test_semua_kombinasi_menghasilkan_filter():
    for a in ASPECT_PRESETS:
        for f in FIT_MODES:
            w, h, _ = resolve_canvas(a, f)
            assert ar.scale_crop_filter(w, h, f)


# ---------- render nyata ----------

def _ukuran(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    return out.stdout.strip()


@pytest.mark.parametrize("aspect", list(ASPECT_PRESETS))
@pytest.mark.parametrize("fit", FIT_MODES)
def test_output_berukuran_persis_kanvas(tmp_path, aspect, fit):
    """Sumber PORTRAIT ke kanvas LANDSCAPE adalah kasus yang dulu rusak."""
    src = tmp_path / "portrait.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc2=size=1080x1920:duration=1:rate=1", "-frames:v", "1", str(src)],
        check=True)

    w, h, f = resolve_canvas(aspect, fit)
    out = tmp_path / "out.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(src), "-t", "1",
         "-vf", ar.scale_crop_filter(w, h, f),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(out)],
        check=True)

    assert _ukuran(out) == f"{w},{h}"


# ---------- teks mengikuti kanvas ----------

def test_ukuran_font_menyesuaikan_tiap_rasio():
    ukuran = {a: ar.subtitle_geometry(*ASPECT_PRESETS[a])[0] for a in ASPECT_PRESETS}
    assert ukuran["9:16"] > ukuran["16:9"], "kanvas lebih tinggi -> font lebih besar"


def test_teks_tetap_di_area_aman_semua_rasio():
    for a, (w, h) in ASPECT_PRESETS.items():
        fs, y = ar.subtitle_geometry(w, h)
        blok = ar.SUBTITLE_MAX_LINES * fs * 1.25
        assert y >= 0, a
        assert y + blok <= h - h * ar.SAFE_BOTTOM_RATIO + 1, a
