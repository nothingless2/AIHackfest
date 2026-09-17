"""Laporan biaya dari run_log.jsonl.

Semua angka biaya di sini ESTIMASI: jumlah tokennya nyata (dari API), tapi
konversi ke USD memakai harga di config/pricing.json yang tidak memperbarui
dirinya sendiri. Panggilan dengan model yang tidak ada di tabel harga dihitung
terpisah sebagai "tanpa harga" -- TIDAK ditebak dan tidak diam-diam dianggap nol.

Pemakaian:
    python3 scripts/cost_report.py            # 30 hari terakhir
    python3 scripts/cost_report.py --days 7
    python3 scripts/cost_report.py --by-chat  # rincikan per chat
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import RUN_LOG_PATH  # noqa: E402
from cost_estimate import PRICING_PATH, load_pricing  # noqa: E402


def baca_event(path, sejak):
    """Baca JSONL. Baris rusak dilewati -- log yang sebagian korup masih berguna."""
    if not os.path.exists(path):
        return []
    rows, rusak = [], 0
    with open(path, encoding="utf-8") as f:
        for baris in f:
            baris = baris.strip()
            if not baris:
                continue
            try:
                e = json.loads(baris)
            except json.JSONDecodeError:
                rusak += 1
                continue
            if e.get("event") not in ("llm_call", "tts_call", "run_started"):
                continue
            ts = e.get("ts") or ""
            if sejak and ts and ts[:10] < sejak:
                continue
            rows.append(e)
    if rusak:
        print(f"[warn] {rusak} baris log tidak terbaca dan dilewati.\n")
    return rows


def kumpulkan(events, per_chat):
    data = defaultdict(lambda: {
        "runs": 0, "llm": 0, "tts": 0, "token_masuk": 0, "token_keluar": 0,
        "chars": 0, "usd": 0.0, "tanpa_harga": 0,
    })
    for e in events:
        tanggal = (e.get("ts") or "")[:10] or "?"
        kunci = (tanggal, str(e.get("chat_id") or "-")) if per_chat else (tanggal,)
        d = data[kunci]
        ev = e.get("event")
        if ev == "run_started":
            d["runs"] += 1
            continue
        biaya = e.get("cost_usd")
        if biaya is None:
            d["tanpa_harga"] += 1
        else:
            d["usd"] += float(biaya)
        if ev == "llm_call":
            d["llm"] += 1
            d["token_masuk"] += int(e.get("prompt_tokens") or 0)
            d["token_keluar"] += int(e.get("completion_tokens") or 0)
        else:
            d["tts"] += 1
            d["chars"] += int(e.get("chars") or 0)
    return data


def main():
    ap = argparse.ArgumentParser(description="Laporan ESTIMASI biaya pipeline.")
    ap.add_argument("--days", type=int, default=30, help="rentang hari (default 30)")
    ap.add_argument("--by-chat", action="store_true", help="rincikan per chat_id")
    args = ap.parse_args()

    sejak = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")
    events = baca_event(RUN_LOG_PATH, sejak)

    harga = load_pricing()
    print("=" * 78)
    print(f"LAPORAN BIAYA — ESTIMASI, {args.days} hari terakhir (sejak {sejak})")
    print(f"Harga: {PRICING_PATH} (updated_at: {harga.get('updated_at', 'tidak diketahui')})")
    print("Token = data nyata dari API. Konversi ke USD = estimasi dari tabel harga.")
    print("=" * 78)

    if not events:
        print("\nBelum ada pemakaian tercatat pada rentang ini.")
        return 0

    data = kumpulkan(events, args.by_chat)
    kepala = ("TANGGAL", "CHAT", "RUN", "LLM", "TOKEN IN", "TOKEN OUT", "TTS CHAR", "EST. USD")
    if args.by_chat:
        print("\n{:<11} {:<13} {:>4} {:>4} {:>9} {:>10} {:>9} {:>10}".format(*kepala))
    else:
        print("\n{:<11} {:>4} {:>4} {:>9} {:>10} {:>9} {:>10}".format(
            kepala[0], *kepala[2:]))

    total_usd, total_tanpa_harga = 0.0, 0
    for kunci in sorted(data):
        d = data[kunci]
        total_usd += d["usd"]
        total_tanpa_harga += d["tanpa_harga"]
        if args.by_chat:
            print("{:<11} {:<13} {:>4} {:>4} {:>9} {:>10} {:>9} {:>10.4f}".format(
                kunci[0], kunci[1][:13], d["runs"], d["llm"],
                d["token_masuk"], d["token_keluar"], d["chars"], d["usd"]))
        else:
            print("{:<11} {:>4} {:>4} {:>9} {:>10} {:>9} {:>10.4f}".format(
                kunci[0], d["runs"], d["llm"],
                d["token_masuk"], d["token_keluar"], d["chars"], d["usd"]))

    print("-" * 78)
    print(f"TOTAL ESTIMASI: USD {total_usd:.4f}")
    total_run = sum(d["runs"] for d in data.values())
    if total_run:
        print(f"Rata-rata per run: USD {total_usd / total_run:.4f} ({total_run} run)")
    if total_tanpa_harga:
        print(f"\n[!] {total_tanpa_harga} panggilan TIDAK punya harga di {os.path.basename(PRICING_PATH)}")
        print("    dan TIDAK ikut dihitung. Total di atas lebih rendah dari biaya sebenarnya.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
