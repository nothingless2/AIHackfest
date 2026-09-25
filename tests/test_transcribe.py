"""Transkripsi audio: model akhirnya MENDENGAR isi video, bukan cuma melihat 1 frame.

Bukti kenapa ini ada — bahan yang sama persis (5 video profil bisnis):
  tanpa audio : "Pesan Positif untuk Generasi Muda" (motivasi generik, karangan)
  dengan audio: "Ketika listing masih dikelola lewat WhatsApp dan spreadsheet..."

Tidak ada panggilan jaringan: conftest memblokirnya, dan transcribe_file_detailed di-mock.
"""

import subprocess

import pytest

import agent1_2_brief as brief
import cost_estimate as ce
import transcribe
import transcribe as t


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


# ---------- transkripsi per bahan (transcribe_assets_report) ----------


def teks_bahan(paths):
    """{nama: teks} dari fungsi yang dipakai pipeline."""
    return {n: d["text"] for n, d in transcribe.transcribe_assets_report(paths)[0].items()}

@pytest.fixture
def mock_transcribe(monkeypatch):
    dipanggil = []

    def fake(audio_path, *, durasi=0.0, vocab_prompt=None):
        dipanggil.append(durasi)
        return {"text": "teks hasil transkrip",
                "segments": [{"start": 0.0, "end": 1.0, "text": "teks hasil transkrip"}],
                "words": [{"start": 0.0, "end": 1.0, "word": "teks"}],
                "language": "indonesian"}

    monkeypatch.setattr(transcribe, "transcribe_file_detailed", fake)
    monkeypatch.setattr(transcribe, "OPENAI_API_KEY", "kunci-palsu")
    monkeypatch.setattr(transcribe, "TRANSCRIBE_ENABLED", True)
    return dipanggil


def test_video_beraudio_ditranskrip(tmp_path, mock_transcribe):
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)
    hasil = teks_bahan([src])
    assert hasil == {"v.mp4": "teks hasil transkrip"}


def test_video_bisu_dilewati_tanpa_memanggil_api(tmp_path, mock_transcribe, capsys):
    src = buat_video(tmp_path / "bisu.mp4", dengan_audio=False)
    assert teks_bahan([src]) == {}
    assert mock_transcribe == [], "API tidak boleh dipanggil untuk video bisu"
    assert "tidak punya trek audio" in capsys.readouterr().out


def test_gambar_dilewati(tmp_path, mock_transcribe):
    f = tmp_path / "foto.jpg"
    f.write_bytes(b"bukan video")
    assert teks_bahan([str(f)]) == {}
    assert mock_transcribe == []


def test_bisa_dimatikan_lewat_config(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "TRANSCRIBE_ENABLED", False)
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)
    assert teks_bahan([src]) == {}


def test_tanpa_api_key_tidak_mencoba(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "OPENAI_API_KEY", None)
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)
    assert teks_bahan([src]) == {}


def test_kegagalan_transkrip_tidak_menggagalkan_pipeline(tmp_path, monkeypatch, capsys):
    """Transkripsi adalah pengayaan, bukan syarat."""
    monkeypatch.setattr(transcribe, "OPENAI_API_KEY", "k")
    monkeypatch.setattr(transcribe, "TRANSCRIBE_ENABLED", True)

    def meledak(*a, **k):
        raise RuntimeError("API mati")

    monkeypatch.setattr(transcribe, "transcribe_file_detailed", meledak)
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)

    assert teks_bahan([src]) == {}
    assert "gagal" in capsys.readouterr().out


def test_transkrip_kosong_tidak_masuk_hasil(tmp_path, monkeypatch):
    """Pemanggil tidak boleh menyangka ada teks padahal kosong."""
    monkeypatch.setattr(transcribe, "OPENAI_API_KEY", "k")
    monkeypatch.setattr(transcribe, "TRANSCRIBE_ENABLED", True)
    monkeypatch.setattr(transcribe, "transcribe_file_detailed",
                        lambda p, durasi=0.0, vocab_prompt=None: {"text": "   ", "segments": []})
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)
    assert teks_bahan([src]) == {}


def test_jumlah_bahan_dibatasi(tmp_path, mock_transcribe):
    paths = [buat_video(tmp_path / f"{i}.mp4", dengan_audio=True) for i in range(4)]
    assert len(transcribe.transcribe_assets_report(paths, max_assets=2)[0]) == 2


def test_video_terlalu_panjang_dilewati(tmp_path, mock_transcribe, monkeypatch, capsys):
    monkeypatch.setattr(transcribe, "TRANSCRIBE_MAX_SECONDS", 1)
    src = buat_video(tmp_path / "panjang.mp4", dengan_audio=True, detik=3)
    assert teks_bahan([src]) == {}
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
    """Yang dijaga: dinyatakan terang-terangan, bertumpu pada yang TERLIHAT, dan
    melarang mengarang dialog. (Kata "transkripsi gagal" sengaja dihapus dari
    daftar penyebab: kegagalan sekarang menghentikan run, jadi sampai di sini
    berarti Whisper BERJALAN dan memang tidak menemukan ucapan.)"""
    note = brief.build_transcript_note({})
    assert "TIDAK ADA ucapan" in note
    assert "TERLIHAT" in note
    assert "mengarang dialog" in note
    assert "gagal" not in note.lower()


def test_prompt_memuat_transkrip():
    p = brief.build_prompt(["a.mp4"], None, jumlah_gambar=1,
                           transkrip={"a.mp4": "isi ucapan asli"})
    assert "isi ucapan asli" in p
    assert "BUKAN tebakan" in p


# ---------- timestamp untuk subtitle ----------

def test_detailed_menyertakan_potongan_bertimestamp(tmp_path, mock_transcribe):
    """Timestamp asli inilah yang membuat subtitle bisa pas dengan ucapan user,
    alih-alih memakai timing karangan LLM yang tidak terkait audio."""
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)
    hasil = transcribe.transcribe_assets_report([src])[0]

    data = hasil["v.mp4"]
    assert data["text"] == "teks hasil transkrip"
    assert data["segments"][0]["start"] == 0.0
    assert data["segments"][0]["end"] == 1.0
    assert data["duration"] > 0


def test_transcribe_assets_tetap_mengembalikan_teks_saja(tmp_path, mock_transcribe):
    """Pemanggil lama (prompt brief) tidak boleh ikut berubah."""
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)
    assert teks_bahan([src]) == {"v.mp4": "teks hasil transkrip"}


# ---------- bias kosakata: "leads" jangan jadi "lid" ----------

def test_prompt_bias_memuat_istilah_serapan():
    """Bahasa Indonesia lisan penuh serapan Inggris; tanpa bias, Whisper
    menuliskannya fonetis. Diuji pada audio nyata user:
        tanpa prompt : "dan ketika semua lid masuk"
        dengan prompt: "dan ketika semua lead masuk"
    """
    p = transcribe.build_vocab_prompt()
    for istilah in ("leads", "listing", "closing", "follow up"):
        assert istilah in p, istilah


def test_prompt_bias_menyertakan_konteks_user():
    """User menyebut domainnya sendiri; itu membantu Whisper memilih ejaan."""
    p = transcribe.build_vocab_prompt("konten promo UMKM properti")
    assert "UMKM properti" in p


def test_prompt_bias_dipotong_agar_tidak_melebihi_batas():
    p = transcribe.build_vocab_prompt("x" * 5000)
    assert len(p) <= transcribe.TRANSCRIBE_PROMPT_MAX_CHARS


def test_prompt_bias_benar_benar_dikirim_ke_api(tmp_path, monkeypatch):
    """Penjaga: prompt yang tidak sampai ke API tidak memperbaiki apa pun."""
    terlihat = {}

    def fake(audio_path, *, durasi=0.0, vocab_prompt=None):
        terlihat["prompt"] = vocab_prompt
        return {"text": "halo", "segments": [], "words": [], "language": "indonesian"}

    monkeypatch.setattr(transcribe, "transcribe_file_detailed", fake)
    monkeypatch.setattr(transcribe, "OPENAI_API_KEY", "k")
    monkeypatch.setattr(transcribe, "TRANSCRIBE_ENABLED", True)

    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)
    transcribe.transcribe_assets_report([src], konteks="properti")[0]

    assert terlihat["prompt"] is not None
    assert "leads" in terlihat["prompt"]
    assert "properti" in terlihat["prompt"]


def test_bahasa_terdeteksi_ikut_dicatat(tmp_path, mock_transcribe):
    """Bahasa DIDETEKSI Whisper, bukan diasumsikan."""
    src = buat_video(tmp_path / "v.mp4", dengan_audio=True)
    hasil = transcribe.transcribe_assets_report([src])[0]
    assert hasil["v.mp4"]["language"] == "indonesian"


# ---------- anggaran waktu transkripsi ----------

def _bahan_palsu(tmp_path, jumlah):
    """File video tiruan; has_audio/extract_audio/media_duration di-stub."""
    return [str(tmp_path / f"klip{i}.mp4") for i in range(jumlah)]


@pytest.fixture
def transkrip_terkendali(monkeypatch, tmp_path):
    monkeypatch.setattr(t, "has_audio", lambda p: True)
    monkeypatch.setattr(t, "media_duration", lambda p: 5.0)

    def extract(src, dst):
        open(dst, "wb").write(b"x")
        return dst

    monkeypatch.setattr(t, "extract_audio", extract)
    monkeypatch.setattr(t, "OPENAI_API_KEY", "kunci-palsu")
    monkeypatch.setattr(t, "TRANSCRIBE_ENABLED", True)
    return monkeypatch


def test_yang_lambat_dilepas_yang_cepat_tetap_dipakai(transkrip_terkendali, tmp_path):
    """Kegagalan nyata 19 Sep: transkripsi lambat membuat SELURUH tahap dibunuh
    timeout tanpa satu pun hasil. Sekarang yang sempat selesai tetap dipakai."""
    import time as _t

    def transkrip(path, *, durasi=0.0, vocab_prompt=None):
        # Setengah berkas lambat, setengah cepat — dibedakan dari isi namanya.
        urutan = int(open(path, "rb").read().decode() or 0)
        if urutan >= 2:
            _t.sleep(2)
        return {"text": f"ucapan {urutan}", "segments": [], "words": []}

    def extract(src, dst):
        open(dst, "w").write(src[-5])  # simpan nomor klip ke berkas audio
        return dst

    transkrip_terkendali.setattr(t, "extract_audio", extract)
    transkrip_terkendali.setattr(t, "transcribe_file_detailed", transkrip)
    transkrip_terkendali.setattr(t, "TRANSCRIBE_BUDGET", 0.5)

    hasil = t.transcribe_assets_report(_bahan_palsu(tmp_path, 4))[0]

    assert len(hasil) == 2, "yang cepat harus tetap terpakai"
    assert all("ucapan" in v["text"] for v in hasil.values())


def test_anggaran_habis_tidak_melempar(transkrip_terkendali, tmp_path):
    """Pipeline harus LANJUT tanpa transkrip, bukan gagal."""
    import time as _t

    def lambat(path, *, durasi=0.0, vocab_prompt=None):
        _t.sleep(2)

    transkrip_terkendali.setattr(t, "transcribe_file_detailed", lambat)
    transkrip_terkendali.setattr(t, "TRANSCRIBE_BUDGET", 0.5)

    assert t.transcribe_assets_report(_bahan_palsu(tmp_path, 3))[0] == {}


def test_batas_waktu_nyata_tidak_melebihi_anggaran():
    """Penjaga yang paling mudah dilanggar tanpa sadar.

    future.cancel() tidak mempan untuk permintaan yang sudah jalan, dan
    ThreadPoolExecutor menunggu thread pekerjanya saat ditutup. Jadi batas waktu
    transkripsi yang sebenarnya = timeout per permintaan x jumlah percobaan.
    Kalau angka itu melebihi anggaran, 'anggaran' cuma tulisan.
    """
    assert t.TRANSCRIBE_TIMEOUT * t.TRANSCRIBE_MAX_ATTEMPTS <= t.TRANSCRIBE_BUDGET


def test_timeout_melebihi_latensi_terukur():
    """66,9 dtk untuk satu klip 5,6 dtk pernah terukur lewat relay, dan 5 klip
    paralel pernah 188 dtk. Timeout yang lebih pendek membunuh panggilan yang
    sebenarnya akan berhasil -- itulah kegagalan 19 Sep."""
    assert t.TRANSCRIBE_TIMEOUT >= 120
