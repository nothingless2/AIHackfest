"""Gaya naskah voice-over: terdengar seperti KREATOR yang bicara, bukan narator dokumentasi.

Asal: user menilai naskah "hambar" (24 Sep). Naskah hasil sebelumnya: "dokumentasi ini
menyajikan ruang utama yang ramai dan meja layanan yang terlihat secara ringkas" --
mendeskripsikan gambar. Video referensi user (kreator TikTok, ditranskrip Whisper):
"Woi kalian, lihat ... Ini ya, ini kita ngedit di VS Code teman-teman" -- menyapa penonton,
kalimat pendek, bahasa lisan, ~2,6 kata/detik, tidak pernah menjelaskan apa yang tampak.

Pembagian kerja (aturan #5): prompt MEMINTA gaya itu; KODE memeriksa ciri hambar yang
bisa diukur (frasa deskriptif, kalimat panjang) dan meminta SATU kali tulis ulang.
"""

import re

ATURAN_GAYA = """- GAYA NASKAH voice-over: seperti kreator TikTok/Reels yang BICARA ke penontonnya,
  bukan narator dokumentasi atau berita.
  * Buka dengan hook 1 kalimat yang memancing: sapaan/reaksi/pertanyaan ("Kalian pernah
    ...?", "Jujur, ini bikin merinding.", "Tunggu sampai akhir, ya.").
  * Sapa penonton langsung ("kalian", "kamu", "teman-teman"). Bahasa lisan sehari-hari
    Indonesia; boleh kata seru wajar ("nih", "tuh", "ya", "loh", "gila", "jujur").
  * Kalimat PENDEK: maksimal 12 kata per kalimat. Ritme cepat, sekitar 2,5 kata per detik.
  * Ceritakan MAKNA / perasaan / ajakan -- BUKAN apa yang terlihat. DILARANG
    mendeskripsikan gambar: jangan pakai "video ini", "dokumentasi ini", "menampilkan",
    "menyajikan", "terlihat", "tampak", "rangkaian acara", "momen kebersamaan".
  * Tutup dengan SATU ajakan konkret yang nyambung dengan konteks user.
  * Tidak mengarang fakta (angka, nama, tanggal) yang tidak ada di permintaan user."""

POLA_HAMBAR = [
    r"\bvideo ini\b", r"\bdokumentasi ini\b", r"\bkonten ini\b", r"\bmenyajikan\b",
    r"\bmenampilkan\b", r"\bditampilkan\b", r"\bterlihat\b", r"\btampak\b",
    r"\brangkaian acara\b", r"\bmomen kebersamaan\b", r"\bsecara ringkas\b",
    r"\bberikut ini\b", r"\bpada kesempatan ini\b",
]
MAKS_KATA_KALIMAT = 16      # sedikit longgar dari aturan prompt (12) supaya tidak cerewet
# Huruf non-Latin yang bocor dari model gratis (terukur 24 Sep: "lalu确认 datang bareng" di
# naskah bahasa Indonesia). TTS membacanya sebagai bahasa lain atau melewatinya.
HURUF_ASING = re.compile(r"[\u0400-\u04FF\u0590-\u06FF\u0E00-\u0E7F\u3040-\u30FF"
                         r"\u3400-\u9FFF\uAC00-\uD7AF\uF900-\uFAFF]+")


def periksa(teks):
    """Daftar masalah yang TERUKUR pada naskah; [] = lolos."""
    t = (teks or "").strip()
    if not t:
        return []
    masalah = []
    asing = HURUF_ASING.findall(t)
    if asing:
        masalah.append(f'huruf asing "{" ".join(asing[:3])}" -- tulis dalam bahasa Indonesia')
    for pola in POLA_HAMBAR:
        m = re.search(pola, t, re.I)
        if m:
            masalah.append(f'frasa deskriptif "{m.group(0)}"')
    for kalimat in re.split(r"(?<=[.!?])\s+", t):
        n = len(kalimat.split())
        if n > MAKS_KATA_KALIMAT:
            masalah.append(f"kalimat terlalu panjang ({n} kata): \"{kalimat[:50]}...\"")
    return masalah


def prompt_tulis_ulang(brief, masalah, konteks):
    return f"""Tulis ulang naskah voice-over berikut supaya terdengar seperti kreator yang
bicara langsung ke penonton, bukan narator dokumentasi.

MASALAH YANG DITEMUKAN (wajib hilang semua):
{chr(10).join('- ' + m for m in masalah)}

ATURAN:
{ATURAN_GAYA}
- Panjang kata sama (boleh selisih 15%), makna dan ajakan tetap.
- Permintaan user: "{konteks or '-'}"

NASKAH LAMA:
{brief.get('full_voice_over', '')}

Balas HANYA JSON: {{"full_voice_over": "...", "voice_over_spoken": "versi yang sama dengan
ejaan fonetis untuk TTS (kata asing dieja Indonesia, angka dieja)"}}"""


def rapikan(brief, konteks, chat_json, **kw):
    """(brief, catatan). Maksimal SATU panggilan LLM; gagal = naskah lama dipertahankan."""
    masalah = periksa(brief.get("full_voice_over"))
    if not masalah:
        return brief, {"diperiksa": True, "masalah": [], "ditulis_ulang": False}
    try:
        baru = chat_json([{"role": "user", "content": prompt_tulis_ulang(brief, masalah, konteks)}], **kw)
        fvo = (baru or {}).get("full_voice_over", "").strip()
        if not fvo:
            raise ValueError("jawaban tanpa full_voice_over")
        sisa = periksa(fvo)
        hasil = {**brief, "full_voice_over": fvo}
        if baru.get("voice_over_spoken"):
            hasil["voice_over_spoken"] = baru["voice_over_spoken"].strip()
        elif "voice_over_spoken" in hasil:
            hasil["voice_over_spoken"] = fvo
        return hasil, {"diperiksa": True, "masalah": masalah, "ditulis_ulang": True, "sisa": sisa}
    except Exception as e:
        return brief, {"diperiksa": True, "masalah": masalah, "ditulis_ulang": False,
                       "gagal": f"{type(e).__name__}: {str(e)[:100]}"}
