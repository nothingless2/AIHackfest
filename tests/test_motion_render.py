"""Motion graphic sungguhan: rencana frame, cadangan saat gagal, dan render Remotion yang
diukur dari PIKSEL (kartu pembuka di awal, elemen muncul saat kata jangkarnya diucapkan dan
tidak sebelumnya, kartu ajakan di akhir, teks narasi tetap ada -- dalam SATU encode)."""

import os
import subprocess

import numpy as np
import pytest

import auto_render as ar
import overlay_remotion as orr

W, H, FPS = 540, 960, 24
ADA_REMOTION = os.path.isdir(os.path.join(orr.REMOTION_DIR, "node_modules"))
NASKAH = "Halo semua. Kita kumpul lagi minggu depan buat donor darah. Yuk daftar lewat link di bio sekarang juga."


def _kata_waktu(per=0.45):
    return [{"word": w, "start": round(0.1 + i * per, 3), "end": round(0.1 + (i + 1) * per, 3)}
            for i, w in enumerate(NASKAH.split())]


def _data(**ubah):
    d = {"full_voice_over": NASKAH, "konteks_user": "ajak donor darah minggu depan",
         "motion_plan": {"hook": "Ayo donor darah!", "hook_sorot": "donor", "hook_emoji": "🩸",
                         "elemen": [{"jenis": "sorot", "teks": "Minggu depan", "saat_kata": "minggu"}],
                         "cta": "Daftar sekarang", "cta_sub": "link di bio", "cta_emoji": "👇"}}
    d.update(ubah)
    return d


def _video(path, detik=9.0):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=gray:size={W}x{H}:rate={FPS}:duration={detik}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)


def _frame(mp4, detik):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{detik}", "-i", str(mp4), "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.uint8).reshape(H, W, 3).astype(int)


def _putih(f, y0, y1):
    z = f[int(H * y0):int(H * y1)]
    return int(((z.min(axis=2)) > 215).sum())


def _ungu(f, y0, y1):
    z = f[int(H * y0):int(H * y1)]
    return int(((z[:, :, 2] > 150) & (z[:, :, 0] > 90) & (z[:, :, 1] < 110)).sum())


@pytest.fixture
def kanvas_kecil(monkeypatch):
    monkeypatch.setattr(ar, "TARGET_W", W)
    monkeypatch.setattr(ar, "TARGET_H", H)
    monkeypatch.setattr(ar, "FPS", FPS)
    monkeypatch.setenv("MOTION_GRAPHIC", "sedang")


# ------------------------------------------------------------------ perencana frame

def test_rencana_motion_masuk_lalu_satu_gambar_diam_yang_memudar():
    props, kerja = orr.rencana_motion([{"jenis": "sorot", "teks": "x", "mulai": 1.0, "selesai": 2.6}], FPS)
    assert [k["jenis"] for k in kerja] == ["klip", "diam"]
    klip, diam = kerja
    assert klip["sampai"] - klip["dari"] + 1 == orr.MOTION_MASUK
    assert diam["frame"] == klip["sampai"] + 1 and diam["pudar"] == orr.MOTION_KELUAR
    assert diam["frame"] + diam["tahan"] == round(2.6 * FPS), "menutup sampai selesai, tanpa celah"
    assert props[0]["masukFrames"] == orr.MOTION_MASUK


def test_rencana_motion_menolak_tumpang_tindih():
    with pytest.raises(orr.OverlayError, match="bertumpuk"):
        orr.rencana_motion([{"jenis": "sorot", "teks": "a", "mulai": 1.0, "selesai": 2.0},
                            {"jenis": "sorot", "teks": "b", "mulai": 1.5, "selesai": 3.0}], FPS)


# ------------------------------------------------------------------ cadangan & sakelar

def test_dimatikan_tidak_memanggil_node(kanvas_kecil, monkeypatch, tmp_path):
    monkeypatch.setenv("MOTION_GRAPHIC", "mati")
    monkeypatch.setattr(orr, "_jalankan_node", lambda *a, **k: pytest.fail("node tidak boleh dipanggil"))
    assert ar.siapkan_motion(_data(), 9.0, _kata_waktu(), False, str(tmp_path)) is None
    assert ar.MOTION == {"dipakai": False, "alasan": "dimatikan"}


def test_brief_tanpa_rencana_dicatat(kanvas_kecil, tmp_path):
    assert ar.siapkan_motion(_data(motion_plan=None), 9.0, [], False, str(tmp_path)) is None
    assert "tanpa rencana" in ar.MOTION["alasan"]


def test_render_gagal_video_tetap_jadi_dan_dicatat(kanvas_kecil, monkeypatch, tmp_path):
    monkeypatch.setattr(orr, "_jalankan_node",
                        lambda *a, **k: (_ for _ in ()).throw(orr.OverlayError("chromium mogok")))
    assert ar.siapkan_motion(_data(), 9.0, _kata_waktu(), False, str(tmp_path)) is None
    assert ar.MOTION["dipakai"] is False and ar.MOTION["gagal"] == "chromium mogok"


def test_tempel_gagal_video_tetap_jadi_dengan_teks(kanvas_kecil, monkeypatch, tmp_path):
    src, out = tmp_path / "s.mp4", tmp_path / "o.mp4"
    _video(src, 3)
    ar.MOTION.clear()
    ar.MOTION["dipakai"] = True
    palsu = [{"jenis": "diam", "frame": 0, "tahan": 10, "mulai": 0, "out": str(tmp_path / "hilang.png")}]
    ar.apply_text_overlay(str(src), [{"start": 0, "end": 3, "text": "Halo", "statis": True}], str(out),
                          motion=palsu)
    assert out.exists() and ar.MOTION["dipakai"] is False and "penempelan" in ar.MOTION["gagal"]
    assert _putih(_frame(out, 1.5), 0, 1) > 50, "teks tetap tampil walau grafik gagal"


# ------------------------------------------------------------------ render sungguhan

@pytest.mark.skipif(not ADA_REMOTION, reason="paket Remotion belum terpasang")
def test_render_nyata_waktu_dan_posisi_dari_piksel(kanvas_kecil, monkeypatch, tmp_path):
    src, out = tmp_path / "s.mp4", tmp_path / "o.mp4"
    _video(src)
    kw = _kata_waktu()
    motion = ar.siapkan_motion(_data(), 9.0, kw, False, str(tmp_path))
    assert motion and ar.MOTION["dipakai"] is True, ar.MOTION
    assert [e["jenis"] for e in ar.MOTION["elemen"]] == ["kartu_hook", "sorot", "kartu_cta"]

    encode = []
    asli = ar.run_ffmpeg
    monkeypatch.setattr(ar, "run_ffmpeg", lambda *a, **k: (encode.append(a), asli(*a, **k))[1])
    narasi = [{"start": kw[0]["start"], "end": 9.0, "text": NASKAH, "words": kw, "gaya": ar.NARASI_STYLE}]
    ar.apply_text_overlay(str(src), narasi, str(out), motion=motion)
    assert encode == [], "grafik + teks narasi harus SATU encode (komposit), bukan encode tambahan"

    minggu = next(w["start"] for w in kw if w["word"] == "minggu")
    polos = _frame(src, 3.0)
    hook, jeda, sorot, cta = _frame(out, 1.2), _frame(out, minggu - 0.3), _frame(out, minggu + 0.9), _frame(out, 8.4)

    # kartu pembuka: teks putih di bagian atas; gambar polos tidak punya putih sama sekali
    assert _putih(polos, 0.1, 0.55) == 0
    assert _putih(hook, 0.1, 0.55) > 800
    # sebelum kata jangkar: tidak ada elemen (bagian atas & zona sorot sama dengan polos)
    assert _putih(jeda, 0.1, 0.55) < 50 and _ungu(jeda, 0.42, 0.56) < 50
    # sesudah kata jangkar: chip sorot ungu-pink di zona sorot
    assert _ungu(sorot, 0.42, 0.56) > 400
    # kartu ajakan di akhir
    assert _putih(cta, 0.1, 0.55) > 500
    # teks narasi (drawtext) tetap ada di zona narasi, tidak tertutup peredup kartu pembuka
    assert _putih(hook, 0.58, 0.76) > 150
    # animasi keluar: kartu pembuka memudar di ujungnya (pudar ffmpeg pada gambar diam)
    akhir_hook = next(e for e in ar.MOTION["elemen"] if e["jenis"] == "sorot")["mulai"]
    assert akhir_hook > 2.2
    pudar = _frame(out, 2.12)
    assert _putih(pudar, 0.1, 0.55) < _putih(hook, 0.1, 0.55) * 0.8
