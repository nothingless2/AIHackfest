"""Musik latar + auto-ducking.

Ducking diuji dengan MENGUKUR, bukan dengan mencocokkan string filter. Caranya:
musik dibuat nada 200 Hz dan "ucapan" nada 2000 Hz, jadi keduanya bisa
dipisahkan lagi dari hasil campuran memakai lowpass. Level musik lalu diukur
pada dua jendela waktu: saat ada ucapan dan saat sunyi.

Test kontrol ikut disertakan: dengan sidechain dimatikan, alat ukur yang sama
HARUS melihat level musik yang hampir sama di kedua jendela. Tanpa kontrol itu,
angka "musik turun" tidak bisa dibedakan dari alat ukur yang rusak.
"""

import os
import re
import subprocess

import pytest

import music as m
import auto_render as ar


def _jalankan(args):
    return subprocess.run(["ffmpeg", "-y", "-v", "error", *args],
                          check=True, capture_output=True, text=True)


def _level_musik(path, mulai, panjang):
    """mean_volume (dB) dari komponen frekuensi rendah = musik saja."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-ss", str(mulai), "-t", str(panjang), "-i", str(path),
         "-af", "lowpass=f=400,volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m_ = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", out.stderr)
    assert m_, f"volumedetect tidak mengembalikan mean_volume:\n{out.stderr[-500:]}"
    return float(m_.group(1))


@pytest.fixture(scope="module")
def bahan(tmp_path_factory):
    """Pabrik video uji: 'ucapan' 2000 Hz di detik 2-4 dan 8-10, sunyi selebihnya.
    Musik: nada 200 Hz konstan, jadi keduanya bisa dipisah lagi dengan filter.

    Amplitudo ucapan bisa dipilih. Itu penting: dengan setelan bawaan ffmpeg,
    ducking terlihat bekerja untuk suara keras tapi praktis mati untuk suara
    pelan -- dan suara pelan justru yang umum di rekaman ponsel.
    """
    d = tmp_path_factory.mktemp("musik")
    track = str(d / "lofi_tenang.mp3")
    _jalankan(["-f", "lavfi", "-i", "sine=frequency=200:duration=20", track])

    dibuat = {}

    def buat(amp=0.5):
        if amp in dibuat:
            return dibuat[amp], track, d
        video = str(d / f"video_{amp}.mp4")
        ucapan = (f"sine=frequency=2000:duration=12,"
                  f"volume='{amp}*(between(t,2,4)+between(t,8,10))':eval=frame")
        _jalankan(["-f", "lavfi", "-i", "color=c=gray:size=240x426:rate=24:duration=12",
                   "-f", "lavfi", "-i", ucapan,
                   # -ac 2 sejak awal: pipeline memang menghasilkan stereo, dan
                   # membandingkan sumber MONO dengan hasil STEREO memberi selisih
                   # -3,01 dB yang tidak ada hubungannya dengan musik
                   # (libswresample membagi tiap kanal dengan akar 2 untuk
                   # menjaga daya total). Itu sempat terbaca seolah
                   # "musik menenggelamkan ucapan".
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                   "-ac", "2", "-shortest", video])
        dibuat[amp] = video
        return video, track, d

    return buat


# ---------- pemilihan track ----------

def test_folder_kosong_bukan_error(tmp_path):
    """Tanpa track, video tetap dibuat — musik itu hiasan."""
    assert m.pick_track(folder=str(tmp_path)) is None


def test_mood_tidak_ketemu_ditolak_bukan_diganti(bahan):
    """Musik salah suasana lebih buruk daripada tanpa musik."""
    _, _, d = bahan()
    with pytest.raises(m.MusicError):
        m.pick_track("dangdut", folder=str(d))


def test_mood_cocok_substring_nama_berkas(bahan):
    _, track, d = bahan()
    assert m.pick_track("lofi", folder=str(d)) == track
    assert m.pick_track("TENANG", folder=str(d)) == track


def test_pemilihan_deterministik_per_run(bahan):
    _, _, d = bahan()
    assert m.pick_track(folder=str(d), run_id="abc") == m.pick_track(folder=str(d), run_id="abc")


@pytest.mark.parametrize("nilai,harap", [("off", False), ("0", False), ("tanpa", False),
                                         ("on", True), ("ya", True)])
def test_permintaan_per_run_menang(monkeypatch, nilai, harap):
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC", nilai)
    assert m.music_wanted() is harap


def test_level_menyesuaikan_loudness_video(monkeypatch):
    """Gain TETAP tidak bisa benar untuk dua hal sekaligus.

    Terukur pada material nyata: dengan gain tetap 0,15 musik hanya mengubah
    level berkas 0,2-0,5 dB -- tidak terdengar sama sekali. Karena itu level
    musik dihitung dari loudness video ini.
    """
    ukur = {"video.mp4": -25.0, "track.mp3": -35.0}
    monkeypatch.setattr(m, "loudness", lambda p: ukur[os.path.basename(p)])

    pelan = m.auto_volume("video.mp4", "track.mp3")

    ukur["video.mp4"] = -15.0  # video yang JAUH lebih keras
    keras = m.auto_volume("video.mp4", "track.mp3")

    assert keras > pelan, "video lebih keras harus dapat musik lebih keras juga"


def test_level_musik_di_bawah_ucapan(monkeypatch):
    """Musik latar, bukan musik utama."""
    monkeypatch.setattr(m, "loudness",
                        lambda p: -25.0 if p.endswith(".mp4") else -25.0)
    # track dan ucapan sama kerasnya -> gain harus MENURUNKAN musik
    assert m.auto_volume("v.mp4", "t.mp3") < 1.0


def test_gagal_mengukur_jatuh_ke_nilai_aman(monkeypatch):
    monkeypatch.setattr(m, "loudness", lambda p: None)
    assert m.auto_volume("v.mp4", "t.mp3") == m.MUSIC_VOLUME


def test_ada_limiter_agar_campuran_tidak_clip():
    """Terukur: ucapan 0 dBFS + musik = melewati skala penuh, ter-clip, dan
    ucapannya justru turun 3 dB. Limiter menahan puncaknya secara halus."""
    f = m.build_filter(10)
    assert "alimiter" in f and "level=disabled" in f


def test_amix_tidak_menormalkan():
    """normalize=1 (default ffmpeg) membagi tiap input dengan jumlah input, jadi
    suara asli video ikut turun separuh hanya karena musik ditambahkan."""
    assert "normalize=0" in m.build_filter(10)


def test_tanpa_trek_audio_musik_tetap_dipasang_tanpa_sidechain():
    f = m.build_filter(10, punya_ucapan=False)
    assert "sidechaincompress" not in f and "[aout]" in f


# ---------- ducking yang diukur ----------

@pytest.mark.parametrize("amp,label", [(0.5, "-6 dBFS"), (0.2, "-14 dBFS")])
def test_musik_mengecil_saat_ada_ucapan(bahan, tmp_path, amp, label):
    """Ucapan pelan ikut diuji, dan itu bukan formalitas: dengan level_sc bawaan
    ffmpeg, ucapan -14 dBFS hanya menurunkan musik 0,4 dB — ducking-nya praktis
    tidak ada, padahal versi suara kerasnya terlihat baik-baik saja."""
    video, track, _ = bahan(amp)
    hasil = str(tmp_path / "campur.mp4")
    ar.tambah_musik(video, track, hasil, 12.0)

    saat_bicara = _level_musik(hasil, 2.5, 1.2)
    saat_sunyi = _level_musik(hasil, 5.5, 1.5)

    assert saat_sunyi - saat_bicara > 8, (
        f"musik tidak ter-duck untuk ucapan {label}: saat bicara "
        f"{saat_bicara:.1f} dB, saat sunyi {saat_sunyi:.1f} dB")


def test_kontrol_positif_tanpa_sidechain_level_tetap_sama(bahan, tmp_path, monkeypatch):
    """Kontrol untuk test di atas: tanpa ducking, alat ukur yang sama harus
    melihat level musik yang hampir sama di kedua jendela."""
    video, track, _ = bahan()
    hasil = str(tmp_path / "tanpa_duck.mp4")
    monkeypatch.setattr(
        ar, "music_filter",
        lambda durasi, punya_ucapan=True, volume=None: (
            # Kontrol yang BENAR: musik + ucapan tetap dicampur dengan gain yang
            # sama, yang dibuang hanya sidechain-nya. Versi pertama test ini
            # memakai punya_ucapan=False, yang ternyata membuang audio video
            # sama sekali -- yang dibandingkan jadi bukan hal yang sama.
            m.build_filter(durasi, volume=volume)
            .replace(f"sidechaincompress=threshold={m.MUSIC_DUCK_THRESHOLD}:"
                     f"ratio={m.MUSIC_DUCK_RATIO}",
                     f"sidechaincompress=threshold={m.MUSIC_DUCK_THRESHOLD}:ratio=1")))
    ar.tambah_musik(video, track, hasil, 12.0)

    selisih = _level_musik(hasil, 5.5, 1.5) - _level_musik(hasil, 2.5, 1.2)
    assert abs(selisih) < 3, f"tanpa sidechain seharusnya rata, selisih {selisih:.1f} dB"


def test_suara_asli_video_tidak_ikut_tenggelam(bahan, tmp_path):
    """Musik boleh terdengar, tapi ucapan tetap yang utama."""
    video, track, _ = bahan()
    hasil = str(tmp_path / "campur.mp4")
    ar.tambah_musik(video, track, hasil, 12.0)

    def level_ucapan(path):
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", "2.5", "-t", "1.2", "-i", str(path),
             "-af", "highpass=f=1000,volumedetect", "-f", "null", "-"],
            capture_output=True, text=True)
        return float(re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", out.stderr).group(1))

    assert level_ucapan(hasil) - level_ucapan(video) > -1.0, "ucapan asli ikut turun"


def test_konversi_kanal_bukan_musik_yang_menurunkan_level():
    """Penjaga dari salah tuduh: kalau suatu saat angka level turun lagi,
    periksa layout kanalnya dulu, bukan musiknya."""
    assert ar.AUDIO_CHANNELS == "2"


def test_durasi_tidak_memanjang_mengikuti_musik(bahan, tmp_path):
    """Track 20 detik, video 12 detik — hasilnya harus tetap 12 detik."""
    video, track, _ = bahan()
    hasil = str(tmp_path / "campur.mp4")
    ar.tambah_musik(video, track, hasil, 12.0)

    def durasi(p):
        return float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(p)], check=True, capture_output=True, text=True).stdout)

    assert durasi(hasil) == pytest.approx(durasi(video), abs=0.3)
