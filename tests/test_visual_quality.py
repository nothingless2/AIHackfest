"""Deteksi & pemotongan bagian video yang goyang/oleng (scripts/visual_quality.py).

Guncangan SINTETIS dengan posisi diketahui: sumber bertekstur (testsrc2) di-crop dengan
offset acak per frame hanya di detik 2-3. Kalibrasi pada klip nyata user (24 Sep): klip
stabil <= 20 %lebar/dtk; kamera mengayun ke lantai 170-317 %lebar/dtk."""

import json
import subprocess

import pytest

import auto_render as ar
import visual_quality as vq


def _video(path, crop_x, detik=5):
    """Kamera sintetis di atas GAMBAR DIAM bertekstur: satu-satunya gerakan adalah gerakan
    kamera (crop). testsrc2 dicoba lebih dulu dan tidak cocok -- polanya sendiri bergerak
    (40-56 %lebar/dtk terukur), jadi 'klip stabil' pun ditandai goyang."""
    gambar = path.parent / "tekstur.png"
    if not gambar.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=480x270:rate=1:duration=1", "-vf",
                        "noise=alls=40:allf=u", "-frames:v", "1", str(gambar)],
                       check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "24", "-i", str(gambar),
                    "-t", str(detik),
                    "-vf", f"crop=w=iw*0.6:h=ih*0.6:x='{crop_x}':y='(ih-oh)/2',scale=320:180",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)
    return str(path)


GUNCANG = "(iw-ow)/2+if(between(t,2,3),(random(0)-0.5)*iw*0.35,0)"


@pytest.fixture(scope="module")
def klip(tmp_path_factory):
    d = tmp_path_factory.mktemp("vq")
    return {
        "stabil": _video(d / "stabil.mp4", "(iw-ow)/2"),
        "guncang": _video(d / "guncang.mp4", GUNCANG),
        # pan halus 12 %lebar/dtk: gerakan kamera yang DISENGAJA, bukan cacat
        "pan": _video(d / "pan.mp4", "(iw-ow)*t/5"),
    }


def test_klip_stabil_tidak_ditandai(klip):
    assert vq.analisis(klip["stabil"])["buruk"] == []


def test_pan_halus_bukan_guncangan(klip):
    assert vq.analisis(klip["pan"])["buruk"] == []


def test_guncangan_ditemukan_di_posisi_yang_benar(klip):
    buruk = vq.analisis(klip["guncang"])["buruk"]
    assert len(buruk) == 1, buruk
    a, b, alasan = buruk[0]
    assert 1.6 <= a <= 2.2 and 2.8 <= b <= 3.4, buruk
    assert alasan in ("goyang", "oleng", "buram")


def test_kurangi_rentang():
    assert vq.kurangi([(0, 5)], [(2, 3, "goyang")]) == [(0, 2), (3, 5)]
    assert vq.kurangi([(0, 5)], [(0, 4.5, "goyang")], min_keep=0.8) == []
    assert vq.kurangi([(0, 2), (3, 5)], [(1.5, 3.5, "x")]) == [(0, 1.5), (3.5, 5)]


def test_satu_frame_aneh_diabaikan():
    import numpy as np
    n = 50
    m = {"gerak": np.zeros(n), "cocok": np.ones(n), "tajam_rel": np.ones(n), "durasi": n / 10}
    m["gerak"][20] = 500                    # satu kedipan
    assert vq.rentang_buruk(m) == []
    m["gerak"][20:26] = 500                 # 0,6 dtk guncangan nyata
    assert len(vq.rentang_buruk(m)) == 1


# ------------------------------------------------------------------ integrasi rencana

@pytest.fixture
def nyala(monkeypatch):
    monkeypatch.setenv("VISUAL_CUT", "1")
    ar.POTONG_VISUAL.clear()


def test_potongan_tengah_jadi_dua_rentang_dengan_transisi(klip, nyala):
    rencana = [{"path": klip["guncang"], "ranges": [(0.0, 5.0)], "durasi": 5.0, "asli": 5.0}]
    baru = ar.terapkan_potong_visual(rencana, {})
    r = baru[0]["ranges"]
    assert len(r) == 2 and r[0][1] < 2.3 and r[1][0] > 2.7
    assert baru[0]["fade_setelah"] == {0}
    assert ar.sambungan_audio_asli(baru) == [True], "sambungan bekas potongan goyang diberi fade"
    assert ar.POTONG_VISUAL["dipotong"][0]["file"] == "guncang.mp4"


def test_potongan_jeda_biasa_tetap_hard_cut(nyala):
    rencana = [{"path": "a.mp4", "ranges": [(0, 2), (3, 5)], "durasi": 4.0, "asli": 5.0}]
    assert ar.sambungan_audio_asli(rencana) == [False]


def test_bagian_goyang_berisi_ucapan_tidak_dibuang(klip, nyala):
    rencana = [{"path": klip["guncang"], "ranges": [(0.0, 5.0)], "durasi": 5.0, "asli": 5.0}]
    data = {"transcript_words": {"guncang.mp4": [{"word": "penting", "start": 2.4, "end": 2.8}]}}
    baru = ar.terapkan_potong_visual(rencana, data)
    assert baru[0]["ranges"] == [(0.0, 5.0)]
    assert ar.POTONG_VISUAL["dipertahankan_ucapan"][0]["file"] == "guncang.mp4"
    assert "dipotong" not in ar.POTONG_VISUAL


def test_gagal_mengukur_dicatat_bukan_dianggap_bersih(nyala, monkeypatch):
    monkeypatch.setattr(ar._vq, "analisis", lambda p: (_ for _ in ()).throw(vq.VisualError("rusak")))
    rencana = [{"path": "x.mp4", "ranges": [(0, 3)], "durasi": 3.0, "asli": 3.0}]
    assert ar.terapkan_potong_visual(rencana, {}) == rencana
    assert ar.POTONG_VISUAL["gagal_ukur"][0]["file"] == "x.mp4"


def test_dimatikan_tidak_menganalisis(monkeypatch):
    monkeypatch.setenv("VISUAL_CUT", "0")
    monkeypatch.setattr(ar._vq, "analisis", lambda p: pytest.fail("tidak boleh dianalisis"))
    rencana = [{"path": "x.mp4", "ranges": [(0, 3)], "durasi": 3.0}]
    assert ar.terapkan_potong_visual(rencana, {}) is rencana


# ------------------------------------------------------------------ mode voice-over AI

def test_ai_memilih_rentang_layak_terpanjang(klip, nyala):
    a, b = ar.rentang_layak_ai(klip["guncang"])
    (x, y, _), = vq.analisis(klip["guncang"])["buruk"]
    assert b <= x or a >= y, "rentang terpilih tidak boleh menyentuh bagian goyang"
    sisa = [(0.0, x), (y, 5.0)]
    assert (b - a) == pytest.approx(max(q - p for p, q in sisa), abs=0.05), "harus yang terpanjang"


def test_ai_klip_stabil_tidak_dipotong(klip, nyala):
    assert ar.rentang_layak_ai(klip["stabil"]) is None


def test_build_segment_dengan_potong_diloop_dan_durasi_tetap(klip, tmp_path):
    seg = tmp_path / "seg.mp4"
    ar.build_segment(klip["stabil"], 3.0, str(seg), keep_audio=False, potong=(0.5, 1.5),
                     fade_in=False, fade_out=False)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(seg)], capture_output=True, text=True).stdout)
    assert dur == pytest.approx(3.0, abs=0.1), "rentang 1 dtk harus diloop mengisi slot 3 dtk"
    assert not list(tmp_path.glob("*_layak.mp4")), "berkas sementara harus dibersihkan"
