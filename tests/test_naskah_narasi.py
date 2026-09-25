"""Naskah bergaya kreator (scripts/naskah.py) dan teks yang MENGIKUTI suara narasi.

Asal: user menilai naskah hambar dan meminta teks seperti video referensi (kreator TikTok):
satu kata per tampilan, berganti tepat mengikuti ucapan."""

import os
import json
import shutil
import subprocess

import numpy as np
import pytest

import auto_render as ar
import naskah as nk

HAMBAR = ("Masih ingat suasana Aksi Merah Laksamana Muda? Agar momen kebersamaan tidak hanya "
          "tersimpan diam, dokumentasi ini menyajikan ruang utama yang ramai dan meja layanan "
          "yang terlihat secara ringkas.")
HIDUP = "Kalian ikut kemarin? Jujur, suasananya bikin merinding. Yuk donor bulan depan!"


def test_naskah_hasil_nyata_yang_hambar_tertangkap():
    m = " ".join(nk.periksa(HAMBAR))
    for frasa in ("dokumentasi ini", "menyajikan", "terlihat", "momen kebersamaan", "terlalu panjang"):
        assert frasa in m, frasa


def test_naskah_gaya_kreator_lolos():
    assert nk.periksa(HIDUP) == []


def test_rapikan_satu_panggilan_dan_hasil_dipakai():
    panggil = []

    def llm(messages, **kw):
        panggil.append(messages)
        return {"full_voice_over": HIDUP, "voice_over_spoken": HIDUP.lower()}

    b, st = nk.rapikan({"full_voice_over": HAMBAR, "voice_over_spoken": HAMBAR}, "donor darah", llm, model="m")
    assert len(panggil) == 1 and b["full_voice_over"] == HIDUP and st["ditulis_ulang"] and st["sisa"] == []
    assert "dokumentasi ini" in panggil[0][0]["content"], "prompt menyebut masalah yang ditemukan"


def test_rapikan_tidak_memanggil_llm_bila_sudah_baik():
    b, st = nk.rapikan({"full_voice_over": HIDUP}, "", lambda *a, **k: pytest.fail("jangan panggil"), model="m")
    assert b["full_voice_over"] == HIDUP and st["masalah"] == []


def test_rapikan_gagal_naskah_lama_dipertahankan_dan_dicatat():
    def llm(*a, **k):
        raise TimeoutError("lambat")
    b, st = nk.rapikan({"full_voice_over": HAMBAR}, "", llm, model="m")
    assert b["full_voice_over"] == HAMBAR and not st["ditulis_ulang"] and "TimeoutError" in st["gagal"]


def test_prompt_brief_memuat_aturan_gaya():
    import agent1_2_brief as ab
    import inspect
    assert "ATURAN_GAYA" in inspect.getsource(ab.build_prompt)
    assert "DILARANG" in nk.ATURAN_GAYA and "kalian" in nk.ATURAN_GAYA


# ------------------------------------------------------------------ waktu per kata

def test_kata_dari_karakter_elevenlabs():
    chars = list("Woi kalian,  ok")
    t = [i * 0.1 for i in range(len(chars))]
    k = ar.kata_dari_karakter(chars, t, [x + 0.1 for x in t])
    assert [w["word"] for w in k] == ["Woi", "kalian,", "ok"]
    assert k[1]["start"] == pytest.approx(0.4) and k[1]["end"] == pytest.approx(1.1)


def test_petakan_kata_jumlah_sama_memakai_waktu_ucapan_dengan_ejaan_tulisan():
    waktu = [{"word": "vi", "start": 0, "end": .3}, {"word": "es", "start": .3, "end": .6}]
    assert [w["word"] for w in ar.petakan_kata("VS Code", waktu)] == ["VS", "Code"]


def test_petakan_kata_jumlah_beda_tetap_monoton_dan_menutup_durasi():
    waktu = [{"word": w, "start": i * .4, "end": i * .4 + .35} for i, w in
             enumerate("pakai vi es kod sekarang juga".split())]
    k = ar.petakan_kata("pakai VSCode sekarang juga", waktu)
    assert [w["word"] for w in k] == ["pakai", "VSCode", "sekarang", "juga"]
    mulai = [w["start"] for w in k]
    assert mulai == sorted(mulai) and len(set(mulai)) == 4
    assert k[-1]["end"] == waktu[-1]["end"]


def test_filter_per_kata_satu_kata_per_jendela_waktu():
    kata = [{"word": "Woi", "start": 0.1, "end": 0.3}, {"word": "kalian,", "start": 0.34, "end": 0.9}]
    f = ar.filter_per_kata(kata, 2.0, 1080, 1920, ar.SUBTITLE_STYLES["kata"])
    assert len(f) == 2
    assert "text='WOI'" in f[0] and "between(t,0.100,0.340)" in f[0]
    assert "KALIAN" in f[1] and "between(t,0.340,2.000)" in f[1]
    assert "Montserrat-ExtraBold" in f[0]


# ------------------------------------------------------------------ render voice-over nyata

@pytest.fixture
def render_ai(monkeypatch, tmp_path):
    W, H = 360, 640
    monkeypatch.setattr(ar, "TARGET_W", W)
    monkeypatch.setattr(ar, "TARGET_H", H)
    monkeypatch.setattr(ar, "THUMBNAIL_ENABLED", False)
    monkeypatch.setattr(ar, "STATUS_PATH", str(tmp_path / "status.json"))
    monkeypatch.setattr(ar, "music_wanted", lambda: False)
    vo = tmp_path / "vo.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "anullsrc=channel_layout=mono:sample_rate=24000", "-t", "4", str(vo)],
                   check=True, capture_output=True)

    async def palsu(teks, out):
        shutil.copy2(vo, out)
        ar.TTS_KATA[:] = [{"word": "Kalian", "start": 0.5, "end": 1.0},
                          {"word": "ikut?", "start": 1.0, "end": 1.6},
                          {"word": "Yuk!", "start": 2.5, "end": 3.0}]

    monkeypatch.setattr(ar, "generate_voice", palsu)
    foto = tmp_path / "f.jpg"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x203040:size={W}x{H}",
                    "-frames:v", "1", str(foto)], check=True, capture_output=True)
    skrip = tmp_path / "s.json"
    skrip.write_text(json.dumps({"judul": "uji", "audio_mode": "ai", "full_voice_over": "Kalian ikut? Yuk!",
                                 "media_assets": [str(foto)],
                                 "scenes": [{"start": 0, "end": 4, "text": "teks lama tulisan LLM"}]}))
    return skrip, tmp_path, W, H


def _terang_bawah(mp4, t, W, H):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(mp4), "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=True, capture_output=True)
    g = np.frombuffer(r.stdout, np.uint8).reshape(H, W)
    return int((g[int(H * 0.55):int(H * 0.78)] > 200).sum())


def test_teks_narasi_muncul_tepat_saat_diucapkan(render_ai, monkeypatch):
    monkeypatch.setenv("TEXT_ANIMATION", "none")
    skrip, tmp_path, W, H = render_ai
    out = tmp_path / "o.mp4"
    ar.render_from_agent_script(str(skrip), str(out))
    assert _terang_bawah(out, 0.2, W, H) < 20, "sebelum kata pertama diucapkan: belum ada teks"
    for t in (0.7, 1.3, 2.7):
        assert _terang_bawah(out, t, W, H) > 150, f"detik {t}: kata yang diucapkan harus tampil"
    a, b = _terang_bawah(out, 0.7, W, H), _terang_bawah(out, 1.3, W, H)
    assert a != b, "kata berganti (KALIAN -> IKUT?), bukan satu teks diam"


def test_tanpa_waktu_kata_teks_lama_tetap_dipakai(render_ai, monkeypatch):
    monkeypatch.setenv("TEXT_ANIMATION", "none")
    skrip, tmp_path, W, H = render_ai

    async def tanpa_waktu(teks, out):
        shutil.copy2(tmp_path / "vo.mp3", out)
        ar.TTS_KATA.clear()

    monkeypatch.setattr(ar, "generate_voice", tanpa_waktu)
    out = tmp_path / "o2.mp4"
    ar.render_from_agent_script(str(skrip), str(out))
    assert out.exists()


def test_huruf_asing_dari_model_ditandai():
    """Terukur 24 Sep: model gratis menyisipkan '确认' di naskah Indonesia."""
    m = nk.periksa("Daftar lewat link di bio, lalu确认 datang bareng.")
    assert any("确认" in x for x in m)
    assert nk.periksa("Yuk daftar! Café, naïve, 100% seru 🎉") == [], "Latin beraksen & emoji bukan huruf asing"
    assert nk.periksa("Привет всем") and nk.periksa("안녕 teman")


def test_kata_narasi_panjang_tidak_masuk_area_tombol_kanan():
    """25 Sep: kata narasi panjang ('TERSELAMATKAN') selebar ±95% masuk area tombol TikTok."""
    import auto_render as ar
    from subtitle_layout import text_width
    gaya = ar.SUBTITLE_STYLES["kata"]
    f = ar.filter_per_kata([{"word": "terselamatkan", "start": 0, "end": 1}], 1.0, 1080, 1920, gaya)
    fs = int(f[0].split("fontsize=")[1].split(":")[0])
    font = os.path.join(ar.ASSETS_FONTS, gaya["font"])
    assert text_width(font, fs, "TERSELAMATKAN") <= 1080 * ar.SUBTITLE_MAX_WIDTH


def test_angka_karangan_di_naskah_ditandai():
    """25 Sep: 'cuma 15 menit' dikarang untuk ajakan donor yang tidak menyebut durasi."""
    m = nk.periksa("Mau bantu cuma 15 menit? Yuk daftar!", sumber="ajak donor darah minggu depan")
    assert any("15" in x for x in m)
    assert nk.periksa("Donor tanggal 12, yuk!", sumber="donor darah tanggal 12") == []
    assert nk.periksa("Cuma 15 menit, yuk!") == [], "tanpa sumber: tidak dinilai (perilaku lama)"
