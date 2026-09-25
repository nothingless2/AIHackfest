"""Durasi yang diminta user + aturan 'durasi video = durasi audio'.

Test durasi di sini sengaja ME-RENDER SUNGGUHAN dan mengukur hasilnya dengan
ffprobe. Alasannya: pemeriksaan yang hanya mencocokkan rumus akan tetap lolos
untuk rumus yang salah (`max(...)` membuat video lebih panjang dari audio,
`min(...)` jauh lebih pendek) — yang perlu dibuktikan adalah videonya, bukan
aritmetikanya.
"""

import json
import subprocess

import pytest

import duration as d
import spoken as sp
from alokasi import susun_potongan
import auto_render as ar


# ---------- parsing & penjepitan ----------

@pytest.mark.parametrize("teks,harap", [
    ("bikin 15 detik saja", 15),
    ("tolong 30 dtk", 30),
    ("make it 45 seconds", 45),
    ("20s aja", 20),
    ("videonya jangan kepanjangan", None),
    ("", None),
])
def test_parsing_durasi(teks, harap):
    assert d.parse_duration(teks) == harap


def test_selera_tanpa_angka_tidak_ditebak():
    """'pendek' bukan angka. Menebaknya berarti membuat durasi yang tidak diminta."""
    assert d.parse_duration("bikin yang pendek dan padat") is None


@pytest.mark.parametrize("minta,dipakai", [(5, 10), (9, 10), (10, 10), (60, 60), (90, 60)])
def test_di_luar_rentang_dijepit_bukan_ditolak(minta, dipakai):
    detik, pesan = d.clamp_duration(minta)
    assert detik == dipakai
    if minta != dipakai:
        assert str(dipakai) in pesan, "user harus diberi tahu nilai yang dipakai"


def test_parameter_tool_menang_atas_regex(monkeypatch):
    monkeypatch.setenv("CONTENT_FACTORY_DURATION", "15")
    monkeypatch.setenv("CONTENT_FACTORY_USER_CONTEXT", "kemarin 45 detik")
    assert d.requested_duration()[0] == 15


def test_tanpa_permintaan_tidak_ada_target(monkeypatch):
    monkeypatch.delenv("CONTENT_FACTORY_DURATION", raising=False)
    monkeypatch.setenv("CONTENT_FACTORY_USER_CONTEXT", "bikin konten dari video ini")
    assert d.requested_duration() == (None, None)


def test_kalimat_prompt_lama_tidak_berubah():
    """Tanpa permintaan durasi, brief harus berbunyi persis seperti sebelum 2.1."""
    assert d.duration_text(None) == "20-35 detik (kira-kira 55-95 kata)"


def test_target_kata_ikut_durasi():
    kmin, kmax = d.word_target(15)
    assert kmin < 15 * d.WORDS_PER_SECOND < kmax
    assert d.word_target(30)[0] > d.word_target(15)[1]


@pytest.mark.parametrize("aktual,target,meleset", [
    (15.0, 15, False), (17.9, 15, False), (18.5, 15, True), (11.0, 15, True),
    (99.0, None, False),
])
def test_ambang_toleransi(aktual, target, meleset):
    assert d.off_target(aktual, target) is meleset


# ---------- pembagian durasi ----------

def _foto(n):
    return [{"path": f"f{i}.jpg", "foto": True} for i in range(n)]


def test_per_klip_tepat_membagi_habis():
    pot, _ = susun_potongan(_foto(3), 30.0)
    assert len(pot) == 3
    assert sum(p["durasi"] for p in pot) == pytest.approx(30.0), "durasi video harus SAMA dengan audio"


def test_aset_dikurangi_bukan_per_klip_dinaikkan():
    """10 detik untuk 8 aset: yang dipotong jumlah asetnya, bukan durasinya —
    menaikkan per_clip membuat video melebihi audio lalu -shortest memotongnya."""
    pot, info = susun_potongan(_foto(8), 10.0, min_klip=ar.MIN_CLIP_DURATION)
    assert len(pot) < 8 and info["potongan_dibuang"] == 8 - len(pot)
    assert min(p["durasi"] for p in pot) >= ar.MIN_CLIP_DURATION
    assert sum(p["durasi"] for p in pot) == pytest.approx(10.0)


def test_audio_sangat_pendek_tetap_satu_aset():
    pot, _ = susun_potongan(_foto(2), 1.0)
    assert len(pot) == 1 and sum(p["durasi"] for p in pot) == pytest.approx(1.0)


# ---------- scene dijepit ----------

def test_scene_melewati_akhir_dipotong():
    assert ar.clamp_scenes([{"start": 0, "end": 30, "text": "x"}], 22)[0]["end"] == 22


def test_scene_yang_mulai_setelah_akhir_dibuang():
    """Scene ini tidak akan pernah tampil; menyimpannya menyembunyikan salah timing."""
    assert ar.clamp_scenes([{"start": 25, "end": 28, "text": "x"}], 22) == []


# ---------- naskah tulis vs naskah ucap (Paket A) ----------

BRIEF_LAFAL = {"full_voice_over": "dapatkan leads lebih banyak",
               "voice_over_spoken": "dapatkan liids lebih banyak"}


def test_tts_memakai_versi_lafal():
    assert sp.spoken_text(BRIEF_LAFAL) == "dapatkan liids lebih banyak"


def test_brief_lama_tanpa_field_lafal_tetap_jalan():
    assert sp.spoken_text({"full_voice_over": "halo"}) == "halo"
    assert sp.spoken_text({"full_voice_over": "halo", "voice_over_spoken": ""}) == "halo"


def test_spoken_rewrite_dimatikan_menyamakan_keduanya(monkeypatch):
    monkeypatch.setattr(sp, "SPOKEN_REWRITE", False)
    assert sp.spoken_text(BRIEF_LAFAL) == BRIEF_LAFAL["full_voice_over"]


def test_aturan_lafal_hilang_dari_prompt_kalau_dimatikan(monkeypatch):
    monkeypatch.setattr(sp, "SPOKEN_REWRITE", False)
    assert sp.prompt_rule() == ""


# ---------- render nyata: durasi video == durasi audio ----------

def _warna(path, detik):
    """Kanal merah rata-rata frame pada detik `detik` — tiap bahan uji diberi
    warna merah yang berbeda, jadi ini menjawab 'bahan mana yang sedang tampil'."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{detik:.3f}", "-i", str(path),
         "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    )
    return out.stdout[0] if out.stdout else None


def _durasi(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


@pytest.fixture
def render_kecil(monkeypatch, tmp_path):
    """Kanvas kecil supaya test cepat; sisanya jalur render yang sama persis."""
    monkeypatch.setattr(ar, "TARGET_W", 240)
    monkeypatch.setattr(ar, "TARGET_H", 426)
    monkeypatch.setattr(ar, "THUMBNAIL_ENABLED", False)

    def buat_aset(n):
        paths = []
        for i in range(n):
            p = tmp_path / f"foto{i}.jpg"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", f"color=c=0x{(i * 30) % 256:02x}8040:size=240x426",
                 "-frames:v", "1", str(p)],
                check=True, capture_output=True)
            paths.append(str(p))
        return paths

    def pasang_audio(detik):
        """Ganti TTS dengan audio senyap sepanjang `detik` — tidak ada jaringan,
        tapi durasinya nyata dan diukur ffprobe seperti audio TTS sungguhan."""
        sumber = tmp_path / f"vo_{detik}.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "anullsrc=channel_layout=mono:sample_rate=24000",
             "-t", str(detik), str(sumber)],
            check=True, capture_output=True)

        async def palsu(teks, out_path):
            import shutil
            shutil.copy2(sumber, out_path)

        monkeypatch.setattr(ar, "generate_voice", palsu)
        return _durasi(sumber)

    return buat_aset, pasang_audio


@pytest.mark.parametrize("detik,jumlah_aset", [(30, 3), (10, 8)])
def test_setiap_bahan_yang_dipakai_benar_benar_tampil(render_kecil, tmp_path,
                                                      detik, jumlah_aset):
    """Ini, bukan durasinya, yang membuktikan rumusnya benar.

    Mengukur durasi saja TIDAK cukup dan sudah dibuktikan begitu: dengan rumus
    lama `max(MIN_CLIP_DURATION, total/n)` video jadi 12 detik untuk audio 10
    detik, lalu `-shortest` memotongnya kembali ke 10 detik — durasinya cocok,
    padahal dua bahan terakhir tidak pernah muncul di layar.

    Jadi yang diperiksa: tiap bahan yang masuk rencana benar-benar terlihat pada
    slot waktunya sendiri.
    """
    buat_aset, pasang_audio = render_kecil
    durasi_audio = pasang_audio(detik)
    aset = buat_aset(jumlah_aset)
    skrip = tmp_path / "script.json"
    skrip.write_text(json.dumps({
        "judul": "uji durasi",
        "audio_mode": "ai",
        "full_voice_over": "naskah uji",
        "media_assets": aset,
        "scenes": [],
    }), encoding="utf-8")

    keluaran = tmp_path / "hasil.mp4"
    ar.render_from_agent_script(str(skrip), str(keluaran))

    pot, _ = susun_potongan([{"path": a, "foto": True} for a in aset], durasi_audio,
                            min_klip=ar.MIN_CLIP_DURATION)
    dipakai, per = pot, pot[0]["durasi"]
    for i in range(len(dipakai)):
        harap = (i * 30) % 256
        terlihat = _warna(keluaran, i * per + per / 2)
        assert terlihat is not None, f"tidak ada frame pada slot bahan ke-{i + 1}"
        assert abs(terlihat - harap) <= 8, (
            f"bahan ke-{i + 1} dari {len(dipakai)} tidak tampil pada slotnya "
            f"(merah {terlihat}, diharap {harap})")

    satu_frame = 1.0 / ar.FPS
    assert _durasi(keluaran) == pytest.approx(durasi_audio, abs=satu_frame * 1.5)


def test_koreksi_durasi_dipanggil_TEPAT_sekali(render_kecil, tmp_path, monkeypatch):
    """Naskah ditulis ulang maksimal satu kali: koreksi berulang membakar kredit
    tanpa jaminan konvergen."""
    buat_aset, pasang_audio = render_kecil
    pasang_audio(30)  # jauh dari target 12 detik -> pasti memicu koreksi

    panggilan = []

    def fake(data, aktual, target):
        panggilan.append((round(aktual), target))
        return {"full_voice_over": "lebih pendek", "voice_over_spoken": "lebih pendek",
                "scenes": []}

    monkeypatch.setattr(ar, "perbaiki_durasi", fake)

    skrip = tmp_path / "script.json"
    skrip.write_text(json.dumps({
        "judul": "uji koreksi", "audio_mode": "ai",
        "full_voice_over": "naskah panjang", "target_duration": 12,
        "media_assets": buat_aset(2), "scenes": [],
    }), encoding="utf-8")

    ar.render_from_agent_script(str(skrip), str(tmp_path / "hasil.mp4"))

    assert len(panggilan) == 1, f"koreksi dipanggil {len(panggilan)} kali"


def test_tanpa_target_tidak_ada_koreksi(render_kecil, tmp_path, monkeypatch):
    """Perilaku lama utuh: user yang tidak meminta durasi tidak membayar
    panggilan LLM tambahan."""
    buat_aset, pasang_audio = render_kecil
    pasang_audio(30)

    def jangan(*a, **k):
        raise AssertionError("koreksi durasi dipanggil padahal user tidak meminta durasi")

    monkeypatch.setattr(ar, "perbaiki_durasi", jangan)

    skrip = tmp_path / "script.json"
    skrip.write_text(json.dumps({
        "judul": "tanpa target", "audio_mode": "ai",
        "full_voice_over": "naskah", "media_assets": buat_aset(2), "scenes": [],
    }), encoding="utf-8")

    ar.render_from_agent_script(str(skrip), str(tmp_path / "hasil.mp4"))


# ---------- isi prompt koreksi ----------

def _tangkap_prompt(monkeypatch):
    """Tangkap pesan yang dikirim ke LLM tanpa menyentuh jaringan."""
    ditangkap = {}

    def fake_chat_json(messages, **kw):
        ditangkap["pesan"] = messages[0]["content"]
        ditangkap["kw"] = kw
        return {"full_voice_over": "x", "voice_over_spoken": "x", "scenes": []}

    import common
    monkeypatch.setattr(common, "chat_json", fake_chat_json)
    return ditangkap


def test_prompt_koreksi_menyebut_rentang_kata_dan_jumlah_sekarang(monkeypatch):
    ditangkap = _tangkap_prompt(monkeypatch)
    ar.perbaiki_durasi({"full_voice_over": "satu dua tiga", "scenes": []}, 28.0, 12)
    kmin, kmax = d.word_target(12)
    assert f"{kmin}-{kmax}" in ditangkap["pesan"]
    assert "3 kata" in ditangkap["pesan"], "jumlah kata sekarang harus disebut"


def test_prompt_koreksi_membawa_aturan_lafal(monkeypatch):
    """Tanpa ini naskah hasil koreksi kehilangan ejaan fonetisnya dan TTS
    kembali membaca 'leads' jadi 'lid'."""
    ditangkap = _tangkap_prompt(monkeypatch)
    ar.perbaiki_durasi({"full_voice_over": "a b c", "scenes": []}, 28.0, 12)
    assert "voice_over_spoken" in ditangkap["pesan"]
    assert "fonetis" in ditangkap["pesan"]


def test_koreksi_satu_percobaan_dengan_timeout_pendek(monkeypatch):
    """Anggaran waktunya sempit: ini berjalan di tengah render yang punya timeout."""
    ditangkap = _tangkap_prompt(monkeypatch)
    ar.perbaiki_durasi({"full_voice_over": "a", "scenes": []}, 28.0, 12)
    assert ditangkap["kw"]["max_attempts"] == 1
    assert ditangkap["kw"]["timeout"] <= 45


def test_koreksi_gagal_tidak_menggagalkan_render(monkeypatch):
    """Lebih baik video dengan durasi meleset daripada tidak ada video."""
    import common

    def meledak(*a, **k):
        raise RuntimeError("provider mati")

    monkeypatch.setattr(common, "chat_json", meledak)
    assert ar.perbaiki_durasi({"full_voice_over": "a", "scenes": []}, 28.0, 12) is None
