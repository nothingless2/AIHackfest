"""Fondasi bersama semua agent: path, state, logging, LLM, persona log."""

import json
import os
import traceback
from datetime import datetime, timezone

from dotenv import load_dotenv

# --- Root proyek dihitung dari lokasi file ini, bukan dari cwd pemanggil.
# Ini penting: pipeline bisa dipanggil dari mana saja (agent Hermes, CLI, test)
# tanpa path menjadi salah arah.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

RAW_DIR = os.path.join(PROJECT_ROOT, "workspace", "raw")
DRAFTS_DIR = os.path.join(PROJECT_ROOT, "workspace", "drafts")
STATE_DIR = os.path.join(PROJECT_ROOT, "workspace", "state")
PUBLISHED_DIR = os.path.join(PROJECT_ROOT, "workspace", "published")  # dipakai mulai 1b

BRIEF_PATH = os.path.join(STATE_DIR, "creative_brief.json")
TREND_REPORT_PATH = os.path.join(STATE_DIR, "trend_report.json")
RENDER_STATUS_PATH = os.path.join(STATE_DIR, "render_status.json")
PUBLISH_HISTORY_PATH = os.path.join(STATE_DIR, "publish_history.json")
PERFORMANCE_PATH = os.path.join(STATE_DIR, "performance_summary.json")
TREND_POOL_PATH = os.path.join(STATE_DIR, "trend_pool.json")
ERROR_LOG_PATH = os.path.join(STATE_DIR, "error.log")
RUN_LOG_PATH = os.path.join(STATE_DIR, "run_log.jsonl")

DRAFT_VIDEO_PATH = os.path.join(DRAFTS_DIR, "video_output.mp4")


def draft_video_path_for_run(run_id):
    """Path video hasil akhir milik satu run spesifik — dibaca oleh apa pun
    yang berjalan SETELAH lock render dilepas (hermes_render membaca hasilnya),
    supaya tidak pernah membaca file yang bisa tertimpa run berikutnya.
    Fallback ke path tetap kalau run_id kosong (pemakaian standalone lama)."""
    if not run_id:
        return DRAFT_VIDEO_PATH
    return os.path.join(DRAFTS_DIR, f"video_{run_id}.mp4")


DRAFT_THUMB_PATH = os.path.splitext(DRAFT_VIDEO_PATH)[0] + ".jpg"


def draft_thumb_path_for_run(run_id):
    """Padanan draft_video_path_for_run() untuk cover JPG.

    Sengaja berbagi awalan nama dengan videonya (`video_{run_id}.*`) supaya satu
    pola retensi menyapu keduanya dan cover tidak jadi file yatim.
    """
    if not run_id:
        return DRAFT_THUMB_PATH
    return os.path.join(DRAFTS_DIR, f"video_{run_id}.jpg")


def brief_path_for_run(run_id):
    """Padanan draft_video_path_for_run() untuk creative_brief.json."""
    if not run_id:
        return BRIEF_PATH
    return os.path.join(STATE_DIR, f"creative_brief_{run_id}.json")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Label chat run ini untuk LOG (CONTENT_FACTORY_CHAT_ID), atau None. Bukan tujuan kirim:
# tidak ada kode di repo ini yang mengirim ke chat. TIDAK ADA fallback ke nilai tetap di .env
# -- versi lama pernah mengirim hasil run siapa pun ke satu chat tetap.
def resolve_chat_id():
    """Label chat run ini, atau None."""
    return (os.getenv("CONTENT_FACTORY_CHAT_ID") or "").strip() or None


# Label persona di log tiap tahap.
PERSONA = {
    "trendanalysts": "🔍 TrendAnalysts",
    "brainidea": "💡 BrainIdea",
    "contentmakers": "✍️ ContentMakers",
    "contentinsight": "📊 ContentInsight",
}

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp",
    ".mp4", ".mov", ".avi", ".mkv",
}


def ensure_dirs():
    for path in (RAW_DIR, DRAFTS_DIR, STATE_DIR, PUBLISHED_DIR):
        os.makedirs(path, exist_ok=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def log_error(context, exc):
    """Catat error lengkap ke error.log tanpa menghentikan proses pemanggil."""
    ensure_dirs()
    entry = f"[{now_iso()}] {context}: {exc}\n{traceback.format_exc()}\n"
    with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry)


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, payload):
    """Tulis JSON secara ATOMIK: ke berkas sementara lalu os.replace.

    Versi lama membuka `path` langsung dengan mode "w" -- yang memotong berkas jadi
    kosong SEBELUM isinya ditulis. Pembaca (atau penulis kedua) yang datang di
    antara keduanya melihat berkas kosong/setengah jadi. os.replace atomik di
    filesystem yang sama, jadi pembaca selalu melihat versi lama utuh atau baru utuh.
    """
    ensure_dirs()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    sementara = f"{path}.{os.getpid()}.tmp"
    try:
        with open(sementara, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4, ensure_ascii=False)
        os.replace(sementara, path)
    finally:
        if os.path.exists(sementara):
            os.remove(sementara)


def notify(agent_key, message, *, chat_id=None):
    """Cetak pesan berlabel persona ke log tahap. TIDAK mengirim ke mana pun: di jalur Hermes
    agent yang menyampaikan hasil ke chat (hermes_render.py). Pengiriman langsung ke Telegram
    dihapus bersama jalur OpenClaw -- satu-satunya pengisi chat tujuannya.
    `chat_id` dipertahankan agar pemanggil lama tetap jalan; diabaikan."""
    print(f"{PERSONA.get(agent_key, agent_key)}: {message}")
    return False


def list_raw_assets():
    """Semua bahan mentah yang benar-benar ada di workspace/raw/."""
    ensure_dirs()
    return sorted(
        name
        for name in os.listdir(RAW_DIR)
        if os.path.splitext(name)[1].lower() in MEDIA_EXTENSIONS
    )


def resolve_assets(names):
    """Ubah nama file jadi path absolut + pastikan semuanya benar-benar ada.

    Zero Hallucination on Assets: kalau ada nama yang tidak ada di disk, gagal
    terang-terangan alih-alih diam-diam melanjutkan dengan bahan karangan.

    Nama WAJIB berupa nama file polos di dalam RAW_DIR. Pemisah path dan '..'
    ditolak, dan hasil akhirnya diperiksa ulang dengan realpath supaya symlink
    pun tidak bisa menunjuk keluar folder. Tanpa ini, satu nama berisi '../'
    cukup untuk membaca file di luar workspace.
    """
    bad = [
        n for n in names
        if not n
        or os.path.basename(n) != n
        or n in (".", "..")
        or os.path.isabs(n)
    ]
    if bad:
        raise ValueError(f"Nama bahan mentah tidak valid (harus nama file polos): {bad}")

    raw_root = os.path.realpath(RAW_DIR)
    paths = [os.path.join(RAW_DIR, name) for name in names]

    escaped = [
        n for n, p in zip(names, paths)
        if os.path.commonpath([raw_root, os.path.realpath(p)]) != raw_root
    ]
    if escaped:
        raise ValueError(f"Bahan mentah menunjuk keluar {RAW_DIR}: {escaped}")

    missing = [n for n, p in zip(names, paths) if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(f"Bahan mentah tidak ada di {RAW_DIR}: {missing}")
    return paths

# ---------------------------------------------------------------- OpenAI

# Timeout per percobaan. Default SDK adalah read=600 detik -- satu panggilan yang
# menggantung akan menahan tahapnya selama 10 menit dan baru dibunuh paksa oleh
# timeout orchestrator. 45 detik jauh di atas latensi normal GPT-4o (termasuk
# dengan gambar), tapi cukup pendek untuk gagal cepat dan mencoba lagi.
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "45"))

# Endpoint alternatif yang kompatibel-OpenAI (mis. relay/proxy). Kosong = resmi.
# Disimpan di .env, TIDAK di-hardcode, supaya tidak ikut ter-commit dan bisa
# diganti tanpa menyentuh kode.
OPENAI_BASE_URL = (os.getenv("OPENAI_BASE_URL") or "").strip() or None

# Satu nama model untuk SEMUA panggilan chat (brief, seleksi, koreksi durasi,
# kata kunci tren). Dulu "gpt-4o" tertulis mati di empat tempat, jadi pindah
# penyedia berarti mengedit kode. Tiap pemanggil masih bisa menimpanya sendiri
# (BRIEF_MODEL, EDIT_MODEL, KEYWORD_MODEL, DURATION_FIX_MODEL).
LLM_MODEL = (os.getenv("LLM_MODEL") or "").strip() or "gpt-4o"


def make_openai_client(*, timeout=None, service=None):
    """Satu-satunya tempat client OpenAI dibuat.

    Dipusatkan supaya base_url, mematikan retry bawaan SDK, dan timeout tidak
    perlu diulang di tiap pemanggil — dan supaya pindah penyedia cukup mengubah
    satu variabel .env.

    `service` ("TRANSCRIBE" atau "TTS") memungkinkan layanan itu memakai penyedia
    SENDIRI lewat {SERVICE}_BASE_URL / {SERVICE}_API_KEY. Perlu karena tidak
    semua penyedia menyediakan semuanya: Snifox hanya punya model chat, tanpa
    Whisper dan tanpa TTS, sehingga chat bisa ke satu penyedia sementara
    transkripsi tetap ke penyedia lain. Kosong = pakai penyedia utama.
    """
    from openai import OpenAI

    kunci, basis = OPENAI_API_KEY, OPENAI_BASE_URL
    if service:
        kunci = (os.getenv(f"{service}_API_KEY") or "").strip() or kunci
        basis = (os.getenv(f"{service}_BASE_URL") or "").strip() or basis
    kw = {
        "api_key": kunci,
        # Retry bawaan SDK dimatikan; retry kita sendiri yang mengatur jeda dan
        # klasifikasi. Tanpa ini, retry bersarang jadi 9 percobaan.
        "max_retries": 0,
        "timeout": timeout if timeout is not None else OPENAI_TIMEOUT_SECONDS,
    }
    if basis:
        kw["base_url"] = basis
    return OpenAI(**kw)


def openai_is_retriable(exc):
    """Kegagalan sementara vs permanen.

    URUTAN PENTING: AuthenticationError/BadRequestError dsb adalah TURUNAN dari
    APIStatusError, jadi kelas permanen harus diperiksa SEBELUM aturan 5xx --
    kalau dibalik, kunci API yang salah akan diulang tiga kali sia-sia.
    """
    import openai

    # Saldo habis datang sebagai RateLimitError (429) juga, TAPI tidak akan pernah
    # membaik dengan menunggu. Tanpa pengecualian ini, tiap panggilan membuang
    # ~6 detik mencoba ulang 3x dan penyebab sebenarnya baru terlihat di ujung.
    # Ditemukan saat kredit user benar-benar habis, bukan dari membaca dokumentasi.
    kode = str(getattr(exc, "code", "") or "")
    tipe = str(getattr(getattr(exc, "body", None), "get", lambda *_: "")("type") or "")
    pesan = str(exc)
    if ("insufficient_quota" in (kode, tipe)
            or "credit_balance_exhausted" in kode
            or "insufficient_quota" in pesan
            or "credit_balance_exhausted" in pesan):
        return False

    if isinstance(exc, (openai.RateLimitError, openai.InternalServerError,
                        openai.APIConnectionError)):
        return True  # APITimeoutError turunan APIConnectionError, ikut tercakup
    if isinstance(exc, (openai.AuthenticationError, openai.BadRequestError,
                        openai.PermissionDeniedError, openai.NotFoundError,
                        openai.UnprocessableEntityError)):
        return False
    if isinstance(exc, openai.APIStatusError):
        return (getattr(exc, "status_code", 0) or 0) >= 500
    return False


def openai_retry_after(exc):
    """Detik yang diminta server lewat header Retry-After, kalau ada."""
    resp = getattr(exc, "response", None)
    nilai = getattr(resp, "headers", {}).get("Retry-After") if resp is not None else None
    try:
        return float(nilai) if nilai else None
    except (TypeError, ValueError):
        return None


def parse_json_lenient(teks):
    """JSON dari jawaban LLM, toleran terhadap pagar kode.

    `response_format=json_object` dihormati OpenAI, tapi tidak oleh semua model
    di balik gateway kompatibel-OpenAI: terukur Claude Haiku dan Gemini
    membungkus jawabannya dengan ```json ... ```, yang membuat json.loads gagal
    padahal isinya benar. Yang dilonggarkan hanya pembungkusnya -- isi yang
    memang bukan JSON tetap melempar.
    """
    import json as _j
    import re as _re

    mentah = (teks or "").strip()
    try:
        return _j.loads(mentah)
    except _j.JSONDecodeError:
        pass
    pagar = _re.search(r"```(?:json)?\s*(.*?)```", mentah, _re.DOTALL | _re.IGNORECASE)
    if pagar:
        return _j.loads(pagar.group(1).strip())
    awal, akhir = mentah.find("{"), mentah.rfind("}")
    if awal != -1 and akhir > awal:
        return _j.loads(mentah[awal:akhir + 1])
    # Sertakan cuplikan jawabannya: pesan lama ("line 1 column 1 (char 0)") tidak
    # memberi tahu apa-apa, padahal jawabannya ternyata PENOLAKAN model yang
    # menjelaskan persis apa yang kurang ("there is no audio transcription...").
    cuplikan = " ".join(mentah.split())[:180] or "(kosong)"
    raise _j.JSONDecodeError(f"jawaban model bukan JSON — isinya: {cuplikan!r}", mentah, 0)


def chat_json(messages, *, model, label="panggilan LLM", max_attempts=None, timeout=None):
    """Panggil chat completion yang mengembalikan JSON, dengan retry.

    max_retries=0 mematikan retry BAWAAN SDK (defaultnya 2). Tanpa itu, retry
    bersarang: SDK mencoba 3x di dalam tiap percobaan kita, jadi total 9 kali
    dengan jeda yang tidak kita kendalikan dan melewati timeout tahap.

    `max_attempts`/`timeout` dipakai pemanggil yang anggaran waktunya sempit
    (mis. koreksi durasi di tengah render, yang harus 1 percobaan singkat dan
    boleh gagal tanpa menggagalkan render).
    """
    from retry import with_retry

    client = make_openai_client(timeout=timeout)

    def sekali(m=model, pesan=messages):
        resp = client.chat.completions.create(
            model=m, messages=pesan,
            response_format={"type": "json_object"},
        )
        _catat_pemakaian_llm(m, resp, label)
        return parse_json_lenient(resp.choices[0].message.content)

    rantai = rantai_model(model)
    if len(rantai) == 1:
        kw = {} if max_attempts is None else {"max_attempts": max_attempts}
        return with_retry(
            sekali,
            is_retriable=openai_is_retriable,
            extract_retry_after=openai_retry_after,
            label=label,
            **kw,
        )
    return _chat_rantai(sekali, rantai, messages, label=label,
                        putaran=max_attempts if max_attempts is not None else PUTARAN_RANTAI)


PUTARAN_RANTAI = int(os.getenv("LLM_FALLBACK_ROUNDS", "2"))
STATUS_TIDAK_TERSEDIA = {404, 429, 502, 503}


def rantai_model(model):
    """Model utama + cadangan (LLM_FALLBACK, dipisah koma), tanpa duplikat. Asal: 25 Sep model
    gratis utama (nex-n2.5-mini) DICABUT dari OpenRouter ("No endpoints found") dan model gratis
    lain bergantian dibatasi antrean bersama (429) -- satu model saja tidak bisa diandalkan."""
    cadangan = [m.strip() for m in (os.getenv("LLM_FALLBACK") or "").split(",") if m.strip()]
    return list(dict.fromkeys([model] + cadangan))


def model_tidak_tersedia(exc):
    """Model tidak bisa melayani SEKARANG (dicabut, antrean penuh, penyedia down) -> coba model
    lain. Kuota/kredit habis dan kunci salah BUKAN kasus ini (tidak akan membaik di model lain)."""
    pesan = str(exc)
    if "insufficient_quota" in pesan or "credit_balance_exhausted" in pesan:
        return False
    status = getattr(exc, "status_code", None)
    if status in STATUS_TIDAK_TERSEDIA:
        return True
    import openai
    return isinstance(exc, (openai.APIConnectionError, openai.InternalServerError))


def _tanpa_gambar(messages):
    hasil = []
    for m in messages:
        isi = m.get("content")
        if isinstance(isi, list):
            isi = [x for x in isi if not (isinstance(x, dict) and x.get("type") == "image_url")]
        hasil.append({**m, "content": isi})
    return hasil


def _ada_gambar(messages):
    return any(isinstance(m.get("content"), list) and any(
        isinstance(x, dict) and x.get("type") == "image_url" for x in m["content"]) for m in messages)


# Panggilan chat_json terakhir: model yang menjawab & apakah gambar terpaksa dibuang. Dibaca
# pemanggil (brief) untuk JUJUR bahwa pemahaman dibuat tanpa melihat video.
PANGGILAN_TERAKHIR = {}

CATATAN_TANPA_GAMBAR = (
    "\n\nCATATAN SISTEM: gambar bahan TIDAK bisa dikirim ke model ini -- kamu TIDAK melihat "
    "videonya. DILARANG mendeskripsikan isi visual (orang, tempat, kegiatan) yang tidak disebut "
    "di transkrip, fakta per bahan, atau permintaan user. Untuk pemahaman per bahan tulis hanya "
    "yang diketahui dari ucapan/fakta; kalau tidak ada, tulis \"(tidak terlihat oleh model)\".")


def _tanpa_gambar_dengan_catatan(messages):
    hasil = _tanpa_gambar(messages)
    for i in range(len(hasil) - 1, -1, -1):
        if hasil[i].get("role") == "user":
            isi = hasil[i]["content"]
            if isinstance(isi, list):
                isi = [dict(x) for x in isi]
                teks = [x for x in isi if x.get("type") == "text"]
                if teks:
                    teks[-1]["text"] = teks[-1]["text"] + CATATAN_TANPA_GAMBAR
            else:
                isi = str(isi) + CATATAN_TANPA_GAMBAR
            hasil[i] = {**hasil[i], "content": isi}
            break
    return hasil


def _chat_rantai(sekali, rantai, messages, *, label, putaran):
    """Coba tiap model berurutan; model yang tidak tersedia dilewati ke cadangan berikutnya.

    Pesan BERGAMBAR: model yang menolak gambar dicatat dan baru dipakai PALING AKHIR (setelah
    semua putaran model bergambar gagal), dengan gambar dibuang + catatan bahwa model tidak
    melihat video. Terukur 25 Sep: model teks-saja langsung dipakai di putaran pertama lalu
    mengarang "petugas medis memeriksa calon pendonor" untuk video suasana food court.
    Galat lain (kunci salah, kuota) langsung naik."""
    import time
    PANGGILAN_TERAKHIR.clear()
    terakhir, teks_saja = None, []
    bergambar = _ada_gambar(messages)
    for p in range(max(1, putaran)):
        for m in rantai:
            if m in teks_saja:
                continue
            try:
                hasil = sekali(m, messages)
                PANGGILAN_TERAKHIR.update(model=m, tanpa_gambar=False)
                return hasil
            except Exception as e:
                terakhir = e
                if bergambar and getattr(e, "status_code", None) == 404 and "image" in str(e).lower():
                    teks_saja.append(m)
                    print(f"[warn] {label}: {m} tidak menerima gambar -- disimpan sebagai cadangan terakhir.")
                    continue
                if not model_tidak_tersedia(e):
                    raise
                print(f"[warn] {label}: model {m} tidak tersedia ({getattr(e, 'status_code', type(e).__name__)})"
                      " -- mencoba model cadangan.")
        if p < putaran - 1:
            time.sleep(3)
    for m in teks_saja:
        try:
            print(f"[warn] {label}: semua model bergambar tidak tersedia -- {m} TANPA gambar.")
            hasil = sekali(m, _tanpa_gambar_dengan_catatan(messages))
            PANGGILAN_TERAKHIR.update(model=m, tanpa_gambar=True)
            return hasil
        except Exception as e:
            terakhir = e
            if not model_tidak_tersedia(e):
                raise
    raise terakhir


def _catat_pemakaian_llm(model, resp, label):
    """Catat pemakaian token NYATA dari response.usage.

    Dicatat per PANGGILAN, bukan per run: percobaan yang gagal lalu diulang tetap
    menghabiskan token di sisi server kalau sempat diproses, dan satu run punya
    lebih dari satu panggilan LLM.

    Seluruh badan fungsi ini dibungkus try/except: pelacakan biaya adalah
    pengamatan dan TIDAK BOLEH menggagalkan pipeline.
    """
    try:
        from cost_estimate import estimate_llm_cost
        from run_log import log_event  # impor tertunda: run_log mengimpor common

        usage = getattr(resp, "usage", None)
        masuk = getattr(usage, "prompt_tokens", None)
        keluar = getattr(usage, "completion_tokens", None)
        log_event(
            "llm_call",
            (os.getenv("CONTENT_FACTORY_RUN_ID") or "").strip() or None,
            chat_id=resolve_chat_id(),
            label=label,
            model=model,
            prompt_tokens=masuk,
            completion_tokens=keluar,
            total_tokens=getattr(usage, "total_tokens", None),
            cost_usd=estimate_llm_cost(model, masuk, keluar),
        )
    except Exception as e:
        print(f"[warn] cost: gagal mencatat pemakaian LLM: {type(e).__name__}: {e}")
