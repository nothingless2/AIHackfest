"""B-roll stok otomatis dari Pexels (API video gratis, butuh key gratis).

ATURAN YANG DIJAGA (semuanya berasal dari kegagalan nyata di repo ini):

- Klip diunduh KE FOLDER KERJA RUN INI SAJA dan dibuang sesudah render. Tidak ada folder
  bersama yang dipindai (versi 21 Sep memindai `workspace/broll_user/` -- melanggar
  aturan #4 dan sudah dihapus).
- Konfigurasi tak lengkap (tanpa key) DITOLAK di titik masuk, sebelum lock dan sebelum
  LLM -- bukan dilewati diam-diam (aturan #7). Kegagalan di tengah jalan (jaringan, kuota,
  hasil kosong) tidak menggagalkan video yang sudah jadi, tapi DILAPORKAN dengan alasan.
- Hanya mode voice-over AI. Mode audio asli/mute masih menampilkan orang yang bicara, dan
  menyisipkan klip di tengahnya menggeser subtitle yang dipatok ke transkrip.
- Jumlah B-roll dibatasi supaya bahan USER tidak terbuang: `alokasi.susun_potongan` hanya
  memakai sebanyak potongan yang muat (min. 1,5 dtk per potongan) untuk durasi narasi.
- Tautan unduhan dari respons API hanya diikuti bila https dan host-nya milik Pexels.
- Pexels meminta kredit kreator; `kredit` dikembalikan supaya ikut ke caption.
"""

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://api.pexels.com/videos/search"
HOST_SAH = (".pexels.com",)
MAKS_UNDUH_MB = 40
DURASI_MIN, DURASI_MAX = 4, 40
JUMLAH_DEFAULT, JUMLAH_MAKS = 3, 6
TIMEOUT = 20


class BrollError(ValueError):
    """Konfigurasi B-roll tidak lengkap/tidak valid. Ditolak sebelum render."""


def _aktif_env(nilai):
    return str(nilai or "").strip().lower() in ("1", "true", "on", "ya", "yes")


def aktif():
    return _aktif_env(os.getenv("BROLL"))


def kunci():
    return (os.getenv("PEXELS_API_KEY") or "").strip()


def tersedia():
    """True bila fitur benar-benar bisa dipakai (ada key). Dipakai untuk memutuskan apakah
    fitur ini boleh DITAWARKAN ke user -- jangan menawarkan yang pasti gagal."""
    return bool(kunci())


def _bersihkan_query(q):
    q = re.sub(r"[^\w\s,-]", " ", str(q or ""), flags=re.UNICODE)
    return re.sub(r"\s+", " ", q).strip()[:80]


def resolve_broll(aktif_=None, query=None, jumlah=None):
    """None kalau tidak diminta; dict {queries, jumlah} kalau diminta; BrollError kalau
    diminta tapi konfigurasinya tidak lengkap."""
    minta = aktif() if aktif_ is None else bool(aktif_)
    if not minta:
        return None
    if not kunci():
        raise BrollError(
            "B-roll diminta tapi PEXELS_API_KEY belum diisi. Daftar gratis di "
            "https://www.pexels.com/api/ lalu isi PEXELS_API_KEY di .env.")
    mentah = query if query is not None else os.getenv("BROLL_QUERY", "")
    queries = [x for x in (_bersihkan_query(p) for p in str(mentah).split(",")) if x]
    raw_n = jumlah if jumlah is not None else os.getenv("BROLL_COUNT", "")
    try:
        n = int(raw_n) if str(raw_n).strip() else JUMLAH_DEFAULT
    except ValueError:
        raise BrollError(f"Jumlah B-roll {raw_n!r} bukan angka.")
    if not 1 <= n <= JUMLAH_MAKS:
        raise BrollError(f"Jumlah B-roll {n} di luar jangkauan 1-{JUMLAH_MAKS}.")
    return {"queries": queries, "jumlah": n}


MAKS_USULAN = 3


def usulan_bersih(usulan, maks=MAKS_USULAN):
    """Usulan B-roll dari BrainIdea -> [{"query", "saat_kata"}] yang aman dipakai.
    Kata kunci dibersihkan sama seperti --broll-query; usulan tanpa kata kunci dibuang."""
    hasil = []
    for u in usulan if isinstance(usulan, list) else []:
        if not isinstance(u, dict):
            continue
        q = _bersihkan_query(u.get("query"))[:60]
        if q:
            hasil.append({"query": q, "saat_kata": str(u.get("saat_kata") or "").strip()[:40]})
    return hasil[:maks]


def orientasi_untuk(lebar, tinggi):
    if tinggi > lebar * 1.1:
        return "portrait"
    if lebar > tinggi * 1.1:
        return "landscape"
    return "square"


# ------------------------------------------------------------------- jaringan
# Dua fungsi kecil ini satu-satunya yang menyentuh jaringan; tes menggantinya.

def _http_get_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def _unduh_ke(url, tujuan, maks_bytes):
    req = urllib.request.Request(url, headers={"User-Agent": "content-factory/1.0"})
    total = 0
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(tujuan, "wb") as f:
        while True:
            blok = r.read(1 << 16)
            if not blok:
                break
            total += len(blok)
            if total > maks_bytes:
                raise BrollError("klip melebihi batas ukuran unduhan")
            f.write(blok)
    return total


def host_sah(url):
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == h.lstrip(".") or host.endswith(h) for h in HOST_SAH)


# ------------------------------------------------------------------- pemilihan

def cari(query, orientasi, per_page=15):
    """Kandidat ternormalisasi dari satu query. Melempar bila HTTP gagal."""
    url = API_URL + "?" + urllib.parse.urlencode(
        {"query": query, "orientation": orientasi, "per_page": per_page, "size": "medium"})
    data = _http_get_json(url, {"Authorization": kunci(), "User-Agent": "content-factory/1.0"})
    hasil = []
    for v in data.get("videos") or []:
        if not isinstance(v, dict) or v.get("id") is None:
            continue
        d = v.get("duration")
        if not isinstance(d, (int, float)) or not DURASI_MIN <= d <= DURASI_MAX:
            continue
        berkas = pilih_berkas(v.get("video_files") or [])
        if not berkas:
            continue
        u = v.get("user") or {}
        gambar = str(v.get("image") or "")
        hasil.append({"id": v["id"], "durasi": d, "berkas": berkas,
                      "halaman": v.get("url") or "", "kreator": u.get("name") or "",
                      "kreator_url": u.get("url") or "",
                      "gambar": gambar if host_sah(gambar) else ""})
    return hasil


def pilih_berkas(berkas, tinggi_min=720, tinggi_maks=2200):
    """Berkas mp4 terkecil yang masih >= 720p; host harus milik Pexels."""
    layak = []
    for b in berkas:
        try:
            h = int(b.get("height") or 0)
            w = int(b.get("width") or 0)
        except (TypeError, ValueError):
            continue
        if b.get("file_type") != "video/mp4" or not host_sah(str(b.get("link") or "")):
            continue
        if max(h, w) < tinggi_min or max(h, w) > tinggi_maks:
            continue
        layak.append((h * w, b))
    return min(layak, key=lambda x: x[0])[1] if layak else None


VARIASI_TERATAS = 3
PERINGKAT_MAKS = 5        # hanya hasil teratas Pexels: ekor daftar terukur tak relevan (25 Sep)
# Kata yang terlalu umum untuk membuktikan relevansi (ada di hampir semua judul klip).
KATA_UMUM = {"a", "an", "the", "of", "in", "on", "at", "with", "and", "for", "to", "by", "from",
             "people", "person", "man", "woman", "men", "women", "young", "old", "group", "close",
             "up", "shot", "view", "video", "footage", "stock", "happy", "smiling", "background",
             "slow", "motion", "top", "aerial", "while", "using", "their", "his", "her"}


def _kata_kunci(teks):
    kata = re.findall(r"[a-z]+", str(teks or "").lower())
    return {k[:-1] if k.endswith("s") and len(k) > 4 else k for k in kata if k not in KATA_UMUM and len(k) > 2}


def relevan(query, kandidat):
    """Judul klip (slug halaman Pexels) berbagi minimal satu kata bermakna dengan kata kunci.
    Dipakai sebagai PREFERENSI urutan, bukan syarat: Pexels mencari secara semantik dan judulnya
    memakai sinonim ('command line terminal' -> 'coding-on-screen'); syarat keras terukur
    menghabisi 8 dari 10 kata kunci nyata (0 klip)."""
    kunci = _kata_kunci(query)
    if not kunci:              # kata kunci tanpa kata bermakna: tidak bisa dinilai, tidak ditolak
        return True
    judul = re.sub(r"\d+", " ", (kandidat.get("halaman") or "").rstrip("/").rsplit("/", 1)[-1].replace("-", " "))
    return bool(kunci & _kata_kunci(judul))


def _urutan_variasi(per_query, run_id):
    """Urutan kandidat: BERGILIRAN menurut peringkat per kata kunci (teratas tiap kata kunci
    dulu), variasi antar run hanya di antara VARIASI_TERATAS hasil teratas. Deterministik per run.

    Versi lama memutar daftar GABUNGAN semua hasil dari posisi acak: render nyata 25 Sep memakai
    hasil ke-30-an ('pria memegang jam', 'klinik hewan') untuk video ajakan donor darah."""
    h = int(hashlib.sha1(str(run_id).encode()).hexdigest(), 16)
    urut = []
    for daftar in per_query:
        # Variasi hanya di antara kandidat teratas yang relevansinya SAMA dengan yang pertama:
        # klip yang judulnya cocok tidak boleh terputar ke belakang oleh variasi antar run.
        n = 0
        while n < min(VARIASI_TERATAS, len(daftar)) and daftar[n].get("_relevan") == daftar[0].get("_relevan"):
            n += 1
        g = h % n if n else 0
        urut.append(daftar[:n][g:] + daftar[:n][:g] + daftar[n:])
    hasil, r = [], 0
    while any(r < len(d) for d in urut):
        hasil += [d[r] for d in urut if r < len(d)]
        r += 1
    return hasil


def _sah_video(path):
    try:
        o = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:format=duration", "-of", "json", path],
            capture_output=True, text=True, timeout=30)
        d = json.loads(o.stdout)
        return bool(d.get("streams")) and float((d.get("format") or {}).get("duration") or 0) > 1
    except (subprocess.SubprocessError, ValueError, OSError):
        return False


def ambil(queries, jumlah, orientasi, folder, run_id, awalan="_broll_", saring=True,
          pakai=(), tolak=()):
    """(daftar_klip, catatan_gagal). Tiap klip: {path, id, durasi, kredit}.

    Revisi cepat: `pakai` = id klip render sebelumnya yang dipertahankan (didahulukan, dalam
    urutan itu), `tolak` = id yang dihapus/diganti user (tidak pernah dipilih lagi).

    Tidak pernah melempar karena jaringan: alasan kegagalan dikembalikan sebagai teks
    supaya video yang sudah jadi tidak digagalkan, tapi user TAHU B-roll-nya tidak ada."""
    os.makedirs(folder, exist_ok=True)
    tolak = {str(i) for i in tolak}
    per_query, terlihat, catatan = [], set(), []
    for q in queries:
        try:
            daftar = [c for c in cari(q, orientasi)
                      if c["id"] not in terlihat and str(c["id"]) not in tolak]
            # Klip yang dipertahankan revisi boleh berada di luar 5 teratas (hasil Pexels bergeser).
            daftar = daftar[:PERINGKAT_MAKS] + [c for c in daftar[PERINGKAT_MAKS:]
                                                if str(c["id"]) in {str(i) for i in pakai}]
            if saring:
                # Yang judulnya berbagi kata dengan kata kunci lebih dulu (urutan stabil).
                for c in daftar:
                    c["_relevan"] = relevan(q, c)
                daftar.sort(key=lambda c: not c["_relevan"])
            terlihat.update(c["id"] for c in daftar)
            per_query.append(daftar)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return [], f"key Pexels ditolak (HTTP {e.code}) -- periksa PEXELS_API_KEY"
            if e.code == 429:
                return [], "kuota API Pexels habis (HTTP 429) -- coba lagi nanti"
            catatan.append(f"pencarian '{q}' gagal (HTTP {e.code})")
        except Exception as e:                       # jaringan, JSON rusak, dsb.
            catatan.append(f"pencarian '{q}' gagal ({type(e).__name__})")
    if not any(per_query):
        return [], "; ".join(catatan) or f"tidak ada klip yang cocok untuk: {', '.join(queries)}"

    urut = _urutan_variasi(per_query, run_id)
    if pakai:
        posisi = {str(i): n for n, i in enumerate(pakai)}
        urut = (sorted((c for c in urut if str(c["id"]) in posisi), key=lambda c: posisi[str(c["id"])])
                + [c for c in urut if str(c["id"]) not in posisi])
    klip = []
    for c in urut:
        if len(klip) >= jumlah:
            break
        tujuan = os.path.join(folder, f"{awalan}{len(klip)}.mp4")
        try:
            _unduh_ke(c["berkas"]["link"], tujuan, MAKS_UNDUH_MB * 1048576)
            if not _sah_video(tujuan):
                raise BrollError("berkas unduhan bukan video yang bisa dibaca")
        except Exception as e:
            catatan.append(f"klip {c['id']} dilewati ({type(e).__name__}: {str(e)[:60]})")
            if os.path.exists(tujuan):
                os.remove(tujuan)
            continue
        kredit = f"Video oleh {c['kreator'] or 'kreator Pexels'} di Pexels"
        klip.append({"path": tujuan, "id": c["id"], "durasi": c["durasi"], "kredit": kredit,
                     "halaman": c["halaman"]})
    gagal = "; ".join(catatan) if not klip else None
    return klip, gagal


MAKS_GAMBAR_MB = 3


def pratinjau(query, orientasi, tujuan):
    """Gambar pratinjau (JPG) klip teratas untuk `query` -- untuk storyboard draf, tanpa
    mengunduh videonya. Return (path, None) atau (None, alasan). Tidak pernah melempar."""
    try:
        kandidat = sorted((c for c in cari(query, orientasi, per_page=5) if c.get("gambar")),
                          key=lambda c: not relevan(query, c))
    except Exception as e:
        return None, f"pencarian gagal ({type(e).__name__})"
    if not kandidat:
        return None, "tidak ada klip yang cocok"
    try:
        _unduh_ke(kandidat[0]["gambar"], tujuan, MAKS_GAMBAR_MB * 1048576)
        return tujuan, None
    except Exception as e:
        return None, f"gambar gagal diunduh ({type(e).__name__})"


def susun_urutan(aset_user, klip_broll):
    """Sisipkan B-roll SETELAH bahan user (bahan pertama tetap milik user: ia jadi cover
    dan pembuka). Klip ke-k masuk setelah bahan ke-(k mod n). Urutan relatif bahan user
    tidak berubah."""
    n = len(aset_user)
    if not n or not klip_broll:
        return list(aset_user)
    setelah = [[] for _ in range(n)]
    for k, b in enumerate(klip_broll):
        setelah[k % n].append(b)
    hasil = []
    for i, a in enumerate(aset_user):
        hasil.append(a)
        hasil.extend(setelah[i])
    return hasil


def jatah(jumlah_diminta, jumlah_aset_user, total_detik, min_klip):
    """Berapa B-roll yang boleh disisipkan tanpa membuang bahan user (lihat alokasi)."""
    muat = int(total_detik // min_klip)
    return max(0, min(jumlah_diminta, muat - jumlah_aset_user))
