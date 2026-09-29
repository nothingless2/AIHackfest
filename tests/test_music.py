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
        # x8: sumber `sine` ffmpeg beramplitudo 1/8 (-18 dBFS). Tanpa itu "ucapan -14 dBFS"
        # sebenarnya -32 dBFS -- jauh di bawah ucapan ponsel nyata (29 Sep), dan tes ini dulu
        # mendorong ducking yang menghilangkan musik di video sungguhan.
        ucapan = (f"sine=frequency=2000:duration=12,"
                  f"volume='{amp * 8}*(between(t,2,4)+between(t,8,10))':eval=frame")
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

@pytest.mark.parametrize("amp,label", [(0.5, "-6 dBFS"), (0.125, "-18 dBFS")])
def test_musik_mengecil_saat_ada_ucapan_tapi_tidak_hilang(bahan, tmp_path, amp, label):
    """Ucapan pelan ikut diuji: dengan level_sc bawaan ffmpeg, ucapan pelan praktis tidak
    menekan musik. Sejak 29 Sep level_sc dinormalkan ke kenyaringan ucapan, jadi tekanannya
    SAMA untuk ucapan keras & pelan. Seberapa terdengar musiknya diuji dengan ucapan
    sungguhan (test_musik_terdengar_di_bawah_ucapan_nyata): nada datar tidak bisa menangkap
    kasus "musik tidak ada" (setelan lama & baru sama-sama ±18 dB pada nada)."""
    video, track, _ = bahan(amp)
    hasil = str(tmp_path / "campur.mp4")
    ar.tambah_musik(video, track, hasil, 12.0)

    saat_bicara = _level_musik(hasil, 2.5, 1.2)
    saat_sunyi = _level_musik(hasil, 5.5, 1.5)

    assert 5 < saat_sunyi - saat_bicara < 20, (
        f"ducking di luar 5-20 dB untuk ucapan {label}: saat bicara "
        f"{saat_bicara:.1f} dB, saat sunyi {saat_sunyi:.1f} dB")


def test_kontrol_positif_tanpa_sidechain_level_tetap_sama(bahan, tmp_path, monkeypatch):
    """Kontrol untuk test di atas: tanpa ducking, alat ukur yang sama harus
    melihat level musik yang hampir sama di kedua jendela."""
    video, track, _ = bahan()
    hasil = str(tmp_path / "tanpa_duck.mp4")
    monkeypatch.setattr(
        ar, "music_filter",
        lambda durasi, punya_ucapan=True, volume=None, level_sc=None: (
            # Kontrol yang BENAR: musik + ucapan tetap dicampur dengan gain yang
            # sama, yang dibuang hanya sidechain-nya. Versi pertama test ini
            # memakai punya_ucapan=False, yang ternyata membuang audio video
            # sama sekali -- yang dibandingkan jadi bukan hal yang sama.
            m.build_filter(durasi, volume=volume, level_sc=level_sc)
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


# ---------- level musik dibanding UCAPAN SUNGGUHAN ----------

UCAPAN_UJI = os.path.join(os.path.dirname(__file__), "data", "ucapan_uji.wav")   # edge-tts, 11,6 dtk, jeda 2,5 dtk


def _pcm16k(args):
    import numpy as np
    out = subprocess.run(["ffmpeg", "-v", "error", *args, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, np.int16).astype(float) / 32768


@pytest.mark.parametrize("lufs", [-24, -16])
def test_musik_terdengar_di_bawah_ucapan_nyata(bahan, tmp_path, lufs):
    """29 Sep, user: "musik tidak ada". Terukur di video user: musik -30 dB di bawah ucapan
    saat bicara. Nada sintetis TIDAK bisa menangkap ini (setelan lama & baru sama-sama ±18 dB
    pada nada datar) -- karena itu diuji dengan ucapan sungguhan: musik setelah ducking
    (filter pipeline apa adanya, keluaran [md]) dibanding ucapan per jendela 400 ms."""
    import numpy as np
    _, track, _ = bahan()
    video = str(tmp_path / "ucapan.mp4")
    _jalankan(["-f", "lavfi", "-i", "color=c=gray:size=240x426:rate=24:duration=11.5", "-i", UCAPAN_UJI,
               "-af", f"loudnorm=I={lufs}:TP=-1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-ac", "2", "-shortest", video])
    f = m.build_filter(11.5, punya_ucapan=True, volume=m.auto_volume(video, track),
                       level_sc=m.sidechain_gain(m.loudness(video))).split("[md];")[0] + "[md]"
    musik = _pcm16k(["-i", video, "-stream_loop", "-1", "-i", track, "-filter_complex", f,
                     "-map", "[md]", "-t", "11.5"])
    ucap = _pcm16k(["-i", video, "-vn"])
    w = 6400
    k = min(len(musik), len(ucap)) // w
    rms = lambda x: np.sqrt((x[:k * w].reshape(k, w) ** 2).mean(1)) + 1e-9   # noqa: E731
    rm, ru = rms(musik), rms(ucap)
    db_u = 20 * np.log10(ru)
    bicara = db_u > np.percentile(db_u, 80) - 15
    bicara[:4] = bicara[-4:] = False                      # fade musik di ujung
    rel = float(np.median(20 * np.log10(rm[bicara] / ru[bicara])))
    assert -21 < rel < -12, f"musik {rel:.1f} dB dari ucapan saat bicara (target -21..-12)"
