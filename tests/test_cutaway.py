"""B-roll cutaway di mode SUARA ASLI (25 Sep): klip stok ditimpa sebentar saat kata jangkarnya
diucapkan; suara asli & subtitle TIDAK bergeser. Pexels dipalsukan dengan klip merah lokal."""

import json
import subprocess

import numpy as np
import pytest

import auto_render as ar
import broll
import motion_plan as mp

KATA = "halo semua hari ini kita ikut donor darah di aula kampus bersama relawan komunitas".split()


def _kata(per=0.5, mulai=0.0):
    return [{"word": w, "start": round(mulai + i * per, 3), "end": round(mulai + i * per + 0.4, 3)}
            for i, w in enumerate(KATA)]


# ------------------------------------------------------------------ penjadwal (murni)

def test_cutaway_mulai_sebelum_kata_jangkar_diucapkan():
    (c,), _ = mp.jadwal_broll([{"query": "hall", "saat_kata": "aula"}], _kata(), 12.0)
    aula = next(w["start"] for w in _kata() if w["word"] == "aula")
    assert c["mulai"] == pytest.approx(aula - mp.CUT_SEBELUM)
    assert c["selesai"] - c["mulai"] == pytest.approx(mp.CUT_LAMA[1])


def test_tidak_menimpa_kartu_pembuka_dan_ajakan():
    jadwal, _ = mp.jadwal_broll([{"query": "hi", "saat_kata": "halo"},
                                 {"query": "c", "saat_kata": "komunitas"}], _kata(per=0.7), 11.0,
                                sibuk=[(0, mp.HOOK_DETIK)])
    for c in jadwal:
        assert c["mulai"] >= mp.HOOK_DETIK and c["selesai"] <= 11.0 - mp.CTA_DETIK + 1e-6


def test_jangkar_tak_terucap_dibuang_dan_dicatat():
    jadwal, cat = mp.jadwal_broll([{"query": "x", "saat_kata": "pizza"}], _kata(), 12.0)
    assert jadwal == [] and "tidak ditemukan" in cat[0]


def test_urut_waktu_berjarak_dan_maksimal_tiga():
    kata = [{"word": f"k{i}", "start": i * 1.0, "end": i * 1.0 + 0.5} for i in range(40)]
    usulan = [{"query": f"q{i}", "saat_kata": f"k{i}"} for i in (30, 4, 12, 20, 8)]
    jadwal, cat = mp.jadwal_broll(usulan, kata, 40.0)
    assert len(jadwal) == mp.CUT_MAKS and any("batas" in c for c in cat)
    assert [c["query"] for c in jadwal] == ["q4", "q8", "q12"], "diproses urut waktu ucapan"
    for x, y in zip(jadwal, jadwal[1:]):
        assert y["mulai"] >= x["selesai"] + mp.CUT_JARAK - 1e-6


def test_tidak_bertumpuk_dengan_elemen_motion():
    jadwal, _ = mp.jadwal_broll([{"query": "x", "saat_kata": "donor"}], _kata(), 12.0,
                                sibuk=[(3.2, 4.8)])
    for c in jadwal:
        assert c["selesai"] <= 3.2 or c["mulai"] >= 4.8


# ------------------------------------------------------------------ usulan & kata kunci

def test_query_user_menang_jangkar_tetap_dari_brief(monkeypatch):
    monkeypatch.setenv("BROLL_QUERY", "blood bag, nurse")
    u = ar._usulan_broll({"broll": [{"query": "donation", "saat_kata": "donor"}]}, _kata(), 12.0)
    assert u == [{"query": "blood bag", "saat_kata": "donor", "dari_user": True},
                 {"query": "nurse", "saat_kata": u[1]["saat_kata"], "dari_user": True}]
    assert u[1]["saat_kata"] in KATA, "tanpa jangkar: kata yang diucapkan di titik merata"


def test_usulan_llm_dibersihkan():
    assert broll.usulan_bersih([{"query": "  blood <b>bag</b>!! ", "saat_kata": "donor"}, "x",
                                {"query": ""}, {"query": "a"}, {"query": "b"}, {"query": "c"}]) == [
        {"query": "blood b bag b", "saat_kata": "donor"}, {"query": "a", "saat_kata": ""},
        {"query": "b", "saat_kata": ""}]


# ------------------------------------------------------------------ render sungguhan

def _video_bersuara(path, detik=12.0):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=gray:size=240x426:rate=24:duration={detik}", "-f", "lavfi", "-i",
                    f"sine=frequency=1000:duration={detik}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True, capture_output=True)
    return str(path)


def _rgb(path, t):
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-vf", "crop=iw:ih*0.3:0:0,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return tuple(out[:3])


def _pcm(path):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                           "-f", "s16le", "-"], check=True, capture_output=True).stdout


def _durasi(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout)


@pytest.fixture
def render(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "TARGET_W", 240)
    monkeypatch.setattr(ar, "TARGET_H", 426)
    monkeypatch.setattr(ar, "THUMBNAIL_ENABLED", False)
    monkeypatch.setattr(ar, "TRIM_SILENCE", False)
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC", "off")
    video = _video_bersuara(tmp_path / "v.mp4")
    # Klip stok palsu: MERAH bertekstur (klip polos kini ditolak sebagai "layar polos").
    bertekstur = tmp_path / "merah.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=red:size=360x640:rate=24:duration=6", "-vf",
                    "drawgrid=w=30:h=30:t=3:c=black", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", str(bertekstur)], check=True, capture_output=True)
    diambil = []

    def palsu(queries, jumlah, orientasi, folder, run_id, awalan="_broll_", saring=True,
              pakai=(), tolak=()):
        import shutil
        tujuan = f"{folder}/{awalan}0.mp4"
        shutil.copy2(bertekstur, tujuan)
        diambil.append(queries)
        return [{"path": tujuan, "id": 1, "durasi": 6, "kredit": "Video oleh X di Pexels",
                 "halaman": "https://pexels.com/x"}], None

    monkeypatch.setattr(broll, "ambil", palsu)
    kata = _kata(per=0.6, mulai=0.5)

    def jalankan(pakai_broll):
        if pakai_broll:
            monkeypatch.setenv("BROLL", "1")
            monkeypatch.setenv("PEXELS_API_KEY", "k")
        else:
            monkeypatch.delenv("BROLL", raising=False)
        data = {"judul": "uji", "audio_mode": "original", "full_voice_over": "x", "media_assets": [video],
                "scenes": [], "transcript_words": {"v.mp4": kata},
                "transcript_segments": {"v.mp4": [{"start": 0.5, "end": 10, "text": " ".join(KATA)}]},
                "broll": [{"query": "blood donation", "saat_kata": "donor"}]}
        s = tmp_path / f"s_{pakai_broll}.json"
        s.write_text(json.dumps(data), encoding="utf-8")
        out = tmp_path / f"h_{pakai_broll}.mp4"
        ar.render_from_agent_script(str(s), str(out))
        return out

    return jalankan, video, kata, diambil


def test_render_cutaway_timpa_sebentar_tanpa_menggeser_suara(render):
    jalankan, video, kata, diambil = render
    tanpa = jalankan(False)
    dengan = jalankan(True)
    donor = next(w["start"] for w in kata if w["word"] == "donor")
    merah = lambda c: c[0] > 180 and c[1] < 80 and c[2] < 80
    status = json.load(open(ar.STATUS_PATH)) if hasattr(ar, "STATUS_PATH") else None
    assert diambil == [["blood donation"]]
    # Cutaway tampil di jendelanya, dan TIDAK sebelum/sesudahnya.
    assert merah(_rgb(dengan, donor + 0.8)) and merah(_rgb(dengan, donor + 2.2))
    assert not merah(_rgb(dengan, donor - 1.0)) and not merah(_rgb(dengan, donor + 4.0))
    # Kontrol: tanpa B-roll, jendela yang sama menampilkan video asli (abu-abu).
    assert not merah(_rgb(tanpa, donor + 0.8))
    # Timeline & suara asli tidak bergeser sama sekali.
    assert _durasi(dengan) == pytest.approx(_durasi(tanpa), abs=0.05)
    # Audio: dibandingkan dengan SUMBER. Jalur cutaway menyalin audio (-c:a copy); jalur tanpa
    # B-roll meng-encode ulang lewat drawtext, jadi keduanya tidak identik bit -- yang dibuktikan:
    # tidak bergeser (lag 0) dan praktis sama (korelasi).
    src = np.frombuffer(_pcm(video), np.int16).astype(float)
    for hasil in (dengan, tanpa):
        x = np.frombuffer(_pcm(hasil), np.int16).astype(float)
        n = min(len(src), len(x))
        assert abs(len(src) - len(x)) < 1600, "durasi audio berubah"
        a, b = src[4000:n - 4000], x[4000:n - 4000]
        lag = int(np.argmax([np.dot(a[800:-800], b[800 + k:len(b) - 800 + k]) for k in range(-40, 41)])) - 40
        # Toleransi 3 ms (48 sampel @16 kHz): jeda priming encoder AAC + sinus periodik; sinkron
        # bibir baru terasa di ±45 ms. Pergeseran nyata (mis. klip disisipkan) = detik.
        assert abs(lag) <= 48, f"audio bergeser {lag} sampel"
        assert np.corrcoef(a, b)[0, 1] > 0.99
    del status


def test_motion_mode_suara_asli_berjangkar_ke_ucapan(monkeypatch, tmp_path):
    """Jangkar divalidasi terhadap kata yang DIUCAPKAN (transkrip), bukan full_voice_over
    (yang di mode suara asli hanya draf caption)."""
    import overlay_remotion as orr
    monkeypatch.setenv("MOTION_GRAPHIC", "sedang")
    ditangkap = {}
    monkeypatch.setattr(orr, "render_motion",
                        lambda items, folder, **k: (ditangkap.setdefault("items", items) and [], {"frame_chromium": 0, "item": len(items)}))
    kata = _kata(per=0.7)
    data = {"full_voice_over": "caption yang tidak diucapkan", "konteks_user": "",
            "motion_plan": {"hook": "Ayo donor", "elemen": [{"jenis": "sorot", "teks": "Aula kampus",
                                                              "saat_kata": "aula"}]}}
    naskah = " ".join(w["word"] for w in kata)
    ar.siapkan_motion(data, 12.0, kata, False, str(tmp_path), naskah=naskah)
    sorot = [i for i in ditangkap["items"] if i["jenis"] == "sorot"]
    aula = next(w["start"] for w in kata if w["word"] == "aula")
    assert sorot and sorot[0]["mulai"] == pytest.approx(aula)
    # Kontrol: dengan naskah = full_voice_over, jangkar 'aula' tidak ada -> elemen dibuang.
    ditangkap.clear()
    ar.siapkan_motion(data, 12.0, kata, False, str(tmp_path))
    assert not [i for i in ditangkap.get("items", []) if i["jenis"] == "sorot"]


def test_prompt_mewajibkan_usulan_broll_bila_user_meminta():
    import agent1_2_brief as ab
    biasa = ab.build_prompt(["a.mp4"], {}, jumlah_gambar=1, mode_audio="original",
                            transkrip={"a.mp4": "halo"})
    diminta = ab.build_prompt(["a.mp4"], {}, jumlah_gambar=1, mode_audio="original",
                              transkrip={"a.mp4": "halo"}, broll_diminta=True)
    assert "MEMINTA B-roll" in diminta and "MEMINTA B-roll" not in biasa


def test_klip_stok_polos_ditolak(tmp_path):
    polos, tekstur = tmp_path / "p.mp4", tmp_path / "t.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:size=200x356:duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(polos)], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=200x356:duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tekstur)], check=True, capture_output=True)
    assert ar.detail_klip(str(polos), 3.0) < ar.DETAIL_MIN <= ar.detail_klip(str(tekstur), 3.0)
