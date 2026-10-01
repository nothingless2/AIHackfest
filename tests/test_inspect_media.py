"""Pemeriksaan bahan sebelum diproses + gerbang di jalur `run`.

Ada dua hal yang harus terbukti:
1. PERTANYAAN ditentukan kode dari fakta terukur (bukan dari selera LLM): permintaan
   yang lengkap tidak ditanyai apa-apa, permintaan singkat atas bahan tanpa ucapan
   ditanyai konteksnya.
2. GERBANG di `run` gagal-tertutup: tanpa pemeriksaan, milik chat lain, bahan
   berbeda, kedaluwarsa, atau belum ditanyakan -> ditolak dengan langkah berikutnya.
"""

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone

import pytest

import inspect_media as im


@pytest.fixture(autouse=True)
def _isolasi(tmp_path, monkeypatch):
    """Status pemeriksaan tidak boleh menyentuh workspace/state asli (aturan #6)."""
    monkeypatch.setattr(im, "INSPECT_DIR", str(tmp_path / "inspect"))
    monkeypatch.setattr(im, "REQUIRE_INSPECT", True)
    monkeypatch.setattr(im, "LOCAL_PYTHON", "/tidak/ada/python")   # VAD tak terukur kecuali diatur


def _video(path, detik=3.0, w=320, h=568, audio=True, volume=0.3):
    args = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
            f"color=c=gray:size={w}x{h}:rate=10:duration={detik}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"anoisesrc=d={detik}:c=pink:a={volume}", "-c:a", "aac", "-shortest"]
    subprocess.run(args + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   check=True, capture_output=True)
    return str(path)


def _ringk(**kw):
    dasar = {"n": 3, "n_video": 3, "n_gambar": 0, "n_berucap": 0, "n_tanpa_ucapan": 3, "n_ragu": 0,
             "n_tanpa_suara": 0, "n_tidak_terukur": 0, "n_suasana": 3, "n_horizontal": 0,
             "total_detik": 15.5}
    dasar.update(kw)
    return dasar


def _tahu(konteks=""):
    return im.dari_konteks(konteks)


def _kode(q):
    return [p["kode"] for p in q]


# ---------- pertanyaan ditentukan fakta ----------

def test_bahan_tanpa_ucapan_ditanyai_konteksnya():
    """Insiden 19 Sep: B-roll tanpa ucapan diedit 'sebagus mungkin' -> judul hanya tebakan."""
    q = im.susun_pertanyaan(_ringk(), _tahu("Coba edit video ini sebagus mungkin untuk tiktok"))
    assert "topik" in _kode(q)
    assert "tidak tahu ceritanya" in q[0]["tanya"]


def test_permintaan_lengkap_atas_talking_head_tidak_ditanyai_apa_apa():
    ringk = _ringk(n_berucap=3, n_tanpa_ucapan=0, n_suasana=0)
    konteks = ("Edit jadi konten TikTok 30 detik tentang sistem listing properti untuk developer, "
               "tanpa suara AI, tambahkan musik lo-fi, ajakan di akhir: hubungi Steven")
    assert im.susun_pertanyaan(ringk, _tahu(konteks)) == []


def test_talking_head_dengan_permintaan_singkat_ditanya_tujuan_dan_platform():
    q = im.susun_pertanyaan(_ringk(n_berucap=3, n_tanpa_ucapan=0, n_suasana=0), _tahu("edit ya"))
    # "gaya" = tawaran proaktif yang menumpang pada pertanyaan yang memang perlu
    assert _kode(q) == ["tujuan", "platform_durasi", "gaya"]


def test_platform_yang_sudah_disebut_tidak_ditanyakan_lagi():
    q = im.susun_pertanyaan(_ringk(n_berucap=3, n_tanpa_ucapan=0, n_suasana=0),
                            _tahu("edit untuk reels ya"))
    assert "platform_durasi" not in _kode(q)


def test_durasi_yang_sudah_disebut_tidak_ditanyakan_lagi():
    q = im.susun_pertanyaan(_ringk(n_berucap=3, n_tanpa_ucapan=0, n_suasana=0), _tahu("edit 20 detik"))
    assert "platform_durasi" not in _kode(q)


def test_maksimal_lima_pertanyaan():
    ringk = _ringk(n_video=5, n_berucap=5, n_tanpa_ucapan=0, n_suasana=0, n_horizontal=5)
    q = im.susun_pertanyaan(ringk, _tahu("edit"))
    assert 1 <= len(q) <= im.MAKS_PERTANYAAN


def test_setiap_pertanyaan_punya_default_dan_alasan():
    for p in im.susun_pertanyaan(_ringk(n_horizontal=3), _tahu("edit")):
        assert p["default"] and p["alasan"] and p["tanya"]


# ---------- pilihan jawaban dibuat KODE (keluhan user: jangan suruh user menebak) ----------

SEMUA_SKENARIO = [
    (_ringk(n_horizontal=3), "edit untuk tiktok"),                                   # B-roll
    (_ringk(n_berucap=5, n_tanpa_ucapan=0, n_suasana=0, n_video=5), "edit"),         # talking-head
    (_ringk(n_tanpa_ucapan=0, n_tanpa_suara=3, n_suasana=0), "edit untuk reels"),    # bisu
    (_ringk(n_video=0, n_gambar=4, n_tanpa_ucapan=0, n_suasana=0), "edit"),          # foto
]


def _semua_pertanyaan():
    for ringk, konteks in SEMUA_SKENARIO:
        yield from im.susun_pertanyaan(ringk, _tahu(konteks))


def test_setiap_pertanyaan_punya_pilihan_bukan_isian_kosong():
    for p in _semua_pertanyaan():
        assert len(p["opsi"]) >= 2, f"{p['kode']}: user dipaksa menebak jawaban"
        assert [o["huruf"] for o in p["opsi"]] == [chr(65 + i) for i in range(len(p["opsi"]))]
        assert sum(o["rekomendasi"] for o in p["opsi"]) == 1, f"{p['kode']}: harus tepat satu bintang"


def test_pemetaan_jawaban_hanya_merujuk_huruf_yang_ada():
    for p in _semua_pertanyaan():
        huruf = {o["huruf"] for o in p["opsi"]}
        assert set(p.get("param", {})) <= huruf, f"{p['kode']}: pemetaan ke pilihan yang tidak ada"


def test_pesan_memuat_semua_pilihan_dan_cara_menjawab():
    q = im.susun_pertanyaan(_ringk(n_horizontal=3), _tahu("edit untuk tiktok"))
    pesan = im.susun_pesan_pertanyaan(q)
    for i, p in enumerate(q, 1):
        assert f"{i}) " in pesan
        for o in p["opsi"]:
            assert f"{o['huruf']}. " in pesan and o["label"] in pesan
    assert "terserah" in pesan and "★" in pesan and "contoh: 1A" in pesan


def test_pesan_aman_untuk_telegram_tanpa_markdown():
    """Telegram menolak/merusak pesan bila markdown-nya tidak seimbang; format sengaja teks biasa."""
    for ringk, konteks in SEMUA_SKENARIO:
        q = im.susun_pertanyaan(ringk, _tahu(konteks))
        if q:
            pesan = im.susun_pesan_pertanyaan(q)
            assert not any(c in pesan for c in "*_`[]<>"), f"karakter markdown di pesan: {pesan!r}"


def test_pilihan_bawaan_bintang_sama_dengan_default_yang_dijanjikan():
    """'terserah' berarti pilihan bintang; harus sama dengan default yang dicatat kode."""
    fit = [p for p in im.susun_pertanyaan(_ringk(n_horizontal=3), _tahu("tiktok")) if p["kode"] == "fit"][0]
    bintang = [o for o in fit["opsi"] if o["rekomendasi"]][0]
    assert bintang["huruf"] == "A" and "tengah" in fit["default"] and "tengah" in bintang["label"].lower()


def test_teks_agent_melarang_ask_user_dan_memuat_pesan_siap_kirim(tmp_path):
    """Insiden 19 Sep 22:34-23:05: ask_user menahan giliran, kedaluwarsa 15 menit, dan tiga
    pertanyaan dalam satu ask_user tidak bisa dijawab dengan satu balasan bebas."""
    paths, r = _inspeksi(tmp_path)
    teks = r["teks"]
    assert "JANGAN memakai tool tanya-jawab" in teks and "15 menit" in teks
    assert "<<<PESAN" in teks and "PESAN>>>" in teks and r["pesan"] in teks
    assert "1A" in teks or "1*" in teks, "pemetaan jawaban -> parameter harus ada"


def test_pemetaan_untuk_agent_memuat_parameter_run(tmp_path):
    q = im.susun_pertanyaan(_ringk(n_horizontal=3), _tahu("edit untuk tiktok"))
    peta = "\n".join(im.susun_pemetaan(q))
    assert 'fitMode="blur"' in peta and "staticText=true" in peta
    assert 'audioMode="original"' in peta and 'music="on"' in peta


def test_teks_statis_sudah_disebut_tidak_ditanyakan_lagi():
    q = im.susun_pertanyaan(_ringk(), _tahu("edit tiktok, teksnya statis saja sepanjang video"))
    assert "teks_layar" not in _kode(q)


def test_bahan_tanpa_ucapan_ditanya_gaya_teks_layar():
    q = im.susun_pertanyaan(_ringk(), _tahu("edit untuk tiktok"))
    assert "teks_layar" in _kode(q)


def test_bahan_berucap_tidak_ditanya_teks_layar():
    """Bahan berucap sudah punya subtitle; pertanyaan ini tidak relevan."""
    q = im.susun_pertanyaan(_ringk(n_berucap=3, n_tanpa_ucapan=0, n_suasana=0), _tahu("edit"))
    assert "teks_layar" not in _kode(q)


def test_inspeksi_mengembalikan_pesan_siap_kirim(tmp_path):
    paths, r = _inspeksi(tmp_path)
    assert "Sebelum saya edit" in r["pesan"]
    assert r["pesan"].startswith("Bahan yang saya terima:")


def test_suasana_tanpa_ucapan_ditanya_audio():
    q = im.susun_pertanyaan(_ringk(), _tahu("edit untuk tiktok"))
    assert "audio" in _kode(q)


def test_audio_yang_sudah_disebut_tidak_ditanyakan():
    q = im.susun_pertanyaan(_ringk(), _tahu("edit untuk tiktok, tanpa suara ai"))
    assert "audio" not in _kode(q)


def test_bahan_tanpa_suara_ditanya_musik_atau_voiceover():
    q = im.susun_pertanyaan(_ringk(n_tanpa_ucapan=0, n_tanpa_suara=3, n_suasana=0),
                            _tahu("edit untuk reels"))
    assert "audio" in _kode(q)


def test_hanya_foto_ditanya_konteks_dan_audio():
    ringk = _ringk(n_video=0, n_gambar=4, n_tanpa_ucapan=0, n_suasana=0)
    assert {"topik", "audio"} <= set(_kode(im.susun_pertanyaan(ringk, _tahu("edit"))))


def test_banyak_video_berucap_ditanya_boleh_dibuang_atau_utuh():
    ringk = _ringk(n_video=5, n_berucap=5, n_tanpa_ucapan=0, n_suasana=0)
    q = im.susun_pertanyaan(ringk, _tahu("edit untuk tiktok 30 detik tentang properti untuk developer"))
    assert "potong" in _kode(q)


def test_sudah_menyebut_buang_atau_utuh_tidak_ditanyakan():
    ringk = _ringk(n_video=5, n_berucap=5, n_tanpa_ucapan=0, n_suasana=0)
    q = im.susun_pertanyaan(ringk, _tahu("edit untuk tiktok 30 detik tentang properti, buang take ulang"))
    assert "potong" not in _kode(q)


def test_sumber_horizontal_ke_vertikal_ditanya_cara_memuat():
    q = im.susun_pertanyaan(_ringk(n_horizontal=3), _tahu("edit untuk tiktok"))
    fit = [p for p in q if p["kode"] == "fit"]
    assert fit and "3 dari 3" in fit[0]["tanya"]


def test_target_landscape_tidak_ditanya_cara_memuat():
    q = im.susun_pertanyaan(_ringk(n_horizontal=3), _tahu("edit untuk youtube 16:9"))
    assert "fit" not in _kode(q)


def test_fit_yang_sudah_disebut_tidak_ditanyakan():
    q = im.susun_pertanyaan(_ringk(n_horizontal=3), _tahu("edit untuk tiktok, pakai latar blur"))
    assert "fit" not in _kode(q)


# ---------- deteksi apa yang sudah disebut ----------

@pytest.mark.parametrize("konteks,kunci,nilai", [
    ("untuk TikTok", "platform", True), ("buat reels", "platform", True),
    ("rasio 1:1", "rasio", True), ("9 : 16", "rasio", True),
    ("bikin 15 detik", "durasi", 15),
    ("tanpa suara ai", "audio", True), ("pakai voice over", "audio", True),
    ("tambahkan musik", "musik", True), ("kasih backsound lofi", "musik", True),
    ("edit saja", "platform", False), ("edit saja", "durasi", None),
])
def test_deteksi_yang_sudah_disebut(konteks, kunci, nilai):
    assert im.dari_konteks(konteks)[kunci] == nilai


# ---------- klasifikasi ucapan (ambang dari klip nyata) ----------

@pytest.mark.parametrize("ucapan,harap", [(8.5, "berucap"), (5.2, "berucap"),   # talking-head 93-95%
                                          (0.0, "tanpa_ucapan"),                # B-roll 0%
                                          (1.2, "ragu")])                       # klip 35% yg dihalusinasi
def test_klasifikasi_memakai_angka_terukur(ucapan, harap):
    f = {"jenis": "video", "punya_audio": True, "ucapan_detik": ucapan, "durasi": 3.6 if ucapan == 1.2 else 8.9}
    assert im.klasifikasi(f) == harap


def test_gambar_dan_tanpa_suara_dan_tak_terukur():
    assert im.klasifikasi({"jenis": "gambar", "punya_audio": False, "ucapan_detik": None, "durasi": None}) == "gambar"
    assert im.klasifikasi({"jenis": "video", "punya_audio": False, "ucapan_detik": None, "durasi": 3}) == "tanpa_suara"
    assert im.klasifikasi({"jenis": "video", "punya_audio": True, "ucapan_detik": None, "durasi": 3}) == "tidak_terukur"


# ---------- fakta dari berkas nyata ----------

def test_probe_membaca_durasi_orientasi_dan_audio(tmp_path):
    v = _video(tmp_path / "a.mp4", detik=2.0, w=640, h=360)
    f = im.probe_clip(v)
    assert f["jenis"] == "video" and f["orientasi"] == "horizontal"
    assert f["durasi"] == pytest.approx(2.0, abs=0.2) and f["punya_audio"] is True
    assert f["volume_db"] is not None and f["volume_db"] > -45


def test_probe_video_tanpa_audio(tmp_path):
    f = im.probe_clip(_video(tmp_path / "b.mp4", audio=False))
    assert f["punya_audio"] is False and f["volume_db"] is None


def test_probe_berkas_rusak_dilaporkan_bukan_meledak(tmp_path):
    rusak = tmp_path / "x.mp4"
    rusak.write_bytes(b"bukan video")
    assert im.probe_clip(str(rusak))["error"]


def test_vad_tidak_tersedia_berarti_tidak_terukur_bukan_nol(tmp_path):
    """Tidak terukur BUKAN 'tanpa ucapan': menyamakannya akan menuduh video
    berucap sebagai B-roll (aturan #7 CLAUDE.md)."""
    v = _video(tmp_path / "a.mp4")
    fakta = im.kumpulkan_fakta([v])
    assert fakta[0]["ucapan_detik"] is None and fakta[0]["kelas"] == "tidak_terukur"


def test_vad_lewat_worker_palsu_terbaca(tmp_path, monkeypatch):
    v = _video(tmp_path / "a.mp4", detik=4.0)
    worker = tmp_path / "vad_palsu.py"
    worker.write_text(textwrap.dedent('''
        import json, os, sys
        for p in [a for a in sys.argv[1:] if os.path.isfile(a)]:
            print(json.dumps({"tipe": "vad", "path": p, "total": 4.0, "ucapan": 3.8}))
    '''))
    monkeypatch.setattr(im, "LOCAL_PYTHON", sys.executable)
    monkeypatch.setattr(im, "LOCAL_WORKER", str(worker))
    fakta = im.kumpulkan_fakta([v])
    assert fakta[0]["ucapan_detik"] == 3.8 and fakta[0]["kelas"] == "berucap"


# ---------- inspeksi + gerbang ----------

def _inspeksi(tmp_path, konteks="edit", chat="111", n=2):
    paths = [_video(tmp_path / f"v{i}.mp4", w=640, h=360) for i in range(n)]
    return paths, im.inspeksi(paths, konteks, chat)


def test_inspeksi_menyimpan_status_dan_mengembalikan_id(tmp_path):
    paths, r = _inspeksi(tmp_path)
    assert r["ok"] and len(r["inspect_id"]) == 12
    st = json.load(open(os.path.join(im.INSPECT_DIR, f"{r['inspect_id']}.json")))
    assert st["chat_id"] == "111" and st["pertanyaan"] == [p["kode"] for p in r["pertanyaan"]]


def test_teks_memuat_id_path_persis_dan_perintah_akhiri_giliran(tmp_path):
    paths, r = _inspeksi(tmp_path)
    assert r["inspect_id"] in r["teks"]
    for p in paths:
        assert f"--media-path {json.dumps(p, ensure_ascii=False)}" in r["teks"]
    assert "AKHIRI" in r["teks"] and "JANGAN menjalankan hermes_render.py" in r["teks"]


def _vad_berucap(tmp_path, monkeypatch, detik=3.0):
    """VAD palsu yang melaporkan hampir seluruh klip berisi ucapan (talking-head)."""
    worker = tmp_path / "vad_berucap.py"
    worker.write_text(textwrap.dedent(f'''
        import json, os, sys
        for p in [a for a in sys.argv[1:] if os.path.isfile(a)]:
            print(json.dumps({{"tipe": "vad", "path": p, "total": {detik}, "ucapan": {detik * 0.95}}}))
    '''))
    monkeypatch.setattr(im, "LOCAL_PYTHON", sys.executable)
    monkeypatch.setattr(im, "LOCAL_WORKER", str(worker))


def test_tanpa_pertanyaan_teks_menyuruh_langsung_lanjut(tmp_path, monkeypatch):
    _vad_berucap(tmp_path, monkeypatch)
    paths = [_video(tmp_path / "v.mp4")]
    r = im.inspeksi(paths, "edit untuk tiktok 20 detik tentang sistem listing properti developer, "
                           "tanpa suara ai, pakai musik", "111")
    assert r["pertanyaan"] == []
    assert "Tidak ada yang perlu ditanyakan" in r["teks"]
    assert r["inspect_id"] in r["teks"]


def test_tanpa_pertanyaan_gerbang_lolos_tanpa_user_answered(tmp_path, monkeypatch):
    """Tidak ada yang ditanyakan -> tidak ada yang perlu dijawab."""
    _vad_berucap(tmp_path, monkeypatch)
    paths = [_video(tmp_path / "v.mp4")]
    r = im.inspeksi(paths, "edit untuk tiktok 20 detik tentang sistem listing properti developer, "
                           "tanpa suara ai, pakai musik", "111")
    assert im.cek_izin(r["inspect_id"], "111", paths, user_answered=False)["ok"] is True


def test_inspeksi_tanpa_chat_ditolak(tmp_path):
    paths = [_video(tmp_path / "v.mp4")]
    r = im.inspeksi(paths, "edit", "")
    assert r["ok"] is False and r["kode"] == "chat_tidak_diketahui"
    assert not os.path.exists(im.INSPECT_DIR) or not os.listdir(im.INSPECT_DIR)


def test_gerbang_lolos_setelah_ditanyakan(tmp_path):
    paths, r = _inspeksi(tmp_path)
    assert im.cek_izin(r["inspect_id"], "111", paths, user_answered=True)["ok"] is True


def test_gerbang_menolak_tanpa_inspect():
    r = im.cek_izin("", "111", ["/x/a.mp4"])
    assert r["ok"] is False and r["kode"] == "wajib_inspect"
    assert "inspect_media.py inspect" in r["teks"], "penolakan harus menyebut langkah berikutnya"


def test_gerbang_menolak_kalau_belum_ditanyakan(tmp_path):
    paths, r = _inspeksi(tmp_path)
    assert r["pertanyaan"], "prasyarat: skenario ini punya pertanyaan"
    g = im.cek_izin(r["inspect_id"], "111", paths, user_answered=False)
    assert g["ok"] is False and g["kode"] == "belum_ditanyakan"


def test_gerbang_menolak_milik_chat_lain(tmp_path):
    """Dua user memakai sistem yang sama: pemeriksaan A tidak boleh dipakai B."""
    paths, r = _inspeksi(tmp_path, chat="111")
    g = im.cek_izin(r["inspect_id"], "222", paths, user_answered=True)
    assert g["ok"] is False and g["kode"] == "milik_chat_lain"


def test_gerbang_menolak_bahan_berbeda(tmp_path):
    paths, r = _inspeksi(tmp_path)
    lain = _video(tmp_path / "lain.mp4", detik=5.0)
    g = im.cek_izin(r["inspect_id"], "111", [lain], user_answered=True)
    assert g["ok"] is False and g["kode"] == "bahan_berbeda"


def test_gerbang_menolak_kalau_ada_bahan_ditambah(tmp_path):
    paths, r = _inspeksi(tmp_path)
    tambahan = _video(tmp_path / "tambah.mp4")
    g = im.cek_izin(r["inspect_id"], "111", paths + [tambahan], user_answered=True)
    assert g["kode"] == "bahan_berbeda"


def test_urutan_path_tidak_mempengaruhi_kecocokan(tmp_path):
    paths, r = _inspeksi(tmp_path, n=3)
    assert im.cek_izin(r["inspect_id"], "111", list(reversed(paths)), True)["ok"] is True


def test_gerbang_menolak_kedaluwarsa(tmp_path):
    paths, r = _inspeksi(tmp_path)
    p = os.path.join(im.INSPECT_DIR, f"{r['inspect_id']}.json")
    st = json.load(open(p))
    st["dibuat"] = (datetime.now(timezone.utc) - timedelta(hours=im.INSPECT_TTL_HOURS + 1)).isoformat()
    json.dump(st, open(p, "w"))
    g = im.cek_izin(r["inspect_id"], "111", paths, user_answered=True)
    assert g["ok"] is False and g["kode"] == "kedaluwarsa"


@pytest.mark.parametrize("id_jahat", ["../../etc/passwd", "abc", "A" * 12, "zzzzzzzzzzzz",
                                      "0123456789ab/../x", "0123456789abcdef"])
def test_inspect_id_tidak_boleh_menyusun_path_sembarangan(id_jahat):
    """Divalidasi regex ketat SEBELUM dipakai menyusun path."""
    g = im.cek_izin(id_jahat, "111", ["/x/a.mp4"], True)
    assert g["ok"] is False and g["kode"] == "tidak_ditemukan"
    assert im._path_state(id_jahat) is None


def test_id_yang_tidak_pernah_dibuat_ditolak():
    assert im.cek_izin("0123456789ab", "111", ["/x/a.mp4"], True)["kode"] == "tidak_ditemukan"


def test_gerbang_bisa_dimatikan(monkeypatch):
    monkeypatch.setattr(im, "REQUIRE_INSPECT", False)
    assert im.cek_izin("", "111", ["/x/a.mp4"])["ok"] is True


def test_status_rusak_ditolak_bukan_meledak(tmp_path):
    paths, r = _inspeksi(tmp_path)
    open(os.path.join(im.INSPECT_DIR, f"{r['inspect_id']}.json"), "w").write("{rusak")
    assert im.cek_izin(r["inspect_id"], "111", paths, True)["kode"] == "tidak_ditemukan"


def test_kegagalan_pemeriksaan_tidak_menahan_user(tmp_path, monkeypatch):
    """Pemeriksaan boleh gagal; user tetap dapat dua pertanyaan generik dan bisa lanjut."""
    monkeypatch.setattr(im, "kumpulkan_fakta", lambda p: (_ for _ in ()).throw(RuntimeError("ffprobe mati")))
    paths = [_video(tmp_path / "v.mp4")]
    r = im.inspeksi(paths, "edit", "111")
    assert r["ok"] and r["degraded"] and len(r["pertanyaan"]) == 2
    assert im.cek_izin(r["inspect_id"], "111", paths, True)["ok"] is True


# ---------- CLI (dijalankan agent Hermes) + gerbang di hermes_render ----------

def _cli(perintah, payload, env_tambahan):
    env = {**os.environ, **env_tambahan}
    o = subprocess.run([sys.executable, os.path.join(os.path.dirname(im.__file__), "inspect_media.py"), perintah],
                       input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=60)
    return o.returncode, json.loads(o.stdout)


def test_cli_inspect_lalu_gerbang_render(tmp_path, monkeypatch):
    """CLI sungguhan (subprocess) menulis status; gerbang cek_izin -- yang dipanggil
    hermes_render -- membacanya. Pengganti tes integrasi plugin OpenClaw yang dihapus."""
    v = _video(tmp_path / "v.mp4", w=640, h=360)
    env = {"INSPECT_DIR": str(tmp_path / "st"), "WHISPER_PYTHON": "/tidak/ada"}
    kode, r = _cli("inspect", {"paths": [v], "konteks": "edit", "chat_id": "555"}, env)
    assert kode == 0 and r["ok"] and r["inspect_id"]
    monkeypatch.setattr(im, "INSPECT_DIR", str(tmp_path / "st"))
    assert im.cek_izin(r["inspect_id"], "555", [v], True)["ok"] is True
    assert im.cek_izin(r["inspect_id"], "999", [v], True)["kode"] == "milik_chat_lain"


def test_cli_masukan_rusak_dan_perintah_salah():
    o = subprocess.run([sys.executable, os.path.join(os.path.dirname(im.__file__), "inspect_media.py"), "inspect"],
                       input="bukan json", capture_output=True, text=True, timeout=30)
    assert o.returncode == 2 and json.loads(o.stdout)["kode"] == "masukan_rusak"
    o = subprocess.run([sys.executable, os.path.join(os.path.dirname(im.__file__), "inspect_media.py"), "apa"],
                       input="{}", capture_output=True, text=True, timeout=30)
    assert o.returncode == 2 and json.loads(o.stdout)["kode"] == "penggunaan"


# ---------- write_json atomik (butir #8 rencana Fase 2) ----------

def test_write_json_tidak_meninggalkan_berkas_sementara(tmp_path):
    import common
    p = str(tmp_path / "x" / "data.json")
    common.write_json(p, {"a": 1})
    assert json.load(open(p)) == {"a": 1}
    assert os.listdir(tmp_path / "x") == ["data.json"]


def test_write_json_gagal_tidak_merusak_isi_lama(tmp_path):
    """Versi lama memotong berkas jadi kosong SEBELUM menulis; kalau serialisasi
    gagal di tengah, isi lama hilang."""
    import common
    p = str(tmp_path / "data.json")
    common.write_json(p, {"lama": True})
    with pytest.raises(TypeError):
        common.write_json(p, {"tidak_bisa": object()})
    assert json.load(open(p)) == {"lama": True}, "isi lama harus utuh"
    assert [n for n in os.listdir(tmp_path) if n.endswith(".tmp")] == []


def test_jalur_darurat_tetap_berpilihan_dan_menghasilkan_pesan(tmp_path, monkeypatch):
    """Justru saat pemeriksaan otomatis gagal, user tidak boleh dibiarkan menebak."""
    monkeypatch.setattr(im, "kumpulkan_fakta", lambda p: (_ for _ in ()).throw(RuntimeError("ffprobe mati")))
    paths = [_video(tmp_path / "v.mp4")]
    r = im.inspeksi(paths, "edit", "111")
    assert r["degraded"]
    for p in r["pertanyaan"]:
        assert len(p["opsi"]) >= 2 and sum(o["rekomendasi"] for o in p["opsi"]) == 1
    assert "A. " in r["pesan"] and "★" in r["pesan"]
    assert "JANGAN memakai tool tanya-jawab" in r["teks"] and r["inspect_id"] in r["teks"]


# ---------- jawaban user harus SAMPAI ke pipeline (celah 19 Sep 23:36) ----------

def _inspeksi_topik(tmp_path, konteks="edit ya"):
    paths = [_video(tmp_path / f"v{i}.mp4", w=640, h=360) for i in range(2)]
    r = im.inspeksi(paths, konteks, "111")
    assert "topik" in [p["kode"] for p in r["pertanyaan"]], "prasyarat: skenario menanyakan topik"
    return paths, r


def test_konteks_tanpa_jawaban_ditolak_meski_userAnswered_true(tmp_path):
    """Persis celah nyata: agent menandai sudah ditanyakan, tapi jawabannya tidak dimasukkan."""
    paths, r = _inspeksi_topik(tmp_path)
    g = im.cek_izin(r["inspect_id"], "111", paths, user_answered=True, konteks="edit ya")
    assert g["ok"] is False and g["kode"] == "jawaban_tidak_di_konteks"
    assert "--user-context" in g["teks"]


def test_konteks_dengan_jawaban_lolos(tmp_path):
    paths, r = _inspeksi_topik(tmp_path)
    g = im.cek_izin(r["inspect_id"], "111", paths, True,
                    konteks="edit ya. Ini acara Aksi Merah Laksamana Muda, donor darah")
    assert g["ok"] is True


@pytest.mark.parametrize("pasrah", ["edit ya. terserah", "edit ya, langsung saja", "edit ya tanpa konteks"])
def test_pasrah_eksplisit_dianggap_jawaban(tmp_path, pasrah):
    paths, r = _inspeksi_topik(tmp_path)
    assert im.cek_izin(r["inspect_id"], "111", paths, True, konteks=pasrah)["ok"] is True


def test_konteks_lebih_pendek_dari_awal_ditolak(tmp_path):
    paths, r = _inspeksi_topik(tmp_path, konteks="edit video ini sebagus mungkin untuk tiktok")
    g = im.cek_izin(r["inspect_id"], "111", paths, True, konteks="edit")
    assert g["kode"] == "jawaban_tidak_di_konteks"


def test_pemanggil_lama_tanpa_konteks_tidak_diperiksa(tmp_path):
    """konteks=None = pemanggil yang tidak mengirimnya; perilaku lama utuh."""
    paths, r = _inspeksi_topik(tmp_path)
    assert im.cek_izin(r["inspect_id"], "111", paths, True)["ok"] is True


def test_tanpa_pertanyaan_topik_konteks_tidak_dipersoalkan(tmp_path, monkeypatch):
    """Cek ini hanya berlaku bila pertanyaan topik/tujuan memang diajukan."""
    monkeypatch.setattr(im, "susun_pertanyaan",
                        lambda ringk, tahu: [p for p in _SEMUA_ASLI(ringk, tahu) if p["kode"] == "fit"])
    paths = [_video(tmp_path / "v.mp4", w=640, h=360)]
    r = im.inspeksi(paths, "edit", "111")
    assert "topik" not in [p["kode"] for p in r["pertanyaan"]]
    assert im.cek_izin(r["inspect_id"], "111", paths, True, konteks="edit")["ok"] is True


_SEMUA_ASLI = im.susun_pertanyaan


def test_konteks_user_wajib_memuat_jawaban(tmp_path, monkeypatch):
    v = _video(tmp_path / "v.mp4", w=640, h=360)
    env = {"INSPECT_DIR": str(tmp_path / "st"), "WHISPER_PYTHON": "/tidak/ada"}
    _, r = _cli("inspect", {"paths": [v], "konteks": "edit ya", "chat_id": "555"}, env)
    monkeypatch.setattr(im, "INSPECT_DIR", str(tmp_path / "st"))
    g = im.cek_izin(r["inspect_id"], "555", [v], True, "edit ya")
    assert g["kode"] == "jawaban_tidak_di_konteks"
    g2 = im.cek_izin(r["inspect_id"], "555", [v], True, "edit ya. Ini acara donor darah Aksi Merah")
    assert g2["ok"] is True


# ---------- musik dari user ----------

def _lagu(path, detik=30):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=330:duration={detik}",
                    "-c:a", "libmp3lame", str(path)], check=True, capture_output=True)
    return str(path)


def test_berkas_audio_dikenali_sebagai_musik_bukan_video(tmp_path):
    f = im.probe_clip(_lagu(tmp_path / "lagu.mp3"))
    assert f["jenis"] == "musik" and f["durasi"] == pytest.approx(30, abs=0.5) and f["punya_audio"]
    assert im.klasifikasi(f) == "musik"


def test_musik_tidak_dihitung_sebagai_video_dan_durasinya_bukan_durasi_bahan(tmp_path):
    v = _video(tmp_path / "v.mp4", detik=4, w=640, h=360)
    fakta = im.kumpulkan_fakta([v, _lagu(tmp_path / "lagu.mp3")])
    r = im.ringkas(fakta)
    assert r["n_video"] == 1 and r["n_musik"] == 1
    assert r["total_detik"] == pytest.approx(4.0, abs=0.5), "durasi musik tidak boleh ikut dijumlahkan"


def test_musik_ditanya_nasib_suara_asli_dengan_pilihan_mute(tmp_path):
    ringk = _ringk(n_musik=1, n_horizontal=0)
    q = im.susun_pertanyaan(ringk, _tahu("edit untuk tiktok"))
    audio = [p for p in q if p["kode"] == "audio"][0]
    assert audio["param"]["A"] == {"audioMode": "mute"}
    assert audio["param"]["B"] == {"audioMode": "original"}
    assert len(audio["opsi"]) == 2


def test_default_musik_bisukan_untuk_bahan_tanpa_ucapan_tapi_pertahankan_untuk_berucap():
    """Membisukan video berucapan menghilangkan apa yang dikatakan -- default-nya berbeda."""
    tanpa = [p for p in im.susun_pertanyaan(_ringk(n_musik=1), _tahu("edit tiktok")) if p["kode"] == "audio"][0]
    assert [o["huruf"] for o in tanpa["opsi"] if o["rekomendasi"]] == ["A"]
    berucap = [p for p in im.susun_pertanyaan(
        _ringk(n_musik=1, n_berucap=3, n_tanpa_ucapan=0, n_suasana=0), _tahu("edit tiktok")) if p["kode"] == "audio"][0]
    assert [o["huruf"] for o in berucap["opsi"] if o["rekomendasi"]] == ["B"]


@pytest.mark.parametrize("konteks", ["bisukan videonya", "tolong mute suara aslinya", "hilangkan suara asli",
                                     "matikan suara"])
def test_permintaan_mute_dianggap_sudah_menjawab_audio(konteks):
    assert im.dari_konteks(konteks)["audio"] is True


def test_menyebut_musik_saja_tidak_menjawab_nasib_suara_asli():
    """'tambahkan musik' tidak berarti 'bisukan suara asli'; pertanyaannya tetap diajukan."""
    q = im.susun_pertanyaan(_ringk(n_musik=1), _tahu("edit tiktok, tambahkan musik ini"))
    assert "audio" in _kode(q)


def test_pemetaan_musik_masuk_teks_agent():
    q = im.susun_pertanyaan(_ringk(n_musik=1), _tahu("edit tiktok"))
    assert 'audioMode="mute"' in "\n".join(im.susun_pemetaan(q))


# ---------------------------------------------------------------- gaya teks & tawaran proaktif

def test_bahan_tanpa_ucapan_ditanya_posisi_dan_font_teks():
    q = im.susun_pertanyaan(_ringk(), _tahu("edit ya"))
    g = next(x for x in q if x["kode"] == "gaya_teks")
    assert g["param"]["B"] == {"textPosition": "tengah", "textFont": "tegas"}
    assert "font" in g["catatan"] and "tengah" in g["catatan"]


def test_posisi_dan_font_yang_sudah_disebut_tidak_ditanyakan_lagi():
    konteks = "buat tulisan di tengah konten dengan font yang estetik"
    q = im.susun_pertanyaan(_ringk(), _tahu(konteks))
    assert "gaya_teks" not in _kode(q)


def test_hanya_posisi_disebut_font_tetap_ditanyakan():
    q = im.susun_pertanyaan(_ringk(), _tahu("teks di tengah ya"))
    assert "gaya_teks" in _kode(q)


def test_tawaran_gaya_menumpang_bukan_memaksa():
    """Permintaan lengkap tetap tidak ditanyai apa pun; tawaran gaya hanya muncul
    bila sudah ada pertanyaan lain yang memang perlu."""
    ringk = _ringk(n_berucap=3, n_tanpa_ucapan=0, n_suasana=0)
    lengkap = ("Edit jadi konten TikTok 30 detik tentang sistem listing properti untuk developer, "
               "tanpa suara AI, tambahkan musik lo-fi, ajakan di akhir: hubungi Steven")
    assert im.susun_pertanyaan(ringk, _tahu(lengkap)) == []
    q = im.susun_pertanyaan(ringk, _tahu("edit ya"))
    g = next(x for x in q if x["kode"] == "gaya")
    # 1 Okt: tawaran berupa preset gaya (paket tampilan + editing), bawaan "klasik" di A.
    assert g["param"]["A"] == {"gaya": "klasik"} and g["opsi"][0]["rekomendasi"]


def test_gaya_yang_sudah_disebut_tidak_ditawarkan():
    q = im.susun_pertanyaan(_ringk(n_berucap=3, n_tanpa_ucapan=0, n_suasana=0),
                            _tahu("edit ya, pakai subtitle gaya capcut"))
    assert "gaya" not in _kode(q)


def test_bahan_berucap_maupun_tidak_ditawari_preset_yang_sama():
    """Sebelum 1 Okt bahan tanpa ucapan ditawari filter warna saja; sekarang keduanya ditawari
    preset (subtitle di preset memang tidak berpengaruh bila tidak ada ucapan)."""
    import gaya
    for ringk in (_ringk(), _ringk(n_berucap=3, n_tanpa_ucapan=0, n_suasana=0)):
        g = next(x for x in im.susun_pertanyaan(ringk, _tahu("edit ya")) if x["kode"] == "gaya")
        assert [v["gaya"] for v in g["param"].values()] == gaya.daftar()
        assert len(g["opsi"]) == len(gaya.daftar())


def test_gaya_dari_profil_tidak_ditanya_lagi(tmp_path, monkeypatch):
    """Gaya yang disimpan user ("pakai gaya hype seterusnya") berlaku otomatis."""
    import gaya
    monkeypatch.setattr(im, "kumpulkan_fakta", lambda paths: [])
    monkeypatch.setattr(im, "ringkas", lambda fakta: _ringk(n_berucap=3, n_tanpa_ucapan=0, n_suasana=0))
    monkeypatch.setattr(im, "INSPECT_DIR", str(tmp_path / "inspect"))
    kode = lambda r: [p["kode"] for p in r["pertanyaan"]]          # noqa: E731
    assert "gaya" in kode(im.inspeksi([], "edit ya", chat_id="DM A")), "kontrol: tanpa profil ditanya"
    gaya.simpan_gaya("DM A", "hype")
    assert "gaya" not in kode(im.inspeksi([], "edit ya", chat_id="DM A"))
    assert "gaya" in kode(im.inspeksi([], "edit ya", chat_id="DM B")), "profil chat lain tidak berlaku"


@pytest.mark.parametrize("teks, harap", [
    ("edit ya, pakai gaya hype", True),
    ("bikin dengan style elegan dong", True),
    ("edit ya, videonya bersih", False),           # kata biasa, bukan nama preset
])
def test_nama_preset_yang_disebut_dianggap_sudah_dijawab(teks, harap):
    assert im.dari_konteks(teks)["gaya"] is harap


def test_nilai_parameter_di_semua_pertanyaan_valid_menurut_style():
    """Konsistensi lintas-modul: opsi yang KITA tawarkan tidak boleh berisi nilai yang
    ditolak validator (StyleError) -- user memilih huruf, lalu render gagal."""
    import style as st
    for ringk in (_ringk(), _ringk(n_berucap=3, n_tanpa_ucapan=0, n_suasana=0)):
        for q in im.susun_pertanyaan(ringk, _tahu("edit ya")):
            for param in (q.get("param") or {}).values():
                if "textPosition" in param:
                    st.resolve_text_position(param["textPosition"])
                if "textFont" in param:
                    st.resolve_text_font(param["textFont"])
                if "colorFilter" in param:
                    st.resolve_color_filter(param["colorFilter"])
                if "gaya" in param:
                    import gaya
                    gaya.muat(param["gaya"])


def test_pesan_pertanyaan_menyebut_bahan_yang_diterima():
    """Regresi 24 Sep: user mengirim 3 video, hanya 2 yang sampai ke agen (album Telegram
    terpotong) dan TIDAK ADA yang memberi tahu. Sekarang jumlahnya tertulis di pesan."""
    ringk = _ringk()
    q = im.susun_pertanyaan(ringk, _tahu("edit ya"))
    pesan = im.susun_pesan_pertanyaan(q, ringk)
    assert pesan.startswith(f"Bahan yang saya terima: {ringk['n_video']} video")
    assert "kirim ulang" in pesan
    assert not im.susun_pesan_pertanyaan(q).startswith("Bahan yang saya terima")   # tanpa ringk: perilaku lama


def test_opsi_narasi_ai_tidak_menjanjikan_suara_suasana_dan_menyalakan_musik():
    """Regresi 24 Sep: label 'suara suasana tetap ada' salah (mode ai menggantinya), dan
    tanpa `music` di pemetaan agen menebak --music off sendiri."""
    q = next(x for x in im.susun_pertanyaan(_ringk(), _tahu("edit ya")) if x["kode"] == "audio")
    c = next(o for o in q["opsi"] if o["huruf"] == "C")
    assert "tetap ada" not in c["label"]
    assert q["param"]["C"] == {"audioMode": "ai", "music": "on"}
