"""Publikasi konten yang sudah di-APPROVE ke platform.

Dipanggil dari agent4_approval.publish_to_platforms(). Mengembalikan URL konten
yang tayang (live_url), atau None kalau belum bisa dipublikasikan -- None BUKAN
kegagalan diam-diam: alasannya selalu dicetak dan dicatat.

--- Kenapa Instagram butuh URL publik ---

Graph API TIDAK menerima unggahan byte untuk Reels. Kita mengirim `video_url`,
lalu server Meta yang MENARIK file itu sendiri. Artinya video harus dapat
diakses publik lewat HTTPS selama proses unggah berlangsung. Itu syarat Meta,
bukan pilihan desain kita.

`PUBLIC_MEDIA_BASE_URL` adalah alamat publik tempat isi workspace/published/
dapat diambil. Tanpa itu, publikasi ditolak lebih awal dengan pesan jelas --
BUKAN dicoba lalu gagal di tengah dengan error Meta yang membingungkan.

--- Alur Reels (4 langkah) ---
1. POST /{ig-user-id}/media          -> creation_id  (container)
2. GET  /{creation_id}?status_code   -> tunggu sampai FINISHED (Meta mengunduh & memproses)
3. POST /{ig-user-id}/media_publish  -> media_id
4. GET  /{media-id}?fields=permalink -> live_url
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from retry import with_retry

GRAPH_VERSION = os.getenv("GRAPH_API_VERSION", "v21.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"

IG_USER_ID = os.getenv("IG_USER_ID")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")
PUBLIC_MEDIA_BASE_URL = (os.getenv("PUBLIC_MEDIA_BASE_URL") or "").rstrip("/")

HTTP_TIMEOUT = int(os.getenv("PUBLISH_HTTP_TIMEOUT", "60"))
CONTAINER_POLL_INTERVAL = int(os.getenv("PUBLISH_POLL_INTERVAL", "5"))
CONTAINER_POLL_TIMEOUT = int(os.getenv("PUBLISH_POLL_TIMEOUT", "300"))


class PublishError(Exception):
    """Gagal mempublikasikan dengan sebab yang sudah diketahui & bisa dijelaskan."""


def _http_retriable(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 429
    return isinstance(exc, (urllib.error.URLError, TimeoutError, OSError))


def _panggil(path, *, params=None, data=None):
    """Satu panggilan Graph API. Error Meta dinaikkan sebagai PublishError yang
    memuat pesan aslinya -- tanpa itu, kegagalan hanya tampak sebagai HTTP 400."""
    url = f"{GRAPH_BASE}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = urllib.parse.urlencode(data).encode() if data else None

    def sekali():
        try:
            with urllib.request.urlopen(url, data=body, timeout=HTTP_TIMEOUT) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                try:
                    pesan = json.loads(e.read()).get("error", {}).get("message", "")
                except Exception:
                    pesan = ""
                raise PublishError(f"Meta menolak ({e.code}): {pesan or 'tanpa pesan'}") from e
            raise

    return with_retry(sekali, is_retriable=_http_retriable, label=f"graph {path}")


def public_url_for(video_path):
    """URL publik untuk file di workspace/published/, atau None kalau belum diatur."""
    if not PUBLIC_MEDIA_BASE_URL:
        return None
    return f"{PUBLIC_MEDIA_BASE_URL}/{os.path.basename(video_path)}"


def instagram_siap():
    """(siap, alasan). Diperiksa SEBELUM mencoba apa pun."""
    kurang = []
    if not IG_USER_ID:
        kurang.append("IG_USER_ID")
    if not IG_ACCESS_TOKEN:
        kurang.append("IG_ACCESS_TOKEN")
    if not PUBLIC_MEDIA_BASE_URL:
        kurang.append("PUBLIC_MEDIA_BASE_URL")
    if kurang:
        return False, f"{', '.join(kurang)} belum diisi di .env"
    return True, ""


def _tunggu_container(creation_id):
    """Meta mengunduh & memproses video secara asinkron; publish sebelum FINISHED
    akan ditolak. Dibatasi CONTAINER_POLL_TIMEOUT supaya tidak menggantung."""
    batas = time.time() + CONTAINER_POLL_TIMEOUT
    terakhir = "?"
    while time.time() < batas:
        hasil = _panggil(creation_id, params={
            "fields": "status_code,status", "access_token": IG_ACCESS_TOKEN})
        terakhir = hasil.get("status_code", "?")
        if terakhir == "FINISHED":
            return
        if terakhir in ("ERROR", "EXPIRED"):
            raise PublishError(
                f"Meta gagal memproses video: {terakhir} — {hasil.get('status', '')}"
            )
        print(f"[info] publish: container {terakhir}, menunggu...")
        time.sleep(CONTAINER_POLL_INTERVAL)
    raise PublishError(
        f"Meta belum selesai memproses setelah {CONTAINER_POLL_TIMEOUT} detik "
        f"(status terakhir: {terakhir})."
    )


def publish_instagram_reels(video_path, caption=""):
    """Publikasikan satu video sebagai Reels. Return permalink, atau raise PublishError."""
    siap, alasan = instagram_siap()
    if not siap:
        raise PublishError(alasan)

    video_url = public_url_for(video_path)
    print(f"[info] publish: Meta akan menarik video dari {video_url}")

    container = _panggil(f"{IG_USER_ID}/media", data={
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": IG_ACCESS_TOKEN,
    })
    creation_id = container.get("id")
    if not creation_id:
        raise PublishError(f"Meta tidak mengembalikan creation_id: {container}")

    _tunggu_container(creation_id)

    hasil = _panggil(f"{IG_USER_ID}/media_publish", data={
        "creation_id": creation_id, "access_token": IG_ACCESS_TOKEN})
    media_id = hasil.get("id")
    if not media_id:
        raise PublishError(f"Meta tidak mengembalikan media_id: {hasil}")

    try:
        info = _panggil(media_id, params={
            "fields": "permalink", "access_token": IG_ACCESS_TOKEN})
        return info.get("permalink") or f"https://www.instagram.com/p/{media_id}/"
    except Exception:
        # Sudah TERBIT; gagal mengambil permalink bukan alasan menyatakan gagal.
        return f"https://www.instagram.com/p/{media_id}/"


def publish(video_path, caption=""):
    """Titik masuk tunggal. Return live_url, atau None dengan alasan tercetak.

    TIDAK pernah melempar ke pemanggil: konten sudah disetujui user dan sudah
    diarsipkan; kegagalan publish tidak boleh menggagalkan alur approval.
    """
    try:
        return publish_instagram_reels(video_path, caption)
    except PublishError as e:
        print(f"[warn] publish Instagram dilewati: {e}")
    except Exception as e:
        print(f"[warn] publish Instagram gagal tak terduga: {type(e).__name__}: {e}")
    return None
