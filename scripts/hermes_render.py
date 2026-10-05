"""Titik masuk draf & render untuk agent Hermes (satu-satunya jalur aktif).

Skrip ini TIDAK mengirim apa pun ke Telegram sendiri. Alasannya arsitektural:

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
from motion_plan import tingkat as motion_tingkat
from overlay_remotion import animasi_diminta
from common import (
    BRIEF_PATH,
    brief_path_for_run,
    jalur_kirim,
    write_json,
    draft_thumb_path_for_run,
    draft_video_path_for_run,
    ensure_dirs,
    log_error,
    RAW_DIR,
    STATE_DIR,
    read_json,
    status_path_for_run,
)
from cost_estimate import ringkasan_biaya
from duration import requested_duration
import draf_naskah
from draf_naskah import DrafError
import gaya
import kamus
from inspect_media import cek_izin, sidik_bahan
from music import MusicError, list_tracks, music_wanted, pick_track, requested_mood
from orchestrator import (
    DRAFT_STAGES, RENDER_STAGES, install_signal_handlers, run_core_stages_locked,
)
from retention import sweep_old_run_files
import revisi
from revisi import RevisiError
from run_lock import FileLockBusyError, generate_run_id, sanitize_run_id
from run_log import log_event
from style import (
    StyleError, resolve_color_filter, resolve_speed_factor, resolve_text_font,
    resolve_text_position,
)

# Root folder tempat Hermes benar-benar menyimpan lampiran yang diunduh dari
# Telegram (dilihat langsung di server: ~/.hermes/cache/{videos,images,...}).
# Path dari model WAJIB realpath-nya berada di dalam salah satu root ini, bukan sekadar berawalan
# string yang sama -- symlink harus ikut ketahuan.
DEFAULT_MEDIA_ROOTS = os.path.expanduser("~/.hermes/cache")
MEDIA_ROOTS = [
    p for p in (os.getenv("HERMES_MEDIA_ROOTS") or DEFAULT_MEDIA_ROOTS).split(":")
    if p.strip()
]


AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"}


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
    """Salin lampiran tervalidasi ke workspace/raw/{prefix}_{nama}: bahan milik run ini
    saja (select_assets menolak nama tanpa prefix run)."""
    os.makedirs(RAW_DIR, exist_ok=True)
    names = []
    for src in paths:
        nama = f"{run_prefix}_{os.path.basename(src)}"
        tujuan = os.path.join(RAW_DIR, nama)
        shutil.copy2(src, tujuan)
        names.append(nama)
    return names


def _parser():
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
    p.add_argument("--broll", action="store_true",
                   help="B-roll stok Pexels (butuh PEXELS_API_KEY): disisipkan di mode ai, cutaway di mode asli/mute.")
    p.add_argument("--broll-query", default=None, help="Kata kunci B-roll, dipisah koma.")
    p.add_argument("--broll-count", type=int, default=None)
    p.add_argument("--voice", choices=["pria", "wanita"], default=None,
                   help="Jenis suara narasi voice-over AI (bawaan: wanita).")
    p.add_argument("--visual-cut", choices=["on", "off"], default=None,
                   help="Buang bagian goyang/oleng/buram (bawaan: on).")
    p.add_argument("--text-animation", default=None, help="pop|loncat|geser|fade|none (teks tulisan di layar).")
    p.add_argument("--text-position", default=None, help="atas|tengah|bawah (teks on-screen, bukan subtitle ucapan).")
    p.add_argument("--text-font", default=None, help="standar|tegas|modern|elegan|santai|bersih.")
    p.add_argument("--montase", choices=["on", "off"], default=None,
                   help="Bahan tanpa ucapan + musik: potongan mengikuti ketukan (bawaan on).")
    p.add_argument("--sfx", choices=["on", "off"], default=None,
                   help="Bunyi pop/whoosh di kata kunci & grafik (bawaan: on untuk --subtitle-style dinamis).")
    p.add_argument("--bersih-suara", choices=["on", "off"], default=None,
                   help="Pembersih suara ucapan asli: gerbang jeda + penegas vokal + peredam bila berisik (bawaan on).")
    p.add_argument("--potong-pengisi", choices=["on", "off"], default=None,
                   help="Buang 'eee/emm/hmm' & ulangan gagap dari ucapan asli (bawaan on).")
    p.add_argument("--zoom-wajah", choices=["on", "off"], default=None,
                   help="Zoom halus ke wajah saat kata kunci (bawaan on untuk gaya dinamis).")
    p.add_argument("--logo-merek", choices=["on", "off"], default=None,
                   help="Kartu logo merek yang diucapkan, dari daftar config/merek_logo.json.")
    p.add_argument("--gaya", default=None,
                   help="Preset gaya tampilan + editing (config/gaya/*.json), mis. klasik|bersih|hype. "
                        "Tanpa flag: gaya di profil chat, lalu 'klasik'. Flag gaya lain tetap menang.")
    p.add_argument("--cover", choices=["desain", "frame"], default=None,
                   help="Cover: 'desain' (frame terbaik + judul besar, bawaan) atau 'frame' (cara lama).")
    p.add_argument("--motion", default=None,
                   help="Motion graphic penjelas: sedang (bawaan) | mati.")
    p.add_argument("--jumlah-short", type=int, choices=[1, 2, 3], default=None,
                   help="Video panjang berucap -> N short terpisah (hanya bersama --draft).")
    p.add_argument("--short", default=None,
                   help="Render dari draf beberapa short: 'semua' atau huruf, mis. 'A,C'.")
    p.add_argument("--draft", action="store_true",
                   help="Hanya buat DRAF naskah (2 varian) untuk dipilih user; belum merender.")
    p.add_argument("--draft-id", default=None, help="Render dari draf yang dipilih user.")
    p.add_argument("--varian", default=None, help="Varian draf pilihan user: A atau B.")
    p.add_argument("--naskah", default=None,
                   help="Naskah LENGKAP hasil ubahan user (menggantikan naskah varian).")
    p.add_argument("--revisi", default=None, metavar="RUN_ID",
                   help="Revisi cepat video yang sudah jadi (run_id dari hasil render): render ulang "
                        "tanpa LLM, hanya flag yang ditulis yang berubah.")
    p.add_argument("--hapus-broll", default=None, help="Revisi: nomor B-roll yang dihapus, mis. '2' atau '1,3'.")
    p.add_argument("--ganti-broll", default=None, help="Revisi: nomor B-roll yang diganti klip lain.")
    p.add_argument("--ganti-musik", action="store_true", help="Revisi: pakai lagu lain dari pustaka.")
    p.add_argument("--kamus", action="store_true", dest="terapkan_kamus",
                   help="Revisi: terapkan kamus istilah chat ini ke subtitle video yang sudah jadi.")
    return p


def _parse_args(argv):
    return _parser().parse_args(argv)


# Pengaturan yang menentukan ISI draf (brief). Saat render dari draf, nilainya dikunci:
# mengubahnya berarti naskah yang disetujui user dibuat dari pengaturan berbeda.
DIKUNCI_DRAF = {"audio_mode", "duration_seconds", "static_text", "edit_mode", "user_context",
                "jumlah_short"}
# Tidak relevan saat render dari draf: gerbang inspect sudah dilewati saat draf dibuat.
DIABAIKAN_DRAF = {"media_paths", "inspect_id", "user_answered", "require_inspect", "chat_id",
                  "draft", "draft_id", "varian", "naskah", "short", "help",
                  "revisi", "hapus_broll", "ganti_broll", "ganti_musik", "terapkan_kamus"}

# Label perubahan gaya yang dilaporkan ke user saat revisi (flag -> nama yang dimengerti user).
LABEL_REVISI = {"music": "musik", "music_mood": "suasana musik", "music_file": "lagu kirimanmu",
                "subtitle_style": "gaya subtitle", "color_filter": "filter warna",
                "text_font": "font teks tulisan", "text_position": "posisi teks tulisan",
                "text_animation": "animasi teks tulisan", "motion": "grafik penjelas",
                "aspect_ratio": "rasio", "fit_mode": "mode bingkai", "voice": "suara narasi",
                "voice_persona": "persona suara", "speed_factor": "kecepatan",
                "visual_cut": "buang bagian goyang", "montase": "montase ketukan",
                "broll": "B-roll", "broll_query": "kata kunci B-roll", "broll_count": "jumlah B-roll",
                "auto_zoom": "zoom otomatis", "sfx": "efek suara",
                "bersih_suara": "pembersih suara", "potong_pengisi": "buang kata pengisi",
                "zoom_wajah": "zoom wajah", "logo_merek": "kartu logo", "cover": "cover",
                "gaya": "gaya tampilan"}


def _flag_eksplisit(parser, argv):
    """dest dari flag yang BENAR-BENAR ditulis di argv (bukan nilai bawaan)."""
    ditulis = {a.split("=", 1)[0] for a in argv if a.startswith("--")}
    return {a.dest for a in parser._actions if any(o in ditulis for o in a.option_strings)}


def _args_draf(args):
    """Pengaturan yang disimpan di draf (tanpa bahan & field gerbang)."""
    return {k: v for k, v in vars(args).items() if k not in DIABAIKAN_DRAF}


def _gabung_args_draf(parser, args, argv, tersimpan):
    """Pengaturan draf + flag yang ditulis ulang saat render. Flag gaya (font, warna, musik,
    ...) boleh diganti; flag yang dikunci harus sama dengan saat draf."""
    eksplisit = _flag_eksplisit(parser, argv)
    for dest in sorted(eksplisit & DIKUNCI_DRAF):
        if getattr(args, dest) != tersimpan.get(dest):
            raise DrafError("draf_terkunci",
                            f"'{dest}' sudah ditetapkan saat draf dibuat ({tersimpan.get(dest)!r}). "
                            "Untuk mengubahnya, buat draf baru.")
    for dest, nilai in tersimpan.items():
        if dest not in eksplisit and hasattr(args, dest):
            setattr(args, dest, nilai)
    return args


def _apply_env(args):
    mapping = {
        "CONTENT_FACTORY_AUDIO_MODE": args.audio_mode,
        "TTS_PERSONA": args.voice_persona,
        "TTS_VOICE_GENDER": args.voice,
        "VIDEO_ASPECT": args.aspect_ratio,
        "FIT_MODE": args.fit_mode,
        "CONTENT_FACTORY_EDIT": args.edit_mode,
        "SUBTITLE_STYLE": args.subtitle_style,
        "CONTENT_FACTORY_MUSIC": args.music,
        "CONTENT_FACTORY_MUSIC_MOOD": args.music_mood,
        "COLOR_FILTER": args.color_filter,
        "BROLL_QUERY": args.broll_query,
        "TEXT_ANIMATION": args.text_animation,
        "VISUAL_CUT": {"on": "1", "off": "0", None: None}[args.visual_cut],
        "TEXT_POSITION": args.text_position,
        "TEXT_FONT": args.text_font,
        "MOTION_GRAPHIC": args.motion,
        "SFX": args.sfx,
        "BERSIH_SUARA": args.bersih_suara,
        "POTONG_PENGISI": args.potong_pengisi,
        "ZOOM_WAJAH": args.zoom_wajah,
        "COVER": args.cover,
        "LOGO_MEREK": args.logo_merek,
        "MONTASE": {"on": "1", "off": "0", None: None}[args.montase],
        "CONTENT_FACTORY_USER_CONTEXT": args.user_context or None,
    }
    for key, value in mapping.items():
        if value:
            os.environ[key] = value
    if args.static_text:
        os.environ["CONTENT_FACTORY_STATIC_TEXT"] = "1"
    if args.duration_seconds:
        os.environ["CONTENT_FACTORY_DURATION"] = str(args.duration_seconds)
    if args.jumlah_short:
        os.environ["CONTENT_FACTORY_SHORT"] = str(args.jumlah_short)
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
        args.music_file = validated


def _sebab_subtitle(alasan):
    """" — saldo/kuota API habis (3), tidak ada ucapan terdeteksi (1)" atau "".
    Dikelompokkan per penyebab, terbanyak lebih dulu."""
    if not alasan:
        return ""
    from transcribe import ALASAN_TEKS
    hitung = {}
    for kode in alasan.values():
        hitung[kode] = hitung.get(kode, 0) + 1
    return " — " + ", ".join(f"{ALASAN_TEKS.get(k, k)} ({n})"
                             for k, n in sorted(hitung.items(), key=lambda kv: -kv[1]))


def catatan_bahan(brief):
    """Hal yang WAJIB diketahui user tentang hasil ini, disusun kode dari brief (dulu hanya
    ada di caption jalur OpenClaw yang sudah dihapus):
    - bahan tanpa subtitle BESERTA penyebabnya (saldo habis = isi ulang, waktu habis = coba
      lagi, tanpa ucapan = tidak perlu apa-apa) -- aturan #7;
    - bahan tanpa ucapan di mode suara asli: judul & teks ditebak dari tampilan saja;
    - ringkasan seleksi potongan (angkanya dihitung kode).
    Mode voice-over AI tidak memakai subtitle ucapan, jadi cakupannya tidak dilaporkan."""
    catatan = []
    cakupan = brief.get("transcript_coverage") or {}
    alasan = cakupan.get("alasan") or {}
    if (brief.get("audio_mode") == "original" and alasan
            and all(k == "tanpa_ucapan" for k in alasan.values())):
        catatan.append("Bahan tidak berisi ucapan (hanya suara suasana), jadi judul dan teks dibuat "
                       "dari TAMPILAN saja dan bisa meleset dari maksudmu. Beri tahu konteks acaranya "
                       "kalau mau disesuaikan.")
    if brief.get("edit_summary"):
        catatan.append(brief["edit_summary"])
    kurang = len(cakupan.get("tanpa_subtitle") or [])
    if kurang and brief.get("audio_mode") != "ai":
        catatan.append(f"{kurang} dari {cakupan.get('total_bahan')} bahan tampil tanpa subtitle"
                       f"{_sebab_subtitle(alasan)}.")
    return catatan


def _selesai_draf(run_id, chat_id, run_prefix, nama_bahan, args, media_paths, gaya_tampilan=None):
    """Simpan draf dari brief run ini, cetak pesan siap kirim. Belum ada video."""
    brief = read_json(brief_path_for_run(run_id), {}) or {}
    try:
        draft_id = draf_naskah.simpan(brief, chat_id=chat_id or "", prefix=run_prefix,
                                      bahan=nama_bahan, sidik=sidik_bahan(media_paths),
                                      args=_args_draf(args))
    except DrafError as e:
        print(json.dumps({"ok": False, "kode": e.kode, "alasan": str(e)}, ensure_ascii=False))
        log_event("run_rejected", run_id, chat_id=chat_id, reason=e.kode)
        return 1
    d = draf_naskah.muat(draft_id, chat_id or "")
    # Pratinjau storyboard (+ contoh suara): tampilan disetujui SEBELUM render penuh. Gagal =
    # draf tetap terkirim, alasannya dilaporkan.
    pratinjau = {"storyboard": [], "contoh_suara": None, "gagal": []}
    if (os.getenv("STORYBOARD") or "1").strip().lower() not in ("0", "off", "mati"):
        try:
            import storyboard
            pratinjau = storyboard.untuk_draf(d, draf_naskah.DRAF_DIR)
        except Exception as e:
            pratinjau["gagal"].append(f"storyboard: {type(e).__name__}: {str(e)[:120]}")
    hasil = {
        "ok": True,
        "mode": "draf",
        "run_id": run_id,
        "draft_id": draft_id,
        "pesan": draf_naskah.susun_pesan(d),
        "varian": [{"huruf": draf_naskah.HURUF[i], "gaya": v.get("gaya"), "judul": v.get("judul"),
                    "naskah": v.get("full_voice_over")} for i, v in enumerate(d["varian"])],
        "audio_mode": brief.get("audio_mode"),
        "storyboard": pratinjau["storyboard"],
        "contoh_suara": pratinjau["contoh_suara"],
        "storyboard_gagal": pratinjau["gagal"] or None,
        "gaya_tampilan": gaya_tampilan,
        "biaya": ringkasan_biaya(run_id),
    }
    hasil["kirim"] = jalur_kirim(hasil)
    print(json.dumps(hasil, ensure_ascii=False))
    log_event("draft_ready", run_id, chat_id=chat_id)
    return 0


def _status_run(run_id):
    """render_status milik run ini (salinan per run dari dalam lock), bukan berkas global yang
    bisa sudah ditimpa render berikutnya."""
    return (read_json(status_path_for_run(run_id), None)
            or read_json(os.path.join(STATE_DIR, "render_status.json"), {}) or {})


def _catat_revisi(run_id, chat_id, run_prefix, nama_bahan, args, revisi_dari=None):
    """Simpan bahan revisi cepat video ini. Gagal menyimpan TIDAK menggagalkan video yang sudah
    jadi, tapi dilaporkan: revisi berikutnya akan ditolak dengan alasan yang jelas."""
    try:
        revisi.simpan(run_id, chat_id=chat_id, prefix=run_prefix, bahan=nama_bahan,
                      args=_args_draf(args),
                      brief=read_json(brief_path_for_run(run_id), {}) or {},
                      status=_status_run(run_id), revisi_dari=revisi_dari)
        return None
    except Exception as e:
        log_error("simpan catatan revisi", e)
        return f"catatan revisi gagal disimpan ({type(e).__name__}); revisi cepat video ini tidak bisa"


def _hasil_render(run_id, pesan_durasi):
    """JSON hasil satu render (dibaca dari berkas hasil per run)."""
    brief = read_json(brief_path_for_run(run_id), {}) or {}
    status = _status_run(run_id)
    mood = status.get("music_mood")
    return {
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
        "teks_animasi": status.get("teks_animasi"),
        "motion": status.get("motion"),
        "caption": status.get("caption"),
        "panggung": status.get("panggung"),
        "suara_bersih": status.get("suara_bersih"),
        "zoom": status.get("zoom"),
        "sampul": status.get("sampul"),
        "logo": status.get("logo"),
        "potong_pengisi": {k: v for k, v in (status.get("potong_pengisi") or {}).items() if k != "rincian"} or None,
        "kamus": status.get("kamus"),
        "sfx": status.get("sfx"),
        "potongan_visual": status.get("potong_visual"),
        "suara": status.get("suara"),
        "broll": status.get("broll"),
        "broll_kredit": [d["kredit"] for d in ((status.get("broll") or {}).get("dipakai") or [])],
        "musik": status.get("music"),
        "musik_suasana": (mood or {}).get("mood"),
        "musik_tempo_bpm": (mood or {}).get("tempo_bpm") if (mood or {}).get("tempo_yakin") else None,
        "catatan_bahan": catatan_bahan(brief),
        "pengisian": status.get("pengisian"),
        "qa": status.get("qa"),
        "montase": status.get("montase"),
        "draf": brief.get("draf"),
        "catatan_naskah": ((brief.get("naskah_status") or {}).get("catatan") or None)
        if (brief.get("draf") or {}).get("diedit") else None,
    }


def _render_beberapa_short(run_id, chat_id, draf, target, pesan_durasi, gagal, args, gaya_tampilan=None):
    """Render beberapa short dari satu draf, berurutan (satu lock per short). Draf diklaim
    sekali (short pertama); short yang gagal tidak menggagalkan yang lain dan dilaporkan.
    Semua gagal -> klaim dilepas (draf boleh dicoba lagi)."""
    hasil, gagal_list, diklaim = [], [], []
    for n, (huruf, brief_k) in enumerate(target):
        rid = run_id if n == 0 else sanitize_run_id(f"{run_id[:8]}{huruf.lower()}{n}")

        def sebelum_tahap(brief_k=brief_k, rid=rid):
            if not diklaim:
                draf_naskah.klaim(draf["draft_id"])
                diklaim.append(True)
            write_json(BRIEF_PATH, {**brief_k, "brief_id": f"brief_draf_{rid}"})

        try:
            status, detail = run_core_stages_locked(
                rid, chat_id=chat_id, capture_output=True,
                on_noncritical_failure=_on_noncritical_failure,
                stages=RENDER_STAGES, sebelum_tahap=sebelum_tahap)
        except FileLockBusyError as e:
            gagal_list.append({"short": huruf, "alasan": f"render lain sedang berjalan ({e.elapsed_seconds:.0f} dtk)"})
            continue
        except DrafError as e:
            return gagal(e.kode, str(e))
        if status == "FAILED":
            _, (label, output) = detail
            gagal_list.append({"short": huruf, "alasan": f"gagal di tahap {label}: "
                                                         f"{(output or '').splitlines()[-1:] or ''}"})
            continue
        item = _hasil_render(rid, pesan_durasi)
        item["short"] = huruf
        item["revisi_gagal"] = _catat_revisi(rid, chat_id, draf["prefix"], draf["bahan"], args)
        hasil.append(item)
        log_event("delivered", rid, chat_id=chat_id)
    if not hasil:
        if diklaim:
            draf_naskah.lepas(draf["draft_id"])
        return gagal("render_gagal", "; ".join(f"short {g['short']}: {g['alasan']}" for g in gagal_list))
    print(json.dumps({"ok": True, "shorts": hasil, "gagal": gagal_list or None,
                      "gaya_tampilan": gaya_tampilan}, ensure_ascii=False))
    return 0


def _on_noncritical_failure(label, code):
    print(f"[warn] {label} gagal (exit {code}), tahap non-kritis — lanjut.")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = _parser()
    args = parser.parse_args(argv)
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

    draf = None
    rec = None
    perubahan = []
    if args.revisi:
        if args.draft or args.draft_id or args.media_paths or args.short or args.varian:
            return gagal("argumen_invalid", "--revisi tidak boleh bersama --draft/--draft-id/"
                         "--media-path/--short/--varian.")
        try:
            rec = revisi.muat(args.revisi, chat_id or "")
            eksplisit = _flag_eksplisit(parser, argv)
            args = _gabung_args_draf(parser, args, argv, rec.get("args") or {})
            if args.ganti_musik:
                if "music_file" not in eksplisit:
                    args.music_file = None          # lagu kiriman lama ikut diganti
                sisa = [t for t in list_tracks() if os.path.basename(t) != (rec["status"].get("music") or "")]
                if not args.music_file and not sisa:
                    raise RevisiError("musik_tidak_ada_pilihan",
                                      "Pustaka musik belum punya lagu lain. Kirim lagu yang kamu mau "
                                      "(berkas audio), nanti aku pasang.")
                args.music = "on"
            brief_pilihan, perubahan = revisi.brief_revisi(
                rec, hapus_broll=args.hapus_broll, ganti_broll=args.ganti_broll, naskah=args.naskah,
                broll_bebas="broll_count" in eksplisit)
        except (RevisiError, DrafError) as e:
            return gagal(e.kode, str(e))
        perubahan = [f"{LABEL_REVISI[d]}: " + (os.path.basename(str(getattr(args, d)))
                                                if d == "music_file" else str(getattr(args, d)))
                     for d in sorted(eksplisit) if d in LABEL_REVISI] + perubahan
        if args.terapkan_kamus:
            # Kamus selalu dipasang; flag ini hanya membuat "betulkan ejaan" sah sebagai revisi.
            if not kamus.daftar(chat_id or ""):
                return gagal("kamus_kosong", "Kamus istilah chat ini masih kosong. Tambahkan istilahnya dulu.")
            perubahan.append("ejaan istilah dari kamus")
        if args.ganti_musik:
            perubahan.append("musik diganti")
            os.environ["MUSIC_TRACK_HINDARI"] = rec["status"].get("music") or ""
        elif not ({"music_mood", "music_file"} & eksplisit) and rec["status"].get("music"):
            # Lagu lama dikunci: run_id baru tidak boleh memilih lagu lain diam-diam.
            os.environ["MUSIC_TRACK_TETAP"] = rec["status"]["music"]
        if not perubahan:
            return gagal("revisi_kosong", "Tidak ada perubahan yang diminta untuk revisi ini.")
        run_prefix, nama_bahan = rec["prefix"], rec["bahan"]
        if any(not os.path.isfile(os.path.join(RAW_DIR, n)) for n in nama_bahan):
            return gagal("revisi_bahan_hilang", "Bahan video itu sudah tidak ada di server. Kirim ulang bahannya.")
    elif args.hapus_broll or args.ganti_broll or args.ganti_musik or args.terapkan_kamus:
        return gagal("argumen_invalid", "--hapus-broll/--ganti-broll/--ganti-musik/--kamus hanya bersama --revisi.")
    elif args.draft_id:
        if args.draft:
            return gagal("argumen_invalid", "--draft dan --draft-id tidak boleh bersamaan.")
        try:
            sidik = None
            if args.media_paths:
                sidik = sidik_bahan(_validate_media_paths(
                    [p for p in args.media_paths if os.path.splitext(p)[1].lower() not in AUDIO_EXT]))
            draf = draf_naskah.muat(args.draft_id, chat_id or "", sidik=sidik)
            if args.short:
                # Draf beberapa short: render satu atau lebih short sekaligus.
                if not draf["brief"].get("jumlah_short"):
                    raise DrafError("argumen_invalid", "--short hanya untuk draf beberapa short.")
                huruf = draf_naskah.pilih_short(args.short, len(draf["varian"]))
            else:
                draf_naskah.indeks_varian(args.varian, len(draf["varian"]))
                huruf = [args.varian]
            args = _gabung_args_draf(parser, args, argv, draf.get("args") or {})
            target = [(h, draf_naskah.brief_terpilih(draf, h, args.naskah if len(huruf) == 1 else None))
                      for h in huruf]
            brief_pilihan = target[0][1]
        except MediaPathError as e:
            return gagal("lampiran_invalid", str(e))
        except DrafError as e:
            return gagal(e.kode, str(e))
        run_prefix = draf["prefix"]
        nama_bahan = draf["bahan"]
        hilang = [n for n in nama_bahan if not os.path.isfile(os.path.join(RAW_DIR, n))]
        if hilang:
            return gagal("draf_bahan_hilang", "Bahan draf ini sudah tidak ada di server. Kirim ulang bahannya.")
    else:
        if args.varian or args.naskah or args.short:
            return gagal("argumen_invalid", "--varian/--naskah/--short hanya bersama --draft-id.")
        if (args.jumlah_short or 0) > 1 and not args.draft:
            return gagal("short_butuh_draf", "Beberapa short dibuat lewat draf dulu (--draft), "
                         "supaya kamu memilih short mana yang dirender.")
        if (args.jumlah_short or 0) > 1 and args.audio_mode == "ai":
            return gagal("short_butuh_suara_asli", "Beberapa short memotong ucapan asli; tidak bisa "
                         "dengan voice-over AI.")
        # Berkas AUDIO yang terkirim sebagai --media-path adalah musik user, bukan bahan visual
        # Dipindah ke --music-file, bukan ditolak.
        audio = [p for p in args.media_paths if os.path.splitext(p)[1].lower() in AUDIO_EXT]
        if audio:
            if len(audio) > 1 or args.music_file:
                return gagal("musik_ganda", "Kirim satu berkas musik saja untuk satu video.")
            args.music_file = audio[0]
            args.media_paths = [p for p in args.media_paths if p not in audio]
            if args.music is None:
                args.music = "on"
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
    if args.draft:
        os.environ["CONTENT_FACTORY_DRAFT"] = "1"

    # Preset gaya: eksplisit > profil chat > bawaan. Nama efektifnya disimpan di args (ikut draf &
    # catatan revisi), knob-nya TIDAK -- supaya ganti gaya nanti tidak terkunci nilai preset lama.
    try:
        preset_gaya, sumber_gaya = gaya.pilih(args.gaya, chat_id or "")
    except gaya.GayaError as e:
        return gagal("gaya_invalid", str(e))
    args.gaya = preset_gaya["nama"]

    try:
        _apply_env(args)
    except MediaPathError as e:
        return gagal("musik_invalid", f"musik: {e}")
    gaya.pasang(preset_gaya, args)
    kamus.pasang(chat_id or "")
    info_gaya = {"nama": preset_gaya["nama"], "label": preset_gaya["label"], "sumber": sumber_gaya}
    audio_gaya = gaya.audio_terpasang(preset_gaya, args)
    if audio_gaya:
        info_gaya["audio"] = audio_gaya       # hanya gaya buatan user yang mengatur audio

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
        # Ditolak SEBELUM lock dan LLM bila tanpa key: B-roll pasti gagal -- jangan render
        # dulu lalu bilang "tanpa B-roll". Mode voice-over AI: klip disisipkan di antara bahan;
        # mode suara asli/mute: klip DITIMPA sebentar (cutaway), suara & subtitle tidak bergeser.
        try:
            resolve_broll()
        except BrollError as e:
            return gagal("broll_tidak_siap", str(e))
    try:
        resolve_color_filter()
        resolve_speed_factor()
        resolve_text_position()
        resolve_text_font()
        animasi_diminta()
        motion_tingkat()
    except StyleError as e:
        return gagal("gaya_invalid", str(e))

    target_durasi, pesan_durasi = requested_duration()
    if target_durasi:
        os.environ["CONTENT_FACTORY_DURATION"] = str(target_durasi)

    if draf and len(target) > 1:
        return _render_beberapa_short(run_id, chat_id, draf, target, pesan_durasi, gagal, args,
                                      gaya_tampilan=info_gaya)

    opsi = {}
    diklaim = []
    if rec:
        def sebelum_tahap():
            write_json(BRIEF_PATH, {**brief_pilihan, "brief_id": f"brief_revisi_{run_id}"})
        opsi = {"stages": RENDER_STAGES, "sebelum_tahap": sebelum_tahap}
    elif draf:
        def sebelum_tahap():
            # DI DALAM lock: klaim atomik dulu (dari dua render draf yang sama hanya satu
            # lolos), baru brief pilihan user ditulis ke path kerja render.
            draf_naskah.klaim(draf["draft_id"])
            diklaim.append(True)
            write_json(BRIEF_PATH, {**brief_pilihan, "brief_id": f"brief_draf_{run_id}"})
        opsi = {"stages": RENDER_STAGES, "sebelum_tahap": sebelum_tahap}
    elif args.draft:
        opsi = {"stages": DRAFT_STAGES, "salin_video": False}

    try:
        status, detail = run_core_stages_locked(
            run_id, chat_id=chat_id, capture_output=True,
            on_noncritical_failure=_on_noncritical_failure, **opsi,
        )
    except FileLockBusyError as e:
        return gagal("render_sibuk",
                    f"masih ada render lain berjalan ({e.elapsed_seconds:.0f} detik lalu).")
    except DrafError as e:
        return gagal(e.kode, str(e))
    except BaseException:
        if diklaim:      # hanya klaim MILIK run ini yang dilepas
            draf_naskah.lepas(draf["draft_id"])
        raise

    if status == "FAILED":
        if diklaim:
            draf_naskah.lepas(draf["draft_id"])      # gagal render: draf boleh dicoba lagi
        code, (label, output) = detail
        tail = ("\n" + "\n".join(output.splitlines()[-4:])) if output else ""
        return gagal("render_gagal", f"gagal di tahap {label}.{tail}")

    if args.draft:
        return _selesai_draf(run_id, chat_id, run_prefix, nama_bahan, args, media_paths,
                             gaya_tampilan=info_gaya)

    hasil = _hasil_render(run_id, pesan_durasi)
    hasil["gaya_tampilan"] = info_gaya
    hasil["revisi_gagal"] = _catat_revisi(run_id, chat_id, run_prefix, nama_bahan, args,
                                          revisi_dari=args.revisi)
    if rec:
        hasil["revisi_dari"] = args.revisi
        hasil["perubahan"] = perubahan
        lama, baru = rec["status"].get("music"), (_status_run(run_id).get("music"))
        if args.ganti_musik and lama and baru == lama:
            hasil["perubahan"] = [p for p in perubahan if p != "musik diganti"] + [
                "musik TIDAK berubah (tidak ada lagu lain yang cocok)"]
    hasil["kirim"] = jalur_kirim(hasil)
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
