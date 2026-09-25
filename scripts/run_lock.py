"""Lock antar-proses berbasis flock untuk render: hanya SATU render/draf pada satu waktu
di seluruh mesin (berkas kerja di workspace/ dipakai bersama)."""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import time
import uuid

from common import STATE_DIR, ensure_dirs

RENDER_LOCK_PATH = os.path.join(STATE_DIR, "pipeline.lock")

_RUN_ID_SAFE = re.compile(r"[^A-Za-z0-9_-]")
_MAX_RUN_ID_LEN = 32
_PREFIX_LEN = 16


class FileLockBusyError(Exception):
    """Lock sedang dipegang proses lain. `elapsed_seconds` dan `holder` (isi
    lock file, best-effort) dipakai pemanggil untuk menyusun pesan ke user."""

    def __init__(self, elapsed_seconds, holder):
        self.elapsed_seconds = elapsed_seconds
        self.holder = holder
        super().__init__(
            f"lock sedang dipegang ({elapsed_seconds:.0f} detik), holder={holder!r}"
        )


def generate_run_id() -> str:
    return uuid.uuid4().hex[:8]


def sanitize_run_id(run_id: str) -> str:
    """Buang karakter di luar [A-Za-z0-9_-]. Kalau hasil kosong: fallback ke
    generate_run_id() baru + warning (BUKAN exception — sistem tidak boleh
    crash gara-gara run_id aneh).

    Kalau hasil lebih panjang dari _MAX_RUN_ID_LEN: JANGAN dipotong buta
    (risiko tabrakan nama file kalau 2 id beda cuma di bagian yang terpotong,
    apalagi id dari luar tidak dijamin panjangnya)
    — pakai prefix pendek + hash dari string LENGKAP, supaya tetap unik.
    """
    cleaned = _RUN_ID_SAFE.sub("", run_id or "")
    if not cleaned:
        new_id = generate_run_id()
        print(f"[warn] run_id tidak valid ({run_id!r}), fallback ke run_id baru: {new_id}")
        return new_id
    if len(cleaned) <= _MAX_RUN_ID_LEN:
        return cleaned
    digest = hashlib.sha256(cleaned.encode()).hexdigest()[:8]
    result = f"{cleaned[:_PREFIX_LEN]}-{digest}"
    print(f"[info] run_id dipotong aman (prefix+hash, bukan truncate buta): {cleaned!r} -> {result!r}")
    return result


@contextlib.contextmanager
def acquire_file_lock(lock_path, holder_id):
    ensure_dirs()
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            holder = {}
            try:
                raw = os.read(fd, 4096)
                if raw:
                    holder = json.loads(raw)
            except Exception:
                pass
            started_at = holder.get("started_at_epoch")
            elapsed = (time.time() - started_at) if started_at else 0.0
            raise FileLockBusyError(elapsed, holder)

        payload = {
            "pid": os.getpid(),
            "holder_id": holder_id,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "started_at_epoch": time.time(),
        }
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(payload).encode())
        os.fsync(fd)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def acquire_render_lock(run_id):
    return acquire_file_lock(RENDER_LOCK_PATH, run_id)
