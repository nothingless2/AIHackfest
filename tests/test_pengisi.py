"""Buang kata pengisi & ulangan gagap (scripts/pengisi.py). Transkrip uji = keluaran NYATA faster-whisper
small atas kalimat edge-tts ber-"eee" (29 Sep): Whisper memecah "eeee" jadi "e -e" dan "Hmmm" jadi
"Hi -mmmm"."""

import json
import subprocess

import pytest

import pengisi as p


def parse(s):
    return [{"word": t.split("@")[0], "start": float(t.split("@")[1].split("-")[0]),
             "end": float(t.split("@")[1].split("-")[1])} for t in s.split()]


K1 = parse("Jadi,@0.00-0.56 e@1.14-1.18 -e,@1.18-1.48 aku@1.72-1.96 pindah@1.96-2.28 ke@2.28-2.36 "
           "Hermis.@2.36-2.82 Hi@3.78-3.98 -mmmm,@3.98-4.40 karena,@4.80-5.22 em,@5.88-6.08 "
           "lebih@6.42-6.68 gampang.@6.68-7.20")
K2 = parse("Aku@0.00-0.42 pakai@0.42-0.68 aku@0.68-0.86 pakai@0.86-1.10 Hermie@1.10-1.46 "
           "setiap@1.46-1.82 hari,@1.82-2.02 setiap@2.42-2.86 hari.@2.86-3.06")


def test_pengisi_dari_transkrip_nyata():
    r = p.rentang_buang(K1, 7.3)
    assert [(a, b) for a, b, _ in r] == [(1.12, 1.5), (3.76, 4.42), (5.86, 6.1)]
    for a, b, _ in r:        # tidak pernah memotong kata sah di sebelahnya
        for w in K1:
            if not p.adalah_pengisi(w["word"]) and w["word"] != "Hi":
                assert w["end"] <= a + 1e-9 or w["start"] >= b - 1e-9, (w, a, b)


def test_ulangan_gagap_dibuang_penekanan_dipertahankan():
    r = p.rentang_buang(K2, 3.2)
    assert r == [(0.0, 0.66, 'ulangan "aku pakai"')], "\"setiap hari, setiap hari\" = penekanan (koma + jeda)"


@pytest.mark.parametrize("teks", ["pelan@0.0-0.3 pelan@0.3-0.6 saja@0.6-0.9",
                                  "sama@0.0-0.3 sama@0.3-0.6 ya@0.6-0.8"])
def test_reduplikasi_bukan_gagap(teks):
    assert p.rentang_buang(parse(teks), 1.0) == []


def test_kata_fungsi_ganda_dibuang():
    r = p.rentang_buang(parse("yang@0.0-0.2 yang@0.25-0.45 penting@0.45-0.9"), 1.0)
    assert len(r) == 1 and r[0][2] == 'ulangan "yang"'


@pytest.mark.parametrize("kata,harap", [("eee", True), ("e", True), ("emm,", True), ("Hmm", True),
                                        ("-mmmm,", True), ("memememem", True), ("uhm", True),
                                        ("emas", False), ("hemat", False), ("me", False), ("mau", False),
                                        ("Hermes", False), ("", False)])
def test_pola_pengisi(kata, harap):
    assert p.adalah_pengisi(kata) is harap


def test_terlalu_banyak_klip_dibiarkan_utuh():
    kata = parse(" ".join(f"eee@{i * 0.5:.2f}-{i * 0.5 + 0.4:.2f}" for i in range(6)) + " halo@3.0-3.4")
    ranges, buang = p.terapkan([(0.0, 3.5)], kata, 3.5)
    assert buang is None and ranges == [(0.0, 3.5)], "transkrip mencurigakan: jangan memotong separuh klip"


def test_tanpa_pengisi_tanpa_potongan():
    assert p.terapkan([(0.0, 3.2)], parse("halo@0.0-0.4 semua@0.5-0.9"), 3.2) == ([(0.0, 3.2)], [])


# ------------------------------------------------------------------ di pipeline

def _render(monkeypatch, tmp_path, aktif):
    import auto_render as ar
    monkeypatch.setenv("POTONG_PENGISI", "1" if aktif else "0")
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC", "off")
    monkeypatch.setattr(ar, "TARGET_W", 160)
    monkeypatch.setattr(ar, "TARGET_H", 284)
    monkeypatch.setattr(ar, "THUMBNAIL_ENABLED", False)
    monkeypatch.setattr(ar, "TRIM_SILENCE", False)
    v = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:size=160x284:rate=24:duration=8",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=8", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ac", "2", "-shortest", str(v)], check=True, capture_output=True)
    data = {"judul": "uji", "audio_mode": "original", "full_voice_over": "x", "media_assets": [str(v)],
            "scenes": [], "transcript_words": {"v.mp4": K1},
            "transcript_segments": {"v.mp4": [{"start": 0, "end": 7.2, "text": "x"}]}}
    s = tmp_path / "s.json"
    s.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "h.mp4"
    ar.render_from_agent_script(str(s), str(out))
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(out)], capture_output=True, text=True).stdout)
    return dur, json.load(open(ar.STATUS_PATH))


def test_pipeline_memendekkan_video_sebesar_yang_dibuang(monkeypatch, tmp_path):
    dur_mati, st_mati = _render(monkeypatch, tmp_path, False)
    dur, st = _render(monkeypatch, tmp_path, True)
    assert st_mati.get("potong_pengisi") is None, "kontrol: dimatikan = tidak ada potongan"
    assert st["potong_pengisi"]["dibuang"] == 3
    # 3 potongan -> 4 segmen, tiap segmen dibulatkan ke frame (1/24 dtk): toleransi 4 frame.
    assert dur_mati - dur == pytest.approx(st["potong_pengisi"]["detik"], abs=4 / 24)
    assert dur_mati - dur > 0.9, "benar-benar lebih pendek"


def test_subtitle_tidak_pernah_menampilkan_pengisi():
    import auto_render as ar
    rencana = [{"path": "/x/v.mp4", "ranges": [(0.0, 7.3)], "durasi": 7.3}]
    scenes = ar.subtitle_scenes({"transcript_words": {"v.mp4": K1}}, rencana, 160, 284)
    teks = " ".join(w["word"] for s in scenes for w in s.get("words") or [])
    assert "karena" in teks and "-mmmm" not in teks and " em," not in f" {teks}" and "-e," not in teks


def test_pengisi_menempel_tidak_memakan_kata_tetangga():
    r = p.rentang_buang(parse("aku@0.00-0.30 eee@0.30-0.60 pindah@0.60-0.90"), 1.0)
    assert [(a, b) for a, b, _ in r] == [(0.30, 0.60)], "margin tidak boleh masuk ke 'aku'/'pindah'"
