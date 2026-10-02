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
| Revisi cepat tanpa LLM (26 Sep) | Render nyata + 4 revisi berantai: 80-94 dtk tiap revisi, 0 panggilan LLM; klip B-roll yang tidak disebut ber-id Pexels sama; audio revisi ke-4 identik byte-per-byte dengan revisi ke-3 (cache TTS, 0 panggilan ElevenLabs); chat lain ditolak |
| Caption dinamis + SFX (27 Sep, tahap uji) | Render nyata 6 video berucap (45 dtk): 42 potongan, kata kunci emas tepat saat diucapkan, 8 pop + 4 whoosh, QA lolos; +51 dtk vs gaya kata; proses terbesar 1,3 GB (versi pertama OOM 3,8 GB → diganti satu input concat) |
| Musik & SFX terdengar + tata letak panggung (29 Sep) | Musik di jeda naik -16,7 → -8,0 dB relatif ucapan, puncak ucapan tetap; tes musik/SFX memakai ucapan sungguhan (setelan lama gagal di tes). Panggung render nyata: latar hanya di jendela 15,75-18,5 dtk (terukur piksel), sudut kartu membulat, +33 dtk render |
| Suara bersih, pengisi dibuang, zoom & logo (29-30 Sep) | Suara: SNR 31,2->36,1 dB di video user, jeda -7,3 dB. Pengisi: kontrol edge-tts ber-"eee" -> 3 potongan tepat, durasi video berkurang sesuai; 6 video user memang 0 pengisi (dibuktikan dengan kontrol positif). Zoom: skala terukur 1,10x di jendela & 1,00 di luar, pada render NYATA. Logo: "Hermes" ditolak (katalog = myHermes), kartu Claude dirender & tidak menimpa wajah |
| Cover didesain (30 Sep) | Render nyata: frame terpilih detik 25,86 (wajah terdeteksi), judul "Ganti API key, error terus". Pita judul 22,1% piksel putih vs 0% di frame asli; area wajah 3,31% vs 2,86% (= terang gambar, bukan teks). Remotion gagal -> cover lama tetap dibuat |
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

## 24 Sept (siang) — mood musik + B-roll Pexels

**Mood musik** (`scripts/music_mood.py`, numpy saja): tempo (autokorelasi onset dengan skor harmonik),
energi, kecerahan -> label tenang/santai/upbeat/energik. Diukur, bukan ditebak: kesalahan yang
ditangkap pengukuran -- (1) tempo 120/140/160 terbaca setengahnya (salah oktaf) -> skor harmonik +
pemecah seri; (2) nada steady dibaca "yakin 112 BPM" -> kriteria keyakinan `FLUX_REL_MIN`/`SKOR_MIN`
dikalibrasi pada kasus terukur (metrik "kontras persentil" yang dicoba lebih dulu DIBUANG: nada steady
4,19 mengalahkan beat berderau 1,4). Lagu lofi asli milik user terbaca 82 BPM/tenang (masuk akal).
JUJUR: ambang LABEL adalah heuristik -- tidak ada kumpulan lagu berlabel untuk mengkalibrasinya.
Dipakai untuk: laporan `inspect` musik user, pencocokan mood ke pustaka bila nama berkas tidak cocok
(hanya kosakata label), `render_status.music_mood`, hasil `hermes_render`.

**B-roll** (`scripts/broll.py`, Pexels): hanya `--audio-mode ai`; tanpa PEXELS_API_KEY ditolak sebelum
render; kegagalan di tengah jalan tidak menggagalkan video tapi dilaporkan; klip diunduh ke folder kerja
run dan dibuang; host unduhan wajib pexels.com; jumlah dibatasi supaya bahan user tidak terbuang
(`bagi_durasi`); kredit kreator dikembalikan untuk caption. Ditawarkan di `inspect` HANYA bila key ada.
Uji nyata: Pexels asli (13 kandidat), render end-to-end -- slot 1 bahan user, slot 2-3 klip barista.
`LLM_MODEL` di .env -> `nex-agi/nex-n2.5-mini:free` (varian pro timeout 59 dtk/balasan kosong; brief
melewati batas 450 dtk). Key Pexels di `.env` (ter-ignore git).

## 24 Sept (sore) — teks animasi Remotion

- `remotion/` (React, Chromium headless Playwright) merender teks TULISAN sebagai lapisan transparan
  ProRes 4444; `scripts/overlay_remotion.py` merencanakan frame dan menempelkannya dengan ffmpeg.
  Subtitle ucapan tetap drawtext karaoke. Gagal -> teks statis + `render_status.teks_animasi.gagal`.
- Terukur: judul 9 dtk = 42 frame Chromium (~13 dtk) berkat gambar diam untuk bagian tak bergerak
  (render penuh 216 frame = 46 dtk). Demo 3 video asli user (15,5 dtk, tanpa LLM): render total 82 dtk,
  ketiga video tampil, emoji berwarna, animasi masuk per kata dan pudar di akhir.
- PENGHALANG: kuota harian model gratis OpenRouter (50 permintaan/hari) habis 24 Sep ~10:30; Hermes
  dan pipeline memakai key yang sama, dan satu giliran Hermes memakan ~10 permintaan. Reset 25 Sep
  07:00 WIB. Tambah kredit $10 di OpenRouter -> 1000/hari (menurut pesan error OpenRouter sendiri).

## 24 Sept (siang) — potong bagian goyang, musik terdengar, perbaikan pilihan audio

- `scripts/visual_quality.py`: gerak global (phase correlation), kecocokan antar-frame, ketajaman
  (variansi Laplacian) pada 10 fps/160 px -> rentang goyang/oleng/buram. Klip nyata user: stabil
  <= 20 %lebar/dtk; video ke-3 kamera mengayun ke lantai 170-317 %lebar/dtk (terdeteksi 1,75-3,6 dtk,
  diverifikasi visual). Ambang 45. Gelap TIDAK dihitung cacat (video malam sah).
- Mode audio asli/mute: bagian buruk dibuang dari rencana, sambungannya diberi fade; bagian buruk
  yang berisi UCAPAN dipertahankan dan dilaporkan. Mode voice-over AI: dipakai rentang layak
  terpanjang (diloop bila perlu). Gagal ukur dicatat, tidak dianggap bersih. `--visual-cut off`.
- MUSIK TIDAK TERDENGAR (keluhan user) -- penyebab terukur: ducking terpicu terus oleh keramaian;
  nada uji di trek musik -22,7 dB = identik kontrol tanpa musik. Kini ducking hanya bila ada
  ucapan (scene ber-`words`), dan musik 4 dB di bawah suasana (MUSIC_BELOW_AMBIENT_DB).
- Opsi audio 5C berlabel "suara suasana tetap ada" padahal mode ai menggantinya -- diperbaiki,
  dan kini membawa `music: on` (agen sempat menebak `--music off` sendiri). Skill: dilarang
  menambah flag di luar pemetaan. Berkas audio di `--media-path` otomatis jadi `--music-file`.
- TTS: TTS_PROVIDER tidak diset -> tiap render mencoba RelayRouter (kuota habis) lalu jatuh ke
  edge-tts (gratis). Suara id-ID tersedia: Gadis (P), Ardi (L).

## 24 Sept (malam) — voice-over ElevenLabs

- `TTS_PROVIDER=elevenlabs` (paket gratis 10.000 karakter/bln), cadangan otomatis edge-tts dengan
  jenis suara yang sama; kegagalan permanen (401/402/403, kuota) tidak diulang dan DILAPORKAN
  (`render_status.suara`). Suara pustaka ditolak API di paket gratis (HTTP 402) -> hanya suara
  bawaan, dipilih dengan mengukur WER Whisper pada kalimat Indonesia: Bella/Matilda (wanita),
  Liam/Chris/George (pria). `--voice pria|wanita` + `--voice-persona`.
- Penghitung kuota ElevenLabs tertunda (0 setelah ~900 karakter) -> cek kuota hanya pengaman kasar.
- Uji nyata (3 video user, Liam energik, B-roll, musik, teks animasi, potong goyang): sukses, 19 dtk.
  Whisper mendengar naskah utuh; hanya "Laksamana" terdengar "laksa mana".
- Gangguan jaringan VPS 13:40-15:28 WIB (Telegram + OpenRouter putus, server sehat). Temuan:
  3.698 login SSH gagal/12 jam, root+password diizinkan, tanpa firewall/fail2ban -- belum diubah.

## 24 Sept (malam) — naskah gaya kreator + teks mengikuti suara

- Referensi user (TikTok kreator, audio utuh; videonya terunggah terpotong ~35/50 dtk): teks satu
  kata per tampilan, kapital tebal, berganti TEPAT mengikuti ucapan (~0,38 dtk/kata, tanpa efek);
  naskah lisan menyapa penonton, ~2,6 kata/dtk.
- `scripts/naskah.py`: aturan gaya di prompt + pemeriksa kode (frasa deskriptif, kalimat > 16 kata)
  -> maks. 1 tulis ulang (mode ai). Naskah hambar hasil nyata sebelumnya tertangkap semua ciri.
- Waktu per kata dari TTS: ElevenLabs `/with-timestamps` (per karakter) dan edge-tts WordBoundary.
  Mode ai: teks per-scene LLM diganti teks narasi gaya `kata` (Montserrat, 66% tinggi); judul
  statis tetap. `petakan_kata` memetakan ejaan tulisan ke waktu naskah lafal.
- Uji nyata: kata berganti mengikuti narasi Liam; naskah menyapa ("Kalian, ini bukan sekadar
  datang dan duduk...") dan lolos pemeriksa tanpa tulis ulang.

## 24 Sept (malam) — BrainIdea menonton klip, draf 2 naskah sebelum render, motion graphic

Permintaan user: BrainIdea membaca video, memahami konteksnya, dan memberi saran naskah
SEBELUM render; lalu motion graphic penjelas seperti video referensi (dibuat Claude dengan Remotion).
Keputusan user: 2 alternatif (pilih lalu boleh edit), tingkat grafik "sedang", model tetap gratis.

- **Penyebab naskah hambar yang terukur**: vision hanya mengirim SATU frame (detik ke-1) per video.
  Sekarang lembar kontak 2x2 = 4 momen berurutan per klip, bagian goyang dilewati (analisis goyang
  di-cache, dipakai bersama renderer). Prompt juga memuat fakta per klip (durasi, cuplikan ucapan
  atau "tanpa ucapan", bagian yang dibuang) dan contoh ritme kreator dari transkrip video referensi
  (`config/gaya_naskah/`). Model mengembalikan `pemahaman_bahan` (1 kalimat per klip) yang
  ditampilkan ke user supaya salah tafsir terkoreksi sebelum render.
- **Draf**: `hermes_render.py --draft` (ContentInsight + brief, tanpa render) menghasilkan 2 varian
  gaya berbeda, masing-masing diperiksa `naskah.periksa`. Render: `--draft-id X --varian A
  [--naskah ...]`, dan brief TIDAK dibuat ulang. Mode audio/durasi/teks statis/konteks dikunci di
  draf; flag gaya boleh diganti. Draf ditolak bila milik chat lain, lebih dari 24 jam, bahannya
  berbeda, atau sudah dirender (O_EXCL; dilepas bila render gagal).
- **Temuan nyata**: model gratis menyisipkan huruf Mandarin ("lalu确认 datang bareng") di naskah
  Indonesia. Kini ditandai pemeriksa (huruf non-Latin) dan memicu tulis ulang; kalau tersisa,
  disebut di pesan draf.
- **Motion graphic** (`remotion/src/MotionOverlay.jsx`, `scripts/motion_plan.py`): kartu pembuka,
  sorot, ikon, langkah "n/N", label, kartu ajakan; gaya kartu kaca gelap bercahaya ungu seperti
  referensi.
  - LLM mengusulkan jenis/teks/kata jangkar. Kode menegakkan katalog dan batas panjang, menolak angka
    yang tidak ada di permintaan user atau ucapan asli, memasang waktu dari kata yang DIUCAPKAN
    narasi TTS, dan menjaga tingkat sedang (maksimal 4 elemen, tanpa tumpang tindih, berjarak
    minimal 1,2 dtk). Zona teks narasi (58-74% tinggi) tidak ditutup.
  - Mode suara asli: hanya kartu pembuka & ajakan.
- **Waktu (terukur, 1080x1920, VPS 4 core tanpa GPU)**:
  - Chromium sekitar 0,3-0,5 dtk/frame, dan sekitar 11 dtk overhead per `renderMedia`.
  - Dua optimasi: animasi keluar dibuat sebagai pudar alpha oleh ffmpeg pada gambar diam, dan frame
    masuk dikurangi 16 -> 12. Hasilnya, 6 elemen turun dari 150 ke 78 frame Chromium.
  - Encode ulang 14 dtk video = 18,5 dtk, jadi penempelan grafik menumpang encode teks (satu
    komposit: overlay grafik lalu drawtext narasi), tanpa encode tambahan. Dites: tidak ada
    `run_ffmpeg` tambahan.
  - Timeout tahap render naik 480 -> 630 (satu render Remotion lagi dengan batas keras 120 dtk).
- **Uji nyata (3 video user, voice-over ElevenLabs, 24 Sep malam)**:
  - Draf 147 dtk: pemahaman 3 klip akurat, 2 gaya berbeda, rencana grafik valid.
  - Render varian A 106 dtk (video 22 dtk): 5 elemen, tidak ada yang dibuang. Chip "DONOR DARAH"
    tidak ada di detik 4,2 dan muncul setelah kata "donor". Langkah 1/2 dan 2/2 muncul saat
    "teman" dan "Daftarin". Teks narasi tetap utuh.
  - Render varian B ubahan (sebelum grafik): Whisper membaca persis naskah ubahan user.
- Biaya: ContentInsight (agent5) dipindah dari `openai/gpt-4o-mini` berbayar (±25 rb token/run)
  ke `LLM_MODEL` gratis (baris `KEYWORD_MODEL` di `.env` dijadikan komentar). Uji nyata: kata
  kunci relevan, 5 dtk, $0. Yang masih berbayar: `EDIT_MODEL` (seleksi potongan ucapan, hanya
  di mode suara asli).

## 25 Sept — audit kode, pembersihan jalur OpenClaw, klip tidak lagi diulang-ulang

**Audit** (pyflakes + vulture + graf impor dari titik masuk aktif: `hermes_render`, `inspect_media`,
`orchestrator`, `check_locks`, `remotion/render.mjs`):
- **Dihapus, tidak dirujuk jalur aktif (±11.700 baris)**:
  - `openclaw-plugin/`, `run_and_deliver.py`, `pipeline.py`, `agent4_approval.py`, `publish.py`,
    `gateway_check.py`, `install_plugin.sh`, `openclaw.config.json`, `agents/*.md`, skill
    deskriptor tanpa kode, `make_demo_music.py`, `cost_report.py`.
  - Fungsi mati (pengirim Telegram, allowlist chat, lock approval, pembungkus transkripsi, dsb.)
    dan tesnya.
  - `notify()` kini hanya mencetak: cabang kirim-Telegram tidak pernah jalan di Hermes.
- **Celah yang ditemukan dan diperbaiki**:
  - Catatan "N bahan tanpa subtitle + penyebabnya", "teks dari tampilan saja", dan ringkasan
    seleksi hanya ada di caption OpenClaw. Jadi sejak pindah ke Hermes, user tidak pernah diberi
    tahu kenapa subtitle hilang. Sekarang `catatan_bahan` ada di hasil `hermes_render`.
  - Teks agent dari `inspect` masih menyuruh `content_factory_run` (nama tool plugin), sekarang
    flag `hermes_render`.
  - `requirements.txt` tidak memuat `numpy` (dipakai), tapi memuat `requests` (tidak dipakai).

**Klip diulang-ulang (keluhan user 24 Sep)**:
- **Penyebab**: narasi dibagi SAMA RATA per klip. Klip yang lebih pendek dari jatahnya diputar
  `-stream_loop`, dan hanya satu rentang layak per klip yang dipakai. Kasus nyata: 1,75 dtk layak,
  jatah 7,38 dtk, jadi ±4x.
- **Brief**: naskah menyesuaikan total bahan layak (sama dengan hitungan renderer). Durasi yang
  diminta user tetap menang.
- **Draf**: menampilkan "narasi ±N dtk · bahan video layak ±M dtk". Bila kurang, menawarkan kirim
  video tambahan / "stok" (Pexels, bila key ada) / biarkan. Naskah ubahan user tidak pernah
  dikoreksi otomatis.
- **Render** (`scripts/alokasi.py`): semua rentang layak dipakai, jatah "isi air" (tanpa melebihi
  sumber), foto menyerap kekurangan, lalu gerak lambat maksimal 0,8x, lalu dipakai ulang
  bergiliran (dari ujung rentang, tidak berturut-turut). `-stream_loop` dihapus. `tpad` menahan
  frame terakhir hanya sebagai jaring pengaman pembulatan. Laporannya di `pengisian`.
- **Tes**: tes piksel render penuh (klip A 1,5 dtk + B 4 dtk, narasi 5 dtk). Renderer lama gagal
  (merah terulang di detik 1,8), yang baru lolos.
- Penanda "1/2" di elemen langkah dihapus (permintaan user).
- **Uji nyata (3 video user, 25 Sep)**:
  - Draf 138 dtk. Naskah A ±13 dtk dan B ±11 dtk, dengan bahan layak ±14 dtk (sebelumnya
    naskah 22 dtk).
  - Render 100 dtk: narasi nyata 14,65 dtk, diperlambat tipis 0,93x, tanpa potongan dipakai ulang.
  - Deteksi frame kembar berjarak ≥ 2 dtk: render lama 24 pasangan (berulang tiap 1,75 dtk),
    render baru 0.

## 25 Sept (siang) — resep konten, beberapa short, montase ketukan, storyboard, pemeriksa mutu

Permintaan user: agent menangani dua kasus pokok, (1) suara asli + subtitle + B-roll/animasi +
musik, dan (2) voice AI natural + subtitle + animasi + musik. Ditambah pilihan user: (3) beberapa
short dari video panjang, (4) montase ketukan, (5) pemeriksa mutu, (6) storyboard. Model Hermes
diganti ke `nex-n2.5-mini:free`: pro macet 20 menit (provider unresponsive); mini 0,7 dtk vs
pro 23,6 dtk. `EDIT_MODEL` berbayar dijadikan komentar sehingga memakai model gratis.

- **(1) B-roll cutaway di mode suara asli**:
  - Klip stok DITIMPA sebentar saat kata jangkarnya diucapkan; audio disalin, subtitle tidak
    bergeser. Diuji: lag ≤ 3 ms, korelasi > 0,99.
  - Grafik dan B-roll dijangkarkan ke kata yang terdengar (transkrip).
  - Di mode ini grafik penjelas diturunkan ke bawah wajah. Storyboard pertama memperlihatkan
    kartu menutupi wajah pembicara.
  - Render nyata membuang SEMUA B-roll karena jangkarnya sama dengan jangkar grafik, jadi
    cutaway kini boleh bertumpuk dengan kartu.
- **(2)** BrainIdea mengusulkan `broll` (kata kunci Inggris + jangkar) per varian; wajib bila user
  memintanya. Mode AI memakai usulan itu sebagai kata kunci sisipan.
- **(3) Beberapa short**: satu panggilan LLM membagi kandidat ucapan. Kode menjamin potongan tidak
  dipakai dua short dan tiap short 15-60 dtk. Draf "Short 1..N" dirender `--short semua|A,C`,
  diklaim sekali, dan short yang gagal tidak menular.
- **(4) Montase**:
  - `music_mood.ketukan`: fase ketukan terkalibrasi ±2 ms pada klik 90/120/140 BPM.
  - Tempo dihaluskan 0,05 BPM, karena tempo kasar 99,5 vs 100 BPM di uji nyata akan melenceng
    ±0,3 dtk di video 60 dtk.
  - Uji nyata (3 klip food court + lagu 100 BPM): sambungan tepat kelipatan unit ketukan; musik
    terukur mulai 0,585 dtk (diminta 0,588).
  - Potongan kelipatan ketukan, hard cut, musik mulai di ketukan pertama.
  - Diuji dari piksel & audio keluaran: sambungan ≤ 1 frame dari ketukan.
- **(5) Pemeriksa mutu**: kenyaringan (loudnorm + limiter), frame hitam/beku, teks terpotong/zona UI
  (dua salah tanda di render nyata diperbaiki: peredup kartu & pencuplikan meleset frame), dan
  durasi. Render nyata: -24,2 → -17,1 LUFS / -1,9 dBTP.
- **(6) Storyboard**: 8 panel/varian (frame bahan, contoh teks, still Remotion asli, thumbnail
  B-roll Pexels) ±5 dtk per varian, plus contoh suara kalimat pertama.
- **Uji nyata Kasus 1** (6 video bicara user, brief dari draf pagi):
  - Cutaway Pexels 'software dashboard' tampil di detik 19,8 saat "dashboard" diucapkan, dengan
    kartu grafik di atasnya.
  - Pemeriksa mutu: suara -24,2 → -17,1 LUFS. Peringatan benar: baris subtitle lebar masuk area
    tombol kanan TikTok.
- **Kendala 25 Sep**: key OpenRouter (dipakai Hermes & pipeline) **kedaluwarsa** ("API key
  expired"). Draf baru dan bot Telegram berhenti sampai key diganti. Uji nyata render memakai brief
  draf yang sudah ada.
- **Uji nyata ujung ke ujung dengan key baru (25 Sep sore)**:
  - Draf 122 dtk. Pemahaman 6 klip akurat; B-roll diusulkan 3 per varian; storyboard A/B
    terkirim.
  - Render 188 dtk: 2 cutaway Pexels, 4 elemen grafik, dan musik. Pemeriksa mutu lolos (-24,2 →
    -17,1 LUFS).
  - Temuan 1: klip stok 'error message' tampil sebagai layar merah polos. Klip dengan detail
    luma < 12 kini ditolak (klip merah terukur 8,2; kandidat lain 42,9 dipakai), dari 2 kandidat
    per kata kunci.
  - Temuan 2: huruf Mandarin "报错" bocor ke caption varian B. Pesan draf kini menandai huruf
    asing di judul/caption juga.

## 30 Sept — video tidak terkirim 2 jam: model cadangan diganti setelah DIUKUR

**Kejadian (29 Sep 23:28 - 30 Sep 01:27).** Render SUKSES (`video_fe04a6ec.mp4`, 15,3 dtk, QA
lolos) tapi video tidak pernah sampai ke user. Dua sebab terpisah:

1. **Agent mengarang perintah kirim.** SKILL lama hanya bilang "BUKAN skrip ini", jadi agent
   mencari CLI lain: `telegram send` -> `tg` -> `telegram-cli` -> `apt-get install` (semua exit
   127). Padahal 8 menit sebelumnya ia BERHASIL mengirim 2 foto storyboard secara native
   (`Sending media group of 2 photo(s)`).
2. **Rantai cadangan jatuh ke model yang tidak layak agent.** deepseek tumbang 23:30 -> agnes
   (balasan 17-56 token) -> `nemotron-3-nano-omni-30b-a3b-reasoning:free`: **28.260 token
   keluaran** berisi "18.50 tbc" berulang, pesan Telegram 35.000 lalu 56.000 karakter, dan
   isi nalar bocor sebagai jawaban ("We need to see the actual output...").

**Yang diukur (30 Sep pagi).** Skenario nyata direproduksi (konteks panjang + 3 tool gagal exit
127), dibandingkan panduan SKILL lama vs baru; plus keandalan 5 panggilan/model:

| Model | Andal | Latensi | Panduan lama | Vision |
|---|---|---|---|---|
| `nemotron-3-super-120b-a12b:free` | 5/5 | 1,5 dtk | bersih, sebut path | ya |
| `qwen3.8-27b:free` | 5/5 | 1,8 dtk | **bocor nalar**, tak sebut path | ya (bocor) |
| `gemma-4-31b-it:free` | 0/5 | - | - | - |
| `deepseek-v4.1-flash:free` (utama lama) | 0/9 | - | - | - |
| `agnes-2.0-flash` | 1/5 | 0,9 dtk | - | - |
| `nemotron-3-nano-...-reasoning:free` | 3/5 | 4,2 dtk | rusak di produksi | - |
| `nemotron-3.5-lightning:free` | - | 219 dtk | **omong kosong** 1.500 token | - |

**Tindakan.** Utama -> `nemotron-3-super-120b-a12b:free`; cadangan: qwen3.8-27b, deepseek,
gemma-4-31b. Dibuang: agnes-2.0-flash, nemotron-nano-reasoning. SKILL Langkah 6 kini MELARANG
KERAS kirim lewat terminal dan mewajibkan: gagal kirim = balas path, berhenti (commit `6e94a2f`).

**Pelajaran alat ukur (aturan #1).** Tes pertama meloloskan model yang terbukti rusak, karena
terlalu mudah. Tes baru memakai konteks panjang + tool gagal berulang, dan dibuktikan dulu bisa
menangkap kegagalannya (qwen: bocor nalar dengan panduan lama, bersih dengan panduan baru).
Parser tes sendiri sempat salah: router menambah spasi di depan JSON dan `data: [DONE]` di
belakangnya, sehingga model sehat terbaca "rusak" -- diperbaiki dengan `raw_decode`.

## 25-26 Sept — key baru, model gratis dicabut, rantai model, uji nyata ulang

- Key OpenRouter baru terpasang (tanpa kedaluwarsa). Lalu `nex-agi/nex-n2.5-*:free` **dicabut**
  dari OpenRouter ("No endpoints found"). Qwen/Gemma gratis bergantian 429 (antrean penyedia
  bersama, bukan kuota key).
- **Rantai model**:
  - Pipeline: `LLM_MODEL=qwen/qwen3.8-27b:free` + `LLM_FALLBACK=gemma-4-31b-it, nemotron-3-ultra`.
    Model yang tidak tersedia (404/429/502/503) dilewati; 2 putaran.
  - Pesan bergambar: model teks-saja PALING AKHIR, diberi tahu ia tidak melihat video; brief
    menandai `tanpa_gambar` dan draf mengakuinya.
  - Hermes: `fallback_providers` yang sama.
- **Temuan uji nyata & perbaikan**:
  - Model teks-saja mengarang "petugas medis memeriksa calon pendonor" untuk video food court.
    Penyebab: model itu dipakai di putaran pertama.
  - Naskah mengarang "cuma 15 menit", sekarang diperiksa `naskah.periksa(sumber=...)` (angka
    yang tidak ada di permintaan user = masalah, ditulis ulang).
  - B-roll mode AI tak relevan ('people-holding-dog' untuk donor darah). Kini judul klip harus
    berbagi kata bermakna dengan kata kunci AI; kata kunci user tidak disaring. Urutan kandidat
    bergiliran menurut peringkat, bukan ekor daftar gabungan.
  - Contoh suara draf gagal (`auto_render` tidak di jalur impor hermes_render), sudah diperbaiki.
  - Subtitle & kata narasi maks 74% lebar (tombol kanan TikTok). Pemecah kalimat kini memakai
    `wrap_text` sendiri: taksiran lama memotong kata jadi "..." setelah dipersempit.
  - Jangkar grafik/B-roll boleh frasa ("API key"), dicocokkan sebagai kata berurutan.
  - Short: pesan draf jujur bila jumlah jadi < diminta.
- **Hasil nyata**:
  - Suara asli + B-roll: peringatan tombol kanan hilang.
  - 2 short dari 6 video (32 & 18 dtk), masing-masing dengan cutaway + grafik; QA lolos.

## 30 Sept 09:26 — "kenapa seperti ini terus": satu jatah akun, bukan model yang rusak

Pagi 30 Sep bot kembali menggantung ("Provider temporarily unavailable — retrying in 64s, cycle
1/5") walau rantai modelnya baru diganti malam sebelumnya. Penelusuran menemukan **penggantian
model kemarin memang tidak bisa menolong**, karena salah sasaran.

**Sebab struktural.** Semua model `:free` OpenRouter memakai **SATU jatah milik AKUN: 50
permintaan/hari** (`limit_source: openrouter_free_tier_daily`, `X-RateLimit-Limit: 50`,
`Remaining: 0`, reset 00:00 UTC / 07:00 WIB). Jadi rantai cadangan berisi empat model `:free`
tidak menambah ketahanan sedikit pun — kalau satu kena 429 karena jatah, semuanya kena. Terukur
09:26:06-09:26:13 (7 detik): nemotron-super, qwen, gemma menjawab 429 identik; probe langsung
pukul 09:37 memastikan **6 model `:free` habis serentak**.

**Pemicunya.** Sesi macet 29 Sep 23:08 (`20260929_230820_e39f64c1`, yang mengarang `telegram-cli`)
masih hidup pagi ini. Antara reset 07:00 dan pesan user 09:26 ia membuat 30 panggilan — 22 di
antaranya pemadatan konteks — dan **menghabiskan jatah 50 yang baru direset** sebelum user
mengirim apa pun. Log: 46 error 429 pukul 00:xx, 16 pukul 01:xx, 16 pukul 08:xx, 29 pukul 09:xx.

**Yang sudah dicek dan BUKAN penyebab** (aturan #1 — kontrol negatif juga perlu):
- cron Hermes: scheduler berdetak tapi **nol job** (`executions.db` tak berubah sejak 21 Sep).
- gateway kedua (`pid 3670`, `/opt/hermes`, `HERMES_HOME=/opt/data`): idle, **tanpa `config.yaml`
  dan tanpa `.env`**, nol koneksi keluar, nol log hari ini.

**Hasil ukur 19 model di router (30 Sep 09:37-10:05).**

| Model | Keadaan | Andal | Tool | Vision | Konteks |
|---|---|---|---|---|---|
| `openrouter/stealth/space-bunny-alpha` | **hidup** | 5/5 | ya | ya | 200K |
| 6 model `openrouter/*:free` | 429 jatah akun | — | — | — | — |
| `tokenharbor/deepseek-v4.1-flash:free` | jatah 7 hari habis s.d. 5 Okt 02:24 UTC | — | — | — | — |
| `agnes/agnes-2.0-flash` | 1 lolos lalu 429 | 0/5 | — | — | — |
| `agnes/agnes-1.5-flash` | `model_not_found` | — | — | — | — |
| `openrouter/inclusionai/ling-3.0-flash-fin:free` | 404 tidak tersedia | — | — | — | — |
| `openrouter/thinkingmachines/inkling-small:free` | 403 | — | — | — | — |
| `openrouter/typesafe/jev-1.13` | 400 "decisions model", bukan chat | — | — | — | — |
| `lm-agent` | 400 unsupported | — | — | — | — |
| `tokenharbor/claude-*`, `gpt-6-*`, `grok-4.7` | berbayar, tidak diprobe tanpa izin | — | — | — | — |

**Uji beban** (skenario nyata 29 Sep: konteks panjang + 3 tool_call kirim gagal exit 127):
`space-bunny-alpha` **0/15 mengulangi perintah CLI** dan **0/15 loop** — mode gagal semalam tidak
tereproduksi. Kebocoran nalar 1/15 dengan panduan SKILL baru saja; 0/6 setelah SOUL diberi satu
baris larangan menulis proses berpikir. Untuk pipeline: `response_format=json_object` **5/5 sah
menurut skema**, dan vision benar — pada frame detik 3 draf itu ia membaca teks overlay
"SPESIAL", yang memang ada di sana (kontrol positif).

**Perubahan.**
- `~/.hermes/config.yaml`: utama -> `openrouter/stealth/space-bunny-alpha` (di luar jatah 50/hari);
  cadangan -> nemotron-super, qwen, deepseek, dengan komentar yang menjelaskan jatah bersama.
  `gemma-4-31b` dibuang (0/5 walau jatah ada). Diff-kontrol: 29 kunci tetap 29, hanya 2 berubah.
- `~/.hermes/SOUL.md`: larangan menulis proses berpikir dan menjawab Bahasa Inggris.
- `scripts/cek_kuota.py`: sejak pindah ke router lokal (tanpa `/key`) pemeriksa **selalu** menjawab
  `bukan_openrouter` — agent buta lagi, persis masalah 26 Sep. Sekarang ia mengukur: satu
  permintaan 1-token ke model utama, dan angka jatah dibaca dari header di badan 429
  (`buka_bungkus` membuka lapisan JSON ter-escape dari router; tanpa itu `raw_decode` selalu
  gagal). Jatah `:free` hanya diprobe kalau perlu, karena probe yang berhasil memakai 1 dari 50.
- `config/pricing.json`: rantai model baru diisi — varian `:free` $0 (dengan sumbernya),
  `space-bunny-alpha` masuk `tanpa_harga_per_token` karena harganya tidak dipublikasikan.
  Ini menutup `test_model_yang_dipakai_pipeline_punya_harga` yang sejak 25 Sep di-deselect.
- SKILL: aturan "satu jatah bersama" + cara pakai `--gratis`.

**Belum dikerjakan.** `.env` repo (`LLM_MODEL`/`LLM_FALLBACK`) **masih rantai lama yang mati**
(deepseek habis s.d. 5 Okt + agnes/gemma/nemotron-nano). Jadi pipeline video masih akan gagal
memanggil LLM walau agent Telegram sudah jalan. Suntingan diblokir classifier ("Production
Deploy"); menunggu user.

**Tes.** 1139 lolos, 0 gagal. Tiga mutasi pada `cek_kuota` (matikan `buka_bungkus`, probe jatah
tanpa alasan, reset ditebak bukan dari header) semuanya tertangkap.

## 1 Okt — hemat token: yang boros ternyata agent, bukan pipeline

**Diukur dulu, dari catatan router** (`~/.9router/db`, tabel `usageHistory`, angka ASLI per
permintaan). Sejak 28 Sep: 4,0 juta token masuk. **Agent Hermes 3,86 juta (96%)**, median 31.256
token per permintaan. **Pipeline video hanya 142 ribu (3,5%)**, median 869. Rencana awal
(mengecilkan gambar di panggilan kata kunci ContentInsight) dibatalkan: hemat nyatanya ±600 token
per draf, dan `detail: low` diabaikan penyedia (terukur sama persis). Angka "±5.300/panggilan" dulu
didominasi gpt-4o-mini (8 panggilan × ±25.700 token).

**Dua sifat router 9Router 0.5.91 yang memengaruhi semua angka token:**
- Setiap `usage` yang dikirim ke klien ditambah **2.000 token palsu** (`prompt_tokens`,
  `input_tokens`, `total_tokens`; fungsi di `app/.next-cli-build/server/chunks/5330.js`). Contoh:
  permintaan "ok" tercatat 564 di router, dilaporkan 2.564 ke kita. Jadi semua `prompt_tokens` di
  `run_log.jsonl` sejak 28 Sep lebih besar 2.000 dari kenyataan per panggilan.
- Pengaturan **Token Saver "caveman" level full** aktif: ±555 token instruksi "jawab ringkas,
  fragmen boleh, sinonim pendek" disuntikkan ke SETIAP permintaan, termasuk naskah kreator dan
  persona Klipa. Pengaturan router milik user; tidak diubah.

**Akar boros di agent: lingkaran pemadatan.** `compression.threshold_tokens` = 16.000 (bawaan
Hermes 256.000; asal perubahannya tidak tercatat di log), padahal beban dasar tiap permintaan
±21K: skema 25 tool ±10.700 + system prompt ±4.500 + skill ±5.900. Hampir tiap giliran
dipadatkan → isi skill terbuang → agent memuat ulang skill (`skill_view`) → lewat batas lagi.

| Sesi | Pemadatan | `skill_view` | Panggilan API | Pesan user |
|---|---|---|---|---|
| 28 Sep | 13 | 4 | 134 | 11 |
| 29 Sep (macet) | 5 | 6 | 49 | 8 |
| 30 Sep | 2 | 7 | 27 | 4 |

Tanpa skill di konteks, agent juga berkeliaran: membaca `music.py`/`revisi.py`/`auto_render.py`,
dan pada uji 1 Okt **mencari `*.mp4` di seluruh `~/.hermes`** sebelum akhirnya memuat skill.

**Perubahan.**
- `~/.hermes/config.yaml` (cadangan `scratchpad/config.yaml.bak-1okt`; diff-kontrol: 29 kunci tetap,
  4 berubah):
  - `compression.threshold_tokens` 16.000 → 64.000;
  - `platform_toolsets.telegram`: `hermes-telegram` (25 tool, ±10.700 token) → `terminal, file,
    skills, vision, memory, code_execution` (14 tool, ±6.000). Yang hilang tidak pernah dipakai di
    1.073 tool call Telegram: brankas login browser, `delegate_task`, web, tts, `clarify`;
  - `skills.auto_load: [content-factory]`: skill di bagian stabil system prompt, tidak terbuang saat
    pemadatan;
  - `skills.platform_disabled.telegram`: 51 skill bawaan lain (indeks 6.506 → 1.569 karakter).
- SKILL: skill sudah termuat, jadi jangan dimuat ulang dan jangan membaca kode pipeline; status kuota
  tanpa istilah teknis.
- `~/.hermes/SOUL.md`: kalimat utuh Bahasa Indonesia, tanpa kata bahasa lain dan istilah server.
- `cek_kuota.py`: kalimat `catatan` (diteruskan agent ke user) tanpa "':free'". Kata itu membuat
  model menerjemahkannya sendiri jadi "免费". Tes penjaga + mutasi tertangkap.

**Hasil, pertanyaan sama ("Bisa buat konten sekarang?"), agent sungguhan, token asli dari router:**

| | Permintaan | Token masuk | Waktu |
|---|---|---|---|
| Sebelum | 10 | 177.946 | 49 dtk |
| Sesudah | 3 | 33.788 | 12 dtk |

Uji perilaku (toolset baru):
- permintaan suara pria enerjik: 2 permintaan, tanpa membaca kode;
- "pakai model apa": persona terjaga.

Uji bahasa, 6 sampel per tahap:
- huruf Mandarin/istilah teknis: 1/6 → 1/6 (setelah SOUL) → **0/6** (setelah `catatan` dibetulkan);
- masih ada selipan gaya ringkas ("checked 08:38", "jatahRolling"), cocok dengan instruksi caveman.

**Belum / keputusan user.**
- Caveman router: menambah ±555 token per permintaan dan menekan gaya bahasa. Usul dimatikan (halaman
  Token Saver di dashboard 9Router), lalu diuji A/B mutu naskah.
- `.env` repo masih rantai model mati, sehingga `cek_kuota` melaporkan "model utama penuh".
- `reasoning_effort: high` tidak diubah (belum diukur dampaknya ke mutu).
- Pemadatan pada percakapan Telegram panjang belum teramati sejak perubahan. Cek
  `sessions.compression*` setelah pemakaian nyata.

## 1 Okt — gaya tampilan yang bisa dipilih + profil per chat

**Masalah.** Gaya tersebar di ±30 flag, dan warna tertanam di tiap komponen Remotion dengan palet
berbeda: motion ungu `#8B5CF6` + pink, panggung oranye `#E8743B` + ungu `#6D5DF5`, caption & cover
emas. Satu-satunya "paket" adalah `--subtitle-style dinamis`.

**Yang dibuat.**
- `config/gaya/*.json`, 6 preset: `klasik` (bawaan, kosong), `bersih`, `edukatif`, `elegan`, `hype`,
  `promo`. Isinya tema (warna, font judul dari daftar tertutup, sudut, cahaya, karakter gerak) dan
  knob editing yang SUDAH ada (subtitle, filter warna, font/animasi teks, SFX, zoom, logo, cover,
  motion).
- `scripts/gaya.py`:
  - validasi ketat, siap-SaaS: hex saja, font dari daftar, enum, kontras teks kartu ≥ 4,5, `url(...)`
    ditolak;
  - prioritas flag eksplisit > `--gaya` > profil chat > klasik;
  - profil per pemilik di `workspace/state/profil/` (nama berkas aman untuk label apa pun, label lain
    tidak dipakai);
  - CLI `daftar | pratinjau | pakai | lihat | lupakan`.
- `remotion/src/tema.js` + `MotionOverlay`, `CaptionDinamis`, `Panggung`, `Sampul`: token yang tidak
  disebut preset jatuh ke literal lama. Tema disuntik di SATU pintu (`overlay_remotion._jalankan_node`).
- Pratinjau: `PratinjauGaya.jsx` memakai komponen asli, satu gambar grid untuk 6 gaya, latar
  sintetis (cache dipakai bersama, jadi tidak boleh memuat bahan user), salinan baru tiap
  permintaan (gateway hanya mengirim berkas baru).
- `hermes_render.py --gaya`:
  - nama efektif disimpan di args, knob preset TIDAK, supaya ganti gaya di draf/revisi tidak
    terkunci;
  - hasil memuat `gaya_tampilan`;
  - revisi cepat menerima `--gaya`.
- Inspect: pertanyaan "gaya" menawarkan preset, dilewati bila profil chat sudah punya gaya atau user
  menyebut nama preset.

**Bukti.**
- **Identik piksel**: 33 still acuan direkam SEBELUM komponen diubah (Sampul; 7 jenis elemen motion
  di tengah animasi & diam; caption biasa/panggung; 6 ilustrasi panggung). Determinisme dicek dulu:
  render ulang tanpa perubahan 33/33 identik. Setelah perubahan, tanpa tema: **33/33 identik**.
- Tes tema kosong vs tanpa tema identik; aksen hype terukur di pil sorot dan ungu bawaan hilang;
  kertas panggung elegan lebih hangat; kata kunci promo kuning.
- Mutasi tertangkap (5/5): injeksi tema dihapus, `bacaTema` mengabaikan warna, preset menimpa flag
  user, validasi warna dilonggarkan, cek pemilik profil dihapus.
- **Uji nyata** (revisi `9cdbfb57`, 6 video berucap, mode dinamis, tanpa LLM):

  | Gaya | Waktu | RSS puncak | QA |
  |---|---|---|---|
  | klasik | 287 dtk | 1,16 GB | lolos |
  | hype | 323 dtk | 1,17 GB | lolos |
  | elegan | 330 dtk | 1,14 GB | lolos |

  - Frame Chromium identik di ketiganya (caption 351, panggung 144, motion 39).
  - Klasik + `--color-filter vivid` saja: 324 dtk, jadi tambahan waktu hype berasal dari
    filter warna `vivid` (knob lama: `unsharp` di tiap frame 1080×1920), BUKAN dari tema. Elegan memakai
    filter `warm`. Gaya tanpa filter warna tidak menambah waktu.
  - Di video: hype hijau di caption, judul panggung, dan centang; elegan serif emas + kertas hangat.

**Ditemukan & diperbaiki saat uji nyata.**
- Preset `hype` (musik "energik") membuat render DITOLAK (`musik_invalid`), karena pustaka hanya
  punya lagu tenang. Di revisi ia juga akan mengganti lagu video yang user cuma minta ganti gayanya.
  Suasana musik dikeluarkan dari preset (dan dari knob yang boleh diatur preset).
- `<Freeze>` di komposisi pratinjau 2 frame menjepit frame-nya, sehingga kartu terekam setengah
  pudar. Diganti still di frame 20, saat animasi sudah diam.
- `tests/test_draft.py` menulis catatan revisi ke `workspace/state/revisi/` ASLI: +7 berkas "DM with
  Uji" setiap suite, 187 dari 210 berkas. Jaring pengaman autouse di conftest; terukur 217 → 217.
  Berkas sampah yang sudah ada tidak dihapus (keputusan user).

**Uji agent setelah fitur gaya (1 Okt) — label chat.**
- "Gaya apa saja?": 2 permintaan, satu `gaya.py pratinjau`, label dan deskripsi disebut tanpa flag.
- "Pakai gaya elegan terus" (mode `hermes -z`, tanpa baris `Source:` konteks sesi): agent menggali
  `run_log`/state, lalu **menyimpan profil untuk "DM with Stringless"** (chat user sungguhan): 11
  permintaan, 214 ribu token. Profil itu dihapus (sebelumnya tidak ada).
- Sebab: satu-satunya kemunculan label itu di system prompt adalah CONTOH di SKILL. Label yang sah
  ada di baris `**Source:** Telegram (DM with <nama>)` dari konteks sesi gateway Hermes.
- Contoh diganti label fiktif, plus aturan: label HANYA dari baris Source; tidak ada -> menolak.
  Uji ulang: 2 permintaan, ±18 ribu token, menolak, tidak ada profil tertulis.
- Belum teruji: jalur positif lewat Telegram sungguhan (perlu pesan dari chat).
- Deskripsi preset Promo tidak lagi menyebut musik (sudah dikeluarkan dari preset).

## 2 Okt — carousel Instagram/TikTok bergaya

**Yang dibuat.** `scripts/carousel.py` + `remotion/src/Carousel.jsx` (1 frame = 1 slide) +
`overlay_remotion.render_carousel`.
- Sumber: teks user (`--teks`), transkrip + naskah video (`--dari-run`, kepemilikan dicek seperti
  revisi), foto user (`--foto`, wajib di cache Hermes), frame berwajah dari video, foto Pexels
  (`--stok`).
- Naskah: satu panggilan LLM; KODE memvalidasi batas kata per jenis (hook / isi / daftar /
  statistik / kutipan / cta), hook di depan dan cta di belakang, 3-10 slide. Angka statistik dan
  kutipan HARUS ada di sumber (aturan #5). Tulis ulang paling banyak sekali; slide yang tetap salah
  dibuang bila strukturnya masih utuh.
- Tema = preset gaya yang sama dengan video. Ukuran IG 1080×1350, TikTok 1080×1920. Keluaran JPEG
  (foto TikTok hanya menerima JPG/WEBP) + caption + hashtag. Memakai lock render yang sama.
- QA diukur dari lapisan teks yang dirender terpisah (latar transparan): teks di luar kotak aman,
  di zona UI TikTok (`qa_video.ZONA_UI`), di atas wajah, dan kontras median teks vs latar (≥ 3).

**Ditemukan QA saat uji, lalu diperbaiki.**
- Preset bersih: angka statistik putih di atas latar putih (kontras 1,0). Warna `kunci` preset dibuat
  untuk teks di atas video. Di latar terang sekarang dipakai gradien aksen (kontras 4,1).
- Slide pembuka berfoto wajah: judul meluap ke atas sampai dagu. Ukuran huruf sekarang diskalakan
  terhadap tinggi kotak aman yang tersisa.

**Bukti.**
- 37 tes, termasuk render sungguhan dua ukuran dan regresi untuk kedua cacat di atas (dengan kontrol
  positif: wajah sintetis terdeteksi). Mutasi tertangkap 4/4: cek angka dari sumber, batas kata,
  perbaikan latar terang, penskalaan huruf.
- Uji nyata dengan LLM sungguhan (model dialihkan lewat env proses, `.env` tidak disentuh):

  | Kasus | Waktu | Slide | QA |
  |---|---|---|---|
  | teks user, hype, IG + TikTok | 43 dtk | 6 + 6 | lolos |
  | `--dari-run 9cdbfb57`, elegan, IG | 36 dtk | 6 | lolos |

  Isi setia pada sumber (tanpa angka/klaim tambahan); versi video memakai foto pembicara dengan teks
  di bawah wajah.

**Belum.**
- `.env` masih rantai model mati, jadi lewat agent carousel akan gagal di panggilan LLM sampai `.env`
  diganti.
- Jalur agent lewat Telegram belum teruji (perlu pesan dari chat).
- SKILL kini 27 KB dan termuat di tiap permintaan (±1.200 token lebih banyak dari sebelum fitur gaya
  dan carousel); pemecahan SKILL masih tertunda.
- `--stok` teruji dengan jaringan dipalsukan saja.

## 2 Okt — posting Instagram/TikTok setelah persetujuan (adapter Zernio, BELUM diuji langsung)

**Yang dibuat.** `scripts/terbit.py`, ditulis dari dokumentasi Zernio (dibaca 2 Okt): `GET /accounts`,
`POST /media/presign` + `PUT`, `POST /posts`, `GET /accounts/{id}/tiktok/creator-info`.
- Dua langkah. `siapkan`: kepemilikan run/carousel (chat lain ditolak), batas platform (Reels ≤ 90
  dtk, carousel IG ≤ 10, caption), akun terhubung (tidak ada atau ganda → ditolak, tidak ditebak),
  lalu pratinjau untuk user. Belum ada unggahan. `kirim`: butuh id itu + kalimat persetujuan user,
  pratinjau ≤ 30 menit, sekali pakai (diklaim sebelum jaringan).
- TikTok bawaan DRAF (inbox). Terbit langsung hanya dengan privasi dari pilihan akun itu. Narasi AI
  diberi `video_made_with_ai`. Instagram langsung terbit (API-nya tanpa draf).
- Tanpa penjadwalan (`publishNow`, tidak pernah `scheduledFor`).
- API key hanya ke host Zernio, tidak ke URL unggah.
- Hanya penolakan 4xx yang berarti "pasti tidak terbit". Waktu habis, putus, 5xx, atau balasan tak
  terbaca → `tidak_pasti`, tidak diulang, user diminta cek akunnya (aturan #7).
- Sukses dicatat ke `publish_history.json` (`PUBLISHED`/`DRAFT`, `publish_id`). Analitik tetap
  `NO_DATA`: pengambilan metriknya belum dibuat.

**Bukti.** 27 tes dengan layanan palsu yang merekam tiap permintaan (bentuk badan, arah key, tanpa
unggah di langkah 1). Mutasi tertangkap 5/5: gerbang persetujuan, key ke URL unggah, 5xx dianggap
pasti gagal, kirim dua kali, TikTok bawaan publik.

**Uji agent** (`hermes -z`, tanpa label chat): "posting video terakhir ke instagram, langsung aja" →
0 tool call; agent menyatakan "langsung aja" belum persetujuan karena pratinjau belum tampil. Ia
sempat meminta user mengetikkan label chat; sekarang dilarang di SKILL (label ketikan bisa milik
orang lain).

**BELUM.**
- Belum pernah dijalankan terhadap Zernio sungguhan: perlu `ZERNIO_API_KEY` dan akun terhubung
  (milik user). Nama field diambil dari dokumentasi lewat ringkasan, jadi langkah pertama saat key
  ada: `terbit.py periksa` (hanya membaca), lalu satu posting DRAF TikTok.
- Jalur Telegram (dengan baris Source) belum teruji.

## 2 Okt (pagi) — keadaan setelah user mengubah pengaturan

- `.env` repo sudah memakai rantai hidup (`space-bunny-alpha` + cadangan); `cek_kuota.py`: model utama
  menjawab, `bisa_jalan: true`. Catatan "`.env` masih rantai mati" di atas tidak berlaku lagi.
- Caveman 9Router dimatikan user. Terukur di catatan router, permintaan "ok" yang sama: 564 → 157
  token masuk, jadi caveman memakan **407 token per permintaan** (perkiraan ±555 sebelumnya terlalu
  tinggi). 6 sampel "Bisa buat konten sekarang?": 0/6 huruf asing/istilah teknis, kalimat utuh.
  Bukan A/B bersih: hari ini model utama hidup sehingga jawabannya memang lebih sederhana. Masih ada
  salah ketik kecil dari modelnya sendiri di 3/6 sampel ("Bisa,Aku", "bruntikan").
- Ponytail 9Router TIDAK dinyalakan: isinya instruksi "lazy senior developer / YAGNI / code first"
  untuk agent penulis kode, tidak relevan untuk Klipa.
- Zernio: `terbit.py periksa` dijalankan user dengan key asli -> `ok: true`, 0 akun. Autentikasi dan
  `GET /accounts` terbukti jalan; akun Instagram/TikTok belum dihubungkan.

## 2 Okt (siang) — uji Telegram pertama carousel, gaya kustom, latar carousel

**Uji Telegram sungguhan (user).** Carousel 6 slide gaya Hype jadi dan terkirim sebagai album; label
chat benar (`DM with Stringless`, dari baris Source); agent melaporkan QA slide 3 apa adanya.

**Cacat slide 3 (diperbaiki).** "Perintah AI yang / Langsung Kepakai": dua baris sama-sama 16
karakter, tapi baris kedua lebih lebar. `layout.susunBaris` mengukur hanya baris dengan karakter
terbanyak, sehingga baris kedua terpotong di tepi. Opsi baru `ukurSemua` mengukur tiap baris; dipakai
carousel saja, jadi overlay video tetap 33/33 identik dengan acuan. Tes regresi gagal sebelum
perbaikan (179 piksel di luar kotak), lolos sesudahnya.

**Gaya kustom** (`gaya.py kustom`): racikan user di atas preset dasar, disimpan di profil chat sebagai
gaya `kustom`, berlaku untuk video dan carousel.
- Cukup satu warna merek: `palet_dari_aksen` (kode, bukan model) menurunkan aksen2, sorot, dan gradien
  kata kunci (dibuat terang karena tampil di atas video).
- Divalidasi sebelum disimpan (hex, font daftar tertutup, kontras teks kartu); tidak sah -> profil
  tidak berubah. Ubahan berikutnya menambah. Dasar yang hilang -> render jatuh ke klasik + sumbernya
  menyebut itu.
- Pratinjau satu kartu dikirim bersama hasil perintah.

**Latar carousel** (`--latar <path>` / `--latar stok`): satu gambar untuk semua slide, diburamkan
ringan + lapisan warna tema 58%. `--latar stok` memakai `kata_kunci_latar` dari model (divalidasi) ke
Pexels; gagal -> carousel tetap jadi polos dan disebut di `catatan`.
- Uji nyata Pexels + LLM: 46 dtk, foto "minimal morning desk", QA lolos (kontras 11-16 pada lapisan
  72%; setelah ditipiskan ke 58% tema gelap tetap > 9).
- QA menangkap: preset bersih di atas latar terang, angka statistik kontras 2,7. Di latar terang
  sekarang dipakai aksen penuh (3,5).

**Bukti.** +16 tes (1276 total), mutasi 4/4 tertangkap (validasi kustom, kontras latar terang, latar
ke semua slide, ukur tiap baris).

**Belum.** Gaya kustom dan `--latar` lewat Telegram belum dicoba user.

## 2 Okt (sore) — kamus istilah; pemecahan SKILL dibatalkan

**Kamus istilah per chat** (`scripts/kamus.py`, disimpan di profil yang sama dengan gaya).
- Dua lapis: ejaan benar ikut dibiaskan ke Whisper (paling depan di prompt), dan KODE mengoreksi hasil
  transkripsi: rangkaian kata yang cocok dengan bentuk salah digabung jadi satu kata dengan `start`
  kata pertama dan `end` kata terakhir (render merujuk kata lewat waktu, jadi subtitle tetap
  sinkron). Batas kata dijaga ("opencloudy" tidak disentuh), pola terpanjang dulu, kapital dirapikan.
- Diterapkan saat transkripsi DAN saat render membaca brief, jadi revisi cepat video lama ikut
  terkoreksi. `hermes_render --revisi ... --kamus` membuat "betulkan ejaan" sah sebagai revisi
  (tanpa itu `revisi_kosong`); kamus kosong -> `kamus_kosong`.
- Carousel dari video (`--dari-run`) memakai ejaan kamus.
- Bukti: 20 tes, mutasi 4/4 (waktu akhir kata gabungan, batas kata, pola terpanjang, pembersihan env
  antar chat). Uji nyata revisi `9cdbfb57` dengan istilah uji "hermes" -> "Hermes Agent":
  `kamus.diganti: 3`, kata kunci caption berubah, QA lolos, tanpa LLM.

**Pemecahan SKILL dibatalkan (diukur dulu).** SKILL 31 KB (±7.800 token) termuat tiap permintaan.
Bagian yang bisa dipindah ke berkas rujukan (carousel, posting, detail gaya, revisi) ±8.800 karakter =
hemat ±1.900 token/permintaan. Tapi tiap kali rujukan dibutuhkan agent harus memanggil `skill_view`
lagi (±17.000 token satu permintaan), dan rujukan yang dimuat lewat tool ikut terbuang saat pemadatan
lalu dimuat ulang -- lingkaran yang diperbaiki 1 Okt. Rugi untuk sesi carousel/posting, untung kecil
untuk sesi video murni. Dibiarkan utuh.

**Belum.** Analitik nyata: belum ada akun terhubung di Zernio dan belum ada posting, jadi tidak ada
metrik untuk diambil atau diverifikasi. Kamus, gaya kustom, dan `--latar` lewat Telegram belum dicoba
user.
