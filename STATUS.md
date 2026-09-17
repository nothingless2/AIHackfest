# Status Fitur — Content Factory 5 Agent

Terakhir diverifikasi: 2026-09-16, terhadap kode di `scripts/`, `skills/`, dan
`openclaw-plugin/` sebagaimana ada di disk saat ini. Lihat [ARCHITECTURE.md](ARCHITECTURE.md)
untuk penjelasan alur & desain lengkap.

## Sudah berfungsi (diverifikasi lewat tes nyata)

| Fitur | Bukti verifikasi |
|---|---|
| Upload gambar/video di Telegram terbaca | File tersalin ke `workspace/raw/` dengan ukuran & dimensi asli utuh |
| Riset tren + naskah oleh GPT-4o nyata | `creative_brief.json` berisi naskah koheren, bukan template |
| Voice-over AI (edge-tts) | Audio track ada di output, `mean_volume` terukur, bukan silent |
| Render 9:16, multi-asset (gambar+video campur) | `ffprobe` konsisten `1080x1920` di semua run tes |
| Teks scene di safe-zone (tidak kepotong UI platform) | Diverifikasi visual lewat screenshot frame |
| Render asinkron (tidak kena timeout tool) | Tool balas <1 detik, render lanjut di background, video valid |
| Video dikirim ke chat pemanggil (bukan ID tetap) | `toolContext.nativeChannelId` diteruskan sebagai `CONTENT_FACTORY_CHAT_ID` |
| Zero Hallucination on Assets | Nama file dipaksa dari Python; nama karangan → gagal eksplisit |
| Pipeline berhenti saat tahap kritis gagal | Exit code diperiksa, tidak lanjut dengan data basi |
| Data performa tidak selalu "HIGH_PERFORMING" | Hasil tes nyata: `MODERATE_PERFORMING` |
| **Render cepat (ffmpeg-native)** | Profiling berlapis: 332,8s → 35,6s untuk render (~9x), lihat detail di bawah |

### Optimasi performa render (2026-09-16)

Render awalnya berbasis moviepy (Python per-frame) — untuk 2 klip video pendek
butuh **332,8 detik**. Diprofilkan per-lapis untuk cari bottleneck sebenarnya:

| Operasi | moviepy (Python) | ffmpeg native (C) |
|---|---|---|
| Resize+Crop ke 1080x1920 (1 klip) | 32,1s | 3,6s |
| Concatenate 2 klip + crossfade | 99,1s | 8,7s (hard-cut, tanpa crossfade) |
| Text overlay | +45s | +5,9s (`drawtext`) |

`skills/video_generator/auto_render.py` ditulis ulang: moviepy hanya dipakai untuk
membaca durasi audio (operasi ringan); semua scale/crop/loop/concat/teks sekarang
lewat subprocess `ffmpeg` native. Hasil akhir: **35,6 detik** untuk kasus yang sama.

## Belum ada / masih stub — sengaja tidak disembunyikan

### Gap arsitektur (mempengaruhi keandalan)

| Gap | Detail | Risiko |
|---|---|---|
| **APPROVE/REVISI tidak pernah ditangkap** | `agent4_approval.py` punya logika polling `getUpdates`, tapi **tidak dipanggil** oleh jalur plugin (`run_and_deliver.py`). Tidak ada kode yang bereaksi terhadap balasan APPROVE. | Human-in-the-loop yang dijanjikan di caption Telegram **tidak ditegakkan** sistem. Untungnya juga tidak ada auto-publish, jadi tidak berbahaya — tapi fiturnya memang belum ada. |
| ~~**Path output video tetap (bukan per-run)**~~ **(diperbaiki di Commit 1a)** | Hasil akhir kini disalin ke `video_{run_id}.mp4` / `creative_brief_{run_id}.json` **sebelum** lock render dilepas; render itu sendiri dilindungi `flock` eksklusif. | Sudah tertutup. Path kerja internal ffmpeg (`_segment_*.mp4` dll) tetap path tetap — aman karena berada di dalam lock. |
| ~~**Tidak ada pembatasan akses per-pengirim**~~ **(diperbaiki: allowlist)** | `ALLOWED_CHAT_IDS` di `.env` dicek sebelum lock render dan sebelum panggilan GPT-4o mana pun. Gagal-tertutup: daftar kosong menolak semua. `tools.toolsBySender` di OpenClaw tetap belum diset, tapi pembatasan kini ditegakkan di sisi pipeline. | Sudah tertutup untuk pemakaian kredit & pengiriman. |
| `publish_to_platforms()` | Stub, `return None`. Butuh kredensial Meta Graph API / TikTok / YouTube Data API. | Publish otomatis tidak ada; user diminta upload manual. |
| `fetch_real_analytics()` | Stub, `return None`, fallback ke estimasi acak. | Angka performa bukan data asli platform. |
| `agents/*.md`, `skills/*.md`, `openclaw.config.json` (AIHackfest) | Tidak dibaca kode apa pun. `agents.entries` di OpenClaw tidak menunjuk ke file `.md` ini. | Dokumen desain murni, tidak mempengaruhi perilaku sistem. |
| Tidak ada SOUL.md | — | Guardrail hanya hidup sebagai kode (validasi, try/except), tidak ada dokumen kebijakan terpisah. |

### Gap kualitas konten (dikonfirmasi 2026-09-16, terverifikasi via code inspection)

| Gap | Detail | Dampak |
|---|---|---|
| **TrendAnalysts & BrainIdea "buta" terhadap isi bahan** | `agent1_2_brief.py` kirim ke GPT-4o cuma **nama file**, bukan isi gambar/video (tidak ada panggilan vision/multimodal). | Naskah & konsep konten tidak benar-benar sesuai apa yang ada di foto/video user — cuma nebak dari nama file. |
| **Tanpa musik latar** | Tidak ada satu baris kode pun yang mencampur BGM. Audio final cuma voice-over polos. | Konten terasa sepi/kering dibanding video sejenis di platform. |
| **Tanpa animasi teks** | Teks muncul & hilang instan di posisi tetap (`drawtext` statis). | Terasa kaku dibanding caption bergaya TikTok/Reels modern. |
| **Voice-over terdengar kaku** | `edge-tts` (Microsoft neural TTS pihak ketiga) tanpa tuning SSML/prosodi. | Keterbatasan teknologi yang dipilih, belum tentu bisa dihilangkan total. |

Belum dikerjakan — menunggu giliran setelah optimasi performa selesai (lihat riwayat percakapan).

## Fase 1 — Reliability/Ops (sedang berjalan)

### Commit 1a — selesai & terverifikasi (2026-09-17)

Lock render (`flock`), path per-run, timeout per tahap dengan `killpg` proses-grup,
dan handler SIGINT/SIGTERM. Diverifikasi: 15 test pytest lolos; SIGINT nyata saat
ffmpeg berjalan -> exit 130, ffmpeg cucu ikut mati, `run_finished` tetap tercatat
lewat `finally`, lock terlepas.

- **`deliver_plugin()` dikerjakan di Commit 1a, bukan 1b** (menyimpang dari rencana
  yang disetujui; kode sudah ter-commit di `0fa6d82`). Fungsi ini **tidak** memanggil
  cleanup apa pun — itu memang disengaja, supaya file per-run tetap jadi fondasi
  kalau/ketika handler APPROVE jalur plugin dibangun.
- **Scope Commit 1b diperbarui** (delivery dikeluarkan karena sudah selesai di 1a):
  lock approval, guard gateway di 2 tempat, `retention.py`
  (`cleanup_run_files` + `sweep_old_run_files`), arsip video APPROVED ke
  `workspace/published/`, rombak urutan `record_history`/cleanup di
  `agent4_approval.py`, dan pembersihan file kerja segmen (lihat di bawah).

### Regresi yang diperkenalkan 1a — harus dipulihkan di 1b

Versi pra-1a `run_and_deliver.py` menghapus `video_output.mp4` basi di awal run
("supaya tidak terkirim tidak sengaja") dan mengecek keberadaan file setelah render.
**Kedua pengaman itu hilang saat 1a menulis ulang `main()`.** Mitigasi yang masih
berlaku: `agent3_render.py` memvalidasi `render_status == SUCCESS` **dan**
`os.path.exists(DRAFT_VIDEO_PATH)`, jadi belum ada jalur gagal yang diketahui —
tapi lapis pertahanannya berkurang, dan itu melanggar aturan "jangan merusak
perilaku yang sudah ada".

### File kerja segmen — diperiksa, bukan bug akut

Run yang mati meninggalkan `_segment_*.mp4` (mis. 48 byte, terpotong). **`concat_segments`
TIDAK memakai glob** — ia menerima daftar eksplisit hasil `enumerate(existing_assets)`,
dan `run_ffmpeg` selalu memakai `-y`. Jadi segmen basi **tidak bisa** menyusup ke render
berikutnya. Yang tersisa hanyalah sampah disk dan berkurangnya lapis pertahanan.
Rencana 1b: hapus file kerja (`_segment_*.mp4`, `_combined_*.mp4`, `_concat_list.txt`,
`temp_vo.mp3`, `video_output.mp4` basi) di **awal** render, **di dalam** lock render —
sekaligus memulihkan regresi di atas.

### Routing per-chat (2026-09-17)

Tujuan pengiriman kini selalu chat pemicu (`CONTENT_FACTORY_CHAT_ID` dari
`nativeChannelId`). Fallback lama ke `TELEGRAM_CHAT_ID` **dihapus** dari jalur
Telegram: fallback itulah yang membuat hasil run siapa pun terkirim ke satu chat
tetap, sehingga materi milik satu user bisa sampai ke user lain. `.env` kini hanya
sumber untuk jalur CLI. `notify()`/`send_video()` mewajibkan `chat_id` eksplisit.

**Wajib untuk handler APPROVE jalur plugin (kalau/ketika dibangun):** handler itu
**harus memfilter balasan per `chat_id` pemicu run**, sama seperti
`wait_for_reply()` di `agent4_approval.py` sekarang. Tanpa filter itu, satu user
bisa menyetujui atau menolak konten milik user lain. Ini bukan detail opsional —
sistem ini dipakai lebih dari satu orang.

### Operasional: install plugin & penyebab OOM

`openclaw plugins install <dir>` menyalin **seluruh** isi folder plugin ke staging
internalnya, termasuk `node_modules`. Dengan devDependency terpasang (`openclaw`,
`typescript`, `vitest`) folder itu **607 MB**, dan proses install kena **OOM-killed
(exit 137)** di mesin 8 GB yang juga menjalankan gateway.

Akibatnya lebih berbahaya daripada sekadar gagal: install yang mati di tengah
meninggalkan `~/.openclaw/extensions/content-factory` **tanpa `openclaw.plugin.json`**,
sehingga gateway diam-diam terus menjalankan build plugin **yang lama**. Inilah sebabnya
perubahan `CONTENT_FACTORY_RUN_ID` dari Commit 1a sempat tidak pernah aktif.

Selalu pakai **`scripts/install_plugin.sh`**: install lengkap -> build -> test ->
salin ke staging terpisah tanpa devDependency (~6,6 MB) -> `openclaw plugins install`
dari staging -> restart gateway, dengan verifikasi manifest & build di tiap langkah.
`node_modules` folder kerja sengaja **tidak** disentuh. Jangan `npm prune` folder kerja
— itu merusak build/test berikutnya.

## Riwayat tes nyata di Telegram

- 14 Sept 11:15 — 4 gambar, **gagal** (race condition: `workspace/raw` sempat kosong saat Agent 1&2 baca; tidak terulang di tes berikutnya).
- 14 Sept 11:29 — 5 gambar, **berhasil**, terkirim (~jalur lama, moviepy).
- 14 Sept — 4 video, **berhasil** tapi 87 menit (kombinasi duplikasi run + moviepy lambat untuk video).
- 16 Sept — profiling & rewrite render engine ke ffmpeg-native, diverifikasi lokal (video-only & campuran gambar+video), **belum dites ulang lewat Telegram**.
- 17 Sept 07:45 — plugin dibangun ulang & diinstal (membawa `CONTENT_FACTORY_RUN_ID`), gateway di-restart.
- 17 Sept ~07:50 — user memicu 2 permintaan lewat Telegram; **tidak ada satu pun yang sampai ke gateway**.
  Bukti: `run_log.jsonl` tidak terbentuk, `workspace/raw` & `drafts` kosong, 0 kemunculan
  `content_factory` di `/tmp/openclaw/openclaw-2026-09-17.log`, dan hanya 17 entri log sejak restart
  (tidak satu pun pesan masuk). Bot sendiri sehat (`getMe` -> `@processingssabot`) dan polling aktif.
  Sempat ada `UND_ERR_CONNECT_TIMEOUT` ke API Telegram jam 07:14 (sebelum restart). **Belum terjelaskan —
  verifikasi run_id dari toolCallId masih tertunda.**
