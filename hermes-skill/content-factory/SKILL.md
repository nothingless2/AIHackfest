---
name: content-factory
description: Edit video/foto yang diupload user di Telegram menjadi draft konten pendek (Reels/TikTok/Shorts) — potong jeda, subtitle karaoke, musik, voice-over AI, filter warna, speed ramp, zoom otomatis. Pakai saat user mengupload video/foto dan minta diedit/disusun jadi konten media sosial.
version: 1.0.0
author: user
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [video, editing, telegram, content, reels, tiktok]
---

# Content Factory (pipeline lokal, bukan editor generatif)

**JANGAN PERNAH menjalankan `ffmpeg` secara manual/improvisasi untuk permintaan
edit video/foto dari user.** Kalau kamu punya tool terminal dan tahu cara pakai
ffmpeg, GODAAN itu justru yang harus dihindari di sini — ffmpeg mentah TIDAK
memotong jeda, TIDAK menambah subtitle, TIDAK memilih potongan terbaik, dan
hasilnya cuma copy/remux mentah yang terlihat "berhasil" padahal tidak diedit
sama sekali. WAJIB lewat `scripts/inspect_media.py` lalu `scripts/hermes_render.py`
persis seperti Langkah 1-4 di bawah, setiap kali, tanpa kecuali.

Pipeline ffmpeg + LLM yang berjalan lokal di mesin ini (`/root/AIHackfest`). Mengedit
bahan MILIK USER — bukan membuat video sintesis AI dari nol.

**YANG BENAR-BENAR DIKERJAKAN**: suara asli dipertahankan (atau voice-over AI /
dibisukan kalau diminta), jeda diam dipotong, editor AI memilih potongan ucapan
terbaik, subtitle karaoke, musik latar dengan auto-ducking, filter warna
(natural/vivid/warm/cool/bw), speed ramp (0.5-2.0x, HANYA mode voice-over AI),
zoom Ken Burns otomatis (HANYA bahan foto), rasio 9:16/1:1/16:9, durasi 10-60
detik, cover JPG.

**YANG TIDAK ADA** — jangan dijanjikan: koreksi warna profesional/LUT, stabilisasi,
B-roll otomatis, stiker, dubbing bahasa lain, upload otomatis ke platform.

## Kapan dipakai

User mengirim video/foto di chat Telegram ini DAN memintanya diedit/disusun jadi
konten (atau konteksnya jelas mengarah ke situ). Jangan dipakai untuk permintaan
lain (mis. "buatkan video animasi dari teks" — itu bukan tool ini).

## Prasyarat penting

- Lampiran yang diupload user tersimpan Hermes di `~/.hermes/cache/videos/`,
  `~/.hermes/cache/images/`, atau `~/.hermes/cache/audio/` (untuk musik). Pakai
  path FILE LOKAL itu apa adanya — jangan menebak atau memindai folder lain.
- Render sekali jalan HANYA SATU pada satu waktu di seluruh mesin (file lock).
  Kalau ditolak dengan alasan render_sibuk, sampaikan itu ke user dan jangan
  mencoba lagi otomatis.
- Proses render makan waktu 2-5 menit — SELALU jalankan sebagai proses latar
  belakang (langkah 3), jangan sinkron.

## Langkah 1 — Periksa bahan (cepat, sinkron)

**Album Telegram tiba sebagai BEBERAPA pesan terpisah.** Kumpulkan path dari SEMUA pesan
lampiran user yang berurutan sejak permintaannya — termasuk pesan pertama yang mungkin
dibalas "Your request was not processed" karena bertabrakan dengan pesan berikutnya
(terjadi 24 Sep: 3 video dikirim, hanya 2 dipakai karena yang pertama, pembawa permintaan,
terlewat). JANGAN memindai folder cache untuk "file terbaru" — folder itu bercampur dengan
lampiran user lain. Pesan pertanyaan dari langkah 1 menyebut jumlah bahan yang diterima;
kalau user membalas bahwa jumlahnya kurang, minta kirim ulang.

```
python3 /root/AIHackfest/scripts/inspect_media.py inspect
```
dengan JSON di stdin:
```json
{"paths": ["<path lokal video/foto dari cache Hermes>", "..."],
 "konteks": "<pesan/permintaan user apa adanya>",
 "chat_id": "<label chat, mis. DM with Stringless>"}
```

Hasilnya JSON berisi fakta terukur (durasi, ada/tidaknya ucapan, dst), `inspectId`,
dan (kalau ada) `pertanyaan` dengan pesan siap kirim + pemetaan jawaban.

## Langkah 2 — Tanya user (kalau ada pertanyaan)

Kirim `pesan` dari hasil langkah 1 APA ADANYA sebagai balasan chat biasa (sudah
berisi opsi berhuruf dengan default berbintang — user MEMILIH, bukan menebak).
Lalu tunggu balasan user di chat ini seperti percakapan biasa — tidak perlu
tool blocking apa pun.

Kalau hasil langkah 1 TIDAK ada `pertanyaan`, lanjut langsung ke langkah 3
dengan pengaturan bawaan.

## Langkah 3 — Render (lambat, WAJIB latar belakang)

Setelah user menjawab (atau kalau tidak ada pertanyaan), petakan jawabannya
lewat `pemetaan` dari hasil langkah 1, lalu jalankan sebagai proses LATAR
BELAKANG dengan `notify_on_complete=true`:

```
python3 /root/AIHackfest/scripts/hermes_render.py \
  --media-path "<path 1>" [--media-path "<path 2>" ...] \
  --chat-id "<label chat yang SAMA PERSIS dengan di langkah 1>" \
  --inspect-id "<inspectId dari langkah 1>" \
  --user-answered \
  --user-context "<pesan/permintaan user apa adanya>" \
  [--audio-mode ai|original|mute] [--subtitle-style ...] [--aspect-ratio ...] \
  [--fit-mode ...] [--edit-mode auto|full] [--music on|off] [--music-mood ...] \
  [--music-file "<path musik user, kalau ada>"] [--duration-seconds N] \
  [--color-filter natural|vivid|warm|cool|bw] [--speed-factor 0.5-2.0] [--auto-zoom] \
  [--text-position atas|tengah|bawah] [--text-font standar|tegas|modern|elegan|santai|bersih]
```

Pemetaan nama parameter di `pemetaan` (langkah 1) ke flag `hermes_render.py`:

| parameter | flag |
|---|---|
| `textPosition` (atas/tengah/bawah) | `--text-position` |
| `textFont` (standar/tegas/modern/elegan/santai/bersih) | `--text-font` |
| animasi teks (pop/loncat/geser/fade/none) | `--text-animation` (bawaan: pop) |
| `subtitleStyle` | `--subtitle-style` |
| `colorFilter` | `--color-filter` |
| `audioMode` | `--audio-mode` |
| `aspectRatio` / `fitMode` / `editMode` | `--aspect-ratio` / `--fit-mode` / `--edit-mode` |
| `staticText` (true) | `--static-text` |
| `music` / `musicMood` | `--music` / `--music-mood` |
| `durationSeconds` | `--duration-seconds` |
| suara narasi pria/wanita (dari jawaban user) | `--voice pria` / `--voice wanita` |
| gaya suara (ramah/energik/profesional/tenang/bercerita) | `--voice-persona` |
| potong bagian goyang (bawaan nyala; user minta jangan) | `--visual-cut off` |
| `broll` (true) / `brollQuery` | `--broll` / `--broll-query` (WAJIB bersama `--audio-mode ai`) |

Teks tulisan di layar kini BERANIMASI (Remotion) dan emoji tampil berwarna. Kalau hasil berisi
`teks_animasi.dipakai: false`, animasi gagal dan video memakai teks statis: sampaikan
`teks_animasi.gagal` ke user dalam satu kalimat. Animasi hanya untuk teks tulisan; subtitle
dari ucapan tetap karaoke.

Kalau `suara.cadangan` true, narasi memakai suara cadangan gratis (edge-tts) karena ElevenLabs
gagal/kuota habis -- sebutkan `suara.alasan` singkat ke user.

Kalau hasil berisi `potongan_visual.dipotong`, sebutkan singkat bagian yang dibuang karena
goyang/oleng (mis. "2 detik terakhir video ke-3 dibuang karena kamera oleng"). Kalau ada
`dipertahankan_ucapan`, beri tahu bahwa bagian goyang itu dipertahankan karena berisi ucapan.

Kalau hasil berisi `catatan_teks` (emoji dihapus dari teks di layar karena font tidak mendukung), sampaikan
ke user dalam satu kalimat.

B-roll: kalau hasil render berisi `broll_kredit`, tambahkan barisnya ke caption (Pexels
meminta kredit kreator). Kalau `broll.gagal` terisi, sampaikan alasannya ke user apa adanya:
video tetap jadi, hanya tanpa klip stok.

Posisi/font hanya berlaku untuk teks tulisan di layar; subtitle dari ucapan tetap di
bawah dengan gayanya sendiri. Kalau user menulis jawaban bebas (mis. "tengah, font
santai"), pakai HANYA nilai dari daftar di atas — jangan menyebut nama font lain.

Kalau langkah 1 tidak menghasilkan pertanyaan (tidak ada gerbang untuk dilewati),
boleh tambahkan `--no-require-inspect` dan hilangkan `--inspect-id`/`--user-answered`.

**JANGAN menambah flag yang tidak berasal dari `pemetaan` atau dari kata-kata user.**
(24 Sep: agen menambah `--music off` sendiri; user memilih narasi AI, bukan tanpa musik.)

Musik dari user: lagu yang dikirim tersimpan di `~/.hermes/cache/audio/` (atau
`cache/documents/` kalau dikirim sebagai file). Berikan path-nya lewat `--music-file`
(kalau terlanjur lewat `--media-path`, skrip memindahkannya otomatis). Lagu boleh dikirim
di pesan berikutnya -- kumpulkan path-nya seperti lampiran video. Sertakan juga path lagu
itu di `paths` langkah 1 supaya suasananya ikut dianalisis.

Isi flag lain HANYA yang benar-benar diminta/tersirat dari user — jangan menebak
nilai yang tidak disebutkan; defaultnya sudah dirancang baik (subtitle karaoke,
suara asli, hard cut + fade di pergantian topik, 20-35 detik, 9:16).

## Langkah 4 — Setelah proses selesai

Baca output JSON (satu baris) dari proses latar belakang itu.

- **`ok: true`**: kirim file di `video_path` ke chat ini (pakai kemampuan kirim
  file/media bawaanmu, BUKAN skrip ini), dengan caption ringkas dari `judul` +
  `deskripsi` + `hashtags`. Sebutkan `catatan_durasi` kalau ada isinya (artinya
  durasi diminta user dijepit ke batas yang berbeda).
- **`ok: false`**: sampaikan `alasan` apa adanya ke user dalam kalimat biasa.
  Jangan mencoba lagi otomatis kalau alasannya `render_sibuk`.

Jangan pernah menjanjikan hasil atau merinci pengaturan sebelum langkah ini
selesai dan filenya benar-benar ada.
