# Pustaka musik latar

Taruh berkas musikmu sendiri di folder ini (`.mp3`, `.m4a`, `.wav`, `.ogg`, `.flac`).

## Lisensi — baca ini dulu

Repo ini **sengaja tidak menyertakan musik komersial**. Konten yang kamu
publikasikan ke Instagram/TikTok/YouTube memakai musik berhak cipta bisa kena
mute otomatis, klaim Content ID, atau takedown. Pakai salah satu dari:

- musik yang kamu buat/beli lisensinya sendiri,
- pustaka bebas royalti dengan lisensi yang jelas,
- musik domain publik / CC0.

## Cara penamaan

Mood dipilih user lewat chat ("pakai musik lofi"), dan pencocokannya memakai
**nama berkas**. Jadi beri nama yang menyebut nuansanya:

```
lofi_santai.mp3
akustik_hangat.mp3
upbeat_energik.mp3
```

Kalau user minta mood yang tidak ada, permintaannya **ditolak** dengan daftar
yang tersedia — bukan diganti diam-diam dengan lagu lain.

## Cara kerja di pipeline

- Volume dasar `MUSIC_VOLUME` (bawaan 0.15 = pelan).
- **Auto-ducking**: musik otomatis mengecil saat ada yang bicara dan naik lagi
  saat jeda (`sidechaincompress` dengan ucapan sebagai pemicu). Terukur turun
  ~15 dB saat ada ucapan.
- Track lebih pendek dari video akan di-loop; hasil akhir tidak pernah ikut
  memanjang mengikuti musik.
- Ada limiter di ujung supaya campuran tidak pernah melewati skala penuh.
- Tidak ada track sama sekali → video tetap dibuat **tanpa** musik, dan
  alasannya dilaporkan.
