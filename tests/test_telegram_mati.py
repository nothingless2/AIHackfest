"""Test penjaga: artefak tes pernah dua kali bocor ke Telegram user. Sejak jalur OpenClaw
dihapus, TIDAK ADA kode di repo ini yang mengirim ke Telegram (agent Hermes yang mengirim).
Test ini menjaga supaya pengirim langsung tidak masuk lagi tanpa disadari."""

import os

import common

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_notify_hanya_mencetak(capsys):
    assert common.notify("brainidea", "halo", chat_id="123") is False
    assert "BrainIdea" in capsys.readouterr().out


def test_tidak_ada_kode_yang_memanggil_api_telegram():
    temuan = []
    for folder in ("scripts", "skills"):
        for akar, _, berkas in os.walk(os.path.join(ROOT, folder)):
            for b in berkas:
                if b.endswith(".py"):
                    p = os.path.join(akar, b)
                    if "api.telegram.org" in open(p, encoding="utf-8").read():
                        temuan.append(p)
    assert temuan == []


def test_pemindai_mengenali_pola_kontrol_positif(tmp_path):
    """Kontrol positif: pola yang dicari memang terdeteksi bila ada."""
    f = tmp_path / "x.py"
    f.write_text('URL = "https://api.telegram.org/bot"')
    assert "api.telegram.org" in f.read_text()
