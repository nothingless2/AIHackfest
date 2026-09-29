"""Tata letak "panggung" (29 Sep, contoh video user Claude + Remotion): di poin utama, pembicara
jadi kartu membulat di latar terang berkisi + ilustrasi; caption jadi judul serif. LLM hanya
mengusulkan ilustrasi & kata jangkar; kode menjadwalkan, lapisan lain menyesuaikan."""

import json
import subprocess

import numpy as np
import pytest

import motion_plan as mp

KATA = "halo semua hari ini kita ikut donor darah di aula kampus bersama relawan komunitas".split()


def kw(per=0.5, mulai=0.5):
    return [{"word": w, "start": round(mulai + i * per, 3), "end": round(mulai + i * per + 0.4, 3)}
            for i, w in enumerate(KATA)]


NASKAH = " ".join(KATA)


# ------------------------------------------------------------------ aturan (murni)

def test_katalog_dan_jangkar_ditegakkan():
    b, cat = mp.bersihkan_panggung([{"ilustrasi": "timeline", "saat_kata": "donor darah"},
                                    {"ilustrasi": "animasi_3d", "saat_kata": "aula"},
                                    {"ilustrasi": "chat", "saat_kata": "pizza"}, "x"], naskah=NASKAH)
    assert [x["ilustrasi"] for x in b] == ["timeline"] and b[0]["jangkar"] == ["donor", "darah"]
    assert len(cat) == 2 and "katalog" in cat[0] and "terucap" in cat[1]
    assert mp.bersihkan_panggung("bukan daftar", naskah=NASKAH)[0] == []


def test_jadwal_dari_kata_terdengar_di_luar_kartu_pembuka_dan_ajakan():
    b, _ = mp.bersihkan_panggung([{"ilustrasi": "kode", "saat_kata": "halo"},
                                  {"ilustrasi": "timeline", "saat_kata": "donor"}], naskah=NASKAH)
    j, cat = mp.jadwal_panggung(b, kw(per=1.3), 20.0)
    assert len(j) == 2, cat
    assert j[0]["mulai"] >= mp.HOOK_DETIK + mp.PANGGUNG_TEPI - 1e-9, "tidak menimpa kartu pembuka"
    donor = next(w["start"] for w in kw(per=1.3) if w["word"] == "donor")
    assert j[1]["mulai"] == pytest.approx(donor - mp.PANGGUNG_SEBELUM)
    for x in j:
        assert x["selesai"] <= 20.0 - mp.CTA_DETIK - mp.PANGGUNG_TEPI + 1e-9
        assert x["selesai"] - x["mulai"] >= mp.PANGGUNG_MIN


def test_jarak_dan_batas_jumlah():
    kata = [{"word": f"k{i}", "start": i * 1.0, "end": i * 1.0 + 0.5} for i in range(40)]
    usul = [{"ilustrasi": "kata", "saat_kata": f"k{i}"} for i in (5, 7, 13, 21, 30)]
    b, _ = mp.bersihkan_panggung(usul, naskah=" ".join(w["word"] for w in kata))
    j, cat = mp.jadwal_panggung(b, kata, 40.0)
    assert len(j) == mp.PANGGUNG_MAKS
    assert j[1]["mulai"] - j[0]["mulai"] >= mp.PANGGUNG_JARAK
    assert any("terlalu dekat" in c for c in cat) and any("batas" in c for c in cat)


def test_video_pendek_tanpa_panggung():
    b, _ = mp.bersihkan_panggung([{"ilustrasi": "kata", "saat_kata": "kampus"}], naskah=NASKAH)
    j, cat = mp.jadwal_panggung(b, kw(per=0.2), 4.0)
    assert j == [] and "tidak cukup waktu" in cat[0]


# ------------------------------------------------------------------ aktivasi & jalur cadangan

@pytest.fixture
def ar_dinamis(monkeypatch):
    import auto_render as ar
    monkeypatch.setenv("SUBTITLE_STYLE", "dinamis")
    monkeypatch.setenv("MOTION_GRAPHIC", "sedang")
    monkeypatch.setattr(ar, "TARGET_W", 270)
    monkeypatch.setattr(ar, "TARGET_H", 480)
    return ar


def test_hanya_aktif_untuk_gaya_dinamis_kanvas_tegak(ar_dinamis, monkeypatch):
    ar = ar_dinamis
    assert ar.panggung_aktif()
    monkeypatch.setenv("SUBTITLE_STYLE", "karaoke")
    assert not ar.panggung_aktif()
    monkeypatch.setenv("SUBTITLE_STYLE", "dinamis")
    monkeypatch.setenv("PANGGUNG", "0")
    assert not ar.panggung_aktif()
    monkeypatch.delenv("PANGGUNG")
    monkeypatch.setattr(ar, "TARGET_H", 270)
    assert not ar.panggung_aktif(), "kanvas mendatar: tata letak dirancang 9:16"


def test_usulan_brief_dipakai(ar_dinamis):
    ar = ar_dinamis
    j = ar.siapkan_panggung({"motion_plan": {"panggung": [{"ilustrasi": "grafik_naik", "saat_kata": "kampus"}]}},
                            kw(), 20.0, NASKAH)
    assert [x["ilustrasi"] for x in j] == ["grafik_naik"] and ar.PANGGUNG["sumber"] == "brief"


def test_tanpa_usulan_kartu_kata_dari_kata_kunci_terucap(ar_dinamis, monkeypatch):
    ar = ar_dinamis
    monkeypatch.setattr(ar, "CAPTION_KUNCI", ["relawan"])
    j = ar.siapkan_panggung({"motion_plan": {}}, kw(), 20.0, NASKAH)
    assert len(j) == 1 and j[0]["ilustrasi"] == "kata" and j[0]["teks"] == "relawan"
    assert ar.PANGGUNG["sumber"] == "kata kunci caption"


def test_motion_yang_bertabrakan_dengan_panggung_dibuang(ar_dinamis, monkeypatch, tmp_path):
    ar = ar_dinamis
    import overlay_remotion as orr
    ditangkap = {}
    monkeypatch.setattr(orr, "render_motion", lambda items, folder, **k:
                        (ditangkap.setdefault("items", items) and [], {"frame_chromium": 0, "item": len(items)}))
    data = {"motion_plan": {"elemen": [{"jenis": "sorot", "teks": "Donor", "saat_kata": "donor"},
                                       {"jenis": "sorot", "teks": "Relawan", "saat_kata": "relawan"}]}}
    donor = next(w["start"] for w in kw() if w["word"] == "donor")
    ar.siapkan_motion(data, 20.0, kw(), False, str(tmp_path), naskah=NASKAH,
                      hindari=[{"mulai": donor - 0.2, "selesai": donor + 1.0}])
    assert [i["teks"] for i in ditangkap["items"]] == ["Relawan"]
    assert any("panggung" in c for c in ar.MOTION["catatan"])


# ------------------------------------------------------------------ render sungguhan (piksel)

def _render(ar, monkeypatch, tmp_path, usulan):
    monkeypatch.setattr(ar, "THUMBNAIL_ENABLED", False)
    monkeypatch.setattr(ar, "TRIM_SILENCE", False)
    monkeypatch.setattr(ar, "SUBTITLE_STYLE", "dinamis")
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC", "off")
    monkeypatch.setenv("SFX", "off")
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=0x2040A0:size=270x480:rate=24:duration=12", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=12", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ac", "2", "-shortest", str(video)], check=True, capture_output=True)
    data = {"judul": "uji", "audio_mode": "original", "full_voice_over": "x", "media_assets": [str(video)],
            "scenes": [], "transcript_words": {"v.mp4": kw()}, "motion_plan": {"panggung": usulan},
            "transcript_segments": {"v.mp4": [{"start": 0.5, "end": 8.5, "text": NASKAH}]}}
    s = tmp_path / "s.json"
    s.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "h.mp4"
    ar.render_from_agent_script(str(s), str(out))
    return out, json.load(open(ar.STATUS_PATH))


def _px(path, t, x, y):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-vf", f"crop=2:2:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 3).mean(0)


def kertas(c):
    return c[0] > 225 and c[1] > 225 and c[2] > 220


def test_render_latar_kartu_hanya_di_jendela(ar_dinamis, monkeypatch, tmp_path):
    ar = ar_dinamis
    out, st = _render(ar, monkeypatch, tmp_path, [{"ilustrasi": "timeline", "saat_kata": "donor"}])
    (j,) = st["panggung"]["jendela"]
    k = __import__("overlay_remotion").kartu_panggung(270, 480)
    tengah = (j["mulai"] + j["selesai"]) / 2
    assert kertas(_px(out, tengah, 4, 4)), "latar kertas tampil di jendela"
    for t in (j["mulai"] - 0.5, j["selesai"] + 0.5):
        c = _px(out, t, 4, 4)
        assert c[2] > c[0] + 60, f"di luar jendela (t={t:.2f}) harus video asli (biru), bukan {c}"
    assert kertas(_px(out, tengah, k["x"], k["y"])) or _px(out, tengah, k["x"], k["y"]).mean() > 180, \
        "sudut kartu membulat (latar/bayangan, bukan video biru)"
    biru = _px(out, tengah, k["x"] + k["w"] // 2, k["y"] + k["h"] // 2)
    assert biru[2] > biru[0] + 60, "tengah kartu = video pembicara"


def test_render_panggung_gagal_video_tetap_jadi(ar_dinamis, monkeypatch, tmp_path):
    ar = ar_dinamis
    import overlay_remotion as orr

    def rusak(*a, **k):
        raise orr.OverlayError("chromium mati")
    monkeypatch.setattr(orr, "render_panggung", rusak)
    out, st = _render(ar, monkeypatch, tmp_path, [{"ilustrasi": "timeline", "saat_kata": "donor"}])
    assert st["panggung"]["dipakai"] is False and "chromium mati" in st["panggung"]["gagal"]
    assert not kertas(_px(out, 5.0, 4, 4)) and out.exists()
