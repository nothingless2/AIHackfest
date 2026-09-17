"""Agent 1 (TrendAnalysts) + Agent 2 (BrainIdea).

Menghasilkan trend_report.json dan creative_brief.json.
Brief-nya sekaligus menjadi input render untuk Agent 3 (judul, voice-over, scenes, media_assets).
"""

import json
import os
import sys

from openai import OpenAI
from vision import build_image_parts

from common import (
    BRIEF_PATH,
    ensure_dirs,
    list_raw_assets,
    log_error,
    notify,
    now_iso,
    OPENAI_API_KEY,
    PERFORMANCE_PATH,
    TREND_POOL_PATH,
    read_json,
    resolve_assets,
    resolve_chat_id,
    TREND_REPORT_PATH,
    write_json,
)

MODEL = "gpt-4o"


def select_assets():
    """Daftar bahan untuk run ini.

    workspace/raw/ dipakai bersama oleh SEMUA run dan SEMUA user, dan isinya tidak
    pernah dihapus. Jadi "pakai semua isi folder" bukan default yang aman: run milik
    user A bisa ikut menyertakan foto milik user B.

    Aturannya sekarang bergantung pada asal run:

    - Run dari plugin/Telegram (CONTENT_FACTORY_RUN_PREFIX terisi): WAJIB menyebut
      bahannya lewat CONTENT_FACTORY_ASSETS, dan setiap nama wajib berawalan
      "{prefix}_" -- yaitu file yang disalin plugin untuk run ini sendiri. Daftar
      kosong berarti GAGAL, bukan jatuh ke seluruh isi folder.

    - Run CLI manual (tanpa prefix): perilaku lama dipertahankan -- pakai
      CONTENT_FACTORY_ASSETS kalau ada, kalau tidak seluruh isi workspace/raw/.
      Di jalur ini operator sendiri yang menaruh file, jadi tidak ada percampuran
      antar-user, tapi tetap diberi peringatan.
    """
    forced = os.getenv("CONTENT_FACTORY_ASSETS", "").strip()
    run_prefix = (os.getenv("CONTENT_FACTORY_RUN_PREFIX") or "").strip()
    names = [n.strip() for n in forced.split(",") if n.strip()] if forced else []

    if run_prefix:
        if not names:
            raise ValueError(
                "Run dari plugin tidak menyebutkan bahan (CONTENT_FACTORY_ASSETS kosong). "
                "Dibatalkan — TIDAK jatuh ke seluruh isi workspace/raw/, karena folder itu "
                "berisi bahan milik run dan user lain."
            )
        asing = [n for n in names if not n.startswith(f"{run_prefix}_")]
        if asing:
            raise ValueError(
                f"Bahan berikut bukan milik run ini (prefix wajib '{run_prefix}_'): {asing}. "
                "Dibatalkan untuk mencegah bahan milik user lain ikut terpakai."
            )
    elif not names:
        names = list_raw_assets()
        if names:
            print(
                f"[warn] Run CLI tanpa CONTENT_FACTORY_ASSETS — memakai SEMUA "
                f"{len(names)} bahan di workspace/raw/. Pastikan isinya memang milik run ini."
            )

    if not names:
        raise ValueError(
            "Belum ada bahan mentah di workspace/raw/. "
            "Minta user mengupload foto/video dulu sebelum menjalankan pipeline."
        )

    resolve_assets(names)  # memvalidasi nama aman + semuanya benar-benar ada
    return names


def build_performance_note(performance):
    """Ringkasan performa untuk prompt -- hanya kalau datanya NYATA.

    Agent 5 kini menulis data_source="NO_DATA" ketika belum ada angka asli.
    Dict itu tetap truthy, jadi pengecekan harus eksplisit: tanpa ini, catatan
    "tidak ada data" ikut terkirim sebagai kalau-kalau ada isinya, dan LLM akan
    memperlakukannya sebagai sinyal.
    """
    if not performance or performance.get("data_source") != "REAL_API":
        alasan = (performance or {}).get("reason", "belum ada data performa asli")
        return (
            f"TIDAK ADA data performa asli ({alasan}). "
            "JANGAN mengarang angka, klaim performa, atau menyebut konten sebelumnya berhasil."
        )
    return json.dumps(performance, ensure_ascii=False, indent=2)


def build_trend_note(pool):
    """Kolam tren NYATA sebagai daftar bernomor. LLM hanya boleh memilih nomor."""
    items = (pool or {}).get("items") or []
    if not items:
        return ("TIDAK ADA data tren yang berhasil diambil. Set trend_index = null "
                "dan tentukan sudut konten murni dari isi gambar."), []
    baris = []
    for i, it in enumerate(items):
        metric = f" [{it['metric']}]" if it.get("metric") else ""
        konteks = ""
        if it.get("context"):
            konteks = " | berita: " + "; ".join(it["context"][:2])
        baris.append(f"{i}. ({it['source']}) {it['term']}{metric}{konteks}")
    return "\n".join(baris), items


def pilih_tren(pool, trend_index):
    """Ambil item tren dari kolam berdasarkan pilihan LLM.

    Ini penegak anti-halusinasinya: LLM hanya mengembalikan NOMOR, dan seluruh
    isi trend_report diambil dari item yang benar-benar difetch. Nomor di luar
    jangkauan atau bukan angka diperlakukan sebagai "tidak memilih" -- bukan
    error, karena menolak memang jawaban yang sah.
    """
    items = (pool or {}).get("items") or []
    if trend_index is None or not items:
        return None
    try:
        idx = int(trend_index)
    except (TypeError, ValueError):
        print(f"[warn] trend_index bukan angka ({trend_index!r}) — dianggap tidak memilih.")
        return None
    if not 0 <= idx < len(items):
        print(f"[warn] trend_index {idx} di luar jangkauan 0..{len(items)-1} — dianggap tidak memilih.")
        return None
    return items[idx]


def build_prompt(asset_names, performance, *, jumlah_gambar, pool=None):
    performance_note = build_performance_note(performance)
    trend_note, _ = build_trend_note(pool)

    bagian_visual = (
        f"Kamu DIBERI {jumlah_gambar} gambar/frame dari bahan mentah user di pesan ini. "
        "LIHAT gambar-gambar itu. Seluruh konsep, judul, dan naskah WAJIB berakar pada "
        "apa yang benar-benar terlihat di sana."
        if jumlah_gambar
        else
        "PERINGATAN: tidak ada gambar yang bisa diproses dari bahan user, jadi kamu TIDAK "
        "tahu isinya. Buat naskah yang sangat umum dan aman, dan JANGAN menyebut objek, "
        "tempat, merek, atau aktivitas spesifik apa pun."
    )

    return f"""
Bertindaklah sebagai dua agent sekaligus:
- Agent 1 TRENDANALYSTS: amati bahan user, tentukan sudut konten yang masuk akal.
- Agent 2 BRAINIDEA: ubah sudut itu jadi satu konsep konten vertikal + naskah.

{bagian_visual}

BATAS PENGETAHUANMU (penting, jangan dilanggar):
- Kamu TIDAK punya akses internet dan TIDAK tahu tren yang sedang ramai saat ini.
- DILARANG mengarang statistik, jumlah view, "sedang viral", "menurut riset",
  nama tren terkini, atau sentimen publik seolah-olah kamu mengukurnya.
- "content_angle" adalah usulanmu berdasarkan isi bahan, BUKAN hasil riset tren.

Data performa konten sebelumnya:
{performance_note}

DAFTAR TREN NYATA (hasil pengambilan data, bukan ingatanmu):
{trend_note}

Aturan memilih tren:
- Kamu HANYA boleh memilih dari daftar bernomor di atas, lewat "trend_index".
- Kalau TIDAK ADA yang benar-benar berhubungan dengan isi gambar, set
  "trend_index": null. Memaksakan tren yang tidak nyambung JAUH lebih buruk
  daripada tidak memakai tren sama sekali.
- DILARANG menulis nama tren lain di luar daftar itu.

Nama file bahan (urutan boleh disusun ulang; gambar di atas berurutan sesuai daftar ini):
{json.dumps(asset_names, ensure_ascii=False)}

Aturan keras:
- Hanya boleh memakai nama file dari daftar di atas. DILARANG mengarang nama file lain.
- DILARANG menyebut objek, orang, tempat, atau aktivitas yang TIDAK terlihat di gambar.
- Naskah voice-over harus Bahasa Indonesia, natural saat dibacakan, 20-35 detik
  (kira-kira 55-95 kata), berstruktur Hook - Masalah - Solusi - CTA.
- "scenes" adalah teks on-screen singkat (maksimal 6 kata per scene), bukan salinan
  penuh voice-over. Waktu mulai/selesai tiap scene harus berurutan dan tidak tumpang tindih.

Balas HANYA JSON murni dengan struktur persis berikut:
{{
  "trend_report": {{
    "observed_material": "deskripsi FAKTUAL apa yang terlihat di gambar, 1-2 kalimat",
    "trend_index": 0,
    "trend_reason": "kenapa tren itu nyambung dengan gambar; kosongkan kalau null",
    "content_angle": "string",
    "recommended_hook_template": "string",
    "format_style": "string"
  }},
  "creative_brief": {{
    "judul": "string",
    "target_trend": "string",
    "full_voice_over": "string",
    "scenes": [
      {{"start": 0, "end": 4, "text": "teks on-screen singkat"}}
    ],
    "hashtags": ["#contoh"]
  }}
}}
""".strip()


def run():
    ensure_dirs()

    chat_id = resolve_chat_id()
    asset_names = select_assets()
    asset_paths = resolve_assets(asset_names)

    notify(
        "trendanalysts",
        f"mengamati {len(asset_names)} bahan mentah...",
        chat_id=chat_id,
    )

    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY belum diisi di .env")

    # Kirim ISI bahan, bukan cuma nama file. Tanpa ini model tidak pernah melihat
    # apa pun dan hanya menebak dari UUID di nama file.
    image_parts = build_image_parts(asset_paths)
    if not image_parts:
        print("[warn] Tidak ada gambar yang bisa diproses — brief akan dibuat tanpa melihat bahan.")
    else:
        print(f"[info] {len(image_parts)} gambar/frame dikirim ke {MODEL} untuk diamati.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    performance = read_json(PERFORMANCE_PATH)

    pool = read_json(TREND_POOL_PATH, {}) or {}
    prompt_text = build_prompt(
        asset_names, performance, jumlah_gambar=len(image_parts), pool=pool
    )
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{
            "role": "user",
            "content": [{"type": "text", "text": prompt_text}, *image_parts],
        }],
        response_format={"type": "json_object"},
    )
    result = json.loads(response.choices[0].message.content)

    trend_report = result["trend_report"]
    brief = result["creative_brief"]

    # Penegakan: seluruh FAKTA tren diambil dari item yang benar-benar difetch,
    # bukan dari teks LLM. LLM hanya menyumbang nomor pilihan + alasannya.
    terpilih = pilih_tren(pool, trend_report.get("trend_index"))
    trend_report.pop("trend_index", None)
    trend_report["timestamp"] = now_iso()
    if terpilih:
        trend_report["trend_source"] = terpilih["source"]
        trend_report["trend_term"] = terpilih["term"]
        trend_report["trend_metric"] = terpilih.get("metric")
        trend_report["trend_url"] = terpilih.get("url")
        trend_report["trend_query"] = terpilih.get("query")
        print(f"[info] tren dipakai: ({terpilih['source']}) {terpilih['term'][:60]}")
    else:
        trend_report["trend_source"] = "TIDAK_ADA_TREN_RELEVAN"
        trend_report["trend_term"] = None
        trend_report["trend_metric"] = None
        trend_report["trend_url"] = None
        trend_report["trend_query"] = None
        trend_report.pop("trend_reason", None)
        print("[info] tidak ada tren relevan — sudut konten murni dari isi bahan.")
    trend_report["pool_size"] = len((pool or {}).get("items") or [])
    trend_report["pool_sources"] = (pool or {}).get("sources_ok") or []

    # Zero Hallucination on Assets: daftar bahan ditentukan Python, bukan LLM.
    brief["media_assets"] = resolve_assets(asset_names)
    brief["asset_names"] = asset_names
    brief["brief_id"] = f"brief_{now_iso()}"
    brief["generated_at"] = now_iso()

    if not brief.get("full_voice_over"):
        raise ValueError("LLM tidak menghasilkan 'full_voice_over'; brief tidak dapat dipakai.")

    write_json(TREND_REPORT_PATH, trend_report)
    write_json(BRIEF_PATH, brief)

    notify(
        "brainidea",
        f"konsep siap: \"{brief.get('judul', 'Untitled')}\" "
        f"({len(brief.get('scenes', []))} scene, sudut: {trend_report.get('content_angle')})",
        chat_id=chat_id,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as e:
        log_error("Agent 1&2 (brief) failure", e)
        notify("trendanalysts", f"gagal menyusun brief — {e}", chat_id=resolve_chat_id())
        sys.exit(1)
