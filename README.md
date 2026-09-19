# AIHackfest — Content Factory 5 Agent

Pipeline otomatis yang menyusun **foto/video mentah milik user** menjadi satu konten
vertikal 9:16 siap tayang, lengkap dengan voice-over AI, lalu meminta persetujuan
user di Telegram sebelum apa pun dipublikasikan.

## Alur 5 Agent

| Fase | Agent | Script | Input → Output |
|---|---|---|---|
| 0 | 📊 ContentInsight | `scripts/agent5_insight.py` | `publish_history.json` → `performance_summary.json` |
| 1 | 🔍 TrendAnalysts + 💡 BrainIdea | `scripts/agent1_2_brief.py` | bahan di `workspace/raw/` + performa → `trend_report.json`, `creative_brief.json` |
| 2 | ✍️ ContentMakers | `scripts/agent3_render.py` → `skills/video_generator/auto_render.py` | brief → `workspace/drafts/video_output.mp4`, `render_status.json` |
| 3 | ✅ ApprovalPost | `scripts/agent4_approval.py` | video → Telegram, tunggu APPROVE/REVISI → `publish_history.json` |

Nama persona sengaja sama persis dengan agent yang terdaftar di OpenClaw
(`agents.entries`: `trendanalysts`, `brainidea`, `contentmakers`, `approvalpost`, `contentinsight`).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # lalu isi nilainya
```

`.env` yang dibutuhkan:

| Variabel | Wajib | Keterangan |
|---|---|---|
| `OPENAI_API_KEY` | ya | untuk riset tren & penulisan naskah (gpt-4o) |
| `TELEGRAM_BOT_TOKEN` | ya (untuk approval) | token bot dari BotFather |
| `TELEGRAM_CHAT_ID` | ya (untuk approval) | tujuan pengiriman draft; wajib karena bot yang memulai percakapan |
| `ALLOWED_CHAT_IDS` | ya (untuk Telegram) | daftar chat yang boleh memakai pipeline, dipisah koma. KOSONG = tidak ada yang diizinkan (gagal-tertutup) |
| `APPROVAL_TIMEOUT_SECONDS` | tidak | default `600` |
| `TARGET_PLATFORM` | tidak | default `Instagram Reels` |

Bentuk video keluaran (semuanya opsional, nilai salah ketik DITOLAK di titik
masuk — sebelum lock render dan sebelum satu pun panggilan LLM, bukan diam-diam
jatuh ke default):

| Variabel | Default | Keterangan |
|---|---|---|
| `VIDEO_ASPECT` | `9:16` | `9:16` (Reels/TikTok), `1:1` (feed), `16:9` (YouTube) |
| `FIT_MODE` | `crop` | `crop` (isi penuh, tepi terpotong), `blur` (latar blur), `letterbox` (latar hitam) |
| `AUDIO_MODE` | `original` | `original` = suara asli video user; `ai` = voice-over AI |
| `TTS_PERSONA` | `ramah` | persona suara AI, dipakai kalau `AUDIO_MODE=ai` |
| `SUBTITLE_FONT` | `DejaVu Sans` | nama keluarga font (bukan path); dicari lewat `fc-match` |
| `TRIM_SILENCE` | `1` | potong jeda/silence dari video user |
| `TRANSITION` | `fade` | transisi antar klip |
| `THUMBNAIL_ENABLED` | `1` | ambil cover JPG dari tengah scene pertama & lampirkan ke video Telegram |
| `DURATION_MIN` / `DURATION_MAX` | `10` / `60` | rentang durasi yang boleh diminta user; di luar itu dijepit dan user diberi tahu |
| `DURATION_TOLERANCE` | `0.20` | meleset lebih dari ini memicu SATU kali penulisan ulang naskah |
| `MUSIC_ENABLED` | `1` | musik latar, kalau ada berkas di `assets/music/` (lihat README di folder itu) |
| `MUSIC_BELOW_SPEECH_DB` | `10` | seberapa jauh musik di bawah ucapan; level dihitung dari loudness video, bukan gain tetap |
| `MUSIC_DUCK_RATIO` / `MUSIC_DUCK_SIDECHAIN_GAIN` | `12` / `8` | kekuatan auto-ducking saat ada yang bicara |
| `SPOKEN_REWRITE` | `1` | brief menulis `voice_over_spoken` (ejaan fonetis untuk TTS) terpisah dari `full_voice_over` (ejaan benar untuk subtitle/caption) |

## Cara menjalankan

### A. Lewat CLI

```bash
# 1. taruh bahan mentah (foto/video) di workspace/raw/
# 2. jalankan seluruh pipeline
python3 scripts/pipeline.py

# atau satu tahap saja
python3 scripts/agent1_2_brief.py
python3 scripts/agent3_render.py
```

Pipeline **berhenti** kalau tahap kritis gagal (exit code diperiksa), jadi tidak akan
lanjut memproses data basi.

### B. Lewat chat Telegram (plugin OpenClaw)

```bash
cd openclaw-plugin && npm install && npm run plugin:build
openclaw plugins install /root/AIHackfest/openclaw-plugin --force --accept-capabilities
openclaw config set plugins.entries.content-factory.config.projectRoot '"/root/AIHackfest"' --strict-json
openclaw config set tools.alsoAllow '["content_factory_run"]' --strict-json
openclaw gateway restart
```

Lalu upload foto/video di chat dan minta disusun jadi konten — model akan memanggil
tool `content_factory_run`.

> **`projectRoot` wajib absolut.** Saat diinstall, plugin di-copy ke
> `~/.openclaw/extensions/`, jadi path relatif terhadap lokasi plugin akan salah arah.
> Plugin akan menolak jalan dengan pesan jelas kalau `projectRoot` kosong/salah.

## ⚠️ Jangan jalankan kedua jalur bersamaan

Gateway OpenClaw dan `agent4_approval.py` memakai **bot token yang sama**, sedangkan
Telegram hanya punya satu antrian `getUpdates` per bot — siapa pun yang fetch duluan
menghabiskan antrian milik yang lain, sehingga balasan APPROVE/REVISI bisa hilang.

- Pakai **jalur plugin** (disarankan): approval memakai delivery bawaan gateway.
- Pakai **jalur CLI**: hentikan gateway dulu (`openclaw gateway stop`).

`agent4_approval.py` akan memperingatkan otomatis kalau mendeteksi gateway aktif.

## Aturan yang dijaga di kode

1. **Zero Hallucination on Assets** — daftar bahan ditentukan Python dan divalidasi ada
   di disk, bukan dipercaya dari LLM. Nama file karangan → gagal terang-terangan.
2. **Human-in-the-loop** — tidak ada jalur kode yang publish tanpa balasan APPROVE user.
3. **Laporan jujur** — Agent 5 menandai `data_source` (`ESTIMATED_PLACEHOLDER` vs
   `REAL_API`) dan angkanya bervariasi, tidak selalu positif.
4. **Resilient error handling** — tiap tahap menulis `workspace/state/error.log` dan
   status JSON, lalu keluar dengan exit code yang benar.
5. **Path cwd-independent** — semua path dihitung dari lokasi file, bukan cwd pemanggil.

## Yang belum selesai

- `publish_to_platforms()` di `agent4_approval.py` masih stub: belum ada kredensial
  Meta Graph API / TikTok Content Posting API / YouTube Data API v3 di `.env`.
  Saat user APPROVE, sistem meminta upload manual — bukan pura-pura berhasil publish.
- `fetch_real_analytics()` di `agent5_insight.py` masih stub dengan alasan yang sama.
- `skills/search_trends`, `skills/copy_formatter`, `skills/channel_notifier` masih berupa
  dokumen SKILL.md; risetnya saat ini dilakukan langsung oleh gpt-4o di Agent 1&2.
