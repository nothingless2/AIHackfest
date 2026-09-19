# Aturan kerja di repo ini

Ditulis setelah kesalahan nyata yang merusak data. Tiap aturan punya sebab yang
konkret, bukan nasihat umum.

## 1. Hasil kosong BUKAN bukti. Sertakan kontrol positif.

Kalau sebuah pemeriksaan menghasilkan nol/kosong/tidak-ada, itu punya dua
kemungkinan yang tidak bisa dibedakan tanpa kontrol positif:

- memang tidak ada, atau
- **pemeriksaannya yang tidak bekerja**

Sebelum menyimpulkan apa pun dari hasil kosong, buktikan dulu alat ukurnya bisa
mendeteksi kasus positif.

Contoh nyata di repo ini:
- `journalctl --user` menjawab kosong dan disimpulkan "tidak ada error di log".
  Yang sebenarnya terjadi: `No journal files were found` — perintahnya memang
  tidak punya akses, jadi ia tidak akan pernah menampilkan error apa pun.
- `pgrep -af ffmpeg` "menemukan proses" yang ternyata perintah bash-nya sendiri.
  Pakai `pgrep -x` untuk nama persis.
- `check_locks.py` diuji dengan memegang lock sungguhan lebih dulu, lalu dicek
  bebas lagi setelah dilepas. Itu yang membuat hasil "bebas" bisa dipercaya.

## 2. Cocokkan timestamp sebelum menyimpulkan penyebab.

Jangan membangun teori di atas snapshot yang diambil pada waktu yang salah.

Contoh nyata: `ls workspace/raw/` dijalankan **7 detik sebelum** plugin menyalin
file ke sana. Hasilnya kosong, lalu dibangun teori panjang bahwa "path dari model
salah" — padahal path-nya benar dan file-nya masuk beberapa detik kemudian.
Semua kesimpulan turunannya ikut salah.

Sebelum menyatakan sebab-akibat: tulis urutan kejadiannya dengan jam, dan pastikan
pemeriksaanmu terjadi **setelah** peristiwa yang kamu periksa. Ingat juga log
internal memakai UTC sedangkan `ls` memakai waktu lokal (WIB, UTC+7).

## 3. Jangan restart gateway tanpa memastikan lock bebas.

Gateway berjalan sebagai service systemd dengan `KillMode=mixed`: restart
mengirim SIGKILL ke **seluruh cgroup**, termasuk proses yang di-spawn `detached`.

Contoh nyata: satu render milik user hilang persis begitu — mati di
`_combined_text.mp4`, **satu langkah** sebelum `mux_audio` selesai. Tidak ada
`run_finished` (proses di-SIGKILL, blok `finally` tidak sempat jalan) dan tidak
ada pesan apa pun ke user.

Selalu jalankan lebih dulu:

```bash
python3 scripts/check_locks.py
```

`scripts/install_plugin.sh` sudah menolak restart secara otomatis kalau lock
dipegang. Lewati hanya dengan `SKIP_LOCK_CHECK=1`, dan hanya kalau kamu memang
bermaksud menghentikan pekerjaan itu.

Render baru di-spawn lewat `systemd-run --user --scope` sehingga punya cgroup
sendiri dan selamat dari restart — tapi jalur cadangan (kalau `systemd-run` tidak
ada) dan approval CLI yang sedang menunggu balasan tetap rentan.

## 4. Gagal-tertutup, bukan gagal-terbuka.

Pola yang berulang di repo ini, dan alasannya selalu sama: sistem dipakai lebih
dari satu orang, dan tebakan yang salah berarti materi seseorang sampai ke orang
lain.

- Chat tujuan tidak diketahui → **tolak**, jangan jatuh ke chat cadangan.
- `ALLOWED_CHAT_IDS` kosong → **tidak ada** yang diizinkan, bukan semua.
- Bahan tidak disebutkan → **gagal**, jangan pakai seluruh isi `workspace/raw/`
  (folder itu berisi materi milik run dan user lain).
- Path lampiran dari model → wajib berada di dalam folder inbound setelah
  `realpath`. **Jangan pernah** memindai folder atau memakai "file terbaru"
  sebagai cadangan.

## 5. LLM tidak boleh mengeluarkan fakta.

LLM mengusulkan kandidat; **kode** yang mengukur dan memberi peringkat.

- Tren: LLM hanya mengembalikan NOMOR dari daftar yang benar-benar difetch, atau
  null. Seluruh isi `trend_report` diambil kode dari item aslinya.
- Angka performa: kalau tidak ada data asli, tulis `NO_DATA` — jangan mengarang.
  (`estimate_metrics()` dulu memakai `random.randint` dan melaporkan
  "HIGH_PERFORMING" padahal belum ada satu pun post terbit.)
- Biaya: token itu data nyata dari API; harga dari `config/pricing.json`. Model
  yang tidak ada di tabel → biayanya `None`, bukan nol dan bukan tebakan.

## 6. Test tidak boleh menyentuh jaringan atau state asli.

`tests/conftest.py` memblokir `urlopen`/`requests` dan mengosongkan env Telegram
untuk seluruh test. Semua path state wajib diarahkan ke `tmp_path`.

Contoh nyata: satu test memanggil `agent5.run()` tanpa mengalihkan
`TREND_POOL_PATH`, sehingga tiap kali suite dijalankan ia menimpa
`workspace/state/` asli **dan** benar-benar meminta data ke Google Trends.

## 7. Kegagalan BUKAN ketiadaan.

"Gagal mengambil data" dan "datanya memang kosong" harus dibedakan secara eksplisit,
dan hanya yang kedua yang boleh memicu jalur "tidak ada apa-apa".

Contoh nyata: saldo Whisper habis (`403 insufficient_quota`) -> 0 transkrip ->
`resolve_audio_mode` menyimpulkan "tidak ada ucapan di bahan" -> naskah dikarang dari
gambar saja ("Halo semuanya! Aku di sini dengan energi positif...") -> suara AI
ditempel di atas video user yang sebenarnya berbicara. Docstring kodenya sendiri
menulis "video bisu, musik saja, atau transkripsi gagal" sebagai satu kelompok.

Aturannya:
- Pemeriksaan yang bisa gagal mengembalikan alasan gagalnya (`transcribe_assets_report`
  -> `{nama: kode_alasan}`), bukan cuma hasil kosong.
- Hanya alasan yang PASTI berarti "kosong" (`tanpa_ucapan`, `tanpa_audio`) yang boleh
  memicu jalur kosong. Selain itu **berhenti dengan pesan yang bisa dibaca user**,
  sebelum ada panggilan LLM berbayar dan sebelum ada yang diganti.
- Pola yang sama berlaku di `edit_plan.transkrip_lengkap()`: memotong konten dari
  transkrip separuh berarti membuang ucapan yang tidak pernah dilihat LLM.

## 8. Verifikasi dengan menjalankan, bukan dengan membaca kode.

Laporkan hasil apa adanya: kalau test gagal, tunjukkan outputnya; kalau langkah
dilewati, katakan. Klaim "sudah diperbaiki" tanpa eksekusi tidak berlaku di sini —
beberapa bug di repo ini justru ditemukan oleh test setelah kode "terlihat benar".
