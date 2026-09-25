"""Seleksi konten: LLM mengusulkan, KODE yang membangun kandidat dan memutuskan.

Pipeline sebelumnya hanya MENYUSUN: semua klip dipakai, urutan upload, dan yang
dibuang hanya jeda diam. Tidak ada satu pun bagian yang bertanya "bagian ini
bagus atau tidak?". Modul ini menambahkan pertanyaan itu.

Pembagian kerja (aturan 5 di CLAUDE.md: LLM tidak boleh mengeluarkan fakta):

  kode    -> membangun daftar KANDIDAT bernomor dari transkrip Whisper: klip
             mana, detik berapa, teks apa. Semua waktu berasal dari sini.
  LLM     -> hanya mengembalikan NOMOR kandidat yang dipilih, urutannya, skor,
             dan alasannya. Ia tidak pernah menulis timestamp, jadi tidak ada
             waktu karangan yang bisa masuk ke render.
  kode    -> memverifikasi tiap nomor, menegakkan batas durasi dengan membuang
             skor terendah, dan menghitung ulang seluruh rentang waktu.

GAGAL-AMAN: kalau apa pun tidak beres (transkrip tidak lengkap, LLM gagal, hasil
tidak valid), rencana TIDAK dibuat dan render memakai perilaku lama (semua klip).
Lebih baik video panjang daripada video yang isinya dipotong berdasarkan
informasi yang tidak lengkap.
"""

import difflib
import os
import re

EDIT_SELECTION = (os.getenv("EDIT_SELECTION") or "1").strip().lower() not in (
    "0", "false", "no", "off",
)

# Batas durasi hasil TANPA permintaan durasi dari user. 60 detik: batas atas
# Reels yang masih nyaman ditonton; di bawahnya LLM bebas membuang yang lemah,
# di atasnya kode memangkas paling rendah skornya.
EDIT_DEFAULT_MAX = float(os.getenv("EDIT_DEFAULT_MAX_SECONDS", "60"))
EDIT_MIN_SECONDS = float(os.getenv("EDIT_MIN_SECONDS", "8"))
# Dengan durasi diminta user, hasil boleh melebihi target sebesar ini.
EDIT_TARGET_TOLERANCE = float(os.getenv("EDIT_TARGET_TOLERANCE", "0.15"))

# Bantalan di sekitar ucapan: sedikit sebelum kata pertama dan sedikit lebih lama
# sesudah kata terakhir, supaya konsonan awal/akhir tidak terpotong.
LEAD_PAD = float(os.getenv("EDIT_LEAD_PAD", "0.15"))
TAIL_PAD = float(os.getenv("EDIT_TAIL_PAD", "0.25"))

# Segmen Whisper lebih panjang dari ini dipecah di jeda antar-kata terbesar,
# supaya LLM bisa membuang sebagian tanpa harus membuang seluruhnya.
SPLIT_LONG = float(os.getenv("EDIT_SPLIT_LONG_SECONDS", "12"))
# Dua potongan berurutan dari klip yang sama dengan jarak sekecil ini digabung
# jadi satu rentang: memotong lalu menyambung kembali hanya menghasilkan loncatan.
MERGE_GAP = float(os.getenv("EDIT_MERGE_GAP", "1.0"))

# Kemiripan teks yang dianggap "take ulang". Ambang ini dipilih longgar karena
# Whisper hampir tidak pernah mentranskrip dua take dengan kata persis sama.
SIMILAR_RATIO = float(os.getenv("EDIT_SIMILAR_RATIO", "0.72"))

# Ambang keyakinan Whisper: ini nilai BAWAAN Whisper sendiri (logprob_threshold,
# no_speech_threshold, compression_ratio_threshold), bukan angka karangan.
WHISPER_LOGPROB = -1.0
WHISPER_NO_SPEECH = 0.6
WHISPER_COMPRESSION = 2.4

# Alasan gagal-transkrip yang PASTI berarti "memang tidak ada ucapan". Selain ini
# (kuota habis, timeout, error) transkripnya TIDAK lengkap, dan memotong konten
# berdasarkan transkrip yang tidak lengkap berarti membuang ucapan yang tidak
# pernah dilihat LLM.
ALASAN_TANPA_UCAPAN = {"tanpa_audio", "tanpa_ucapan"}
# Bahan tanpa ucapan lebih dari ini -> bukan konten talking-head, jangan seleksi.
MAKS_BAHAN_TANPA_UCAPAN = 0.25


def _norm(teks):
    return re.sub(r"[^a-z0-9 ]+", "", (teks or "").lower()).strip()


def _rentang_kata(kata, mulai, selesai):
    """Kata-kata yang jatuh di dalam [mulai, selesai] (toleransi 50 ms)."""
    return [w for w in kata
            if w.get("start", 0) >= mulai - 0.05 and w.get("end", 0) <= selesai + 0.05]


def _pecah_panjang(mulai, selesai, kata, batas):
    """Pecah segmen yang terlalu panjang di jeda antar-kata terbesar.

    Mengembalikan daftar (mulai, selesai). Tanpa data kata, tidak ada yang bisa
    dipecah dengan aman, jadi dikembalikan utuh.
    """
    if selesai - mulai <= batas or len(kata) < 2:
        return [(mulai, selesai)]
    jeda = [(kata[i + 1]["start"] - kata[i]["end"], i) for i in range(len(kata) - 1)]
    besar, i = max(jeda)
    if besar <= 0:
        return [(mulai, selesai)]
    kiri, kanan = kata[: i + 1], kata[i + 1:]
    return (_pecah_panjang(kiri[0]["start"], kiri[-1]["end"], kiri, batas)
            + _pecah_panjang(kanan[0]["start"], kanan[-1]["end"], kanan, batas))


def bangun_kandidat(urutan_bahan, transkrip, durasi_file):
    """Daftar kandidat bernomor dari transkrip.

    `urutan_bahan`: nama berkas sesuai urutan upload. Nomor "klip" yang dilihat
    LLM adalah posisi di daftar ini.
    `transkrip`: {nama: {"segments": [...], "words": [...]}}.
    `durasi_file`: {nama: detik} durasi asli tiap berkas.

    Semua waktu di hasil berasal dari sini, bukan dari LLM.
    """
    kandidat, nomor = [], 0
    for posisi, nama in enumerate(urutan_bahan, 1):
        data = transkrip.get(nama)
        if not data:
            continue
        kata = data.get("words") or []
        segmen = sorted(data.get("segments") or [], key=lambda s: s.get("start", 0))
        durasi = float(durasi_file.get(nama) or 0)

        bagian = []   # (mulai, selesai, teks, segmen_asal)
        for seg in segmen:
            teks = (seg.get("text") or "").strip()
            if not teks:
                continue
            s0, s1 = float(seg.get("start", 0)), float(seg.get("end", 0))
            kw = _rentang_kata(kata, s0, s1)
            # Batas ucapan SEBENARNYA dari kata pertama/terakhir: batas segmen
            # Whisper sering menyertakan jeda di awalnya.
            if kw:
                s0, s1 = kw[0]["start"], kw[-1]["end"]
            if s1 <= s0:
                continue
            potongan = _pecah_panjang(s0, s1, kw, SPLIT_LONG)
            if len(potongan) == 1:
                bagian.append((s0, s1, teks, seg))
                continue
            for a, b in potongan:
                isi = " ".join(w["word"] for w in _rentang_kata(kw, a, b)) or teks
                bagian.append((a, b, isi, seg))

        for i, (a, b, teks, seg) in enumerate(bagian):
            nomor += 1
            sebelum = bagian[i - 1][1] if i > 0 else 0.0
            sesudah = bagian[i + 1][0] if i + 1 < len(bagian) else (durasi or b)
            c = {
                "id": nomor, "klip": posisi, "file": nama,
                "mulai": round(a, 2), "selesai": round(b, 2),
                "durasi": round(b - a, 2), "teks": teks,
                "sebelum": round(sebelum, 2), "sesudah": round(max(sesudah, b), 2),
                "durasi_file": durasi,
            }
            ragu = (
                (seg.get("avg_logprob") is not None and seg["avg_logprob"] < WHISPER_LOGPROB)
                or (seg.get("no_speech_prob") is not None and seg["no_speech_prob"] > WHISPER_NO_SPEECH)
                or (seg.get("compression_ratio") is not None and seg["compression_ratio"] > WHISPER_COMPRESSION)
            )
            if ragu:
                c["ragu"] = True
            if c["durasi"] < 1.0:
                c["sangat_pendek"] = True
            kandidat.append(c)

    # Take ulang: kandidat yang teksnya mirip dengan kandidat SEBELUMNYA.
    for i, c in enumerate(kandidat):
        terbaik, rasio_maks = None, 0.0
        for d in kandidat[:i]:
            r = difflib.SequenceMatcher(None, _norm(c["teks"]), _norm(d["teks"])).ratio()
            if r > rasio_maks:
                terbaik, rasio_maks = d, r
        if terbaik and rasio_maks >= SIMILAR_RATIO:
            c["mirip_dengan"] = terbaik["id"]
    return kandidat


def rentang_kandidat(c):
    """(a, b) rentang waktu asli untuk memutar kandidat ini, dengan bantalan.

    Bantalan dijepit ke titik tengah jeda dengan ucapan tetangga: memberi
    bantalan penuh akan menyertakan ekor kalimat sebelumnya atau kepala kalimat
    berikutnya, yaitu ucapan yang mungkin justru dibuang.
    """
    batas_kiri = (c["sebelum"] + c["mulai"]) / 2 if c["sebelum"] < c["mulai"] else c["mulai"]
    batas_kanan = (c["selesai"] + c["sesudah"]) / 2 if c["sesudah"] > c["selesai"] else c["selesai"]
    a = max(0.0, batas_kiri, c["mulai"] - LEAD_PAD)
    b = min(c["selesai"] + TAIL_PAD, batas_kanan)
    if c.get("durasi_file"):
        b = min(b, c["durasi_file"])
    return round(a, 3), round(max(b, a), 3)


def transkrip_lengkap(urutan_bahan, transkrip, gagal):
    """True kalau SETIAP bahan sudah dijelaskan: punya transkrip, atau PASTI tanpa ucapan.

    `gagal`: {nama: kode_alasan} dari transcribe_assets_report. Bahan yang tidak
    ada di transkrip DAN tidak ada di `gagal` juga dianggap tidak lengkap --
    tidak dijelaskan berarti tidak diketahui, bukan berarti aman. Kegagalan selain
    "memang tidak ada ucapan" berarti LLM akan memilih tanpa pernah melihat
    sebagian konten, dan bagian itu terbuang tanpa ada yang memutuskannya.
    """
    for nama in urutan_bahan:
        if nama in transkrip:
            continue
        if gagal.get(nama) in ALASAN_TANPA_UCAPAN:
            continue
        return False
    return True


def bangun_prompt(kandidat, *, konteks="", judul="", sudut="", max_total, min_total):
    baris = []
    for c in kandidat:
        tanda = []
        if c.get("mirip_dengan"):
            tanda.append(f"MIRIP #{c['mirip_dengan']} (kemungkinan take ulang)")
        if c.get("ragu"):
            tanda.append("UCAPAN TIDAK JELAS menurut Whisper")
        if c.get("sangat_pendek"):
            tanda.append("sangat pendek")
        ekor = f"  <-- {'; '.join(tanda)}" if tanda else ""
        baris.append(f"#{c['id']} | klip {c['klip']} | {c['durasi']:.1f} dtk | {c['teks']}{ekor}")

    konteks_baris = f'Permintaan user: "{konteks}"\n' if konteks else ""
    return f"""Kamu editor video pendek untuk Reels/TikTok. Bahannya rekaman talking-head
mentah. Tugasmu MEMILIH dan MENGURUTKAN potongan ucapan terbaik, sekaligus
menentukan mana yang dibuang.

{konteks_baris}Judul konsep: {judul or '-'}
Sudut konten: {sudut or '-'}

DAFTAR KANDIDAT (setiap baris = satu potongan ucapan yang sudah terukur; nomornya
yang kamu kembalikan, JANGAN menulis waktu atau nama berkas):
{chr(10).join(baris)}

Aturan:
- Total durasi yang dipilih maksimal {max_total:.0f} detik, minimal {min_total:.0f} detik.
- Potongan pertama harus hook yang kuat: bikin orang berhenti scroll.
- Pertahankan urutan asli kecuali memindahkan hook terkuat ke depan jelas memperbaiki alur.
- BUANG: take ulang (pilih SATU yang terbaik dari yang mirip), ucapan tidak jelas,
  kalimat yang menggantung/tidak selesai, pengulangan yang tidak menambah informasi.
- Jangan membuang yang penting hanya demi pendek: cerita harus tetap runtut.
- "transisi": "fade" HANYA di pergantian topik/bagian yang jelas (paling banyak
  sepertiga dari jumlah potongan), selebihnya "cut".

Balas HANYA JSON:
{{"pilih": [{{"id": 3, "skor": 9, "transisi": "cut", "alasan": "hook kuat, pertanyaan langsung"}}],
  "dibuang": [{{"id": 5, "alasan": "take ulang, kurang jelas dari #4"}}]}}
"skor" 1-10 = seberapa penting potongan itu untuk video (dipakai kode untuk
memangkas kalau kelebihan durasi). "pilih" ditulis DALAM URUTAN TAYANG."""


def _int(x):
    try:
        return int(str(x).strip().lstrip("#"))
    except (TypeError, ValueError):
        return None


def susun_rencana(kandidat, usulan, *, max_total, min_total=None, urutan_bahan=()):
    """Verifikasi usulan LLM dan hitung rencana final. Return (rencana, alasan_gagal).

    Yang ditegakkan KODE, apa pun kata LLM:
    - nomor harus ada di daftar kandidat (yang tidak ada dibuang dan dihitung)
    - tiap nomor dipakai paling banyak sekali
    - seluruh rentang waktu dihitung ulang dari kandidat, bukan dari LLM
    - kelebihan durasi dipangkas dengan membuang skor terendah
    - hasil terlalu pendek -> rencana ditolak
    - fade dibatasi sepertiga dari jumlah potongan
    """
    min_total = EDIT_MIN_SECONDS if min_total is None else min_total
    peta = {c["id"]: c for c in kandidat}
    pilihan = (usulan or {}).get("pilih") or []
    if not isinstance(pilihan, list):
        return None, "format 'pilih' bukan daftar"

    dipakai, tidak_valid, sudah = [], 0, set()
    for p in pilihan:
        if not isinstance(p, dict):
            tidak_valid += 1
            continue
        i = _int(p.get("id"))
        if i not in peta or i in sudah:
            tidak_valid += 1
            continue
        sudah.add(i)
        try:
            skor = max(1, min(10, int(float(p.get("skor", 5)))))
        except (TypeError, ValueError):
            skor = 5
        dipakai.append({
            "id": i, "skor": skor,
            "transisi": "fade" if str(p.get("transisi", "")).lower() == "fade" else "cut",
            "alasan": str(p.get("alasan") or "")[:140],
        })

    if not dipakai:
        return None, "tidak ada nomor valid yang dipilih LLM"

    def total(daftar):
        return sum(rentang_kandidat(peta[p["id"]])[1] - rentang_kandidat(peta[p["id"]])[0]
                   for p in daftar)

    dibuang_anggaran = []
    while total(dipakai) > max_total and len(dipakai) > 1:
        # skor terendah dulu; kalau seri, yang PALING AKHIR (mempertahankan hook)
        idx = min(range(len(dipakai)), key=lambda k: (dipakai[k]["skor"], -k))
        dibuang_anggaran.append(dipakai.pop(idx))

    detik = total(dipakai)
    if detik < min_total:
        return None, f"hasil terlalu pendek ({detik:.1f} dtk < {min_total:.0f} dtk)"

    # fade: hanya di pergantian antar-klip, dan dibatasi
    maks_fade = max(1, -(-len(dipakai) // 3))
    n_fade = 0
    picks = []
    for k, p in enumerate(dipakai):
        c = peta[p["id"]]
        a, b = rentang_kandidat(c)
        beda_klip = k > 0 and peta[dipakai[k - 1]["id"]]["file"] != c["file"]
        fade = beda_klip and p["transisi"] == "fade" and n_fade < maks_fade
        n_fade += 1 if fade else 0
        picks.append({
            "id": c["id"], "file": c["file"], "klip": c["klip"],
            "mulai": c["mulai"], "selesai": c["selesai"], "range": [a, b],
            "skor": p["skor"], "fade_masuk": bool(fade),
            "alasan": p["alasan"], "teks": c["teks"],
        })

    dibuang = []
    for d in (usulan or {}).get("dibuang") or []:
        i = _int(d.get("id")) if isinstance(d, dict) else None
        if i in peta and i not in sudah:
            dibuang.append({"id": i, "klip": peta[i]["klip"], "sebab": "llm",
                            "alasan": str(d.get("alasan") or "")[:140]})
    for p in dibuang_anggaran:
        dibuang.append({"id": p["id"], "klip": peta[p["id"]]["klip"],
                        "sebab": "anggaran durasi", "alasan": p["alasan"]})

    return {
        "status": "applied",
        "picks": picks,
        "dibuang": dibuang[:12],
        "kandidat": len(kandidat),
        "dipilih": len(picks),
        "detik_dipilih": round(detik, 1),
        "detik_bahan": round(sum(c["durasi"] for c in kandidat), 1),
        "batas_maks": round(max_total, 1),
        "nomor_tidak_valid": tidak_valid,
    }, None


def batas_durasi(target_durasi=None):
    """Batas atas total durasi: target user + toleransi, atau bawaan."""
    if target_durasi:
        return float(target_durasi) * (1 + EDIT_TARGET_TOLERANCE)
    return EDIT_DEFAULT_MAX


def buat_rencana(urutan_bahan, transkrip, gagal, durasi_file, *, konteks="", judul="",
                 sudut="", target_durasi=None, panggil_llm=None):
    """Titik masuk: (rencana|None, status). `status` selalu berisi alasan.

    `panggil_llm(prompt) -> dict` disuntikkan supaya modul ini bisa diuji tanpa
    jaringan; default-nya memakai common.chat_json dengan SATU percobaan dan
    timeout pendek -- tahap ini opsional, kegagalannya tidak boleh menahan run.
    """
    if not EDIT_SELECTION:
        return None, {"status": "dimatikan", "alasan": "EDIT_SELECTION=0"}

    if not transkrip_lengkap(urutan_bahan, transkrip, gagal):
        salah = {n: gagal.get(n, "tidak_diketahui") for n in urutan_bahan
                 if n not in transkrip and gagal.get(n) not in ALASAN_TANPA_UCAPAN}
        return None, {"status": "dilewati",
                      "alasan": f"transkrip tidak lengkap: {len(salah)} bahan gagal ditranskrip",
                      "gagal": salah}

    tanpa_ucapan = [n for n in urutan_bahan if n not in transkrip]
    if urutan_bahan and len(tanpa_ucapan) / len(urutan_bahan) > MAKS_BAHAN_TANPA_UCAPAN:
        return None, {"status": "dilewati",
                      "alasan": f"{len(tanpa_ucapan)} dari {len(urutan_bahan)} bahan tanpa "
                                "ucapan — bukan konten talking-head"}

    kandidat = bangun_kandidat(urutan_bahan, transkrip, durasi_file)
    if not kandidat:
        return None, {"status": "dilewati", "alasan": "tidak ada kandidat ucapan"}

    max_total = batas_durasi(target_durasi)
    total_bahan = sum(c["durasi"] for c in kandidat)
    if total_bahan <= EDIT_MIN_SECONDS:
        return None, {"status": "dilewati", "alasan": "bahan terlalu pendek untuk diseleksi"}

    prompt = bangun_prompt(kandidat, konteks=konteks, judul=judul, sudut=sudut,
                           max_total=max_total, min_total=EDIT_MIN_SECONDS)
    try:
        if panggil_llm is None:
            from common import LLM_MODEL, chat_json
            usulan = chat_json([{"role": "user", "content": prompt}],
                               model=os.getenv("EDIT_MODEL") or LLM_MODEL,
                               label="seleksi konten", max_attempts=1,
                               timeout=float(os.getenv("EDIT_TIMEOUT_SECONDS", "45")))
        else:
            usulan = panggil_llm(prompt)
    except Exception as e:
        return None, {"status": "dilewati",
                      "alasan": f"LLM gagal ({type(e).__name__}): {str(e)[:160]}"}

    rencana, alasan = susun_rencana(kandidat, usulan, max_total=max_total,
                                    urutan_bahan=urutan_bahan)
    if rencana is None:
        return None, {"status": "dilewati", "alasan": f"usulan LLM ditolak: {alasan}"}
    rencana["tanpa_ucapan"] = tanpa_ucapan
    return rencana, {"status": "applied", "alasan": "ok"}


def ringkas(rencana):
    """Kalimat untuk user, dihitung dari data — angkanya dari kode, bukan LLM."""
    if not rencana or rencana.get("status") != "applied":
        return ""
    s = (f"✂️ Editing: dipilih {rencana['dipilih']} dari {rencana['kandidat']} potongan "
         f"({rencana['detik_dipilih']:.0f} dari {rencana['detik_bahan']:.0f} detik bahan).")
    buang = [d for d in rencana.get("dibuang") or [] if d.get("alasan")][:3]
    if buang:
        s += " Dibuang: " + "; ".join(f"klip {d['klip']} ({d['alasan']})" for d in buang) + "."
    tanpa = len(rencana.get("tanpa_ucapan") or [])
    if tanpa:
        s += f" {tanpa} bahan tanpa ucapan tidak dipakai."
    return s


# ---------------------------------------------------------------- beberapa short
SHORT_MIN_DETIK = float(os.getenv("SHORT_MIN_SECONDS", "15"))
SHORT_MAKS_DETIK = float(os.getenv("SHORT_MAX_SECONDS", "60"))
SHORT_MAKS = 3


def bangun_prompt_banyak(kandidat, jumlah, *, konteks=""):
    baris = [f"#{c['id']} | klip {c['klip']} | {c['durasi']:.1f} dtk | {c['teks']}" for c in kandidat]
    konteks_baris = f'Permintaan user: "{konteks}"\n' if konteks else ""
    return f"""Kamu editor video pendek. Bahannya rekaman bicara yang PANJANG. Pecah jadi {jumlah} short
TERPISAH untuk TikTok/Reels, masing-masing utuh dan bisa ditonton sendiri (punya hook, isi, penutup).

{konteks_baris}DAFTAR KANDIDAT (satu baris = satu potongan ucapan terukur; kembalikan NOMORNYA saja):
{chr(10).join(baris)}

Aturan:
- Tepat {jumlah} short, tiap short {SHORT_MIN_DETIK:.0f}-{SHORT_MAKS_DETIK:.0f} detik, topik tiap short berbeda.
- Satu nomor HANYA boleh dipakai di SATU short. Potongan pertama tiap short = hook terkuat topiknya.
- Buang take ulang, ucapan tidak jelas, dan kalimat menggantung.
- "motion_plan": kartu pembuka "hook" (maks 6 kata), 1-3 "elemen" penjelas yang muncul saat kata
  "saat_kata" DIUCAPKAN di potongan short itu sendiri, dan "cta" (maks 6 kata). Tanpa angka/statistik
  yang tidak diucapkan.
- "broll": 0-2 usulan klip stok {{"query": "kata kunci Inggris", "saat_kata": "kata yang diucapkan di short itu"}}.

Balas HANYA JSON:
{{"shorts": [{{"judul": "...", "deskripsi": "caption maks 30 kata", "hashtags": ["#..."],
  "pilih": [{{"id": 3, "skor": 9, "transisi": "cut"}}],
  "motion_plan": {{"hook": "...", "hook_sorot": "", "hook_emoji": "", "elemen": [{{"jenis": "sorot", "teks": "...", "saat_kata": "..."}}], "cta": "...", "cta_sub": "", "cta_emoji": ""}},
  "broll": [{{"query": "...", "saat_kata": "..."}}]}}]}}"""


def buat_rencana_banyak(urutan_bahan, transkrip, gagal, durasi_file, *, jumlah, konteks="",
                        panggil_llm=None):
    """(shorts, status). SATU panggilan LLM membagi kandidat ucapan jadi `jumlah` short; KODE
    memvalidasi: nomor dipakai paling banyak di satu short (bentrok dibuang dari short belakangan),
    tiap short lolos susun_rencana dengan batas SHORT_MIN..SHORT_MAKS detik. Short yang gagal
    dibuang dan dicatat. Tiap short: {judul, deskripsi, hashtags, motion_plan, broll, edit_plan}."""
    jumlah = max(1, min(SHORT_MAKS, int(jumlah)))
    if not transkrip_lengkap(urutan_bahan, transkrip, gagal):
        return [], {"status": "dilewati", "alasan": "transkrip tidak lengkap"}
    kandidat = bangun_kandidat(urutan_bahan, transkrip, durasi_file)
    if not kandidat:
        return [], {"status": "dilewati", "alasan": "tidak ada kandidat ucapan"}
    total_bahan = sum(c["durasi"] for c in kandidat)
    if total_bahan < SHORT_MIN_DETIK * jumlah:
        return [], {"status": "dilewati",
                    "alasan": f"ucapan hanya {total_bahan:.0f} dtk, kurang untuk {jumlah} short "
                              f"@{SHORT_MIN_DETIK:.0f} dtk"}
    prompt = bangun_prompt_banyak(kandidat, jumlah, konteks=konteks)
    try:
        if panggil_llm is None:
            from common import LLM_MODEL, chat_json
            usulan = chat_json([{"role": "user", "content": prompt}],
                               model=os.getenv("EDIT_MODEL") or LLM_MODEL, label="seleksi beberapa short",
                               max_attempts=1, timeout=float(os.getenv("EDIT_TIMEOUT_SECONDS", "90")))
        else:
            usulan = panggil_llm(prompt)
    except Exception as e:
        return [], {"status": "dilewati", "alasan": f"LLM gagal ({type(e).__name__}): {str(e)[:160]}"}

    shorts, catatan, terpakai = [], [], set()
    for k, s in enumerate((usulan or {}).get("shorts") or [], 1):
        if not isinstance(s, dict) or len(shorts) >= jumlah:
            continue
        pilih = [p for p in (s.get("pilih") or []) if isinstance(p, dict)]
        bentrok = [p for p in pilih if _int(p.get("id")) in terpakai]
        if bentrok:
            catatan.append(f"short {k}: {len(bentrok)} potongan sudah dipakai short lain -- dibuang")
        pilih = [p for p in pilih if _int(p.get("id")) not in terpakai]
        rencana, alasan = susun_rencana(kandidat, {"pilih": pilih}, max_total=SHORT_MAKS_DETIK,
                                        min_total=SHORT_MIN_DETIK, urutan_bahan=urutan_bahan)
        if rencana is None:
            catatan.append(f"short {k} dibuang: {alasan}")
            continue
        terpakai.update(p["id"] for p in rencana["picks"])
        shorts.append({"judul": str(s.get("judul") or f"Short {len(shorts) + 1}")[:120],
                       "deskripsi": str(s.get("deskripsi") or "")[:400],
                       "hashtags": [str(h)[:40] for h in (s.get("hashtags") or [])][:8],
                       "motion_plan": s.get("motion_plan") if isinstance(s.get("motion_plan"), dict) else None,
                       "broll": s.get("broll") if isinstance(s.get("broll"), list) else [],
                       "edit_plan": rencana})
    if not shorts:
        return [], {"status": "dilewati", "alasan": "; ".join(catatan) or "LLM tidak mengusulkan short yang sah"}
    return shorts, {"status": "applied", "alasan": "ok", "diminta": jumlah, "jadi": len(shorts),
                    "catatan": catatan}
