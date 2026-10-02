"""Posting ke Instagram / TikTok lewat Zernio -- HANYA setelah user menyetujui tiap posting.

Dua langkah, supaya tidak ada yang terbit tanpa dilihat user (human-in-the-loop):
  1. siapkan : cek kepemilikan (run/carousel milik chat ini), berkas, batas platform, akun yang
               terhubung; simpan permintaan "menunggu" dan kembalikan PRATINJAU untuk ditunjukkan
               ke user. Belum ada yang diunggah.
  2. kirim   : hanya dengan id permintaan itu + kalimat persetujuan user. Sekali pakai.

Tidak ada penjadwal di server ini (aturan tetap): posting selalu "sekarang". TikTok bawaannya DRAF
(masuk inbox TikTok, user menyelesaikannya di aplikasi); terbit langsung hanya bila user memilih
privasinya sendiri dari pilihan akun itu -- TikTok mewajibkan pratinjau & persetujuan eksplisit.

API key (ZERNIO_API_KEY di .env) hanya dikirim ke host Zernio, tidak pernah ke URL unggah.
Kegagalan yang ambigu (waktu habis saat membuat post) TIDAK diulang: postingnya mungkin sudah
terbit, jadi statusnya "tidak_pasti" dan user diminta mengecek akunnya (aturan #7).

Ditulis dari dokumentasi Zernio (dibaca 2 Okt 2026): GET /accounts, POST /media/presign + PUT,
POST /posts, GET /accounts/{id}/tiktok/creator-info.

CLI (satu baris JSON):
  python3 scripts/terbit.py periksa
  python3 scripts/terbit.py siapkan --chat-id "<label>" (--run RUN_ID | --carousel ID) --platform instagram|tiktok [--caption "..."]
  python3 scripts/terbit.py kirim --chat-id "<label>" --id ID --setuju "<kalimat user>" [--privasi NILAI]
"""

import argparse
import json
import mimetypes
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    PUBLISH_HISTORY_PATH, STATE_DIR, draft_video_path_for_run, now_iso, read_json, write_json,
)

BASIS = "https://zernio.com/api/v1"
HOST_API = "zernio.com"
TERBIT_DIR = os.path.join(STATE_DIR, "terbit")
BERLAKU_DETIK = 30 * 60              # pratinjau yang disetujui harus masih segar
PLATFORM = ("instagram", "tiktok")
IG_CAPTION, IG_SLIDE, IG_REEL_DETIK, IG_VIDEO_MB = 2200, 10, 90, 300
TIKTOK_JUDUL_FOTO, TIKTOK_DESKRIPSI, TIKTOK_FOTO = 90, 4000, 35
TIMEOUT = 60
TIMEOUT_UNGGAH = 600


class TerbitError(RuntimeError):
    def __init__(self, kode, pesan, http=None):
        super().__init__(pesan)
        self.kode = kode
        self.http = http


class TidakPasti(TerbitError):
    """Permintaan mungkin sudah diproses di sisi layanan; jangan diulang otomatis."""


def kunci():
    return (os.getenv("ZERNIO_API_KEY") or "").strip()


# ------------------------------------------------------------------ jaringan (tes menggantinya)

def _http(metode, url, *, data=None, headers=None, timeout=TIMEOUT):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=metode)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _api(metode, jalur, body=None):
    """Panggilan ke API Zernio. Key HANYA dikirim lewat fungsi ini (host tetap)."""
    if not kunci():
        raise TerbitError("terbit_tidak_siap", "ZERNIO_API_KEY belum diisi di .env. Daftar di zernio.com, "
                                               "hubungkan akun Instagram/TikTok, lalu isi key-nya.")
    url = BASIS + jalur
    assert urllib.parse.urlparse(url).hostname == HOST_API
    headers = {"Authorization": f"Bearer {kunci()}", "User-Agent": "content-factory/1.0"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        _, isi = _http(metode, url, data=data, headers=headers)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise TerbitError("layanan_menolak", f"Zernio menjawab {e.code}: {detail}", http=e.code)
    try:
        return json.loads(isi or b"{}")
    except ValueError:
        raise TerbitError("layanan_aneh", "balasan Zernio bukan JSON")


def akun_terhubung():
    data = _api("GET", "/accounts")
    return [{"id": a.get("_id"), "platform": a.get("platform"), "username": a.get("username") or "",
             "aktif": a.get("isActive") is not False}
            for a in (data.get("accounts") or []) if isinstance(a, dict) and a.get("_id")]


def _akun_untuk(platform):
    cocok = [a for a in akun_terhubung() if a["platform"] == platform and a["aktif"]]
    if not cocok:
        raise TerbitError("akun_belum_terhubung", f"Belum ada akun {platform} aktif yang terhubung di Zernio.")
    if len(cocok) > 1:
        raise TerbitError("akun_ganda", f"Ada {len(cocok)} akun {platform} terhubung; pilih satu di Zernio dulu "
                                        "(tidak menebak akun mana).")
    return cocok[0]


def privasi_tiktok(akun_id):
    data = _api("GET", f"/accounts/{urllib.parse.quote(str(akun_id))}/tiktok/creator-info")
    info = data.get("creatorInfo") or data.get("data") or data
    opsi = info.get("privacy_level_options") or info.get("privacyLevelOptions") or []
    return [str(o) for o in opsi if isinstance(o, str)]


def unggah(path):
    """Berkas lokal -> URL publik Zernio (presign + PUT). Key TIDAK dikirim ke URL unggah."""
    tipe = mimetypes.guess_type(path)[0] or "application/octet-stream"
    pre = _api("POST", "/media/presign", {"filename": os.path.basename(path), "contentType": tipe,
                                          "size": os.path.getsize(path)})
    upload_url, publik = pre.get("uploadUrl"), pre.get("publicUrl")
    if not upload_url or not publik or urllib.parse.urlparse(upload_url).scheme != "https":
        raise TerbitError("layanan_aneh", "balasan presign Zernio tidak berisi URL unggah https")
    with open(path, "rb") as f:
        isi = f.read()
    try:
        _http("PUT", upload_url, data=isi, headers={"Content-Type": tipe}, timeout=TIMEOUT_UNGGAH)
    except urllib.error.HTTPError as e:
        raise TerbitError("unggah_gagal", f"unggah ditolak ({e.code})")
    return publik


# ------------------------------------------------------------------ bahan posting

def _bahan_run(run_id, chat_id):
    import revisi
    from vision import durasi_video
    try:
        rec = revisi.muat(run_id, chat_id)
    except Exception as e:  # noqa: BLE001  (RevisiError: milik chat lain / kedaluwarsa / tidak ada)
        raise TerbitError(getattr(e, "kode", "run_tidak_ada"), str(e))
    video = draft_video_path_for_run(run_id)
    if not os.path.isfile(video):
        raise TerbitError("video_hilang", "Berkas video itu sudah tidak ada di server. Render ulang dulu.")
    brief = rec.get("brief") or {}
    tag = " ".join(h for h in (brief.get("hashtags") or []) if isinstance(h, str))
    caption = "\n\n".join(b for b in (brief.get("judul"), brief.get("deskripsi"), tag) if b)
    return {"jenis": "video", "berkas": [video], "caption": caption, "judul": brief.get("judul") or "",
            "durasi": durasi_video(video), "suara_ai": brief.get("audio_mode") == "ai"}


def _bahan_carousel(cid, chat_id, platform):
    import carousel
    if not cid.isalnum():
        raise TerbitError("carousel_tidak_ada", "Carousel itu tidak ditemukan.")
    meta = read_json(os.path.join(carousel.CAROUSEL_DIR, cid, "meta.json"), None)
    if not meta:
        raise TerbitError("carousel_tidak_ada", "Carousel itu tidak ditemukan.")
    if meta.get("chat_id") != str(chat_id):
        raise TerbitError("carousel_chat_lain", "Carousel itu bukan milik chat ini.")
    kunci_platform = "ig" if platform == "instagram" else "tiktok"
    berkas = (meta.get("slide_berkas") or {}).get(kunci_platform) or []
    if not berkas:
        raise TerbitError("ukuran_tidak_ada", f"Carousel itu belum dibuat untuk {platform}. Buat ulang dengan "
                                              f"--platform {kunci_platform}.")
    if any(not os.path.isfile(b) for b in berkas):
        raise TerbitError("slide_hilang", "Berkas slide sudah tidak ada di server. Buat ulang carouselnya.")
    tag = " ".join(meta.get("hashtags") or [])
    judul = (meta.get("slides") or [{}])[0].get("judul") or ""
    return {"jenis": "carousel", "berkas": berkas, "caption": "\n\n".join(b for b in (meta.get("caption"), tag) if b),
            "judul": judul, "durasi": None, "suara_ai": False}


def _periksa_batas(platform, bahan, caption):
    if platform == "instagram":
        if len(caption) > IG_CAPTION:
            raise TerbitError("caption_kepanjangan", f"Caption {len(caption)} karakter; batas Instagram {IG_CAPTION}.")
        if bahan["jenis"] == "carousel" and len(bahan["berkas"]) > IG_SLIDE:
            raise TerbitError("slide_kebanyakan", f"Carousel Instagram maksimal {IG_SLIDE} slide.")
        if bahan["jenis"] == "video":
            if (bahan["durasi"] or 0) > IG_REEL_DETIK:
                raise TerbitError("video_kepanjangan", f"Reels maksimal {IG_REEL_DETIK} detik; video ini "
                                                       f"{bahan['durasi']:.0f} detik.")
            if os.path.getsize(bahan["berkas"][0]) > IG_VIDEO_MB * 1024 * 1024:
                raise TerbitError("video_kebesaran", f"Video melebihi {IG_VIDEO_MB} MB.")
    else:
        if len(caption) > TIKTOK_DESKRIPSI:
            raise TerbitError("caption_kepanjangan", f"Caption {len(caption)} karakter; batas TikTok {TIKTOK_DESKRIPSI}.")
        if bahan["jenis"] == "carousel" and len(bahan["berkas"]) > TIKTOK_FOTO:
            raise TerbitError("slide_kebanyakan", f"Foto geser TikTok maksimal {TIKTOK_FOTO}.")


# ------------------------------------------------------------------ langkah 1: siapkan

def _path(id_):
    if not (isinstance(id_, str) and id_.isalnum() and len(id_) <= 32):
        raise TerbitError("permintaan_tidak_ada", "Permintaan posting itu tidak ditemukan.")
    return os.path.join(TERBIT_DIR, id_ + ".json")


def siapkan(*, chat_id, platform, run=None, carousel=None, caption=None, sekarang=None):
    if not str(chat_id or "").strip():
        raise TerbitError("chat_tidak_diketahui", "Label chat tidak diketahui.")
    if platform not in PLATFORM:
        raise TerbitError("argumen_invalid", f"platform harus salah satu dari {', '.join(PLATFORM)}")
    if bool(run) == bool(carousel):
        raise TerbitError("argumen_invalid", "sebutkan tepat satu: --run atau --carousel")
    bahan = _bahan_run(run, chat_id) if run else _bahan_carousel(carousel, chat_id, platform)
    teks = (caption if caption is not None else bahan["caption"]).strip()
    _periksa_batas(platform, bahan, teks)
    akun = _akun_untuk(platform)
    opsi = privasi_tiktok(akun["id"]) if platform == "tiktok" else []
    id_ = secrets.token_hex(5)
    rec = {"id": id_, "chat_id": str(chat_id), "platform": platform, "akun": akun, "jenis": bahan["jenis"],
           "berkas": bahan["berkas"], "caption": teks, "judul": bahan["judul"], "suara_ai": bahan["suara_ai"],
           "run_id": run, "carousel_id": carousel, "privasi_tiktok": opsi,
           "dibuat": sekarang or time.time(), "status": "menunggu"}
    write_json(_path(id_), rec)
    baris = [f"Akun: @{akun['username']} ({platform})",
             f"Isi: {'video' if bahan['jenis'] == 'video' else str(len(bahan['berkas'])) + ' slide'}",
             f"Caption:\n{teks}"]
    if platform == "tiktok":
        baris.append("Bawaan: masuk DRAF TikTok (kamu yang menekan posting di aplikasi). Terbit langsung? "
                     "Pilih privasinya: " + (", ".join(opsi) or "(akun tidak memberi pilihan)"))
    else:
        baris.append("Instagram tidak punya draf lewat API: begitu disetujui langsung TERBIT.")
    return {"ok": True, "id": id_, "platform": platform, "akun": akun["username"], "jenis": bahan["jenis"],
            "jumlah_berkas": len(bahan["berkas"]), "caption": teks, "privasi_tiktok": opsi,
            "berlaku_menit": BERLAKU_DETIK // 60, "pratinjau": "\n".join(baris)}


# ------------------------------------------------------------------ langkah 2: kirim

def _badan_post(rec, media, privasi):
    tipe = "video" if rec["jenis"] == "video" else "image"
    badan = {"content": rec["caption"], "mediaItems": [{"type": tipe, "url": u} for u in media],
             "platforms": [{"platform": rec["platform"], "accountId": rec["akun"]["id"]}], "publishNow": True}
    if rec["platform"] == "instagram":
        badan["platforms"][0]["platformSpecificData"] = {"shareToFeed": True} if tipe == "video" else {}
        return badan
    ts = {"allow_comment": True, "content_preview_confirmed": True, "express_consent_given": True}
    if privasi == "draf":
        ts["draft"] = True
    else:
        ts["privacy_level"] = privasi
    if tipe == "video":
        ts.update(allow_duet=True, allow_stitch=True)
        if rec.get("suara_ai"):
            ts["video_made_with_ai"] = True
    else:
        badan["content"] = (rec.get("judul") or rec["caption"])[:TIKTOK_JUDUL_FOTO]
        ts.update(media_type="photo", photo_cover_index=0, description=rec["caption"][:TIKTOK_DESKRIPSI],
                  auto_add_music=True)
    badan["tiktokSettings"] = ts
    return badan


def kirim(*, chat_id, id_, setuju, privasi=None, sekarang=None):
    rec = read_json(_path(id_), None)
    if not rec:
        raise TerbitError("permintaan_tidak_ada", "Permintaan posting itu tidak ditemukan.")
    if rec.get("chat_id") != str(chat_id or ""):
        raise TerbitError("permintaan_chat_lain", "Permintaan posting itu bukan milik chat ini.")
    if rec.get("status") != "menunggu":
        raise TerbitError("sudah_diproses", f"Permintaan itu sudah {rec.get('status')}; tidak dikirim dua kali.")
    if (sekarang or time.time()) - float(rec.get("dibuat") or 0) > BERLAKU_DETIK:
        raise TerbitError("pratinjau_kedaluwarsa", "Pratinjaunya sudah lebih dari "
                                                   f"{BERLAKU_DETIK // 60} menit. Siapkan ulang dan tunjukkan lagi.")
    if not str(setuju or "").strip():
        raise TerbitError("belum_disetujui", "Posting hanya setelah user menyetujui pratinjaunya. Sertakan "
                                             "kalimat persetujuan user di --setuju.")
    if rec["platform"] == "tiktok":
        privasi = (privasi or "draf").strip()
        if privasi != "draf" and privasi not in (rec.get("privasi_tiktok") or []):
            raise TerbitError("privasi_tidak_sah", "Privasi TikTok harus 'draf' atau salah satu pilihan akun: "
                                                   + ", ".join(rec.get("privasi_tiktok") or []))
    elif privasi:
        raise TerbitError("argumen_invalid", "--privasi hanya untuk TikTok.")
    if any(not os.path.isfile(b) for b in rec["berkas"]):
        raise TerbitError("berkas_hilang", "Berkasnya sudah tidak ada di server.")

    # Klaim dulu: dari dua `kirim` yang sama hanya satu yang lanjut, dan proses yang mati di tengah
    # tidak meninggalkan status "menunggu" yang bisa dikirim ulang.
    rec.update(status="mengirim", setuju=str(setuju).strip()[:500], privasi=privasi, mulai_kirim=now_iso())
    write_json(_path(id_), rec)
    try:
        media = [unggah(b) for b in rec["berkas"]]
    except TerbitError as e:
        rec.update(status="gagal", alasan=str(e))
        write_json(_path(id_), rec)
        raise
    except Exception as e:  # noqa: BLE001  (jaringan putus saat unggah: belum ada post yang dibuat)
        rec.update(status="gagal", alasan=f"{type(e).__name__}: {e}")
        write_json(_path(id_), rec)
        raise TerbitError("unggah_gagal", f"Unggah gagal ({type(e).__name__}). Belum ada yang terbit; boleh "
                                          "disiapkan ulang.")
    try:
        hasil = _api("POST", "/posts", _badan_post(rec, media, privasi))
    except Exception as e:  # noqa: BLE001
        # Hanya penolakan tegas (4xx) yang PASTI berarti tidak terbit. Waktu habis, sambungan putus,
        # 5xx, atau balasan tak terbaca: post MUNGKIN sudah dibuat -> tidak diulang (aturan #7).
        pasti_gagal = isinstance(e, TerbitError) and e.kode == "layanan_menolak" and 400 <= (e.http or 0) < 500
        rec.update(status="gagal" if pasti_gagal else "tidak_pasti", alasan=f"{type(e).__name__}: {e}"[:400])
        write_json(_path(id_), rec)
        if pasti_gagal:
            raise
        raise TidakPasti("tidak_pasti", "Jawaban layanan tidak jelas saat membuat posting. Postingnya MUNGKIN "
                                        "sudah terbit: cek akunnya dulu. Jangan dikirim ulang sebelum dicek.")
    post = hasil.get("post") or {}
    baris = next((p for p in post.get("platforms") or [] if p.get("platform") == rec["platform"]), {})
    rec.update(status="terkirim", post_id=post.get("_id"), status_layanan=post.get("status"),
               url=baris.get("platformPostUrl"), selesai=now_iso())
    write_json(_path(id_), rec)
    riwayat = read_json(PUBLISH_HISTORY_PATH, []) or []
    riwayat.append({"status": "PUBLISHED" if privasi != "draf" else "DRAFT", "timestamp": now_iso(),
                    "platform": rec["platform"], "publish_id": post.get("_id"), "url": baris.get("platformPostUrl"),
                    "run_id": rec.get("run_id"), "carousel_id": rec.get("carousel_id"), "chat_id": rec["chat_id"],
                    "lewat": "zernio"})
    write_json(PUBLISH_HISTORY_PATH, riwayat)
    return {"ok": True, "id": id_, "platform": rec["platform"], "akun": rec["akun"]["username"],
            "mode": "draf" if privasi == "draf" else "terbit", "status_layanan": post.get("status"),
            "url": baris.get("platformPostUrl"), "post_id": post.get("_id")}


# ------------------------------------------------------------------ CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="Posting ke Instagram/TikTok setelah persetujuan user")
    ap.add_argument("perintah", choices=["periksa", "siapkan", "kirim"])
    ap.add_argument("--chat-id", default="")
    ap.add_argument("--platform", default="")
    ap.add_argument("--run", default=None)
    ap.add_argument("--carousel", default=None)
    ap.add_argument("--caption", default=None)
    ap.add_argument("--id", default="", dest="id_")
    ap.add_argument("--setuju", default="")
    ap.add_argument("--privasi", default=None)
    a = ap.parse_args(argv)
    try:
        if a.perintah == "periksa":
            akun = akun_terhubung()
            out = {"ok": True, "akun": [{k: v for k, v in x.items() if k != "id"} for x in akun],
                   "siap": sorted({x["platform"] for x in akun if x["aktif"] and x["platform"] in PLATFORM})}
        elif a.perintah == "siapkan":
            out = siapkan(chat_id=a.chat_id, platform=a.platform, run=a.run, carousel=a.carousel, caption=a.caption)
        else:
            out = kirim(chat_id=a.chat_id, id_=a.id_, setuju=a.setuju, privasi=a.privasi)
    except TerbitError as e:
        out = {"ok": False, "kode": e.kode, "alasan": str(e)}
    except Exception as e:  # noqa: BLE001
        out = {"ok": False, "kode": "gagal", "alasan": f"{type(e).__name__}: {str(e)[:300]}"}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
