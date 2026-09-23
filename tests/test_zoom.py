"""Zoom otomatis (Ken Burns) untuk bahan GAMBAR.

BUG LAMA (commit 21 Sep): ekspresi zoompan memakai variabel `time`, yang TIDAK
ADA di zoompan -- hasilnya zoom selalu diam di 1.0, tidak pernah membesar, dan
tidak ada test yang akan menangkapnya karena tidak pernah benar-benar diukur
dari video hasil render. Test di sini mengukur PIKSEL video hasil render,
bukan isi string filter -- pola yang sama dengan test_transition.py.
"""

import os
import subprocess
import tempfile

import pytest

import auto_render as ar


def _yv_strip_kiri(path, *, di_akhir=False):
    """(Y, SAT) rata-rata dari strip 4px paling kiri satu frame -- dipakai
    membedakan 'masih kena border merah' (Y tinggi, SAT tinggi) dari 'sudah
    ter-crop ke interior hitam' (Y rendah, SAT ~0).

    Ekstraksi frame lewat `ffmpeg -sseof` (ffprobe TIDAK mendukung -sseof),
    baru diukur lewat ffprobe signalstats pada frame yang sudah diekstrak --
    dua langkah, bukan satu panggilan movie= gabungan.
    """
    with tempfile.TemporaryDirectory() as d:
        frame = os.path.join(d, "frame.png")
        extract = ["ffmpeg", "-v", "error"]
        if di_akhir:
            extract += ["-sseof", "-0.08"]
        extract += ["-i", path, "-frames:v", "1", frame]
        subprocess.run(extract, check=True, capture_output=True)

        hasil = subprocess.run(
            ["ffprobe", "-v", "error", "-f", "lavfi",
             "-i", f"movie={frame},crop={_STRIP_W}:ih:0:0,signalstats",
             "-show_entries", "frame_tags=lavfi.signalstats.YAVG,lavfi.signalstats.SATAVG",
             "-of", "csv=p=0"],
            check=True, capture_output=True, text=True,
        )
    y, sat = hasil.stdout.strip().splitlines()[0].split(",")
    return float(y), float(sat)



# scale_crop_filter() default-nya memuat ke TARGET_W x TARGET_H (1080x1920).
# Sumber uji dibuat dengan RASIO YANG SAMA PERSIS (9:16), supaya
# scale(...increase)+crop tidak ikut memotong pinggirannya -- kalau rasionya
# beda, crop-ke-kanvas sendiri sudah memakan sebagian bingkai SEBELUM zoom
# sempat berperan, dan pengukuran strip kiri jadi tidak berarti apa-apa.
_SRC_W, _SRC_H = ar.TARGET_W // 4, ar.TARGET_H // 4   # 270x480, rasio sama, ringan
_BORDER_SRC = 10          # -> ~40px di kanvas akhir (1080 lebar) setelah scale 4x
_STRIP_W = 15              # jauh di bawah border akhir (~40px) dan di bawah offset
                           # zoom (~125px di ZOOM_MAX_FACTOR=1.3, lihat test di bawah)


@pytest.fixture(scope="module")
def foto_berbingkai(tmp_path_factory):
    """Gambar rasio 9:16: bingkai MERAH, interior HITAM. Kalau frame di-crop
    lebih ke tengah (zoom membesar), strip paling kiri berpindah dari bingkai
    merah ke interior hitam."""
    out = str(tmp_path_factory.mktemp("img") / "foto.png")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=red:size={_SRC_W}x{_SRC_H}:rate=24:duration=1",
         "-vf", f"drawbox=x=0:y=0:w={_SRC_W}:h={_SRC_H}:color=red:t=fill,"
                f"drawbox=x={_BORDER_SRC}:y={_BORDER_SRC}:"
                f"w={_SRC_W - 2 * _BORDER_SRC}:h={_SRC_H - 2 * _BORDER_SRC}:color=black:t=fill",
         "-frames:v", "1", out],
        check=True, capture_output=True,
    )
    return out


def test_tanpa_auto_zoom_bingkai_tetap_terlihat_di_akhir(foto_berbingkai, tmp_path, monkeypatch):
    """Baseline/kontrol negatif: TANPA zoom, strip kiri harus tetap menampilkan
    bingkai merah di frame terakhir seperti di frame pertama -- tidak ada yang
    diam-diam bergeser."""
    monkeypatch.delenv("AUTO_ZOOM", raising=False)
    seg = str(tmp_path / "seg_nozoom.mp4")
    ar.build_segment(foto_berbingkai, 1.5, seg, keep_audio=False, fade_in=False, fade_out=False)
    y0, sat0 = _yv_strip_kiri(seg)
    y1, sat1 = _yv_strip_kiri(seg, di_akhir=True)
    assert sat0 > 60, "frame pertama semestinya menampilkan bingkai merah"
    assert sat1 > 60, "tanpa zoom, bingkai merah semestinya masih terlihat di akhir"
    assert abs(sat1 - sat0) < 20, "tanpa zoom, saturasi strip tidak boleh berubah banyak"


def test_kontrol_positif_auto_zoom_bingkai_hilang_di_akhir(foto_berbingkai, tmp_path, monkeypatch):
    """Kontrol positif: DENGAN zoom, di akhir klip crop sudah bergeser ke
    interior hitam -- bingkai merah di strip kiri harus HILANG. Faktor zoom
    dilebihkan (bukan nilai produksi ZOOM_MAX_FACTOR) supaya perbedaannya besar
    dan tidak rapuh terhadap tuning nilai produksi di masa depan."""
    monkeypatch.setenv("AUTO_ZOOM", "1")
    monkeypatch.setattr(ar, "ZOOM_MAX_FACTOR", 1.3)
    seg = str(tmp_path / "seg_zoom.mp4")
    ar.build_segment(foto_berbingkai, 1.5, seg, keep_audio=False, fade_in=False, fade_out=False)

    y0, sat0 = _yv_strip_kiri(seg)
    y1, sat1 = _yv_strip_kiri(seg, di_akhir=True)
    assert sat0 > 60, "frame pertama semestinya masih menampilkan bingkai (zoom mulai dari 1.0)"
    assert sat1 < 10, "frame terakhir semestinya sudah ter-crop ke interior hitam"
    assert y1 < y0 - 30, "kecerahan strip kiri harus turun jelas dari bingkai merah ke interior hitam"


def test_auto_zoom_tidak_berlaku_untuk_video(tmp_path_factory, tmp_path, monkeypatch):
    """Zoom HANYA untuk gambar -- video yang sudah bergerak tidak disentuh sama
    sekali (lihat build_segment: `zoom=is_image and auto_zoom_enabled()`)."""
    monkeypatch.setenv("AUTO_ZOOM", "1")
    monkeypatch.setattr(ar, "ZOOM_MAX_FACTOR", 1.3)
    video = str(tmp_path_factory.mktemp("vid") / "klip.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=red:size={_SRC_W}x{_SRC_H}:rate=24:duration=1",
         "-vf", f"drawbox=x=0:y=0:w={_SRC_W}:h={_SRC_H}:color=red:t=fill,"
                f"drawbox=x={_BORDER_SRC}:y={_BORDER_SRC}:"
                f"w={_SRC_W - 2 * _BORDER_SRC}:h={_SRC_H - 2 * _BORDER_SRC}:color=black:t=fill",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", video],
        check=True, capture_output=True,
    )
    seg = str(tmp_path / "seg_video_zoom.mp4")
    ar.build_segment(video, 1.5, seg, keep_audio=False, fade_in=False, fade_out=False)
    y1, sat1 = _yv_strip_kiri(seg, di_akhir=True)
    assert sat1 > 60, "video tidak boleh ikut di-zoom walau AUTO_ZOOM menyala"


def test_durasi_dan_frame_count_tidak_berubah_oleh_zoom(foto_berbingkai, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_ZOOM", "1")
    seg = str(tmp_path / "seg_durasi.mp4")
    ar.build_segment(foto_berbingkai, 1.5, seg, keep_audio=False, fade_in=False, fade_out=False)
    hasil = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", seg],
        check=True, capture_output=True, text=True,
    )
    assert float(hasil.stdout.strip()) == pytest.approx(1.5, abs=0.06)
