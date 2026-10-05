"""Gaya BUATAN USER (scripts/gaya.py `buat`): bernama, banyak per chat, dan mengatur empat bagian --
tema, editing, audio (musik/suara), carousel. Gaya bawaan (config/gaya/) tidak berubah."""

import json
import os
from types import SimpleNamespace

import pytest

import carousel
import gaya
import music
import overlay_remotion as orr

A = "DM A"


def _args(**isi):
    dasar = {k: None for k in list(gaya.EDITING) + [v[2] for v in gaya.AUDIO.values() if v[2]]}
    return SimpleNamespace(**{**dasar, "music_file": None, "revisi": None, "ganti_musik": False, **isi})


@pytest.fixture(autouse=True)
def _pustaka_musik(monkeypatch):
    """Pustaka tetap: satu lagu tenang (nama berkas) + satu yang hanya dikenali lewat analisis."""
    monkeypatch.setattr(music, "list_tracks", lambda folder=None: ["/musik/lofi_tenang.mp3", "/musik/trek2.mp3"])
    monkeypatch.setattr(music, "_cocok_lewat_analisis",
                        lambda tracks, kata: ["/musik/trek2.mp3"] if kata == "upbeat" else [])


# ------------------------------------------------------------------ daftar nilai tetap sinkron

def test_daftar_tertutup_sama_dengan_modul_aslinya():
    import music_mood
    assert gaya.MOODS == music_mood.MOODS
    assert gaya.CAROUSEL_SLIDE == (carousel.SLIDE_MIN, carousel.SLIDE_MAX)
    assert gaya.LEVEL_MUSIK["sedang"] == 10.0, "sedang = bawaan terukur di music.py"
    assert gaya.LEVEL_MUSIK["pelan"] > gaya.LEVEL_MUSIK["sedang"] > gaya.LEVEL_MUSIK["keras"]
    assert gaya.LEPAS not in {v for t in (gaya.EDITING, gaya.AUDIO) for d in t.values() for v in d[1]}


def test_flag_audio_gaya_sama_dengan_flag_render():
    """Tiap pengaturan audio gaya harus menulis ENV yang SAMA dengan flag render-nya; kalau tidak,
    'flag user menang' hanya di atas kertas."""
    import hermes_render as hr
    for kunci, (env, nilai, dest) in gaya.AUDIO.items():
        if not dest:
            continue
        os.environ.pop(env, None)
        args = hr._parser().parse_args([])
        assert getattr(args, dest) is None, f"{dest}: bawaan flag harus None supaya gaya bisa mengisi"
        setattr(args, dest, nilai[0])
        hr._apply_env(args)
        assert os.environ[env] == nilai[0], kunci


def test_level_musik_mengubah_gain_sesuai_selisih_db(monkeypatch):
    """Kontrol angka: 'keras' (7 dB) vs 'pelan' (14 dB) = musik 7 dB lebih keras, bukan label kosong."""
    import math
    monkeypatch.setattr(music, "loudness", lambda p: -20.0 if p == "v" else -30.0)
    keras = music.auto_volume("v", "t", below_db=gaya.LEVEL_MUSIK["keras"])
    pelan = music.auto_volume("v", "t", below_db=gaya.LEVEL_MUSIK["pelan"])
    assert 20 * math.log10(keras / pelan) == pytest.approx(7.0, abs=0.01)


# ------------------------------------------------------------------ membuat & memilih

def test_beberapa_gaya_bernama_per_chat_dan_terpisah_antar_chat():
    p = gaya.simpan_gaya_saya(A, "kopi-senja", "elegan", {"tema": {"warna": {"aksen": "#B45309"}}})
    assert p["nama"] == "kopi-senja" and p["label"] == "Kopi Senja" and p["dasar"] == "elegan"
    assert p["tema"]["warna"]["aksen"] == "#B45309" and len(p["tema"]["warna"]["kunci"]) == 3
    gaya.simpan_gaya_saya(A, "promo_kilat", "hype", {"editing": {"subtitle_style": "kata"}})
    assert [x["nama"] for x in gaya.daftar_milik(A)] == ["kopi-senja", "promo_kilat"]
    assert gaya.pilih(None, A)[0]["nama"] == "promo_kilat", "yang terakhir dibuat jadi gaya aktif"
    q, sumber = gaya.pilih("kopi-senja", A)
    assert (q["nama"], sumber) == ("kopi-senja", "diminta")
    assert gaya.daftar_milik("DM B") == []
    with pytest.raises(gaya.GayaError, match="tidak dikenal"):
        gaya.pilih("kopi-senja", "DM B")
    assert "kopi-senja" not in gaya.daftar(), "gaya buatan user bukan preset bawaan"


def test_editing_bisa_ditimpa_dan_sisanya_ikut_dasar():
    p = gaya.simpan_gaya_saya(A, "punyaku", "hype", {"editing": {"subtitle_style": "kata", "sfx": "off"}})
    hype = gaya.muat("hype")["editing"]
    assert p["editing"]["subtitle_style"] == "kata" and p["editing"]["sfx"] == "off"
    assert p["editing"]["color_filter"] == hype["color_filter"] == "vivid"
    assert gaya.muat("hype")["editing"]["sfx"] == "on", "preset bawaan tidak ikut berubah"


def test_ubahan_bertahap_dan_bawaan_melepas_pengaturan():
    gaya.simpan_gaya_saya(A, "punyaku", "hype", {"audio": {"musik": "on", "level_musik": "keras"}})
    p = gaya.simpan_gaya_saya(A, "punyaku", None, {"carousel": {"jumlah": 8}})
    assert p["audio"] == {"musik": "on", "level_musik": "keras"} and p["carousel"] == {"jumlah": 8}
    assert p["dasar"] == "hype", "dasar tidak berubah bila tidak disebut"
    p = gaya.simpan_gaya_saya(A, "punyaku", None, {"audio": {"level_musik": None}, "editing": {"sfx": None}})
    assert p["audio"] == {"musik": "on"} and p["editing"]["sfx"] == "on", "dilepas = kembali ke dasar (hype: on)"


@pytest.mark.parametrize("nama", ["", "A", "Kopi Senja", "../x", "x" * 40, "1kopi", "kopi.json", "hype", "klasik"])
def test_nama_tidak_sah_atau_bentrok_ditolak(nama):
    with pytest.raises(gaya.GayaError):
        gaya.simpan_gaya_saya(A, nama, None, {"editing": {"sfx": "off"}})
    assert gaya.profil(A) == {}


@pytest.mark.parametrize("ubahan", [
    {"tema": {"warna": {"aksen": "url(http://evil.example/x)"}}},
    {"editing": {"subtitle_style": "tidak-ada"}},
    {"editing": {"broll": "on"}},
    {"audio": {"musik": "kadang"}},
    {"audio": {"suasana_musik": "lofi; rm -rf /"}},
    {"audio": {"volume": "11"}},
    {"carousel": {"jumlah": 99}},
    {"carousel": {"jumlah": True}},
    {"carousel": {"platform": "facebook"}},
    {"carousel": {"latar": "/etc/passwd"}},
    {"skrip": {"jalankan": "x"}},
])
def test_isi_tidak_sah_ditolak_dan_profil_tidak_berubah(ubahan):
    gaya.simpan_gaya_saya(A, "punyaku", "hype", {"editing": {"sfx": "off"}})      # kontrol: yang sah tersimpan
    sebelum = gaya.profil(A)
    with pytest.raises(gaya.GayaError):
        gaya.simpan_gaya_saya(A, "punyaku", None, ubahan)
    with pytest.raises(gaya.GayaError):
        gaya.simpan_gaya_saya(A, "baru", "hype", ubahan)
    assert gaya.profil(A) == sebelum


def test_batas_jumlah_gaya_per_chat(monkeypatch):
    monkeypatch.setattr(gaya, "MAKS_GAYA_SAYA", 2)
    gaya.simpan_gaya_saya(A, "satu", None, {"editing": {"sfx": "off"}})
    gaya.simpan_gaya_saya(A, "dua", None, {"editing": {"sfx": "off"}})
    with pytest.raises(gaya.GayaError, match="Hapus"):
        gaya.simpan_gaya_saya(A, "tiga", None, {"editing": {"sfx": "off"}})
    gaya.simpan_gaya_saya(A, "dua", None, {"editing": {"sfx": "on"}})             # mengubah yang ada tetap boleh
    assert gaya.pilih("dua", A)[0]["editing"]["sfx"] == "on"


def test_hapus_melepas_gaya_aktif():
    gaya.simpan_gaya_saya(A, "satu", None, {"editing": {"sfx": "off"}})
    gaya.simpan_gaya_saya(A, "dua", None, {"editing": {"sfx": "off"}})
    assert gaya.hapus_gaya_saya(A, "satu") == (True, False), "yang dihapus bukan gaya aktif"
    assert gaya.hapus_gaya_saya(A, "satu") == (False, False)
    assert gaya.hapus_gaya_saya("DM B", "dua") == (False, False), "chat lain tidak bisa menghapusnya"
    assert gaya.hapus_gaya_saya(A, "dua") == (True, True)
    assert gaya.pilih(None, A)[1] == "bawaan" and gaya.daftar_milik(A) == []


def test_profil_yang_diubah_di_disk_divalidasi_ulang():
    """Profil adalah berkas: isi yang disunting dari luar tidak boleh sampai ke render."""
    gaya.simpan_gaya_saya(A, "punyaku", "hype", {"audio": {"musik": "on"}})
    path = gaya._berkas_profil(A)
    data = json.load(open(path))
    data["gaya_saya"]["punyaku"]["audio"] = {"musik": "on; curl evil"}
    data["gaya_saya"]["../x"] = {"dasar": "hype"}
    json.dump(data, open(path, "w"))
    assert gaya.pilih(None, A)[1] == "bawaan_profil_tidak_berlaku"
    with pytest.raises(gaya.GayaError):
        gaya.pilih("punyaku", A)
    assert gaya.daftar_milik(A) == [], "gaya rusak dan nama aneh tidak ditawarkan"


# ------------------------------------------------------------------ audio

def test_suasana_musik_dicek_ke_pustaka_saat_disimpan(monkeypatch):
    p = gaya.simpan_gaya_saya(A, "kalem", None, {"audio": {"suasana_musik": "tenang"}})        # cocok nama berkas
    assert p["audio"]["suasana_musik"] == "tenang"
    gaya.simpan_gaya_saya(A, "ceria", None, {"audio": {"suasana_musik": "upbeat"}})            # cocok lewat analisis
    sebelum = gaya.profil(A)
    with pytest.raises(gaya.GayaError, match="belum punya lagu bernuansa energik"):
        gaya.simpan_gaya_saya(A, "keras", None, {"audio": {"suasana_musik": "energik"}})
    assert gaya.profil(A) == sebelum
    monkeypatch.setattr(music, "list_tracks", lambda folder=None: [])
    with pytest.raises(gaya.GayaError, match="kosong"):
        gaya.simpan_gaya_saya(A, "kalem2", None, {"audio": {"suasana_musik": "tenang"}})


def test_audio_gaya_dipasang_dan_flag_user_menang():
    p = gaya.simpan_gaya_saya(A, "punyaku", None, {"audio": {
        "musik": "on", "suasana_musik": "tenang", "level_musik": "keras", "bersih_suara": "off",
        "suara_narasi": "pria"}})
    env = {}
    gaya.pasang(p, _args(), env)
    assert env["CONTENT_FACTORY_MUSIC"] == "on" and env["CONTENT_FACTORY_MUSIC_MOOD"] == "tenang"
    assert env["MUSIC_BELOW_SPEECH_DB"] == "7" and env["BERSIH_SUARA"] == "off" and env["TTS_VOICE_GENDER"] == "pria"
    e = gaya.env_audio(p, _args(music="off", voice="wanita"))
    assert "CONTENT_FACTORY_MUSIC" not in e and "TTS_VOICE_GENDER" not in e, "flag user tidak ditimpa gaya"
    assert e["CONTENT_FACTORY_MUSIC_MOOD"] == "tenang"


def test_lagu_kiriman_user_dan_revisi_tidak_dikalahkan_gaya():
    p = gaya.simpan_gaya_saya(A, "punyaku", None, {"audio": {"musik": "off", "suasana_musik": "tenang",
                                                             "level_musik": "pelan"}})
    e = gaya.env_audio(p, _args(music_file="/cache/audio/laguku.mp3"))
    assert e == {"MUSIC_BELOW_SPEECH_DB": "14"}, "lagu kiriman user: 'musik off' dan suasana gaya tidak berlaku"
    e = gaya.env_audio(p, _args(revisi="run123"))
    assert "CONTENT_FACTORY_MUSIC_MOOD" not in e, "revisi tanpa ganti lagu: lagu lama tidak boleh berganti"
    assert gaya.env_audio(p, _args(revisi="run123", ganti_musik=True))["CONTENT_FACTORY_MUSIC_MOOD"] == "tenang"


def test_preset_bawaan_tidak_membawa_audio_atau_carousel():
    for n in gaya.daftar():
        p = gaya.muat(n)
        assert gaya.env_audio(p, _args()) == {} and not p.get("carousel")
    with pytest.raises(gaya.GayaError):
        gaya.validasi({"label": "X", "audio": {"musik": "on"}}, "x")


def _main(monkeypatch, tmp_path, argv):
    import hermes_render as hr
    root = tmp_path / "cache"
    root.mkdir(exist_ok=True)
    v = root / "v.mp4"
    v.write_bytes(b"x")
    monkeypatch.setattr(hr, "MEDIA_ROOTS", [str(root)])
    for n in ("install_signal_handlers", "sweep_old_run_files", "ensure_dirs", "log_event"):
        monkeypatch.setattr(hr, n, lambda *a, **k: None)
    tangkap = {}

    def palsu(*a, **k):
        tangkap.update({k_: os.environ.get(k_) for k_ in
                        ("GAYA_NAMA", "SUBTITLE_STYLE", "CONTENT_FACTORY_MUSIC", "CONTENT_FACTORY_MUSIC_MOOD",
                         "MUSIC_BELOW_SPEECH_DB", "BERSIH_SUARA")})
        raise RuntimeError("berhenti di sini")

    monkeypatch.setattr(hr, "run_core_stages_locked", palsu)
    monkeypatch.setattr(hr, "pick_track", lambda *a, **k: None)
    monkeypatch.setattr(hr, "_stage_assets", lambda paths, pre: [f"{pre}_x.mp4" for _ in paths])
    try:
        rc = hr.main(["--media-path", str(v), "--no-require-inspect", "--chat-id", A, *argv])
    except RuntimeError:
        rc = None
    return rc, tangkap


def test_render_memakai_gaya_buatan_sendiri_dari_profil(monkeypatch, tmp_path):
    # os.environ.pop, BUKAN monkeypatch.delenv: delenv di tengah tes mencatat nilai yang baru dipasang
    # render sebagai "nilai asli", lalu memulihkannya SETELAH fixture pemulih env conftest -- bocor ke
    # tes berikutnya (terukur 4 Okt: test_revisi gagal dengan "Tidak ada musik bernuansa 'tenang'").
    for k in ("SUBTITLE_STYLE", "CONTENT_FACTORY_MUSIC", "CONTENT_FACTORY_MUSIC_MOOD", "MUSIC_BELOW_SPEECH_DB",
              "BERSIH_SUARA", "GAYA_TEMA"):
        os.environ.pop(k, None)
    _, t = _main(monkeypatch, tmp_path, [])
    assert t["GAYA_NAMA"] == "klasik" and t["CONTENT_FACTORY_MUSIC_MOOD"] is None, "kontrol: tanpa gaya = tanpa audio gaya"
    gaya.simpan_gaya_saya(A, "kopi-senja", "hype", {"editing": {"subtitle_style": "kata"}, "audio": {
        "musik": "on", "suasana_musik": "tenang", "level_musik": "pelan", "bersih_suara": "off"}})
    _, t = _main(monkeypatch, tmp_path, [])
    assert t == {"GAYA_NAMA": "kopi-senja", "SUBTITLE_STYLE": "kata", "CONTENT_FACTORY_MUSIC": "on",
                 "CONTENT_FACTORY_MUSIC_MOOD": "tenang", "MUSIC_BELOW_SPEECH_DB": "14", "BERSIH_SUARA": "off"}
    for k in ("CONTENT_FACTORY_MUSIC", "CONTENT_FACTORY_MUSIC_MOOD"):
        os.environ.pop(k, None)
    _, t = _main(monkeypatch, tmp_path, ["--music", "off", "--subtitle-style", "capcut"])
    assert t["CONTENT_FACTORY_MUSIC"] == "off" and t["SUBTITLE_STYLE"] == "capcut", "flag run ini menang"


# ------------------------------------------------------------------ carousel

def test_carousel_mengambil_bawaan_dari_gaya_dan_flag_menang():
    polos = gaya.muat("klasik")
    assert carousel.terapkan_gaya(polos) == ("ig", carousel.JUMLAH_BAWAAN, False, None, []), "kontrol: bawaan lama"
    p = gaya.simpan_gaya_saya(A, "punyaku", None, {"carousel": {
        "jumlah": 8, "platform": "keduanya", "latar": "stok", "foto_stok": "on"}})
    platform, jumlah, stok, latar, dari = carousel.terapkan_gaya(p)
    assert (platform, jumlah, stok, latar) == ("keduanya", 8, True, "stok")
    assert sorted(dari) == ["foto_stok", "jumlah", "latar", "platform"]
    platform, jumlah, stok, latar, dari = carousel.terapkan_gaya(p, platform="ig", jumlah=4, stok=False, latar="polos")
    assert (platform, jumlah, stok, latar, dari) == ("ig", 4, False, None, [])
    assert carousel.terapkan_gaya(p, latar="/cache/images/a.jpg")[3] == "/cache/images/a.jpg"


def test_carousel_cli_tanpa_flag_tidak_mengunci_bawaan():
    """`--platform`/`--jumlah` dulu berbawaan 'ig'/6: gaya tidak akan pernah terpakai."""
    tangkap = {}

    def palsu(**k):
        tangkap.update(k)
        raise carousel.CarouselError("uji", "berhenti")
    asli, carousel.buat = carousel.buat, palsu
    try:
        assert carousel.main(["--chat-id", A, "--teks", "x"]) == 1
        assert (tangkap["platform"], tangkap["jumlah"], tangkap["stok"], tangkap["latar"]) == (None, None, None, None)
        carousel.main(["--chat-id", A, "--teks", "x", "--tanpa-stok", "--jumlah", "4"])
        assert tangkap["stok"] is False and tangkap["jumlah"] == 4
        carousel.main(["--chat-id", A, "--teks", "x", "--stok"])
        assert tangkap["stok"] is True
    finally:
        carousel.buat = asli


# ------------------------------------------------------------------ CLI

def _jalan(capsys, *a):
    rc = gaya.main(list(a))
    return rc, json.loads(capsys.readouterr().out)


def test_cli_buat_lihat_daftar_pakai_hapus(monkeypatch, capsys):
    monkeypatch.setattr(orr, "render_pratinjau_gaya", lambda presets, out: open(out, "wb").write(b"jpg") and out)
    rc, out = _jalan(capsys, "buat", "--chat-id", A, "--nama", "Kopi-Senja", "--dasar", "elegan", "--aksen", "#B45309",
                     "--subtitle-style", "kata", "--musik", "on", "--suasana-musik", "tenang", "--level-musik", "pelan",
                     "--carousel-jumlah", "7", "--carousel-latar", "stok")
    assert rc == 0 and out["gaya"]["nama"] == "kopi-senja" and out["dasar"] == "elegan"
    assert out["editing"]["subtitle_style"] == "kata" and out["carousel"] == {"latar": "stok", "jumlah": 7}
    assert out["audio"] == {"musik": "on", "suasana_musik": "tenang", "level_musik": "pelan"}
    assert out["gambar"] and os.path.exists(out["gambar"])

    rc, out = _jalan(capsys, "buat", "--chat-id", A, "--nama", "kopi-senja", "--level-musik", "bawaan")
    assert rc == 0 and "level_musik" not in out["audio"] and out["audio"]["musik"] == "on"
    rc, out = _jalan(capsys, "buat", "--chat-id", A, "--nama", "kopi-senja", "--suasana-musik", "energik")
    assert rc == 1 and "energik" in out["alasan"]
    rc, out = _jalan(capsys, "buat", "--chat-id", A, "--nama", "kopi-senja", "--carousel-jumlah", "banyak")
    assert rc == 1 and "3-10" in out["alasan"]
    assert _jalan(capsys, "buat", "--chat-id", A, "--nama", "kopi-senja")[0] == 1, "tanpa ubahan ditolak"
    assert _jalan(capsys, "buat", "--chat-id", "", "--nama", "x1", "--sfx", "off")[0] == 1, "chat tak dikenal ditolak"

    rc, out = _jalan(capsys, "daftar", "--chat-id", A)
    assert out["aktif"] == "kopi-senja" and [g["nama"] for g in out["gaya_saya"]] == ["kopi-senja"]
    assert "gaya_saya" not in _jalan(capsys, "daftar")[1], "tanpa chat: hanya gaya bawaan"
    assert _jalan(capsys, "pakai", "--chat-id", A, "--gaya", "hype")[1]["gaya"]["nama"] == "hype"
    assert _jalan(capsys, "pakai", "--chat-id", A, "--gaya", "kopi-senja")[1]["gaya"]["label"] == "Kopi Senja"
    assert _jalan(capsys, "lihat", "--chat-id", A)[1]["audio"]["musik"] == "on"
    assert _jalan(capsys, "pakai", "--chat-id", "DM B", "--gaya", "kopi-senja")[0] == 1, "milik chat lain"

    rc, out = _jalan(capsys, "hapus", "--chat-id", A, "--nama", "kopi-senja")
    assert rc == 0 and out["gaya_aktif_dilepas"] is True
    assert _jalan(capsys, "hapus", "--chat-id", A, "--nama", "kopi-senja")[0] == 1
    assert _jalan(capsys, "lihat", "--chat-id", A)[1]["gaya"] is None


def test_cli_pilihan_memuat_semua_nilai_sah(capsys):
    rc, out = _jalan(capsys, "pilihan")
    assert rc == 0 and out["audio"]["suasana_musik"] == list(gaya.MOODS)
    assert out["editing"]["subtitle_style"] == list(gaya.SUBTITLE_STYLES) and out["carousel"]["jumlah"] == [3, 10]
    for bagian in ("editing", "audio"):
        for kunci, nilai in out[bagian].items():
            getattr(gaya, "validasi_" + bagian)({kunci: nilai[0]})
