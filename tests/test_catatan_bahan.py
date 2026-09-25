"""Catatan untuk user di hasil hermes_render (dipindah dari caption jalur OpenClaw yang
dihapus): subtitle yang hilang BESERTA penyebabnya, bahan tanpa ucapan, ringkasan seleksi."""

import hermes_render as hr


def cakupan(total, tanpa, alasan=None):
    return {"ditranskrip": total - len(tanpa), "total_bahan": total, "tanpa_subtitle": tanpa,
            "alasan": alasan or {}}


def test_menyebut_jumlah_dan_penyebab_terbanyak_dulu():
    b = {"audio_mode": "original", "transcript_coverage": cakupan(10, list("abcd"), {
        "a": "kuota_habis", "b": "kuota_habis", "c": "kuota_habis", "d": "tanpa_ucapan"})}
    (teks,) = hr.catatan_bahan(b)
    assert "4 dari 10" in teks and "tanpa subtitle" in teks
    assert "saldo/kuota API habis (3)" in teks and "tidak ada ucapan terdeteksi (1)" in teks
    assert teks.index("saldo/kuota") < teks.index("tidak ada ucapan")


def test_tidak_berisik_kalau_semua_bersubtitle():
    assert hr.catatan_bahan({"audio_mode": "original", "transcript_coverage": cakupan(3, [])}) == []


def test_tanpa_ucapan_di_mode_asli_mengingatkan_teks_dari_tampilan():
    b = {"audio_mode": "original",
         "transcript_coverage": cakupan(3, list("abc"), {k: "tanpa_ucapan" for k in "abc"})}
    assert any("TAMPILAN saja" in c for c in hr.catatan_bahan(b))


def test_mode_voiceover_tidak_melaporkan_subtitle_ucapan():
    """Narasi AI punya teks sendiri; bahan tanpa ucapan di sini bukan masalah."""
    b = {"audio_mode": "ai", "transcript_coverage": cakupan(3, list("abc"), {k: "tanpa_ucapan" for k in "abc"})}
    assert hr.catatan_bahan(b) == []


def test_ringkasan_seleksi_ikut():
    b = {"audio_mode": "original", "edit_summary": "dipilih 2 dari 3 potongan",
         "transcript_coverage": cakupan(3, [])}
    assert hr.catatan_bahan(b) == ["dipilih 2 dari 3 potongan"]
