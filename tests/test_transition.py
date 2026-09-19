"""Transisi antar klip.

Dipilih "fade lewat hitam", BUKAN xfade. Alasannya terukur:
    hard cut : 8,02 dtk durasi
    xfade    : 7,67 dtk  <- MENYUSUT karena klip tumpang tindih
    fade     : 8,06 dtk  <- utuh

xfade memendekkan video sebesar durasi overlap di TIAP sambungan. Dengan 7
sambungan itu ~2,8 detik pergeseran, dan semua subtitle sesudahnya melenceng --
padahal sinkronisasi itu baru dibangun lewat map_time().
"""

import subprocess

import pytest

import auto_render as ar


def test_transisi_bisa_dimatikan(monkeypatch):
    monkeypatch.setattr(ar, "TRANSITION", "none")
    assert ar.fade_filters(4.0, keep_audio=True) == ("", "")


def test_klip_normal_dapat_fade_video_dan_audio():
    vf, af = ar.fade_filters(4.0, keep_audio=True)
    assert "fade=t=in:st=0" in vf and "fade=t=out" in vf
    assert "afade=t=in" in af and "afade=t=out" in af


def test_tanpa_audio_hanya_fade_video():
    """Mode voice-over AI: segmen tidak punya audio sendiri."""
    vf, af = ar.fade_filters(4.0, keep_audio=False)
    assert vf and af == ""


def test_fade_out_dimulai_sebelum_klip_habis():
    vf, _ = ar.fade_filters(4.0, keep_audio=False)
    mulai = float(vf.split("fade=t=out:st=")[1].split(":")[0])
    durasi = float(vf.split("fade=t=out:")[1].split("d=")[1])
    assert mulai + durasi == pytest.approx(4.0, abs=0.01)


def test_klip_pendek_fade_ikut_diperpendek():
    """Fade 0,2 dtk pada klip 0,5 dtk membuat klip nyaris tidak pernah terang."""
    vf, _ = ar.fade_filters(0.5, keep_audio=False)
    d = float(vf.split("fade=t=in:st=0:d=")[1].split(",")[0])
    assert d <= 0.5 / 3 + 0.001


def test_klip_sangat_pendek_dilewati():
    assert ar.fade_filters(0.1, keep_audio=True) == ("", "")


def test_fade_audio_jauh_lebih_pendek_dari_video():
    """0,2 dtk pada audio akan memotong suku kata; tujuannya cuma cegah bunyi klik."""
    vf, af = ar.fade_filters(4.0, keep_audio=True)
    dv = float(vf.split("fade=t=in:st=0:d=")[1].split(",")[0])
    da = float(af.split("afade=t=in:st=0:d=")[1].split(",")[0])
    assert da < dv


def test_fade_tidak_pernah_melebihi_sepertiga_durasi():
    for d in [0.2, 0.5, 1.0, 3.0, 10.0]:
        vf, _ = ar.fade_filters(d, keep_audio=False)
        if not vf:
            continue
        dur = float(vf.split("fade=t=in:st=0:d=")[1].split(",")[0])
        assert dur <= d / 3 + 0.001, f"durasi {d}"


def test_filter_fade_masuk_ke_rantai_segmen(monkeypatch):
    """Penjaga: fade yang tidak ikut ke perintah ffmpeg tidak menghasilkan apa pun."""
    terlihat = {}

    def fake(args, konteks):
        terlihat["args"] = args

    monkeypatch.setattr(ar, "run_ffmpeg", fake)
    ar.build_segment("/x/klip.mp4", 4.0, "/x/out.mp4", keep_audio=True)

    gabung = " ".join(terlihat["args"])
    assert "fade=t=in" in gabung, "fade video harus ikut terkirim"
    assert "afade=t=in" in gabung, "fade audio harus ikut terkirim"


# ---------- segmen pertama tidak boleh dibuka dari hitam ----------

def test_segmen_pertama_tanpa_fade_masuk():
    """Frame 0 hitam bukan cuma soal selera: Telegram MENGABAIKAN thumbnail yang
    kita kirim dan membuat sendiri dari frame pertama, jadi preview tiap video
    jadi hitam polos. Diverifikasi lewat pengiriman nyata (field `thumbnail`
    maupun bentuk `attach://` sama-sama diabaikan)."""
    vf, _ = ar.fade_filters(4.0, keep_audio=True, fade_in=False)
    assert "fade=t=in" not in vf
    assert "fade=t=out" in vf, "fade keluar tetap ada, yang dibuang hanya fade masuk"


def test_fade_audio_tetap_ada_di_segmen_pertama():
    """Audio boleh tetap fade masuk — itu mencegah 'klik' dan tidak menyentuh frame."""
    _, af = ar.fade_filters(4.0, keep_audio=True, fade_in=False)
    assert "afade=t=in" in af


def test_klip_berikutnya_tetap_fade_masuk():
    vf, _ = ar.fade_filters(4.0, keep_audio=True, fade_in=True)
    assert "fade=t=in:st=0" in vf


# ---------- penjaga regresi: frame 0 diukur, bukan diperiksa lewat string filter ----------

def _yavg_frame_pertama(path):
    """Kecerahan rata-rata (luma) frame pertama, diukur ffmpeg signalstats.

    Rentang video: 16 = hitam penuh, 235 = putih penuh.
    """
    hasil = subprocess.run(
        ["ffprobe", "-v", "error", "-f", "lavfi", "-i", f"movie={path},signalstats",
         "-show_entries", "frame_tags=lavfi.signalstats.YAVG",
         "-read_intervals", "%+0.1", "-of", "csv=p=0"],
        check=True, capture_output=True, text=True,
    )
    return float(hasil.stdout.strip().splitlines()[0])


@pytest.fixture(scope="module")
def sumber_terang(tmp_path_factory):
    """Klip abu-abu terang 2 detik — kalau frame 0 gelap, itu ulah fade, bukan bahannya."""
    out = str(tmp_path_factory.mktemp("src") / "terang.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=gray:size=320x568:rate=24:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", out],
        check=True, capture_output=True,
    )
    return out


def test_frame_pertama_video_TIDAK_gelap(sumber_terang, tmp_path):
    """Regresi yang dijaga: frame 0 hitam membuat Telegram menampilkan preview hitam
    polos, karena ia mengabaikan cover kita dan memakai frame pertama video."""
    seg = str(tmp_path / "seg0.mp4")
    ar.build_segment(sumber_terang, 2.0, seg, keep_audio=False, fade_in=False)
    assert _yavg_frame_pertama(seg) > 60, "frame pertama tidak boleh gelap"


def test_kontrol_positif_fade_masuk_memang_menggelapkan(sumber_terang, tmp_path):
    """Kontrol positif untuk test di atas: dengan fade masuk, alat ukur yang sama
    HARUS melihat frame 0 gelap. Tanpa ini, 'terang' bisa saja berarti
    pengukurannya yang tidak bekerja."""
    seg = str(tmp_path / "seg_fade.mp4")
    ar.build_segment(sumber_terang, 2.0, seg, keep_audio=False, fade_in=True)
    assert _yavg_frame_pertama(seg) < 30, "kontrol positif gagal: alat ukur tidak mendeteksi fade"


# ---------- aturan: hard cut di dalam klip, fade hanya di sambungan yang pantas ----------

def test_potongan_di_dalam_klip_selalu_hard_cut():
    """Keluhan nyata user: 17 kedipan hitam dalam 68,9 detik. Sebabnya fade
    dipasang per segmen, sedangkan pemotongan jeda memecah SATU klip jadi
    beberapa segmen — jadi tiap jeda yang dibuang berkedip hitam di tengah
    kalimat."""
    rencana = [{"path": "a", "ranges": [(0, 3), (4, 6), (7, 9)], "asli": 9.0}]
    assert ar.sambungan_audio_asli(rencana) == [False, False]


def test_pergantian_klip_dengan_jeda_panjang_dapat_fade():
    rencana = [{"path": "a", "ranges": [(0, 5)], "asli": 8.0},   # 3 dtk dibuang di ekor
               {"path": "b", "ranges": [(0, 4)], "asli": 4.0}]
    assert ar.sambungan_audio_asli(rencana) == [True]


def test_pergantian_klip_tanpa_jeda_tetap_hard_cut():
    """Ucapan mengalir terus melewati sambungan — fade di situ terbaca sebagai
    kerusakan, bukan transisi."""
    rencana = [{"path": "a", "ranges": [(0, 5)], "asli": 5.0},
               {"path": "b", "ranges": [(0, 4)], "asli": 4.0}]
    assert ar.sambungan_audio_asli(rencana) == [False]


def test_satu_sambungan_mematikan_dua_ujung():
    """Kalau cuma salah satu ujung dimatikan, layar tetap berkedip di situ."""
    bendera = ar.fade_flags([False, True], 3)
    assert bendera[0] == (False, False), "segmen pertama: tanpa fade masuk"
    assert bendera[1] == (False, True), "hard cut di kiri, fade di kanan"
    assert bendera[2] == (True, True), "fade masuk, lalu penutup video"


def test_segmen_terakhir_selalu_menutup_dengan_fade():
    assert ar.fade_flags([], 1) == [(False, True)]


def test_mode_ai_hard_cut_di_tengah_kalimat():
    """Tanpa jeda bicara yang bisa diukur, yang dipakai teksnya: sambungan di
    tengah satu scene berarti kalimatnya masih berjalan."""
    scenes = [{"start": 0, "end": 6, "text": "satu kalimat panjang"}]
    assert ar.sambungan_scene([3.0, 3.0], scenes) == [False]


def test_mode_ai_fade_di_pergantian_scene():
    scenes = [{"start": 0, "end": 3, "text": "gagasan satu"},
              {"start": 3, "end": 6, "text": "gagasan dua"}]
    assert ar.sambungan_scene([3.0, 3.0], scenes) == [True]


def test_tanpa_data_scene_perilaku_lama_dipertahankan():
    assert ar.sambungan_scene([2.0, 2.0, 2.0], []) == [True, True]


def test_sambungan_hard_cut_TIDAK_menghasilkan_frame_hitam(sumber_terang, tmp_path):
    """Diukur, bukan dipercaya dari daftar argumen: rakit dua segmen dengan
    sambungan hard cut, lalu pastikan tidak ada frame gelap di sambungannya."""
    segmen = []
    for i, (f_in, f_out) in enumerate(ar.fade_flags([False], 2)):
        p = str(tmp_path / f"seg{i}.mp4")
        ar.build_segment(sumber_terang, 2.0, p, keep_audio=False,
                         fade_in=f_in, fade_out=f_out)
        segmen.append(p)
    gabung = str(tmp_path / "gabung.mp4")
    ar.concat_segments(segmen, gabung, str(tmp_path))

    hasil = subprocess.run(
        ["ffprobe", "-v", "error", "-f", "lavfi", "-i", f"movie={gabung},signalstats",
         "-show_entries", "frame_tags=lavfi.signalstats.YAVG", "-of", "csv=p=0"],
        check=True, capture_output=True, text=True)
    nilai = [float(x) for x in hasil.stdout.split() if x.strip()]
    # 0,5 detik di sekitar sambungan (detik ke-2 dari total 4 detik)
    sekitar = nilai[int(1.75 * ar.FPS):int(2.25 * ar.FPS)]
    assert sekitar, "tidak ada frame di sekitar sambungan"
    assert min(sekitar) > 60, f"masih ada frame gelap di sambungan: {min(sekitar)}"
