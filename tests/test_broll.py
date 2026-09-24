"""B-roll stok (scripts/broll.py). Jaringan TIDAK pernah disentuh: dua fungsi jaringan
(`_http_get_json`, `_unduh_ke`) diganti. Integrasi merender video nyata dengan ffmpeg dan
mengukur warna piksel pada slot waktu masing-masing (pola test_duration.py)."""

import json
import shutil
import subprocess
import urllib.error

import numpy as np
import pytest

import auto_render as ar
import broll as b


@pytest.fixture(autouse=True)
def _bersih(monkeypatch):
    for k in ("BROLL", "BROLL_QUERY", "BROLL_COUNT", "PEXELS_API_KEY"):
        monkeypatch.delenv(k, raising=False)


# ------------------------------------------------------------------ konfigurasi

def test_tidak_diminta_berarti_none():
    assert b.resolve_broll() is None


def test_diminta_tanpa_key_ditolak_bukan_dilewati(monkeypatch):
    monkeypatch.setenv("BROLL", "1")
    with pytest.raises(b.BrollError, match="PEXELS_API_KEY"):
        b.resolve_broll()


def test_konfigurasi_valid(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "kunci")
    cfg = b.resolve_broll(True, "makanan, restoran!! ,  ", 2)
    assert cfg == {"queries": ["makanan", "restoran"], "jumlah": 2}


@pytest.mark.parametrize("n", [0, 7, -1])
def test_jumlah_di_luar_jangkauan_ditolak(monkeypatch, n):
    monkeypatch.setenv("PEXELS_API_KEY", "kunci")
    with pytest.raises(b.BrollError, match="jangkauan"):
        b.resolve_broll(True, "x", n)


def test_tersedia_hanya_bila_ada_key(monkeypatch):
    assert b.tersedia() is False
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    assert b.tersedia() is True


@pytest.mark.parametrize("lebar,tinggi,harap", [(1080, 1920, "portrait"), (1920, 1080, "landscape"),
                                                 (1080, 1080, "square")])
def test_orientasi(lebar, tinggi, harap):
    assert b.orientasi_untuk(lebar, tinggi) == harap


# ------------------------------------------------------------------ host & berkas

@pytest.mark.parametrize("url,harap", [
    ("https://videos.pexels.com/video-files/1/x.mp4", True),
    ("https://pexels.com/x.mp4", True),
    ("http://videos.pexels.com/x.mp4", False),                  # bukan https
    ("https://evil.com/pexels.com/x.mp4", False),               # path, bukan host
    ("https://pexels.com.evil.com/x.mp4", False),               # akhiran menipu
    ("https://evilpexels.com/x.mp4", False),
    ("file:///etc/passwd", False),
    ("", False),
])
def test_host_sah(url, harap):
    assert b.host_sah(url) is harap


def test_pilih_berkas_terkecil_yang_masih_hd_dan_hanya_mp4_di_host_sah():
    berkas = [
        {"file_type": "video/mp4", "width": 360, "height": 640, "link": "https://videos.pexels.com/a.mp4"},
        {"file_type": "video/mp4", "width": 1080, "height": 1920, "link": "https://videos.pexels.com/b.mp4"},
        {"file_type": "video/mp4", "width": 720, "height": 1280, "link": "https://videos.pexels.com/c.mp4"},
        {"file_type": "video/webm", "width": 720, "height": 1280, "link": "https://videos.pexels.com/d.webm"},
        {"file_type": "video/mp4", "width": 720, "height": 1280, "link": "https://evil.com/e.mp4"},
        {"file_type": "video/mp4", "width": 4320, "height": 7680, "link": "https://videos.pexels.com/f.mp4"},
    ]
    assert b.pilih_berkas(berkas)["link"].endswith("/c.mp4")
    assert b.pilih_berkas([]) is None


# ------------------------------------------------------------------ pencarian

def _video(id_, durasi=10, link="https://videos.pexels.com/v.mp4"):
    return {"id": id_, "duration": durasi, "url": f"https://www.pexels.com/video/{id_}/",
            "user": {"name": "Ani", "url": "https://www.pexels.com/@ani"},
            "video_files": [{"file_type": "video/mp4", "width": 720, "height": 1280, "link": link}]}


def test_cari_menyaring_durasi_dan_entri_rusak(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setattr(b, "_http_get_json", lambda u, h: {"videos": [
        _video(1), _video(2, durasi=2), _video(3, durasi=120), "rusak", {"id": None},
        _video(4, link="https://evil.com/x.mp4")]})
    assert [c["id"] for c in b.cari("makanan", "portrait")] == [1]


def test_cari_mengirim_key_di_header_bukan_di_url(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "RAHASIA")
    lihat = {}

    def fake(url, headers):
        lihat.update(url=url, headers=headers)
        return {"videos": []}

    monkeypatch.setattr(b, "_http_get_json", fake)
    b.cari("kopi", "portrait")
    assert lihat["headers"]["Authorization"] == "RAHASIA"
    assert "RAHASIA" not in lihat["url"] and "orientation=portrait" in lihat["url"]


def _http_error(kode):
    return urllib.error.HTTPError("u", kode, "x", {}, None)


@pytest.mark.parametrize("kode,potongan", [(401, "ditolak"), (403, "ditolak"), (429, "kuota")])
def test_kegagalan_http_dilaporkan_dengan_alasan_bukan_melempar(monkeypatch, tmp_path, kode, potongan):
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setattr(b, "_http_get_json", lambda u, h: (_ for _ in ()).throw(_http_error(kode)))
    klip, gagal = b.ambil(["x"], 2, "portrait", str(tmp_path), "run")
    assert klip == [] and potongan in gagal


def test_jaringan_putus_dilaporkan(monkeypatch, tmp_path):
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setattr(b, "_http_get_json", lambda u, h: (_ for _ in ()).throw(TimeoutError()))
    klip, gagal = b.ambil(["x"], 2, "portrait", str(tmp_path), "run")
    assert klip == [] and "TimeoutError" in gagal


def test_hasil_kosong_dibedakan_dari_kegagalan(monkeypatch, tmp_path):
    """Aturan #7: 'tidak ada yang cocok' dan 'gagal mengambil' adalah dua pesan berbeda."""
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setattr(b, "_http_get_json", lambda u, h: {"videos": []})
    _, gagal = b.ambil(["kata-ajaib"], 2, "portrait", str(tmp_path), "run")
    assert "tidak ada klip yang cocok" in gagal and "gagal" not in gagal.split("cocok")[0]


def _video_uji(path, warna="blue", detik=4, ukuran="240x426"):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c={warna}:size={ukuran}:rate=24:duration={detik}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   check=True, capture_output=True)


def test_unduhan_rusak_dilewati_dan_dibersihkan(monkeypatch, tmp_path):
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setattr(b, "_http_get_json", lambda u, h: {"videos": [_video(1), _video(2)]})
    baik = tmp_path / "baik.mp4"
    _video_uji(baik)

    def unduh(url, tujuan, maks):
        if not hasattr(unduh, "n"):
            unduh.n = 0
        unduh.n += 1
        if unduh.n == 1:
            open(tujuan, "wb").write(b"bukan video sama sekali")
        else:
            shutil.copy(baik, tujuan)

    monkeypatch.setattr(b, "_unduh_ke", unduh)
    klip, gagal = b.ambil(["x"], 1, "portrait", str(tmp_path / "w"), "run")
    assert len(klip) == 1 and gagal is None
    sisa = sorted(p.name for p in (tmp_path / "w").iterdir())
    assert sisa == ["_broll_0.mp4"], "berkas rusak tidak boleh tertinggal"
    assert klip[0]["kredit"] == "Video oleh Ani di Pexels"


def test_susun_urutan_bahan_user_tetap_berurutan_dan_pertama():
    assert b.susun_urutan(["A1", "A2"], ["B1", "B2", "B3"]) == ["A1", "B1", "B3", "A2", "B2"]
    assert b.susun_urutan(["A1", "A2"], []) == ["A1", "A2"]
    assert b.susun_urutan([], ["B1"]) == []


@pytest.mark.parametrize("diminta,aset,total,harap", [
    (3, 2, 12, 3), (6, 2, 12, 6), (6, 2, 6, 2), (3, 4, 6, 0), (3, 2, 3, 0)])
def test_jatah_tidak_membuang_bahan_user(diminta, aset, total, harap):
    assert b.jatah(diminta, aset, total, 1.5) == harap


# ------------------------------------------------------------------ integrasi render

def _rgb(mp4, detik, w=240, h=426):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(detik), "-i", str(mp4), "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True)
    px = np.frombuffer(r.stdout, dtype=np.uint8).reshape(h, w, 3)
    return tuple(int(v) for v in px[h // 2, w // 2])


@pytest.fixture
def render_ai(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "TARGET_W", 240)
    monkeypatch.setattr(ar, "TARGET_H", 426)
    monkeypatch.setattr(ar, "THUMBNAIL_ENABLED", False)
    monkeypatch.setattr(ar, "STATUS_PATH", str(tmp_path / "status.json"))
    monkeypatch.setenv("MUSIC_ENABLED", "0")
    monkeypatch.setattr(ar, "music_wanted", lambda: False)
    vo = tmp_path / "vo.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "anullsrc=channel_layout=mono:sample_rate=24000", "-t", "12", str(vo)],
                   check=True, capture_output=True)

    async def palsu(teks, out):
        shutil.copy2(vo, out)

    monkeypatch.setattr(ar, "generate_voice", palsu)
    foto = []
    for i, warna in enumerate(("0xc03020", "0x20c030")):       # merah bata, hijau
        p = tmp_path / f"foto{i}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"color=c={warna}:size=240x426", "-frames:v", "1", str(p)],
                       check=True, capture_output=True)
        foto.append(str(p))
    skrip = tmp_path / "script.json"
    skrip.write_text(json.dumps({"judul": "uji broll", "audio_mode": "ai", "full_voice_over": "naskah",
                                 "media_assets": foto, "scenes": []}), encoding="utf-8")
    return skrip, tmp_path


def _pasang_pexels(monkeypatch, tmp_path):
    biru = tmp_path / "biru_sumber.mp4"
    _video_uji(biru, "0x0000ff", 6)
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setattr(b, "_http_get_json", lambda u, h: {"videos": [_video(11), _video(12)]})
    monkeypatch.setattr(b, "_unduh_ke", lambda url, tujuan, maks: shutil.copy(biru, tujuan))


def test_broll_muncul_di_slot_yang_benar_dan_bahan_user_tidak_terbuang(render_ai, monkeypatch):
    skrip, tmp_path = render_ai
    _pasang_pexels(monkeypatch, tmp_path)
    monkeypatch.setenv("BROLL", "1")
    monkeypatch.setenv("BROLL_COUNT", "2")
    monkeypatch.setenv("BROLL_QUERY", "kopi")
    hasil = tmp_path / "hasil.mp4"
    ar.render_from_agent_script(str(skrip), "", str(hasil))

    # 12 dtk, 4 slot x 3 dtk: foto1, biru, foto2, biru
    slot = [_rgb(hasil, 1.5 + 3 * i) for i in range(4)]
    assert slot[0][0] > 150 and slot[0][2] < 100, f"slot 1 harus foto merah bata: {slot[0]}"
    assert slot[1][2] > 200 and slot[1][0] < 60, f"slot 2 harus B-roll biru: {slot[1]}"
    assert slot[2][1] > 150 and slot[2][2] < 100, f"slot 3 harus foto hijau: {slot[2]}"
    assert slot[3][2] > 200 and slot[3][0] < 60, f"slot 4 harus B-roll biru: {slot[3]}"

    status = json.load(open(tmp_path / "status.json"))
    assert status["broll"]["gagal"] is None
    assert [d["kredit"] for d in status["broll"]["dipakai"]] == ["Video oleh Ani di Pexels"] * 2
    assert not list(tmp_path.glob("_broll_*.mp4")), "klip unduhan harus dibersihkan"


def test_tanpa_broll_render_tidak_berubah(render_ai):
    skrip, tmp_path = render_ai
    hasil = tmp_path / "hasil.mp4"
    ar.render_from_agent_script(str(skrip), "", str(hasil))
    slot = [_rgb(hasil, 3 + 6 * i) for i in range(2)]
    assert slot[0][0] > 150 and slot[1][1] > 150
    assert json.load(open(tmp_path / "status.json"))["broll"] is None


def test_kegagalan_broll_tidak_menggagalkan_video_tapi_dilaporkan(render_ai, monkeypatch):
    skrip, tmp_path = render_ai
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setenv("BROLL", "1")
    monkeypatch.setattr(b, "_http_get_json", lambda u, h: (_ for _ in ()).throw(_http_error(429)))
    hasil = tmp_path / "hasil.mp4"
    ar.render_from_agent_script(str(skrip), "", str(hasil))
    assert hasil.exists()
    status = json.load(open(tmp_path / "status.json"))
    assert status["broll"]["dipakai"] == [] and "kuota" in status["broll"]["gagal"]
    slot = [_rgb(hasil, 3 + 6 * i) for i in range(2)]
    assert slot[0][0] > 150 and slot[1][1] > 150, "bahan user tetap utuh"


def test_durasi_terlalu_pendek_broll_dilewati_bukan_membuang_bahan(render_ai, monkeypatch):
    skrip, tmp_path = render_ai
    _pasang_pexels(monkeypatch, tmp_path)
    monkeypatch.setenv("BROLL", "1")
    monkeypatch.setattr(b, "jatah", lambda *a, **k: 0)
    hasil = tmp_path / "hasil.mp4"
    ar.render_from_agent_script(str(skrip), "", str(hasil))
    status = json.load(open(tmp_path / "status.json"))
    assert "terlalu pendek" in status["broll"]["gagal"]


# ------------------------------------------------------------------ pintu masuk Hermes

def _hermes(monkeypatch, tmp_path, argv, capsys):
    import hermes_render as hr
    root = tmp_path / "cache"
    root.mkdir(exist_ok=True)
    v = root / "k.mp4"
    _video_uji(v)
    monkeypatch.setattr(hr, "MEDIA_ROOTS", [str(root)])
    for nama in ("install_signal_handlers", "sweep_old_run_files", "ensure_dirs"):
        monkeypatch.setattr(hr, nama, lambda *a, **k: None)
    monkeypatch.setattr(hr, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(hr, "run_core_stages_locked",
                        lambda *a, **k: pytest.fail("render tidak boleh dijalankan"))
    rc = hr.main(["--media-path", str(v), "--no-require-inspect", *argv])
    return rc, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_hermes_broll_tanpa_key_ditolak_sebelum_render(monkeypatch, tmp_path, capsys):
    rc, out = _hermes(monkeypatch, tmp_path, ["--broll", "--audio-mode", "ai"], capsys)
    assert rc == 1 and out["kode"] == "broll_tidak_siap" and "PEXELS_API_KEY" in out["alasan"]


def test_hermes_broll_di_luar_mode_voiceover_ditolak_bukan_diabaikan(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    for argv in (["--broll"], ["--broll", "--audio-mode", "original"], ["--broll", "--audio-mode", "mute"]):
        rc, out = _hermes(monkeypatch, tmp_path, argv, capsys)
        assert rc == 1 and out["kode"] == "broll_butuh_voiceover", argv


# ------------------------------------------------------------------ tawaran di inspect

def _q(monkeypatch, konteks, key):
    import inspect_media as im
    if key:
        monkeypatch.setenv("PEXELS_API_KEY", "k")
    ringk = {"n_video": 2, "n_gambar": 0, "n_berucap": 0, "n_suasana": 2, "n_tanpa_suara": 0,
             "n_horizontal": 0, "n_musik": 0, "total_detik": 20}
    return im.susun_pertanyaan(ringk, im.dari_konteks(konteks))


def test_broll_ditawarkan_hanya_bila_key_ada(monkeypatch):
    assert "broll" not in [x["kode"] for x in _q(monkeypatch, "edit ya", key=False)]
    q = _q(monkeypatch, "edit ya", key=True)
    tawaran = next(x for x in q if x["kode"] == "broll")
    assert tawaran["param"]["B"] == {"broll": True, "audioMode": "ai"}


def test_broll_yang_sudah_disebut_tidak_ditawarkan_lagi(monkeypatch):
    assert "broll" not in [x["kode"] for x in _q(monkeypatch, "edit ya, tambah b-roll kopi", key=True)]


def test_permintaan_lengkap_tetap_nol_pertanyaan_walau_key_ada(monkeypatch):
    """Tawaran B-roll menumpang; ia tidak boleh memaksa pertanyaan pada permintaan lengkap."""
    import inspect_media as im
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    ringk = {"n_video": 3, "n_gambar": 0, "n_berucap": 3, "n_suasana": 0, "n_tanpa_suara": 0,
             "n_horizontal": 0, "n_musik": 0, "total_detik": 60}
    lengkap = ("Edit jadi konten TikTok 30 detik tentang sistem listing properti untuk developer, "
               "tanpa suara AI, tambahkan musik lo-fi, ajakan di akhir: hubungi Steven")
    assert im.susun_pertanyaan(ringk, im.dari_konteks(lengkap)) == []


def test_render_dengan_broll_diminta_tanpa_key_tidak_crash_dan_dilaporkan(render_ai, monkeypatch):
    """Regresi: BrollError tidak diimpor di auto_render -> NameError saat jalur ini terjadi.
    Pintu masuk seharusnya sudah menolak, tapi renderer sendiri tidak boleh crash."""
    skrip, tmp_path = render_ai
    monkeypatch.setenv("BROLL", "1")
    hasil = tmp_path / "hasil.mp4"
    ar.render_from_agent_script(str(skrip), "", str(hasil))
    assert hasil.exists()
    status = json.load(open(tmp_path / "status.json"))
    assert status["broll"]["dipakai"] == [] and "PEXELS_API_KEY" in status["broll"]["gagal"]


def test_env_hermes_render_tidak_bocor_antar_test_1(monkeypatch, tmp_path, capsys):
    import hermes_render as hr
    hr._apply_env(hr._parse_args(["--media-path", "/x", "--broll"]))
    import os
    assert os.environ.get("BROLL") == "1"


def test_env_hermes_render_tidak_bocor_antar_test_2():
    import os
    assert os.environ.get("BROLL") is None
