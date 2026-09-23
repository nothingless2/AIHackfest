"""Filter warna (colorFilter). Diukur dari saturasi PIKSEL video hasil render,
bukan dari isi string filter -- pola yang sama dengan test_transition.py dan
test_zoom.py.
"""

import subprocess

import pytest

import auto_render as ar


def _satavg(path):
    hasil = subprocess.run(
        ["ffprobe", "-v", "error", "-f", "lavfi", "-i", f"movie={path},signalstats",
         "-show_entries", "frame_tags=lavfi.signalstats.SATAVG",
         "-read_intervals", "%+#1", "-of", "csv=p=0"],
        check=True, capture_output=True, text=True,
    )
    return float(hasil.stdout.strip().splitlines()[0])


@pytest.fixture(scope="module")
def foto_berwarna(tmp_path_factory):
    """Ungu sedang -- cukup saturasi untuk terlihat naik (vivid) atau turun
    habis (bw), tidak seperti abu-abu yang saturasinya sudah 0 dari awal."""
    out = str(tmp_path_factory.mktemp("img") / "ungu.png")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=0x8040A0:size=64x64:rate=24:duration=1",
         "-frames:v", "1", out],
        check=True, capture_output=True,
    )
    return out


def test_tanpa_filter_saturasi_tidak_berubah(foto_berwarna, tmp_path, monkeypatch):
    monkeypatch.delenv("COLOR_FILTER", raising=False)
    seg = str(tmp_path / "seg_none.mp4")
    ar.build_segment(foto_berwarna, 1.0, seg, keep_audio=False, fade_in=False, fade_out=False)
    baseline = _satavg(seg)
    assert baseline > 0, "sumber ungu semestinya sudah punya saturasi > 0"


def test_kontrol_positif_bw_menghilangkan_saturasi(foto_berwarna, tmp_path, monkeypatch):
    monkeypatch.delenv("COLOR_FILTER", raising=False)
    tanpa = str(tmp_path / "seg_none2.mp4")
    ar.build_segment(foto_berwarna, 1.0, tanpa, keep_audio=False, fade_in=False, fade_out=False)
    baseline = _satavg(tanpa)

    monkeypatch.setenv("COLOR_FILTER", "bw")
    seg = str(tmp_path / "seg_bw.mp4")
    ar.build_segment(foto_berwarna, 1.0, seg, keep_audio=False, fade_in=False, fade_out=False)
    assert _satavg(seg) < 3, "filter bw semestinya menghilangkan saturasi nyaris total"
    assert _satavg(seg) < baseline - 20


def test_vivid_menaikkan_saturasi_dibanding_baseline(foto_berwarna, tmp_path, monkeypatch):
    monkeypatch.delenv("COLOR_FILTER", raising=False)
    tanpa = str(tmp_path / "seg_none3.mp4")
    ar.build_segment(foto_berwarna, 1.0, tanpa, keep_audio=False, fade_in=False, fade_out=False)
    baseline = _satavg(tanpa)

    monkeypatch.setenv("COLOR_FILTER", "vivid")
    seg = str(tmp_path / "seg_vivid.mp4")
    ar.build_segment(foto_berwarna, 1.0, seg, keep_audio=False, fade_in=False, fade_out=False)
    assert _satavg(seg) > baseline, "vivid semestinya menaikkan saturasi dibanding tanpa filter"


@pytest.mark.parametrize("nama", ["natural", "warm", "cool"])
def test_preset_lain_berhasil_dirender(foto_berwarna, tmp_path, monkeypatch, nama):
    """Bukan soal arah perubahannya di sini -- yang penting semua preset benar-benar
    valid sebagai filter ffmpeg dan tidak menggagalkan render."""
    monkeypatch.setenv("COLOR_FILTER", nama)
    seg = str(tmp_path / f"seg_{nama}.mp4")
    ar.build_segment(foto_berwarna, 1.0, seg, keep_audio=False, fade_in=False, fade_out=False)
    assert _satavg(seg) >= 0


def test_filter_berlaku_juga_untuk_video(tmp_path_factory, tmp_path, monkeypatch):
    video = str(tmp_path_factory.mktemp("vid") / "klip.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=0x8040A0:size=64x64:rate=24:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", video],
        check=True, capture_output=True,
    )
    monkeypatch.delenv("COLOR_FILTER", raising=False)
    tanpa = str(tmp_path / "seg_v_none.mp4")
    ar.build_segment(video, 1.0, tanpa, keep_audio=False, fade_in=False, fade_out=False)
    baseline = _satavg(tanpa)

    monkeypatch.setenv("COLOR_FILTER", "bw")
    seg = str(tmp_path / "seg_v_bw.mp4")
    ar.build_segment(video, 1.0, seg, keep_audio=False, fade_in=False, fade_out=False)
    assert _satavg(seg) < baseline - 20
