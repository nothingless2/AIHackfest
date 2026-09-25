"""Pemeriksa mutu (scripts/qa_video.py): tiap pemeriksaan diuji dengan cacat sintetis yang
posisinya diketahui, DAN kontrol bersih yang harus lolos (aturan #1)."""

import os
import subprocess

import pytest

import qa_video as qa

W, H = 216, 384


def _ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True, capture_output=True)


def _video(path, detik=6, volume_db=-16, sumber="testsrc2", suara=True):
    pemisah = ":" if "=" in sumber else "="
    args = ["-f", "lavfi", "-i", f"{sumber}{pemisah}size={W}x{H}:rate=24:duration={detik}"]
    if suara:
        args += ["-f", "lavfi", "-i", f"anoisesrc=d={detik}:c=pink:a=0.5", "-af",
                 f"loudnorm=I={volume_db}:TP=-2", "-ar", "44100", "-c:a", "aac", "-shortest"]
    _ff(*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path))
    return str(path)


def test_suara_pelan_diperbaiki_otomatis(tmp_path):
    v = _video(tmp_path / "pelan.mp4", volume_db=-32)
    sebelum = qa.ukur_loudness(v)["lufs"]
    hasil = qa.periksa(v)
    assert sebelum < -28, "kontrol: sumber memang pelan"
    assert hasil["diperbaiki"] and hasil["lolos"]
    assert qa.ukur_loudness(v)["lufs"] == pytest.approx(qa.TARGET_LUFS, abs=1.5)


def test_suara_normal_tidak_disentuh(tmp_path):
    v = _video(tmp_path / "normal.mp4", volume_db=-16)
    mtime = os.path.getmtime(v)
    hasil = qa.periksa(v)
    assert hasil["lolos"] and hasil["diperbaiki"] == [] and os.path.getmtime(v) == mtime


def test_video_bisu_padahal_harus_bersuara(tmp_path):
    v = _video(tmp_path / "bisu.mp4", suara=False)
    assert "TIDAK bersuara" in qa.periksa(v)["masalah"][0]
    assert qa.periksa(v, harus_bersuara=False)["lolos"], "mode bisu tanpa musik memang tanpa suara"


def _sambung(tmp_path, *bagian):
    daftar = tmp_path / "d.txt"
    daftar.write_text("".join(f"file '{p}'\n" for p in bagian))
    out = tmp_path / "gabung.mp4"
    _ff("-f", "concat", "-safe", "0", "-i", str(daftar), "-c", "copy", str(out))
    return str(out)


def test_frame_hitam_di_tengah_terdeteksi(tmp_path):
    a = _video(tmp_path / "a.mp4", detik=3, suara=False)
    hitam = tmp_path / "h.mp4"
    _ff("-f", "lavfi", "-i", f"color=c=black:size={W}x{H}:rate=24:duration=1", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(hitam))
    b = _video(tmp_path / "b.mp4", detik=3, suara=False)
    hasil = qa.periksa(_sambung(tmp_path, a, str(hitam), b), harus_bersuara=False)
    assert any("frame hitam di 3.0-4.0" in m for m in hasil["masalah"]), hasil
    assert qa.periksa(a, harus_bersuara=False)["lolos"], "kontrol bersih"


def test_gambar_beku_terdeteksi_kecuali_bahan_foto(tmp_path):
    beku = tmp_path / "beku.mp4"
    _ff("-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=24:duration=6", "-vf",
        "tpad=stop_mode=clone:stop_duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(beku))
    hasil = qa.periksa(str(beku), harus_bersuara=False)
    assert any("gambar beku" in m for m in hasil["masalah"]), hasil
    assert not any("beku" in m for m in qa.periksa(str(beku), harus_bersuara=False, ada_foto=True)["masalah"])


# Dasar abu-abu polos = diam total (seperti foto), jadi uji zona memakai ada_foto=True supaya
# pemeriksaan "gambar beku" tidak ikut menilai.
def _dengan_teks(dasar, out, x, y, teks="TEKS PANJANG SEKALI"):
    _ff("-i", dasar, "-vf", f"drawtext=text='{teks}':x={x}:y={y}:fontsize=26:fontcolor=white:"
        "box=1:boxcolor=black@0.9", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out))
    return str(out)


def test_teks_terpotong_di_tepi_dan_zona_ui(tmp_path):
    dasar = _video(tmp_path / "dasar.mp4", sumber="color=c=gray", suara=False)
    tengah = _dengan_teks(dasar, tmp_path / "t.mp4", "(w-text_w)/2", "h*0.35", "OKE")
    tepi = _dengan_teks(dasar, tmp_path / "p.mp4", "w-text_w*0.6", "h*0.35")
    tombol = _dengan_teks(dasar, tmp_path / "k.mp4", "w*0.80", "h*0.6", "SUKA")
    bawah = _dengan_teks(dasar, tmp_path / "b.mp4", "(w-text_w)/2", "h*0.93", "OKE")
    assert qa.periksa(tengah, dasar=dasar, harus_bersuara=False, ada_foto=True)["lolos"], "kontrol: teks aman"
    assert any("terpotong" in m for m in qa.periksa(tepi, dasar=dasar, harus_bersuara=False, ada_foto=True)["masalah"])
    assert any("tombol kanan" in p for p in qa.periksa(tombol, dasar=dasar, harus_bersuara=False, ada_foto=True)["peringatan"])
    assert any("caption bawah" in p for p in qa.periksa(bawah, dasar=dasar, harus_bersuara=False, ada_foto=True)["peringatan"])


def test_jendela_cutaway_dilewati(tmp_path):
    dasar = _video(tmp_path / "dasar.mp4", sumber="color=c=gray", suara=False)
    tepi = _dengan_teks(dasar, tmp_path / "p.mp4", "w-text_w*0.6", "h*0.35")
    assert qa.periksa(tepi, dasar=dasar, harus_bersuara=False, ada_foto=True, lewati=[(0, 6)])["lolos"]


def test_peredup_selebar_layar_bukan_teks_terpotong(tmp_path):
    """Salah tanda pertama di render nyata 25 Sep: lapisan gelap kartu pembuka (selebar layar)
    terbaca 'teks terpotong'. Yang dihitung hanya piksel yang jadi LEBIH TERANG."""
    dasar = _video(tmp_path / "dasar.mp4", sumber="color=c=gray", suara=False)
    redup = tmp_path / "r.mp4"
    _ff("-i", dasar, "-vf", "drawbox=x=0:y=0:w=iw:h=ih*0.55:color=black@0.6:t=fill", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-an", str(redup))
    assert qa.periksa(str(redup), dasar=dasar, harus_bersuara=False, ada_foto=True)["lolos"]


def test_geser_satu_frame_antar_encode_bukan_overlay(tmp_path):
    """Salah tanda kedua di render nyata 25 Sep: cuplikan video akhir & dasar meleset 1 frame,
    gerakan di tepi (tangan) terbaca 'teks terpotong'. Video akhir = dasar yang digeser 1 frame,
    TANPA overlay apa pun -> harus lolos."""
    dasar = tmp_path / "dasar.mp4"
    _ff("-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=24:duration=5", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(dasar))
    geser = tmp_path / "geser.mp4"
    _ff("-i", str(dasar), "-vf", "trim=start_frame=1,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(geser))
    assert qa.periksa(str(geser), dasar=str(dasar), harus_bersuara=False, ada_foto=True)["lolos"]
