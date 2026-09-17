"""Retry untuk kegagalan sementara, dipakai bersama oleh semua panggilan jaringan.

Satu run kini melakukan lima panggilan jaringan (kata kunci vision, YouTube,
Google News, Google Trends, brief). Tanpa retry, satu hiccup 503 atau TCP reset
menggagalkan seluruh run -- termasuk membuang kerja dan kredit yang sudah terpakai
di tahap sebelumnya.

Keputusan desain:

- Hanya kegagalan SEMENTARA yang diulang. Kunci API salah atau permintaan cacat
  tidak akan membaik dengan menunggu; mengulanginya hanya memperlambat kegagalan
  dan memboroskan kuota. Klasifikasi diserahkan ke pemanggil lewat `is_retriable`,
  karena tiap layanan punya hierarki exception sendiri.

- Exponential backoff + jitter. Tanpa jitter, beberapa proses yang gagal bersamaan
  akan mencoba ulang pada detik yang sama persis dan saling menabrak lagi.

- `Retry-After` dari server dihormati, tapi DIBATASI `max_retry_after`. Server
  berhak meminta tunggu 15 menit; kita tidak berhak mematuhinya, karena tahap
  pemanggil punya timeout sendiri dan akan dibunuh paksa lebih dulu.

- Exception asli di-raise ulang apa adanya, tidak dibungkus. Pemanggil di atas
  sudah punya penanganan sendiri, dan membungkus akan menyembunyikan tipe aslinya.
"""

import asyncio
import random
import time

MAX_ATTEMPTS = 3          # 1 percobaan awal + 2 kali ulang
BASE_DELAY = 1.0
MAX_RETRY_AFTER = 30.0


def compute_delay(attempt, base_delay=BASE_DELAY):
    """Jeda sebelum percobaan berikutnya: eksponensial + jitter penuh.

    attempt=1 -> 1..2 detik, attempt=2 -> 2..3 detik (dgn base 1.0).
    """
    return base_delay * (2 ** (attempt - 1)) + random.uniform(0, base_delay)


def _delay_for(attempt, exc, base_delay, extract_retry_after, max_retry_after):
    if extract_retry_after:
        try:
            diminta = extract_retry_after(exc)
        except Exception:
            diminta = None
        if diminta:
            return min(float(diminta), max_retry_after)
    return compute_delay(attempt, base_delay)


def _laporkan(label, attempt, max_attempts, exc, jeda):
    print(
        f"[warn] {label}: percobaan {attempt}/{max_attempts} gagal "
        f"({type(exc).__name__}: {str(exc)[:120]}). Coba lagi dalam {jeda:.1f} detik."
    )


def with_retry(fn, *, is_retriable, max_attempts=MAX_ATTEMPTS, base_delay=BASE_DELAY,
               extract_retry_after=None, max_retry_after=MAX_RETRY_AFTER, label="panggilan"):
    """Jalankan `fn()` dengan retry untuk kegagalan sementara saja.

    `fn` harus tanpa argumen (pakai lambda/partial). Mengembalikan hasil `fn()`,
    atau me-raise ulang exception terakhir apa adanya.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= max_attempts or not is_retriable(exc):
                raise
            jeda = _delay_for(attempt, exc, base_delay, extract_retry_after, max_retry_after)
            _laporkan(label, attempt, max_attempts, exc, jeda)
            time.sleep(jeda)


async def with_retry_async(fn, *, is_retriable, max_attempts=MAX_ATTEMPTS,
                            base_delay=BASE_DELAY, extract_retry_after=None,
                            max_retry_after=MAX_RETRY_AFTER, label="panggilan",
                            attempt_timeout=None):
    """Versi async. `fn` adalah coroutine function tanpa argumen.

    `attempt_timeout` membatasi TIAP percobaan, bukan totalnya -- tanpa itu satu
    percobaan yang menggantung akan menahan seluruh tahap sampai dibunuh paksa.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt_timeout:
                return await asyncio.wait_for(fn(), timeout=attempt_timeout)
            return await fn()
        except Exception as exc:
            if attempt >= max_attempts or not is_retriable(exc):
                raise
            jeda = _delay_for(attempt, exc, base_delay, extract_retry_after, max_retry_after)
            _laporkan(label, attempt, max_attempts, exc, jeda)
            await asyncio.sleep(jeda)
