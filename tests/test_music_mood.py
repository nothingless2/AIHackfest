"""Analisis mood musik (scripts/music_mood.py).

Tes memakai sinyal SINTETIS dengan tempo/kecerahan/denyut yang diketahui -- kontrol
positif dan negatif. Ambang label adalah heuristik (lihat docstring modul); yang dijaga
di sini adalah ukurannya: tempo akurat, dan ketiadaan denyut TIDAK dilaporkan sebagai
denyut. Lagu asli milik user tidak dipakai di tes (aturan #6).
"""

import subprocess

import numpy as np
import pytest

import music_mood as mm

SR = 22050


def _wav(tmp_path, nama, x):
    raw = tmp_path / f"{nama}.f32"
    np.asarray(x, dtype=np.float32).tofile(raw)
    out = str(tmp_path / f"{nama}.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1",
                    "-i", str(raw), out], check=True, capture_output=True)
    return out


def _klik(bpm, dur=30, amp=0.6, f=1200):
    x = np.zeros(SR * dur)
    t = np.arange(int(0.03 * SR)) / SR
    burst = np.sin(2 * np.pi * f * t) * np.exp(-t * 120)
    for s in range(0, len(x) - len(burst), int(SR * 60 / bpm)):
        x[s:s + len(burst)] += amp * burst
    return x


@pytest.mark.parametrize("bpm", [70, 80, 90, 100, 120, 140, 160])
def test_tempo_klik_akurat_termasuk_tempo_cepat(tmp_path, bpm):
    """Termasuk 120-160: sebelum skor harmonik + pemecah seri oktaf, 120/140/160 terbaca
    60/70/80 (setengahnya)."""
    a = mm.analisis(_wav(tmp_path, f"k{bpm}", _klik(bpm)))
    assert a["tempo_yakin"]
    assert abs(a["tempo_bpm"] - bpm) <= 2.0, a


def test_nada_steady_bukan_denyut(tmp_path):
    """Regresi terukur: nada 220 Hz murni pernah dibaca 'yakin 112 BPM'."""
    x = 0.3 * np.sin(2 * np.pi * 220 * np.arange(SR * 30) / SR)
    a = mm.analisis(_wav(tmp_path, "pad", x))
    assert a["tempo_yakin"] is False
    assert a["mood"] == "tenang"           # rendah, tanpa denyut


def test_derau_bukan_denyut(tmp_path):
    x = 0.2 * np.random.default_rng(1).standard_normal(SR * 30)
    a = mm.analisis(_wav(tmp_path, "derau", x))
    assert a["tempo_yakin"] is False


def test_tanpa_denyut_tidak_pernah_energik_atau_upbeat(tmp_path):
    for nama, x in {
        "pad": 0.3 * np.sin(2 * np.pi * 220 * np.arange(SR * 30) / SR),
        "derau": 0.2 * np.random.default_rng(2).standard_normal(SR * 30),
    }.items():
        assert mm.analisis(_wav(tmp_path, nama, x))["mood"] in ("tenang", "santai")


def test_pola_drum_lofi_sintetik_terbaca_dekat_tempo_aslinya(tmp_path):
    """Kick di ketukan 1 & 3, snare (derau) di 2 & 4, hat 8th, plus pad: pola musik,
    bukan sekadar klik. 82 BPM ~ tempo lofi."""
    bpm, dur = 82, 40
    x = 0.05 * np.sin(2 * np.pi * 196 * np.arange(SR * dur) / SR)
    rng = np.random.default_rng(3)
    ketuk = SR * 60 / bpm
    for i in range(int(dur * bpm / 60)):
        s = int(i * ketuk)
        tk = np.arange(int(0.12 * SR)) / SR
        if i % 4 in (0, 2):
            x[s:s + len(tk)] += 0.7 * np.sin(2 * np.pi * 60 * tk) * np.exp(-tk * 25)
        else:
            x[s:s + len(tk)] += 0.4 * rng.standard_normal(len(tk)) * np.exp(-tk * 30)
        h = s + int(ketuk / 2)
        th = np.arange(int(0.03 * SR)) / SR
        x[h:h + len(th)] += 0.15 * rng.standard_normal(len(th)) * np.exp(-th * 150)
    a = mm.analisis(_wav(tmp_path, "lofi", x))
    assert a["tempo_yakin"]
    # oktaf setengah/ganda sama-sama sah untuk pola ini; yang dijaga: bukan tempo ngawur
    assert any(abs(a["tempo_bpm"] - k * bpm) <= 3 for k in (0.5, 1, 2)), a


@pytest.mark.parametrize("bpm,yakin,energi,terang,harapan", [
    (80, True, -30, 1500, "tenang"),
    (100, True, -30, 1500, "santai"),
    (80, True, -30, 3000, "santai"),        # pelan tapi terang: bukan 'tenang'
    (118, True, -30, 1500, "upbeat"),
    (140, True, -15, 3000, "energik"),
    (140, True, -35, 3000, "upbeat"),       # cepat tapi pelan: bukan 'energik'
    (140, False, -15, 3000, "santai"),      # tempo tak yakin TIDAK boleh jadi energik
    (None, False, -15, 800, "tenang"),
])
def test_label_mood(bpm, yakin, energi, terang, harapan):
    assert mm.label_mood(bpm, yakin, energi, terang) == harapan


def test_ringkas_jujur_soal_tempo_tak_yakin():
    assert "tempo ~92 BPM" in mm.ringkas({"tempo_bpm": 92, "tempo_yakin": True, "mood": "santai"})
    kalimat = mm.ringkas({"tempo_bpm": 92, "tempo_yakin": False, "mood": "tenang"})
    assert "92" not in kalimat and "tanpa denyut" in kalimat


def test_berkas_rusak_dan_terlalu_pendek_melempar(tmp_path):
    rusak = tmp_path / "rusak.mp3"
    rusak.write_bytes(b"bukan audio")
    with pytest.raises(mm.MoodError):
        mm.analisis(str(rusak))
    with pytest.raises(mm.MoodError, match="pendek"):
        mm.analisis(_wav(tmp_path, "pendek", np.zeros(SR)))


def test_cache_tidak_menganalisis_ulang_dan_berubah_saat_berkas_berubah(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "_cache_path", lambda: str(tmp_path / "cache.json"))
    panggilan = []
    monkeypatch.setattr(mm, "analisis", lambda p: panggilan.append(p) or {"mood": "tenang"})
    lagu = tmp_path / "a.wav"
    lagu.write_bytes(b"1234")
    assert mm.analisis_cached(str(lagu)) == {"mood": "tenang"}
    mm.analisis_cached(str(lagu))
    assert len(panggilan) == 1, "panggilan kedua harus dari cache"
    lagu.write_bytes(b"123456789")
    mm.analisis_cached(str(lagu))
    assert len(panggilan) == 2, "berkas berubah -> dianalisis ulang"


# ---------------------------------------------------------------- integrasi

def test_pick_track_mencocokkan_mood_lewat_analisis_bila_nama_tidak_menyebutnya(tmp_path):
    import music
    pustaka = tmp_path / "lib"
    pustaka.mkdir()
    import shutil
    shutil.copy(_wav(tmp_path, "x", _klik(70)), pustaka / "trek_a.wav")
    shutil.copy(_wav(tmp_path, "y", _klik(140)), pustaka / "trek_b.wav")
    assert music.pick_track("tenang", folder=str(pustaka)).endswith("trek_a.wav")
    assert music.pick_track("upbeat", folder=str(pustaka)).endswith("trek_b.wav")


def test_mood_tanpa_track_cocok_tetap_ditolak_bukan_diganti_lagu_lain(tmp_path):
    import music
    pustaka = tmp_path / "lib"
    pustaka.mkdir()
    import shutil
    shutil.copy(_wav(tmp_path, "x", _klik(70)), pustaka / "trek_a.wav")
    with pytest.raises(music.MusicError):
        music.pick_track("energik", folder=str(pustaka))


def test_kata_bebas_di_luar_kosakata_label_tidak_dicocokkan_lewat_analisis(tmp_path):
    """"lofi" bukan label analisis: tetap mensyaratkan nama berkas."""
    import music
    pustaka = tmp_path / "lib"
    pustaka.mkdir()
    import shutil
    shutil.copy(_wav(tmp_path, "x", _klik(70)), pustaka / "trek_a.wav")
    with pytest.raises(music.MusicError):
        music.pick_track("lofi", folder=str(pustaka))


def test_nama_berkas_tetap_didahulukan_dari_analisis(tmp_path):
    import music, shutil
    pustaka = tmp_path / "lib"
    pustaka.mkdir()
    shutil.copy(_wav(tmp_path, "x", _klik(70)), pustaka / "lagu_upbeat.wav")   # terdengar tenang
    assert music.pick_track("upbeat", folder=str(pustaka)).endswith("lagu_upbeat.wav")


def test_inspect_melaporkan_mood_musik_user(tmp_path):
    import inspect_media as im
    info = im.probe_clip(_wav(tmp_path, "m", _klik(90)))
    assert info["jenis"] == "musik"
    assert info["mood"]["mood"] in mm.MOODS
    assert "tempo ~90 BPM" in info["mood_ringkas"]
    assert "tempo ~90 BPM" in im._fmt_klip(1, info)


def test_inspect_tidak_gagal_kalau_analisis_mood_error(tmp_path, monkeypatch):
    import inspect_media as im
    monkeypatch.setattr(mm, "analisis", lambda p: (_ for _ in ()).throw(mm.MoodError("rusak")))
    info = im.probe_clip(_wav(tmp_path, "m2", _klik(90)))
    assert info["error"] is None and "mood_error" in info and "mood_ringkas" not in info
