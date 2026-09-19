"""Pemeriksaan bahan SEBELUM diproses, supaya agent menanyakan yang benar-benar kurang.

Kenapa ada: hasil terbaik butuh konteks yang tidak bisa dilihat dari piksel. Contoh
nyata (19 Sep): tiga klip B-roll food court tanpa ucapan diminta "diedit sebagus
mungkin"; tanpa tahu acaranya, judul dan teks hanya bisa TEBAKAN ("materi edukasi",
"ratusan peserta"). Bertanya dulu lebih murah daripada merender ulang.

Alurnya dua panggilan tool biasa -- TANPA timer, penjadwal, atau proses latar
belakang. Percakapannya sepenuhnya urusan OpenClaw:

    1. content_factory_inspect  -> fakta terukur + pertanyaan yang kurang + inspect_id
    2. agent bertanya ke user, lalu BERHENTI; jawaban user = giliran baru
    3. content_factory_run(inspectId, userAnswered=true, userContext=...) -> render

Pembagian kerja (aturan #5 CLAUDE.md): KODE yang mengukur fakta dan menentukan
pertanyaan mana yang perlu; LLM hanya menyampaikannya dengan bahasanya sendiri.

Pintu masuk `run` diperiksa oleh cek_izin(): inspect_id harus ada, milik chat yang
sama, untuk bahan yang sama, belum kedaluwarsa, dan -- kalau ada pertanyaan --
sudah ditanyakan. Semua gagal-tertutup.

CLI (dipanggil plugin, JSON lewat stdin, satu objek JSON di stdout):
    python3 scripts/inspect_media.py inspect   {"paths": [...], "konteks": "", "chat_id": ""}
    python3 scripts/inspect_media.py check     {"inspect_id": "", "chat_id": "", "paths": [...],
                                                "user_answered": false}
"""

import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import STATE_DIR, write_json  # noqa: E402
from duration import parse_duration  # noqa: E402
from transcribe import LOCAL_PYTHON, LOCAL_WORKER  # noqa: E402

INSPECT_DIR = os.getenv("INSPECT_DIR") or os.path.join(STATE_DIR, "inspect")
INSPECT_TTL_HOURS = float(os.getenv("INSPECT_TTL_HOURS", "24"))
# Gerbang di jalur `run`. "0" mematikannya (mis. untuk skrip/tes).
REQUIRE_INSPECT = (os.getenv("CONTENT_FACTORY_REQUIRE_INSPECT") or "1").strip().lower() not in (
    "0", "false", "no", "off")
MAKS_PERTANYAAN = 4
VAD_TIMEOUT = 40

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm"}

# Rasio detik-berucap terhadap durasi. Diukur pada klip nyata: talking-head 0,93-0,97,
# B-roll keramaian 0-0,35 (yang 0,35 itu klip yang dihalusinasi Whisper).
RASIO_BERUCAP = 0.5
RASIO_TANPA_UCAPAN = 0.2
# Sama dengan audio_mode.AMBANG_ADA_SUARA_DB: di atas ini = ada suara, bukan hening.
AMBANG_SUARA_DB = float(os.getenv("AUDIO_ADA_SUARA_DB", "-45"))

_ID = re.compile(r"^[a-f0-9]{12}$")


# ---------------------------------------------------------------- fakta bahan

def _jalankan(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return None


def _volume_rata(path):
    o = _jalankan(["ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect", "-vn",
                   "-f", "null", "-"], timeout=60)
    if o is None:
        return None
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", o.stderr)
    return float(m.group(1)) if m else None


def _orientasi(w, h):
    if not w or not h:
        return None
    if h > w * 1.1:
        return "vertikal"
    if w > h * 1.1:
        return "horizontal"
    return "persegi"


def probe_clip(path):
    """Fakta satu bahan. Yang tidak bisa diukur bernilai None, tidak ditebak."""
    ext = os.path.splitext(path)[1].lower()
    info = {"nama": os.path.basename(path), "jenis": "gambar" if ext in IMAGE_EXT else "video",
            "ukuran_mb": None, "durasi": None, "lebar": None, "tinggi": None,
            "orientasi": None, "punya_audio": False, "volume_db": None,
            "ucapan_detik": None, "error": None}
    try:
        info["ukuran_mb"] = round(os.path.getsize(path) / 1048576, 2)
    except OSError as e:
        info["error"] = f"berkas tidak terbaca: {e}"
        return info

    o = _jalankan(["ffprobe", "-v", "error", "-print_format", "json",
                   "-show_streams", "-show_format", path])
    if o is None or o.returncode != 0:
        info["error"] = "ffprobe gagal membaca berkas"
        return info
    try:
        d = json.loads(o.stdout)
    except ValueError:
        info["error"] = "keluaran ffprobe bukan JSON"
        return info

    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and info["lebar"] is None:
            w, h = s.get("width"), s.get("height")
            rot = (s.get("tags") or {}).get("rotate")
            for sd in s.get("side_data_list") or []:
                if "rotation" in sd:
                    rot = sd["rotation"]
            try:
                if rot is not None and abs(int(float(rot))) % 180 == 90:
                    w, h = h, w      # video ponsel yang disimpan miring
            except (TypeError, ValueError):
                pass
            info["lebar"], info["tinggi"] = w, h
        elif s.get("codec_type") == "audio":
            info["punya_audio"] = True
    info["orientasi"] = _orientasi(info["lebar"], info["tinggi"])
    if info["jenis"] == "video":
        try:
            info["durasi"] = round(float(d.get("format", {}).get("duration")), 2)
        except (TypeError, ValueError):
            pass
        if info["punya_audio"]:
            info["volume_db"] = _volume_rata(path)
    return info


def ukur_ucapan(paths):
    """{path: detik_ucapan | None} lewat VAD di venv Whisper. None = tidak terukur."""
    hasil = {p: None for p in paths}
    if not (os.path.exists(LOCAL_PYTHON) and os.path.exists(LOCAL_WORKER)):
        return hasil
    try:
        proc = subprocess.run([LOCAL_PYTHON, LOCAL_WORKER, "--vad-only", *paths],
                              capture_output=True, text=True, timeout=VAD_TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return hasil
    for baris in proc.stdout.splitlines():
        try:
            o = json.loads(baris)
        except ValueError:
            continue
        if o.get("tipe") == "vad" and "ucapan" in o and o.get("path") in hasil:
            hasil[o["path"]] = float(o["ucapan"])
    return hasil


def klasifikasi(info):
    """'berucap' | 'tanpa_ucapan' | 'ragu' | 'tanpa_suara' | 'gambar' | 'tidak_terukur'."""
    if info["jenis"] == "gambar":
        return "gambar"
    if not info["punya_audio"]:
        return "tanpa_suara"
    if info["ucapan_detik"] is None or not info["durasi"]:
        return "tidak_terukur"
    r = info["ucapan_detik"] / info["durasi"]
    if r >= RASIO_BERUCAP:
        return "berucap"
    if r <= RASIO_TANPA_UCAPAN:
        return "tanpa_ucapan"
    return "ragu"


def kumpulkan_fakta(paths):
    """Daftar fakta per bahan, sudah termasuk ucapan dan klasifikasi."""
    fakta = [probe_clip(p) for p in paths]
    vad = ukur_ucapan([p for p, f in zip(paths, fakta)
                       if f["jenis"] == "video" and f["punya_audio"] and not f["error"]])
    for p, f in zip(paths, fakta):
        f["ucapan_detik"] = vad.get(p)
        f["kelas"] = klasifikasi(f)
    return fakta


def ringkas(fakta):
    """Hitungan gabungan yang dipakai penentu pertanyaan."""
    k = [f["kelas"] for f in fakta]
    return {
        "n": len(fakta),
        "n_video": sum(f["jenis"] == "video" for f in fakta),
        "n_gambar": k.count("gambar"),
        "n_berucap": k.count("berucap"),
        "n_tanpa_ucapan": k.count("tanpa_ucapan"),
        "n_ragu": k.count("ragu"),
        "n_tanpa_suara": k.count("tanpa_suara"),
        "n_horizontal": sum(f["orientasi"] == "horizontal" for f in fakta),
        "n_tidak_terukur": k.count("tidak_terukur"),
        # bersuara tapi tanpa ucapan: keramaian, musik, mesin
        "n_suasana": sum(
            f["kelas"] in ("tanpa_ucapan", "ragu") and (f["volume_db"] or -99) > AMBANG_SUARA_DB
            for f in fakta),
        "total_detik": round(sum(f["durasi"] or 0 for f in fakta), 1),
    }


# ---------------------------------------------------------- yang sudah disebut

_PLATFORM = re.compile(r"\b(tiktok|tik\s*tok|reels?|instagram|ig|shorts?|youtube|yt|feed|story|"
                       r"stories|facebook|fb|linkedin)\b", re.I)
_RASIO = re.compile(r"\b(9\s*:\s*16|1\s*:\s*1|16\s*:\s*9)\b")
_AUDIO = re.compile(r"(suara\s*ai|voice\s*-?\s*over|voiceover|narasi|dubbing|suara\s*asli|"
                    r"audio\s*asli|tanpa\s*suara|tanpa\s*voice)", re.I)
_FIT = re.compile(r"\b(blur|buram|crop|potong tengah|letterbox|hitam|penuh layar|isi penuh)\b", re.I)
_MUSIK = re.compile(r"\b(musik|lagu|backsound|bgm|music|soundtrack)\b", re.I)
_POTONG = re.compile(r"\b(buang|potong|pilih|semua|utuh|apa adanya|jangan dibuang|singkat|"
                     r"padat)\b", re.I)


def dari_konteks(konteks):
    """Apa yang SUDAH disebut user. Deteksi ringan berbasis pola, sengaja
    konservatif: lebih baik bertanya sekali lagi daripada menganggap sudah dijawab."""
    k = (konteks or "").strip()
    return {
        "kata": len(k.split()),
        "platform": bool(_PLATFORM.search(k)),
        "rasio": bool(_RASIO.search(k)),
        "rasio_nilai": (re.sub(r"\s", "", _RASIO.search(k).group(1)) if _RASIO.search(k) else None),
        "fit": bool(_FIT.search(k)),
        "durasi": parse_duration(k),
        "audio": bool(_AUDIO.search(k)),
        "musik": bool(_MUSIK.search(k)),
        "potong": bool(_POTONG.search(k)),
    }


# --------------------------------------------------------------- pertanyaan

def susun_pertanyaan(ringk, tahu):
    """Pertanyaan yang benar-benar kurang, paling penting dulu, maksimal 4.

    Setiap butir: {kode, tanya, default, alasan}. `alasan` menjelaskan KENAPA
    ditanyakan (dari fakta terukur), supaya agent bisa menyampaikannya jujur.
    """
    q = []
    nv = ringk["n_video"]
    tanpa_ucapan_semua = nv > 0 and ringk["n_berucap"] == 0
    hanya_gambar = nv == 0 and ringk["n_gambar"] > 0

    if tanpa_ucapan_semua or hanya_gambar:
        q.append({
            "kode": "topik",
            "tanya": ("Bahan ini tidak berisi ucapan, jadi saya tidak tahu ceritanya. Ini tentang "
                      "apa dan untuk apa (mis. dokumentasi acara, promosi tempat, tips)? Sebutkan "
                      "nama tempat, brand, atau acara yang harus tertulis persis."),
            "default": "tanpa konteks, teks hanya menggambarkan apa yang terlihat, secara netral",
            "alasan": "tidak ada ucapan di bahan, judul dan teks hanya bisa ditebak dari gambar",
        })
    elif tahu["kata"] < 10:
        q.append({
            "kode": "tujuan",
            "tanya": ("Tujuan videonya apa (promosi, edukasi, personal branding, lainnya), dan "
                      "adakah ajakan/CTA di akhir? Ada nama, brand, atau istilah yang harus "
                      "tertulis persis?"),
            "default": "disimpulkan dari ucapan; ejaan istilah mengikuti transkrip",
            "alasan": "permintaan singkat; ejaan nama dan istilah sering salah bila tidak diberi tahu",
        })

    if not (tahu["platform"] or tahu["rasio"]) and tahu["durasi"] is None:
        total = min(ringk["total_detik"], 60) or None
        q.append({
            "kode": "platform_durasi",
            "tanya": ("Untuk platform apa dan seberapa panjang? TikTok/Reels/Shorts (9:16), feed "
                      "Instagram (1:1), atau YouTube (16:9); durasi 10-60 detik."),
            "default": "9:16, panjang mengikuti bahan"
                       + (f" (sekitar {total:.0f} dtk)" if total else ""),
            "alasan": "rasio dan durasi belum disebut",
        })

    # Sumber HORIZONTAL ke kanvas vertikal: pilihan potong-tengah / blur / hitam sangat
    # menentukan hasil, dan terlihat jelas dari fakta terukur (bukan tebakan).
    target_vertikal = tahu.get("rasio_nilai") in (None, "9:16")
    if (nv > 0 and ringk.get("n_horizontal", 0) * 2 >= nv and target_vertikal
            and not tahu.get("fit")):
        q.append({
            "kode": "fit",
            "tanya": (f"{ringk['n_horizontal']} dari {nv} video berbentuk horizontal, sedangkan "
                      "TikTok/Reels vertikal. Mau dipotong di tengah supaya memenuhi layar "
                      "(sisi kiri-kanan terbuang), seluruh gambar tetap tampil dengan latar "
                      "blur, atau dengan latar hitam?"),
            "default": "dipotong di tengah, memenuhi layar",
            "alasan": "sumber horizontal ke kanvas vertikal: sebagian gambar pasti terbuang atau "
                      "ada ruang kosong",
        })

    if not (tahu["audio"] or tahu["musik"]):
        if tanpa_ucapan_semua and ringk["n_suasana"] > 0:
            q.append({
                "kode": "audio",
                "tanya": ("Videonya berisi suara suasana, bukan ucapan. Mau tetap pakai suara "
                          "suasana + musik latar ringan, tambahkan voice-over AI yang membacakan "
                          "narasi, atau hanya teks?"),
                "default": "suara suasana dipertahankan + musik latar ringan (bila tersedia)",
                "alasan": "ada suara tapi bukan ucapan",
            })
        elif hanya_gambar or (nv > 0 and ringk["n_tanpa_suara"] == nv):
            q.append({
                "kode": "audio",
                "tanya": ("Bahannya tidak bersuara. Mau musik latar saja atau voice-over AI? "
                          "Kalau voice-over, gayanya: ramah, profesional, energik, tenang, atau "
                          "bercerita?"),
                "default": "musik latar saja (bila tersedia), tanpa voice-over",
                "alasan": "bahan tanpa suara sama sekali",
            })

    if ringk["n_berucap"] >= 4 and not tahu["potong"]:
        q.append({
            "kode": "potong",
            "tanya": ("Ada beberapa video berucapan. Boleh saya pilih bagian terbaik dan buang "
                      "take ulang atau bagian tidak jelas, atau semuanya dipakai utuh? Ada "
                      "bagian yang wajib dipertahankan?"),
            "default": "dipilih bagian terbaik, take ulang dan bagian tidak jelas dibuang",
            "alasan": f"{ringk['n_berucap']} video berucapan; pemilihan otomatis bisa membuang "
                      "bagian yang penting bagimu",
        })
    return q[:MAKS_PERTANYAAN]


# -------------------------------------------------------------------- teks

def _fmt_klip(i, f):
    if f["error"]:
        return f"- Bahan {i}: TIDAK TERBACA ({f['error']})"
    if f["jenis"] == "gambar":
        return f"- Bahan {i}: gambar {f['lebar']}x{f['tinggi']}"
    bagian = [f"{f['durasi']:.1f} dtk" if f["durasi"] else "durasi ?", f["orientasi"] or "?"]
    if not f["punya_audio"]:
        bagian.append("tanpa suara")
    else:
        if f["volume_db"] is not None:
            bagian.append(f"volume {f['volume_db']:.0f} dB")
        if f["ucapan_detik"] is not None and f["durasi"]:
            bagian.append(f"ucapan ±{f['ucapan_detik'] / f['durasi'] * 100:.0f}%")
    label = {"berucap": "BERUCAP", "tanpa_ucapan": "tanpa ucapan", "ragu": "ucapan tidak pasti",
             "tanpa_suara": "tanpa suara", "tidak_terukur": "ucapan tidak terukur"}.get(f["kelas"], "")
    return f"- Bahan {i}: " + ", ".join(bagian) + (f"  → {label}" if label else "")


def susun_teks(fakta, pertanyaan, tahu, ringk, *, inspect_id, paths):
    """Teks yang dibaca AGENT. Fakta dan pertanyaan berasal dari kode; agent hanya
    menyampaikannya dengan bahasanya sendiri."""
    baris = ["PEMERIKSAAN BAHAN (angka terukur, bukan tebakan):"]
    baris += [_fmt_klip(i, f) for i, f in enumerate(fakta, 1)]
    baris.append(f"Total {ringk['n_video']} video / {ringk['n_gambar']} gambar, "
                 f"{ringk['total_detik']:.1f} dtk video.")
    if ringk["n_video"] and ringk["n_berucap"] == 0 and ringk["n_tidak_terukur"] == 0:
        baris.append("Tidak ada video yang berisi ucapan: tidak ada yang bisa ditranskrip, jadi isi "
                     "cerita TIDAK diketahui dari bahan itu sendiri.")

    sudah = [n for n, ada in (("platform", tahu["platform"]), ("rasio", tahu["rasio"]),
                              ("durasi", tahu["durasi"] is not None), ("audio", tahu["audio"]),
                              ("musik", tahu["musik"])) if ada]
    baris.append("Sudah disebut user: " + (", ".join(sudah) if sudah else "belum ada yang spesifik") + ".")
    baris.append("DEFAULT bila user tidak menjawab: rasio 9:16, suara asli dipertahankan (bukan "
                 "voice-over AI), subtitle karaoke bila ada ucapan, musik latar bila tersedia, "
                 "durasi mengikuti bahan (maks 60 dtk).")

    paths_json = json.dumps(paths, ensure_ascii=False)
    baris.append("")
    if pertanyaan:
        baris.append("PERTANYAAN yang perlu ditanyakan ke user "
                     f"({len(pertanyaan)}; sampaikan dalam SATU pesan singkat, tiap butir dengan "
                     "default-nya, dan izinkan jawaban 'terserah'):")
        for i, p in enumerate(pertanyaan, 1):
            baris.append(f"{i}. {p['tanya']}  [default: {p['default']}]  (alasan: {p['alasan']})")
        baris += [
            "",
            "ATURAN LANJUTAN:",
            "- Tanyakan SEKARANG. Pakai tool ask_user kalau tersedia: satu pemanggilan per "
            "pertanyaan, default-nya jadi opsi pertama berlabel '(Recommended)', dan jawabannya "
            "kembali di giliran ini sehingga kamu bisa langsung lanjut. Kalau ask_user tidak "
            "ada, tulis satu pesan singkat lalu BERHENTI dan tunggu jawaban; JANGAN memanggil "
            "content_factory_run pada giliran itu.",
            "- Setelah user menjawab (atau bilang 'terserah'/'langsung saja'), panggil "
            "content_factory_run dengan:",
            f"    mediaPaths = {paths_json}",
            f"    inspectId = {inspect_id}",
            "    userAnswered = true",
            "    userContext = permintaan awal + jawaban user, dengan kata-kata user apa adanya "
            "(nama, brand, istilah harus persis)",
            "    serta parameter yang jawabannya menyebut (aspectRatio, durationSeconds, "
            "audioMode, music, editMode, ...).",
            "- Jangan menambah pertanyaan di luar daftar ini kecuali jawabannya membingungkan.",
        ]
    else:
        baris += [
            "Tidak ada yang perlu ditanyakan: permintaan user sudah cukup lengkap. Langsung "
            "panggil content_factory_run dengan:",
            f"    mediaPaths = {paths_json}",
            f"    inspectId = {inspect_id}",
        ]
    return "\n".join(baris)


# ---------------------------------------------------------- status & gerbang

def _sekarang():
    return datetime.now(timezone.utc)


def sidik_bahan(paths):
    """Sidik jari himpunan bahan: nama berkas + ukuran, terurut."""
    hasil = []
    for p in paths:
        try:
            hasil.append(f"{os.path.basename(p)}:{os.path.getsize(p)}")
        except OSError:
            hasil.append(f"{os.path.basename(p)}:?")
    return sorted(hasil)


def _path_state(inspect_id):
    if not _ID.match(str(inspect_id or "")):
        return None
    return os.path.join(INSPECT_DIR, f"{inspect_id}.json")


def inspeksi(paths, konteks="", chat_id=""):
    """Periksa bahan, simpan status, kembalikan dict siap dikirim ke plugin."""
    if not chat_id:
        return {"ok": False, "kode": "chat_tidak_diketahui",
                "teks": "Chat pemicu tidak diketahui, pemeriksaan dibatalkan."}
    inspect_id = secrets.token_hex(6)
    degraded = None
    try:
        fakta = kumpulkan_fakta(paths)
        ringk = ringkas(fakta)
        tahu = dari_konteks(konteks)
        pertanyaan = susun_pertanyaan(ringk, tahu)
    except Exception as e:  # noqa: BLE001
        # Pemeriksaan boleh gagal tanpa menahan user: dua pertanyaan generik cukup.
        degraded = f"{type(e).__name__}: {e}"
        fakta, ringk, tahu = [], {"n": len(paths), "n_video": 0, "n_gambar": 0, "total_detik": 0,
                                  "n_berucap": 0}, dari_konteks(konteks)
        pertanyaan = [
            {"kode": "tujuan", "tanya": "Ini tentang apa dan untuk apa? Ada nama/brand yang harus "
                                        "tertulis persis?", "default": "disimpulkan dari bahan",
             "alasan": "pemeriksaan otomatis gagal"},
            {"kode": "platform_durasi", "tanya": "Untuk platform apa dan seberapa panjang?",
             "default": "9:16, mengikuti bahan", "alasan": "pemeriksaan otomatis gagal"},
        ]

    status = {
        "inspect_id": inspect_id, "chat_id": str(chat_id), "dibuat": _sekarang().isoformat(),
        "bahan": sidik_bahan(paths), "pertanyaan": [p["kode"] for p in pertanyaan],
        "degraded": degraded, "konteks_awal": (konteks or "")[:500],
    }
    write_json(_path_state(inspect_id), status)
    teks = susun_teks(fakta, pertanyaan, tahu, ringk, inspect_id=inspect_id, paths=paths) \
        if not degraded else _teks_degraded(pertanyaan, inspect_id, paths)
    return {"ok": True, "inspect_id": inspect_id, "pertanyaan": pertanyaan,
            "ringkasan": ringk, "degraded": degraded, "teks": teks}


def _teks_degraded(pertanyaan, inspect_id, paths):
    baris = ["Pemeriksaan otomatis bahan gagal; tanyakan ke user:"]
    baris += [f"{i}. {p['tanya']}" for i, p in enumerate(pertanyaan, 1)]
    baris += ["", "Setelah user menjawab, panggil content_factory_run dengan "
              f"mediaPaths = {json.dumps(paths, ensure_ascii=False)}, inspectId = {inspect_id}, "
              "userAnswered = true, dan userContext berisi jawaban user."]
    return "\n".join(baris)


def cek_izin(inspect_id, chat_id, paths, user_answered=False):
    """Gerbang di jalur `run`. Return {"ok": bool, "kode": str, "teks": str}.

    Gagal-tertutup: apa pun yang tidak bisa dibuktikan menghasilkan penolakan
    dengan langkah berikutnya yang jelas, bukan render diam-diam.
    """
    if not REQUIRE_INSPECT:
        return {"ok": True, "kode": "dinonaktifkan", "teks": ""}

    def tolak(kode, teks):
        return {"ok": False, "kode": kode, "teks": teks}

    if not inspect_id:
        return tolak("wajib_inspect",
                     "Belum ada pemeriksaan bahan. Panggil content_factory_inspect dengan "
                     "mediaPaths yang sama TERLEBIH DAHULU, tanyakan ke user pertanyaan yang "
                     "diminta, lalu panggil content_factory_run lagi dengan inspectId dari hasil "
                     "itu.")
    p = _path_state(inspect_id)
    if p is None or not os.path.exists(p):
        return tolak("tidak_ditemukan", "inspectId tidak dikenal atau sudah dihapus. Panggil "
                                        "content_factory_inspect lagi untuk bahan ini.")
    try:
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return tolak("tidak_ditemukan", "Status pemeriksaan rusak. Panggil content_factory_inspect lagi.")

    if str(st.get("chat_id")) != str(chat_id):
        return tolak("milik_chat_lain", "inspectId ini bukan milik chat ini. Panggil "
                                        "content_factory_inspect untuk bahan yang diupload di sini.")
    try:
        umur_jam = (_sekarang() - datetime.fromisoformat(st["dibuat"])).total_seconds() / 3600
    except (KeyError, ValueError):
        umur_jam = float("inf")
    if umur_jam > INSPECT_TTL_HOURS:
        return tolak("kedaluwarsa", f"Pemeriksaan sudah lebih dari {INSPECT_TTL_HOURS:.0f} jam. "
                                    "Panggil content_factory_inspect lagi.")
    if sorted(st.get("bahan") or []) != sidik_bahan(paths):
        return tolak("bahan_berbeda", "Bahan yang dikirim tidak sama dengan yang diperiksa. "
                                      "Pakai mediaPaths PERSIS seperti pada hasil pemeriksaan.")
    if st.get("pertanyaan") and not user_answered:
        return tolak("belum_ditanyakan",
                     "Pemeriksaan menemukan hal yang perlu ditanyakan ke user "
                     f"({', '.join(st['pertanyaan'])}). Tanyakan dulu dan tunggu jawabannya, lalu "
                     "panggil lagi dengan userAnswered = true dan userContext berisi jawaban user.")
    return {"ok": True, "kode": "ok", "teks": ""}


# ---------------------------------------------------------------------- CLI

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("inspect", "check"):
        print(json.dumps({"ok": False, "kode": "penggunaan",
                          "teks": "pakai: inspect_media.py inspect|check (JSON di stdin)"}))
        return 2
    try:
        masuk = json.load(sys.stdin)
    except ValueError:
        print(json.dumps({"ok": False, "kode": "masukan_rusak", "teks": "stdin bukan JSON"}))
        return 2
    if argv[0] == "inspect":
        hasil = inspeksi(masuk.get("paths") or [], masuk.get("konteks") or "",
                         str(masuk.get("chat_id") or ""))
    else:
        hasil = cek_izin(masuk.get("inspect_id") or "", str(masuk.get("chat_id") or ""),
                         masuk.get("paths") or [], bool(masuk.get("user_answered")))
    print(json.dumps(hasil, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
