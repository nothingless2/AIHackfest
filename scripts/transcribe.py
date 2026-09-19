"""Transkripsi audio dari bahan video milik user.

Kenapa ada: sebelum ini model HANYA melihat satu frame diam per video
(`vision.py` mengambil frame di detik ke-1) dan tidak pernah mendengar apa pun.
Untuk video talking-head — orang bicara ke kamera — itu berarti hampir seluruh
isinya tidak terlihat sistem. Buktinya ada di brief yang pernah dihasilkan:
`observed_material` berbunyi "seseorang berbicara di depan kamera dengan latar
belakang dinding yang dekoratif", lalu naskahnya dikarang jadi motivasi generik.

Modul ini menutup lubang itu: audio diekstrak dengan ffmpeg (sudah jadi
dependensi render, jadi tidak ada paket baru) lalu ditranskrip lewat OpenAI.

Aturan yang dijaga:
- Video tanpa trek audio dilewati tanpa membuang kuota.
- Audio dikompres kecil (16 kHz mono) sebelum dikirim: akurasi bicara tidak
  butuh lebih, dan batas unggah API 25 MB jadi tidak pernah terancam.
- TIDAK PERNAH melempar ke pemanggil. Transkripsi adalah pengayaan, bukan
  syarat — gagal berarti brief dibuat tanpa transkrip, bukan pipeline berhenti.
"""

import os
import subprocess
import tempfile
import time

from common import OPENAI_API_KEY

TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "whisper-1")
TRANSCRIBE_ENABLED = os.getenv("TRANSCRIBE_ENABLED", "1") not in ("0", "false", "False")
# 210 detik, naik dari 60. DIUKUR, bukan ditebak: lewat RelayRouter satu klip
# 5,6 detik butuh 66,9 detik, dan 5 klip paralel selesai antara 7 dan 188 detik.
# Dengan timeout 60 detik, panggilan yang SEBENARNYA akan berhasil justru
# dibunuh lalu diulang — itulah yang membuat tahap brief mentok 255 detik tanpa
# satu pun transkrip selesai.
TRANSCRIBE_TIMEOUT = int(os.getenv("TRANSCRIBE_TIMEOUT_SECONDS", "180"))
# SATU percobaan, bukan tiga. Dua alasan, dan yang kedua lebih menentukan:
# 1. satu percobaan saja sudah bisa makan 3 menit, jadi percobaan kedua tidak
#    pernah muat di anggaran tahap;
# 2. thread yang sedang menunggu jaringan TIDAK BISA dibatalkan -- lihat catatan
#    di TRANSCRIBE_BUDGET. Jadi yang benar-benar membatasi waktu adalah timeout
#    per permintaan dikali jumlah percobaan.
TRANSCRIBE_MAX_ATTEMPTS = int(os.getenv("TRANSCRIBE_MAX_ATTEMPTS", "1"))
# Anggaran untuk SELURUH transkripsi. Lewat dari ini, sisa bahan dilepas dan
# pipeline lanjut dengan transkrip seadanya -- video dengan subtitle sebagian
# jauh lebih berguna daripada tahap yang dibunuh timeout tanpa hasil apa pun.
#
# CATATAN JUJUR: anggaran ini TIDAK bisa membunuh permintaan yang sudah berjalan.
# future.cancel() hanya mempan untuk yang belum mulai, dan ThreadPoolExecutor
# menunggu thread pekerjanya selesai saat ditutup. Jadi batas waktu yang
# SEBENARNYA adalah TRANSCRIBE_TIMEOUT x TRANSCRIBE_MAX_ATTEMPTS -- karena itu
# keduanya sengaja disetel supaya sama dengan anggaran ini, bukan lebih besar.
TRANSCRIBE_BUDGET = int(os.getenv("TRANSCRIBE_BUDGET_SECONDS", "180"))
# Permintaan dikirim bersamaan; relay mengantrekannya, tapi terukur tetap ~1,8x
# lebih cepat daripada berurutan (5 klip: 188 dtk paralel vs ~335 dtk serial).
TRANSCRIBE_CONCURRENCY = int(os.getenv("TRANSCRIBE_CONCURRENCY", "5"))
# 10, naik dari 6. Dulu 6 masuk akal karena transkripsi berjalan BERURUTAN dan
# tiap bahan menambah waktu tahap secara linier. Sekarang permintaan dikirim
# bersamaan dengan anggaran waktu, jadi batas yang terlalu ketat hanya membuat
# bahan ke-7 dan seterusnya tampil TANPA subtitle padahal sempat diproses.
TRANSCRIBE_MAX_ASSETS = int(os.getenv("TRANSCRIBE_MAX_ASSETS", "10"))
TRANSCRIBE_MAX_SECONDS = int(os.getenv("TRANSCRIBE_MAX_SECONDS", "600"))

# Kosakata yang dibiaskan ke Whisper. Bahasa Indonesia lisan penuh serapan
# Inggris, dan Whisper cenderung menuliskannya secara fonetis: "leads" jadi
# "lid", "closing" jadi "klosing". Diuji langsung pada audio user:
#   tanpa prompt : "dan ketika semua lid masuk"
#   dengan prompt: "dan ketika semua lead masuk"
# `language` TIDAK dipaksa -- diuji tidak menambah apa pun, dan memaksanya justru
# berisiko untuk bahan yang campur dua bahasa.
TRANSCRIBE_VOCAB = os.getenv(
    "TRANSCRIBE_VOCAB",
    "Istilah yang sering dipakai: leads, listing, website, spreadsheet, follow up, "
    "closing, database, CRM, marketing, konten, brand, engagement, konversi, "
    "landing page, e-commerce, digital, online, offline, target, budget.",
)
# Batas prompt Whisper ~224 token; dipotong aman jauh di bawahnya.
TRANSCRIBE_PROMPT_MAX_CHARS = 700

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".opus"}


def build_vocab_prompt(konteks=""):
    """Prompt bias kosakata: daftar istilah + permintaan user apa adanya.

    Konteks user disertakan karena ia menyebut domainnya sendiri ("UMKM",
    "properti", "skincare"), dan itu membantu Whisper memilih ejaan yang benar
    untuk istilah domain tersebut.
    """
    bagian = [TRANSCRIBE_VOCAB]
    konteks = (konteks or "").strip()
    if konteks:
        bagian.append(konteks)
    return " ".join(bagian)[:TRANSCRIBE_PROMPT_MAX_CHARS]


def _ffprobe(path, entries):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", entries,
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        return ""


def has_audio(path):
    """True kalau file punya trek audio. Tanpa ini kita membuang kuota untuk
    video bisu (screen recording, timelapse, video tanpa suara)."""
    return "audio" in _ffprobe(path, "stream=codec_type").split()


def media_duration(path):
    try:
        return float(_ffprobe(path, "format=duration") or 0)
    except ValueError:
        return 0.0


def extract_audio(path, out_path):
    """Ambil trek audio jadi MP3 16 kHz mono. Return True kalau berhasil."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", path, "-vn",
             "-ac", "1", "-ar", "16000", "-b:a", "48k", out_path],
            capture_output=True, timeout=TRANSCRIBE_TIMEOUT,
        )
        return proc.returncode == 0 and os.path.getsize(out_path) > 0
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[warn] transcribe: gagal mengekstrak audio dari {os.path.basename(path)}: {e}")
        return False


def transcribe_file(audio_path, *, durasi=0.0):
    """Transkrip satu file audio. Return teks, atau "" kalau gagal."""
    return (transcribe_file_detailed(audio_path, durasi=durasi) or {}).get("text", "")


def transcribe_file_detailed(audio_path, *, durasi=0.0, vocab_prompt=None):
    """Transkrip + potongan bertimestamp.

    Return {"text": str, "segments": [{"start": float, "end": float, "text": str}]}.

    Timestamp diminta lewat response_format="verbose_json" -- ini yang membuat
    subtitle bisa pas dengan ucapan asli user, alih-alih memakai timing karangan
    LLM yang tidak ada hubungannya dengan audio sebenarnya.
    """
    from retry import with_retry

    from common import make_openai_client, openai_is_retriable, openai_retry_after

    client = make_openai_client(timeout=TRANSCRIBE_TIMEOUT, service="TRANSCRIBE")

    def sekali():
        with open(audio_path, "rb") as f:
            return client.audio.transcriptions.create(
                model=TRANSCRIBE_MODEL, file=f, response_format="verbose_json",
                prompt=vocab_prompt if vocab_prompt is not None else build_vocab_prompt(),
                # Granularitas KATA dipakai untuk animasi teks yang muncul
                # mengikuti ucapan. Tidak menambah biaya: data ini datang dari
                # panggilan transkripsi yang sama.
                timestamp_granularities=["segment", "word"],
            )

    hasil = with_retry(
        sekali,
        is_retriable=openai_is_retriable,
        extract_retry_after=openai_retry_after,
        max_attempts=TRANSCRIBE_MAX_ATTEMPTS,
        label=f"transkripsi {TRANSCRIBE_MODEL}",
    )

    teks = (getattr(hasil, "text", "") or "").strip()

    kata = []
    for w in (getattr(hasil, "words", None) or []):
        isi = (getattr(w, "word", "") or "").strip()
        if not isi:
            continue
        kata.append({
            "start": float(getattr(w, "start", 0) or 0),
            "end": float(getattr(w, "end", 0) or 0),
            "word": isi,
        })

    potongan = []
    for seg in (getattr(hasil, "segments", None) or []):
        isi = (getattr(seg, "text", "") or "").strip()
        if not isi:
            continue
        item = {
            "start": float(getattr(seg, "start", 0) or 0),
            "end": float(getattr(seg, "end", 0) or 0),
            "text": isi,
        }
        # Metrik keyakinan bawaan Whisper, disimpan HANYA kalau ada. Dipakai
        # seleksi konten untuk menandai bagian yang ucapannya tidak jelas --
        # ambangnya juga bawaan Whisper (lihat edit_plan.py), bukan karangan.
        for kunci in ("avg_logprob", "no_speech_prob", "compression_ratio"):
            nilai = getattr(seg, kunci, None)
            if nilai is not None:
                item[kunci] = float(nilai)
        potongan.append(item)

    _catat_biaya(durasi, teks)
    return {
        "text": teks,
        "segments": potongan,
        "words": kata,
        # Bahasa DIDETEKSI Whisper, bukan diasumsikan — berguna kalau nanti
        # naskah/subtitle perlu menyesuaikan bahasa bahan.
        "language": getattr(hasil, "language", None),
    }


def alasan_gagal(exc):
    """Kode alasan singkat untuk sebuah kegagalan transkripsi.

    Dipisah dari pesan mentah karena user perlu tahu PENYEBAB yang bisa ia
    tindak lanjuti: "saldo habis" berarti isi ulang, "timeout" berarti coba
    lagi, dan keduanya jauh berbeda dari "klip itu memang tidak ada ucapannya".
    Sebelum ini semuanya sama-sama jadi baris [warn] di keluaran yang tertangkap,
    sehingga yang terlihat user hanya subtitle yang hilang.
    """
    pesan = str(exc)
    kode = str(getattr(exc, "code", "") or "") + " " + pesan
    if "insufficient_quota" in kode or "credit_balance_exhausted" in kode:
        return "kuota_habis"
    if "timed out" in pesan.lower() or type(exc).__name__ in ("APITimeoutError", "TimeoutError"):
        return "timeout"
    if type(exc).__name__ in ("InternalServerError",) or "503" in pesan or "502" in pesan:
        return "layanan_tidak_tersedia"
    if type(exc).__name__ in ("AuthenticationError", "PermissionDeniedError"):
        return "akses_ditolak"
    return "error_api"


ALASAN_TEKS = {
    "tanpa_audio": "tidak punya trek audio",
    "terlalu_panjang": "terlalu panjang untuk ditranskrip",
    "gagal_ekstrak": "audio gagal diekstrak",
    "tanpa_ucapan": "tidak ada ucapan terdeteksi",
    "kuota_habis": "saldo/kuota API habis",
    "timeout": "waktu habis (layanan lambat)",
    "layanan_tidak_tersedia": "layanan transkripsi sedang tidak tersedia",
    "akses_ditolak": "akses API ditolak",
    "error_api": "error dari layanan transkripsi",
    "tidak_selesai": "tidak selesai dalam anggaran waktu",
    "tidak_diproses": "melebihi batas jumlah bahan yang ditranskrip",
}


def _catat_biaya(durasi_detik, teks):
    """Biaya transkripsi dihitung per MENIT audio, bukan per token."""
    try:
        from cost_estimate import estimate_transcribe_cost
        from run_log import log_event

        log_event(
            "transcribe_call",
            (os.getenv("CONTENT_FACTORY_RUN_ID") or "").strip() or None,
            chat_id=(os.getenv("CONTENT_FACTORY_CHAT_ID") or "").strip() or None,
            model=TRANSCRIBE_MODEL,
            audio_seconds=round(durasi_detik, 1),
            chars=len(teks),
            cost_usd=estimate_transcribe_cost(TRANSCRIBE_MODEL, durasi_detik),
        )
    except Exception as e:
        print(f"[warn] cost: gagal mencatat transkripsi: {type(e).__name__}: {e}")


def transcribe_assets(paths, *, max_assets=None, konteks=""):
    """Transkrip semua bahan yang punya audio. Return {nama_file: teks}."""
    return {
        nama: data["text"]
        for nama, data in transcribe_assets_detailed(
            paths, max_assets=max_assets, konteks=konteks).items()
    }


def _buang(path):
    """Hapus berkas sementara tanpa pernah menggagalkan apa pun karenanya."""
    try:
        os.unlink(path)
    except OSError:
        pass


def transcribe_assets_detailed(paths, *, max_assets=None, konteks=""):
    """Seperti transcribe_assets, tapi menyertakan potongan bertimestamp.

    Return {nama_file: {"text":..., "segments":[...], "duration": float}} — hanya
    berisi yang BERHASIL dan tidak kosong. Bahan tanpa audio, gagal ekstrak, atau
    gagal transkrip tidak muncul, sehingga pemanggil tidak pernah menyangka ada
    teks padahal tidak ada.

    Pemanggil yang perlu tahu KENAPA sebuah bahan tidak muncul memakai
    transcribe_assets_report().
    """
    return transcribe_assets_report(paths, max_assets=max_assets, konteks=konteks)[0]


def transcribe_assets_report(paths, *, max_assets=None, konteks=""):
    """(hasil, gagal): `hasil` seperti transcribe_assets_detailed, `gagal` adalah
    {nama_file: kode_alasan} untuk SETIAP bahan yang tidak menghasilkan transkrip.

    Alasan dicatat karena "subtitle hilang" punya penyebab yang sangat berbeda
    dan tindak lanjutnya pun berbeda: saldo API habis (isi ulang), layanan lambat
    (coba lagi), atau klip memang tanpa ucapan (tidak ada yang perlu dilakukan).
    Lihat ALASAN_TEKS untuk kode yang mungkin.
    """
    gagal = {}
    if not TRANSCRIBE_ENABLED or not OPENAI_API_KEY:
        return {}, gagal

    batas = TRANSCRIBE_MAX_ASSETS if max_assets is None else max_assets
    vocab = build_vocab_prompt(konteks)
    hasil = {}

    # --- Tahap 1 (lokal, cepat): siapkan audio untuk tiap bahan yang layak.
    siap = []
    for path in paths[:batas]:
        nama = os.path.basename(path)
        ext = os.path.splitext(path)[1].lower()
        if ext not in VIDEO_EXTENSIONS and ext not in AUDIO_EXTENSIONS:
            continue
        if not has_audio(path):
            print(f"[info] transcribe: {nama} tidak punya trek audio, dilewati.")
            gagal[nama] = "tanpa_audio"
            continue

        durasi = media_duration(path)
        if durasi > TRANSCRIBE_MAX_SECONDS:
            print(f"[warn] transcribe: {nama} {durasi:.0f} dtk "
                  f"melebihi batas {TRANSCRIBE_MAX_SECONDS} dtk, dilewati.")
            gagal[nama] = "terlalu_panjang"
            continue

        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()
        if extract_audio(path, tmp.name):
            siap.append((path, tmp.name, durasi))
        else:
            _buang(tmp.name)
            gagal[nama] = "gagal_ekstrak"

    if not siap:
        return hasil, gagal

    # --- Tahap 2 (jaringan, lambat): semua dikirim bersamaan, dengan anggaran
    # waktu KERAS. Yang belum selesai saat anggaran habis dilepas begitu saja.
    import concurrent.futures as cf

    t0 = time.time()
    print(f"[info] transcribe: {len(siap)} bahan, anggaran {TRANSCRIBE_BUDGET} detik "
          f"({TRANSCRIBE_CONCURRENCY} permintaan bersamaan).")

    durasi_per_path = {p: d for p, _, d in siap}
    with cf.ThreadPoolExecutor(max_workers=TRANSCRIBE_CONCURRENCY) as pool:
        futures = {
            pool.submit(transcribe_file_detailed, audio, durasi=durasi, vocab_prompt=vocab): (path, audio)
            for path, audio, durasi in siap
        }
        selesai, tertunda = cf.wait(futures, timeout=TRANSCRIBE_BUDGET)

        for fut in selesai:
            path, audio = futures[fut]
            nama = os.path.basename(path)
            try:
                data = fut.result() or {}
            except Exception as e:
                kode = alasan_gagal(e)
                gagal[nama] = kode
                print(f"[warn] transcribe: {nama} gagal ({ALASAN_TEKS[kode]}): "
                      f"{type(e).__name__}: {str(e)[:200]}")
                continue
            teks = (data.get("text") or "").strip()
            if not teks:
                gagal[nama] = "tanpa_ucapan"
                continue
            hasil[nama] = {
                "text": teks,
                "segments": data.get("segments") or [],
                "words": data.get("words") or [],
                "language": data.get("language"),
                "duration": durasi_per_path.get(path, 0.0),
            }
            print(f"[info] transcribe: {nama} -> {len(teks)} karakter, "
                  f"{len(data.get('segments') or [])} potongan bertimestamp")

        if tertunda:
            # Dilaporkan terang-terangan: user berhak tahu sebagian videonya
            # tidak bersubtitle, alih-alih mengira transkripsinya memang kosong.
            print(f"[warn] transcribe: {len(tertunda)} bahan TIDAK selesai dalam "
                  f"{TRANSCRIBE_BUDGET} detik dan dilepas — video tetap dibuat, "
                  f"tapi bagian itu tanpa subtitle.")
            for fut in tertunda:
                gagal[os.path.basename(futures[fut][0])] = "tidak_selesai"
                fut.cancel()

    for _, audio, _ in siap:
        _buang(audio)

    print(f"[info] transcribe: {len(hasil)}/{len(siap)} bahan selesai "
          f"dalam {time.time() - t0:.0f} detik.")

    return hasil, gagal
