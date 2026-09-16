# Arsitektur & Alur Pipeline

Lihat [STATUS.md](STATUS.md) untuk daftar fitur yang sudah/belum berfungsi.

## Gambaran besar: 2 lapis + 1 jembatan

```
┌──────────────────────────────────────────────────────────────┐
│  LAPIS 1: OpenClaw Gateway (produk pihak lain, sudah dipakai)│
│  - Menangani koneksi Telegram, autentikasi, LLM reasoning    │
│  - Agent "main" (model openai/gpt-5.6-sol) baca chat user    │
│  - Memilih tool yang tepat berdasarkan permintaan user        │
└───────────────────────┬────────────────────────────────────────┘
                         │ satu titik sambung:
                         │ tool "content_factory_run"
┌───────────────────────▼────────────────────────────────────────┐
│  JEMBATAN: openclaw-plugin/ (TypeScript, plugin resmi OpenClaw)│
│  - Terima path file dari OpenClaw                              │
│  - Copy ke workspace/raw/, validasi projectRoot & format        │
│  - Spawn proses Python DETACHED, balas ke chat dalam <1 detik  │
└───────────────────────┬────────────────────────────────────────┘
                         │ subprocess.spawn(detached: true)
┌───────────────────────▼────────────────────────────────────────┐
│  LAPIS 2: Pipeline 5-Agent (Python murni, tidak tahu OpenClaw) │
│  - Baca/tulis file JSON sebagai state antar-tahap               │
│  - Kirim progress & hasil ke Telegram lewat HTTP langsung       │
└──────────────────────────────────────────────────────────────┘
```

**Kenapa dipisah begini?** Render video butuh 2-4 menit, jauh melebihi batas
waktu eksekusi tool OpenClaw (~90 detik). Kalau pipeline dijalankan di dalam
eksekusi tool dan ditunggu, prosesnya dibunuh di tengah jalan — video jadi
rusak. Solusinya: plugin cuma menyalakan proses lalu lepas tangan
(`detached: true`, `child.unref()`); proses Python itu sendiri yang mengirim
hasil ke Telegram lewat Bot API, sepenuhnya independen dari siklus hidup tool.

## Alur selangkah demi selangkah

```
User upload foto/video + teks permintaan
        │
        ▼
[OpenClaw] Model baca chat, putuskan panggil content_factory_run(mediaPaths=[...])
        │
        ▼
[Plugin] Validasi projectRoot & format file → copy ke workspace/raw/
         → spawn scripts/run_and_deliver.py (detached) → balas "sedang diproses"
        │
        ▼  (proses Python berjalan independen mulai di sini)
┌────────────────────────────────────────────────────────────────┐
│ [Fase 0] 📊 ContentInsight  (agent5_insight.py)   — non-kritis  │
│ [Fase 1] 🔍 TrendAnalysts + 💡 BrainIdea  (agent1_2_brief.py)   │
│ [Fase 2] ✍️ ContentMakers  (agent3_render.py → auto_render.py) │
│ [Fase 3] ✅ ApprovalPost  (kirim ke Telegram, dari run_and_deliver.py)│
└────────────────────────────────────────────────────────────────┘
        │
        ▼
Video + caption terkirim ke chat asal via Bot API langsung
```

`pipeline.py` (dijalankan lewat CLI) memakai urutan tahap yang sama, tapi
memanggil `agent4_approval.py` di Fase 3 (dengan polling `getUpdates` untuk
APPROVE/REVISI) — bukan `run_and_deliver.py`. **Dua jalur ini punya perilaku
approval yang berbeda**, lihat bagian "Dua jalur eksekusi" di bawah.

## Input → Proses → Output per komponen

### 1. Plugin (`openclaw-plugin/src/index.ts`)

| | Detail |
|---|---|
| **Input** | `mediaPaths: string[]` (path lokal, disuntik OpenClaw dari attachment chat), `toolContext.nativeChannelId` (ID chat asal), `api.pluginConfig.projectRoot` (config plugin) |
| **Proses** | 1) Validasi `projectRoot` (harus absolut, `scripts/pipeline.py` harus ada). 2) Validasi tiap `mediaPaths` ada & ekstensinya didukung. 3) Copy tiap file ke `workspace/raw/<prefix-toolCallId>_<nama-asli>` (prefix mencegah tabrakan nama antar-run). 4) Spawn `run_and_deliver.py` sebagai proses detached, teruskan `CONTENT_FACTORY_ASSETS` (nama file yang baru dicopy) dan `CONTENT_FACTORY_CHAT_ID` (chat asal) lewat environment variable. |
| **Output** | Balasan tool **synchronous** (<1 detik): teks "pipeline dimulai" + `pid` proses. **Tidak** menunggu render selesai. |

### 2. `common.py` — fondasi bersama

Bukan sebuah tahap, tapi dipakai semua tahap:
- **Path**: dihitung dari `os.path.abspath(__file__)`, bukan `cwd` — supaya aman dipanggil dari proses mana pun (plugin, CLI, cron).
- **State**: `read_json`/`write_json` ke `workspace/state/*.json`.
- **`notify(agent_key, message)`**: cetak ke stdout + kirim ke Telegram (kecuali `CONTENT_FACTORY_QUIET` diset).
- **`resolve_assets(names)`**: ubah nama file jadi path absolut, **gagal eksplisit** kalau ada yang tidak ada di disk (Zero Hallucination on Assets).
- **`TELEGRAM_CHAT_ID`**: `CONTENT_FACTORY_CHAT_ID` (dari plugin) diutamakan atas `.env` (dipakai CLI).

### 3. Fase 0 — 📊 ContentInsight (`agent5_insight.py`)

| | Detail |
|---|---|
| **Input** | `workspace/state/publish_history.json` (riwayat publish sebelumnya, kalau ada) |
| **Proses** | Cari post terakhir berstatus `PUBLISHED`. Coba `fetch_real_analytics()` (**stub**, selalu `None`) → fallback ke estimasi acak (`views`, `likes`, `comments`, `shares` dalam rentang wajar, **tidak selalu positif**). Klasifikasi `HIGH`/`MODERATE`/`LOW_PERFORMING` dari `engagement_rate`. |
| **Output** | `workspace/state/performance_summary.json` — jadi konteks untuk Fase 1. Non-kritis: kalau gagal, pipeline tetap lanjut. |

### 4. Fase 1 — 🔍 TrendAnalysts + 💡 BrainIdea (`agent1_2_brief.py`)

| | Detail |
|---|---|
| **Input** | `CONTENT_FACTORY_ASSETS` (env, daftar nama file dari plugin) atau semua file di `workspace/raw/` kalau env kosong (jalur CLI). `performance_summary.json` dari Fase 0. |
| **Proses** | 1) `select_assets()`: validasi semua nama file ada di `workspace/raw/`. 2) Bangun prompt teks (**cuma nama file**, bukan isi gambar — lihat gap di STATUS.md) + data performa, kirim ke **GPT-4o** (`response_format: json_object`). 3) Model kembalikan `trend_report` + `creative_brief` (judul, naskah voice-over, daftar scene bertimestamp, hashtag). 4) **Paksa** `media_assets`/`asset_names` dari daftar Python asli (bukan dari output LLM) — Zero Hallucination on Assets. |
| **Output** | `workspace/state/trend_report.json`, `workspace/state/creative_brief.json`. Kritis: gagal → pipeline berhenti. |

### 5. Fase 2 — ✍️ ContentMakers (`agent3_render.py` → `auto_render.py`)

| | Detail |
|---|---|
| **Input** | `creative_brief.json` (naskah, scene, `media_assets` — path absolut ke bahan mentah) |
| **Proses** | Lihat detail di bawah ("Render engine"). Ringkas: TTS → hitung durasi total → tiap bahan diproses jadi segmen 9:16 lewat ffmpeg → digabung (hard-cut) → teks per-scene dibakar (`drawtext`) → audio voice-over dimux. |
| **Output** | `workspace/drafts/video_output.mp4`, `workspace/state/render_status.json` (`SUCCESS`/`FAILED`). Kritis: gagal → pipeline berhenti, file rusak (kalau ada) tidak terkirim. |

#### Render engine (`skills/video_generator/auto_render.py`) — detail

```
full_voice_over (teks) ──► edge-tts ──► temp_vo.mp3 ──► durasi total (detik)
                                                              │
media_assets[] ──► per-asset: ffmpeg scale+crop+loop/trim ──► segmen_0.mp4, segmen_1.mp4, ...
                    (durasi tiap segmen = durasi_total / jumlah_aset)
                                                              │
                                          ffmpeg concat (hard-cut) ──► _combined_silent.mp4
                                                              │
scenes[] ──────────────────────► ffmpeg drawtext berantai ──► _combined_text.mp4
                                                              │
                                          ffmpeg mux (+ temp_vo.mp3) ──► video_output.mp4
```

Poin desain penting:
- **Gambar** → `ffmpeg -loop 1 -i foto.jpg -t <durasi>` (statis, ditampilkan sepanjang durasi slotnya).
- **Video** → `ffmpeg -stream_loop -1 -i klip.mp4 -t <durasi>` (diulang kalau lebih pendek dari slot, dipotong kalau lebih panjang).
- Semua segmen di-scale+crop ke `1080x1920` lewat filter `scale=...,crop=...` (isi kanvas tanpa distorsi, crop tengah).
- Penggabungan pakai **hard-cut** (`ffmpeg concat demuxer`), bukan crossfade — crossfade (`compose`) terbukti 11x lebih lambat saat diprofilkan.
- Teks dibakar via filter `drawtext` berantai, satu per scene, dengan `enable='between(t,start,end)'` supaya tiap teks cuma tampil di rentang waktunya.
- Posisi teks: `y = min(SAFE_TOP_MARGIN_PX, TARGET_H - SAFE_BOTTOM_MARGIN_PX - tinggi_teks)` — menjauhi zona UI atas/bawah platform (Reels/TikTok/Shorts biasanya menutup ~300px dari tiap tepi).
- moviepy **hanya** dipakai untuk `AudioFileClip(...).duration` (baca durasi file audio) — operasi ringan yang tidak melalui pipeline per-frame.

### 6. Fase 3 — ✅ ApprovalPost

**Ada 2 implementasi berbeda** untuk fase ini — lihat "Dua jalur eksekusi" di bawah.

## Dua jalur eksekusi — penting untuk dipahami

| | Jalur Plugin (Telegram, dipakai sekarang) | Jalur CLI (`pipeline.py`) |
|---|---|---|
| Dipicu oleh | Plugin OpenClaw → `run_and_deliver.py` | `python3 scripts/pipeline.py` manual |
| Fase 3 dieksekusi oleh | Kode di dalam `run_and_deliver.py` sendiri (kirim video + caption, **tidak menunggu balasan**) | `agent4_approval.py` (kirim video + **polling `getUpdates` sampai 10 menit** menunggu APPROVE/REVISI) |
| Menangkap balasan APPROVE/REVISI? | **Tidak** — gap yang tercatat di STATUS.md | Ya, lalu tulis `publish_history.json` |
| Risiko | Approval tidak benar-benar ditegakkan (tapi juga tidak ada auto-publish, jadi aman) | Kalau dijalankan bersamaan dengan gateway OpenClaw yang jalan, **rebutan `getUpdates`** karena token bot sama (`agent4_approval.py` mendeteksi & memperingatkan ini otomatis) |

## Zero Hallucination on Assets — bagaimana ditegakkan di kode

Prinsip: LLM tidak pernah dipercaya untuk menentukan file mana yang "dipakai" —
Python yang memutuskan, LLM cuma diberi tahu hasilnya.

1. `select_assets()` di `agent1_2_brief.py` menyusun daftar nama file **sebelum** memanggil LLM, dan memvalidasi semuanya ada di `workspace/raw/`.
2. Daftar itu dikirim ke LLM sebagai instruksi ("bahan yang harus dipakai: [...]").
3. Setelah LLM membalas, `brief["media_assets"]` **ditimpa paksa** dengan daftar asli dari langkah 1 — bukan dipercaya dari output LLM.
4. `common.resolve_assets()` dipanggil lagi di `agent3_render.py` sebelum render — kalau ada file yang hilang di antara Fase 1 dan Fase 2, gagal eksplisit dengan `FileNotFoundError`, bukan diam-diam lanjut dengan bahan yang salah.

## State yang mengalir antar-tahap

```
workspace/raw/*                        (input: bahan mentah user)
workspace/state/performance_summary.json   (Fase 0 → konteks Fase 1)
workspace/state/trend_report.json          (Fase 1, referensi)
workspace/state/creative_brief.json        (Fase 1 → input Fase 2)
workspace/state/render_status.json         (Fase 2 → dicek Fase 2 & 3)
workspace/drafts/video_output.mp4          (Fase 2 → dikirim Fase 3)
workspace/state/publish_history.json       (Fase 3, jalur CLI saja → konteks Fase 0 run berikutnya)
workspace/state/error.log                  (semua fase, append-only)
```
