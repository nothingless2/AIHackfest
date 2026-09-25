"""Storyboard pratinjau di pesan draf (scripts/storyboard.py): panel memuat bahan yang benar
pada perkiraan waktunya, grafik & B-roll pada posisinya, dan kegagalan tidak menggagalkan draf."""

import os
import subprocess

import pytest
from PIL import Image

import overlay_remotion as orr
import storyboard as sb
from test_draft import buat_draf, env

__all__ = ["env"]          # fixture dipakai ulang dari test_draft (lewat parameter tes)

ADA_REMOTION = os.path.isdir(os.path.join(orr.REMOTION_DIR, "node_modules"))
WARNA = {"red": (255, 0, 0), "lime": (0, 255, 0), "blue": (0, 0, 255)}


def _klip(path, warna, detik=4):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c={warna}:size=360x640:rate=24:duration={detik}", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)
    return str(path)


def _panel(path, n, fx=0.5, fy=0.3):
    im = Image.open(path).convert("RGB")
    x = (n % sb.KOLOM) * sb.PANEL_W + int(sb.PANEL_W * fx)
    y = (n // sb.KOLOM) * sb.PANEL_H + int(sb.PANEL_H * fy)
    return im.getpixel((x, y))


def _terdekat(c):
    return min(WARNA, key=lambda k: sum((a - b) ** 2 for a, b in zip(WARNA[k], c)))


@pytest.fixture
def bahan(tmp_path):
    return [_klip(tmp_path / f"{w}.mp4", w) for w in ("red", "lime", "blue")]


NASKAH = " ".join(["kata"] * 27)          # ±10 dtk narasi


def test_panel_ai_menampilkan_klip_pada_perkiraan_waktunya(bahan, tmp_path):
    brief = {"audio_mode": "ai", "media_assets": bahan}
    total, pot, _ = sb.linimasa(brief, {"full_voice_over": NASKAH})
    assert total == pytest.approx(10.0, abs=0.1)
    hasil = sb.buat(brief, {"full_voice_over": NASKAH}, str(tmp_path / "s.jpg"))
    assert hasil["panel"] == sb.JUMLAH_PANEL
    waktu = sb.pilih_waktu(total, [], [])
    for n, t in enumerate(waktu):
        q = next(p for p in pot if p["mulai"] <= t < p["selesai"])
        harap = os.path.basename(q["path"]).split(".")[0]
        assert _terdekat(_panel(tmp_path / "s.jpg", n)) == harap, f"panel {n} (t={t:.1f})"


def test_panel_suara_asli_memakai_kata_transkrip_dan_thumbnail_broll(bahan, tmp_path):
    kata = [{"word": w, "start": 0.4 * i, "end": 0.4 * i + 0.3}
            for i, w in enumerate("halo semua kita ikut donor darah bersama relawan di aula kampus hari ini ya".split())]
    brief = {"audio_mode": "original", "media_assets": bahan,
             "transcript_words": {"red.mp4": kata}}
    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (100, 100), (255, 255, 255)).save(thumb)
    diminta = []
    hasil = sb.buat(brief, {"broll": [{"query": "blood donation", "saat_kata": "donor"}]},
                    str(tmp_path / "s.jpg"),
                    pratinjau_broll=lambda q, tujuan: (diminta.append(q), str(thumb))[1])
    assert diminta == ["blood donation"] and hasil["panel"] == sb.JUMLAH_PANEL
    putih = [n for n in range(sb.JUMLAH_PANEL) if min(_panel(tmp_path / "s.jpg", n)) > 230]
    assert putih, "panel di jendela cutaway menampilkan thumbnail B-roll"


def test_remotion_gagal_storyboard_tetap_jadi(bahan, tmp_path, monkeypatch):
    monkeypatch.setattr(orr, "_jalankan_node", lambda *a, **k: (_ for _ in ()).throw(orr.OverlayError("mogok")))
    plan = {"hook": "Ayo lihat", "elemen": [], "cta": ""}
    hasil = sb.buat({"audio_mode": "ai", "media_assets": bahan},
                    {"full_voice_over": NASKAH, "motion_plan": plan}, str(tmp_path / "s.jpg"))
    assert os.path.exists(hasil["path"]) and "grafik tidak dipratinjau" in hasil["catatan"][0]


@pytest.mark.skipif(not ADA_REMOTION, reason="paket Remotion belum terpasang")
def test_still_grafik_tampil_di_panel_kartu_pembuka(bahan, tmp_path):
    """Panel pertama = kartu pembuka (still Remotion sungguhan). Kontrol: storyboard yang sama
    tanpa rencana grafik -- area kartu harus jelas berbeda; panel lain tidak."""
    brief = {"audio_mode": "ai", "media_assets": bahan}
    plan = {"hook": "Ayo lihat ini", "elemen": [], "cta": ""}
    sb.buat(brief, {"full_voice_over": NASKAH, "motion_plan": plan}, str(tmp_path / "g.jpg"))
    sb.buat(brief, {"full_voice_over": NASKAH}, str(tmp_path / "p.jpg"))
    beda = lambda n: sum(abs(a - b) for a, b in zip(_panel(tmp_path / "g.jpg", n, fy=0.30),
                                                     _panel(tmp_path / "p.jpg", n, fy=0.30)))
    assert beda(0) > 120, "kartu pembuka harus menutupi bahan di panel pertama"
    assert beda(5) < 30, "panel tanpa elemen grafik identik dengan kontrol"


def test_kalimat_pertama():
    assert sb.kalimat_pertama("Halo semua! Hari ini kita donor.") == "Halo semua!"
    assert len(sb.kalimat_pertama("kata " * 100)) <= 140


def test_draf_tetap_terkirim_walau_storyboard_gagal(env, monkeypatch, capsys):
    """Ujung ke ujung lewat hermes_render --draft: storyboard meledak -> draf tetap ok,
    alasannya dilaporkan."""
    import storyboard
    monkeypatch.setenv("STORYBOARD", "1")
    monkeypatch.setattr(storyboard, "untuk_draf", lambda d, f: (_ for _ in ()).throw(RuntimeError("boom")))
    out = buat_draf(env, capsys)
    assert out["ok"] and out["storyboard"] == [] and "boom" in out["storyboard_gagal"][0]


def test_draf_membawa_path_storyboard(env, monkeypatch, capsys):
    import storyboard
    monkeypatch.setenv("STORYBOARD", "1")
    monkeypatch.setattr(storyboard, "untuk_draf",
                        lambda d, f: {"storyboard": [f"{f}/{d['draft_id']}_A.jpg"], "contoh_suara": None,
                                      "gagal": []})
    out = buat_draf(env, capsys)
    assert out["storyboard"][0].endswith(out["draft_id"] + "_A.jpg") and out["storyboard_gagal"] is None


def test_auto_render_terimpor_dari_proses_hermes_render():
    """Proses hermes_render hanya punya scripts/ di jalur impor (bug nyata 25 Sep)."""
    import sys
    scripts = os.path.dirname(os.path.abspath(sb.__file__))
    kode = (f"import sys; sys.path = [{scripts!r}] + [p for p in sys.path if 'video_generator' not in p];"
            "import storyboard; m = storyboard.impor_auto_render(); print(hasattr(m, 'generate_voice'))")
    out = subprocess.run([sys.executable, "-c", kode], capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().endswith("True"), out.stderr[-400:]
