"""Draf naskah: BrainIdea mengusulkan DUA varian, user memilih (dan boleh mengubah kalimatnya)
SEBELUM render. Permintaan user 24 Sep: "memberikan saran naskah kepadaku sebelum video
ingin dirender".

Alur: hermes_render.py --draft (brief saja, di dalam lock) -> simpan() -> pesan ke user ->
hermes_render.py --draft-id X --varian A [--naskah "..."] -> muat() + brief_terpilih() ->
tahap render saja. Brief TIDAK dibuat ulang saat render: yang dirender persis yang user setujui.

Gagal-tertutup (aturan #4): draf milik chat lain, kedaluwarsa, bahannya berbeda, atau sudah
dipakai -> ditolak dengan alasan yang bisa dibaca, bukan dirender diam-diam.
"""

import json
import os
import re
import secrets
import time

from common import STATE_DIR, write_json
from duration import perkiraan_detik
from naskah import periksa

DRAF_DIR = os.path.join(STATE_DIR, "draf_naskah")
DRAF_TTL_JAM = float(os.getenv("DRAF_TTL_HOURS", "24"))
_ID = re.compile(r"^[0-9a-f]{12}$")
HURUF = "ABC"
MAKS_NASKAH_USER = 1500
# Narasi lebih panjang dari bahan layak x ini -> gambar harus diperlambat/dipakai ulang:
# user diberi tahu dan ditawari pilihan (keputusan user 24 Sep: naskah menyesuaikan bahan).
BATAS_KURANG = 1.15


def bahan_kurang(teks, brief):
    """(narasi_detik, bahan_detik) bila narasi jelas lebih panjang dari bahan video layak,
    selain itu None. Foto bisa tampil sepanjang apa pun, jadi bahan berfoto tidak dianggap kurang."""
    bahan = brief.get("bahan_layak_detik")
    if brief.get("audio_mode") != "ai" or not bahan or brief.get("bahan_foto"):
        return None
    narasi = perkiraan_detik(teks)
    return (narasi, bahan) if narasi > bahan * BATAS_KURANG else None

# Field yang BERBEDA antar-varian; sisanya (transkrip, rencana edit, mode audio, bahan) sama.
FIELD_VARIAN = ("gaya", "judul", "deskripsi", "full_voice_over", "voice_over_spoken", "scenes",
                "hashtags", "target_trend", "motion_plan", "naskah_status", "broll",
                "edit_plan", "edit_summary")


class DrafError(ValueError):
    def __init__(self, kode, pesan):
        super().__init__(pesan)
        self.kode = kode


def _path(draft_id):
    if not _ID.match(str(draft_id or "")):
        raise DrafError("draf_invalid", "draft-id tidak valid.")
    return os.path.join(DRAF_DIR, f"{draft_id}.json")


def _path_klaim(draft_id):
    return _path(draft_id)[:-5] + ".dipakai"


def simpan(brief, *, chat_id, prefix, bahan, sidik, args):
    """Simpan draf dari brief mode draf (berisi `varian`). Return draft_id."""
    varian = brief.get("varian") or []
    if not varian:
        raise DrafError("draf_kosong", "BrainIdea tidak menghasilkan varian naskah.")
    draft_id = secrets.token_hex(6)
    dasar = {k: v for k, v in brief.items() if k != "varian"}
    write_json(_path(draft_id), {
        "draft_id": draft_id, "chat_id": str(chat_id or ""), "dibuat": time.time(),
        "prefix": prefix, "bahan": bahan, "sidik": sidik, "args": args,
        "brief": dasar, "varian": varian,
    })
    return draft_id


def muat(draft_id, chat_id, *, sidik=None):
    """Draf yang sah untuk chat ini, atau DrafError. `sidik` diisi bila pemanggil ikut
    menyebut bahan: harus sama dengan bahan saat draf dibuat."""
    p = _path(draft_id)
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        raise DrafError("draf_tidak_ada", "Draf tidak ditemukan. Buat draf baru dari bahan ini.")
    except (OSError, ValueError):
        raise DrafError("draf_rusak", "Draf rusak. Buat draf baru dari bahan ini.")
    if str(d.get("chat_id")) != str(chat_id or ""):
        raise DrafError("draf_chat_lain", "Draf ini bukan milik chat ini.")
    if time.time() - float(d.get("dibuat") or 0) > DRAF_TTL_JAM * 3600:
        raise DrafError("draf_kedaluwarsa",
                        f"Draf sudah lebih dari {DRAF_TTL_JAM:.0f} jam. Kirim ulang bahannya untuk draf baru.")
    if sidik is not None and sorted(sidik) != sorted(d.get("sidik") or []):
        raise DrafError("draf_bahan_beda", "Bahan yang disebut tidak sama dengan bahan draf ini.")
    if os.path.exists(_path_klaim(draft_id)):
        raise DrafError("draf_sudah_dipakai",
                        "Draf ini sudah dirender. Minta draf baru kalau ingin versi lain.")
    return d


def klaim(draft_id):
    """Tandai draf dipakai -- atomik (O_EXCL): dari dua render bersamaan hanya satu lolos.
    Dipanggil DI DALAM lock render."""
    try:
        fd = os.open(_path_klaim(draft_id), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise DrafError("draf_sudah_dipakai",
                        "Draf ini sudah dirender. Minta draf baru kalau ingin versi lain.")
    os.write(fd, str(time.time()).encode())
    os.close(fd)


def lepas(draft_id):
    """Render gagal: draf boleh dicoba lagi."""
    try:
        os.remove(_path_klaim(draft_id))
    except OSError:
        pass


def indeks_varian(huruf, jumlah):
    h = str(huruf or "").strip().upper()
    if len(h) != 1 or h not in HURUF[:jumlah]:
        raise DrafError("varian_invalid", f"Pilih salah satu: {', '.join(HURUF[:jumlah])}.")
    return HURUF.index(h)


def pilih_short(teks, jumlah):
    """'semua' / 'A,C' / 'a c' -> ['A', 'C'] (urut, unik), atau DrafError."""
    t = str(teks or "").strip().lower()
    if t in ("semua", "all", "*"):
        return list(HURUF[:jumlah])
    huruf = sorted({x for x in re.split(r"[\s,;]+", t.upper()) if x})
    salah = [h for h in huruf if len(h) != 1 or h not in HURUF[:jumlah]]
    if not huruf or salah:
        raise DrafError("varian_invalid", f"Pilih 'semua' atau huruf short: {', '.join(HURUF[:jumlah])}.")
    return huruf


def brief_terpilih(d, huruf, naskah=None):
    """Brief siap render dari varian pilihan user. Naskah ubahan user dipakai APA ADANYA --
    pemeriksa gaya hanya memberi catatan, tidak pernah menimpa kalimat user."""
    i = indeks_varian(huruf, len(d["varian"]))
    v = d["varian"][i]
    brief = dict(d["brief"])
    for k in FIELD_VARIAN:
        if k in v:
            brief[k] = v[k]
    info = {"id": d["draft_id"], "varian": HURUF[i], "diedit": False}
    if naskah is not None:
        teks = " ".join(str(naskah).split())
        if not teks:
            raise DrafError("naskah_kosong", "Naskah ubahan kosong.")
        if len(teks) > MAKS_NASKAH_USER:
            raise DrafError("naskah_terlalu_panjang",
                            f"Naskah ubahan lebih dari {MAKS_NASKAH_USER} karakter.")
        brief["full_voice_over"] = teks
        brief["voice_over_spoken"] = teks
        info["diedit"] = True
        catatan = periksa(teks)
        kurang = bahan_kurang(teks, d["brief"])
        if kurang:
            catatan.append(f"naskah ±{kurang[0]:.0f} dtk, bahan video layak ±{kurang[1]:.0f} dtk: "
                           "sebagian gambar akan diperlambat atau dipakai ulang")
        brief["naskah_status"] = {"sumber": "user", "catatan": catatan}
        # Naskah user dipakai APA ADANYA: koreksi durasi otomatis saat render (yang menulis
        # ulang naskah bila meleset dari target) tidak boleh menyentuhnya.
        brief["target_duration"] = None
    brief["draf"] = info
    return brief


def _potong(teks, n=400):
    teks = " ".join(str(teks or "").split())
    return teks if len(teks) <= n else teks[:n].rsplit(" ", 1)[0] + " ..."


def susun_pesan(d):
    """Pesan draf siap kirim (teks biasa) -- disusun KODE supaya isinya persis draf."""
    brief = d["brief"]
    ai = brief.get("audio_mode") == "ai"
    baris, kurang_varian = [], []
    paham = brief.get("pemahaman_bahan") or []
    if paham:
        baris.append(f"Saya sudah menonton {len(d.get('bahan') or [])} bahan. Yang saya tangkap:")
        baris += [f"{i}. {t}" for i, t in enumerate(paham, 1)]
        baris.append("(Kalau ada yang keliru, koreksi dulu, nanti saya buatkan draf baru.)")
        baris.append("")
    for i, v in enumerate(d["varian"]):
        judul = f"{HURUF[i]}. {v.get('gaya') or 'Varian ' + HURUF[i]}"
        baris.append(judul)
        baris.append(f"Judul: {v.get('judul', '')}")
        if ai:
            baris.append(f"Naskah (dibacakan suara AI): \"{_potong(v.get('full_voice_over'))}\"")
            detik = perkiraan_detik(v.get("full_voice_over"))
            bahan = brief.get("bahan_layak_detik")
            baris.append(f"Durasi: narasi ±{detik:.0f} dtk" + (f" · bahan video layak ±{bahan:.0f} dtk" if bahan else ""))
            if bahan_kurang(v.get("full_voice_over"), brief):
                kurang_varian.append(HURUF[i])
        else:
            # Short dari video panjang: ucapan MILIK short itu sendiri (potongan terpilihnya).
            ucapan = v.get("full_voice_over") if v.get("edit_plan") and brief.get("jumlah_short") \
                else teks_ucapan(brief)
            if brief.get("jumlah_short") and v.get("edit_plan"):
                baris.append(f"Durasi: ±{v['edit_plan'].get('detik_dipilih', 0):.0f} dtk "
                             f"({v['edit_plan'].get('dipilih')} potongan)")
            if ucapan:
                # Mode suara asli: subtitle = ucapanmu sendiri (bukan tulisan AI).
                baris.append(f"Subtitle dari ucapanmu: \"{_potong(ucapan, 160)}\"")
            else:
                teks = [s.get("text", "") for s in (v.get("scenes") or []) if isinstance(s, dict)]
                if teks:
                    baris.append("Teks di layar: " + " / ".join(t for t in teks if t))
            baris.append(f"Caption: {_potong(v.get('deskripsi'), 200)}")
        broll = [b for b in (v.get("broll") or []) if isinstance(b, dict) and b.get("query")]
        if broll:
            baris.append("B-roll (bila diminta): " + " · ".join(
                f"'{b['query']}'" + (f" saat \"{b['saat_kata']}\"" if b.get("saat_kata") else "")
                for b in broll))
        st = v.get("naskah_status") or {}
        sisa = st.get("sisa") if st.get("ditulis_ulang") else st.get("masalah")
        if ai and sisa:
            # Pemeriksa gaya tidak berhasil membereskannya: user yang memutuskan.
            baris.append("Catatan pemeriksa: " + "; ".join(sisa[:2]))
        grafik = ringkas_grafik(v.get("motion_plan"),
                                naskah=v.get("full_voice_over") if ai else teks_ucapan(brief), brief=brief)
        if grafik:
            baris.append(f"Grafik: {grafik}")
        baris.append("")
    if kurang_varian:
        from broll import tersedia as broll_tersedia
        opsi = ["kirim video tambahan (saya buatkan draf baru)"]
        if broll_tersedia():
            opsi.append("balas \"stok\" untuk mengisi dengan klip stok Pexels yang relevan")
        opsi.append("biarkan saja: sebagian gambar diperlambat atau dipakai ulang")
        baris.append(f"Catatan: naskah {', '.join(kurang_varian)} lebih panjang dari bahan video. "
                     "Pilihanmu: " + "; ".join(opsi) + ".")
        baris.append("")
    if brief.get("jumlah_short"):
        baris.append(f"Balas \"semua\" untuk membuat {len(d['varian'])} short sekaligus, atau pilih "
                     "hurufnya (mis. \"A, C\").")
        return "\n".join(baris).strip()
    pilihan = " atau ".join(HURUF[:len(d["varian"])])
    baris.append(f"Balas {pilihan}. Mau mengubah kalimatnya? Tulis saja versimu, "
                 f"mis. \"A, tapi pembukanya: ...\".")
    return "\n".join(baris).strip()


def teks_ucapan(brief):
    """Seluruh ucapan asli (transkrip) sebagai satu teks: yang terdengar di mode suara asli."""
    return " ".join(str(seg.get("text") or "").strip()
                    for segs in (brief.get("transcript_segments") or {}).values()
                    for seg in (segs or []) if isinstance(seg, dict)).strip()


def ringkas_grafik(plan, *, naskah="", brief=None):
    """Ringkasan rencana motion graphic untuk pesan draf -- HANYA yang lolos pemeriksaan kode
    (katalog, angka, kata jangkar; motion_plan.bersihkan), supaya user tidak dijanjikan elemen
    yang nanti dibuang. Waktunya ditentukan saat render dari suara narasi."""
    if not isinstance(plan, dict):
        return ""
    import motion_plan as mp
    brief = brief or {}
    ucapan = [str(seg.get("text") or "") for segs in (brief.get("transcript_segments") or {}).values()
              for seg in (segs or []) if isinstance(seg, dict)]
    bersih, _ = mp.bersihkan(plan, naskah=naskah, sumber_fakta=" ".join([brief.get("konteks_user") or ""] + ucapan),
                             pakai_jangkar=brief.get("audio_mode") == "ai" or bool(naskah))
    bagian = []
    if bersih["hook"]:
        bagian.append(f"kartu pembuka \"{bersih['hook']['teks']}\"")
    bagian += [f"{el['jenis']} \"{el['teks']}\"" for el in bersih["elemen"]]
    if bersih["cta"]:
        bagian.append(f"kartu ajakan \"{bersih['cta']['teks']}\"")
    return " · ".join(bagian)
