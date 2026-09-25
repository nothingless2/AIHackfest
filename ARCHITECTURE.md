# Arsitektur & Alur Pipeline

Lihat [STATUS.md](STATUS.md) untuk riwayat fitur dan hasil uji nyata.

## Gambaran besar

```
Telegram ──► Hermes gateway (hermes-gateway.service, model gratis OpenRouter)
               │  agent membaca hermes-skill/content-factory/SKILL.md
               │  (salinan terpasang: ~/.hermes/skills/content-factory/SKILL.md)
               ▼
      scripts/inspect_media.py inspect     (sinkron, ~1-2 dtk, tanpa LLM)
               │  pesan pertanyaan berpilihan -> user menjawab
               ▼
      scripts/hermes_render.py --draft     (latar belakang, ~1-2 menit)
               │  pesan draf: pemahaman per klip + naskah A/B + rencana grafik
               ▼  user memilih / mengubah naskah
      scripts/hermes_render.py --draft-id X --varian A [--naskah ...]
               │  (latar belakang, render saja)
               ▼
      JSON hasil (video_path, judul, catatan...) -> agent Hermes mengirim video ke chat
```

Pipeline Python **tidak pernah mengirim apa pun ke Telegram**. Hermes tidak memberi identitas
chat yang tepercaya ke proses anak, jadi skrip hanya mengembalikan path hasil lewat stdout.
Agent Hermes yang mengirimnya, di sesi chat yang sama.

## Titik masuk

| Berkas | Dipanggil oleh | Tugas |
|---|---|---|
| `scripts/inspect_media.py inspect` | agent (JSON di stdin) | Mengukur fakta bahan (durasi, suara, ucapan lewat VAD lokal, mood musik), menyusun pertanyaan yang benar-benar kurang, lalu menyimpan status `inspect_id` (24 jam). |
| `scripts/hermes_render.py` | agent (latar belakang) | Memvalidasi path lampiran (harus di `~/.hermes/cache/` setelah `realpath`), menjalankan gerbang `cek_izin`, menyalin bahan ke `workspace/raw/{prefix}_`, menyetel env pengaturan, lalu menjalankan tahap di dalam lock render. |
| `scripts/check_locks.py` | operator | Memastikan lock render bebas sebelum restart gateway. |

## Tahap (scripts/orchestrator.py)

Tiap tahap adalah subprocess dengan process group sendiri dan batas waktu keras (`killpg`).
Semuanya berjalan di dalam SATU lock render (`flock`), karena berkas kerja di `workspace/`
dipakai bersama.

| Mode | Tahap |
|---|---|
| `--draft` | `agent5_insight.py` (ContentInsight, non-kritis), lalu `agent1_2_brief.py` (mode draf, 2 varian) |
| `--draft-id` | `agent3_render.py` saja. Brief = varian pilihan user dari `workspace/state/draf_naskah/{id}.json` |
| tanpa draf | ketiganya berurutan (dipakai tes & pemakaian manual) |

1. **ContentInsight** (`agent5_insight.py`):
   - Laporan performa: `NO_DATA` sampai ada API analitik platform.
   - Kata kunci dari isi bahan (1 panggilan LLM), lalu tren nyata dari YouTube dan Google Trends
     (`skills/search_trends/fetch_trends.py`), ditulis ke `trend_pool.json`.
2. **TrendAnalysts + BrainIdea** (`agent1_2_brief.py`):
   - Transkripsi (`transcribe.py`: Whisper lokal atau API; alasan gagal per bahan dicatat).
   - Mode audio (`audio_mode.py`).
   - Lembar kontak 4 momen per klip (`vision.py`, bagian goyang dilewati lewat
     `visual_quality.py`), plus fakta per klip dan contoh gaya kreator (`config/gaya_naskah/`).
   - Satu panggilan LLM menghasilkan `trend_report` + brief (atau 2 varian di mode draf) +
     `motion_plan`. Tren dipilih hanya lewat NOMOR dari kolam yang benar-benar diambil.
   - Naskah diperiksa `naskah.py` (frasa deskriptif, kalimat panjang, huruf non-Latin) dan
     ditulis ulang maksimal sekali.
   - Mode suara asli: seleksi potongan ucapan terbaik (`edit_plan.py`).
3. **ContentMakers** (`agent3_render.py` -> `skills/video_generator/auto_render.py`):
   - TTS: ElevenLabs dengan waktu per karakter, cadangan edge-tts dengan WordBoundary.
   - Segmen 9:16 per bahan lewat ffmpeg: kanvas, filter warna, zoom foto, speed ramp,
     pemotongan bagian goyang, B-roll Pexels opsional (`broll.py`).
   - Concat dengan fade di pergantian topik.
   - Teks: karaoke dari ucapan, teks narasi satu kata mengikuti suara, dan teks tulisan
     beranimasi lewat Remotion (`overlay_remotion.py` + `remotion/src/TextOverlay.jsx`).
   - Motion graphic: `motion_plan.py` memvalidasi dan menjadwalkan ke waktu kata narasi,
     `remotion/src/MotionOverlay.jsx` merender. Ditempel dalam encode yang sama dengan teks.
   - Musik latar dengan ducking (`music.py`, `music_mood.py`) dan cover JPG (`thumbnail.py`).

## Prinsip yang ditegakkan di kode (lihat CLAUDE.md)

- **Gagal-tertutup**:
  - Path lampiran di luar cache Hermes ditolak.
  - Bahan tanpa prefix run ditolak (`select_assets`).
  - `inspect_id` dan draf wajib milik chat yang sama, belum kedaluwarsa, bahannya sama. Draf
    hanya bisa dirender sekali (klaim `O_EXCL`).
- **LLM mengusulkan, kode mengukur**:
  - Tren hanya lewat nomor dari kolam.
  - Daftar bahan dipaksa dari Python.
  - Angka di motion graphic harus ada di permintaan user atau ucapan asli.
  - Waktu elemen diambil dari kata yang diucapkan.
  - Biaya dari token nyata × `config/pricing.json`.
- **Kegagalan bukan ketiadaan**: transkripsi yang gagal dibedakan dari "tanpa ucapan", dan
  dilaporkan ke user lewat `catatan_bahan` di hasil `hermes_render`.

## State

```
workspace/raw/{prefix}_*                 bahan mentah per run (tidak pernah dihapus retensi)
workspace/state/inspect/{id}.json        status pemeriksaan (24 jam)
workspace/state/draf_naskah/{id}.json    draf naskah (24 jam; .dipakai = sudah dirender)
workspace/state/trend_pool.json          tren nyata dari ContentInsight
workspace/state/creative_brief.json      brief kerja (di dalam lock)
workspace/state/creative_brief_{run}.json, workspace/drafts/video_{run}.mp4/.jpg   hasil per run (7 hari)
workspace/state/render_status.json       status render + catatan (motion, potong_visual, suara, ...)
workspace/state/run_log.jsonl            event siklus hidup, panggilan LLM/TTS/transkripsi + biaya
workspace/state/*_cache.json             cache analisis goyang & mood musik
```
