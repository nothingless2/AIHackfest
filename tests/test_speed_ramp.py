"""Speed ramp (kecepatan klip) untuk mode voice-over AI.

Diukur lewat render nyata, bukan lewat string filter -- lihat test_transition.py
untuk pola yang sama (`_yavg_frame_pertama`) dan alasannya.

Kunci desain yang diuji di sini: `-t {actual_duration}` di build_segment adalah
batas durasi OUTPUT (posisinya SETELAH -i), jadi mengubah speed_factor TIDAK
BOLEH mengubah durasi segmen -- ia hanya mengubah seberapa banyak bahan sumber
yang sempat terpakai untuk mengisi slot waktu yang sama. Itu yang membuat speed
ramp aman disisipkan tanpa merusak invarian "durasi video = durasi audio"
(rencana Fase 2) atau posisi scene/subtitle di timeline gabungan.
"""

import os
import subprocess
import tempfile

import pytest

import auto_render as ar


def _uv_frame_terakhir(path):
    """(U, V) rata-rata frame terakhir -- dipakai membedakan merah vs biru
    tanpa bergantung pada decoding warna eksak.

    Ekstraksi lewat `ffmpeg -sseof` (ffprobe TIDAK mendukung -sseof), lalu
    diukur lewat ffprobe signalstats pada frame yang sudah diekstrak.
    """
    with tempfile.TemporaryDirectory() as d:
        frame = os.path.join(d, "frame.png")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-sseof", "-0.08", "-i", path,
             "-frames:v", "1", frame],
            check=True, capture_output=True,
        )
        hasil = subprocess.run(
            ["ffprobe", "-v", "error", "-f", "lavfi", "-i", f"movie={frame},signalstats",
             "-show_entries", "frame_tags=lavfi.signalstats.UAVG,lavfi.signalstats.VAVG",
             "-of", "csv=p=0"],
            check=True, capture_output=True, text=True,
        )
    u, v = hasil.stdout.strip().splitlines()[0].split(",")
    return float(u), float(v)


def _durasi(path):
    hasil = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        check=True, capture_output=True, text=True,
    )
    return float(hasil.stdout.strip())


@pytest.fixture(scope="module")
def sumber_merah_biru(tmp_path_factory):
    """Video 2 detik: 1 detik MERAH lalu 1 detik BIRU. Slot output di bawah ini
    sengaja dibuat 1 detik -- pada speed 1x seharusnya TIDAK PERNAH melihat biru
    (sumbernya baru sampai ke bagian biru setelah detik ke-1)."""
    d = tmp_path_factory.mktemp("src")
    merah, biru, gabung = str(d / "merah.mp4"), str(d / "biru.mp4"), str(d / "gabung.mp4")
    for warna, out in (("red", merah), ("blue", biru)):
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"color=c={warna}:size=64x64:rate=24:duration=1",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", out],
            check=True, capture_output=True,
        )
    daftar = str(d / "list.txt")
    with open(daftar, "w") as f:
        f.write(f"file '{merah}'\nfile '{biru}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", daftar, "-c", "copy", gabung],
        check=True, capture_output=True,
    )
    return gabung


def test_speed_normal_slot_1_detik_tidak_pernah_sampai_ke_biru(sumber_merah_biru, tmp_path):
    seg = str(tmp_path / "seg_1x.mp4")
    ar.build_segment(sumber_merah_biru, 1.0, seg, keep_audio=False, speed_factor=1.0, fade_in=False, fade_out=False)
    u, v = _uv_frame_terakhir(seg)
    assert v > 200, "frame terakhir semestinya MASIH merah (V tinggi) di speed normal"


def test_kontrol_positif_speed_2x_menampilkan_bahan_lebih_jauh(sumber_merah_biru, tmp_path):
    """Kontrol positif: pada speed 2x, slot 1 detik yang sama harus sempat
    menampilkan bagian BIRU (detik ke-2 sumber) -- membuktikan setpts benar-benar
    mempercepat, bukan cuma dipasang tanpa efek."""
    seg = str(tmp_path / "seg_2x.mp4")
    ar.build_segment(sumber_merah_biru, 1.0, seg, keep_audio=False, speed_factor=2.0, fade_in=False, fade_out=False)
    u, v = _uv_frame_terakhir(seg)
    assert u > 200, "frame terakhir semestinya sudah BIRU (U tinggi) di speed 2x"


@pytest.mark.parametrize("faktor", [0.5, 1.0, 1.5, 2.0])
def test_durasi_output_TIDAK_berubah_oleh_speed_factor(sumber_merah_biru, tmp_path, faktor):
    """Invarian yang wajib dijaga: slot di timeline gabungan (durasi segmen)
    sama sekali tidak boleh bergeser gara-gara speed ramp -- kalau bergeser,
    posisi scene/subtitle sesudahnya ikut melenceng."""
    seg = str(tmp_path / f"seg_{faktor}.mp4")
    ar.build_segment(sumber_merah_biru, 1.2, seg, keep_audio=False, speed_factor=faktor, fade_in=False, fade_out=False)
    assert _durasi(seg) == pytest.approx(1.2, abs=0.06)


def test_gambar_tidak_terpengaruh_speed_factor(tmp_path_factory, tmp_path):
    """speed_factor tidak masuk akal untuk bahan gambar (bukan video) -- pemanggil
    boleh mengirimnya, tapi tidak boleh membuat render gagal atau berubah."""
    gambar = str(tmp_path_factory.mktemp("img") / "foto.png")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=green:size=64x64:rate=24:duration=1", "-frames:v", "1", gambar],
        check=True, capture_output=True,
    )
    seg = str(tmp_path / "seg_img.mp4")
    ar.build_segment(gambar, 1.0, seg, keep_audio=False, speed_factor=2.0, fade_in=False, fade_out=False)
    assert _durasi(seg) == pytest.approx(1.0, abs=0.06)


def test_resolve_speed_factor_hanya_dipanggil_di_cabang_voice_over_ai():
    """Penjaga struktural lewat sumber, bukan tebakan: resolve_speed_factor()
    HANYA boleh muncul satu kali di render_from_agent_script, dan itu harus
    berada SETELAH percabangan `else:` (mode voice-over AI) -- bukan di cabang
    `if pakai_audio_asli:` (audio asli/mute, yang orangnya masih terlihat
    bicara di layar). Kalau pemanggilan ini pernah pindah/ditambah ke cabang
    audio asli, test ini pecah duluan sebelum sempat merusak sinkronisasi
    subtitle di produksi."""
    import inspect

    src = inspect.getsource(ar.render_from_agent_script)
    baris_panggilan = [
        i for i, baris in enumerate(src.splitlines())
        if "resolve_speed_factor()" in baris and not baris.strip().startswith("#")
    ]
    assert len(baris_panggilan) == 1, (
        f"resolve_speed_factor() dipanggil {len(baris_panggilan)}x di luar komentar, harusnya 1x"
    )

    batas_else = src.index("\n    else:\n")
    titik_panggil = src.index("resolve_speed_factor()", src.index("faktor_kecepatan ="))
    assert titik_panggil > batas_else, (
        "resolve_speed_factor() dipanggil sebelum cabang else (voice-over AI) -- "
        "berisiko ikut aktif di mode audio asli/mute."
    )
