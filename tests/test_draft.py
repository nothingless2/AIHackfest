"""Draf naskah sebelum render (scripts/draf_naskah.py + hermes_render.py --draft/--draft-id).

Permintaan user 24 Sep: BrainIdea memberi saran naskah (2 alternatif) SEBELUM video dirender;
user memilih dan boleh mengubah kalimatnya. Yang diuji di sini: yang dirender PERSIS yang
disetujui (tanpa LLM brief ulang), naskah user tidak ditimpa, dan gerbang gagal-tertutup
(chat lain, kedaluwarsa, bahan beda, dipakai dua kali)."""

import json
import os
import threading

import pytest

import agent1_2_brief as ab
import draf_naskah as dn
import hermes_render as hr
from orchestrator import DRAFT_STAGES, RENDER_STAGES

CHAT = "DM with Uji"
NASKAH_A = "Kalian pernah lihat antrean seramai ini? Yuk, ikut donor minggu depan!"
NASKAH_B = "Satu kantong darah bisa berarti banyak. Datang, ya. Kami tunggu kalian."
VARIAN = [
    {"gaya": "Santai lucu", "judul": "Judul A", "deskripsi": "desk A", "full_voice_over": NASKAH_A,
     "voice_over_spoken": NASKAH_A, "scenes": [{"start": 0, "end": 3, "text": "teks A"}],
     "hashtags": ["#a"]},
    {"gaya": "Hangat mengajak", "judul": "Judul B", "deskripsi": "desk B", "full_voice_over": NASKAH_B,
     "voice_over_spoken": NASKAH_B, "scenes": [{"start": 0, "end": 3, "text": "teks B"}],
     "hashtags": ["#b"]},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    raw = tmp_path / "raw"
    bahan = []
    for n, isi in (("v1.mp4", b"a" * 10), ("v2.mp4", b"b" * 20)):
        (cache / n).write_bytes(isi)
        bahan.append(str(cache / n))
    lain = cache / "lain.mp4"
    lain.write_bytes(b"c" * 30)

    monkeypatch.setattr(hr, "MEDIA_ROOTS", [str(cache)])
    monkeypatch.setattr(hr, "RAW_DIR", str(raw))
    monkeypatch.setattr(hr, "STATE_DIR", str(state))
    monkeypatch.setattr(hr, "BRIEF_PATH", str(state / "creative_brief.json"))
    monkeypatch.setattr(hr, "brief_path_for_run", lambda rid: str(state / f"brief_{rid}.json"))
    monkeypatch.setattr(dn, "DRAF_DIR", str(state / "draf_naskah"))
    monkeypatch.setattr(hr, "sweep_old_run_files", lambda: 0)
    monkeypatch.setattr(hr, "ensure_dirs", lambda: None)
    monkeypatch.setattr(hr, "ringkasan_biaya", lambda rid: None)
    monkeypatch.setattr(hr, "install_signal_handlers", lambda: None)
    log = []
    monkeypatch.setattr(hr, "log_event", lambda ev, rid, **k: log.append((ev, k)))

    calls = []
    kontrol = {"gagal": False}

    def fake_run(run_id, *, stages=None, salin_video=True, sebelum_tahap=None, **kw):
        calls.append({"stages": stages, "salin_video": salin_video,
                      "draft_env": os.environ.get("CONTENT_FACTORY_DRAFT"),
                      "text_font": os.environ.get("TEXT_FONT"),
                      "audio_mode": os.environ.get("CONTENT_FACTORY_AUDIO_MODE")})
        if sebelum_tahap:
            sebelum_tahap()
        if kontrol["gagal"]:
            return "FAILED", (1, ("ContentMakers", "ffmpeg meledak"))
        if stages is DRAFT_STAGES:
            brief = {"audio_mode": "ai", "pemahaman_bahan": ["Antrean donor di aula.", "Petugas mengukur tensi."],
                     "media_assets": [], **VARIAN[0], "varian": VARIAN}
        else:
            with open(hr.BRIEF_PATH, encoding="utf-8") as f:
                brief = json.load(f)
            calls[-1]["brief"] = brief
        with open(hr.brief_path_for_run(run_id), "w", encoding="utf-8") as f:
            json.dump(brief, f)
        return "SUCCESS", None

    monkeypatch.setattr(hr, "run_core_stages_locked", fake_run)
    return {"bahan": bahan, "lain": str(lain), "calls": calls, "kontrol": kontrol, "raw": raw,
            "log": log}


def jalan(capsys, *argv):
    kode = hr.main(list(argv))
    out = capsys.readouterr().out.strip().splitlines()[-1]
    return kode, json.loads(out)


def buat_draf(env, capsys, *extra):
    args = ["--draft", "--chat-id", CHAT, "--no-require-inspect", "--music", "off",
            "--audio-mode", "ai", "--user-context", "ajak donor darah"]
    for b in env["bahan"]:
        args += ["--media-path", b]
    kode, out = jalan(capsys, *args, *extra)
    assert kode == 0, out
    return out


# ------------------------------------------------------------------ membuat draf

def test_draf_tidak_merender_dan_berisi_dua_varian(env, capsys):
    out = buat_draf(env, capsys)
    (c,) = env["calls"]
    assert c["stages"] is DRAFT_STAGES and c["salin_video"] is False
    assert "agent3_render.py" not in [s[0] for s in c["stages"]], "draf tidak boleh merender"
    assert c["draft_env"] == "1"
    assert out["mode"] == "draf" and "video_path" not in out
    assert [v["huruf"] for v in out["varian"]] == ["A", "B"]
    pesan = out["pesan"]
    assert "Antrean donor di aula." in pesan and "Petugas mengukur tensi." in pesan
    assert NASKAH_A in pesan and NASKAH_B in pesan
    assert "Santai lucu" in pesan and "Balas A atau B" in pesan
    assert os.path.exists(os.path.join(dn.DRAF_DIR, out["draft_id"] + ".json"))


# ------------------------------------------------------------------ render dari draf

def test_render_varian_b_tanpa_brief_ulang(env, capsys):
    d = buat_draf(env, capsys)
    kode, out = jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "b")
    assert kode == 0, out
    c = env["calls"][-1]
    assert c["stages"] is RENDER_STAGES
    assert "agent1_2_brief.py" not in [s[0] for s in c["stages"]], "brief TIDAK boleh dibuat ulang"
    assert c["brief"]["full_voice_over"] == NASKAH_B and c["brief"]["judul"] == "Judul B"
    assert "varian" not in c["brief"]
    assert c["audio_mode"] == "ai", "pengaturan draf harus dipakai lagi saat render"
    assert out["draf"] == {"id": d["draft_id"], "varian": "B", "diedit": False}


def test_naskah_ubahan_user_dipakai_apa_adanya(env, capsys):
    d = buat_draf(env, capsys)
    # Sengaja memuat frasa 'hambar' -- pemeriksa gaya hanya boleh memberi catatan.
    ubahan = "Video ini menampilkan antrean donor darah kami. Yuk ikut!"
    kode, out = jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "A",
                      "--naskah", ubahan)
    assert kode == 0, out
    b = env["calls"][-1]["brief"]
    assert b["full_voice_over"] == ubahan and b["voice_over_spoken"] == ubahan
    assert out["draf"]["diedit"] is True
    assert out["catatan_naskah"], "catatan gaya dilaporkan, bukan diam-diam menulis ulang"


def test_flag_gaya_boleh_diganti_flag_isi_dikunci(env, capsys):
    d = buat_draf(env, capsys)
    kode, out = jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "A",
                      "--audio-mode", "original")
    assert kode == 1 and out["kode"] == "draf_terkunci"
    # nilai yang SAMA dengan draf tidak ditolak; flag gaya boleh berubah
    kode, out = jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "A",
                      "--audio-mode", "ai", "--text-font", "santai")
    assert kode == 0, out
    assert env["calls"][-1]["text_font"] == "santai"


# ------------------------------------------------------------------ gagal-tertutup

def test_draf_dipakai_dua_kali_ditolak(env, capsys):
    d = buat_draf(env, capsys)
    assert jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "A")[0] == 0
    kode, out = jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "B")
    assert kode == 1 and out["kode"] == "draf_sudah_dipakai"


def test_render_gagal_melepas_klaim(env, capsys):
    d = buat_draf(env, capsys)
    env["kontrol"]["gagal"] = True
    kode, out = jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "A")
    assert kode == 1 and out["kode"] == "render_gagal"
    env["kontrol"]["gagal"] = False
    kode, out = jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "A")
    assert kode == 0, "render gagal tidak boleh menghanguskan draf"


def test_draf_chat_lain_ditolak(env, capsys):
    d = buat_draf(env, capsys)
    kode, out = jalan(capsys, "--chat-id", "DM with Orang Lain", "--draft-id", d["draft_id"], "--varian", "A")
    assert kode == 1 and out["kode"] == "draf_chat_lain"


def test_draf_kedaluwarsa_ditolak(env, capsys, monkeypatch):
    d = buat_draf(env, capsys)
    monkeypatch.setattr(dn, "DRAF_TTL_JAM", 0.0)
    kode, out = jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "A")
    assert kode == 1 and out["kode"] == "draf_kedaluwarsa"


def test_bahan_berbeda_ditolak_bahan_sama_diterima(env, capsys):
    d = buat_draf(env, capsys)
    kode, out = jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "A",
                      "--media-path", env["lain"])
    assert kode == 1 and out["kode"] == "draf_bahan_beda"
    args = ["--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "A"]
    for b in env["bahan"]:
        args += ["--media-path", b]
    assert jalan(capsys, *args)[0] == 0


def test_bahan_draf_hilang_ditolak(env, capsys):
    d = buat_draf(env, capsys)
    for f in env["raw"].iterdir():
        f.unlink()
    kode, out = jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "A")
    assert kode == 1 and out["kode"] == "draf_bahan_hilang"


@pytest.mark.parametrize("draft_id", ["../../etc/passwd", "ABCDEF123456", "abc", ""])
def test_draft_id_aneh_ditolak(env, capsys, draft_id):
    kode, out = jalan(capsys, "--chat-id", CHAT, "--draft-id", draft_id or "x", "--varian", "A")
    assert kode == 1 and out["kode"] in ("draf_invalid",)


def test_varian_di_luar_pilihan_ditolak(env, capsys):
    d = buat_draf(env, capsys)
    kode, out = jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "C")
    assert kode == 1 and out["kode"] == "varian_invalid"
    assert not env["calls"][1:], "ditolak sebelum lock/render"


def test_varian_tanpa_draft_id_ditolak(env, capsys):
    kode, out = jalan(capsys, "--chat-id", CHAT, "--varian", "A", "--media-path", env["bahan"][0],
                      "--no-require-inspect")
    assert kode == 1 and out["kode"] == "argumen_invalid"


def test_klaim_bersamaan_hanya_satu_lolos(tmp_path, monkeypatch):
    monkeypatch.setattr(dn, "DRAF_DIR", str(tmp_path))
    draft_id = "0123456789ab"
    hasil, mulai = [], threading.Barrier(8)

    def coba():
        mulai.wait()
        try:
            dn.klaim(draft_id)
            hasil.append("lolos")
        except dn.DrafError:
            hasil.append("ditolak")

    ts = [threading.Thread(target=coba) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert hasil.count("lolos") == 1 and hasil.count("ditolak") == 7


# ------------------------------------------------------------------ brief mode draf

def test_validasi_varian_membuang_yang_cacat():
    v = ab.validasi_varian([VARIAN[0], {"judul": "x"}, "teks", {**VARIAN[1], "gaya": "g" * 99}, VARIAN[0]])
    assert [x["judul"] for x in v] == ["Judul A", "Judul B"]
    assert len(v[1]["gaya"]) == 40
    assert ab.validasi_varian(None) == []


@pytest.fixture
def brief_draf(monkeypatch, tmp_path):
    nama = ["a.mp4", "b.mp4"]
    paths = [str(tmp_path / n) for n in nama]
    disimpan, prompt = {}, []
    monkeypatch.setattr(ab, "ensure_dirs", lambda: None)
    monkeypatch.setattr(ab, "resolve_chat_id", lambda: "1")
    monkeypatch.setattr(ab, "select_assets", lambda: nama)
    monkeypatch.setattr(ab, "resolve_assets", lambda n: paths)
    monkeypatch.setattr(ab, "notify", lambda *a, **k: True)
    monkeypatch.setattr(ab, "OPENAI_API_KEY", "kunci-palsu")
    monkeypatch.setattr(ab, "build_image_parts", lambda p, **k: [])
    monkeypatch.setattr(ab, "read_json", lambda *a, **k: {})
    monkeypatch.setattr(ab, "write_json", lambda path, data: disimpan.__setitem__(path, data))
    monkeypatch.setattr(ab, "transcribe_assets_report", lambda p, konteks="": ({}, {}))
    monkeypatch.setattr(ab, "bahan_punya_suara", lambda p: False)
    monkeypatch.setenv("CONTENT_FACTORY_AUDIO_MODE", "ai")
    monkeypatch.setenv("CONTENT_FACTORY_DRAFT", "1")

    def llm(messages, **kw):
        prompt.append((kw.get("label"), messages[0]["content"]))
        if kw.get("label") == "tulis ulang naskah":
            return {"full_voice_over": "Kalian wajib datang. Seru banget!"}
        return {"trend_report": {"content_angle": "x", "trend_index": None,
                                 "pemahaman_bahan": ["klip satu", "klip dua", "klip tiga berlebih"]},
                "varian": [{**VARIAN[0], "full_voice_over": "Video ini menampilkan antrean donor."},
                           VARIAN[1]]}

    monkeypatch.setattr(ab, "chat_json", llm)
    return lambda: (ab.run(), disimpan[ab.BRIEF_PATH])[1], prompt


def test_brief_draf_menyimpan_varian_dan_pemahaman(brief_draf):
    jalankan, prompt = brief_draf
    b = jalankan()
    assert [v["judul"] for v in b["varian"]] == ["Judul A", "Judul B"]
    # varian A hambar -> ditulis ulang SENDIRI; B sudah baik -> tidak disentuh
    assert b["varian"][0]["full_voice_over"] == "Kalian wajib datang. Seru banget!"
    assert b["varian"][0]["naskah_status"]["ditulis_ulang"] is True
    assert b["varian"][1]["full_voice_over"] == NASKAH_B
    assert b["full_voice_over"] == b["varian"][0]["full_voice_over"], "brief dasar = varian A"
    assert b["pemahaman_bahan"] == ["klip satu", "klip dua"], "maksimal satu per bahan"
    teks_prompt = [c for label, c in prompt if label != "tulis ulang naskah"][0][0]["text"]
    assert "MODE DRAF" in teks_prompt and '"varian"' in teks_prompt


def test_brief_draf_tanpa_varian_valid_gagal(brief_draf, monkeypatch):
    jalankan, _ = brief_draf
    monkeypatch.setattr(ab, "chat_json", lambda m, **k: {"trend_report": {}, "varian": [{"judul": ""}]})
    with pytest.raises(ValueError, match="varian"):
        jalankan()


def test_brief_biasa_tidak_berubah(brief_draf, monkeypatch):
    """Kontrol: tanpa mode draf, skema lama (creative_brief) tetap dipakai."""
    jalankan, prompt = brief_draf
    monkeypatch.delenv("CONTENT_FACTORY_DRAFT")
    monkeypatch.setattr(ab, "chat_json", lambda m, **k: (
        prompt.append(m[0]["content"][0]["text"]) or
        {"trend_report": {}, "creative_brief": {**VARIAN[1]}}))
    b = jalankan()
    assert "varian" not in b and b["full_voice_over"] == NASKAH_B
    assert "MODE DRAF" not in prompt[-1]


def test_pesan_draf_menyebut_masalah_yang_tersisa():
    d = {"draft_id": "0123456789ab", "bahan": ["a"], "brief": {"audio_mode": "ai"},
         "varian": [{**VARIAN[0], "naskah_status": {"masalah": ['huruf asing "确认"'], "ditulis_ulang": False}},
                    {**VARIAN[1], "naskah_status": {"masalah": ["x"], "ditulis_ulang": True, "sisa": []}}]}
    pesan = dn.susun_pesan(d)
    assert pesan.count("Catatan pemeriksa") == 1 and "确认" in pesan


def test_pesan_draf_menampilkan_rencana_grafik():
    plan = {"hook": "Ayo donor!", "cta": "Daftar sekarang",
            "elemen": [{"jenis": "sorot", "teks": "Minggu depan", "saat_kata": "antrean"},
                       {"jenis": "sorot", "teks": "90% hadir", "saat_kata": "ikut"},       # angka karangan
                       {"jenis": "sorot", "teks": "Gratis", "saat_kata": "gratis"}]}     # tidak di naskah
    d = {"draft_id": "0123456789ab", "bahan": ["a"], "brief": {"audio_mode": "ai", "konteks_user": "donor"},
         "varian": [{**VARIAN[0], "motion_plan": plan}, VARIAN[1]]}
    pesan = dn.susun_pesan(d)
    assert 'Grafik: kartu pembuka "Ayo donor!" · sorot "Minggu depan" · kartu ajakan "Daftar sekarang"' in pesan
    assert "90%" not in pesan and "Gratis" not in pesan, "yang tidak lolos pemeriksaan tidak dijanjikan"
    assert pesan.count("Grafik:") == 1, "varian tanpa rencana tidak diberi baris grafik"


def test_motion_tidak_dikenal_ditolak_sebelum_lock(env, capsys):
    args = ["--draft", "--chat-id", CHAT, "--no-require-inspect", "--music", "off", "--motion", "heboh"]
    for b in env["bahan"]:
        args += ["--media-path", b]
    kode, out = jalan(capsys, *args)
    assert kode == 1 and out["kode"] == "gaya_invalid" and env["calls"] == []


def test_motion_mati_tersimpan_di_draf_dan_dipakai_saat_render(env, capsys, monkeypatch):
    d = buat_draf(env, capsys, "--motion", "mati")
    monkeypatch.setenv("MOTION_GRAPHIC", "sedang")      # proses render = proses baru
    jalan(capsys, "--chat-id", CHAT, "--draft-id", d["draft_id"], "--varian", "A")
    assert os.environ.get("MOTION_GRAPHIC") == "mati"


# ------------------------------------------------------------------ naskah vs panjang bahan

PANJANG = " ".join(["kata"] * 60)          # ±22 dtk dibacakan


def _draf_bahan(naskah_a, bahan=13.6, foto=0):
    return {"draft_id": "0123456789ab", "bahan": ["a"],
            "brief": {"audio_mode": "ai", "bahan_layak_detik": bahan, "bahan_foto": foto},
            "varian": [{**VARIAN[0], "full_voice_over": naskah_a}, VARIAN[1]]}


def test_pesan_draf_membandingkan_durasi_dan_menawarkan_pilihan(monkeypatch):
    import broll
    monkeypatch.setattr(broll, "tersedia", lambda: True)
    pesan = dn.susun_pesan(_draf_bahan(PANJANG))
    assert "narasi ±22 dtk · bahan video layak ±14 dtk" in pesan
    assert "naskah A lebih panjang dari bahan video" in pesan, "hanya varian yang kelebihan disebut"
    assert "video tambahan" in pesan and '"stok"' in pesan


def test_tanpa_key_pexels_tidak_menawarkan_stok(monkeypatch):
    import broll
    monkeypatch.setattr(broll, "tersedia", lambda: False)
    pesan = dn.susun_pesan(_draf_bahan(PANJANG))
    assert "video tambahan" in pesan and '"stok"' not in pesan


def test_naskah_muat_tidak_ada_catatan():
    pesan = dn.susun_pesan(_draf_bahan(" ".join(["kata"] * 35)))     # ±13 dtk
    assert "lebih panjang dari bahan" not in pesan


def test_bahan_berfoto_tidak_dianggap_kurang():
    assert dn.bahan_kurang(PANJANG, {"audio_mode": "ai", "bahan_layak_detik": 5, "bahan_foto": 1}) is None


def test_naskah_ubahan_panjang_dicatat_dan_tidak_dikoreksi_otomatis():
    d = _draf_bahan(VARIAN[0]["full_voice_over"])
    d["brief"]["target_duration"] = 30
    b = dn.brief_terpilih(d, "A", PANJANG)
    assert b["full_voice_over"] == PANJANG
    assert any("bahan video layak" in c for c in b["naskah_status"]["catatan"])
    assert b["target_duration"] is None, "koreksi durasi saat render tidak boleh menulis ulang naskah user"


def test_prompt_brief_menyesuaikan_panjang_bahan():
    p = ab.build_prompt(["a.mp4"], {}, jumlah_gambar=1, bahan_layak=13.6)
    assert "sekitar 14 detik SAJA" in p and "±14 detik" in p
    # durasi yang DIMINTA user menang
    p = ab.build_prompt(["a.mp4"], {}, jumlah_gambar=1, bahan_layak=13.6, target_duration=30)
    assert "SAJA" not in p and "30 detik" in p
    # bahan panjang: rentang bawaan
    p = ab.build_prompt(["a.mp4"], {}, jumlah_gambar=1, bahan_layak=50)
    assert "20-35 detik" in p


def test_hitung_bahan_layak_sama_dengan_renderer():
    total = ab.hitung_bahan_layak(["/v1.mp4", "/v2.mp4", "/f.jpg"],
                                  {"/v1.mp4": 5.5, "/v2.mp4": 3.6},
                                  {"/v2.mp4": [(1.75, 3.6, "goyang")]})
    assert total == pytest.approx(5.5 + 1.75 + ab.FOTO_DETIK, abs=0.06)   # dibulatkan 0,1


def test_pesan_draf_mode_suara_asli_menampilkan_ucapan_broll_dan_grafik():
    brief = {"audio_mode": "original", "konteks_user": "",
             "transcript_segments": {"a.mp4": [{"text": "Halo semua, hari ini kita donor darah di aula."}]}}
    plan = {"hook": "Ayo donor", "elemen": [{"jenis": "sorot", "teks": "Aula", "saat_kata": "aula"},
                                            {"jenis": "sorot", "teks": "Pizza", "saat_kata": "pizza"}]}
    d = {"draft_id": "0123456789ab", "bahan": ["a"], "brief": brief,
         "varian": [{**VARIAN[0], "motion_plan": plan,
                     "broll": [{"query": "blood donation", "saat_kata": "donor"}]}, VARIAN[1]]}
    pesan = dn.susun_pesan(d)
    assert 'Subtitle dari ucapanmu: "Halo semua, hari ini kita donor darah di aula."' in pesan
    assert "'blood donation' saat \"donor\"" in pesan
    assert 'sorot "Aula"' in pesan and "Pizza" not in pesan, "jangkar dicek terhadap UCAPAN"
    assert "Teks di layar" not in pesan


def test_huruf_asing_di_caption_ditandai():
    d = {"draft_id": "0123456789ab", "bahan": ["a"], "brief": {"audio_mode": "original"},
         "varian": [{**VARIAN[0], "deskripsi": "OpenClaw yang报错 lalu Hermes"}, VARIAN[1]]}
    pesan = dn.susun_pesan(d)
    assert pesan.count("huruf asing") == 1 and "报错" in pesan


def test_pesan_draf_jujur_bila_model_tidak_melihat_video():
    d = {"draft_id": "0123456789ab", "bahan": ["a", "b"],
         "brief": {"audio_mode": "ai", "tanpa_gambar": True, "pemahaman_bahan": ["x", "y"]},
         "varian": [VARIAN[0], VARIAN[1]]}
    pesan = dn.susun_pesan(d)
    assert "TIDAK bisa melihat video" in pesan and "Saya sudah membaca 2 bahan" in pesan
    assert "menonton" not in pesan
