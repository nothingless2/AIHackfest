"""Entry point render untuk Hermes (menggantikan openclaw-plugin/run_and_deliver.py
sebagai jalur pengiriman Telegram).

BEDA UTAMA dari run_and_deliver.py: skrip ini TIDAK mengirim apa pun ke Telegram
sendiri. Alasannya arsitektural, bukan sekadar pilihan gaya -- lihat AGENTS.md/
HERMES.md di repo ini untuk detail penelusurannya:

- OpenClaw memberi tool-call sebuah `nativeChannelId` yang diambil KODE (closure
  server-side), bukan argumen yang diisi model -- itu yang membuat routing lewat
  CONTENT_FACTORY_CHAT_ID di run_and_deliver.py aman dipercaya.
- Hermes TIDAK memberi identitas chat yang setara ke tool MCP/terminal biasa;
  `HERMES_SESSION_CHAT_ID` cs. adalah contextvar INTERNAL Hermes (dipakai
  gateway-nya sendiri untuk notifikasi background), bukan sesuatu yang
  diteruskan sebagai env var tepercaya ke proses anak.
- Karena itu skrip ini hanya MERENDER dan mengembalikan path filenya lewat
  JSON di stdout. Pengiriman ke chat yang benar dilakukan agent Hermes SENDIRI,
  setelah dibangunkan kembali oleh notify_on_complete=true di sesi (chat) yang
  sama tempat permintaan itu berasal -- itu levelnya Hermes sendiri yang
  menjamin, sama seperti kemampuan bawaannya mengirim pesan/gambar biasa.

`--chat-id` di sini HANYA label untuk log dan pemeriksaan kepemilikan inspectId (harus
SAMA persis dengan yang dipakai saat inspect). Bukan gerbang akses, bukan tujuan kirim.
"""

import argparse
import json
import os
import shutil
import sys

from broll import BrollError, resolve_broll
from canvas import CanvasError, resolve_canvas
from common import (
    brief_path_for_run,
    draft_thumb_path_for_run,
    draft_video_path_for_run,
    ensure_dirs,
    log_error,
    PROJECT_ROOT,
    RAW_DIR,
    STATE_DIR,
    read_json,
)
from cost_estimate import ringkasan_biaya
from duration import requested_duration
from inspect_media import cek_izin
from music import MusicError, music_wanted, pick_track, requested_mood
from orchestrator import install_signal_handlers, run_core_stages_locked
from retention import sweep_old_run_files
from run_lock import FileLockBusyError, generate_run_id, sanitize_run_id
from run_log import log_event
from style import (
    StyleError, resolve_color_filter, resolve_speed_factor, resolve_text_font,
    resolve_text_position,
)

# Root folder tempat Hermes benar-benar menyimpan lampiran yang diunduh dari
# Telegram (dilihat langsung di server: ~/.hermes/cache/{videos,images,...}).
# Sama seperti verifyInbound() di openclaw-plugin: path dari model WAJIB
# realpath-nya berada di dalam salah satu root ini, bukan sekadar berawalan
# string yang sama -- symlink harus ikut ketahuan.
DEFAULT_MEDIA_ROOTS = os.path.expanduser("~/.hermes/cache")
MEDIA_ROOTS = [
    p for p in (os.getenv("HERMES_MEDIA_ROOTS") or DEFAULT_MEDIA_ROOTS).split(":")
    if p.strip()
]


class MediaPathError(ValueError):
    """Path lampiran di luar root yang diizinkan, atau tidak ada di disk."""


def _validate_media_paths(paths):
    if not paths:
        raise MediaPathError("tidak ada lampiran yang disebutkan.")
    roots_real = [os.path.realpath(r) for r in MEDIA_ROOTS]
    hasil = []
    for p in paths:
        real = os.path.realpath(p)
        if not os.path.isfile(real):
            raise MediaPathError(f"berkas tidak ditemukan: {p}")
        if not any(real == r or real.startswith(r + os.sep) for r in roots_real):
            raise MediaPathError(
                f"'{p}' berada di luar folder cache Hermes yang diizinkan "
                f"({', '.join(MEDIA_ROOTS)}). Tidak diproses."
            )
        hasil.append(real)
    return hasil


def _stage_assets(paths, run_prefix):
    """Salin lampiran tervalidasi ke workspace/raw/{prefix}_{nama}, sama seperti
    yang dilakukan openclaw-plugin/src/index.ts untuk run OpenClaw."""
    os.makedirs(RAW_DIR, exist_ok=True)
    names = []
    for src in paths:
        nama = f"{run_prefix}_{os.path.basename(src)}"
        tujuan = os.path.join(RAW_DIR, nama)
        shutil.copy2(src, tujuan)
        names.append(nama)
    return names


def _parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--media-path", action="append", default=[], dest="media_paths",
                   help="Path lampiran (boleh diulang). Wajib di dalam ~/.hermes/cache/.")
    p.add_argument("--chat-id", default="", help="Label chat: samakan persis dengan yang dipakai saat inspect. Bukan gerbang akses.")
    p.add_argument("--user-context", default="")
    p.add_argument("--inspect-id", default="", help="Hasil dari inspect_media.py inspect.")
    p.add_argument("--user-answered", action="store_true")
    p.add_argument("--require-inspect", action="store_true", default=True)
    p.add_argument("--no-require-inspect", dest="require_inspect", action="store_false")
    p.add_argument("--audio-mode", choices=["ai", "original", "mute"], default=None)
    p.add_argument("--voice-persona", default=None)
    p.add_argument("--aspect-ratio", default=None)
    p.add_argument("--fit-mode", default=None)
    p.add_argument("--edit-mode", choices=["auto", "full"], default=None)
    p.add_argument("--subtitle-style", default=None)
    p.add_argument("--static-text", action="store_true")
    p.add_argument("--music", choices=["on", "off"], default=None)
    p.add_argument("--music-mood", default=None)
    p.add_argument("--music-file", default=None, help="Path musik user, di dalam ~/.hermes/cache/.")
    p.add_argument("--duration-seconds", type=int, default=None)
    p.add_argument("--color-filter", default=None)
    p.add_argument("--speed-factor", type=float, default=None)
    p.add_argument("--auto-zoom", action="store_true")
    p.add_argument("--broll", action="store_true", help="Sisipkan B-roll stok Pexels (hanya --audio-mode ai; butuh PEXELS_API_KEY).")
    p.add_argument("--broll-query", default=None, help="Kata kunci B-roll, dipisah koma.")
    p.add_argument("--broll-count", type=int, default=None)
    p.add_argument("--text-position", default=None, help="atas|tengah|bawah (teks on-screen, bukan subtitle ucapan).")
    p.add_argument("--text-font", default=None, help="standar|tegas|modern|elegan|santai|bersih.")
    return p.parse_args(argv)


def _apply_env(args):
    mapping = {
        "CONTENT_FACTORY_AUDIO_MODE": args.audio_mode,
        "TTS_PERSONA": args.voice_persona,
        "VIDEO_ASPECT": args.aspect_ratio,
        "FIT_MODE": args.fit_mode,
        "CONTENT_FACTORY_EDIT": args.edit_mode,
        "SUBTITLE_STYLE": args.subtitle_style,
        "CONTENT_FACTORY_MUSIC": args.music,
        "CONTENT_FACTORY_MUSIC_MOOD": args.music_mood,
        "COLOR_FILTER": args.color_filter,
        "BROLL_QUERY": args.broll_query,
        "TEXT_POSITION": args.text_position,
        "TEXT_FONT": args.text_font,
        "CONTENT_FACTORY_USER_CONTEXT": args.user_context or None,
    }
    for key, value in mapping.items():
        if value:
            os.environ[key] = value
    if args.static_text:
        os.environ["CONTENT_FACTORY_STATIC_TEXT"] = "1"
    if args.duration_seconds:
        os.environ["CONTENT_FACTORY_DURATION"] = str(args.duration_seconds)
    if args.speed_factor is not None:
        os.environ["SPEED_FACTOR"] = str(args.speed_factor)
    if args.auto_zoom:
        os.environ["AUTO_ZOOM"] = "1"
    if args.broll:
        os.environ["BROLL"] = "1"
    if args.broll_count is not None:
        os.environ["BROLL_COUNT"] = str(args.broll_count)
    if args.music_file:
        (validated,) = _validate_media_paths([args.music_file])
        os.environ["CONTENT_FACTORY_MUSIC_FILE"] = validated


def _on_noncritical_failure(label, code):
    print(f"[warn] {label} gagal (exit {code}), tahap non-kritis — lanjut.")


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    install_signal_handlers()
    ensure_dirs()
    sweep_old_run_files()

    run_id = sanitize_run_id(generate_run_id())
    chat_id = args.chat_id or None

    def gagal(kode, pesan):
        print(json.dumps({"ok": False, "kode": kode, "alasan": pesan}, ensure_ascii=False))
        log_event("run_rejected", run_id, chat_id=chat_id, reason=kode)
        return 1

    # Tidak ada cek ALLOWED_CHAT_IDS di jalur ini, sengaja: Hermes menyodorkan ke
    # agent LABEL chat ("DM with Stringless"), bukan id numerik, dan nilainya diisi
    # model sehingga bisa dipalsukan -- gerbang itu keamanan semu (terbukti: run
    # asli Stringless ditolak). Kontrol akses jalur ini adalah pairing Hermes:
    # hanya user yang di-approve admin yang bisa memanggil terminal.

    try:
        media_paths = _validate_media_paths(args.media_paths)
    except MediaPathError as e:
        return gagal("lampiran_invalid", str(e))

    if args.require_inspect:
        izin = cek_izin(args.inspect_id, chat_id or "", media_paths, args.user_answered,
                        args.user_context)
        if not izin.get("ok"):
            return gagal(izin.get("kode", "gerbang_ditolak"), izin.get("teks", ""))

    run_prefix = run_id[:8]
    nama_bahan = _stage_assets(media_paths, run_prefix)
    os.environ["CONTENT_FACTORY_RUN_PREFIX"] = run_prefix
    os.environ["CONTENT_FACTORY_ASSETS"] = ",".join(nama_bahan)
    os.environ["CONTENT_FACTORY_RUN_ID"] = run_id

    _apply_env(args)

    try:
        resolve_canvas()
    except CanvasError as e:
        return gagal("kanvas_invalid", str(e))
    try:
        if music_wanted():
            pick_track(requested_mood(), run_id=run_id)
    except MusicError as e:
        return gagal("musik_invalid", str(e))
    if args.broll:
        # Ditolak SEBELUM lock dan LLM: tanpa key, atau di luar mode voice-over AI, B-roll
        # tidak mungkin berhasil -- jangan render dulu lalu bilang "tanpa B-roll".
        if args.audio_mode != "ai":
            return gagal("broll_butuh_voiceover",
                         "B-roll hanya untuk mode voice-over AI (--audio-mode ai): di mode audio "
                         "asli/mute klip sisipan menggeser subtitle dari ucapan.")
        try:
            resolve_broll()
        except BrollError as e:
            return gagal("broll_tidak_siap", str(e))
    try:
        resolve_color_filter()
        resolve_speed_factor()
        resolve_text_position()
        resolve_text_font()
    except StyleError as e:
        return gagal("gaya_invalid", str(e))

    target_durasi, pesan_durasi = requested_duration()
    if target_durasi:
        os.environ["CONTENT_FACTORY_DURATION"] = str(target_durasi)

    try:
        status, detail = run_core_stages_locked(
            run_id, chat_id=chat_id, capture_output=True,
            on_noncritical_failure=_on_noncritical_failure,
        )
    except FileLockBusyError as e:
        return gagal("render_sibuk",
                    f"masih ada render lain berjalan ({e.elapsed_seconds:.0f} detik lalu).")

    if status == "FAILED":
        code, (label, output) = detail
        tail = ("\n" + "\n".join(output.splitlines()[-4:])) if output else ""
        return gagal("render_gagal", f"gagal di tahap {label}.{tail}")

    brief = read_json(brief_path_for_run(run_id), {}) or {}
    status = read_json(os.path.join(STATE_DIR, "render_status.json"), {}) or {}
    mood = status.get("music_mood")
    hasil = {
        "ok": True,
        "run_id": run_id,
        "video_path": draft_video_path_for_run(run_id),
        "thumb_path": draft_thumb_path_for_run(run_id),
        "judul": brief.get("judul"),
        "deskripsi": brief.get("deskripsi"),
        "hashtags": brief.get("hashtags"),
        "biaya": ringkasan_biaya(run_id),
        "catatan_durasi": pesan_durasi or None,
        "catatan_teks": ("Emoji " + " ".join(status["emoji_dihapus"]) + " dihapus dari teks di layar "
                         "(font tidak mendukung emoji); tetap ada di caption.") if status.get("emoji_dihapus") else None,
        "broll": status.get("broll"),
        "broll_kredit": [d["kredit"] for d in ((status.get("broll") or {}).get("dipakai") or [])],
        "musik": status.get("music"),
        "musik_suasana": (mood or {}).get("mood"),
        "musik_tempo_bpm": (mood or {}).get("tempo_bpm") if (mood or {}).get("tempo_yakin") else None,
    }
    print(json.dumps(hasil, ensure_ascii=False))
    log_event("delivered", run_id, chat_id=chat_id)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log_error("hermes_render failure", e)
        print(json.dumps({"ok": False, "kode": "error_tak_terduga", "alasan": str(e)}))
        sys.exit(1)
