"""Caption dinamis (scripts/caption_dinamis.py): potongan 1-3 kata dari waktu kata yang
TERDENGAR, satu kata kunci besar. Contoh user 27 Sep: "dan / **Remotion**"."""

import caption_dinamis as cd


def kw(teks, per=0.3, jeda_di=()):
    t, hasil = 0.0, []
    for i, w in enumerate(teks.split()):
        if i in jeda_di:
            t += 0.6
        hasil.append({"word": w, "start": round(t, 3), "end": round(t + per * 0.8, 3)})
        t += per
    return hasil


TEKS = "Aku biasa ngedit video pakai Claude dan Remotion. Hasilnya 3 kali lebih cepat buat konten harian"


def test_semua_kata_tercakup_berurutan_dan_tidak_bertumpuk():
    p = cd.potong(kw(TEKS))
    assert [w for x in p for w in x["kata"]] == [cd.tampilan(w) for w in TEKS.split()]
    for a, b in zip(p, p[1:]):
        assert a["selesai"] <= b["mulai"] + 1e-9 and a["mulai"] < a["selesai"]
    assert all(1 <= len(x["kata"]) <= cd.MAKS_KATA for x in p)


def test_pemutus_jeda_dan_akhir_kalimat():
    p = cd.potong(kw("satu dua tiga empat", jeda_di=(2,)))
    assert [x["kata"] for x in p] == [["satu", "dua"], ["tiga", "empat"]], "jeda memutus"
    p = cd.potong(kw("Selesai. Mulai lagi"))
    assert p[0]["kata"] == ["Selesai"], "akhir kalimat memutus"


def test_batas_huruf():
    p = cd.potong(kw("mempertimbangkan kemungkinan terburuk"))
    assert all(len(" ".join(x["kata"])) <= cd.MAKS_HURUF or len(x["kata"]) == 1 for x in p)


def test_potongan_hilang_saat_jeda_panjang():
    p = cd.potong(kw("halo semua", per=0.3) + [{"word": "lanjut", "start": 5.0, "end": 5.3}])
    assert p[0]["selesai"] <= 0.54 + cd.EKOR + 1e-9, "tidak menggantung selama jeda"


def test_kata_kunci_llm_menang_dan_heuristik_tidak_memilih_kata_sambung():
    p = cd.potong(kw(TEKS, per=0.5), kata_kunci=["remotion"])
    kunci = [x["kata"][x["kunci"]] for x in p if x["kunci"] is not None]
    assert "Remotion" in kunci
    assert not {cd.norm(k) for k in kunci} & cd.KATA_SAMBUNG
    assert "3" in kunci or "Claude" in kunci, "angka / merek di tengah kalimat diutamakan heuristik"


def test_porsi_dan_jarak_kata_kunci_heuristik():
    teks = " ".join(f"Produk{i} keren" for i in range(20))
    p = cd.potong(kw(teks, per=0.4))
    dipilih = [x for x in p if x["kunci"] is not None]
    assert 0 < len(dipilih) <= int(len(p) * cd.PORSI_KUNCI)
    for a, b in zip(dipilih, dipilih[1:]):
        assert b["mulai"] - a["mulai"] >= cd.JARAK_KUNCI - 1e-9


def test_tanpa_kandidat_tidak_ada_kata_kunci():
    # Kata sambung PANJANG (>= 6 huruf) juga tidak boleh jadi kata kunci.
    p = cd.potong(kw("dan yang karena supaya tetapi sangat banget sebelum", per=1.5))
    assert all(x["kunci"] is None for x in p)


def test_maksimal_tiga_kata_walau_pendek():
    p = cd.potong(kw("ya ok aja deh sip"))
    assert [len(x["kata"]) for x in p] == [3, 2]


def test_kata_kunci_llm_tetap_dibatasi_porsi_jarak_dan_ulang():
    """Render nyata 27 Sep: 21/42 potongan ditekankan, 'OpenClaw' 7 kali."""
    teks = " ".join("pakai OpenClaw lagi." for _ in range(12))
    p = cd.potong(kw(teks, per=0.5), kata_kunci=["OpenClaw"])
    dipilih = [x for x in p if x["kunci"] is not None]
    assert len(dipilih) <= max(1, int(len(p) * cd.PORSI_KUNCI))
    assert sum(1 for x in dipilih if cd.norm(x["kata"][x["kunci"]]) == "openclaw") <= cd.MAKS_ULANG
    for a, b in zip(dipilih, dipilih[1:]):
        assert b["mulai"] - a["mulai"] >= cd.JARAK_KUNCI - 1e-9


def test_tanda_baca_depan_dibuang():
    assert cd.tampilan("-error") == "error" and cd.tampilan('"Hermes,"') == "Hermes"


def test_usulan_yang_tidak_diucapkan_dibuang():
    assert cd.kata_kunci_bersih(["Remotion", "Figma", "yang", "Claude Code"], TEKS) == ["remotion", "claude"]
    assert cd.kata_kunci_bersih("bukan list", TEKS) == []


def test_masukan_kosong_aman():
    assert cd.potong([]) == [] and cd.potong(None) == []


# ------------------------------------------------------------------ render sungguhan (Remotion)

import json  # noqa: E402
import subprocess  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

KATA_UJI = "halo semua hari ini kita ikut donor darah di aula kampus bersama relawan komunitas".split()


def _render(monkeypatch, tmp_path, gaya, kunci=("donor",)):
    import auto_render as ar
    monkeypatch.setattr(ar, "TARGET_W", 270)
    monkeypatch.setattr(ar, "TARGET_H", 480)
    monkeypatch.setattr(ar, "THUMBNAIL_ENABLED", False)
    monkeypatch.setattr(ar, "TRIM_SILENCE", False)
    monkeypatch.setattr(ar, "SUBTITLE_STYLE", gaya)
    monkeypatch.setenv("SUBTITLE_STYLE", gaya)
    monkeypatch.setenv("CONTENT_FACTORY_MUSIC", "off")
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=0x303848:size=270x480:rate=24:duration=9", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=9", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ac", "2", "-shortest", str(video)], check=True, capture_output=True)
    kata = [{"word": w, "start": round(0.5 + i * 0.5, 3), "end": round(0.5 + i * 0.5 + 0.4, 3)}
            for i, w in enumerate(KATA_UJI)]
    data = {"judul": "uji", "audio_mode": "original", "full_voice_over": "x", "media_assets": [str(video)],
            "scenes": [], "transcript_words": {"v.mp4": kata}, "kata_kunci": list(kunci),
            "transcript_segments": {"v.mp4": [{"start": 0.5, "end": 8.5, "text": " ".join(KATA_UJI)}]}}
    s = tmp_path / f"s_{gaya}.json"
    s.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / f"h_{gaya}.mp4"
    ar.render_from_agent_script(str(s), str(out))
    return out, kata, json.load(open(ar.STATUS_PATH))


def _emas(path, t):
    """Jumlah piksel emas (R tinggi, G sedang-tinggi, B rendah) di zona caption."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-vf", "crop=iw:ih*0.3:0:ih*0.58", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(int)
    return int(((a[:, 0] > 190) & (a[:, 1] > 130) & (a[:, 2] < 110)).sum())


def test_render_kata_kunci_emas_tepat_saat_diucapkan(monkeypatch, tmp_path):
    out, kata, status = _render(monkeypatch, tmp_path, "dinamis")
    donor = next(w["start"] for w in kata if w["word"] == "donor")
    assert status["caption"]["dipakai"] and "donor" in status["caption"]["kata_kunci"]
    assert _emas(out, donor + 0.3) > 30, "kata kunci emas tampil saat 'donor' diucapkan"
    assert _emas(out, 0.9) == 0, "potongan tanpa kata kunci: tanpa emas"
    assert status["sfx"]["dipakai"] and status["sfx"]["pop"] >= 1, "pop di kata kunci"


def test_kontrol_gaya_kata_tanpa_emas_dan_tanpa_sfx(monkeypatch, tmp_path):
    out, kata, status = _render(monkeypatch, tmp_path, "kata")
    donor = next(w["start"] for w in kata if w["word"] == "donor")
    assert _emas(out, donor + 0.3) == 0 and status.get("caption") is None and status.get("sfx") is None


def test_remotion_gagal_subtitle_tetap_ada(monkeypatch, tmp_path):
    import overlay_remotion as orr

    def rusak(*a, **k):
        raise orr.OverlayError("chromium mati")
    monkeypatch.setattr(orr, "render_caption", rusak)
    out, kata, status = _render(monkeypatch, tmp_path, "dinamis")
    assert status["caption"] == {"dipakai": False, "gagal": "chromium mati"}
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "2.3", "-i", str(out), "-frames:v", "1", "-vf",
                          "crop=iw:ih*0.3:0:ih*0.55", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         check=True, capture_output=True).stdout
    assert (np.frombuffer(raw, np.uint8) > 230).sum() > 30, "cadangan drawtext (putih) tampil"
    assert status["sfx"]["dipakai"] is False or status["sfx"].get("pop", 0) == 0, "tanpa pop bila caption gagal"


def test_penempelan_gagal_subtitle_digambar_ulang(monkeypatch, tmp_path):
    import overlay_remotion as orr

    def rusak(*a, **k):
        raise orr.OverlayError("ffmpeg overlay rusak")
    monkeypatch.setattr(orr, "komposit", rusak)
    out, kata, status = _render(monkeypatch, tmp_path, "dinamis")
    assert status["caption"]["dipakai"] is False and status["caption"]["gagal"].startswith("penempelan")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "2.3", "-i", str(out), "-frames:v", "1", "-vf",
                          "crop=iw:ih*0.3:0:ih*0.55", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         check=True, capture_output=True).stdout
    assert (np.frombuffer(raw, np.uint8) > 230).sum() > 30, "subtitle tidak hilang"
    assert status["sfx"]["dipakai"] is False or status["sfx"].get("pop", 0) == 0


def test_daftar_concat_tepat_waktu():
    import overlay_remotion as orr
    m = orr.CAPTION_MASUK
    pot = [{"mulai": 1.0, "selesai": 2.0, "kata": ["a"], "kunci": None},
           {"mulai": 2.0, "selesai": 2.1, "kata": ["b"], "kunci": None}]         # 2-3 frame < M
    items, jadwal = orr.rencana_caption(pot, 24)
    assert jadwal == [(24, 24), (48, 2)]
    png = [f"f{i}.png" for i in range(len(items) * (m + 1))]
    baris = orr.daftar_concat(jadwal, png, "kosong.png", 24)
    file = [b.split("'")[1] for b in baris if b.startswith("file")]
    dur = [float(b.split()[1]) for b in baris if b.startswith("duration")]
    assert file[0] == "kosong.png" and dur[0] == pytest.approx(1.0), "celah sebelum potongan pertama"
    assert file[1:1 + m] == png[:m] and all(d == pytest.approx(1 / 24) for d in dur[1:1 + m])
    assert file[1 + m] == png[m] and dur[1 + m] == pytest.approx((24 - m) / 24), "frame diam sisa potongan"
    assert file[2 + m:4 + m] == png[m + 1:m + 3], "potongan pendek: hanya frame masuk yang muat"
    assert sum(dur[:-1]) == pytest.approx(50 / 24), "tidak ada frame yang bergeser"


def test_banyak_potongan_tetap_satu_input_komposit(tmp_path):
    """Render nyata 27 Sep: 42 potongan = ~100 input ffmpeg -> OOM. Kini selalu 1 input."""
    import overlay_remotion as orr
    pot = [{"mulai": i * 0.5, "selesai": i * 0.5 + 0.5, "kata": ["kata", f"ke{i}"], "kunci": 1}
           for i in range(30)]
    kerja, info = orr.render_caption(pot, str(tmp_path), lebar=180, tinggi=320, fps=24, durasi=15.2)
    assert len(kerja) == 1 and kerja[0]["jenis"] == "concat" and info["potongan"] == 30
