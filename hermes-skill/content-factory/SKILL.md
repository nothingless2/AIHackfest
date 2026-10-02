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

**Skill ini sudah termuat penuh di awal sesi (`skills.auto_load`).** Jangan memanggil
`skill_view content-factory` lagi, dan jangan membaca kode sumber pipeline (`scripts/*.py`,
`skills/*`) untuk mencari opsi: semua opsi yang ada tercantum di sini. Opsi yang tidak tercantum
= belum didukung; bilang begitu ke user. (30 Sep: tanpa skill di konteks, agent membaca
`music.py`/`revisi.py`/`auto_render.py` dan mencari `*.mp4` di seluruh `~/.hermes`.)

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

Sebelum render, BrainIdea MENONTON bahan (4 momen per klip + ucapan) lalu mengirim 2 pilihan
naskah (atau beberapa short) BESERTA gambar storyboard; video baru dibuat setelah user memilih
(Langkah 3-5).

## Resep (pilih dari permintaan user, jangan menebak)

| Permintaan user | Flag di Langkah 3 |
|---|---|
| Subtitle dari suara asli + B-roll/ilustrasi (+musik) | `--audio-mode original --broll` (+ `--music-file` bila user kirim lagu) |
| Voice AI natural + teks + animasi (+musik/lagu) | `--audio-mode ai` (+ `--broll` bila minta klip stok, `--music on`) |
| Video bicara panjang dipecah jadi beberapa konten | `--audio-mode original --jumlah-short 2` atau `3` |
| Klip/foto tanpa omongan + lagu | `--audio-mode mute --music on` (+ `--music-file`); potongan otomatis mengikuti ketukan lagu |
| "Teks lebih dinamis", "caption kayak Remotion/CapCut kekinian", "kata penting dibesarkan" | tambahkan `--subtitle-style dinamis` ke resep mana pun yang berucap/bernarasi |

`--subtitle-style dinamis` (tahap uji): teks tampil 1-3 kata sekaligus, kata PENTING tampil besar
berwarna emas dengan animasi pop, plus efek suara pop/whoosh (matikan: `--sfx off`). Di 1-2 poin
utama layar berganti ke tata letak "panggung" ±3 dtk: pembicara jadi kartu membulat di latar
terang berkisi, ilustrasi animasi di atas (timeline/grafik/checklist/chat/kode/kata), caption jadi
judul serif. Render ±1-1,5 menit lebih lama. `panggung.gagal` terisi -> sebut singkat (video tetap
jadi tanpa tata letak itu).

Ikut aktif bersama gaya dinamis (mode suara asli): **zoom halus ke wajah** di kata kunci
(`zoom.dipakai`, matikan `--zoom-wajah off`) dan **kartu logo merek** yang diucapkan
(`logo.dipakai`, `--logo-merek off`). Merek hanya dari daftar `config/merek_logo.json` -- di luar
daftar TIDAK ada kartu (bukan logo tebakan). `logo.alasan` "tidak ada merek terdaftar yang
diucapkan" itu NORMAL; kalau user mau mereknya tampil, beri tahu ia bisa menambahkannya ke
`config/merek_logo.json`.

**Cover** kini didesain: frame terbaik (wajah besar & tajam, di luar jendela panggung/B-roll) +
judul besar di bawah wajah, bukan frame acak yang subtitle-nya sudah terbakar (`sampul.dipakai`,
kembali ke cara lama: `--cover frame`). Gagal -> cover lama, `sampul.gagal` disebut singkat.

Mode suara asli juga otomatis: **suara dibersihkan** (`suara_bersih`, `--bersih-suara off`) dan
**"eee/emm/hmm" & ulangan gagap dibuang** (`potong_pengisi`, `--potong-pengisi off`). Sebut singkat
kalau `potong_pengisi.dibuang` > 0 ("N kata pengisi dibuang"). Kalau hasil berisi `caption.gagal`, sampaikan: video tetap bersubtitle gaya
biasa. `sfx.dipakai: false` + `alasan` cukup disebut singkat.

B-roll di mode suara asli ditampilkan SEBENTAR di atas video (cutaway) saat kata yang relevan
diucapkan: suara asli dan subtitle tidak bergeser. Di mode voice-over AI klip stok disisipkan.
Grafik penjelas (kartu, sorot, ikon, label) otomatis; di mode suara asli ditempatkan di bawah
wajah pembicara.

**YANG TIDAK ADA** — jangan dijanjikan: koreksi warna profesional/LUT, stabilisasi,
stiker, dubbing bahasa lain, posting terjadwal, posting tanpa persetujuan user.

## Gaya tampilan (preset)

Satu **gaya** = paket tampilan + editing yang konsisten di subtitle, kartu grafik, tata letak
panggung, dan cover: `klasik` (bawaan), `bersih`, `edukatif`, `elegan`, `hype`, `promo`.
Daftar dan penjelasannya: `python3 /root/AIHackfest/scripts/gaya.py daftar`.

- **"Gaya apa saja?" / "contoh gaya" / user ragu memilih**: jalankan
  `python3 /root/AIHackfest/scripts/gaya.py pratinjau`, kirim berkas `gambar` dari hasilnya (satu
  gambar berisi contoh semua gaya) dengan kemampuan kirim bawaanmu, plus satu baris per gaya dari
  `gaya[].label` + `deskripsi`. Gagal (`ok: false`) -> sebutkan gayanya dengan kata saja.
- **Memilih untuk satu video**: jawaban pertanyaan `gaya` (Langkah 1) atau permintaan user ->
  `--gaya <nama>` di Langkah 3/5. Flag gaya lain yang diminta user (font, filter warna, subtitle)
  tetap menang atas bagian preset yang sama.
- **"Pakai gaya X seterusnya" / "simpan gayaku"**: `python3 /root/AIHackfest/scripts/gaya.py pakai
  --chat-id "<label chat yang SAMA PERSIS>" --gaya <nama>`. Video berikutnya otomatis memakainya
  (pertanyaan gaya tidak muncul lagi). "Lupakan gayaku": perintah `lupakan` dengan `--chat-id` sama;
  "gayaku apa?": perintah `lihat`.
- **Mengganti gaya video yang sudah jadi**: revisi cepat (Langkah 7) dengan `--gaya <nama>`.
- **Gaya kustom** ("pakai warna merekku biru tua", "fontnya yang elegan tapi tetap hype", "tanpa
  pendar"): racik di atas salah satu gaya dan simpan untuk chat ini:
  ```
  python3 /root/AIHackfest/scripts/gaya.py kustom --chat-id "<label chat>" [--dasar <gaya>] \
    [--aksen "#RRGGBB"] [--font standar|tegas|modern|elegan|santai|bersih] [--sudut 0-2] \
    [--cahaya on|off] [--gerak pegas|halus|tegas] [--kartu "#RRGGBB"] [--teks "#RRGGBB"]
  ```
  Cukup `--aksen` untuk warna merek: warna pendampingnya diturunkan otomatis. Warna WAJIB hex; user
  menyebut nama warna ("marun", "biru dongker") -> pilih hex yang wajar dan SEBUTKAN ke user. Kirim
  `gambar` pratinjaunya. Sesudah itu video dan carousel chat ini otomatis memakainya; perubahan
  berikutnya menambah ke yang sudah ada. `ok: false` (mis. kontras teks kurang) -> sampaikan
  `alasan`, tawarkan warna lain. Kembali ke gaya jadi: `pakai --gaya <nama>`; ke racikannya lagi:
  `pakai --gaya kustom`.
- Hasil render berisi `gaya_tampilan` (`label`, `sumber`). Sebut singkat labelnya; kalau
  `sumber` = `profil`, cukup "pakai gaya tersimpanmu (Hype)". `bawaan_profil_tidak_berlaku` ->
  gaya tersimpan sudah tidak ada, video memakai Klasik: beri tahu user.
- Ke user sebut LABEL-nya (Hype, Elegan), bukan flag atau nama berkas.

## Kamus istilah (nama, merek, istilah yang salah tulis di subtitle)

User bilang tulisannya salah ("harusnya OpenClaw, bukan Open Cloud", "namaku ditulis salah"):
```
python3 /root/AIHackfest/scripts/kamus.py tambah --chat-id "<label chat>" --benar "OpenClaw" \
  --salah "open cloud" --salah "opencloud"
```
`--benar` = ejaan dari user, persis. `--salah` = bentuk salah yang MUNCUL di subtitle/naskah (boleh
beberapa). Berlaku untuk semua video dan carousel chat ini sesudahnya. Untuk video yang sudah jadi:
jalankan revisi cepat (Langkah 7) dengan `--kamus` setelah menambah istilah -> subtitle-nya ikut
dibetulkan; hasil berisi `kamus.diganti` (jumlah kata yang dikoreksi), sebut singkat. `kamus` kosong
di hasil = bentuk salahnya tidak ditemukan di ucapan: tanyakan tulisan salah yang persis terlihat. `daftar` / `hapus --benar ...`
untuk melihat dan menghapus. Jangan menambah istilah yang tidak diminta user.

## Kapan dipakai

User mengirim video/foto di chat Telegram ini DAN memintanya diedit/disusun jadi
konten (atau konteksnya jelas mengarah ke situ). Jangan dipakai untuk permintaan
lain (mis. "buatkan video animasi dari teks" — itu bukan tool ini).

Juga untuk **carousel** (postingan geser Instagram/TikTok) dari tulisan user atau dari video yang
sudah jadi — lihat bagian "Carousel" di bawah.

## Carousel (Instagram / TikTok)

Postingan geser 3-10 slide (pembuka, isi/daftar, ajakan) dengan gaya yang sama dengan video.
WAJIB latar belakang (±1 menit), sama seperti render:

```
python3 /root/AIHackfest/scripts/carousel.py --chat-id "<label chat yang SAMA PERSIS>" \
  --teks "<topik / poin / naskah dari user, apa adanya>" \
  [--dari-run "<run_id video yang didaur ulang>"] [--foto "<path foto user>"]... [--stok] \
  [--gaya <nama>] [--platform ig|tiktok|keduanya] [--jumlah 3-10]
```

| Permintaan user | Flag |
|---|---|
| "bikin carousel tentang ..." | `--teks "<pesan user apa adanya>"` |
| "jadikan video tadi carousel" | `--dari-run <run_id video itu>` (boleh ditambah `--teks`) |
| user mengirim foto untuk carousel | `--foto <path>` per foto (urut: slide pembuka dulu) |
| "pakai foto stok" tiap slide | `--stok` (gagal -> slide tetap jadi tanpa foto) |
| "latarnya pakai gambar ini" (satu gambar untuk semua slide) | `--latar <path gambar user>` |
| "latarnya foto estetik yang cocok" / "kasih background" | `--latar stok` (satu foto sesuai topik) |
| untuk TikTok / dua-duanya | `--platform tiktok` / `--platform keduanya` (bawaan: `ig`) |
| jumlah slide / gaya | `--jumlah N`, `--gaya <nama>` (tanpa flag: gaya tersimpan, lalu Klasik) |

- **`ok: true`**: kirim SEMUA berkas `slide.ig` (dan/atau `slide.tiktok`) berurutan dalam SATU balasan
  dengan kemampuan kirim bawaanmu (jadi album), lalu `caption` + `hashtags` sebagai teks siap tempel.
  Sebut `gaya_tampilan.label`. `catatan` berisi sesuatu -> sebut singkat (mis. "foto latar gagal").
  `--foto` = foto jadi gambar utama slide (teks menjauhi wajah); `--latar` = gambar diburamkan di
  belakang SEMUA slide. Boleh digabung: slide berfoto memakai fotonya, sisanya memakai latar.
- `qa.<platform>.lolos: false`: sebutkan slide mana dan masalahnya (`per_slide[].masalah`), tawarkan
  dibuat ulang lebih ringkas. Jangan bilang "sudah rapi" kalau QA tidak lolos.
- **`ok: false`**: sampaikan `alasan` apa adanya. `render_sibuk` -> jangan coba lagi otomatis.
  `sumber_kurang` -> minta user menuliskan topik/poinnya.
- **Fakta hanya dari user**: angka dan kutipan di slide HANYA yang tertulis di pesan user atau
  terucap di videonya (kode menolak yang lain). User ingin angka tertentu -> minta ia menuliskannya.
- Mengunggahnya ke Instagram/TikTok: lihat "Posting" di bawah (hanya atas persetujuan user).

## Posting ke Instagram / TikTok (HANYA setelah user setuju)

Tidak ada yang boleh terbit tanpa user melihat pratinjaunya dan menjawab ya UNTUK POSTING ITU.
Persetujuan lama, "oke" untuk hal lain, atau "posting semua nanti" BUKAN persetujuan. Tidak ada
penjadwalan: posting selalu saat itu juga.

1. User minta posting -> siapkan (cepat, belum mengunggah apa pun):
   ```
   python3 /root/AIHackfest/scripts/terbit.py siapkan --chat-id "<label chat yang SAMA PERSIS>" \
     (--run "<run_id video>" | --carousel "<carousel_id>") --platform instagram|tiktok [--caption "<caption dari user>"]
   ```
2. Kirim `pratinjau` dari hasilnya APA ADANYA ke user dan tanya: "Posting sekarang?" Untuk TikTok
   sebutkan bawaannya masuk DRAF (user menekan posting di aplikasi TikTok); kalau user mau langsung
   terbit, ia memilih SENDIRI privasinya dari `privasi_tiktok`. Instagram langsung terbit.
3. Setelah user menjawab ya:
   ```
   python3 /root/AIHackfest/scripts/terbit.py kirim --chat-id "<label chat>" --id "<id dari langkah 1>" \
     --setuju "<kalimat persetujuan user apa adanya>" [--privasi <pilihan user, TikTok saja>]
   ```
   `--setuju` diisi kalimat user, bukan karanganmu. User ragu, diam, atau minta ubah caption ->
   JANGAN kirim; ulangi langkah 1 dengan perubahannya.
- `ok: true`: sebut akunnya, `mode` (draf/terbit), dan `url` bila ada.
- `tidak_pasti`: sambungan terputus saat posting; MUNGKIN sudah terbit. Minta user cek akunnya.
  JANGAN mengirim ulang. `sudah_diproses`: sama, jangan diulang.
- `terbit_tidak_siap` / `akun_belum_terhubung`: posting belum diatur; bilang user bisa mengunggah
  sendiri berkas yang sudah kamu kirim. Kode lain: sampaikan `alasan` apa adanya.
- `python3 /root/AIHackfest/scripts/terbit.py periksa` -> akun mana yang siap.

## Prasyarat penting

- Lampiran yang diupload user tersimpan Hermes di `~/.hermes/cache/videos/`,
  `~/.hermes/cache/images/`, atau `~/.hermes/cache/audio/` (untuk musik). Pakai
  path FILE LOKAL itu apa adanya — jangan menebak atau memindai folder lain.
- Render sekali jalan HANYA SATU pada satu waktu di seluruh mesin (file lock).
  Kalau ditolak dengan alasan render_sibuk, sampaikan itu ke user dan jangan
  mencoba lagi otomatis.
- Proses render makan waktu 2-5 menit — SELALU jalankan sebagai proses latar
  belakang (langkah 3 dan 5), jangan sinkron.

## Status kuota/error: cek SEKARANG, jangan dari ingatan

**JANGAN PERNAH** bilang "kuota habis", "model error", atau "coba lagi besok" berdasarkan
riwayat obrolan atau error yang pernah kamu lihat sebelumnya. Kuota direset tiap hari, key bisa
diganti, dan model yang penuh sering pulih dalam hitungan menit. (26 Sep: bot dua kali menolak
membuat konten dengan error 24 Sep dari key lama — padahal kuota key baru terpakai 1 dari 50.)

- Kalau user bertanya "bisa buat konten sekarang?" atau semacamnya, jalankan:
  ```bash
  cd /root/AIHackfest && python3 scripts/cek_kuota.py
  ```
  Memakai satu permintaan 1-token ke model utama (di luar jatah gratis, jadi tidak mengurangi
  jatah). `bisa_jalan: true` → jawab bisa, lalu lanjut Langkah 1. `bisa_jalan: false` → sebut
  `kuota_gratis` dan `reset` apa adanya. `ok: false` → pemeriksaannya yang gagal (`alasan`),
  BUKAN berarti kuota habis: tetap coba Langkah 1.
  Tambahkan `--gratis` HANYA kalau user memang menanyakan jatah model gratis; kalau jatahnya
  masih ada, pemeriksaan itu sendiri memakai 1 dari 50.
  Saat melaporkan, **jangan sebut id/nama model mana pun** (lihat SOUL) dan jangan istilah teknis
  (`429`, `:free`, `rate limit`): cukup "model utama" dan "jatah model gratis", plus angka dan
  jam resetnya. Jangan pula meneruskan saran jualan penyedia
  ("pakai model berbayar", nama paket langganan) — itu bukan keputusanmu.
- **Semua model `:free` OpenRouter berbagi SATU jatah akun: 50 permintaan/hari**, reset 00:00 UTC
  (07:00 WIB) — `limit_source: openrouter_free_tier_daily`. Jadi kalau satu model `:free` kena
  429 karena jatah, **semua** model `:free` juga kena, dan berganti model `:free` tidak menolong.
  Jangan pernah menjanjikan "aku coba model lain" dalam keadaan itu; sebut jam resetnya.
  (Terukur 30 Sep: 6 model `:free` menjawab 429 identik dalam 3 detik.)
- Error hanya boleh dilaporkan dari hasil perintah yang BARU SAJA kamu jalankan di giliran ini,
  dikutip apa adanya dengan jamnya. Waktu reset dari header harus diubah ke tanggal & jam WIB;
  kalau sudah lewat, jangan disebut "besok".
- "Model percakapan jalan tapi pipeline tidak" bukan kesimpulan yang boleh ditarik tanpa
  menjalankan pipeline. Pipeline punya rantai model cadangan sendiri; kalau model yang bisa melihat
  gambar sedang penuh, draf tetap jadi dan menyebutnya sendiri.

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
 "chat_id": "<label chat dari baris Source di konteks sesi, mis. DM with Budi>"}
```

**Label chat** (`chat_id` / `--chat-id`) diambil HANYA dari baris `**Source:**` konteks sesi ini
(mis. `Telegram (DM with Budi)` -> `DM with Budi`), dan dipakai SAMA PERSIS di semua langkah.
DILARANG mencarinya di log, `run_log.jsonl`, berkas state, atau perintah sebelumnya: label di sana
milik chat lain, dan memakainya berarti draf, revisi, atau gaya orang lain ikut tersentuh. Tidak ada
baris Source -> jangan jalankan perintah yang butuh `--chat-id`; bilang itu hanya bisa dari chat.
JANGAN meminta user mengetikkan label chat, dan jangan memakai label yang ia ketik: itu bisa label
orang lain.
(1 Okt: tanpa baris Source, agent mengambil label user lain dari log lalu menyimpan gayanya.)

Hasilnya JSON berisi fakta terukur (durasi, ada/tidaknya ucapan, dst), `inspectId`,
dan (kalau ada) `pertanyaan` dengan pesan siap kirim + pemetaan jawaban.

## Langkah 2 — Tanya user (kalau ada pertanyaan)

Kirim `pesan` dari hasil langkah 1 APA ADANYA sebagai balasan chat biasa (sudah
berisi opsi berhuruf dengan default berbintang — user MEMILIH, bukan menebak).
Lalu tunggu balasan user di chat ini seperti percakapan biasa — tidak perlu
tool blocking apa pun.

Kalau hasil langkah 1 TIDAK ada `pertanyaan`, lanjut langsung ke langkah 3
dengan pengaturan bawaan.

## Langkah 3 — Draf naskah (WAJIB latar belakang, ±1-2 menit)

Setelah user menjawab (atau kalau tidak ada pertanyaan), petakan jawabannya
lewat `pemetaan` dari hasil langkah 1, lalu jalankan perintah di bawah DENGAN `--draft`
sebagai proses LATAR BELAKANG dengan `notify_on_complete=true`. `--draft` belum merender:
BrainIdea menonton bahan dan menulis 2 pilihan naskah.

```
python3 /root/AIHackfest/scripts/hermes_render.py --draft \
  --media-path "<path 1>" [--media-path "<path 2>" ...] \
  --chat-id "<label chat yang SAMA PERSIS dengan di langkah 1>" \
  --inspect-id "<inspectId dari langkah 1>" \
  --user-answered \
  --user-context "<pesan/permintaan user apa adanya>" \
  [--audio-mode ai|original|mute] [--subtitle-style ...] [--aspect-ratio ...] \
  [--fit-mode ...] [--edit-mode auto|full] [--music on|off] [--music-mood ...] \
  [--music-file "<path musik user, kalau ada>"] [--duration-seconds N] \
  [--color-filter natural|vivid|warm|cool|bw] [--speed-factor 0.5-2.0] [--auto-zoom] \
  [--text-position atas|tengah|bawah] [--text-font standar|tegas|modern|elegan|santai|bersih] \
  [--gaya klasik|bersih|edukatif|elegan|hype|promo]
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
| `broll` (true) / `brollQuery` | `--broll` / `--broll-query` (semua mode) |
| `jumlahShort` (2/3) | `--jumlah-short 2` / `--jumlah-short 3` |
| motion graphic (bawaan sedang; user minta tanpa grafik) | `--motion mati` |
| `gaya` (klasik/bersih/edukatif/elegan/hype/promo) | `--gaya` |

Teks tulisan di layar kini BERANIMASI (Remotion) dan emoji tampil berwarna. Kalau hasil berisi
`teks_animasi.dipakai: false`, animasi gagal dan video memakai teks statis: sampaikan
`teks_animasi.gagal` ke user dalam satu kalimat. Animasi hanya untuk teks tulisan; subtitle
dari ucapan tetap karaoke.

Mode voice-over AI kini menampilkan teks NARASI satu kata per tampilan yang mengikuti suara
(gaya `kata`, seperti video kreator). Minta user menulis TUJUAN kontennya (mis. "ajak anak muda
donor darah") dan teruskan ke `--user-context`; naskah ditulis sebagai kreator yang menyapa
penonton, bukan deskripsi gambar.

Motion graphic (kartu pembuka, sorot kata kunci, ikon, langkah, label, kartu ajakan) kini
BAWAAN, gaya kartu gelap berpendar seperti video referensi. BrainIdea mengusulkannya di draf
(baris "Grafik:"); kode memeriksa dan memasangnya tepat saat kata jangkarnya diucapkan narasi.
Elemen penjelas (sorot/ikon/langkah/label) hanya di mode voice-over AI; mode suara asli hanya
kartu pembuka & ajakan. Di hasil render: kalau `motion.dipakai` false, sampaikan
`motion.gagal` atau `motion.alasan` singkat (video tetap jadi, tanpa grafik); kalau
`motion.catatan` berisi elemen yang dibuang (mis. angka yang tidak ada di permintaan user),
sebutkan satu kalimat.

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

**JANGAN melewati draf** (menjalankan tanpa `--draft` lalu langsung render): user meminta
melihat naskah sebelum video dibuat.

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

## Langkah 4 — Kirim draf, tunggu pilihan user

Hasil `--draft` (JSON satu baris) berisi `ok`, `draft_id`, `pesan`, `storyboard`, `contoh_suara`.
- **`ok: true`**: kirim `pesan` APA ADANYA ke user (sudah berisi apa yang BrainIdea tangkap
  dari tiap klip, naskah A dan B, dan cara membalas). Lalu kirim tiap gambar di `storyboard`
  (satu per varian, beri keterangan "Storyboard A/B") dan, bila ada, audio `contoh_suara`
  ("contoh suara narasi"). Kalau `storyboard_gagal` terisi, sebutkan singkat. Simpan
  `draft_id`. Tunggu balasan.
- **`ok: false`**: sampaikan `alasan` ke user.

Membaca balasan user:
- "A" / "B" (atau "yang pertama/kedua") -> `--varian A` / `--varian B`.
- User mengubah kalimat (mis. "A, tapi pembukanya: Halo semua!") -> ambil naskah varian itu dari
  `varian[].naskah` di hasil draf, terapkan PERSIS perubahan yang diminta user (jangan
  menulis ulang bagian lain), lalu kirim naskah LENGKAP hasilnya lewat `--naskah "..."`.
  Kalau user menulis naskahnya sendiri seluruhnya, pakai teks user apa adanya.
- Pesan draf bisa berisi "Catatan: naskah ... lebih panjang dari bahan video". Balasan user:
  "stok" -> render dengan `--broll` (klip stok Pexels mengisi kekurangan); kirim video tambahan
  -> kumpulkan SEMUA path (lama + baru), ulangi Langkah 1 lalu Langkah 3 (draf baru); "biarkan"
  / pilih A/B saja -> render biasa (sebagian gambar diperlambat atau dipakai ulang).
- Draf BEBERAPA SHORT (varian bernama "Short 1..N"): "semua" -> `--short semua`; "A dan C" ->
  `--short A,C`; satu huruf -> `--short A`. Hasil render berisi `shorts: [...]` -- kirim tiap
  video dengan caption-nya sendiri, dan sebutkan short yang `gagal` bila ada.
- User mengoreksi pemahaman ("itu bukan antrean, itu pendaftaran") atau minta gaya lain ->
  jalankan Langkah 3 lagi dengan koreksi itu ditambahkan ke `--user-context` (draf baru).

## Langkah 5 — Render dari draf (lambat, WAJIB latar belakang)

```
python3 /root/AIHackfest/scripts/hermes_render.py \
  --chat-id "<label chat yang SAMA PERSIS>" \
  --draft-id "<draft_id dari langkah 3>" --varian A|B [--naskah "<naskah lengkap ubahan user>"]
  # draf beberapa short: ganti --varian dengan --short semua  (atau --short A,C)
```

Pengaturan (mode audio, durasi, font, musik, dst.) diambil otomatis dari draf -- TIDAK perlu
diulang. Kalau user sekarang meminta perubahan gaya (font, posisi teks, warna, musik, suara),
tambahkan flag itu saja. Mode audio, durasi, teks statis, dan konteks TERKUNCI di draf: kalau
user ingin mengubahnya, buat draf baru (Langkah 3). Satu draf hanya bisa dirender sekali
(`draf_sudah_dipakai`); render yang gagal boleh diulang dengan draf yang sama.

## Langkah 6 — Setelah render selesai

Baca output JSON (satu baris) dari proses latar belakang itu.

**CARA MENGIRIM VIDEO — baca ini sebelum apa pun.** Kirim `video_path` memakai kemampuan kirim
file/media BAWAANMU, persis seperti kamu mengirim gambar storyboard di Langkah 4. **DILARANG
KERAS** memakai terminal untuk mengirim: tidak ada perintah `telegram`, `tg`, `telegram-cli`,
`hermes send`, atau sejenisnya di mesin ini, dan **JANGAN memasang apa pun** (`apt-get install`,
`npm i`, `pip install`) untuk mencarinya. Kalau perintah kirim pertamamu gagal, JANGAN mencoba
perintah lain dan JANGAN mengulang: langsung balas user dengan satu kalimat + `video_path`-nya,
mis. "Videonya jadi tapi gagal kukirim: /root/AIHackfest/workspace/drafts/video_xxx.mp4".

(29 Sep 23:28: render SUKSES (`video_fe04a6ec.mp4`, 15 dtk, QA lolos), lalu 2 jam habis mencoba
`telegram send` → `tg` → `telegram-cli` → `apt-get install`, berujung dua kali loop pengulangan dan
pesan 35.000-56.000 karakter berisi "18.50 tbc" berulang. Videonya tidak pernah sampai ke user.)

- **`ok: true`**: kirim file di `video_path` ke chat ini (kemampuan kirim bawaanmu, BUKAN
  terminal/skrip apa pun). **Simpan `run_id`-nya** (per short untuk hasil `shorts`):
  itu kunci revisi cepat (Langkah 7). Caption ringkas dari `judul` +
  `deskripsi` + `hashtags`. Sebutkan `catatan_durasi` kalau ada isinya (artinya
  durasi diminta user dijepit ke batas yang berbeda). Kalau `qa.masalah` berisi sesuatu,
  sampaikan (mis. "ada frame hitam di detik 3"); `qa.diperbaiki` (mis. suara dikeraskan) cukup
  disebut singkat; `qa.peringatan` sebutkan bila menyangkut teks tertutup UI TikTok. Kalau
  `montase.dipakai` false padahal bahan tanpa ucapan + lagu, sebutkan `montase.alasan`.
  Kalau `catatan_naskah` berisi sesuatu,
  sebutkan singkat (naskah user tetap dipakai apa adanya).
- Kalau `catatan_bahan` berisi sesuatu (mis. "2 dari 3 bahan tampil tanpa subtitle — saldo/kuota
  API habis"), sampaikan apa adanya: penyebabnya menentukan tindak lanjut user.
- Kalau `pengisian.lambat` atau `pengisian.dipakai_ulang_detik` terisi, sebutkan singkat bahwa
  bahan video lebih pendek dari narasi, jadi sebagian gambar diperlambat atau dipakai ulang.
- **`ok: false`**: sampaikan `alasan` apa adanya ke user dalam kalimat biasa.
  Jangan mencoba lagi otomatis kalau alasannya `render_sibuk`.

Jangan pernah menjanjikan hasil atau merinci pengaturan sebelum langkah ini
selesai dan filenya benar-benar ada.

## Langkah 7 — Revisi cepat video yang sudah jadi (tanpa draf baru, WAJIB latar belakang)

Kalau user minta perubahan kecil pada video yang BARU kamu kirim, JANGAN buat draf baru. Render
ulang dari video itu: ±1 menit, tanpa panggilan AI, dan bagian yang tidak diminta TETAP sama
(klip B-roll dan lagu lainnya dikunci).

```
python3 /root/AIHackfest/scripts/hermes_render.py   --chat-id "<label chat yang SAMA PERSIS>" --revisi "<run_id video yang direvisi>" <flag perubahan>
```

| Permintaan user | Flag |
|---|---|
| "hapus B-roll ke-2" / "klip stok kedua jelek, buang" | `--hapus-broll 2` (boleh `1,3`) |
| "ganti B-roll pertama" | `--ganti-broll 1` |
| "ganti lagunya" | `--ganti-musik` (atau `--music-file <path>` bila user kirim lagu) |
| "tanpa musik" | `--music off` |
| "subtitle/teks narasi lebih tebal/mencolok" | `--subtitle-style capcut` (atau `karaoke-tebal`, `kata`, `karaoke-kapital`) |
| "teks lebih dinamis", "kata penting dibesarkan" | `--subtitle-style dinamis` |
| "efek suaranya dimatikan" / "tanpa bunyi pop" | `--sfx off` |
| "font judul/teks tulisan", "teks di atas" | `--text-font tegas`, `--text-position atas` (HANYA teks tulisan, bukan subtitle/narasi) |
| "warna lebih hangat" | `--color-filter warm` |
| "ganti gayanya jadi elegan", "coba gaya hype" | `--gaya elegan` / `--gaya hype` |
| "tanpa grafik/animasi" | `--motion mati` |
| "ejaan X salah di subtitle" | tambah ke kamus dulu (lihat "Kamus istilah"), lalu `--kamus` |
| "suara pria" | `--voice pria` |
| "ganti kalimat terakhir jadi ..." (voice-over AI) | `--naskah "<naskah LENGKAP>"`: ambil naskah lama, ubah PERSIS yang diminta |

- Nomor B-roll = urutan klip di video (urutan `broll_kredit` di hasil sebelumnya).
- Hasil revisi berisi `perubahan` (daftar yang benar-benar diubah) dan `run_id` BARU. Sebutkan
  `perubahan` saat mengirim video, dan pakai `run_id` baru itu untuk revisi berikutnya.
- Ditolak dengan `draf_terkunci` (mode audio, durasi, konteks) atau user ingin naskah/gaya yang
  benar-benar lain -> buat draf baru (Langkah 3). `revisi_tidak_ada`/`revisi_kedaluwarsa`/
  `revisi_bahan_hilang`/`musik_tidak_ada_pilihan` -> sampaikan `alasan` apa adanya.
