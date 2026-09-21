"""Agent 1 (TrendAnalysts) + Agent 2 (BrainIdea).

Menghasilkan trend_report.json dan creative_brief.json.
Brief-nya sekaligus menjadi input render untuk Agent 3 (judul, voice-over, scenes, media_assets).
"""

import json
import os
import sys

from audio_mode import bahan_punya_suara, mode_eksplisit, requested_mode, resolve_audio_mode
from duration import duration_text, requested_duration
from edit_plan import buat_rencana, ringkas as ringkas_edit
from spoken import SPOKEN_REWRITE, prompt_rule
from transcribe import media_duration, transcribe_assets_report
from vision import build_image_parts

from common import (
    LLM_MODEL,
    BRIEF_PATH,
    ensure_dirs,
    list_raw_assets,
    log_error,
    notify,
    now_iso,
    OPENAI_API_KEY,
    PERFORMANCE_PATH,
    chat_json,
    TREND_POOL_PATH,
    read_json,
    resolve_assets,
    resolve_chat_id,
    TREND_REPORT_PATH,
    write_json,
)

MODEL = os.getenv("BRIEF_MODEL") or LLM_MODEL


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


def build_transcript_note(transkrip):
    """Apa yang user BENAR-BENAR ucapkan di videonya.

    Ini sumber kebenaran terkuat yang kita punya — jauh di atas tebakan dari satu
    frame diam. Untuk video talking-head, frame hanya memperlihatkan wajah; isi
    sebenarnya seluruhnya ada di ucapan.
    """
    if not transkrip:
        return ("TIDAK ADA ucapan di bahan (gambar, video tanpa suara, atau hanya suara "
                "suasana/musik). Bertumpu pada apa yang TERLIHAT di gambar saja. JANGAN "
                "mengarang dialog atau klaim yang seolah-olah diucapkan siapa pun. JANGAN "
                "menyebut jenis acara, tujuan, nama, atau jumlah orang yang tidak bisa "
                "dipastikan dari gambar -- kalau ragu, pakai kata netral (mis. 'suasana ramai', "
                "'orang-orang berkumpul') dan biarkan user yang menambahkan konteksnya.")
    baris = [f'- "{teks}"' for teks in transkrip.values()]
    return (
        "UCAPAN ASLI user di dalam video (hasil transkripsi, BUKAN tebakan):\n"
        + "\n".join(baris)
        + "\n\nIni sumber kebenaran UTAMA. Kalau isinya bertentangan dengan tebakanmu "
          "atas gambar, MENANGKAN transkrip — gambar cuma memperlihatkan wajah dan latar, "
          "sedangkan transkrip memuat isi sebenarnya. DILARANG membuat konten bertema lain "
          "dari yang dibicarakan di transkrip."
    )


def build_durasi_note(durasi_bahan, bisu=False):
    """Durasi NYATA tiap bahan, supaya scene mengikuti batas antar klip.

    Tanpa ini model menulis waktu scene sesuka hati (0-5, 5-10, ...) dan teks satu
    klip terus tampil di klip berikutnya -- terlihat di video nyata: "Persiapan
    materi edukasi" masih tampil pada detik 5 padahal klip pertama selesai di 3,6.
    Angkanya diukur kode (ffprobe), bukan dikarang.
    """
    if not durasi_bahan:
        return ""
    total = sum(durasi_bahan)
    daftar = ", ".join(f"bahan {i} = {d:.1f} dtk" for i, d in enumerate(durasi_bahan, 1))
    return (
        f"DURASI NYATA TIAP BAHAN (urutan sama dengan gambar): {daftar}; total {total:.1f} dtk.\n"
        f"Video akhir berdurasi sekitar {total:.0f} dtk dan "
        + ("suara asli DIBISUKAN (yang terdengar hanya musik)" if bisu else "SUARA ASLI dipertahankan")
        + ", jadi 'full_voice_over' TIDAK dibacakan (hanya draf caption; tidak perlu 20-35 detik). "
        "Buat 'scenes' yang mengikuti batas antar bahan: satu scene per bahan, dengan "
        "start/end sesuai durasi di atas, dan scene terakhir berakhir <= total."
    )


def build_teks_statis_note():
    return (
        "TEKS ON-SCREEN STATIS: user meminta SATU teks yang sama tampil sepanjang video. Buat "
        "'scenes' berisi tepat SATU scene (start 0, end sama dengan total durasi) dengan teks "
        "singkat (maksimal 8 kata) yang menggambarkan acara atau temanya, memakai nama tempat, "
        "acara, atau brand PERSIS seperti tertulis di permintaan user. Jangan menambah klaim "
        "yang tidak disebut user."
    )


def build_prompt(asset_names, performance, *, jumlah_gambar, pool=None, konteks="",
                 transkrip=None, target_duration=None, durasi_bahan=None, teks_statis=False,
                 audio_bisu=False):
    # Durasi & aturan lafal disuntikkan, bukan hardcode: tanpa permintaan user,
    # duration_text() mengembalikan kalimat lama kata per kata sehingga brief
    # untuk run yang tidak meminta durasi tidak berubah sama sekali.
    durasi_note = duration_text(target_duration)
    lafal_note = prompt_rule()
    baris_spoken = (
        '    "voice_over_spoken": "naskah yang sama, ejaan fonetis untuk TTS",\n'
        if SPOKEN_REWRITE else ""
    )
    performance_note = build_performance_note(performance)
    trend_note, _ = build_trend_note(pool)
    transcript_note = build_transcript_note(transkrip)
    klip_note = build_durasi_note(durasi_bahan, bisu=audio_bisu)
    if teks_statis:
        klip_note = (klip_note + "\n\n" if klip_note else "") + build_teks_statis_note()
    konteks_note = (
        f'PERMINTAAN USER (apa adanya): "{konteks}"\n'
        "Ini yang user benar-benar inginkan. Judul, sudut, dan naskah WAJIB melayani\n"
        "permintaan ini. Kalau bertentangan dengan tren mana pun di daftar, MENANGKAN\n"
        "permintaan user dan set trend_index = null."
        if konteks else
        "PERMINTAAN USER: tidak ada — user hanya mengirim file tanpa menjelaskan maunya.\n"
        "Tentukan sudut dari isi gambar saja, dan jangan mengarang maksud user."
    )

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

{konteks_note}

{transcript_note}

{klip_note}

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
- Naskah voice-over harus Bahasa Indonesia, natural saat dibacakan, {durasi_note},
  berstruktur Hook - Masalah - Solusi - CTA.
{lafal_note}
- "deskripsi" ditulis untuk dibaca calon penonton di kolom deskripsi platform,
  bukan ringkasan internal. Jangan mengulang judul apa adanya.
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
    "deskripsi": "deskripsi konten 2-3 kalimat untuk kolom caption/description platform: apa isinya, untuk siapa, dan apa yang didapat penonton. Berbeda dari judul (pendek) dan dari hashtags.",
    "target_trend": "string",
    "full_voice_over": "string",
{baris_spoken}    "scenes": [
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

    performance = read_json(PERFORMANCE_PATH)

    konteks = (os.getenv("CONTENT_FACTORY_USER_CONTEXT") or "").strip()
    rinci, gagal_transkrip = transcribe_assets_report(asset_paths, konteks=konteks)
    transkrip = {nama: d["text"] for nama, d in rinci.items()}
    if transkrip:
        print(f"[info] {len(transkrip)} bahan berhasil ditranskrip — isi ucapan ikut dikirim.")

    # Mode audio diputuskan DI SINI karena di sinilah kita tahu apakah bahan
    # benar-benar berisi ucapan: transkrip yang tidak kosong adalah buktinya.
    ada_suara = None if transkrip else bahan_punya_suara(asset_paths)
    mode_audio, alasan_audio = resolve_audio_mode(
        requested_mode(), transkrip, eksplisit=mode_eksplisit(), gagal=gagal_transkrip,
        ada_suara=ada_suara)
    print(f"[info] mode audio: {mode_audio} — {alasan_audio}")

    pool = read_json(TREND_POOL_PATH, {}) or {}
    if konteks:
        print(f"[info] konteks user dipakai -> {konteks[:80]!r}")

    target_durasi, pesan_durasi = requested_duration(context=konteks)
    if target_durasi:
        print(f"[info] durasi diminta: {target_durasi} detik"
              + (f" ({pesan_durasi})" if pesan_durasi else ""))

    teks_statis = (os.getenv("CONTENT_FACTORY_STATIC_TEXT") or "").strip().lower() in ("1", "true", "ya", "on")
    durasi_bahan = None
    if mode_audio in ("original", "mute"):
        durasi_bahan = [d for d in (media_duration(p) for p in asset_paths) if d and d > 0]
    prompt_text = build_prompt(
        asset_names, performance, jumlah_gambar=len(image_parts), pool=pool,
        konteks=konteks, transkrip=transkrip, target_duration=target_durasi,
        durasi_bahan=durasi_bahan, teks_statis=teks_statis, audio_bisu=(mode_audio == "mute"),
    )
    result = chat_json(
        [{"role": "user",
          "content": [{"type": "text", "text": prompt_text}, *image_parts]}],
        model=MODEL,
        label="brief TrendAnalysts & BrainIdea",
    )

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
    brief["audio_mode"] = mode_audio
    brief["audio_mode_reason"] = alasan_audio
    # Potongan bertimestamp disimpan supaya renderer bisa membuat subtitle yang
    # pas dengan ucapan asli, bukan memakai timing karangan LLM.
    brief["transcript_segments"] = {nama: d["segments"] for nama, d in rinci.items()}
    brief["transcript_words"] = {nama: d.get("words") or [] for nama, d in rinci.items()}
    bahasa = {d.get("language") for d in rinci.values() if d.get("language")}
    brief["detected_language"] = sorted(bahasa)[0] if len(bahasa) == 1 else (sorted(bahasa) or None)
    # Berapa bahan yang benar-benar punya subtitle. Tanpa angka ini, bahan yang
    # gagal/terlewat ditranskrip tampil tanpa teks dan user tidak pernah tahu
    # bedanya dengan "bahan itu memang tidak ada ucapannya".
    brief["transcript_coverage"] = {
        "ditranskrip": len(rinci),
        "total_bahan": len(asset_names),
        "tanpa_subtitle": [n for n in asset_names if n not in rinci],
        # KENAPA tiap bahan tidak bersubtitle. Bahan yang tidak ada di transkrip
        # maupun di daftar gagal berarti tidak pernah diproses (batas jumlah).
        "alasan": {n: gagal_transkrip.get(n, "tidak_diproses")
                   for n in asset_names if n not in rinci},
    }
    brief["media_assets"] = resolve_assets(asset_names)
    brief["asset_names"] = asset_names
    brief["brief_id"] = f"brief_{now_iso()}"
    # Renderer perlu tahu targetnya untuk memutuskan apakah naskah perlu
    # dikoreksi setelah TTS. None = user tidak meminta durasi = tidak ada
    # pengecekan sama sekali (perilaku lama utuh).
    brief["target_duration"] = target_durasi
    # User meminta satu teks statis sepanjang video: renderer menyeragamkan scene-nya.
    brief["static_text"] = teks_statis
    # Disimpan untuk diagnostik: tanpa ini tidak ada cara memeriksa apakah jawaban user
    # benar-benar sampai ke pipeline (brief 19 Sep 23:36 tidak memuatnya).
    brief["konteks_user"] = konteks[:800]
    brief["generated_at"] = now_iso()

    # --- Seleksi konten: pilih & urutkan potongan ucapan terbaik. Hanya untuk mode
    # audio asli (di mode voice-over AI tidak ada ucapan user yang bisa dipilih).
    # Gagal-aman: apa pun yang tidak beres -> rencana None -> render memakai
    # perilaku lama (semua klip). Alasannya SELALU dicatat di edit_status.
    rencana_edit, status_edit = None, {"status": "dilewati", "alasan": "mode voice-over AI"}
    if mode_audio in ("original", "mute"):
        if (os.getenv("CONTENT_FACTORY_EDIT") or "auto").strip().lower() == "full":
            status_edit = {"status": "dilewati",
                           "alasan": "diminta memakai semua bahan apa adanya"}
        else:
            rencana_edit, status_edit = buat_rencana(
                asset_names, rinci, gagal_transkrip,
                {n: d.get("duration") for n, d in rinci.items()},
                konteks=konteks, judul=brief.get("judul", ""),
                sudut=trend_report.get("content_angle", ""),
                target_durasi=target_durasi,
            )
    brief["edit_plan"] = rencana_edit
    brief["edit_status"] = status_edit
    brief["edit_summary"] = ringkas_edit(rencana_edit)
    if rencana_edit:
        print(f"[info] seleksi konten: {brief['edit_summary']}")
    else:
        print(f"[info] seleksi konten dilewati: {status_edit.get('alasan')}")

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
