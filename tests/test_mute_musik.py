"""Mode mute + musik eksternal (berkas yang diunggah user).

Yang dijaga bukan string filter, tapi HASIL RENDER: berapa aliran audio yang ada, seberapa
keras musiknya, dan bahwa suara asli benar-benar hilang. Musik solo harus mencapai
kenyaringan yang wajar -- MUSIC_VOLUME 0,15 yang dirancang sebagai latar nyaris tak
terdengar bila ia satu-satunya suara.
"""

import json
import os
import subprocess

import pytest

import audio_mode as am
import auto_render as ar
import music as m
import retention


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True, capture_output=True)


def _video_bersuara(path, detik=4, frek=1000):
    """Video dengan 'suara asli' berupa nada 1000 Hz -- mudah dibedakan dari musik 330 Hz."""
    _ffmpeg("-f", "lavfi", "-i", f"color=c=0x50a0c0:size=240x426:rate=10:duration={detik}",
            "-f", "lavfi", "-i", f"sine=frequency={frek}:duration={detik}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-shortest", str(path))
    return str(path)


def _musik(path, detik=30, frek=330):
    _ffmpeg("-f", "lavfi", "-i", f"sine=frequency={frek}:duration={detik}", "-c:a", "libmp3lame", str(path))
    return str(path)


def _streams(path):
    o = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return o.stdout.split()


def _energi_frekuensi(path, band):
    """mean_volume (dB) pada pita SEMPIT di sekitar satu nada: 'low' = 330 Hz (musik),
    'high' = 1000 Hz (suara asli video).

    Versi pertama memakai lowpass/highpass lebar, dan hasilnya menyesatkan: highpass 800 Hz
    meloloskan sisa nada musik 330 Hz (filter 2-kutub hanya meredam ~17 dB), sehingga
    'suara asli bocor -35 dB' sebenarnya bocoran musik itu sendiri. Pita sempit
    memisahkan kedua nada dengan bersih."""
    pusat = 330 if band == "low" else 1000
    filt = f"bandpass=f={pusat}:width_type=h:width=60"
    o = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", f"{filt},volumedetect",
                        "-vn", "-f", "null", "-"], capture_output=True, text=True)
    import re
    mm = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", o.stderr)
    return float(mm.group(1)) if mm else -99.0


@pytest.fixture
def render(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "TARGET_W", 240)
    monkeypatch.setattr(ar, "TARGET_H", 426)
    monkeypatch.setattr(ar, "THUMBNAIL_ENABLED", False)
    monkeypatch.setattr(ar, "TRIM_SILENCE", False)
    monkeypatch.delenv("CONTENT_FACTORY_MUSIC", raising=False)
    monkeypatch.delenv("CONTENT_FACTORY_MUSIC_FILE", raising=False)
    monkeypatch.setattr(m, "MUSIC_DIR", str(tmp_path / "pustaka_kosong"))   # tanpa pustaka
    video = _video_bersuara(tmp_path / "v.mp4")

    def jalankan(mode, musik=None, **tambahan):
        if musik:
            monkeypatch.setenv("CONTENT_FACTORY_MUSIC_FILE", musik)
        data = {"judul": "uji", "audio_mode": mode, "full_voice_over": "x",
                "media_assets": [video], "scenes": [], **tambahan}
        s = tmp_path / f"s_{mode}.json"
        s.write_text(json.dumps(data), encoding="utf-8")
        out = tmp_path / f"h_{mode}_{bool(musik)}.mp4"
        ar.render_from_agent_script(str(s), str(out))
        return str(out)

    return jalankan, tmp_path


# ---------- mode ----------

def test_mute_adalah_mode_yang_dikenal():
    assert am.MODE_MUTE in am.VALID_MODES


def test_mute_tidak_pernah_jatuh_ke_suara_ai_meski_tanpa_ucapan():
    mode, alasan = am.resolve_audio_mode(am.MODE_MUTE, {}, gagal={"a": "tanpa_ucapan"})
    assert mode == am.MODE_MUTE and "dibisukan" in alasan


def test_mute_tidak_dihentikan_kegagalan_transkripsi():
    """Suara asli memang tidak dipakai; kegagalan transkripsi hanya berarti tanpa subtitle,
    bukan run yang dihentikan (beda dengan mode original: aturan #7 CLAUDE.md)."""
    mode, _ = am.resolve_audio_mode(am.MODE_MUTE, {}, gagal={"a": "kuota_habis"})
    assert mode == am.MODE_MUTE


def test_original_tetap_dihentikan_kegagalan_transkripsi():
    with pytest.raises(am.TranscriptionUnavailable):
        am.resolve_audio_mode(am.MODE_ORIGINAL, {}, gagal={"a": "kuota_habis"})


# ---------- render nyata ----------

def test_mute_tanpa_musik_menghasilkan_video_tanpa_audio(render):
    jalankan, _ = render
    hasil = jalankan("mute")
    assert "audio" not in _streams(hasil), "suara asli harus benar-benar hilang"
    assert "video" in _streams(hasil)


def test_mute_dengan_musik_eksternal_hanya_musik_yang_terdengar(render, tmp_path):
    jalankan, _ = render
    lagu = _musik(tmp_path / "lagu.mp3")
    hasil = jalankan("mute", musik=lagu)
    assert "audio" in _streams(hasil)
    musik_db = _energi_frekuensi(hasil, "low")       # 330 Hz = musik
    suara_asli_db = _energi_frekuensi(hasil, "high")  # 1000 Hz = suara asli video
    assert musik_db > -30, f"musik nyaris tak terdengar: {musik_db} dB"
    assert suara_asli_db < musik_db - 25, (
        f"suara asli masih bocor: musik {musik_db} dB vs suara asli {suara_asli_db} dB")


def test_kontrol_positif_mode_original_membawa_suara_asli(render, tmp_path):
    """Kontrol untuk tes di atas: tanpa mute, alat ukur yang sama HARUS mendengar nada
    1000 Hz milik video. Tanpa ini 'suara asli hilang' bisa berarti alat ukurnya buta."""
    jalankan, _ = render
    hasil = jalankan("original")
    assert "audio" in _streams(hasil)
    assert _energi_frekuensi(hasil, "high") > -35, "alat ukur harus mendengar nada 1000 Hz milik video"


def test_musik_solo_mencapai_kenyaringan_target(render, tmp_path):
    """Musik yang jadi SATU-SATUNYA suara harus di sekitar -16 LUFS, bukan MUSIC_VOLUME
    0,15 (~ -40 LUFS) yang dirancang sebagai latar."""
    jalankan, _ = render
    lagu = _musik(tmp_path / "lagu.mp3")
    hasil = jalankan("mute", musik=lagu)
    lufs = m.loudness(hasil)
    assert lufs is not None and abs(lufs - m.SOLO_TARGET_LUFS) < 3, f"{lufs} LUFS"


def test_musik_yang_terlalu_keras_diturunkan_ke_target(tmp_path):
    keras = tmp_path / "keras.mp3"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:duration=10", "-af", "volume=15dB", "-c:a", "libmp3lame", str(keras))
    lufs_asal = m.loudness(str(keras))
    gain = m.solo_volume(str(keras))
    assert gain < 1.0, "musik yang sudah keras harus dikecilkan"
    assert abs((lufs_asal + 20 * __import__("math").log10(gain)) - m.SOLO_TARGET_LUFS) < 1.0


def test_musik_lebih_pendek_dari_video_di_loop(render, tmp_path):
    jalankan, _ = render
    lagu = _musik(tmp_path / "pendek.mp3", detik=1)
    hasil = jalankan("mute", musik=lagu)
    o = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", hasil],
                       capture_output=True, text=True)
    assert float(o.stdout) == pytest.approx(4.0, abs=0.4), "durasi tidak boleh ikut memendek"
    assert _energi_frekuensi(hasil, "low") > -35, "musik pendek harus diulang sampai akhir"


def test_musik_lebih_panjang_dari_video_tidak_memanjangkan_hasil(render, tmp_path):
    jalankan, _ = render
    lagu = _musik(tmp_path / "panjang.mp3", detik=60)
    hasil = jalankan("mute", musik=lagu)
    o = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", hasil],
                       capture_output=True, text=True)
    assert float(o.stdout) == pytest.approx(4.0, abs=0.4)


# ---------- berkas musik eksternal ----------

def test_berkas_eksternal_mengalahkan_pustaka_dan_mood(tmp_path, monkeypatch):
    pustaka = tmp_path / "pustaka"
    pustaka.mkdir()
    _musik(pustaka / "lofi_tenang.mp3")
    lagu = _musik(tmp_path / "punya_user.mp3")
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC_FILE", lagu)
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC_MOOD", "lofi")
    assert m.pick_track(m.requested_mood(), run_id="x") == lagu


def test_berkas_eksternal_hilang_ditolak_bukan_diganti_lagu_lain(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC_FILE", str(tmp_path / "tidak_ada.mp3"))
    with pytest.raises(m.MusicError) as e:
        m.pick_track(None)
    assert "tidak ditemukan" in str(e.value)


def test_berkas_eksternal_tanpa_audio_ditolak(tmp_path, monkeypatch):
    palsu = tmp_path / "bukan_lagu.mp3"
    palsu.write_bytes(b"ini bukan audio")
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC_FILE", str(palsu))
    with pytest.raises(m.MusicError) as e:
        m.pick_track(None)
    assert "audio" in str(e.value)


def test_musik_eksternal_dipakai_meski_music_enabled_mati(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "MUSIC_ENABLED", False)
    monkeypatch.delenv("CONTENT_FACTORY_MUSIC", raising=False)
    assert m.music_wanted() is False
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC_FILE", _musik(tmp_path / "x.mp3"))
    assert m.music_wanted() is True


def test_user_bilang_tanpa_musik_mengalahkan_berkas(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC_FILE", _musik(tmp_path / "x.mp3"))
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC", "off")
    assert m.music_wanted() is False


# ---------- brief ----------

def test_prompt_mode_mute_menyebut_suara_dibisukan():
    import agent1_2_brief as brief
    p = brief.build_prompt(["a.mp4"], {}, jumlah_gambar=1, durasi_bahan=[5.0], audio_bisu=True)
    assert "DIBISUKAN" in p and "SUARA ASLI dipertahankan" not in p


def test_prompt_mode_original_tidak_berubah():
    import agent1_2_brief as brief
    p = brief.build_prompt(["a.mp4"], {}, jumlah_gambar=1, durasi_bahan=[5.0])
    assert "SUARA ASLI dipertahankan" in p and "DIBISUKAN" not in p


# ---------- retensi ----------

def test_sweep_membuang_musik_user_yang_tua(tmp_path, monkeypatch):
    state = tmp_path / "workspace" / "state"
    musik = tmp_path / "workspace" / "music_user"
    state.mkdir(parents=True)
    musik.mkdir()
    tua, baru = musik / "tua.mp3", musik / "baru.mp3"
    for f in (tua, baru):
        f.write_bytes(b"x")
    lama = 30 * 86400
    os.utime(tua, (os.path.getmtime(tua) - lama, os.path.getmtime(tua) - lama))
    monkeypatch.setattr(retention, "STATE_DIR", str(state))
    monkeypatch.setattr(retention, "DRAFTS_DIR", str(tmp_path / "drafts"))
    retention.sweep_old_run_files(max_age_days=7)
    assert not tua.exists() and baru.exists()
