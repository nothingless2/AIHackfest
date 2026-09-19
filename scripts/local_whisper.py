"""Worker transkripsi Whisper LOKAL (faster-whisper). Dijalankan oleh
scripts/transcribe.py memakai python dari .venv-whisper, BUKAN python sistem.

Kenapa proses terpisah dengan venv sendiri:
- faster-whisper menarik ctranslate2/onnxruntime/numpy; memasangnya ke Python
  sistem berisiko mengubah versi numpy yang dipakai moviepy dan openai.
- Model dimuat SEKALI untuk semua berkas dalam satu panggilan, alih-alih per klip.

Kenapa ada sama sekali: transkripsi lewat API berbayar gagal ketika saldo habis
(403 insufficient_quota), dan tanpa transkrip agent TIDAK BISA MENDENGAR video --
model chat hanya melihat frame. Whisper lokal menghapus ketergantungan itu.

Pemakaian:
    .venv-whisper/bin/python scripts/local_whisper.py --model small \\
        [--language id] [--prompt "kosakata"] berkas1 berkas2 ...
Keluaran (stdout): SATU OBJEK JSON PER BARIS, dikirim begitu siap, supaya pemanggil
yang kehabisan waktu tetap bisa memakai klip yang sudah selesai:
    {"tipe": "model", "model": ..., "detik_muat_model": ...}
    {"tipe": "berkas", "path": ..., "data": {"text","segments","words",
                                              "language","detik_proses"} | {"error": ...}}
    {"tipe": "fatal", "pesan": ...}        (lalu proses berhenti)
Bentuk segments/words sama persis dengan yang dihasilkan jalur API.
"""

import argparse
import json
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="small")
    ap.add_argument("--language", default=None)
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--threads", type=int, default=0, help="0 = semua core")
    ap.add_argument("berkas", nargs="+")
    a = ap.parse_args()

    # Diimpor DI SINI supaya kesalahan impor (paket tidak terpasang) tetap
    # menghasilkan JSON yang bisa dibaca pemanggil, bukan traceback mentah.
    def kirim(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    try:
        from faster_whisper import WhisperModel
    except Exception as e:  # noqa: BLE001
        kirim({"tipe": "fatal", "pesan": f"faster-whisper tidak bisa diimpor: {e}"})
        return 2

    t0 = time.time()
    try:
        model = WhisperModel(a.model, device="cpu", compute_type="int8",
                             cpu_threads=a.threads)
    except Exception as e:  # noqa: BLE001
        kirim({"tipe": "fatal",
               "pesan": f"model {a.model!r} gagal dimuat: {type(e).__name__}: {e}"})
        return 3
    kirim({"tipe": "model", "model": a.model, "detik_muat_model": round(time.time() - t0, 2)})

    for path in a.berkas:
        mulai = time.time()
        try:
            segs, info = model.transcribe(
                path,
                language=a.language or None,
                initial_prompt=a.prompt or None,
                word_timestamps=True,
                # VAD membuang hening sebelum Whisper melihatnya: mengurangi
                # halusinasi teks pada jeda, dan membantu timestamp awal kata.
                vad_filter=True,
                beam_size=5,
                condition_on_previous_text=False,
            )
            segmen, kata, teks = [], [], []
            for s in segs:
                isi = (s.text or "").strip()
                if not isi:
                    continue
                teks.append(isi)
                segmen.append({
                    "start": float(s.start), "end": float(s.end), "text": isi,
                    "avg_logprob": float(s.avg_logprob),
                    "no_speech_prob": float(s.no_speech_prob),
                    "compression_ratio": float(s.compression_ratio),
                })
                for w in (s.words or []):
                    kt = (w.word or "").strip()
                    if kt:
                        kata.append({"start": float(w.start), "end": float(w.end), "word": kt})
            kirim({"tipe": "berkas", "path": path, "data": {
                "text": " ".join(teks).strip(), "segments": segmen, "words": kata,
                "language": info.language, "detik_proses": round(time.time() - mulai, 2),
            }})
        except Exception as e:  # noqa: BLE001
            kirim({"tipe": "berkas", "path": path,
                   "data": {"error": f"{type(e).__name__}: {e}"}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
