"""Transkripsi audio: model akhirnya MENDENGAR isi video, bukan cuma melihat 1 frame.

Bukti kenapa ini ada — bahan yang sama persis (5 video profil bisnis):
  tanpa audio : "Pesan Positif untuk Generasi Muda" (motivasi generik, karangan)
  dengan audio: "Ketika listing masih dikelola lewat WhatsApp dan spreadsheet..."

Tidak ada panggilan jaringan: conftest memblokirnya, dan transcribe_file di-mock.
"""

import subprocess

import pytest

import agent1_2_brief as brief
import cost_estimate as ce
import transcribe


def buat_video(path, *, dengan_audio, detik=2):
    args = ["ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size=320x240:duration={detik}:rate=10"]
    if dengan_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={detik}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if dengan_audio:
        args += ["-c:a", "aac", "-shortest"]
    args += [str(path)]
    subprocess.run(args, check=True)
    return str(path)


# ---------- deteksi audio ----------

def test_mendeteksi_video_yang_punya_audio(tmp_path):
    assert transcribe.has_audio(buat_video(tmp_path / "ada.mp4", dengan_audio=True)) is True


def test_video_bisu_terdeteksi_tanpa_audio(tmp_path):
    """Tanpa cek ini, video bisu (screen recording, timelapse) membuang kuota."""
    assert transcribe.has_audio(buat_video(tmp_path / "bisu.mp4", dengan_audio=False)) is False


def test_file_tidak_ada_dianggap_tanpa_audio(tmp_path):
    assert transcribe.has_audio(str(tmp_path / "hantu.mp4")) is False


def test_durasi_terbaca(tmp_path):
    d = transcribe.media_duration(buat_video(tmp_path / "v.mp4", dengan_audio=True, detik=3))
    assert 2.5 <= d <= 3.5


# ---------- ekstraksi audio ----------

def test_ekstrak_audio_menghasilkan_file(tmp_path):
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)
    out = tmp_path / "out.mp3"
    assert transcribe.extract_audio(src, str(out)) is True
    assert out.stat().st_size > 0


def test_ekstrak_dari_video_bisu_gagal_tanpa_melempar(tmp_path):
    src = buat_video(tmp_path / "bisu.mp4", dengan_audio=False)
    assert transcribe.extract_audio(src, str(tmp_path / "out.mp3")) is False


# ---------- transcribe_assets ----------

@pytest.fixture
def mock_transcribe(monkeypatch):
    dipanggil = []

    def fake(audio_path, *, durasi=0.0):
        dipanggil.append(durasi)
        return "teks hasil transkrip"

    monkeypatch.setattr(transcribe, "transcribe_file", fake)
    monkeypatch.setattr(transcribe, "OPENAI_API_KEY", "kunci-palsu")
    monkeypatch.setattr(transcribe, "TRANSCRIBE_ENABLED", True)
    return dipanggil


def test_video_beraudio_ditranskrip(tmp_path, mock_transcribe):
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)
    hasil = transcribe.transcribe_assets([src])
    assert hasil == {"v.mp4": "teks hasil transkrip"}


def test_video_bisu_dilewati_tanpa_memanggil_api(tmp_path, mock_transcribe, capsys):
    src = buat_video(tmp_path / "bisu.mp4", dengan_audio=False)
    assert transcribe.transcribe_assets([src]) == {}
    assert mock_transcribe == [], "API tidak boleh dipanggil untuk video bisu"
    assert "tidak punya trek audio" in capsys.readouterr().out


def test_gambar_dilewati(tmp_path, mock_transcribe):
    f = tmp_path / "foto.jpg"
    f.write_bytes(b"bukan video")
    assert transcribe.transcribe_assets([str(f)]) == {}
    assert mock_transcribe == []


def test_bisa_dimatikan_lewat_config(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "TRANSCRIBE_ENABLED", False)
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)
    assert transcribe.transcribe_assets([src]) == {}


def test_tanpa_api_key_tidak_mencoba(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "OPENAI_API_KEY", None)
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)
    assert transcribe.transcribe_assets([src]) == {}


def test_kegagalan_transkrip_tidak_menggagalkan_pipeline(tmp_path, monkeypatch, capsys):
    """Transkripsi adalah pengayaan, bukan syarat."""
    monkeypatch.setattr(transcribe, "OPENAI_API_KEY", "k")
    monkeypatch.setattr(transcribe, "TRANSCRIBE_ENABLED", True)

    def meledak(*a, **k):
        raise RuntimeError("API mati")

    monkeypatch.setattr(transcribe, "transcribe_file", meledak)
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)

    assert transcribe.transcribe_assets([src]) == {}
    assert "gagal" in capsys.readouterr().out


def test_transkrip_kosong_tidak_masuk_hasil(tmp_path, monkeypatch):
    """Pemanggil tidak boleh menyangka ada teks padahal kosong."""
    monkeypatch.setattr(transcribe, "OPENAI_API_KEY", "k")
    monkeypatch.setattr(transcribe, "TRANSCRIBE_ENABLED", True)
    monkeypatch.setattr(transcribe, "transcribe_file", lambda p, durasi=0.0: "   ")
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)
    assert transcribe.transcribe_assets([src]) == {}


def test_jumlah_bahan_dibatasi(tmp_path, mock_transcribe):
    paths = [buat_video(tmp_path / f"{i}.mp4", dengan_audio=True) for i in range(4)]
    assert len(transcribe.transcribe_assets(paths, max_assets=2)) == 2


def test_video_terlalu_panjang_dilewati(tmp_path, mock_transcribe, monkeypatch, capsys):
    monkeypatch.setattr(transcribe, "TRANSCRIBE_MAX_SECONDS", 1)
    src = buat_video(tmp_path / "panjang.mp4", dengan_audio=True, detik=3)
    assert transcribe.transcribe_assets([src]) == {}
    assert "melebihi batas" in capsys.readouterr().out


# ---------- biaya: per MENIT, bukan per token ----------

def test_biaya_dihitung_per_menit():
    harga = {"transcribe": {"m": {"per_minute": 0.6}}}
    assert ce.estimate_transcribe_cost("m", 60, pricing=harga) == 0.6
    assert ce.estimate_transcribe_cost("m", 30, pricing=harga) == 0.3


def test_biaya_model_tak_dikenal_None():
    assert ce.estimate_transcribe_cost("asing", 60, pricing={"transcribe": {}}) is None


def test_whisper_ada_di_pricing_repo():
    assert "whisper-1" in (ce.load_pricing().get("transcribe") or {})


def test_model_transkripsi_yang_dipakai_punya_harga():
    assert transcribe.TRANSCRIBE_MODEL in (ce.load_pricing().get("transcribe") or {})


# ---------- prompt ----------

def test_transkrip_jadi_sumber_kebenaran_utama():
    note = brief.build_transcript_note({"a.mp4": "Saya Steven, web developer."})
    assert "Saya Steven, web developer." in note
    assert "sumber kebenaran UTAMA" in note
    assert "MENANGKAN transkrip" in note


def test_tanpa_transkrip_dinyatakan_jelas():
    note = brief.build_transcript_note({})
    assert "TIDAK ADA transkrip" in note
    assert "Bertumpu pada gambar saja" in note


def test_prompt_memuat_transkrip():
    p = brief.build_prompt(["a.mp4"], None, jumlah_gambar=1,
                           transkrip={"a.mp4": "isi ucapan asli"})
    assert "isi ucapan asli" in p
    assert "BUKAN tebakan" in p
