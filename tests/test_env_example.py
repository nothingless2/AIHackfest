"""`.env.example` disalin jadi `.env` (deploy/bootstrap.sh melakukannya), lalu python-dotenv memuat SEMUA baris,
termasuk yang nilainya kosong. Nilai kosong BUKAN "tidak diset": os.getenv("K", "2") mengembalikan "" bukan "2".

Asal (3 Okt): `LLM_FALLBACK_ROUNDS=` ditambahkan kosong ke .env.example dengan komentar "Kosong = 2". Salah:
scripts/common.py membaca int(os.getenv("LLM_FALLBACK_ROUNDS", "2")) -> int('') -> seluruh pipeline gagal saat
di-import. Ketahuan hanya karena bootstrap dijalankan di mesin bersih; tidak ada tes yang memuat .env.example.
"""

import os
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _kunci_env_example():
    """{KUNCI: nilai} dari baris aktif (bukan komentar) di .env.example, kosong tetap kosong."""
    hasil = {}
    with open(os.path.join(RAIZ, ".env.example"), encoding="utf-8") as f:
        for baris in f:
            baris = baris.strip()
            if not baris or baris.startswith("#") or "=" not in baris:
                continue
            kunci, nilai = baris.split("=", 1)
            hasil[kunci.strip()] = nilai.strip()
    return hasil


def _impor_modul_utama(env_tambahan):
    """Impor modul yang membaca env saat import, di proses TERPISAH (env bersih dari conftest)."""
    env = dict(os.environ)
    env.update(env_tambahan)
    kode = ("import sys; sys.path[:0] = ['scripts', 'skills/video_generator']; "
            "import common, music, terbit, auto_render")
    return subprocess.run([sys.executable, "-c", kode], cwd=RAIZ, env=env,
                          capture_output=True, text=True, timeout=180)


def test_env_example_punya_kunci_aktif():
    """Kontrol: pembaca benar-benar menemukan kunci (hasil kosong bukan bukti)."""
    kunci = _kunci_env_example()
    assert len(kunci) > 20
    assert "OPENAI_API_KEY" in kunci


def test_salinan_env_example_tidak_merusak_import():
    """Memuat SETIAP kunci aktif di .env.example (kosong tetap kosong) tidak boleh menggagalkan import."""
    r = _impor_modul_utama(_kunci_env_example())
    assert r.returncode == 0, "import gagal dengan nilai dari .env.example:\n" + r.stderr[-800:]


def _nilai_modul(env_tambahan, ekspresi):
    """Nilai `ekspresi` setelah modul transcribe diimpor dengan env tertentu (proses terpisah)."""
    env = dict(os.environ)
    env.update(env_tambahan)
    kode = f"import sys; sys.path[:0] = ['scripts']; import transcribe; print({ekspresi})"
    r = subprocess.run([sys.executable, "-c", kode], cwd=RAIZ, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-600:]
    return r.stdout.strip()


def test_salinan_env_example_tidak_menghapus_kosakata_whisper():
    """Asal (3 Okt): TRANSCRIBE_VOCAB= kosong di .env.example menggantikan daftar bawaan (getenv(k, default)
    mengembalikan "" untuk nilai kosong), sehingga "leads" kembali ditranskrip "lid" tanpa pesan apa pun."""
    assert _nilai_modul(_kunci_env_example(), "'leads' in transcribe.TRANSCRIBE_VOCAB") == "True"


def test_kontrol_positif_kosakata_bisa_diganti_dan_bisa_hilang():
    """Kontrol: nilai yang DIISI user memang dipakai (alat ukur melihat perbedaan), jadi 'leads' ada
    pada tes di atas karena daftar bawaan, bukan karena pemeriksaannya selalu True."""
    assert _nilai_modul({"TRANSCRIBE_VOCAB": "hanya-istilah-ini"},
                        "'leads' in transcribe.TRANSCRIBE_VOCAB") == "False"
    assert _nilai_modul({"TRANSCRIBE_VOCAB": "hanya-istilah-ini"},
                        "'hanya-istilah-ini' in transcribe.TRANSCRIBE_VOCAB") == "True"


def test_kontrol_positif_nilai_rusak_memang_tertangkap():
    """Tanpa ini 'import lolos' bisa berarti alat ukurnya buta. Nilai non-angka HARUS membuat import gagal."""
    r = _impor_modul_utama({"LLM_FALLBACK_ROUNDS": "abc"})
    assert r.returncode != 0
    assert "LLM_FALLBACK_ROUNDS" in r.stderr or "invalid literal" in r.stderr


def test_kontrol_positif_nilai_kosong_pada_angka_memang_berbahaya():
    """Mendokumentasikan alasan LLM_FALLBACK_ROUNDS tidak boleh kosong di .env.example."""
    r = _impor_modul_utama({"LLM_FALLBACK_ROUNDS": ""})
    assert r.returncode != 0, "kalau ini lolos, common.py sudah menoleransi kosong: boleh perbarui komentar .env.example"
