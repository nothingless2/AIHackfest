"""Agent 1 (TrendAnalysts) + Agent 2 (BrainIdea).

Menghasilkan trend_report.json dan creative_brief.json.
Brief-nya sekaligus menjadi input render untuk Agent 3 (judul, voice-over, scenes, media_assets).
"""

import json
import os
import sys

from openai import OpenAI

from common import (
    BRIEF_PATH,
    OPENAI_API_KEY,
    PERFORMANCE_PATH,
    TREND_REPORT_PATH,
    ensure_dirs,
    list_raw_assets,
    log_error,
    notify,
    now_iso,
    read_json,
    resolve_assets,
    write_json,
)

MODEL = "gpt-4o"


def select_assets():
    """Daftar bahan untuk run ini: dari env CONTENT_FACTORY_ASSETS kalau ada,
    kalau tidak pakai semua yang ada di workspace/raw/."""
    forced = os.getenv("CONTENT_FACTORY_ASSETS", "").strip()
    available = list_raw_assets()

    if forced:
        names = [n.strip() for n in forced.split(",") if n.strip()]
    else:
        names = available

    if not names:
        raise ValueError(
            "Belum ada bahan mentah di workspace/raw/. "
            "Minta user mengupload foto/video dulu sebelum menjalankan pipeline."
        )

    resolve_assets(names)  # memvalidasi semuanya benar-benar ada
    return names


def build_prompt(asset_names, performance):
    performance_note = (
        json.dumps(performance, ensure_ascii=False, indent=2)
        if performance
        else "Belum ada data performa post sebelumnya."
    )

    return f"""
Bertindaklah sebagai dua agent sekaligus:
- Agent 1 TRENDANALYSTS: riset tren dan sentimen publik.
- Agent 2 BRAINIDEA: ubah tren itu jadi satu konsep konten vertikal + naskah.

Data performa konten sebelumnya (untuk closed-loop, pertimbangkan apa yang berhasil):
{performance_note}

Bahan mentah yang WAJIB dipakai (foto/video milik user, urutan boleh disusun ulang):
{json.dumps(asset_names, ensure_ascii=False)}

Aturan keras:
- Hanya boleh memakai nama file dari daftar di atas. DILARANG mengarang nama file lain.
- Naskah voice-over harus Bahasa Indonesia, natural saat dibacakan, 20-35 detik
  (kira-kira 55-95 kata), berstruktur Hook - Masalah - Solusi - CTA.
- "scenes" adalah teks on-screen singkat (maksimal 6 kata per scene), bukan salinan
  penuh voice-over. Waktu mulai/selesai tiap scene harus berurutan dan tidak tumpang tindih.

Balas HANYA JSON murni dengan struktur persis berikut:
{{
  "trend_report": {{
    "timestamp": "{now_iso()}",
    "top_trend_topic": "string",
    "recommended_hook_template": "string",
    "format_style": "string",
    "confidence_score": 0.0,
    "public_sentiment": "string"
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

    asset_names = select_assets()
    notify("trendanalysts", f"menganalisis tren untuk {len(asset_names)} bahan mentah...")

    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY belum diisi di .env")

    client = OpenAI(api_key=OPENAI_API_KEY)
    performance = read_json(PERFORMANCE_PATH)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": build_prompt(asset_names, performance)}],
        response_format={"type": "json_object"},
    )
    result = json.loads(response.choices[0].message.content)

    trend_report = result["trend_report"]
    brief = result["creative_brief"]

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
        f"({len(brief.get('scenes', []))} scene, tren: {trend_report.get('top_trend_topic')})",
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as e:
        log_error("Agent 1&2 (brief) failure", e)
        notify("trendanalysts", f"gagal menyusun brief — {e}")
        sys.exit(1)
