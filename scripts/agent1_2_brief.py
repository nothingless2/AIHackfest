"""Agent 1 (TrendAnalysts) + Agent 2 (BrainIdea).

Menghasilkan trend_report.json dan creative_brief.json.
Brief-nya sekaligus menjadi input render untuk Agent 3 (judul, voice-over, scenes, media_assets).
"""

import json
import os
import sys

from audio_mode import bahan_punya_suara, mode_eksplisit, requested_mode, resolve_audio_mode
from duration import duration_text, kata_untuk_bahan, requested_duration, target_dari_bahan
from edit_plan import buat_rencana, ringkas as ringkas_edit
from spoken import SPOKEN_REWRITE, prompt_rule
from transcribe import media_duration, transcribe_assets_report
from vision import IMAGE_EXTENSIONS, build_image_parts
import visual_quality as _vq

from naskah import ATURAN_GAYA, rapikan as rapikan_naskah
from draf_naskah import FIELD_VARIAN
from common import (
    LLM_MODEL,
    BRIEF_PATH,
    ensure_dirs,
    list_raw_assets,
    log_error,
    notify,
    now_iso,
    OPENAI_API_KEY,
    PERFORMANCE_PATH,
    chat_json,
    TREND_POOL_PATH,
    read_json,
    resolve_assets,
    resolve_chat_id,
    TREND_REPORT_PATH,
    write_json,
)

MODEL = os.getenv("BRIEF_MODEL") or LLM_MODEL


def select_assets():
    """Daftar bahan untuk run ini.

    workspace/raw/ dipakai bersama oleh SEMUA run dan SEMUA user, dan isinya tidak
    pernah dihapus. Jadi "pakai semua isi folder" bukan default yang aman: run milik
    user A bisa ikut menyertakan foto milik user B.

    Aturannya sekarang bergantung pada asal run:

    - Run dari plugin/Telegram (CONTENT_FACTORY_RUN_PREFIX terisi): WAJIB menyebut
      bahannya lewat CONTENT_FACTORY_ASSETS, dan setiap nama wajib berawalan
      "{prefix}_" -- yaitu file yang disalin plugin untuk run ini sendiri. Daftar
      kosong berarti GAGAL, bukan jatuh ke seluruh isi folder.

    - Run CLI manual (tanpa prefix): perilaku lama dipertahankan -- pakai
      CONTENT_FACTORY_ASSETS kalau ada, kalau tidak seluruh isi workspace/raw/.
      Di jalur ini operator sendiri yang menaruh file, jadi tidak ada percampuran
      antar-user, tapi tetap diberi peringatan.
    """
    forced = os.getenv("CONTENT_FACTORY_ASSETS", "").strip()
    run_prefix = (os.getenv("CONTENT_FACTORY_RUN_PREFIX") or "").strip()
    names = [n.strip() for n in forced.split(",") if n.strip()] if forced else []

    if run_prefix:
        if not names:
            raise ValueError(
                "Run dari plugin tidak menyebutkan bahan (CONTENT_FACTORY_ASSETS kosong). "
                "Dibatalkan — TIDAK jatuh ke seluruh isi workspace/raw/, karena folder itu "
                "berisi bahan milik run dan user lain."
            )
        asing = [n for n in names if not n.startswith(f"{run_prefix}_")]
        if asing:
            raise ValueError(
                f"Bahan berikut bukan milik run ini (prefix wajib '{run_prefix}_'): {asing}. "
                "Dibatalkan untuk mencegah bahan milik user lain ikut terpakai."
            )
    elif not names:
        names = list_raw_assets()
        if names:
            print(
                f"[warn] Run CLI tanpa CONTENT_FACTORY_ASSETS — memakai SEMUA "
                f"{len(names)} bahan di workspace/raw/. Pastikan isinya memang milik run ini."
            )

    if not names:
        raise ValueError(
            "Belum ada bahan mentah di workspace/raw/. "
            "Minta user mengupload foto/video dulu sebelum menjalankan pipeline."
        )

    resolve_assets(names)  # memvalidasi nama aman + semuanya benar-benar ada
    return names


def build_performance_note(performance):
    """Ringkasan performa untuk prompt -- hanya kalau datanya NYATA.

    Agent 5 kini menulis data_source="NO_DATA" ketika belum ada angka asli.
    Dict itu tetap truthy, jadi pengecekan harus eksplisit: tanpa ini, catatan
    "tidak ada data" ikut terkirim sebagai kalau-kalau ada isinya, dan LLM akan
    memperlakukannya sebagai sinyal.
    """
    if not performance or performance.get("data_source") != "REAL_API":
        alasan = (performance or {}).get("reason", "belum ada data performa asli")
        return (
            f"TIDAK ADA data performa asli ({alasan}). "
            "JANGAN mengarang angka, klaim performa, atau menyebut konten sebelumnya berhasil."
        )
    return json.dumps(performance, ensure_ascii=False, indent=2)


def build_trend_note(pool):
    """Kolam tren NYATA sebagai daftar bernomor. LLM hanya boleh memilih nomor."""
    items = (pool or {}).get("items") or []
    items = items[:15]  # Batasi maksimal 15 tren teratas untuk menghemat token
    if not items:
        return ("TIDAK ADA data tren yang berhasil diambil. Set trend_index = null "
                "dan tentukan sudut konten murni dari isi gambar."), []
    baris = []
    for i, it in enumerate(items):
        metric = f" [{it['metric']}]" if it.get("metric") else ""
        konteks = ""
        if it.get("context"):
            konteks = " | berita: " + "; ".join(it["context"][:2])
        baris.append(f"{i}. ({it['source']}) {it['term']}{metric}{konteks}")
    return "\n".join(baris), items


def pilih_tren(pool, trend_index):
    """Ambil item tren dari kolam berdasarkan pilihan LLM.

    Ini penegak anti-halusinasinya: LLM hanya mengembalikan NOMOR, dan seluruh
    isi trend_report diambil dari item yang benar-benar difetch. Nomor di luar
    jangkauan atau bukan angka diperlakukan sebagai "tidak memilih" -- bukan
    error, karena menolak memang jawaban yang sah.
    """
    items = (pool or {}).get("items") or []
    if trend_index is None or not items:
        return None
    try:
        idx = int(trend_index)
    except (TypeError, ValueError):
        print(f"[warn] trend_index bukan angka ({trend_index!r}) — dianggap tidak memilih.")
        return None
    if not 0 <= idx < len(items):
        print(f"[warn] trend_index {idx} di luar jangkauan 0..{len(items)-1} — dianggap tidak memilih.")
        return None
    return items[idx]


def build_transcript_note(transkrip):
    """Apa yang user BENAR-BENAR ucapkan di videonya.

    Ini sumber kebenaran terkuat yang kita punya — jauh di atas tebakan dari satu
    frame diam. Untuk video talking-head, frame hanya memperlihatkan wajah; isi
    sebenarnya seluruhnya ada di ucapan.
    """
    if not transkrip:
        return ("TIDAK ADA ucapan di bahan (gambar, video tanpa suara, atau hanya suara "
                "suasana/musik). Bertumpu pada apa yang TERLIHAT di gambar saja. JANGAN "
                "mengarang dialog atau klaim yang seolah-olah diucapkan siapa pun. JANGAN "
                "menyebut jenis acara, tujuan, nama, atau jumlah orang yang tidak bisa "
                "dipastikan dari gambar -- kalau ragu, pakai kata netral (mis. 'suasana ramai', "
                "'orang-orang berkumpul') dan biarkan user yang menambahkan konteksnya.")
    baris = [f'- "{teks}"' for teks in transkrip.values()]
    return (
        "UCAPAN ASLI user di dalam video (hasil transkripsi, BUKAN tebakan):\n"
        + "\n".join(baris)
        + "\n\nIni sumber kebenaran UTAMA. Kalau isinya bertentangan dengan tebakanmu "
          "atas gambar, MENANGKAN transkrip — gambar cuma memperlihatkan wajah dan latar, "
          "sedangkan transkrip memuat isi sebenarnya. DILARANG membuat konten bertema lain "
          "dari yang dibicarakan di transkrip."
    )


GAYA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", "gaya_naskah")
MAKS_KATA_CUPLIKAN = 30


def ukur_buruk(asset_paths):
    """{path: [(a, b, alasan)]} bagian goyang/oleng tiap video -- sama dengan yang nanti
    dibuang renderer (cache bersama). Gagal mengukur = tidak dicatat sebagai bersih: path
    itu tidak masuk hasil dan alasannya dicetak (aturan #7)."""
    if not _vq.aktif():
        return {}
    hasil = {}
    for p in asset_paths:
        if os.path.splitext(p)[1].lower() in IMAGE_EXTENSIONS:
            continue
        try:
            hasil[p] = _vq.analisis_cached(p)["buruk"]
        except Exception as e:
            print(f"[warn] ukur goyang gagal untuk {os.path.basename(p)}: {type(e).__name__}: {e}"[:200])
    return hasil


def build_klip_note(asset_names, asset_paths, transkrip, gagal_transkrip, buruk, durasi):
    """FAKTA TERUKUR per bahan, urutan sama dengan gambar. Semua diambil KODE (ffprobe,
    transkrip, analisis goyang) -- model diberi tahu apa yang terjadi di tiap klip, bukan
    hanya satu gambar diam."""
    baris = []
    for i, (nama, path) in enumerate(zip(asset_names, asset_paths), 1):
        if os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS:
            baris.append(f"{i}. FOTO.")
            continue
        d = durasi.get(path)
        bagian = [f"{i}. VIDEO {d:.1f} dtk" if d else f"{i}. VIDEO"]
        if nama in transkrip:
            kata = transkrip[nama].split()
            cuplikan = " ".join(kata[:MAKS_KATA_CUPLIKAN]) + (" ..." if len(kata) > MAKS_KATA_CUPLIKAN else "")
            bagian.append(f'ucapan: "{cuplikan}"')
        elif gagal_transkrip.get(nama) in ("tanpa_ucapan", "tanpa_audio"):
            bagian.append("tanpa ucapan (hanya suara suasana)" if gagal_transkrip[nama] == "tanpa_ucapan"
                          else "tanpa audio")
        else:
            bagian.append("ucapan tidak diketahui")
        for a, b, alasan in buruk.get(path) or []:
            bagian.append(f"detik {a:.1f}-{b:.1f} {alasan}, DIBUANG dari video akhir -- jangan dibahas")
        baris.append("; ".join(bagian) + ".")
    return ("FAKTA PER BAHAN (diukur kode, urutan sama dengan gambar):\n" + "\n".join(baris))


def contoh_gaya(maks=2, maks_karakter=900):
    """Contoh naskah kreator nyata dari config/gaya_naskah/*.txt (few-shot RITME bicara)."""
    try:
        berkas = sorted(f for f in os.listdir(GAYA_DIR) if f.endswith(".txt"))[:maks]
    except OSError:
        return ""
    potongan = []
    for f in berkas:
        try:
            with open(os.path.join(GAYA_DIR, f), encoding="utf-8") as fh:
                teks = "\n".join(b for b in fh.read().splitlines() if b.strip() and not b.startswith("#"))
        except OSError:
            continue
        if teks:
            potongan.append(teks[:maks_karakter])
    if not potongan:
        return ""
    return ("CONTOH CARA BICARA KREATOR NYATA (tiru RITME, sapaan, dan kalimat pendeknya -- "
            "JANGAN tiru topik atau kata-katanya):\n"
            + "\n---\n".join(potongan))


MOTION_NOTE = """
MOTION GRAPHIC (elemen penjelas di layar, gaya kartu gelap berpendar) di "motion_plan":
- "hook" = kartu pembuka di 2 detik pertama: inti hook naskah, maks 6 kata.
- "elemen" = 2-4 elemen yang MEMPERJELAS isi naskah; tiap elemen muncul saat kata "saat_kata"
  DIUCAPKAN narasi. Jenis: "sorot" = kata kunci penting; "ikon" = benda/aksi + emoji;
  "langkah" = tahap/cara berurutan (mis. "Daftar online"); "label" = nama acara/tempat/brand
  PERSIS dari permintaan user. Sebar kata jangkar dari tengah sampai akhir naskah, jangan
  di kalimat pertama (kartu pembuka sedang tampil).
- "cta" = kartu ajakan di 2 detik terakhir, sejalan dengan ajakan penutup naskah.
- DILARANG angka, harga, tanggal, atau statistik yang tidak ada di permintaan user."""

MOTION_NOTE_TANPA_NARASI = """
MOTION GRAPHIC di "motion_plan": hanya "hook" (kartu pembuka, maks 6 kata) dan "cta" (kartu
ajakan penutup, maks 6 kata); "elemen" dibiarkan []. DILARANG angka, harga, tanggal, atau
statistik yang tidak ada di permintaan user."""

DRAF_NOTE = """
MODE DRAF -- user akan MEMILIH salah satu dari DUA varian sebelum video dibuat:
- Buat tepat 2 varian untuk bahan dan permintaan yang SAMA, dengan GAYA yang jelas berbeda
  (mis. A: santai, lucu, penuh energi; B: hangat, menyentuh, berujung ajakan). Pilih dua gaya
  yang paling cocok dengan permintaan user.
- Hook pembuka, pilihan kata, dan ajakan penutup kedua varian HARUS berbeda -- jangan
  sekadar menukar beberapa kata.
- Kedua varian tetap wajib mematuhi SEMUA aturan di atas."""


def mode_draf():
    return (os.getenv("CONTENT_FACTORY_DRAFT") or "").strip() == "1"


def validasi_varian(varian, maks=2):
    """Varian yang bisa dipakai: dict dengan judul & full_voice_over berisi teks.
    Varian cacat dibuang (dicatat), bukan diperbaiki dengan tebakan."""
    if not isinstance(varian, list):
        return []
    hasil = []
    for i, v in enumerate(varian):
        if not isinstance(v, dict):
            print(f"[warn] varian {i + 1} bukan objek -- dibuang.")
            continue
        if not str(v.get("full_voice_over") or "").strip() or not str(v.get("judul") or "").strip():
            print(f"[warn] varian {i + 1} tanpa judul/naskah -- dibuang.")
            continue
        v = dict(v)
        v["gaya"] = str(v.get("gaya") or "").strip()[:40]
        hasil.append(v)
    return hasil[:maks]


FOTO_DETIK = 4.0     # satu foto dihitung sepanjang ini saat menakar bahan untuk naskah


def hitung_bahan_layak(asset_paths, durasi, buruk):
    """Total detik bahan yang LAYAK tampil: video tanpa bagian goyang (rentang yang sama
    dengan yang dipakai renderer, visual_quality.rentang_layak) + FOTO_DETIK per foto."""
    total = 0.0
    for p in asset_paths:
        if os.path.splitext(p)[1].lower() in IMAGE_EXTENSIONS:
            total += FOTO_DETIK
            continue
        rentang, _ = _vq.rentang_layak(durasi.get(p) or 0.0, buruk.get(p) or [])
        total += sum(z - a for a, z in rentang)
    return round(total, 1)


def build_durasi_note(durasi_bahan, bisu=False):
    """Durasi NYATA tiap bahan, supaya scene mengikuti batas antar klip.

    Tanpa ini model menulis waktu scene sesuka hati (0-5, 5-10, ...) dan teks satu
    klip terus tampil di klip berikutnya -- terlihat di video nyata: "Persiapan
    materi edukasi" masih tampil pada detik 5 padahal klip pertama selesai di 3,6.
    Angkanya diukur kode (ffprobe), bukan dikarang.
    """
    if not durasi_bahan:
        return ""
    total = sum(durasi_bahan)
    daftar = ", ".join(f"bahan {i} = {d:.1f} dtk" for i, d in enumerate(durasi_bahan, 1))
    return (
        f"DURASI NYATA TIAP BAHAN (urutan sama dengan gambar): {daftar}; total {total:.1f} dtk.\n"
        f"Video akhir berdurasi sekitar {total:.0f} dtk dan "
        + ("suara asli DIBISUKAN (yang terdengar hanya musik)" if bisu else "SUARA ASLI dipertahankan")
        + ", jadi 'full_voice_over' TIDAK dibacakan (hanya draf caption; tidak perlu 20-35 detik). "
        "Buat 'scenes' yang mengikuti batas antar bahan: satu scene per bahan, dengan "
        "start/end sesuai durasi di atas, dan scene terakhir berakhir <= total."
    )


def build_teks_statis_note():
    return (
        "TEKS ON-SCREEN STATIS: user meminta SATU teks yang sama tampil sepanjang video. Buat "
        "'scenes' berisi tepat SATU scene (start 0, end sama dengan total durasi) dengan teks "
        "singkat (maksimal 8 kata) yang menggambarkan acara atau temanya, memakai nama tempat, "
        "acara, atau brand PERSIS seperti tertulis di permintaan user. Jangan menambah klaim "
        "yang tidak disebut user."
    )


def build_prompt(asset_names, performance, *, jumlah_gambar, pool=None, konteks="",
                 transkrip=None, target_duration=None, durasi_bahan=None, teks_statis=False,
                 audio_bisu=False, klip_fakta="", gaya_contoh="", kontak=False, draf=False,
                 mode_audio="ai", bahan_layak=None):
    # Durasi & aturan lafal disuntikkan, bukan hardcode: tanpa permintaan user,
    # duration_text() mengembalikan kalimat lama kata per kata sehingga brief
    # untuk run yang tidak meminta durasi tidak berubah sama sekali.
    durasi_note = duration_text(target_duration)
    if bahan_layak and not target_duration and mode_audio == "ai":
        # Naskah menyesuaikan bahan (keputusan user 24 Sep): narasi lebih panjang dari
        # bahan layak = gambar terpaksa diperlambat/diulang.
        target_bahan = target_dari_bahan(bahan_layak)
        if target_bahan:
            kmin, kmax = kata_untuk_bahan(target_bahan)
            durasi_note = (f"sekitar {target_bahan} detik SAJA, karena bahan video yang layak "
                           f"tampil hanya ±{bahan_layak:.0f} detik (kira-kira {kmin}-{kmax} kata; "
                           "LEBIH PANJANG dari itu = gambar terpaksa diperlambat atau diulang)")
    lafal_note = prompt_rule()
    baris_spoken = (
        '    "voice_over_spoken": "naskah yang sama, ejaan fonetis untuk TTS",\n'
        if SPOKEN_REWRITE else ""
    )
    performance_note = build_performance_note(performance)
    trend_note, _ = build_trend_note(pool)
    transcript_note = build_transcript_note(transkrip)
    klip_note = build_durasi_note(durasi_bahan, bisu=audio_bisu)
    if teks_statis:
        klip_note = (klip_note + "\n\n" if klip_note else "") + build_teks_statis_note()
    konteks_note = (
        f'PERMINTAAN USER (apa adanya): "{konteks}"\n'
        "Ini yang user benar-benar inginkan. Judul, sudut, dan naskah WAJIB melayani\n"
        "permintaan ini. Kalau bertentangan dengan tren mana pun di daftar, MENANGKAN\n"
        "permintaan user dan set trend_index = null."
        if konteks else
        "PERMINTAAN USER: tidak ada — user hanya mengirim file tanpa menjelaskan maunya.\n"
        "Tentukan sudut dari isi gambar saja, dan jangan mengarang maksud user."
    )

    bagian_visual = (
        f"Kamu DIBERI {jumlah_gambar} gambar/frame dari bahan mentah user di pesan ini. "
        "LIHAT gambar-gambar itu. Seluruh konsep, judul, dan naskah WAJIB berakar pada "
        "apa yang benar-benar terlihat di sana."
        + (" Gambar dari VIDEO berupa lembar 2x2 berisi 4 momen BERURUTAN dari klip itu "
           "(kiri-atas -> kanan-atas -> kiri-bawah -> kanan-bawah): baca sebagai alur "
           "kejadian, bukan 4 hal terpisah." if kontak else "")
        if jumlah_gambar
        else
        "PERINGATAN: tidak ada gambar yang bisa diproses dari bahan user, jadi kamu TIDAK "
        "tahu isinya. Buat naskah yang sangat umum dan aman, dan JANGAN menyebut objek, "
        "tempat, merek, atau aktivitas spesifik apa pun."
    )

    elemen_skema = (
        '\n        {{"jenis": "sorot|ikon|langkah|label", "teks": "maks 4 kata", '
        '"sub": "keterangan kecil maks 5 kata, boleh kosong", "emoji": "satu emoji (wajib untuk ikon)", '
        '"saat_kata": "SATU kata yang PERSIS ada di full_voice_over"}}\n      '
        if mode_audio == "ai" else "")
    motion_note = MOTION_NOTE if mode_audio == "ai" else MOTION_NOTE_TANPA_NARASI
    isi_brief = f"""{{{{
    "judul": "string",
    "deskripsi": "deskripsi konten MAKSIMAL 30 kata untuk kolom caption platform. Harus singkat dan padat.",
    "target_trend": "string",
    "full_voice_over": "naskah voice-over MAKSIMAL 60 kata. Sangat ringkas, padat, dan langsung ke intinya.",
{baris_spoken}    "scenes": [
      {{{{"start": 0, "end": 4, "text": "teks on-screen singkat"}}}}
    ],
    "hashtags": ["#contoh"],
    "motion_plan": {{{{
      "hook": "teks kartu pembuka, maks 6 kata",
      "hook_sorot": "SATU kata dari hook yang diberi warna",
      "hook_emoji": "satu emoji atau kosong",
      "elemen": [{elemen_skema}],
      "cta": "teks kartu ajakan, maks 6 kata",
      "cta_sub": "keterangan kecil, boleh kosong",
      "cta_emoji": "satu emoji atau kosong"
    }}}}
  }}}}"""
    if draf:
        bagian_brief = (
            '  "varian": [\n'
            '  ' + isi_brief.replace('{{\n', '{{\n    "gaya": "nama gaya varian A, 2-4 kata",\n', 1) + ',\n'
            '  ' + isi_brief.replace('{{\n', '{{\n    "gaya": "nama gaya varian B, 2-4 kata",\n', 1) + '\n'
            '  ]')
        draf_note = DRAF_NOTE
    else:
        bagian_brief = '  "creative_brief": ' + isi_brief
        draf_note = ""
    bagian_brief = bagian_brief.replace("{{", "{").replace("}}", "}")

    return f"""
Bertindaklah sebagai dua agent sekaligus:
- Agent 1 TRENDANALYSTS: amati bahan user, tentukan sudut konten yang masuk akal.
- Agent 2 BRAINIDEA: ubah sudut itu jadi satu konsep konten vertikal + naskah.

{bagian_visual}

BATAS PENGETAHUANMU (penting, jangan dilanggar):
- Kamu TIDAK punya akses internet dan TIDAK tahu tren yang sedang ramai saat ini.
- DILARANG mengarang statistik, jumlah view, "sedang viral", "menurut riset",
  nama tren terkini, atau sentimen publik seolah-olah kamu mengukurnya.
- "content_angle" adalah usulanmu berdasarkan isi bahan, BUKAN hasil riset tren.

{konteks_note}

{transcript_note}

{klip_fakta}

{klip_note}

Data performa konten sebelumnya:
{performance_note}

DAFTAR TREN NYATA (hasil pengambilan data, bukan ingatanmu):
{trend_note}

Aturan memilih tren:
- Kamu HANYA boleh memilih dari daftar bernomor di atas, lewat "trend_index".
- Kalau TIDAK ADA yang benar-benar berhubungan dengan isi gambar, set
  "trend_index": null. Memaksakan tren yang tidak nyambung JAUH lebih buruk
  daripada tidak memakai tren sama sekali.
- DILARANG menulis nama tren lain di luar daftar itu.

Nama file bahan (urutan boleh disusun ulang; gambar di atas berurutan sesuai daftar ini):
{json.dumps(asset_names, ensure_ascii=False)}

Aturan keras:
- Hanya boleh memakai nama file dari daftar di atas. DILARANG mengarang nama file lain.
- DILARANG menyebut objek, orang, tempat, atau aktivitas yang TIDAK terlihat di gambar.
- Naskah voice-over harus Bahasa Indonesia, {durasi_note}.
{ATURAN_GAYA}
{gaya_contoh}
{lafal_note}
- "deskripsi" ditulis untuk dibaca calon penonton di kolom deskripsi platform,
  bukan ringkasan internal. Jangan mengulang judul apa adanya.
- "scenes" adalah teks on-screen singkat (maksimal 6 kata per scene), bukan salinan
  penuh voice-over. Waktu mulai/selesai tiap scene harus berurutan dan tidak tumpang tindih.
{motion_note}
{draf_note}

Balas HANYA JSON murni dengan struktur persis berikut:
{{
  "trend_report": {{
    "observed_material": "deskripsi FAKTUAL apa yang terlihat di gambar, 1-2 kalimat",
    "pemahaman_bahan": ["SATU kalimat faktual per bahan, urutan sama dengan gambar: apa yang terjadi di klip itu"],
    "trend_index": 0,
    "trend_reason": "kenapa tren itu nyambung dengan gambar; kosongkan kalau null",
    "content_angle": "string",
    "recommended_hook_template": "string",
    "format_style": "string"
  }},
{bagian_brief}
}}
""".strip()


def run():
    ensure_dirs()

    chat_id = resolve_chat_id()
    asset_names = select_assets()
    asset_paths = resolve_assets(asset_names)

    notify(
        "trendanalysts",
        f"mengamati {len(asset_names)} bahan mentah...",
        chat_id=chat_id,
    )

    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY belum diisi di .env")

    # Kirim ISI bahan, bukan cuma nama file. Tanpa ini model tidak pernah melihat
    # apa pun dan hanya menebak dari UUID di nama file.
    buruk = ukur_buruk(asset_paths)
    image_parts = build_image_parts(asset_paths, kontak=True, buruk=buruk)
    if not image_parts:
        print("[warn] Tidak ada gambar yang bisa diproses — brief akan dibuat tanpa melihat bahan.")
    else:
        print(f"[info] {len(image_parts)} gambar/frame dikirim ke {MODEL} untuk diamati.")

    performance = read_json(PERFORMANCE_PATH)

    konteks = (os.getenv("CONTENT_FACTORY_USER_CONTEXT") or "").strip()
    rinci, gagal_transkrip = transcribe_assets_report(asset_paths, konteks=konteks)
    transkrip = {nama: d["text"] for nama, d in rinci.items()}
    if transkrip:
        print(f"[info] {len(transkrip)} bahan berhasil ditranskrip — isi ucapan ikut dikirim.")

    # Mode audio diputuskan DI SINI karena di sinilah kita tahu apakah bahan
    # benar-benar berisi ucapan: transkrip yang tidak kosong adalah buktinya.
    ada_suara = None if transkrip else bahan_punya_suara(asset_paths)
    mode_audio, alasan_audio = resolve_audio_mode(
        requested_mode(), transkrip, eksplisit=mode_eksplisit(), gagal=gagal_transkrip,
        ada_suara=ada_suara)
    print(f"[info] mode audio: {mode_audio} — {alasan_audio}")

    pool = read_json(TREND_POOL_PATH, {}) or {}
    if konteks:
        print(f"[info] konteks user dipakai -> {konteks[:80]!r}")

    target_durasi, pesan_durasi = requested_duration(context=konteks)
    if target_durasi:
        print(f"[info] durasi diminta: {target_durasi} detik"
              + (f" ({pesan_durasi})" if pesan_durasi else ""))

    teks_statis = (os.getenv("CONTENT_FACTORY_STATIC_TEXT") or "").strip().lower() in ("1", "true", "ya", "on")
    durasi_bahan = None
    if mode_audio in ("original", "mute"):
        durasi_bahan = [d for d in (media_duration(p) for p in asset_paths) if d and d > 0]
    durasi_semua = {p: media_duration(p) for p in asset_paths
                    if os.path.splitext(p)[1].lower() not in IMAGE_EXTENSIONS}
    klip_fakta = build_klip_note(asset_names, asset_paths, transkrip, gagal_transkrip, buruk,
                                 durasi_semua)
    bahan_layak = hitung_bahan_layak(asset_paths, durasi_semua, buruk)
    klip_fakta += f"\nTOTAL BAHAN LAYAK TAMPIL: {bahan_layak:.1f} dtk."
    prompt_text = build_prompt(
        asset_names, performance, jumlah_gambar=len(image_parts), pool=pool,
        konteks=konteks, transkrip=transkrip, target_duration=target_durasi,
        durasi_bahan=durasi_bahan, teks_statis=teks_statis, audio_bisu=(mode_audio == "mute"),
        klip_fakta=klip_fakta, gaya_contoh=contoh_gaya() if mode_audio == "ai" else "",
        kontak=True, draf=mode_draf(), mode_audio=mode_audio, bahan_layak=bahan_layak,
    )
    result = chat_json(
        [{"role": "user",
          "content": [{"type": "text", "text": prompt_text}, *image_parts]}],
        model=MODEL,
        label="brief TrendAnalysts & BrainIdea",
    )

    trend_report = result["trend_report"]
    varian = None
    if mode_draf():
        varian = validasi_varian(result.get("varian"))
        if not varian:
            raise ValueError("LLM tidak menghasilkan varian naskah yang bisa dipakai untuk draf.")
        print(f"[info] draf: {len(varian)} varian naskah ({', '.join(v['gaya'] or '-' for v in varian)}).")
        brief = dict(varian[0])
    else:
        brief = result["creative_brief"]
    pemahaman = trend_report.pop("pemahaman_bahan", None)

    # Penegakan: seluruh FAKTA tren diambil dari item yang benar-benar difetch,
    # bukan dari teks LLM. LLM hanya menyumbang nomor pilihan + alasannya.
    terpilih = pilih_tren(pool, trend_report.get("trend_index"))
    trend_report.pop("trend_index", None)
    trend_report["timestamp"] = now_iso()
    if terpilih:
        trend_report["trend_source"] = terpilih["source"]
        trend_report["trend_term"] = terpilih["term"]
        trend_report["trend_metric"] = terpilih.get("metric")
        trend_report["trend_url"] = terpilih.get("url")
        trend_report["trend_query"] = terpilih.get("query")
        print(f"[info] tren dipakai: ({terpilih['source']}) {terpilih['term'][:60]}")
    else:
        trend_report["trend_source"] = "TIDAK_ADA_TREN_RELEVAN"
        trend_report["trend_term"] = None
        trend_report["trend_metric"] = None
        trend_report["trend_url"] = None
        trend_report["trend_query"] = None
        trend_report.pop("trend_reason", None)
        print("[info] tidak ada tren relevan — sudut konten murni dari isi bahan.")
    trend_report["pool_size"] = len((pool or {}).get("items") or [])
    trend_report["pool_sources"] = (pool or {}).get("sources_ok") or []

    # Zero Hallucination on Assets: daftar bahan ditentukan Python, bukan LLM.
    brief["audio_mode"] = mode_audio
    brief["audio_mode_reason"] = alasan_audio
    # Potongan bertimestamp disimpan supaya renderer bisa membuat subtitle yang
    # pas dengan ucapan asli, bukan memakai timing karangan LLM.
    brief["transcript_segments"] = {nama: d["segments"] for nama, d in rinci.items()}
    brief["transcript_words"] = {nama: d.get("words") or [] for nama, d in rinci.items()}
    bahasa = {d.get("language") for d in rinci.values() if d.get("language")}
    brief["detected_language"] = sorted(bahasa)[0] if len(bahasa) == 1 else (sorted(bahasa) or None)
    # Berapa bahan yang benar-benar punya subtitle. Tanpa angka ini, bahan yang
    # gagal/terlewat ditranskrip tampil tanpa teks dan user tidak pernah tahu
    # bedanya dengan "bahan itu memang tidak ada ucapannya".
    brief["transcript_coverage"] = {
        "ditranskrip": len(rinci),
        "total_bahan": len(asset_names),
        "tanpa_subtitle": [n for n in asset_names if n not in rinci],
        # KENAPA tiap bahan tidak bersubtitle. Bahan yang tidak ada di transkrip
        # maupun di daftar gagal berarti tidak pernah diproses (batas jumlah).
        "alasan": {n: gagal_transkrip.get(n, "tidak_diproses")
                   for n in asset_names if n not in rinci},
    }
    # Pemahaman model atas tiap bahan -- DITAMPILKAN ke user di draf supaya salah tafsir
    # terkoreksi sebelum render. Hanya string, maksimal satu per bahan.
    brief["pemahaman_bahan"] = ([str(x).strip()[:200] for x in pemahaman if str(x).strip()][:len(asset_names)]
                                if isinstance(pemahaman, list) else [])
    # Panjang bahan layak: draf membandingkannya dengan panjang naskah (tawaran bila kurang).
    brief["bahan_layak_detik"] = bahan_layak
    brief["bahan_foto"] = sum(1 for p in asset_paths if os.path.splitext(p)[1].lower() in IMAGE_EXTENSIONS)
    brief["media_assets"] = resolve_assets(asset_names)
    brief["asset_names"] = asset_names
    brief["brief_id"] = f"brief_{now_iso()}"
    # Renderer perlu tahu targetnya untuk memutuskan apakah naskah perlu
    # dikoreksi setelah TTS. None = user tidak meminta durasi = tidak ada
    # pengecekan sama sekali (perilaku lama utuh).
    brief["target_duration"] = target_durasi
    # User meminta satu teks statis sepanjang video: renderer menyeragamkan scene-nya.
    brief["static_text"] = teks_statis
    # Disimpan untuk diagnostik: tanpa ini tidak ada cara memeriksa apakah jawaban user
    # benar-benar sampai ke pipeline (brief 19 Sep 23:36 tidak memuatnya).
    brief["konteks_user"] = konteks[:800]
    brief["generated_at"] = now_iso()

    # --- Seleksi konten: pilih & urutkan potongan ucapan terbaik. Hanya untuk mode
    # audio asli (di mode voice-over AI tidak ada ucapan user yang bisa dipilih).
    # Gagal-aman: apa pun yang tidak beres -> rencana None -> render memakai
    # perilaku lama (semua klip). Alasannya SELALU dicatat di edit_status.
    rencana_edit, status_edit = None, {"status": "dilewati", "alasan": "mode voice-over AI"}
    if mode_audio in ("original", "mute"):
        if (os.getenv("CONTENT_FACTORY_EDIT") or "auto").strip().lower() == "full":
            status_edit = {"status": "dilewati",
                           "alasan": "diminta memakai semua bahan apa adanya"}
        else:
            rencana_edit, status_edit = buat_rencana(
                asset_names, rinci, gagal_transkrip,
                {n: d.get("duration") for n, d in rinci.items()},
                konteks=konteks, judul=brief.get("judul", ""),
                sudut=trend_report.get("content_angle", ""),
                target_durasi=target_durasi,
            )
    brief["edit_plan"] = rencana_edit
    brief["edit_status"] = status_edit
    brief["edit_summary"] = ringkas_edit(rencana_edit)
    if rencana_edit:
        print(f"[info] seleksi konten: {brief['edit_summary']}")
    else:
        print(f"[info] seleksi konten dilewati: {status_edit.get('alasan')}")

    if not brief.get("full_voice_over"):
        raise ValueError("LLM tidak menghasilkan 'full_voice_over'; brief tidak dapat dipakai.")
    if mode_audio == "ai":
        # Naskah HANYA dibacakan di mode voice-over AI; di mode lain ia draf caption.
        # Draf: tiap varian diperiksa sendiri (maksimal satu tulis ulang per varian).
        for i, target in enumerate(varian or [brief]):
            target, target["naskah_status"] = rapikan_naskah(
                target, konteks, chat_json, model=MODEL, label="tulis ulang naskah",
                max_attempts=1, timeout=60)
            if varian:
                varian[i] = target
            else:
                brief = target
            st = target["naskah_status"]
            if st.get("masalah"):
                print(f"[info] naskah{' varian ' + 'AB'[i] if varian else ''}: {len(st['masalah'])} ciri hambar -> "
                      + ("ditulis ulang" if st.get("ditulis_ulang") else f"TIDAK ditulis ulang ({st.get('gagal')})"))
    if varian:
        # Brief dasar = varian A (renderer lama tetap bisa membacanya); daftar varian ikut
        # disimpan untuk draf.
        for k in FIELD_VARIAN:
            if k in varian[0]:
                brief[k] = varian[0][k]
        brief["varian"] = varian

    write_json(TREND_REPORT_PATH, trend_report)
    write_json(BRIEF_PATH, brief)

    notify(
        "brainidea",
        f"konsep siap: \"{brief.get('judul', 'Untitled')}\" "
        f"({len(brief.get('scenes', []))} scene, sudut: {trend_report.get('content_angle')})",
        chat_id=chat_id,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as e:
        log_error("Agent 1&2 (brief) failure", e)
        notify("trendanalysts", f"gagal menyusun brief — {e}", chat_id=resolve_chat_id())
        sys.exit(1)
