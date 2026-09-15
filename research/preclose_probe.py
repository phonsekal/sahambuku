#!/usr/bin/env python3
"""Ambil snapshot SAHAM yang naik >= 8% PRA-TUTUP (sebelum 15:50 WIB), lalu simpan.

Tujuannya satu: membuktikan bahwa pindai pra-tutup benar-benar memberi harga masuk
yang bisa dibayar, dan mengukur berapa besar selisihnya dari harga tutup resmi.

Kenapa probe ini perlu ada di samping uji riset: seluruh tabel audit mengukur
close[t] -> close[t+h] dan menyimpulkan alpha momentum hilang bila masuk di harga
pembukaan sesi berikutnya. Pindai pra-tutup adalah satu-satunya cara membayar harga
tutup hari sinyal, TETAPI harga pukul 15:40 bukan harga final: sisa sesi (termasuk
lelang penutupan) masih bisa menggesernya. Angka itu tidak bisa diambil dari data
harian, jadi diambil langsung dari bursa saat sesi berjalan.

Jalankan pada 15:35-15:49 WIB:
  .venv/bin/python research/preclose_probe.py --limit 250
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "api"))

import panel as P  # noqa: E402

WIB = timezone(timedelta(hours=7))
OUT_DIR = os.path.join(HERE, ".cache")


def load_api():
    spec = importlib.util.spec_from_file_location("idx_api", os.path.join(ROOT, "api", "index.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=250)
    ap.add_argument("--mom", type=float, default=8.0)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    A = load_api()
    now = datetime.now(WIB)
    print(f"jam WIB: {now:%Y-%m-%d %H:%M:%S} (sesi reguler sampai 15:49:59)")

    tickers = A.load_idx_tickers("all")
    if args.limit and len(tickers) > args.limit:
        tickers = A.spread_pick(tickers, args.limit)
    print(f"memeriksa {len(tickers)} emiten harga live (curl_cffi, 1 permintaan/emiten)")

    def probe(tk: str):
        q = A.fetch_live_quote(tk)
        if not q or not q.get("last_price"):
            return None
        px, prev = float(q["last_price"]), q.get("previous_close")
        if not prev:
            return None
        return {
            "ticker": tk,
            "trigger_price": px,
            "prev_close": float(prev),
            "day_ret_pct": round((px / float(prev) - 1) * 100, 3),
            "market_state": q.get("market_state"),
            "as_of": q.get("as_of"),
        }

    t0 = time.time()
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(probe, tickers):
            if r:
                rows.append(r)
    print(f"selesai {len(rows)}/{len(tickers)} dalam {time.time() - t0:.0f} dtk")

    cand = sorted([r for r in rows if r["day_ret_pct"] >= args.mom],
                  key=lambda r: -r["day_ret_pct"])
    print(f"kandidat naik >= {args.mom}%: {len(cand)}")
    for r in cand[:20]:
        print(f"  {r['ticker']:<10} {r['trigger_price']:>10,.0f}  "
              f"{r['day_ret_pct']:>+7.2f}%  (prev {r['prev_close']:,.0f})")

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    tag = f"_{args.tag}" if args.tag else ""
    path = os.path.join(OUT_DIR, f"preclose_probe{tag}_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "captured_at_wib": now.isoformat(timespec="seconds"),
            "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mom_min_pct": args.mom,
            "universe_size": len(tickers),
            "checked": len(rows),
            "source": "yahoo-live (curl_cffi, chart 5m)",
            "candidates": cand,
            "all_rows": rows,
        }, f, ensure_ascii=False)
    print(f"disimpan: {os.path.relpath(path, ROOT)}")
    print("stempel waktu bukti: " + now.strftime("%Y-%m-%d %H:%M WIB"))


if __name__ == "__main__":
    main()
