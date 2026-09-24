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

### Commit 1b — selesai (2026-09-17)

Lock approval terpisah dari lock render (dua proses yang sama-sama polling
`getUpdates` dengan token sama akan saling mencuri balasan), guard gateway di dua
tempat, arsip `workspace/published/`, dan urutan cleanup yang benar.

- **Video APPROVED dipindah, bukan dihapus.** `publish_history.json` menyimpan
  `file_path` permanen; kalau filenya dibuang retensi, riwayatnya jadi rujukan
  mati. `published/` di luar `DRAFTS_DIR` sehingga tidak pernah kena sweep.
- **`record_history()` path-agnostik** — pemanggil menyebutkan `video_path`/
  `brief_path` yang benar untuk momennya (per-run sebelum APPROVE, `published/`
  sesudahnya).
- **Cleanup paling akhir**, setelah semua pembacaan selesai. Desain awal menaruh
  `cleanup_run_files()` di `finally`, yang akan menghapus brief SEBELUM
  `record_history()` sempat membacanya.
- **Lock approval sibuk → tidak cleanup sama sekali.** File dibiarkan utuh supaya
  bisa dicoba lagi tanpa render ulang (yang berarti membakar kredit GPT-4o lagi).
  Pesannya menyertakan `run_id` dan perintah ulangnya.
- **Guard gateway di `pipeline.py`** (sebelum render, supaya tidak membakar kredit
  lalu ditolak di ujung) **dan di `agent4_approval.py`** (untuk pemakaian
  standalone). Diekstrak ke `scripts/gateway_check.py` agar tidak terduplikasi.

### Regresi yang diperkenalkan 1a — sudah dipulihkan di 1b

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
- 19 Sept 08:15–08:25 — uji cover (2.4), 4 kiriman ke chat 1583550141. **Temuan: Telegram
  mengabaikan thumbnail custom untuk video MP4** dan membuat sendiri dari frame pertama.
  Diuji dua bentuk request (field `thumbnail` multipart dan `attach://`): keduanya
  menghasilkan thumbnail server 644 byte **hitam**, padahal cover kita 11 kB dan benar
  (sudah dilihat langsung). Sebab hitamnya: `fade=t=in:st=0` dipasang di segmen pertama
  sehingga frame 0 punya YAVG 16. Setelah fade masuk dibuang dari segmen pertama,
  thumbnail server jadi 7.739 byte berisi gambar asli. Cover JPG tetap dibuat sebagai
  artefak (dipakai di `published/` dan untuk publikasi ke platform lain).

## Backlog dari 2.4 (cover)

- **Tempelkan teks hook ke file JPG cover**, bukan menggeser subtitle. Keputusan user
  19 Sep: timing teks harus mengikuti ucapan; pengalaman menonton tidak boleh dirusak
  demi preview. Cover JPG adalah file terpisah, jadi hook bisa dibakar ke situ tanpa
  menyentuh video. Catatan: preview Telegram sendiri tetap dari frame pertama video
  (cover diabaikan), jadi manfaat langsungnya ada di `published/` dan publikasi ke
  platform lain — bukan di chat.
- Cover via model gambar: tidak diaktifkan, tetap di backlog.

## 19 Sept — penyebab "subtitle hilang-hilang" dan pindah penyedia chat

- **Akar masalah subtitle bukan hanya timeout.** Whisper lewat RelayRouter ditolak
  `403 local:insufficient_quota` (saldo $0,0004, butuh $0,0066/permintaan). Kegagalan itu
  sebelumnya hanya jadi baris `[warn]` di keluaran yang tertangkap; sekarang tiap bahan
  yang gagal punya kode alasan dan caption menyebutnya.
- **Snifox hanya menyediakan model chat** (tanpa Whisper/TTS; 15 model, cek 19 Sep). Chat
  dipindah ke `core.snifoxai.com/v1`, Whisper/TTS tetap ke RelayRouter lewat `TRANSCRIBE_*`
  / `TTS_*`. Paket langganan Snifox (Quantum) berbatas **30 RPM per kunci** dan tidak mencakup
  Claude Haiku (PAYG saja) — kalau memakai paket itu, `LLM_MODEL=anthropic/claude-sonnet-5`.
- **Seleksi konten** (`scripts/edit_plan.py`): LLM memilih nomor kandidat; kode memverifikasi.
  Sengaja DIBATALKAN kalau ada bahan yang transkripnya gagal (bukan sekadar tanpa ucapan):
  memotong dari data separuh berarti membuang ucapan yang tidak pernah dilihat LLM.
- **Temuan timestamp Whisper:** kata pertama tiap klip dilaporkan mulai di 0,00 padahal suara
  baru mulai ~0,66. Jangan pernah mengandalkan `start` kata pertama; ujung kata lebih bisa
  dipercaya. Sebuah penyaring titik-tengah sempat membuang kata pertama hampir tiap klip
  karena ini — sekarang berbasis tumpang-tindih.

- **Agent "tidak bisa mendengar" (19 Sep, ~20:00 WIB).** Video draf "Halo, Aku di Sini!":
  saldo Whisper habis -> 0 transkrip -> sistem menyimpulkan "tidak ada ucapan" -> memakai
  voice-over AI dan mengarang naskah dari gambar. Bug logika di `audio_mode.py`
  (kegagalan disamakan dengan ketiadaan) -- diperbaiki: sekarang berhenti dengan pesan jelas.
  Whisper lokal (`scripts/local_whisper.py`, venv `.venv-whisper`) menggantikan API sebagai
  default (`TRANSCRIBE_PROVIDER=auto`). Terukur pada 6 klip nyata: kecocokan 0,92 dengan
  whisper-1, RTF 0,76 pada 4 core. Timestamp kata pertama lebih jujur (0,18-0,72 dtk, bukan
  0,00 seperti API). Satu klip nyata: "calon pembeli" terbaca "cara pembeli".

### Backlog
- Seleksi visual (klip buram/gelap) — sekarang seleksi hanya dari transkrip.

- **Video B-roll food court gagal (19 Sep, 20:27 WIB) — dua bug berbeda.** Klip berisi keramaian tanpa
  ucapan. (1) Whisper menghalusinasi "You" / "Thank you for watching!" / "." pada audio tanpa ucapan
  (no_speech_prob 0,81-0,89; aturan bawaan Whisper `no_speech>0,6 DAN logprob<-1,0` tidak menangkapnya
  karena logprob -0,88/-0,95). "." lolos sebagai transkrip, prompt menyebutnya "sumber kebenaran UTAMA",
  model brief menolak -> "jawaban bukan JSON". Sekarang `saring_ucapan()` membuang segmen tanpa huruf atau
  dengan no_speech_prob > 0,6. (2) Tanpa ucapan, sistem mengganti suara asli dengan voice-over AI "supaya
  tidak sunyi" padahal audionya keramaian -15 dB. Sekarang bahan yang BERSUARA tetap memakai suara asli;
  hanya yang benar-benar hening yang jatuh ke suara AI. Juga: teks on-screen tanpa timestamp dipecah, bukan
  dipangkas "..."; prompt diberi durasi nyata tiap klip supaya scene menempel di klipnya; caption memberi
  tahu bahwa teks dibuat dari tampilan saja. Deskripsi tool plugin ditulis ulang (dulu menyebut
  "voice-over AI" dan tidak menyebut apa yang tidak ada, sehingga bot menjanjikan koreksi warna dsb).

## 19 Sept (malam) — agent bertanya dulu (inspect -> ask_user -> run)

- Rancangan: `content_factory_inspect` (fakta terukur + pertanyaan yang kurang) lalu
  `content_factory_run` yang WAJIB membawa `inspectId` sah. Aturan gerbang hanya ada di Python
  (`scripts/inspect_media.py cek_izin`), diuji di pytest, dipanggil plugin lewat `runPythonJson`
  (process group dibunuh saat timeout/abort; ada tes vitest + kontrol positif).
- **OpenClaw sudah punya tool `ask_user`** (jawaban kembali di giliran yang sama). Model
  memakainya langsung, SEBELUM `inspect`, dengan pertanyaan generik "Ya, langsung saja". Deskripsi
  tool tidak cukup; arahan tetap di `USER.md` workspace yang berhasil (toolSummary: inspect
  dipanggil pertama).
- **Cacat yang nyaris lolos:** penjaga lampiran menolak folder > 30 menit. Jawaban user yang datang
  lebih lambat akan ditolak "bukan dari pesan ini". Sekarang kesegaran dinilai saat `inspect`,
  dan `run` hanya mensyaratkan struktur bila ada pemeriksaan sah (`verifyForRun`). Dijaga tes
  integrasi `flow.integration.test.ts` (folder 3 jam lalu tetap diterima) dengan kontrol positif.
- E2E lewat CLI OpenClaw TIDAK mungkin: giliran CLI tidak punya `nativeChannelId`, kedua tool
  menolak (gagal-tertutup, benar). Alur utuh hanya teruji lewat pesan Telegram sungguhan.
- Penolakan `inspect` kini dicatat di `plugin_calls.jsonl` (dulu hanya yang sukses, sehingga empat
  panggilan gagal tidak meninggalkan jejak apa pun).
- `common.write_json` sekarang atomik (butir #8 rencana Fase 2).

## 19 Sept 22:34-23:05 — uji nyata pertama "agent bertanya dulu" gagal; pelajaran

- **`ask_user` bukan jalur yang cocok.** Log: `question.waitAnswer 899980ms` dua kali — ia menahan
  giliran agent lalu kedaluwarsa TEPAT 15 menit (tidak bisa diatur di konfigurasi). User membalas
  setelah 28 menit. Pertanyaan kedua berisi 3 pertanyaan sekaligus (penomoran opsinya diratakan
  jadi 1-12) dan balasan bebas gagal berulang: `question 'fit_mode' requires an answer`. Lalu
  `Agent run failed`. Kesimpulan saya sebelumnya ("pakai ask_user") keliru.
- Rancangan sekarang: pesan biasa yang tidak memblokir. PILIHAN jawaban (huruf, bintang = default)
  dan pemetaan jawaban -> parameter run dibuat KODE (`susun_pesan_pertanyaan`, `susun_pemetaan`),
  bukan model. Agent mengirim pesan itu apa adanya lalu mengakhiri giliran; balasan kapan pun
  dalam 24 jam menjadi giliran baru (kesegaran lampiran dinilai saat inspect).
- **Kesalahan saya: uji CLI dengan `--channel telegram --to <chat user>` menempel ke sesi utama
  user** (`agent:main:main`) dan mengganti sessionId-nya menjadi `uji-inspect-N`. Percakapan Telegram
  user berjalan di atas riwayat berisi pesan ujiku. Dipulihkan dengan `sessions.reset`. JANGAN
  menjalankan `openclaw agent` dengan `--channel/--to` milik user untuk uji; pakai `--session-id`
  tanpa `--channel` (sesi `explicit:` terpisah).
- Fitur baru dari jawaban user: teks STATIS sepanjang video (`staticText`). Teks panjang mengecil,
  tidak dipecah.

## 20 Sept — mute + musik eksternal

- `audioMode: "mute"`: suara asli dibuang seluruhnya; transkripsi tetap dipakai untuk subtitle dan
  seleksi konten. Tidak dihentikan kegagalan transkripsi (beda dengan `original`, aturan #7).
- Musik dari user: berkas audio di daftar lampiran dipisah KODE dari bahan visual (`pisahMusik`),
  disalin ke `workspace/music_user/`, dan mengalahkan pustaka/mood. Musik solo dibawa ke -16 LUFS
  (musik latar di bawah ucapan tetap 10 dB di bawahnya). Terukur: suara asli -24,2 dB -> -53,0 dB,
  musik -19,9 dB, total -17,2 LUFS.
- `inspect` mengenali berkas audio sebagai MUSIK (durasinya tidak dijumlahkan sebagai durasi
  bahan) dan menanyakan nasib suara asli. Default: bisukan untuk bahan tanpa ucapan, pertahankan
  untuk bahan berucap (membisukan video berucapan menghilangkan apa yang dikatakan).
- BELUM DIVERIFIKASI di Telegram sungguhan: apakah lampiran audio (lagu, voice note) di-stage OpenClaw
  ke `media/inbound/openclaw-staged-*` dengan ekstensi yang dikenal.

### Peta fitur ala CapCut (jujur, 20 Sept)
Sudah ada: potong jeda, seleksi konten AI, subtitle karaoke, transisi fade/hard-cut cerdas, rasio +
crop/blur/letterbox, musik + ducking + level otomatis, mute + musik user, voice-over AI (5 persona),
durasi target, cover, teks statis, pertanyaan berpilihan.
Murah (ffmpeg, tanpa GPU): kecepatan/slow-mo/speed ramp, filter warna + auto-enhance, zoom/punch-in
otomatis + Ken Burns foto, stabilisasi (libvidstab tersedia), noise removal + loudnorm ucapan,
watermark/logo, end-card CTA, variasi transisi (xfade memendekkan durasi -- perlu penyesuaian sub).
Sedang: reframing otomatis mengikuti wajah (horizontal -> vertikal), deteksi klip buram/goyang,
potong pada beat musik, penekanan kata kunci di subtitle, preset gaya.
Berat/di luar jangkauan mesin ini (4 core, tanpa GPU): hapus latar berkualitas, avatar/stiker/upscale
AI generatif, editor timeline manual (produk berbeda: ini editor berbasis chat), musik/efek/font/
template komersial (lisensi), musik trending TikTok (tidak ada API resmi).

## 23 Sept — perbaikan regresi dari commit CapCut (21 Sept)

Commit 21 Sept (`b504c65`, tidak sempat tercatat di STATUS.md ini) menambah gaya
subtitle CapCut, zoom, xfade, auto-discovery musik/B-roll, dan izin multi-folder
lampiran -- tapi **`install_plugin.sh` diubah ke `npm test || true` di commit yang
sama**, sehingga terpasang dengan 19 test gagal, termasuk test keamanan lintas-user.
Ditemukan saat menjalankan suite ulang (bukan dari laporan user). Rinciannya ada di
pesan commit `efe0018`. Poin yang perlu diketahui:

- **Live rusak sejak 21 Sept**: file `dummy.mp3` (0 byte) tertinggal di
  `workspace/music_user/`; fitur auto-discovery yang ditambahkan membuat SEMUA
  render (yang tidak menyebut musiknya sendiri) mencoba memakainya dan ditolak
  di validasi awal, sebelum render sempat mulai. Sudah diperbaiki + dibersihkan.
- Auto-discovery musik & B-roll dihapus (bukan diperbaiki) -- keduanya memindai
  folder bersama tanpa kepemilikan per-run, persis pola yang dilarang aturan #4.
- Penolakan campuran folder lampiran (dua user) dikembalikan.
- `.env` sempat memaksa `SUBTITLE_STYLE=capcut` + zoom/xfade sebagai default
  GLOBAL (bukan per-permintaan) -- dilepas. Style CapCut & flag zoom/xfade masih
  ada di kode kalau mau diaktifkan lewat parameter per-run nanti, tapi belum
  disambungkan ke tool plugin dan belum diuji sebagai fitur yang bisa diminta user.
- `install_plugin.sh` dikembalikan jadi gerbang keras (test gagal = install gagal).

Pelajaran: **jangan pernah melonggarkan gerbang test untuk membuat commit lolos.**
Kalau test gagal, itu sinyal untuk berhenti dan memperbaiki, bukan melewatinya.

## 23 Sept — filter warna, speed ramp, zoom otomatis (diperbaiki dari percobaan gagal 21 Sept)

- `colorFilter`/`speedFactor`/`autoZoom` disambungkan sebagai parameter tool plugin nyata
  (bukan flag env statis di `.env`), tervalidasi eager sebelum lock+LLM (`scripts/style.py`).
- Bug zoompan lama (`time` -- variabel yang tidak ada) diganti resep standar; diverifikasi
  dengan gambar berbingkai + ukur saturasi piksel, bukan baca string filter.
- `speedFactor` sengaja HANYA aktif di `audioMode=ai`: `-t` di build_segment adalah batas
  durasi OUTPUT, jadi setpts tidak mengubah slot timeline (aman untuk scene/subtitle),
  tapi mode audio asli/mute tetap dikecualikan karena orangnya masih terlihat bicara.
- Uji manual: render end-to-end (`render_from_agent_script`, mode AI, edge-tts) dengan
  ketiga fitur aktif bersamaan -- sukses, durasi keluaran 7,83 dtk cocok narasi.
- 720 pytest + 52 vitest lolos, `tsc` bersih.

## 23 Sept — jembatan ke Hermes (menggantikan OpenClaw)

User memutuskan pindah dari OpenClaw ke Hermes (agent lain, sudah terpasang manual
di mesin ini sejak 19-21 Sept lewat installer resmi Nous Research -- ditelusuri,
BUKAN instalasi tak dikenal) untuk jembatan Telegram. `openclaw-gateway.service`
sudah di-stop+disable (bentrok getUpdates: keduanya memakai token bot yang sama).
`~/.openclaw` BELUM dihapus -- ditahan sampai Hermes terbukti jalan.

Temuan arsitektur (dari membaca source Hermes langsung, `/usr/local/lib/hermes-agent`):
tool MCP custom di Hermes TIDAK menerima identitas chat tepercaya lewat argumen
tool-call (beda dari OpenClaw yang menangkap `nativeChannelId` lewat closure
server-side) -- `HERMES_SESSION_CHAT_ID` dkk. adalah contextvar internal Hermes,
dipakai gateway-nya sendiri untuk notifikasi proses latar belakang, bukan
diteruskan ke proses anak. Karena itu jalur yang dibangun BUKAN MCP server, tapi:
agent memanggil `scripts/hermes_render.py` (baru) lewat tool terminal Hermes
sendiri (`background=true, notify_on_complete=true`); skrip itu HANYA merender
dan mengembalikan path file lewat JSON -- pengiriman ke chat dilakukan agent
Hermes sendiri setelah dibangunkan kembali di sesi/chat yang sama, memakai
kemampuan kirim-file bawaannya (levelnya Hermes sendiri yang menjamin chat benar,
sama seperti kemampuan kirim pesan biasa).

`scripts/hermes_render.py` memakai ulang seluruh core pipeline (`run_core_stages_locked`,
`cek_izin`, `resolve_canvas`, `music.py`, `style.py`) -- validasi lampiran lewat
realpath-di-dalam-root yang sama semangatnya dengan `verifyInbound` OpenClaw, root-nya
`~/.hermes/cache/`. Skill instruksinya di `hermes-skill/content-factory/SKILL.md`
(belum dipasang ke `~/.hermes/skills/` -- itu langkah manual user).

Uji manual: render end-to-end lewat `hermes_render.main()` dengan bahan di folder
cache tiruan -- sukses, JSON hasil benar (video_path, judul, deskripsi, biaya).
728 pytest lolos (+8 test baru untuk validasi path/staging).

BELUM DIUJI: giliran nyata lewat Telegram sungguhan lewat Hermes (perlu pesan
nyata dari user); apakah `notify_on_complete` benar-benar mengembalikan stdout
proses ke agent seperti yang diasumsikan skill-nya.

## 24 Sept — posisi/font teks + pertanyaan proaktif (jalur Hermes)

- `TEXT_POSITION` (atas/tengah/bawah) dan `TEXT_FONT` (standar/tegas/modern/elegan/santai/bersih);
  flag `--text-position/--text-font` di `hermes_render.py`. Hanya untuk teks TULISAN di layar;
  subtitle ucapan tidak ikut. Daftar font kecil yang diuji + 3 font OFL baru di `assets/fonts/`.
- Bug yang ditangkap tes: judul tengah dibesarkan 1,4x terpotong di kedua tepi pada font lebar,
  karena lebar dihitung dengan satu rasio karakter. Kini `_muat_lebar()` mengukur dengan berkas font
  yang dipakai; tes regresi untuk semua font x teks pendek/panjang.
- `inspect_media.py`: pertanyaan `gaya_teks` (posisi+font, bahan tanpa ucapan) dan tawaran proaktif
  `gaya` (CapCut / filter warna) yang HANYA menumpang bila sudah ada pertanyaan lain -- permintaan
  yang sudah lengkap tidak dipaksa ditanyai. MAKS_PERTANYAAN 5 -> 6.
- TEMUAN: budget koboiLLM habis untuk pipeline juga (`.env` AIHackfest, 429 Budget exceeded). Uji
  lewat override env dengan `nex-agi/nex-n2.5-pro:free` (OpenRouter; gambar+JSON) sukses end-to-end.
  `.env` AIHackfest BELUM diubah -- keputusan user.
- Belum dikerjakan: analisis mood musik, B-roll (butuh key Pexels).
