# AIHackfest — Content Factory

Pipeline yang menyusun **foto/video mentah milik user** menjadi konten vertikal siap tayang
(voice-over AI atau suara asli, subtitle, teks animasi, motion graphic, musik), dipesan lewat
chat Telegram ke agent **Hermes**. User memilih naskah dulu sebelum video dirender; tidak ada
publikasi otomatis ke platform mana pun.

Arsitektur: [ARCHITECTURE.md](ARCHITECTURE.md). Riwayat & hasil uji: [STATUS.md](STATUS.md).

## Tahap

| Tahap | Script | Input → Output |
|---|---|---|
| 📊 ContentInsight | `scripts/agent5_insight.py` | isi bahan + permintaan → kata kunci → tren nyata (`trend_pool.json`) |
| 🔍 TrendAnalysts + 💡 BrainIdea | `scripts/agent1_2_brief.py` | bahan (lembar kontak, transkrip) + tren → `creative_brief.json` (2 varian di mode draf) |
| ✍️ ContentMakers | `scripts/agent3_render.py` → `skills/video_generator/auto_render.py` | brief → `workspace/drafts/video_{run}.mp4`, `render_status.json` |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # lalu isi nilainya
```

`.env` yang dibutuhkan:

| Variabel | Wajib | Keterangan |
|---|---|---|
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | ya | penyedia chat OpenAI-kompatibel (sekarang OpenRouter) |
| `LLM_MODEL` | ya | model semua panggilan chat (sekarang `nex-agi/nex-n2.5-mini:free`); `KEYWORD_MODEL`/`EDIT_MODEL`/`BRIEF_MODEL` opsional per tahap |
| `ELEVENLABS_API_KEY` + `TTS_PROVIDER=elevenlabs` | tidak | voice-over ElevenLabs; tanpa itu / kuota habis → edge-tts gratis |
| `PEXELS_API_KEY` | tidak | B-roll stok (gratis) |
| `YOUTUBE_API_KEY` | tidak | sumber tren tambahan; tanpa key hanya Google Trends RSS |

Telegram ditangani sepenuhnya oleh Hermes (`~/.hermes/`); repo ini tidak menyimpan token bot.

Bentuk video keluaran (semuanya opsional, nilai salah ketik DITOLAK di titik
masuk — sebelum lock render dan sebelum satu pun panggilan LLM, bukan diam-diam
jatuh ke default):

| Variabel | Default | Keterangan |
|---|---|---|
| `VIDEO_ASPECT` | `9:16` | `9:16` (Reels/TikTok), `1:1` (feed), `16:9` (YouTube) |
| `FIT_MODE` | `crop` | `crop` (isi penuh, tepi terpotong), `blur` (latar blur), `letterbox` (latar hitam) |
| `AUDIO_MODE` | `original` | `original` = suara asli video user; `mute` = suara asli DIBISUKAN (hanya musik yang terdengar; subtitle tetap dibuat); `ai` = voice-over AI |
| `MUSIC_SOLO_TARGET_LUFS` | `-16` | kenyaringan musik bila ia satu-satunya suara (mode `mute`). Musik latar di bawah ucapan memakai `MUSIC_BELOW_SPEECH_DB` |
| `COLOR_FILTER` | (kosong) | filter warna: `natural`, `vivid`, `warm`, `cool`, `bw`. Berlaku untuk semua mode audio dan jenis bahan (chain `eq`/`hue`/`colorbalance` ffmpeg) |
| `SPEED_FACTOR` | `1.0` | kelipatan kecepatan klip (0.5-2.0). **Hanya aktif di `AUDIO_MODE=ai`** — mode audio asli/mute tidak menyentuhnya sama sekali, supaya subtitle (dipatok ke transkrip asli) tidak lepas dari gerak bibir orang di video |
| `TEXT_ANIMATION` | `pop` | animasi teks TULISAN di layar lewat Remotion (`remotion/`, Chromium headless): `pop`, `loncat`, `geser`, `fade`, `none`. Emoji berwarna didukung. Hanya frame yang bergerak dirender Chromium; bagian diam memakai satu gambar. Gagal/lewat `TEXT_ANIMATION_TIMEOUT` (120 dtk) -> teks statis drawtext + dilaporkan. Subtitle ucapan tetap drawtext karaoke. Pasang: `cd remotion && npm install` |
| `TEXT_POSITION` | `bawah` | posisi teks TULISAN di layar: `atas`, `tengah`, `bawah`. Subtitle dari ucapan tidak ikut |
| `TEXT_FONT` | `standar` | font teks tulisan: `standar` (DejaVu), `tegas` (Montserrat ExtraBold), `modern` (Bebas Neue), `elegan` (Playfair Display), `santai` (Pacifico), `bersih` (Inter). Font bundel di `assets/fonts/` (OFL, lihat FONTS.md) |
| `BROLL` / `BROLL_QUERY` / `BROLL_COUNT` | (mati) / (judul) / `3` | sisipkan klip video stok Pexels (1-6) di sela bahan user. **Hanya `AUDIO_MODE=ai`.** Butuh `PEXELS_API_KEY` (gratis, pexels.com/api); tanpa key diminta = DITOLAK sebelum render. Kegagalan di tengah jalan (kuota/jaringan) tidak menggagalkan video, tapi dilaporkan. Klip diunduh ke folder kerja run dan dibuang; kredit kreator dikembalikan untuk caption |
| `AUTO_ZOOM` | `0` | efek Ken Burns (zoom perlahan) pada bahan GAMBAR saja; video tidak disentuh |
| `TTS_PERSONA` | `ramah` | persona suara AI, dipakai kalau `AUDIO_MODE=ai` |
| `SUBTITLE_STYLE` | `karaoke` | `karaoke` (frasa diam, kata aktif menyala kuning, kotak gelap), `karaoke-tebal` (tanpa kotak), `karaoke-kapital` (huruf besar); gaya lama `putih-kotak`, `kuning-kotak`, `putih-tebal`, `kuning` (teks menumpuk kata demi kata, seluruhnya di-center ulang tiap kata baru) |
| `SUBTITLE_FONT` | `DejaVu Sans` | nama keluarga font (bukan path); dicari lewat `fc-match` |
| `TRIM_SILENCE` | `1` | potong jeda/silence dari video user |
| `TRANSITION` | `fade` | transisi antar klip |
| `THUMBNAIL_ENABLED` | `1` | ambil cover JPG dari tengah scene pertama (disimpan di samping video) |
| `DURATION_MIN` / `DURATION_MAX` | `10` / `60` | rentang durasi yang boleh diminta user; di luar itu dijepit dan user diberi tahu |
| `DURATION_TOLERANCE` | `0.20` | meleset lebih dari ini memicu SATU kali penulisan ulang naskah |
| `TRANSCRIBE_PROVIDER` | `auto` | `local` = Whisper di mesin ini (faster-whisper, venv `.venv-whisper`, pasang dengan `scripts/setup_local_whisper.sh`); `api` = penyedia OpenAI-kompatibel; `auto` = local kalau terpasang, selain itu api. **Tanpa transkrip agent tidak bisa mendengar video** |
| `TRANSCRIBE_LOCAL_MODEL` | `small` | model Whisper lokal. `small` diukur: kecocokan 0,92 dengan whisper-1, ~0,76x waktu nyata pada 4 core. `medium` ~3x lebih lambat |
| `TRANSCRIBE_LANGUAGE` | (otomatis) | kunci bahasa Whisper lokal (mis. `id`); kosong = deteksi otomatis |
| `INSPECT_TTL_HOURS` | `24` | umur pemeriksaan bahan (`inspect_id`); lewat itu agent harus memeriksa ulang |
| `CONTENT_FACTORY_REQUIRE_INSPECT` | `1` | gerbang di `hermes_render.py`: wajib ada pemeriksaan bahan yang sah. `0` mematikannya (mis. skrip) |
| `MOTION_GRAPHIC` | `sedang` | motion graphic penjelas (`sedang` / `mati`) |
| `VISUAL_CUT` | `1` | buang bagian goyang/oleng/buram |
| `EDIT_SELECTION` | `1` | editor AI memilih & mengurutkan potongan ucapan terbaik (mode audio asli). LLM hanya mengembalikan NOMOR kandidat; kode yang membangun kandidat, memverifikasi, dan menegakkan batas durasi. Gagal/transkrip tidak lengkap -> pakai semua klip |
| `EDIT_DEFAULT_MAX_SECONDS` | `60` | batas atas durasi hasil seleksi kalau user tidak meminta durasi tertentu; kelebihannya dipangkas dari skor terendah |
| `TRANSCRIBE_BASE_URL` / `TRANSCRIBE_API_KEY` | (penyedia utama) | penyedia SENDIRI untuk Whisper; perlu kalau penyedia chat tidak punya Whisper |
| `TTS_BASE_URL` / `TTS_API_KEY` | (penyedia utama) | penyedia SENDIRI untuk TTS |
| `MUSIC_ENABLED` | `1` | musik latar, kalau ada berkas di `assets/music/` (lihat README di folder itu) |
| `MUSIC_BELOW_SPEECH_DB` | `10` | seberapa jauh musik di bawah ucapan; level dihitung dari loudness video, bukan gain tetap |
| `MUSIC_DUCK_RATIO` / `MUSIC_DUCK_SIDECHAIN_GAIN` | `12` / `8` | kekuatan auto-ducking saat ada yang bicara |
| `SPOKEN_REWRITE` | `1` | brief menulis `voice_over_spoken` (ejaan fonetis untuk TTS) terpisah dari `full_voice_over` (ejaan benar untuk subtitle/caption) |

## Cara menjalankan

Lewat chat Telegram ke bot Hermes. Satu tahap bisa juga dijalankan manual untuk debugging:
`python3 scripts/agent1_2_brief.py`, lalu `python3 scripts/agent3_render.py` (bahan di
`workspace/raw/`, sebutkan lewat `CONTENT_FACTORY_ASSETS`).

### Alur di Telegram (Hermes): draf naskah dulu, baru render

Instruksi agent: `hermes-skill/content-factory/SKILL.md` (salin ke `~/.hermes/skills/`).

1. `scripts/inspect_media.py inspect` memeriksa bahan dan menyusun pertanyaan berpilihan
   (termasuk penonton & ajakan).
2. `scripts/hermes_render.py --draft ...` (±1-2 menit, tanpa render). BrainIdea melihat
   **lembar kontak 4 momen per klip** (bagian goyang dilewati) plus fakta per klip (durasi,
   ucapan, bagian yang dibuang), lalu menulis **2 varian naskah** dengan gaya berbeda. Tiap
   varian diperiksa kode (frasa deskriptif, kalimat panjang, huruf non-Latin) dan boleh ditulis
   ulang sekali. Pesan draf menampilkan pemahaman per klip, naskah A/B, dan rencana grafik yang
   lolos pemeriksaan.
3. User membalas `A`/`B`, atau mengirim naskah ubahannya.
4. `scripts/hermes_render.py --draft-id X --varian A [--naskah "..."]` merender **tanpa
   membuat brief ulang**. Naskah ubahan user dibacakan apa adanya. Draf ditolak bila milik chat
   lain, lebih dari 24 jam, bahannya berbeda, atau sudah dirender (klaim sekali pakai
   `O_EXCL`; dilepas lagi bila render gagal).

**Resep** (lihat tabel di SKILL.md):
1. suara asli + subtitle + B-roll cutaway/grafik (+musik);
2. voice AI natural + teks + grafik + B-roll sisipan + musik;
3. video bicara panjang dipecah jadi 2-3 short (`--jumlah-short`, pilih `--short semua|A,C`);
4. klip tanpa omongan + lagu: montase yang pergantian gambarnya jatuh di ketukan.

**Pengurang revisi**:
- *Storyboard* 8 panel per varian + contoh suara dikirim bersama draf (`scripts/storyboard.py`).
- *Pemeriksa mutu* sebelum kirim (`scripts/qa_video.py`): kenyaringan diperbaiki otomatis,
  lalu frame hitam/beku, teks terpotong/tertutup UI TikTok, dan durasi.
- *Revisi cepat* (`--revisi <run_id>`, `scripts/revisi.py`): render ulang video yang sudah jadi
  **tanpa LLM** (±80 dtk), mis. `--hapus-broll 2`, `--ganti-broll 1`, `--ganti-musik`,
  `--subtitle-style capcut`, `--naskah "..."`. Brief yang dirender dipakai ulang persis (naskah
  hasil koreksi durasi ikut disimpan). Klip B-roll yang tidak disebut dan lagunya dikunci.
  Voice-over dipakai ulang dari cache bila naskah & suaranya sama. Catatan milik chat lain,
  lebih dari 7 hari, atau bahannya hilang ditolak.

**Gaya tampilan** (`--gaya klasik|bersih|edukatif|elegan|hype|promo`, `scripts/gaya.py`,
`config/gaya/*.json`): satu preset = tema Remotion (warna, font judul, sudut, cahaya, karakter gerak)
+ knob editing yang sudah ada (subtitle, filter warna, font/animasi teks, SFX, zoom, cover), konsisten
di caption, kartu motion, panggung, dan cover. Prioritas: flag eksplisit > `--gaya` > profil chat
(`gaya.py pakai --chat-id ... --gaya ...`) > `klasik`. `klasik` = tampilan lama, identik piksel.
Validasi ketat (hex, font dari daftar, kontras teks kartu ≥ 4,5) karena tema kelak bisa datang dari
pengguna. `gaya.py pratinjau` membuat satu gambar contoh semua gaya (latar sintetis, di-cache).

**Carousel** (`scripts/carousel.py`, `remotion/src/Carousel.jsx`): 3-10 slide untuk Instagram
(1080×1350) dan TikTok (1080×1920) dari teks user atau transkrip video (`--dari-run`). Satu panggilan
LLM menyusun slide; kode memvalidasi (batas kata, hook→cta, angka dan kutipan harus ada di sumber) dan
mengukur hasil render dari lapisan teks terpisah: kotak aman, zona UI TikTok, wajah, kontras ≥ 3.
Tema = preset gaya yang sama dengan video. Keluaran JPEG + caption + hashtag.

**Posting** (`scripts/terbit.py`, lewat Zernio; butuh `ZERNIO_API_KEY`): dua langkah, `siapkan`
(cek kepemilikan, batas platform, akun; mengembalikan pratinjau) lalu `kirim` dengan kalimat
persetujuan user. Sekali pakai, pratinjau berlaku 30 menit, tanpa penjadwalan. TikTok bawaannya draf;
privasi terbit harus dari pilihan akun itu. Key hanya dikirim ke host Zernio. Kegagalan ambigu
(waktu habis, 5xx) berstatus `tidak_pasti` dan tidak diulang. **Belum diuji terhadap layanan
sungguhan** (ditulis dari dokumentasinya; tes memakai layanan palsu).

**Caption dinamis** (`--subtitle-style dinamis`, tahap uji): potongan 1-3 kata dari waktu kata
yang terdengar (`scripts/caption_dinamis.py`), satu kata kunci tampil besar bergradasi emas dengan
pop (`remotion/src/CaptionDinamis.jsx`), plus SFX pop/whoosh sintesis di kata kunci, kartu, dan
cutaway (`scripts/sfx.py`, `--sfx on|off`).
- Kata kunci: usulan brief yang benar-benar diucapkan, lalu heuristik (angka, merek, kata panjang).
  Keduanya dibatasi ±40% potongan, berjarak ≥ 1,2 dtk, dan maksimal 2 kali per kata.
- Render hemat: SATU sesi Chromium untuk semua potongan (timeline ringkas), lalu satu input concat
  PNG ke komposit. Versi pertama memakai satu input ffmpeg per potongan dan terbunuh OOM (3,8 GB).
- Terukur di video 45 dtk: +51 dtk render, proses terbesar 1,3 GB. Gagal → subtitle gaya `kata`.
- Aturan Remotion mengikuti Remotion Agent Skills (`.claude/skills/remotion-*`, untuk Claude Code).
- **Tata letak "panggung"** (`remotion/src/Panggung.jsx`, `motion_plan.jadwal_panggung`): 1-2 jendela
  ±3 dtk di poin utama (usulan brief `motion_plan.panggung`, atau cadangan: kata kunci caption).
  Latar kertas + ilustrasi dirender Remotion (satu input concat), video pembicara dikecilkan
  ffmpeg jadi kartu membulat yang meluncur naik; caption di jendela itu jadi judul serif
  (Instrument Serif Italic, OFL). Motion & cutaway yang bertabrakan dibuang, QA melewati jendela.
- **Pembersih suara** (`scripts/suara.py`): gerbang jeda (ambang dari level ucapan video), penegas
  vokal, kompresor; peredam bising hanya bila SNR < 25 dB (di audio bersih afftdn mengubah spektrum
  ucapan 1,1 dB). Video user: SNR 31,2 -> 36,1 dB, jeda 7,3 dB lebih senyap.
- **Buang kata pengisi** (`scripts/pengisi.py`): "eee/emm/hmm" & ulangan gagap ("aku pakai aku
  pakai") dipotong dari `ranges`; penekanan ("setiap hari, setiap hari") dan reduplikasi ("pelan
  pelan") TIDAK. Kata pengisi tidak pernah tampil di subtitle. Batas aman 25% durasi klip.
- **Zoom punch-in & kartu logo** (`scripts/zoom_wajah.py`, `scripts/logo.py`, `scripts/wajah.py`):
  zoom 1,10x ke wajah (OpenCV Haar) di kata kunci, maks 3, di luar jendela panggung; kartu logo
  merek yang diucapkan dari daftar TERTUTUP `config/merek_logo.json` + ikon simple-icons (CC0).
  Daftar tertutup itu wajib: katalog punya "Hermes" milik myHermes (kurir Jerman).
- **Cover didesain** (`scripts/sampul.py`, `remotion/src/Sampul.jsx`): frame dipilih dengan MENGUKUR
  (luas wajah x ketajaman Laplacian) dari video sebelum teks, judul dari kartu pembuka yang sudah
  divalidasi, ditaruh di bawah kotak wajah. Terukur di cover nyata: pita judul 22,1% piksel putih
  (frame asli 0%), area wajah justru lebih bersih. Gagal -> cover lama (`--cover frame`).
- **Level audio** (29 Sep, diukur pada ucapan sungguhan `tests/data/ucapan_uji.wav`): musik -17 dB
  di bawah ucapan saat bicara, -6 dB di jeda (dulu -30 dB = "musik tidak ada"); sidechain
  dinormalkan ke kenyaringan ucapan. SFX 3-6 dB di bawah PUNCAK ucapan (dulu ±18 dB).

**Motion graphic** (bawaan `sedang`, matikan dengan `--motion mati`) dirender Remotion
(`remotion/src/MotionOverlay.jsx`):
- Jenisnya: kartu pembuka, sorot kata kunci, ikon, langkah, label, kartu ajakan.
- LLM hanya mengusulkan. `scripts/motion_plan.py` menegakkan katalog, menolak angka yang tidak
  ada di permintaan user, dan memasang tiap elemen tepat saat kata jangkarnya **diucapkan**
  narasi TTS.
- Penempelan menumpang encode teks, jadi tidak ada encode tambahan.
- Gagal berarti video tanpa grafik, dan alasannya dilaporkan.

## Aturan yang dijaga di kode

Lihat [CLAUDE.md](CLAUDE.md). Ringkasnya:
- **Zero Hallucination on Assets**: daftar bahan ditentukan Python dan divalidasi ada di disk.
- **Gagal-tertutup**: lampiran di luar cache Hermes, bahan milik run lain, dan draf/pemeriksaan
  milik chat lain ditolak.
- **LLM mengusulkan, kode mengukur**: tren, angka, waktu grafik, dan biaya berasal dari kode.
- **Laporan jujur**: tidak ada data performa asli → `NO_DATA`. Kegagalan dilaporkan, bukan
  disamakan dengan "kosong".
- **Human-in-the-loop**: user memilih naskah sebelum render; posting hanya setelah user menyetujui
  pratinjau tiap posting.

## Yang belum ada

- Analitik performa asli (`fetch_real_analytics()` di `agent5_insight.py` masih mengembalikan
  `None` → `NO_DATA`). Posting lewat `scripts/terbit.py` sudah mencatat `publish_id` ke
  `publish_history.json`, tapi pengambilan metriknya belum dibuat.
- Posting terjadwal dan YouTube.
