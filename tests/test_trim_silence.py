"""Pemotongan jeda diam.

Diukur pada 5 video user: ~6,4 detik dari 35,3 detik adalah jeda (18%), dan
hampir semua klip diawali ruang mati.

Hal paling rawan: memotong jeda MENGGESER semua waktu sesudahnya. Subtitle
dibangun dari timestamp Whisper terhadap audio ASLI, jadi tanpa map_time ia akan
melenceng makin jauh.
"""

import subprocess

import pytest

import trim_silence as ts


# ---------- rentang yang dipertahankan ----------

def test_jeda_di_awal_dipotong():
    kr = ts.keep_ranges(10.0, [(0.0, 2.0)], pad=0.0, min_keep=0.1)
    assert kr == [(2.0, 10.0)]


def test_jeda_di_tengah_memecah_jadi_dua():
    kr = ts.keep_ranges(10.0, [(4.0, 6.0)], pad=0.0, min_keep=0.1)
    assert kr == [(0.0, 4.0), (6.0, 10.0)]


def test_jeda_di_akhir_tanpa_silence_end():
    """silence_start tanpa pasangan berarti jeda sampai ujung file."""
    kr = ts.keep_ranges(10.0, [(8.0, None)], pad=0.0, min_keep=0.1)
    assert kr == [(0.0, 8.0)]


def test_bantalan_mencegah_suku_kata_terpotong():
    """Pemotongan terlalu rapat terdengar seperti tergagap."""
    kr = ts.keep_ranges(10.0, [(4.0, 6.0)], pad=0.2, min_keep=0.1)
    assert kr[0][1] == pytest.approx(4.2)
    assert kr[1][0] == pytest.approx(5.8)


def test_potongan_terlalu_pendek_dibuang():
    kr = ts.keep_ranges(10.0, [(0.2, 9.8)], pad=0.0, min_keep=0.5)
    assert all(b - a >= 0.5 for a, b in kr)


def test_tanpa_jeda_klip_utuh():
    assert ts.keep_ranges(7.0, []) == [(0.0, 7.0)]


def test_seluruh_klip_terdeteksi_diam_tetap_kembalikan_utuh():
    """Lebih baik menyimpan terlalu banyak daripada menghasilkan video kosong."""
    assert ts.keep_ranges(5.0, [(0.0, 5.0)]) == [(0.0, 5.0)]


def test_rentang_tidak_pernah_melewati_durasi():
    for a, b in ts.keep_ranges(5.0, [(4.0, 99.0)], pad=1.0, min_keep=0.1):
        assert 0 <= a <= 5.0 and 0 <= b <= 5.0


# ---------- pemetaan waktu ----------

def test_waktu_sebelum_potongan_tidak_bergeser():
    kr = [(0.0, 4.0), (6.0, 10.0)]
    assert ts.map_time(2.0, kr) == 2.0


def test_waktu_setelah_potongan_maju_sebesar_yang_dibuang():
    """2 detik dibuang di 4-6, jadi detik 7 asli jadi detik 5 di hasil."""
    kr = [(0.0, 4.0), (6.0, 10.0)]
    assert ts.map_time(7.0, kr) == 5.0


def test_waktu_di_dalam_bagian_yang_dipotong_jatuh_ke_batas():
    kr = [(0.0, 4.0), (6.0, 10.0)]
    assert ts.map_time(5.0, kr) == 4.0


def test_pemetaan_monoton_tidak_pernah_mundur():
    """Subtitle yang waktunya mundur akan tampil kacau."""
    kr = [(0.0, 2.0), (3.0, 5.0), (7.0, 9.0)]
    sebelum = -1
    for t in [x / 10 for x in range(0, 95)]:
        kini = ts.map_time(t, kr)
        assert kini >= sebelum
        sebelum = kini


def test_total_dipertahankan_sama_dengan_jumlah_rentang():
    kr = [(0.0, 4.0), (6.0, 10.0)]
    assert ts.total_kept(kr) == 8.0
    assert ts.map_time(10.0, kr) == ts.total_kept(kr)


# ---------- deteksi nyata (ffmpeg) ----------

def test_deteksi_menemukan_jeda_yang_sengaja_dibuat(tmp_path):
    """KONTROL POSITIF. silencedetect menulis di level log `info`; menjalankan
    ffmpeg dengan `-v error` membuat hasilnya HILANG dan mudah disalahartikan
    sebagai 'tidak ada jeda'. Test ini yang menjaga alat ukurnya tetap bekerja."""
    f = tmp_path / "uji.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", str(f)],
        check=True,
    )
    senyap = ts.detect_silence(str(f))
    assert senyap, "jeda 3 detik yang sengaja dibuat harus terdeteksi"
    a, b = senyap[0]
    assert a == pytest.approx(2.0, abs=0.2)
    assert b == pytest.approx(5.0, abs=0.2)


def test_file_tanpa_jeda_tidak_menghasilkan_rentang(tmp_path):
    f = tmp_path / "penuh.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=3", str(f)],
        check=True,
    )
    assert ts.detect_silence(str(f)) == []


def test_file_tidak_ada_tidak_melempar(tmp_path):
    assert ts.detect_silence(str(tmp_path / "hantu.mp4")) == []
