"""Alokasi narasi ke bahan (scripts/alokasi.py): tidak ada potongan yang melebihi sumbernya,
tidak ada loop, dan pengisian dilaporkan. Angka dari kasus nyata 24 Sep: klip 5,5 / 3,6
(layak 1,75, sisanya goyang) / 6,4 dtk; narasi 22,15 dtk -> klip 2 terulang ±4x."""

import pytest

from alokasi import LAMBAT_MIN, susun_potongan

NYATA = [{"path": "a", "rentang": [(0.0, 5.5)]},
         {"path": "b", "rentang": [(0.0, 1.75)]},
         {"path": "c", "rentang": [(0.0, 6.4)]}]


def _total(pot):
    return sum(p["durasi"] for p in pot)


def _cek_sumber_cukup(pot, bahan):
    """Tiap potongan: sumber yang dipakai (durasi x speed) muat di rentangnya, dan rentang
    itu berada di dalam rentang layak bahannya."""
    layak = {b["path"]: b.get("rentang") for b in bahan}
    for p in pot:
        if p["potong"] is None:
            continue
        a, z = p["potong"]
        assert p["durasi"] * p["speed"] <= (z - a) + 1e-6, f"{p} melebihi sumbernya"
        assert any(ra - 1e-9 <= a and z <= rb + 1e-9 for ra, rb in layak[p["path"]])


def test_bahan_cukup_tanpa_loop_dan_tanpa_lambat():
    pot, info = susun_potongan(NYATA, 10.0)
    assert _total(pot) == pytest.approx(10.0)
    assert [p["path"] for p in pot] == ["a", "b", "c"]
    assert not any(p["ulang"] for p in pot) and info["lambat"] is None
    _cek_sumber_cukup(pot, NYATA)
    # klip pendek hanya sepanjang miliknya; sisanya dibagi rata ke yang lebih panjang
    assert pot[1]["durasi"] == pytest.approx(1.75)
    assert pot[0]["durasi"] == pytest.approx(pot[2]["durasi"]) == pytest.approx(4.125)


def test_potongan_mentok_di_panjangnya_sendiri():
    pot, _ = susun_potongan(NYATA, 13.0)
    assert [round(p["durasi"], 2) for p in pot] == [5.5, 1.75, 5.75]


def test_kasus_nyata_22_detik_lambat_lalu_pakai_ulang_bukan_berturut():
    pot, info = susun_potongan(NYATA, 22.15)
    assert _total(pot) == pytest.approx(22.15)
    assert info["lambat"] == LAMBAT_MIN and info["bahan_layak"] == pytest.approx(13.65)
    assert info["dipakai_ulang_detik"] > 0
    _cek_sumber_cukup(pot, NYATA)
    for x, y in zip(pot, pot[1:]):
        assert x["path"] != y["path"], "bahan yang sama tidak boleh tampil dua kali berturut-turut"
    # klip 2 (1,75 dtk layak) muncul paling banyak DUA kali, tidak lagi ±4x
    assert sum(1 for p in pot if p["path"] == "b") <= 2


def test_pakai_ulang_mengambil_ujung_rentang():
    pot, _ = susun_potongan(NYATA, 22.15)
    ulang = [p for p in pot if p["ulang"]]
    asli = {p["path"]: p for p in pot if not p["ulang"]}
    for u in ulang:
        rentang = next(b["rentang"][0] for b in NYATA if b["path"] == u["path"])
        assert u["potong"][1] == pytest.approx(rentang[1]), "dipakai ulang dari ujung rentang"
        assert u["potong"] != asli[u["path"]]["potong"] or len(pot) == 1


def test_sedikit_kurang_cukup_diperlambat():
    pot, info = susun_potongan(NYATA, 15.0)
    assert info["lambat"] == pytest.approx(13.65 / 15.0, abs=1e-3) and info["dipakai_ulang_detik"] == 0
    assert _total(pot) == pytest.approx(15.0)
    _cek_sumber_cukup(pot, NYATA)


def test_foto_menyerap_kekurangan():
    bahan = NYATA + [{"path": "f.jpg", "foto": True}]
    pot, info = susun_potongan(bahan, 22.15)
    assert info["lambat"] is None and info["dipakai_ulang_detik"] == 0
    assert _total(pot) == pytest.approx(22.15)
    foto = next(p for p in pot if p["path"] == "f.jpg")
    assert foto["potong"] is None and foto["durasi"] == pytest.approx(22.15 - 13.65)


def test_klip_goyang_menyumbang_dua_rentang():
    bahan = [{"path": "g", "rentang": [(0.0, 1.85), (3.15, 5.0)]}]
    pot, _ = susun_potongan(bahan, 3.5)
    assert [p["potong"][0] for p in pot] == [0.0, 3.15]
    _cek_sumber_cukup(pot, bahan)


def test_speed_ramp_ikut_dihitung():
    """Speed 2x memakai sumber dua kali lebih cepat: bahan 13,65 dtk hanya cukup ~6,8 dtk."""
    pot, info = susun_potongan(NYATA, 10.0, speed=2.0)
    assert info["lambat"] is not None
    _cek_sumber_cukup(pot, NYATA)
    assert all(p["speed"] == pytest.approx(2.0 * info["lambat"]) for p in pot)


def test_satu_klip_satu_rentang_terpaksa_diulang_dan_dilaporkan():
    bahan = [{"path": "x", "rentang": [(0.0, 2.0)]}]
    pot, info = susun_potongan(bahan, 6.0)
    assert _total(pot) == pytest.approx(6.0) and info["dipakai_ulang_detik"] > 0
    _cek_sumber_cukup(pot, bahan)


def test_kosong_aman():
    assert susun_potongan([], 10.0)[0] == []
    assert susun_potongan(NYATA, 0)[0] == []


# ------------------------------------------------------------------ render sungguhan (piksel)

import json  # noqa: E402
import subprocess  # noqa: E402

import auto_render as ar  # noqa: E402


def _klip_warna(path, warna, detik_per=0.5):
    args = ["ffmpeg", "-y", "-v", "error"]
    for w in warna:
        args += ["-f", "lavfi", "-i", f"color=c={w}:size=240x426:rate=24:duration={detik_per}"]
    args += ["-filter_complex", "".join(f"[{i}:v]" for i in range(len(warna))) + f"concat=n={len(warna)}:v=1:a=0",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True, capture_output=True)
    return str(path)


def _rgb(path, t):
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return tuple(out[:3])


def test_render_ai_klip_pendek_tidak_diputar_ulang(tmp_path, monkeypatch):
    """Klip A 1,5 dtk (merah->hijau->biru) + klip B 4 dtk (putih), narasi 5 dtk. Pembagian
    rata lama: A dapat 2,5 dtk -> diputar ulang (merah muncul lagi di ±1,6 dtk). Sekarang: A
    tampil sekali (1,5 dtk) lalu B."""
    monkeypatch.setattr(ar, "TARGET_W", 240)
    monkeypatch.setattr(ar, "TARGET_H", 426)
    monkeypatch.setattr(ar, "THUMBNAIL_ENABLED", False)
    monkeypatch.setenv("TRANSITION", "none")
    a = _klip_warna(tmp_path / "a.mp4", ["red", "lime", "blue"])
    b = _klip_warna(tmp_path / "b.mp4", ["white"] * 8)
    vo = tmp_path / "vo.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "anullsrc=channel_layout=mono:sample_rate=24000", "-t", "5", str(vo)],
                   check=True, capture_output=True)

    async def palsu(teks, out_path):
        import shutil
        shutil.copy2(vo, out_path)

    monkeypatch.setattr(ar, "generate_voice", palsu)
    skrip = tmp_path / "s.json"
    skrip.write_text(json.dumps({"judul": "uji", "audio_mode": "ai", "full_voice_over": "naskah uji",
                                 "media_assets": [a, b], "scenes": []}), encoding="utf-8")
    out = tmp_path / "o.mp4"
    ar.render_from_agent_script(str(skrip), str(out))

    merah = lambda c: c[0] > 180 and c[1] < 90 and c[2] < 90
    putih = lambda c: min(c) > 200
    assert merah(_rgb(out, 0.2)), "kontrol: klip A tampil di awal"
    for t in (1.8, 2.3, 3.5, 4.6):
        c = _rgb(out, t)
        assert putih(c), f"detik {t}: {c} -- klip A terulang (bukan B)"
    assert ar.PENGISIAN["dipakai_ulang_detik"] == 0 and ar.PENGISIAN["lambat"] is None
