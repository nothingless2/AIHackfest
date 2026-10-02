"""Preset gaya (scripts/gaya.py + config/gaya/ + remotion/src/tema.js): satu pilihan user menjadi
paket tampilan + editing yang konsisten, tersimpan per pemilik, tanpa mengubah tampilan bawaan."""

import json
import os
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

import gaya
import overlay_remotion as orr


def _args(**isi):
    dasar = {k: None for k in gaya.EDITING}
    return SimpleNamespace(**{**dasar, **isi})


# ------------------------------------------------------------------ preset

def test_semua_preset_sah_dan_klasik_kosong_di_depan():
    nama = gaya.daftar()
    assert nama[0] == gaya.BAWAAN and len(nama) >= 6
    for n in nama:
        p = gaya.muat(n)
        assert p["label"] and p["deskripsi"]
    k = gaya.muat("klasik")
    assert k["tema"] == {} and k["editing"] == {}, "klasik = tampilan bawaan, tanpa penimpaan apa pun"


def test_daftar_subtitle_sama_dengan_renderer():
    import auto_render
    assert set(gaya.SUBTITLE_STYLES) == set(auto_render.SUBTITLE_STYLES)


def test_nilai_editing_yang_diizinkan_lolos_validator_aslinya():
    """Konsistensi lintas-modul: nilai yang KITA izinkan di preset tidak boleh ditolak validator
    knob-nya saat render (user memilih gaya, lalu render gagal)."""
    import motion_plan
    import style
    for v in gaya.EDITING["color_filter"][1]:
        style.resolve_color_filter(v)
    for v in gaya.EDITING["text_font"][1]:
        style.resolve_text_font(v)
    for v in gaya.EDITING["text_position"][1]:
        style.resolve_text_position(v)
    for v in gaya.EDITING["text_animation"][1]:
        orr.animasi_diminta(v)
    for v in gaya.EDITING["motion"][1]:
        motion_plan.tingkat(v)


def test_preset_tidak_mengatur_musik():
    """Uji nyata 1 Okt: preset bermusik 'energik' menggagalkan render (pustaka hanya lagu tenang)."""
    assert "music_mood" not in gaya.EDITING
    with pytest.raises(gaya.GayaError):
        gaya.validasi_editing({"music_mood": "energik"})


@pytest.mark.parametrize("nama", ["../klasik", "klasik/../x", "/etc/passwd", "KLASIK.json", "", "tidak_ada"])
def test_nama_preset_aneh_ditolak(nama):
    with pytest.raises(gaya.GayaError):
        gaya.muat(nama)


# ------------------------------------------------------------------ validasi tema (siap-SaaS)

SAH = {"warna": {"aksen": "#22C55E", "kunci": ["#FFFFFF", "#000000"], "kartu": "#FFFFFFF0", "teks": "#111111"},
       "font": {"judul": "elegan"}, "sudut": 0.5, "cahaya": False, "gerak": "halus"}


def test_tema_sah_diterima():
    """Kontrol positif untuk tes penolakan di bawah."""
    assert gaya.validasi_tema(SAH) == SAH


@pytest.mark.parametrize("ubah", [
    {"warna": {"aksen": "url(http://evil.example/x.png)"}},        # nilai bebas ke CSS -> ditolak
    {"warna": {"aksen": "red"}},
    {"warna": {"aksen": "#FFF"}},
    {"warna": {"aksen": "#22C55E80"}},       # aksen digabung alpha di komponen -> wajib 6 digit
    {"warna": {"latar_belakang": "#FFFFFF"}},
    {"warna": {"kunci": ["#FFFFFF"] * 4}},
    {"warna": {"kunci": "#FFFFFF"}},
    {"font": {"judul": "Comic Sans"}},
    {"font": {"judul": "tegas", "isi": "tegas"}},
    {"sudut": 9},
    {"sudut": True},
    {"cahaya": "ya"},
    {"gerak": "melayang"},
    {"bayangan": "kuat"},
])
def test_tema_tidak_sah_ditolak(ubah):
    with pytest.raises(gaya.GayaError):
        gaya.validasi_tema({**SAH, **ubah})


def test_teks_kartu_yang_tidak_terbaca_ditolak():
    with pytest.raises(gaya.GayaError, match="kontras"):
        gaya.validasi_tema({"warna": {"kartu": "#FFFFFFF0", "teks": "#EEEEEE"}})
    with pytest.raises(gaya.GayaError, match="kontras"):
        gaya.validasi_tema({"warna": {"teks": "#1A1A1A"}})       # di kartu gelap bawaan


def test_editing_tidak_sah_ditolak():
    assert gaya.validasi_editing({"sfx": "off"}) == {"sfx": "off"}
    with pytest.raises(gaya.GayaError):
        gaya.validasi_editing({"sfx": "kadang"})
    with pytest.raises(gaya.GayaError):
        gaya.validasi_editing({"broll": "on"})       # bukan knob yang boleh diatur preset


# ------------------------------------------------------------------ profil & prioritas

def test_prioritas_eksplisit_profil_bawaan():
    assert gaya.pilih(None, "DM A") [1] == "bawaan"
    gaya.simpan_gaya("DM A", "hype")
    p, sumber = gaya.pilih(None, "DM A")
    assert (p["nama"], sumber) == ("hype", "profil")
    p, sumber = gaya.pilih("elegan", "DM A")
    assert (p["nama"], sumber) == ("elegan", "diminta")
    assert gaya.pilih(None, "DM B")[0]["nama"] == "klasik", "profil chat lain tidak berlaku"
    with pytest.raises(gaya.GayaError):
        gaya.pilih("tidak_ada", "DM A")


def test_profil_yang_presetnya_hilang_tidak_menggagalkan_render():
    gaya.simpan_gaya("DM A", "hype")
    path = gaya._berkas_profil("DM A")
    data = json.load(open(path))
    data["gaya"] = "sudah_dihapus"
    json.dump(data, open(path, "w"))
    p, sumber = gaya.pilih(None, "DM A")
    assert p["nama"] == "klasik" and sumber == "bawaan_profil_tidak_berlaku"


@pytest.mark.parametrize("label", ["DM with Stringless", "../../etc/passwd", "a/b", "x" * 200, "Ünïcode 🎬"])
def test_berkas_profil_selalu_di_dalam_folder_profil(label):
    path = os.path.realpath(gaya._berkas_profil(label))
    assert os.path.dirname(path) == os.path.realpath(gaya.PROFIL_DIR)
    gaya.simpan_gaya(label, "promo")
    assert gaya.profil(label)["gaya"] == "promo"


def test_profil_milik_label_lain_tidak_dipakai():
    """Gagal-tertutup: berkas yang isinya milik label lain (tabrakan/rusak) diabaikan."""
    gaya.simpan_gaya("DM A", "hype")
    data = json.load(open(gaya._berkas_profil("DM A")))
    json.dump({**data, "pemilik": "DM B"}, open(gaya._berkas_profil("DM A"), "w"))
    assert gaya.profil("DM A") == {}


def test_lupakan_gaya():
    assert gaya.lupakan_gaya("DM A") is False
    gaya.simpan_gaya("DM A", "hype")
    assert gaya.lupakan_gaya("DM A") is True and gaya.pilih(None, "DM A")[1] == "bawaan"


def test_pemilik_kosong_tidak_punya_profil():
    assert gaya.profil("") == {}
    with pytest.raises(gaya.GayaError):
        gaya.simpan_gaya("", "hype")


# ------------------------------------------------------------------ env

def test_flag_eksplisit_menang_atas_preset():
    hype = gaya.muat("hype")
    env = gaya.env_editing(hype, _args(color_filter="bw"))
    assert "COLOR_FILTER" not in env, "flag user tidak boleh ditimpa preset"
    assert env["SUBTITLE_STYLE"] == "dinamis" and env["ZOOM_WAJAH"] == "on"


def test_pasang_tema_dan_membersihkan_sisa_tema_lama():
    env = {}
    gaya.pasang(gaya.muat("hype"), _args(), env)
    assert env["GAYA_NAMA"] == "hype" and json.loads(env["GAYA_TEMA"])["warna"]["aksen"] == "#22C55E"
    gaya.pasang(gaya.muat("klasik"), _args(), env)
    assert env["GAYA_NAMA"] == "klasik" and "GAYA_TEMA" not in env


def test_tema_dari_env_divalidasi_ulang():
    assert gaya.tema_aktif({}) is None
    with pytest.raises(gaya.GayaError):
        gaya.tema_aktif({"GAYA_TEMA": json.dumps({"warna": {"aksen": "url(x)"}})})
    with pytest.raises(gaya.GayaError):
        gaya.tema_aktif({"GAYA_TEMA": "{bukan json"})


def _props_terkirim(monkeypatch, tmp_path, props):
    """_jalankan_node tanpa Chromium: tangkap props yang akan dikirim ke render.mjs."""
    monkeypatch.setattr(orr.shutil, "which", lambda n: "/usr/bin/node")
    monkeypatch.setattr(orr.os.path, "isdir", lambda p: True)

    class Proses:
        returncode, pid = 0, 0

        def __init__(self, args, **k):
            self.masukan = args[-1]

        def communicate(self, timeout=None):
            return "", ""

    tangkap = {}

    def popen(args, **k):
        tangkap["data"] = json.load(open(args[-1], encoding="utf-8"))
        return Proses(args)

    monkeypatch.setattr(orr.subprocess, "Popen", popen)
    with pytest.raises(orr.OverlayError, match="keluaran animasi tidak ada"):
        orr._jalankan_node(props, [{"jenis": "diam", "frame": 0, "tahan": 1, "mulai": 0}], str(tmp_path))
    return tangkap["data"]["props"]


def test_tema_disuntik_ke_semua_komposisi_lewat_satu_pintu(monkeypatch, tmp_path):
    monkeypatch.delenv("GAYA_TEMA", raising=False)
    assert "tema" not in _props_terkirim(monkeypatch, tmp_path, {"a": 1}), "tanpa preset: props tidak berubah"
    gaya.pasang(gaya.muat("promo"), _args())
    props = _props_terkirim(monkeypatch, tmp_path, {"a": 1})
    assert props["tema"]["warna"]["aksen"] == "#EF4444"
    sendiri = _props_terkirim(monkeypatch, tmp_path, {"a": 1, "tema": {}})
    assert sendiri["tema"] == {}, "props yang sudah membawa tema (pratinjau) tidak ditimpa"


def test_tema_rusak_di_env_jadi_overlay_error(monkeypatch, tmp_path):
    """Pemanggil menangkap OverlayError lalu jatuh ke teks statis dan melaporkannya."""
    monkeypatch.setenv("GAYA_TEMA", json.dumps({"warna": {"aksen": "url(x)"}}))
    with pytest.raises(orr.OverlayError, match="tema gaya"):
        orr._jalankan_node({}, [], str(tmp_path))


# ------------------------------------------------------------------ titik masuk render

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
                        ("SUBTITLE_STYLE", "COLOR_FILTER", "SFX", "GAYA_NAMA", "GAYA_TEMA")})
        raise RuntimeError("berhenti di sini")

    monkeypatch.setattr(hr, "run_core_stages_locked", palsu)
    monkeypatch.setattr(hr, "pick_track", lambda *a, **k: None)
    monkeypatch.setattr(hr, "_stage_assets", lambda paths, pre: [f"{pre}_x.mp4" for _ in paths])
    try:
        rc = hr.main(["--media-path", str(v), "--no-require-inspect", "--chat-id", "DM A", *argv])
    except RuntimeError:
        rc = None
    return rc, tangkap


def test_render_memakai_preset_dan_flag_user_tetap_menang(monkeypatch, tmp_path):
    for k in ("SUBTITLE_STYLE", "COLOR_FILTER", "SFX", "GAYA_TEMA"):
        monkeypatch.delenv(k, raising=False)
    _, t = _main(monkeypatch, tmp_path, ["--gaya", "hype", "--color-filter", "bw"])
    assert t["GAYA_NAMA"] == "hype" and t["SUBTITLE_STYLE"] == "dinamis" and t["SFX"] == "on"
    assert t["COLOR_FILTER"] == "bw", "flag eksplisit menang atas preset"
    assert json.loads(t["GAYA_TEMA"])["warna"]["aksen"] == "#22C55E"


def test_render_tanpa_flag_memakai_gaya_profil(monkeypatch, tmp_path):
    monkeypatch.delenv("GAYA_TEMA", raising=False)
    _, t = _main(monkeypatch, tmp_path, [])
    assert t["GAYA_NAMA"] == "klasik" and t["GAYA_TEMA"] is None, "kontrol: tanpa profil = klasik"
    gaya.simpan_gaya("DM A", "elegan")
    _, t = _main(monkeypatch, tmp_path, [])
    assert t["GAYA_NAMA"] == "elegan"


def test_gaya_tidak_dikenal_ditolak_sebelum_render(monkeypatch, tmp_path, capsys):
    rc, t = _main(monkeypatch, tmp_path, ["--gaya", "tidak_ada"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 1 and out["kode"] == "gaya_invalid" and t == {}, "berhenti sebelum tahap render"


# ------------------------------------------------------------------ CLI

def test_cli_pakai_lihat_lupakan(capsys):
    def jalan(*a):
        rc = gaya.main(list(a))
        return rc, json.loads(capsys.readouterr().out)
    rc, out = jalan("lihat", "--chat-id", "DM A")
    assert rc == 0 and out["gaya"] is None
    rc, out = jalan("pakai", "--chat-id", "DM A", "--gaya", "Promo")
    assert rc == 0 and out["gaya"]["nama"] == "promo"
    assert jalan("lihat", "--chat-id", "DM A")[1]["gaya"]["nama"] == "promo"
    rc, out = jalan("pakai", "--chat-id", "DM A", "--gaya", "tidak_ada")
    assert rc == 1 and not out["ok"] and "Pilihan" in out["alasan"]
    assert jalan("lupakan", "--chat-id", "DM A")[1]["dihapus"] is True


def test_pratinjau_dicache_dan_selalu_salinan_baru(monkeypatch, tmp_path):
    panggil = []

    def render(presets, out):
        panggil.append([p["nama"] for p in presets])
        open(out, "wb").write(b"jpg")
        return out
    monkeypatch.setattr(orr, "render_pratinjau_gaya", render)
    a = gaya.pratinjau(str(tmp_path / "kirim"))
    b = gaya.pratinjau(str(tmp_path / "kirim"))
    assert len(panggil) == 1 and panggil[0] == gaya.daftar(), "render sekali, lalu dari cache"
    assert a != b and os.path.exists(a) and os.path.exists(b), "tiap permintaan berkas baru (gateway)"


# ------------------------------------------------------------------ render sungguhan (piksel)

W, H = 270, 480


def _png(path):
    from PIL import Image
    return np.asarray(Image.open(path).convert("RGBA")).astype(int)


def _render(komposisi, props, frame, folder):
    os.makedirs(folder, exist_ok=True)
    (p,) = orr._jalankan_node({"lebar": W, "tinggi": H, "fps": 24, **props},
                              [{"jenis": "diam", "frame": frame, "tahan": 1, "mulai": 0}], folder,
                              komposisi=komposisi, batas=180)
    return _png(p["out"])


MOTION = {"durasi": 2, "aksen": orr.MOTION_AKSEN, "tata": "atas", "items": [
    {"jenis": "sorot", "teks": "Gratis", "mulai": 0, "selesai": 2, "masukFrames": 12, "keluarFrames": 8}]}


def _rona(px, hex_, toleransi=60):
    """Jumlah piksel opak yang dekat warna hex."""
    target = np.array([int(hex_[i:i + 2], 16) for i in (1, 3, 5)])
    opak = px[..., 3] > 200
    return int(((np.abs(px[..., :3] - target).sum(axis=2) < toleransi) & opak).sum())


def _hangat(px):
    rgb = px[..., :3]
    # > 225: kotak kartu pembicara #DADAD6 (218) tidak dihitung; < 252: teks putih juga tidak.
    terang = (rgb.min(axis=2) > 225) & (rgb.max(axis=2) < 252) & ((rgb.max(axis=2) - rgb.min(axis=2)) < 30)
    return float((rgb[..., 0] - rgb[..., 2])[terang].mean())


def test_render_klasik_identik_dengan_tanpa_tema(monkeypatch, tmp_path):
    """Tema kosong (preset klasik) tidak boleh mengubah satu piksel pun."""
    monkeypatch.delenv("GAYA_TEMA", raising=False)
    tanpa = _render("MotionOverlay", MOTION, 14, str(tmp_path / "a"))
    klasik = _render("MotionOverlay", {**MOTION, "tema": {}}, 14, str(tmp_path / "b"))
    assert (tanpa == klasik).all()
    assert _rona(tanpa, orr.MOTION_AKSEN) > 50, "kontrol positif: pil sorot ungu bawaan terlihat"


def test_render_hype_memakai_aksen_hijau_bukan_ungu(monkeypatch, tmp_path):
    monkeypatch.delenv("GAYA_TEMA", raising=False)
    hype = _render("MotionOverlay", {**MOTION, "tema": gaya.muat("hype")["tema"]}, 14, str(tmp_path / "h"))
    assert _rona(hype, "#22C55E") > 50, "aksen preset tampil"
    assert _rona(hype, orr.MOTION_AKSEN) < 5, "aksen ungu bawaan tidak tersisa"


def test_render_panggung_dan_caption_ikut_tema_yang_sama(monkeypatch, tmp_path):
    """Konsistensi lintas komponen: satu preset mengganti latar panggung dan warna kata kunci."""
    monkeypatch.delenv("GAYA_TEMA", raising=False)
    elegan = gaya.muat("elegan")["tema"]
    pg = {"durasi": 1, "items": [{"ilustrasi": "kata", "teks": "edit", "dari": 0, "dur": 24}],
          "kartu": orr.kartu_panggung(W, H)}
    a = _render("Panggung", pg, 20, str(tmp_path / "pa"))
    b = _render("Panggung", {**pg, "tema": elegan}, 20, str(tmp_path / "pb"))
    # Kehangatan kertas (rata-rata R-B pada piksel terang tak jenuh): tahan terhadap bayangan
    # kartu yang di kanvas kecil menutupi hampir semua kertas. #F4F4F1 -> 3, #F7F3EA -> 13.
    assert _hangat(a) < 5, f"kontrol: kertas bawaan netral ({_hangat(a):.1f})"
    assert _hangat(b) - _hangat(a) > 4, f"kertas elegan lebih hangat ({_hangat(b):.1f} vs {_hangat(a):.1f})"
    cap = {"durasi": 1, "items": [{"kata": ["ini", "rahasia"], "kunci": 1, "mulai": 0, "selesai": 1,
                                   "masukFrames": 8, "varian": "biasa"}]}
    promo = gaya.muat("promo")["tema"]
    c = _render("CaptionDinamis", {**cap, "tema": promo}, 12, str(tmp_path / "c"))
    assert _rona(c, "#FACC15", 70) > 30, "kata kunci memakai gradien kuning preset promo"


# ------------------------------------------------------------------ gaya kustom (racikan user)

def test_palet_diturunkan_kode_dari_satu_warna():
    p = gaya.palet_dari_aksen("#b91c1c")
    assert p["aksen"] == "#B91C1C" and len(p["kunci"]) == 3 and p["kunci"][-1] == "#B91C1C"
    gaya.validasi_tema({"warna": p})                      # hasil turunan selalu sah
    assert gaya._luminans(p["sorot"]) > gaya._luminans(p["aksen"]) > gaya._luminans(p["aksen2"])
    with pytest.raises(gaya.GayaError):
        gaya.palet_dari_aksen("merah")


def test_kustom_tersimpan_di_profil_dan_dipakai_otomatis():
    assert "kustom" not in gaya.daftar(), "kustom bukan berkas preset"
    with pytest.raises(gaya.GayaError):
        gaya.pilih("kustom", "DM A")                       # belum pernah dibuat
    p = gaya.simpan_kustom("DM A", "hype", {"warna": {"aksen": "#B91C1C"}, "font": {"judul": "elegan"}})
    assert p["nama"] == "kustom" and p["dasar"] == "hype" and p["editing"] == gaya.muat("hype")["editing"]
    assert p["tema"]["warna"]["aksen"] == "#B91C1C" and p["tema"]["font"] == {"judul": "elegan"}
    assert p["tema"]["gerak"] == "tegas", "yang tidak diubah ikut preset dasar"
    q, sumber = gaya.pilih(None, "DM A")
    assert (q["nama"], sumber) == ("kustom", "profil") and q["tema"] == p["tema"]
    assert gaya.pilih(None, "DM B")[0]["nama"] == "klasik", "kustom chat lain tidak berlaku"


def test_kustom_bertahap_dan_bisa_kembali_setelah_ganti_preset():
    gaya.simpan_kustom("DM A", "hype", {"warna": {"aksen": "#B91C1C"}})
    p = gaya.simpan_kustom("DM A", None, {"font": {"judul": "elegan"}})
    assert p["tema"]["warna"]["aksen"] == "#B91C1C" and p["dasar"] == "hype", "ubahan kedua menambah, bukan mengganti"
    gaya.simpan_gaya("DM A", "bersih")
    assert gaya.pilih(None, "DM A")[0]["nama"] == "bersih"
    gaya.simpan_gaya("DM A", "kustom")
    assert gaya.pilih(None, "DM A")[0]["tema"]["warna"]["aksen"] == "#B91C1C"


@pytest.mark.parametrize("ubahan", [
    {"warna": {"aksen": "url(http://evil.example/x)"}},
    {"warna": {"teks": "#111111"}},                         # teks gelap di kartu gelap: tak terbaca
    {"font": {"judul": "Comic Sans"}},
    {"sudut": 5},
])
def test_kustom_tidak_sah_ditolak_dan_profil_tidak_berubah(ubahan):
    gaya.simpan_gaya("DM A", "hype")
    with pytest.raises(gaya.GayaError):
        gaya.simpan_kustom("DM A", "hype", ubahan)
    assert gaya.profil("DM A").get("gaya") == "hype" and "kustom" not in gaya.profil("DM A")


def test_kustom_yang_dasarnya_hilang_tidak_menggagalkan_render():
    gaya.simpan_kustom("DM A", "hype", {"warna": {"aksen": "#B91C1C"}})
    path = gaya._berkas_profil("DM A")
    data = json.load(open(path))
    data["kustom"]["dasar"] = "sudah_dihapus"
    json.dump(data, open(path, "w"))
    assert gaya.pilih(None, "DM A")[1] == "bawaan_profil_tidak_berlaku"


def test_cli_kustom_menyimpan_dan_memberi_pratinjau(monkeypatch, capsys):
    monkeypatch.setattr(orr, "render_pratinjau_gaya", lambda presets, out: open(out, "wb").write(b"jpg") and out)
    assert gaya.main(["kustom", "--chat-id", "DM A", "--dasar", "elegan", "--aksen", "#0EA5E9", "--cahaya", "on"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["gaya"]["nama"] == "kustom" and out["dasar"] == "elegan" and out["tema"]["cahaya"] is True
    assert out["gambar"] and os.path.exists(out["gambar"])
    assert gaya.main(["kustom", "--chat-id", "DM A"]) == 1, "tanpa ubahan ditolak"
    capsys.readouterr()
    assert gaya.main(["lihat", "--chat-id", "DM A"]) == 0
    assert json.loads(capsys.readouterr().out)["tema"]["warna"]["aksen"] == "#0EA5E9"


def test_render_memakai_tema_kustom_dari_profil(monkeypatch, tmp_path):
    monkeypatch.delenv("GAYA_TEMA", raising=False)
    gaya.simpan_kustom("DM A", "hype", {"warna": {"aksen": "#B91C1C"}})
    _, t = _main(monkeypatch, tmp_path, [])
    assert t["GAYA_NAMA"] == "kustom" and json.loads(t["GAYA_TEMA"])["warna"]["aksen"] == "#B91C1C"
    assert t["SUBTITLE_STYLE"] == "dinamis", "editing ikut preset dasar (hype)"
