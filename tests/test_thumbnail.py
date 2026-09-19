"""Cover video: pemilihan frame, batas Telegram, dan pelampirannya ke sendVideo.

Batas yang diuji di sini BUKAN tebakan — dikutip dari dokumentasi Bot API resmi
(https://core.telegram.org/bots/api):

    "The thumbnail should be in JPEG format and less than 200 kB in size.
     A thumbnail's width and height should not exceed 320."

Test ekstraksi memanggil ffmpeg sungguhan: yang perlu dibuktikan adalah filenya
benar-benar jadi dengan ukuran yang benar, bukan bahwa kita menyusun daftar
argumen yang terlihat masuk akal.
"""

import os
import subprocess

import pytest

import common
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


# ---------- batas Telegram ----------

@pytest.mark.parametrize("w,h", [(1080, 1920), (1080, 1080), (1920, 1080)])
def test_versi_kecil_memenuhi_batas_bot_api(tmp_path_factory, tmp_path, w, h):
    """Ketiga rasio kanvas harus muat: <=320 px KEDUA sisinya dan <200 kB."""
    vid = _buat_video(tmp_path_factory.mktemp("v") / "v.mp4", w, h, detik=1)
    besar = str(tmp_path / "besar.jpg")
    thumbnail.extract_thumbnail(vid, besar, 0.5)

    kecil = thumbnail.telegram_thumb(besar, str(tmp_path / "kecil.jpg"))

    assert kecil is not None
    lebar, tinggi = _ukuran(kecil)
    assert lebar <= thumbnail.THUMB_MAX_SIDE and tinggi <= thumbnail.THUMB_MAX_SIDE
    assert os.path.getsize(kecil) < thumbnail.THUMB_MAX_BYTES
    # Rasio dipertahankan: yang diperkecil adalah sisi terpanjang.
    assert abs((lebar / tinggi) - (w / h)) < 0.02


def test_artefak_besar_tidak_ikut_berubah(video_9_16, tmp_path):
    besar = str(tmp_path / "besar.jpg")
    thumbnail.extract_thumbnail(video_9_16, besar, 1.0)
    thumbnail.telegram_thumb(besar, str(tmp_path / "kecil.jpg"))
    assert _ukuran(besar) == (540, 960), "JPG besar tetap disimpan utuh sebagai artefak"


def test_sumber_hilang_mengembalikan_none(tmp_path):
    assert thumbnail.telegram_thumb(str(tmp_path / "x.jpg"), str(tmp_path / "y.jpg")) is None


# ---------- pelampiran ke sendVideo ----------

class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.text = "" if status == 200 else '{"description":"THUMB_INVALID"}'


@pytest.fixture
def telegram_hidup(monkeypatch):
    monkeypatch.setattr(common, "TELEGRAM_BOT_TOKEN", "123:TOKEN-PALSU")
    panggilan = []

    def fake_post(url, data=None, files=None, timeout=None):
        # Isi file dibaca DI SINI, saat handle masih terbuka — sesudah send_video
        # selesai, file sementaranya sudah dihapus.
        rekam = {"data": dict(data or {}), "fields": sorted((files or {}).keys())}
        if files and "thumbnail" in files:
            rekam["thumb_bytes"] = files["thumbnail"].read()
        panggilan.append(rekam)
        return _Resp(fake_post.status)

    fake_post.status = 200
    monkeypatch.setattr(common.requests, "post", fake_post)
    return panggilan, fake_post


def test_thumbnail_dikirim_sebagai_file_multipart(tmp_path, telegram_hidup, video_9_16):
    """Bot API: thumbnail DIABAIKAN kalau tidak diunggah lewat multipart/form-data."""
    panggilan, _ = telegram_hidup
    besar = str(tmp_path / "cover.jpg")
    thumbnail.extract_thumbnail(video_9_16, besar, 1.0)

    assert common.send_video("cap", video_9_16, chat_id="777", thumb_path=besar) is True
    assert panggilan[0]["fields"] == ["thumbnail", "video"]
    assert "thumbnail" not in panggilan[0]["data"], "harus file, bukan string di data="


def test_yang_dikirim_versi_kecil_bukan_artefak_besar(tmp_path, telegram_hidup, video_9_16):
    panggilan, _ = telegram_hidup
    besar = str(tmp_path / "cover.jpg")
    thumbnail.extract_thumbnail(video_9_16, besar, 1.0)

    common.send_video("cap", video_9_16, chat_id="777", thumb_path=besar)

    assert len(panggilan[0]["thumb_bytes"]) < thumbnail.THUMB_MAX_BYTES
    assert len(panggilan[0]["thumb_bytes"]) < os.path.getsize(besar)


def test_cover_ditolak_video_tetap_terkirim(tmp_path, monkeypatch, video_9_16):
    """Videonya jauh lebih berharga daripada covernya: request yang ditolak karena
    cover harus diulang tanpa cover, bukan dilaporkan gagal."""
    monkeypatch.setattr(common, "TELEGRAM_BOT_TOKEN", "123:TOKEN-PALSU")
    besar = str(tmp_path / "cover.jpg")
    thumbnail.extract_thumbnail(video_9_16, besar, 1.0)

    panggilan = []
    status = iter([400, 200])

    def post(url, data=None, files=None, timeout=None):
        panggilan.append(sorted((files or {}).keys()))
        return _Resp(next(status))

    monkeypatch.setattr(common.requests, "post", post)

    assert common.send_video("cap", video_9_16, chat_id="777", thumb_path=besar) is True
    assert panggilan == [["thumbnail", "video"], ["video"]]


def test_tanpa_thumb_path_perilaku_lama(telegram_hidup, video_9_16):
    panggilan, _ = telegram_hidup
    assert common.send_video("cap", video_9_16, chat_id="777") is True
    assert panggilan[0]["fields"] == ["video"]


def test_thumb_path_tidak_ada_tetap_kirim(telegram_hidup, video_9_16, tmp_path):
    panggilan, _ = telegram_hidup
    ok = common.send_video("cap", video_9_16, chat_id="777",
                           thumb_path=str(tmp_path / "tidak-ada.jpg"))
    assert ok is True
    assert panggilan[0]["fields"] == ["video"]


def test_tidak_meninggalkan_file_sementara(tmp_path, telegram_hidup, video_9_16):
    import glob
    import tempfile
    panggilan, _ = telegram_hidup
    besar = str(tmp_path / "cover.jpg")
    thumbnail.extract_thumbnail(video_9_16, besar, 1.0)

    sebelum = set(glob.glob(os.path.join(tempfile.gettempdir(), "tg_thumb_*")))
    common.send_video("cap", video_9_16, chat_id="777", thumb_path=besar)
    sesudah = set(glob.glob(os.path.join(tempfile.gettempdir(), "tg_thumb_*")))
    assert sesudah == sebelum
