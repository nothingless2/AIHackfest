"""Mode audio: suara asli user vs voice-over AI, plus subtitle dari transkrip.

Aturan yang diminta user:
    user minta "tanpa suara AI"  DAN  ada ucapan  ->  suara asli
    selain itu                                    ->  voice-over AI

"Ada ucapan" TIDAK ditebak — pendeteksinya hasil transkripsi.
"""

import subprocess

import pytest

import audio_mode as am
import auto_render as ar


# ---------- keputusan mode ----------

def test_minta_asli_dan_ada_ucapan_pakai_suara_asli():
    mode, alasan = am.resolve_audio_mode(am.MODE_ORIGINAL, {"v.mp4": "halo"})
    assert mode == am.MODE_ORIGINAL
    assert "ada ucapan" in alasan


def test_minta_asli_tapi_video_bisu_jatuh_ke_voice_over():
    """Memakai 'suara asli' pada video bisu menghasilkan video sunyi — jatuh ke
    voice-over AI adalah perilaku yang benar, bukan kompromi."""
    mode, alasan = am.resolve_audio_mode(am.MODE_ORIGINAL, {})
    assert mode == am.MODE_AI
    assert "tidak ada ucapan" in alasan


def test_minta_ai_tetap_ai_walau_ada_ucapan():
    mode, _ = am.resolve_audio_mode(am.MODE_AI, {"v.mp4": "halo"})
    assert mode == am.MODE_AI


def test_tanpa_permintaan_tetap_voice_over_ai():
    """Perilaku lama tidak boleh berubah untuk user yang tidak minta apa-apa."""
    mode, alasan = am.resolve_audio_mode(am.MODE_AUTO, {"v.mp4": "halo"})
    assert mode == am.MODE_AI
    assert "default" in alasan


def test_mode_diminta_dari_env(monkeypatch):
    monkeypatch.setenv("CONTENT_FACTORY_AUDIO_MODE", "original")
    assert am.requested_mode() == am.MODE_ORIGINAL


def test_mode_tak_dikenal_jadi_auto_dengan_peringatan(monkeypatch, capsys):
    """Salah ketik di .env tidak boleh diam-diam mengubah perilaku audio."""
    monkeypatch.setenv("CONTENT_FACTORY_AUDIO_MODE", "aslii")
    assert am.requested_mode() == am.MODE_AUTO
    assert "tidak dikenal" in capsys.readouterr().out


# ---------- pembungkusan teks ----------

def test_teks_pendek_tidak_diubah():
    assert ar.wrap_text("Ingin Lebih Efisien?", 52, 1080) == "Ingin Lebih Efisien?"


def test_kalimat_panjang_dibungkus_beberapa_baris():
    t = "Perkenalkan saya Steven seorang web developer yang membantu bisnis membangun website"
    hasil = ar.wrap_text(t, 52, 1080)
    assert "\n" in hasil
    for baris in hasil.split("\n"):
        assert len(baris) <= 34, f"baris terlalu panjang: {baris!r}"


def test_tiap_baris_muat_di_kanvas_sempit():
    hasil = ar.wrap_text("satu dua tiga empat lima enam tujuh", 52, 540)
    for baris in hasil.split("\n"):
        assert len(baris) <= 18


# ---------- pemecahan subtitle ----------

def test_kalimat_panjang_dipecah_bukan_dipotong():
    """Memotong berarti membuang ucapan user."""
    t = " ".join(["kata"] * 60)
    bagian = ar.split_for_subtitle(t, 52, 1080)
    assert len(bagian) > 1
    assert " ".join(bagian).split() == t.split(), "tidak boleh ada kata yang hilang"


def test_kalimat_pendek_tetap_satu_bagian():
    assert ar.split_for_subtitle("halo dunia", 52, 1080) == ["halo dunia"]


# ---------- timing absolut ----------

def test_subtitle_digeser_sesuai_posisi_klip():
    """Whisper memberi waktu relatif tiap klip; di video gabungan harus digeser,
    kalau tidak semua subtitle menumpuk di detik-detik awal."""
    data = {"transcript_segments": {
        "a.mp4": [{"start": 0.0, "end": 2.0, "text": "satu"}],
        "b.mp4": [{"start": 0.5, "end": 2.5, "text": "dua"}],
    }}
    sc = ar.subtitle_scenes(data, ["/x/a.mp4", "/x/b.mp4"], [4.0, 5.0])
    assert sc[0]["start"] == 0.0
    assert sc[1]["start"] == 4.5, "klip kedua harus bergeser sepanjang klip pertama"


def test_subtitle_dijepit_ke_panjang_klip():
    """Transkrip bisa sedikit melewati batas klip; subtitle yang muncul setelah
    klipnya berganti akan menyesatkan."""
    data = {"transcript_segments": {"a.mp4": [{"start": 0.0, "end": 99.0, "text": "x"}]}}
    sc = ar.subtitle_scenes(data, ["/x/a.mp4"], [3.0])
    assert sc[-1]["end"] <= 3.0


def test_tanpa_transkrip_kembalikan_kosong():
    assert ar.subtitle_scenes({}, ["/x/a.mp4"], [3.0]) == []


def test_potongan_kosong_dilewati():
    data = {"transcript_segments": {"a.mp4": [
        {"start": 0.0, "end": 1.0, "text": "  "},
        {"start": 1.0, "end": 0.5, "text": "terbalik"},
    ]}}
    assert ar.subtitle_scenes(data, ["/x/a.mp4"], [5.0]) == []


# ---------- segmen dengan audio ----------

def _punya_audio(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return "audio" in out.stdout


def test_keep_audio_mempertahankan_suara_video(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:duration=2:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)],
        check=True,
    )
    out = tmp_path / "seg.mp4"
    ar.build_segment(str(src), 2.0, str(out), keep_audio=True)
    assert _punya_audio(out)


def test_gambar_dapat_trek_senyap_saat_keep_audio(tmp_path):
    """Concat demuxer dgn -c copy menuntut susunan stream yang sama; satu segmen
    tanpa audio akan merusak penggabungan."""
    src = tmp_path / "foto.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc2=size=320x240:duration=1:rate=1", "-frames:v", "1", str(src)],
        check=True,
    )
    out = tmp_path / "seg.mp4"
    ar.build_segment(str(src), 2.0, str(out), keep_audio=True)
    assert _punya_audio(out)


def test_mode_ai_tetap_membuang_audio_asli(tmp_path):
    """Perilaku lama: voice-over menggantikan audio sumber."""
    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:duration=2:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)],
        check=True,
    )
    out = tmp_path / "seg.mp4"
    ar.build_segment(str(src), 1.0, str(out), keep_audio=False)
    assert not _punya_audio(out)
