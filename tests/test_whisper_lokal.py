"""Whisper lokal + aturan "kegagalan transkripsi BUKAN 'tidak ada ucapan'".

Latar belakang nyata (19 Sep): saldo Whisper habis -> 0 transkrip -> sistem
menyimpulkan "tidak ada ucapan" -> naskah dikarang dari gambar ("Halo semuanya!
Aku di sini dengan energi positif...") lalu suara AI ditempel di atas video user
yang sebenarnya berbicara.

Worker asli TIDAK PERNAH dijalankan di sini (conftest memblokirnya). Jalur lokal
diuji dengan worker PALSU yang berbicara protokol yang sama: satu objek JSON per
baris di stdout.
"""

import json
import os
import stat
import sys
import textwrap

import pytest

import audio_mode as am
import transcribe as t


# ---------- worker palsu ----------

def _worker_palsu(tmp_path, perilaku):
    """Tulis skrip worker palsu. `perilaku`: kode Python badan `main(berkas)`."""
    skrip = tmp_path / "worker_palsu.py"
    skrip.write_text(textwrap.dedent('''
        import json, os, sys, time
        # hanya argumen yang BENAR-BENAR berkas: nilai --model/--prompt/--language
        # bukan berkas, dan versi pertama worker palsu ini menganggapnya begitu
        berkas = [a for a in sys.argv[1:] if os.path.isfile(a)]
        def kirim(o):
            sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
        ''') + textwrap.dedent(perilaku), encoding="utf-8")
    return str(skrip)


@pytest.fixture
def lokal(monkeypatch, tmp_path):
    """Arahkan jalur lokal ke worker palsu dan buat bahan audio tiruan."""
    monkeypatch.setattr(t, "TRANSCRIBE_PROVIDER", "local")
    monkeypatch.setattr(t, "LOCAL_PYTHON", sys.executable)
    monkeypatch.setattr(t, "has_audio", lambda p: True)
    monkeypatch.setattr(t, "media_duration", lambda p: 5.0)
    monkeypatch.setattr(t, "OPENAI_API_KEY", "kunci-palsu")
    monkeypatch.setattr(t, "TRANSCRIBE_ENABLED", True)

    def extract(src, dst):
        open(dst, "w").write("x")
        return dst

    monkeypatch.setattr(t, "extract_audio", extract)

    def pasang(perilaku):
        monkeypatch.setattr(t, "LOCAL_WORKER", _worker_palsu(tmp_path, perilaku))

    paths = [str(tmp_path / f"klip{i}.mp4") for i in range(3)]
    return pasang, paths


PERILAKU_BAIK = '''
    kirim({"tipe": "model", "model": "small", "detik_muat_model": 0.1})
    for p in berkas:
        kirim({"tipe": "berkas", "path": p, "data": {
            "text": "ucapan uji", "language": "id", "detik_proses": 0.2,
            "segments": [{"start": 0.2, "end": 1.5, "text": "ucapan uji", "avg_logprob": -0.3}],
            "words": [{"start": 0.2, "end": 0.8, "word": "ucapan"}, {"start": 0.9, "end": 1.5, "word": "uji"}]}})
'''


# ---------- jalur lokal ----------

def test_lokal_menghasilkan_bentuk_yang_sama_dengan_api(lokal):
    pasang, paths = lokal
    pasang(PERILAKU_BAIK)
    hasil, gagal = t.transcribe_assets_report(paths)

    assert sorted(hasil) == ["klip0.mp4", "klip1.mp4", "klip2.mp4"] and gagal == {}
    h = hasil["klip0.mp4"]
    assert h["text"] == "ucapan uji" and h["language"] == "id"
    assert h["words"][0]["word"] == "ucapan"
    assert h["segments"][0]["avg_logprob"] == -0.3, "metrik keyakinan ikut terbawa"
    assert h["duration"] == 5.0


def test_lokal_mencatat_biaya_nol_yang_sebenarnya(lokal, monkeypatch):
    """Nol karena memang tidak ada tagihan -- BUKAN None ('tidak diketahui')."""
    pasang, paths = lokal
    pasang(PERILAKU_BAIK)
    tercatat = []
    import run_log
    monkeypatch.setattr(run_log, "log_event", lambda ev, rid, **kw: tercatat.append((ev, kw)))

    t.transcribe_assets_report(paths[:1])

    ev, kw = tercatat[0]
    assert ev == "transcribe_call" and kw["model"] == "local/small"
    assert kw["cost_usd"] == 0.0 and kw["cost_usd"] is not None


def test_hasil_parsial_dipakai_saat_anggaran_habis(lokal, monkeypatch):
    """Worker mengalirkan hasil per klip, jadi klip yang sudah selesai tidak
    ikut hilang ketika waktunya habis di tengah."""
    pasang, paths = lokal
    pasang('''
    kirim({"tipe": "model", "model": "small", "detik_muat_model": 0.1})
    kirim({"tipe": "berkas", "path": berkas[0], "data": {
        "text": "sempat selesai", "segments": [], "words": [], "language": "id"}})
    time.sleep(30)
    ''')
    monkeypatch.setattr(t, "LOCAL_BUDGET", 2)

    hasil, gagal = t.transcribe_assets_report(paths)

    assert list(hasil) == ["klip0.mp4"], "yang sempat selesai harus tetap dipakai"
    assert gagal == {"klip1.mp4": "tidak_selesai", "klip2.mp4": "tidak_selesai"}


def test_timeout_membunuh_worker_bukan_membiarkannya_yatim(lokal, monkeypatch, tmp_path):
    pasang, paths = lokal
    tanda = tmp_path / "pid.txt"
    pasang(f'''
    import os
    open({str(tanda)!r}, "w").write(str(os.getpid()))
    time.sleep(60)
    ''')
    monkeypatch.setattr(t, "LOCAL_BUDGET", 2)
    t.transcribe_assets_report(paths[:1])

    pid = int(tanda.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_error_satu_klip_tidak_menjatuhkan_yang_lain(lokal):
    pasang, paths = lokal
    pasang('''
    kirim({"tipe": "model", "model": "small", "detik_muat_model": 0.1})
    kirim({"tipe": "berkas", "path": berkas[0], "data": {"error": "ValueError: audio rusak"}})
    kirim({"tipe": "berkas", "path": berkas[1], "data": {"text": "ok", "segments": [], "words": []}})
    ''')
    hasil, gagal = t.transcribe_assets_report(paths[:2])
    assert list(hasil) == ["klip1.mp4"] and gagal == {"klip0.mp4": "whisper_lokal_gagal"}


def test_klip_tanpa_ucapan_dibedakan_dari_gagal(lokal):
    pasang, paths = lokal
    pasang('''
    kirim({"tipe": "berkas", "path": berkas[0], "data": {"text": "", "segments": [], "words": []}})
    ''')
    _, gagal = t.transcribe_assets_report(paths[:1])
    assert gagal == {"klip0.mp4": "tanpa_ucapan"}


def test_worker_fatal_pada_mode_local_dilaporkan_bukan_diam(lokal):
    pasang, paths = lokal
    pasang('''
    kirim({"tipe": "fatal", "pesan": "model gagal dimuat"})
    sys.exit(3)
    ''')
    hasil, gagal = t.transcribe_assets_report(paths[:2])
    assert hasil == {}
    assert set(gagal.values()) == {"whisper_lokal_gagal"}


def test_mode_auto_jatuh_ke_api_kalau_lokal_gagal_total(lokal, monkeypatch):
    pasang, paths = lokal
    pasang('''
    kirim({"tipe": "fatal", "pesan": "faster-whisper tidak bisa diimpor"})
    sys.exit(2)
    ''')
    monkeypatch.setattr(t, "TRANSCRIBE_PROVIDER", "auto")
    dipanggil = []

    def api(path, *, durasi=0.0, vocab_prompt=None):
        dipanggil.append(path)
        return {"text": "dari api", "segments": [], "words": []}

    monkeypatch.setattr(t, "transcribe_file_detailed", api)
    hasil, gagal = t.transcribe_assets_report(paths[:2])

    assert len(dipanggil) == 2 and sorted(hasil) == ["klip0.mp4", "klip1.mp4"]


def test_mode_local_tidak_diam_diam_memakai_api(lokal, monkeypatch):
    """TRANSCRIBE_PROVIDER=local berarti local: tanpa fallback ke API."""
    pasang, paths = lokal
    pasang('''
    kirim({"tipe": "fatal", "pesan": "x"})
    sys.exit(2)
    ''')
    monkeypatch.setattr(t, "transcribe_file_detailed",
                        lambda *a, **k: pytest.fail("API dipanggil padahal mode local"))
    t.transcribe_assets_report(paths[:1])


def test_venv_tidak_ada_pada_mode_auto_memakai_api(monkeypatch):
    monkeypatch.setattr(t, "TRANSCRIBE_PROVIDER", "auto")
    monkeypatch.setattr(t, "LOCAL_PYTHON", "/tidak/ada/python")
    assert t.pilih_penyedia() == "api"


def test_mode_api_eksplisit_menang_atas_venv_yang_ada(monkeypatch):
    monkeypatch.setattr(t, "TRANSCRIBE_PROVIDER", "api")
    monkeypatch.setattr(t, "LOCAL_PYTHON", sys.executable)
    assert t.pilih_penyedia() == "api"


def test_prompt_dan_bahasa_diteruskan_ke_worker(lokal, monkeypatch, tmp_path):
    pasang, paths = lokal
    rekam = tmp_path / "argv.json"
    pasang(f'''
    open({str(rekam)!r}, "w").write(json.dumps(sys.argv[1:]))
    kirim({{"tipe": "berkas", "path": berkas[-1], "data": {{"text": "x", "segments": [], "words": []}}}})
    ''')
    monkeypatch.setattr(t, "LOCAL_LANGUAGE", "id")
    t.transcribe_assets_report(paths[:1], konteks="properti")
    argv = json.loads(rekam.read_text())
    assert "--language" in argv and argv[argv.index("--language") + 1] == "id"
    assert "properti" in argv[argv.index("--prompt") + 1]


def test_bahasa_kosong_berarti_deteksi_otomatis(lokal, monkeypatch, tmp_path):
    pasang, paths = lokal
    rekam = tmp_path / "argv.json"
    pasang(f'''
    open({str(rekam)!r}, "w").write(json.dumps(sys.argv[1:]))
    ''')
    monkeypatch.setattr(t, "LOCAL_LANGUAGE", None)
    t.transcribe_assets_report(paths[:1])
    assert "--language" not in json.loads(rekam.read_text())


# ---------- kegagalan BUKAN "tidak ada ucapan" ----------

def test_transkripsi_gagal_menghentikan_run_bukan_ganti_ke_suara_ai():
    """Persis insiden 19 Sep: kuota habis, 0 transkrip, suara asli diminta."""
    with pytest.raises(am.TranscriptionUnavailable) as e:
        am.resolve_audio_mode(am.MODE_ORIGINAL, {}, eksplisit=True,
                              gagal={"video.mp4": "kuota_habis"})
    pesan = str(e.value)
    assert "saldo/kuota API habis" in pesan, "penyebab harus terbaca user"
    assert "TIDAK dibuat" in pesan and "suara AI" in pesan


@pytest.mark.parametrize("kode", ["kuota_habis", "timeout", "layanan_tidak_tersedia",
                                  "akses_ditolak", "error_api", "gagal_ekstrak",
                                  "tidak_selesai", "whisper_lokal_gagal"])
def test_setiap_kegagalan_selain_tanpa_ucapan_menghentikan_run(kode):
    with pytest.raises(am.TranscriptionUnavailable):
        am.resolve_audio_mode(am.MODE_ORIGINAL, {}, gagal={"v.mp4": kode})


@pytest.mark.parametrize("kode", ["tanpa_ucapan", "tanpa_audio"])
def test_tanpa_ucapan_yang_pasti_tetap_jatuh_ke_suara_ai(kode):
    """Foto / video bisu: Whisper BERJALAN dan memang tidak ada ucapan."""
    mode, alasan = am.resolve_audio_mode(am.MODE_ORIGINAL, {}, gagal={"v.mp4": kode})
    assert mode == am.MODE_AI and "tidak ada ucapan" in alasan


def test_gagal_sebagian_tetap_lanjut_dengan_suara_asli():
    """Sebagian berhasil = ucapan sudah terbukti ada; yang gagal dilaporkan di
    caption, bukan menggagalkan seluruh run."""
    mode, _ = am.resolve_audio_mode(
        am.MODE_ORIGINAL, {"a.mp4": "ada teks"}, gagal={"b.mp4": "kuota_habis"})
    assert mode == am.MODE_ORIGINAL


def test_suara_ai_yang_diminta_eksplisit_tidak_terpengaruh_kegagalan():
    mode, _ = am.resolve_audio_mode(am.MODE_AI, {}, gagal={"v.mp4": "kuota_habis"})
    assert mode == am.MODE_AI


def test_tanpa_data_gagal_perilaku_lama_utuh():
    """Pemanggil lama (tanpa argumen gagal) tidak berubah."""
    mode, _ = am.resolve_audio_mode(am.MODE_ORIGINAL, {})
    assert mode == am.MODE_AI
