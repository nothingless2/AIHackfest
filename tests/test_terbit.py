"""Posting lewat Zernio (scripts/terbit.py): dua langkah, hanya setelah user setuju, sekali pakai,
key hanya ke host layanan, dan kegagalan ambigu tidak diulang. Jaringan dipalsukan."""

import json
import os
import time
import urllib.error

import pytest

import terbit as tb


class Zernio:
    """Layanan palsu: merekam (metode, url, headers, body) dan menjawab seperti dokumentasinya."""

    def __init__(self):
        self.panggil = []
        self.akun = [{"_id": "ig1", "platform": "instagram", "username": "tokoku", "isActive": True},
                     {"_id": "tt1", "platform": "tiktok", "username": "tokoku_tt", "isActive": True}]
        self.post = lambda body: (201, {"post": {"_id": "p1", "status": "published", "platforms": [
            {"platform": body["platforms"][0]["platform"], "status": "published",
             "platformPostUrl": "https://example.com/p/1"}]}})

    def __call__(self, metode, url, *, data=None, headers=None, timeout=None):
        self.panggil.append({"metode": metode, "url": url, "headers": dict(headers or {}),
                             "body": json.loads(data) if data and (headers or {}).get("Content-Type") == "application/json" else None})
        if url.startswith("https://storage.example/"):
            return 200, b""
        jalur = url.replace(tb.BASIS, "")
        if jalur == "/accounts":
            return 200, json.dumps({"accounts": self.akun}).encode()
        if jalur.endswith("/tiktok/creator-info"):
            return 200, json.dumps({"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]}).encode()
        if jalur == "/media/presign":
            n = sum(1 for p in self.panggil if p["url"].endswith("/media/presign"))
            return 200, json.dumps({"uploadUrl": f"https://storage.example/put/{n}",
                                    "publicUrl": f"https://cdn.example/f{n}"}).encode()
        if jalur == "/posts":
            kode, isi = self.post(json.loads(data))
            return kode, json.dumps(isi).encode()
        raise AssertionError(f"panggilan tak terduga: {metode} {url}")

    def ke(self, akhiran):
        return [p for p in self.panggil if p["url"].endswith(akhiran)]


@pytest.fixture
def z(monkeypatch, tmp_path):
    import carousel
    import common
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_rahasia_palsu")
    monkeypatch.setattr(tb, "TERBIT_DIR", str(tmp_path / "terbit"))
    monkeypatch.setattr(tb, "PUBLISH_HISTORY_PATH", str(tmp_path / "publish_history.json"))
    monkeypatch.setattr(carousel, "CAROUSEL_DIR", str(tmp_path / "carousel"))
    monkeypatch.setattr(common, "DRAFTS_DIR", str(tmp_path / "drafts"))
    zz = Zernio()
    monkeypatch.setattr(tb, "_http", zz)
    return zz


def _carousel(tmp_path, cid="abc123", chat="DM A", n=3, platform=("ig", "tiktok")):
    import carousel
    d = os.path.join(carousel.CAROUSEL_DIR, cid)
    os.makedirs(d, exist_ok=True)
    berkas = {}
    for p in platform:
        berkas[p] = []
        for i in range(n):
            f = os.path.join(d, f"{p}_{i + 1:02d}.jpg")
            open(f, "wb").write(b"jpg")
            berkas[p].append(f)
    json.dump({"chat_id": chat, "carousel_id": cid, "caption": "Cerita ganti API key.", "hashtags": ["#api"],
               "slides": [{"jenis": "hook", "judul": "Ganti API key kok error?"}], "slide_berkas": berkas},
              open(os.path.join(d, "meta.json"), "w"))
    return cid


def _run(monkeypatch, tmp_path, run_id="run12345", chat="DM A", durasi=30.0, mode="original"):
    import revisi
    import vision
    os.makedirs(revisi.REVISI_DIR, exist_ok=True)
    json.dump({"run_id": run_id, "chat_id": chat, "dibuat": time.time(), "bahan": [], "prefix": "x",
               "brief": {"judul": "Judul video", "deskripsi": "Deskripsi.", "hashtags": ["#a", "#b"],
                         "audio_mode": mode}}, open(os.path.join(revisi.REVISI_DIR, f"{run_id}.json"), "w"))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"mp4")
    monkeypatch.setattr(tb, "draft_video_path_for_run", lambda r: str(video))
    monkeypatch.setattr(vision, "durasi_video", lambda p: durasi)
    return run_id


# ------------------------------------------------------------------ kesiapan

def test_tanpa_key_tidak_ada_panggilan_jaringan(z, monkeypatch):
    monkeypatch.setenv("ZERNIO_API_KEY", "")
    with pytest.raises(tb.TerbitError) as e:
        tb.akun_terhubung()
    assert e.value.kode == "terbit_tidak_siap" and z.panggil == []


def test_periksa_tidak_membocorkan_key(z, capsys):
    assert tb.main(["periksa"]) == 0
    out = capsys.readouterr().out
    assert "sk_rahasia_palsu" not in out and json.loads(out)["siap"] == ["instagram", "tiktok"]


# ------------------------------------------------------------------ siapkan

def test_siapkan_tidak_mengunggah_apa_pun(z, tmp_path):
    r = tb.siapkan(chat_id="DM A", platform="instagram", carousel=_carousel(tmp_path))
    assert r["ok"] and r["jumlah_berkas"] == 3 and "@tokoku" in r["pratinjau"] and "langsung TERBIT" in r["pratinjau"]
    assert not z.ke("/media/presign") and not z.ke("/posts"), "langkah 1 hanya membaca"
    assert json.load(open(tb._path(r["id"])))["status"] == "menunggu"


def test_pratinjau_tiktok_menyebut_draf_dan_pilihan_privasi(z, tmp_path):
    r = tb.siapkan(chat_id="DM A", platform="tiktok", carousel=_carousel(tmp_path))
    assert r["privasi_tiktok"] == ["PUBLIC_TO_EVERYONE", "SELF_ONLY"] and "DRAF" in r["pratinjau"]


@pytest.mark.parametrize("arg, kode", [
    ({"chat_id": ""}, "chat_tidak_diketahui"),
    ({"platform": "youtube"}, "argumen_invalid"),
    ({"chat_id": "DM B"}, "carousel_chat_lain"),
    ({"carousel": "tidakada"}, "carousel_tidak_ada"),
    ({"carousel": "../abc123"}, "carousel_tidak_ada"),
    ({"caption": "x" * 2201}, "caption_kepanjangan"),
])
def test_gerbang_siapkan(z, tmp_path, arg, kode):
    cid = _carousel(tmp_path)
    with pytest.raises(tb.TerbitError) as e:
        tb.siapkan(**{"chat_id": "DM A", "platform": "instagram", "carousel": cid, **arg})
    assert e.value.kode == kode


def test_video_milik_chat_lain_dan_reel_kepanjangan_ditolak(z, monkeypatch, tmp_path):
    run = _run(monkeypatch, tmp_path, durasi=120.0)
    with pytest.raises(tb.TerbitError) as e:
        tb.siapkan(chat_id="DM B", platform="instagram", run=run)
    assert e.value.kode == "revisi_chat_lain"
    with pytest.raises(tb.TerbitError) as e:
        tb.siapkan(chat_id="DM A", platform="instagram", run=run)
    assert e.value.kode == "video_kepanjangan"
    assert tb.siapkan(chat_id="DM A", platform="tiktok", run=run)["ok"], "TikTok tidak punya batas 90 dtk itu"


def test_akun_belum_terhubung_atau_ganda_tidak_ditebak(z, tmp_path):
    cid = _carousel(tmp_path)
    z.akun = [a for a in z.akun if a["platform"] != "instagram"]
    with pytest.raises(tb.TerbitError) as e:
        tb.siapkan(chat_id="DM A", platform="instagram", carousel=cid)
    assert e.value.kode == "akun_belum_terhubung"
    z.akun += [{"_id": "ig1", "platform": "instagram", "username": "a"}, {"_id": "ig2", "platform": "instagram", "username": "b"}]
    with pytest.raises(tb.TerbitError) as e:
        tb.siapkan(chat_id="DM A", platform="instagram", carousel=cid)
    assert e.value.kode == "akun_ganda"


# ------------------------------------------------------------------ kirim

def test_kirim_instagram_carousel_bentuk_permintaan_dan_key(z, tmp_path):
    r = tb.siapkan(chat_id="DM A", platform="instagram", carousel=_carousel(tmp_path))
    h = tb.kirim(chat_id="DM A", id_=r["id"], setuju="ya posting")
    assert h["ok"] and h["mode"] == "terbit" and h["url"] == "https://example.com/p/1"
    (post,) = z.ke("/posts")
    assert post["body"]["publishNow"] is True and "scheduledFor" not in post["body"], "tidak ada penjadwalan"
    assert post["body"]["mediaItems"] == [{"type": "image", "url": f"https://cdn.example/f{i}"} for i in (1, 2, 3)]
    assert post["body"]["platforms"] == [{"platform": "instagram", "accountId": "ig1", "platformSpecificData": {}}]
    for p in z.panggil:
        ada_key = "sk_rahasia_palsu" in json.dumps(p["headers"])
        assert ada_key == p["url"].startswith(tb.BASIS), f"key hanya ke host layanan: {p['url']}"
    assert len([p for p in z.panggil if p["url"].startswith("https://storage.example/")]) == 3, "kontrol: unggah terjadi"
    riwayat = json.load(open(tb.PUBLISH_HISTORY_PATH))
    assert riwayat[-1]["status"] == "PUBLISHED" and riwayat[-1]["publish_id"] == "p1"


def test_tiktok_bawaan_draf_dan_privasi_harus_pilihan_akun(z, tmp_path):
    r = tb.siapkan(chat_id="DM A", platform="tiktok", carousel=_carousel(tmp_path))
    with pytest.raises(tb.TerbitError) as e:
        tb.kirim(chat_id="DM A", id_=r["id"], setuju="ya", privasi="FRIENDS_ONLY")
    assert e.value.kode == "privasi_tidak_sah" and not z.ke("/posts")
    h = tb.kirim(chat_id="DM A", id_=r["id"], setuju="ya")
    ts = z.ke("/posts")[0]["body"]["tiktokSettings"]
    assert h["mode"] == "draf" and ts["draft"] is True and "privacy_level" not in ts
    assert ts["media_type"] == "photo" and ts["content_preview_confirmed"] and ts["express_consent_given"]
    assert json.load(open(tb.PUBLISH_HISTORY_PATH))[-1]["status"] == "DRAFT"


def test_tiktok_video_terbit_dengan_privasi_pilihan_dan_label_ai(z, monkeypatch, tmp_path):
    r = tb.siapkan(chat_id="DM A", platform="tiktok", run=_run(monkeypatch, tmp_path, mode="ai"))
    tb.kirim(chat_id="DM A", id_=r["id"], setuju="posting publik", privasi="PUBLIC_TO_EVERYONE")
    body = z.ke("/posts")[0]["body"]
    assert body["tiktokSettings"]["privacy_level"] == "PUBLIC_TO_EVERYONE" and "draft" not in body["tiktokSettings"]
    assert body["tiktokSettings"]["video_made_with_ai"] is True, "narasi AI diberi label"
    assert body["mediaItems"][0]["type"] == "video" and "Judul video" in body["content"]


@pytest.mark.parametrize("ubah, kode", [
    ({"setuju": ""}, "belum_disetujui"),
    ({"setuju": "   "}, "belum_disetujui"),
    ({"chat_id": "DM B"}, "permintaan_chat_lain"),
    ({"id_": "tidakada"}, "permintaan_tidak_ada"),
    ({"id_": "../../x"}, "permintaan_tidak_ada"),
    ({"privasi": "SELF_ONLY"}, "argumen_invalid"),
])
def test_gerbang_kirim_tanpa_unggah(z, tmp_path, ubah, kode):
    r = tb.siapkan(chat_id="DM A", platform="instagram", carousel=_carousel(tmp_path))
    with pytest.raises(tb.TerbitError) as e:
        tb.kirim(**{"chat_id": "DM A", "id_": r["id"], "setuju": "ya", **ubah})
    assert e.value.kode == kode and not z.ke("/media/presign") and not z.ke("/posts")


def test_pratinjau_kedaluwarsa_dan_tidak_bisa_dikirim_dua_kali(z, tmp_path):
    r = tb.siapkan(chat_id="DM A", platform="instagram", carousel=_carousel(tmp_path))
    with pytest.raises(tb.TerbitError) as e:
        tb.kirim(chat_id="DM A", id_=r["id"], setuju="ya", sekarang=time.time() + tb.BERLAKU_DETIK + 5)
    assert e.value.kode == "pratinjau_kedaluwarsa"
    tb.kirim(chat_id="DM A", id_=r["id"], setuju="ya")
    with pytest.raises(tb.TerbitError) as e:
        tb.kirim(chat_id="DM A", id_=r["id"], setuju="ya")
    assert e.value.kode == "sudah_diproses" and len(z.ke("/posts")) == 1


def _http_error(kode):
    import io
    return urllib.error.HTTPError("https://zernio.com/api/v1/posts", kode, "x", {}, io.BytesIO(b'{"error":"x"}'))


@pytest.mark.parametrize("gagal, status, kode", [
    (lambda b: (_ for _ in ()).throw(_http_error(422)), "gagal", "layanan_menolak"),      # ditolak tegas
    (lambda b: (_ for _ in ()).throw(_http_error(502)), "tidak_pasti", "tidak_pasti"),
    (lambda b: (_ for _ in ()).throw(TimeoutError("habis")), "tidak_pasti", "tidak_pasti"),
])
def test_gagal_membuat_post_ambigu_tidak_boleh_diulang(z, tmp_path, gagal, status, kode):
    r = tb.siapkan(chat_id="DM A", platform="instagram", carousel=_carousel(tmp_path))
    z.post = gagal
    with pytest.raises(tb.TerbitError) as e:
        tb.kirim(chat_id="DM A", id_=r["id"], setuju="ya")
    assert e.value.kode == kode and json.load(open(tb._path(r["id"])))["status"] == status
    assert not os.path.exists(tb.PUBLISH_HISTORY_PATH), "yang tidak terbukti terbit tidak dicatat terbit"
    z.post = Zernio().post
    with pytest.raises(tb.TerbitError) as e:
        tb.kirim(chat_id="DM A", id_=r["id"], setuju="ya")
    assert e.value.kode == "sudah_diproses" and len(z.ke("/posts")) == 1, "tidak dikirim ulang"


def test_unggah_gagal_belum_ada_post(z, monkeypatch, tmp_path):
    r = tb.siapkan(chat_id="DM A", platform="instagram", carousel=_carousel(tmp_path))
    asli = z.__call__

    def putus(metode, url, **k):
        if url.startswith("https://storage.example/"):
            raise ConnectionError("putus")
        return asli(metode, url, **k)
    monkeypatch.setattr(tb, "_http", putus)
    with pytest.raises(tb.TerbitError) as e:
        tb.kirim(chat_id="DM A", id_=r["id"], setuju="ya")
    assert e.value.kode == "unggah_gagal" and not z.ke("/posts")
    assert json.load(open(tb._path(r["id"])))["status"] == "gagal"


def test_url_unggah_bukan_https_ditolak(z, monkeypatch, tmp_path):
    r = tb.siapkan(chat_id="DM A", platform="instagram", carousel=_carousel(tmp_path))
    asli = z.__call__

    def http(metode, url, **k):
        if url.endswith("/media/presign"):
            return 200, json.dumps({"uploadUrl": "http://storage.example/put/1", "publicUrl": "https://cdn/x"}).encode()
        return asli(metode, url, **k)
    monkeypatch.setattr(tb, "_http", http)
    with pytest.raises(tb.TerbitError) as e:
        tb.kirim(chat_id="DM A", id_=r["id"], setuju="ya")
    assert e.value.kode == "layanan_aneh" and not z.ke("/posts")
