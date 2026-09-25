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

import json
import signal
import threading

from common import OPENAI_API_KEY, PROJECT_ROOT

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

# --- Penyedia transkripsi ---------------------------------------------------
# "local" = faster-whisper di mesin ini (scripts/local_whisper.py, venv sendiri).
# "api"   = Whisper lewat penyedia OpenAI-kompatibel (perilaku lama).
# "auto"  = local kalau terpasang, selain itu api (DEFAULT). Kalau local gagal
#           TOTAL (paket rusak / model tak termuat) auto jatuh ke api.
#
# Kenapa local jadi pilihan utama: tanpa transkrip agent TIDAK BISA MENDENGAR
# video -- model chat hanya melihat frame. Jalur API mati total saat saldo habis
# (403 insufficient_quota) dan, lewat relay, satu klip 5,6 dtk pernah butuh 66,9
# dtk. Lokal: 51 dtk audio diproses 39 dtk pada 4 core, tanpa saldo.
TRANSCRIBE_PROVIDER = (os.getenv("TRANSCRIBE_PROVIDER") or "auto").strip().lower()
# "small" DIUKUR, bukan ditebak: kecocokan 0,92 dengan whisper-1 pada 6 klip
# nyata, RTF 0,76. "medium" ~3x lebih lambat -- terlalu berat untuk 4 core dengan
# anggaran 180 dtk.
LOCAL_MODEL = os.getenv("TRANSCRIBE_LOCAL_MODEL") or "small"
# Kosong = bahasa dideteksi otomatis (permintaan user: auto-detect bahasa).
LOCAL_LANGUAGE = (os.getenv("TRANSCRIBE_LANGUAGE") or "").strip() or None
# 180 = sama dengan anggaran jalur API, supaya rincian timeout tahap brief (450 dtk,
# lihat orchestrator.py) tetap berlaku. 10 klip ~10 dtk diproses ~85 dtk (diukur).
LOCAL_BUDGET = int(os.getenv("TRANSCRIBE_LOCAL_BUDGET_SECONDS", "180"))
LOCAL_PYTHON = os.getenv("WHISPER_PYTHON") or os.path.join(
    PROJECT_ROOT, ".venv-whisper", "bin", "python")
LOCAL_WORKER = os.path.join(PROJECT_ROOT, "scripts", "local_whisper.py")
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
    "whisper_lokal_gagal": "Whisper lokal gagal berjalan",
}


# Segmen dengan no_speech_prob di atas ini dianggap BUKAN ucapan. Ucapan asli hampir
# selalu di bawah 0,1. Diukur pada klip suasana food court nyata (19 Sep): Whisper
# menulis "You" (0,89), "Thank you for watching!" (0,81) dan "." untuk audio
# keramaian tanpa satu pun orang bicara -- halusinasi Whisper yang terkenal.
# CATATAN: aturan bawaan Whisper (no_speech > 0,6 DAN avg_logprob < -1,0) TIDAK
# menangkapnya, karena logprob-nya -0,88 dan -0,95. Karena itu no_speech dipakai sendiri.
NO_SPEECH_MAX = float(os.getenv("TRANSCRIBE_NO_SPEECH_MAX", "0.6"))


def _ada_huruf(teks):
    return any(c.isalnum() for c in (teks or ""))


def saring_ucapan(data):
    """Buang segmen yang jelas bukan ucapan; hitung ulang teks dan kata.

    Dibuang: segmen tanpa satu pun huruf/angka (mis. "."), dan segmen dengan
    no_speech_prob > NO_SPEECH_MAX. Metrik yang tidak tersedia TIDAK dianggap
    mencurigakan -- tanpa data kita tidak menuduh apa pun.

    Sebelum ini, "." dari Whisper lolos sebagai transkrip, prompt menyebutnya
    "sumber kebenaran UTAMA", dan model brief menolak mengerjakannya.
    """
    segmen = data.get("segments") or []
    if not segmen:
        teks = (data.get("text") or "").strip()
        return data if _ada_huruf(teks) else {**data, "text": ""}

    simpan, buang = [], []
    for s in segmen:
        mencurigakan = (
            not _ada_huruf(s.get("text"))
            or (s.get("no_speech_prob") is not None and s["no_speech_prob"] > NO_SPEECH_MAX)
        )
        (buang if mencurigakan else simpan).append(s)
    if not buang:
        return data

    kata = [w for w in (data.get("words") or [])
            if not any(b["start"] - 0.05 <= w["start"] and w["end"] <= b["end"] + 0.05 for b in buang)]
    return {**data, "segments": simpan, "words": kata,
            "text": " ".join(s["text"] for s in simpan).strip()}


def local_available():
    """Worker dan python venv-nya ada? (Belum membuktikan paketnya bisa diimpor --
    itu ketahuan saat worker dijalankan, dan auto akan jatuh ke API.)"""
    return os.path.exists(LOCAL_PYTHON) and os.path.exists(LOCAL_WORKER)


def pilih_penyedia():
    """'local' atau 'api' untuk run ini, sesuai TRANSCRIBE_PROVIDER."""
    if TRANSCRIBE_PROVIDER == "api":
        return "api"
    if TRANSCRIBE_PROVIDER == "local":
        return "local"
    return "local" if local_available() else "api"


def transcribe_local(audio_paths, vocab, *, budget=None):
    """Transkripsi lokal untuk daftar berkas audio. Return (hasil, fatal).

    `hasil`: {path: data | {"error": ...}} untuk berkas yang SEMPAT selesai.
    `fatal`: teks kalau worker tidak bisa jalan sama sekali, selain itu None.

    Keluaran worker dibaca baris demi baris, jadi kalau anggaran waktu habis
    klip yang sudah selesai tetap dipakai dan hanya sisanya yang dilepas.
    Worker dijalankan di process group sendiri dan dibunuh utuh saat timeout --
    sama seperti tahap pipeline lain, supaya tidak ada proses yatim.
    """
    budget = LOCAL_BUDGET if budget is None else budget
    if not local_available():
        return {}, f"Whisper lokal belum terpasang ({LOCAL_PYTHON} tidak ada)"

    cmd = [LOCAL_PYTHON, LOCAL_WORKER, "--model", LOCAL_MODEL]
    if LOCAL_LANGUAGE:
        cmd += ["--language", LOCAL_LANGUAGE]
    if vocab:
        cmd += ["--prompt", vocab]
    cmd += list(audio_paths)

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, start_new_session=True)
    except OSError as e:
        return {}, f"worker Whisper lokal gagal dijalankan: {e}"

    hasil, fatal = {}, [None]

    def baca():
        for baris in proc.stdout:
            try:
                o = json.loads(baris)
            except ValueError:
                continue
            if o.get("tipe") == "berkas":
                hasil[o["path"]] = o.get("data") or {}
            elif o.get("tipe") == "fatal":
                fatal[0] = o.get("pesan") or "worker melaporkan kegagalan"

    pembaca = threading.Thread(target=baca, daemon=True)
    pembaca.start()
    try:
        proc.wait(timeout=budget)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()
    pembaca.join(timeout=5)

    if not hasil and fatal[0] is None and proc.returncode not in (0, None):
        fatal[0] = f"worker keluar dengan kode {proc.returncode} tanpa hasil"
    return hasil, fatal[0]


def _catat_biaya(durasi_detik, teks, *, lokal=False):
    """Biaya transkripsi dihitung per MENIT audio, bukan per token.

    `lokal=True`: biayanya 0,0 dan itu NOL YANG SEBENARNYA (tidak ada tagihan),
    bukan "tidak diketahui" -- beda dengan model API yang tidak ada di tabel harga
    (None). Dicatat tetap supaya jumlah menit audio yang diproses terlihat.
    """
    try:
        from cost_estimate import estimate_transcribe_cost
        from run_log import log_event

        model = f"local/{LOCAL_MODEL}" if lokal else TRANSCRIBE_MODEL
        log_event(
            "transcribe_call",
            (os.getenv("CONTENT_FACTORY_RUN_ID") or "").strip() or None,
            chat_id=(os.getenv("CONTENT_FACTORY_CHAT_ID") or "").strip() or None,
            model=model,
            audio_seconds=round(durasi_detik, 1),
            chars=len(teks),
            cost_usd=0.0 if lokal else estimate_transcribe_cost(TRANSCRIBE_MODEL, durasi_detik),
        )
    except Exception as e:
        print(f"[warn] cost: gagal mencatat transkripsi: {type(e).__name__}: {e}")


def _buang(path):
    """Hapus berkas sementara tanpa pernah menggagalkan apa pun karenanya."""
    try:
        os.unlink(path)
    except OSError:
        pass


def transcribe_assets_report(paths, *, max_assets=None, konteks=""):
    """(hasil, gagal): `hasil` = {nama_file: {"text", "segments", "words", "duration", ...}}
    untuk bahan yang BERHASIL dan tidak kosong; `gagal` adalah
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

    durasi_per_path = {p: d for p, _, d in siap}
    t0 = time.time()
    penyedia = pilih_penyedia()

    def terima(path, data, *, lokal):
        """Catat satu hasil. Return True kalau ada ucapan."""
        nama = os.path.basename(path)
        mentah = (data.get("text") or "").strip()
        data = saring_ucapan(data)
        teks = (data.get("text") or "").strip()
        if mentah and not teks:
            print(f"[info] transcribe: {nama} hanya berisi {mentah[:40]!r} — halusinasi pada "
                  "audio tanpa ucapan, dianggap tidak ada ucapan.")
        if not teks:
            gagal[nama] = "tanpa_ucapan"
            return False
        hasil[nama] = {
            "text": teks,
            "segments": data.get("segments") or [],
            "words": data.get("words") or [],
            "language": data.get("language"),
            "duration": durasi_per_path.get(path, 0.0),
        }
        if lokal:
            _catat_biaya(durasi_per_path.get(path, 0.0), teks, lokal=True)
        print(f"[info] transcribe: {nama} -> {len(teks)} karakter, "
              f"{len(data.get('segments') or [])} potongan bertimestamp"
              + (f" ({data.get('detik_proses')} dtk, lokal)" if lokal else ""))
        return True

    # --- Jalur LOKAL: satu proses worker, model dimuat sekali, klip berurutan.
    if penyedia == "local":
        print(f"[info] transcribe: {len(siap)} bahan, Whisper LOKAL ({LOCAL_MODEL}), "
              f"anggaran {LOCAL_BUDGET} detik.")
        peta_audio = {audio: path for path, audio, _ in siap}
        lokal, fatal = transcribe_local(list(peta_audio), build_vocab_prompt(konteks))
        if fatal and not lokal and TRANSCRIBE_PROVIDER == "auto":
            print(f"[warn] transcribe: Whisper lokal gagal ({fatal}) — mencoba API.")
            penyedia = "api"
        elif fatal and not lokal:
            print(f"[warn] transcribe: Whisper lokal gagal: {fatal}")
            for path, _, _ in siap:
                gagal[os.path.basename(path)] = "whisper_lokal_gagal"
        else:
            for audio, path in peta_audio.items():
                data = lokal.get(audio)
                nama = os.path.basename(path)
                if data is None:
                    gagal[nama] = "tidak_selesai"
                elif data.get("error"):
                    gagal[nama] = "whisper_lokal_gagal"
                    print(f"[warn] transcribe: {nama} gagal: {data['error'][:160]}")
                else:
                    terima(path, data, lokal=True)
            if any(k == "tidak_selesai" for k in gagal.values()):
                print(f"[warn] transcribe: sebagian bahan TIDAK selesai dalam "
                      f"{LOCAL_BUDGET} detik dan dilepas.")

    # --- Jalur API: semua dikirim bersamaan, dengan anggaran waktu KERAS.
    # Yang belum selesai saat anggaran habis dilepas begitu saja.
    if penyedia == "api":
        import concurrent.futures as cf

        print(f"[info] transcribe: {len(siap)} bahan, anggaran {TRANSCRIBE_BUDGET} detik "
              f"({TRANSCRIBE_CONCURRENCY} permintaan bersamaan).")

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
                terima(path, data, lokal=False)

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
