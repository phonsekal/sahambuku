#!/usr/bin/env python3
"""Ekspor snapshot harga ringkas ke `api/` supaya bisa dibaca endpoint produksi.

Kenapa ada
----------
Endpoint Vercel TIDAK boleh menarik harga saat request (lihat README folder ini).
Jadi pipeline CI menarik data, lalu berkas ini menulis bagian yang dibutuhkan
runtime ke dalam `api/` — pola yang sama dengan `api/fundamentals.json`.

Yang ditulis: `api/market_<pasar>.csv` berisi `bars` bar terakhir per ticker
(default 220, cukup untuk SMA200 dan high-20 yang dipakai aturan crypto; dijaga
kecil supaya snapshot mingguan tidak membengkakkan repo).

CATATAN UKURAN: crypto top-100 x 400 bar ~ 40 ribu baris (~2 MB) — wajar untuk
repo. Saham AS (~5.900 ticker) akan menjadi ~2,4 juta baris (~90 MB) dan itu
TIDAK boleh masuk repo, jadi pasar AS sengaja tidak diekspor sampai ada aturan
yang lolos pengukuran (dan bahkan nanti pun ia perlu mekanisme lain, mis. hanya
mengekspor sinyal hari itu, bukan panel penuh).

Jalankan:
    .venv/bin/python research/markets/export.py --market crypto
    .venv/bin/python research/markets/export.py --market crypto --bars 260
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import pickle
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
API_DIR = os.path.join(os.path.dirname(RESEARCH), "api")
CACHE_DIR = os.path.join(RESEARCH, ".cache", "markets")

# Pasar yang boleh diekspor ke repo. AS menunggu aturan yang lolos (ukurannya
# puluhan MB) — kalau dipaksa, ukurannya membengkakkan repo dan waktu deploy.
BIG_MARKETS = {"us"}


def export_market(market: str, bars: int = 260) -> str:
    if market in BIG_MARKETS:
        raise SystemExit(
            f"'{market}' terlalu besar untuk diekspor penuh ({bars} bar x ribuan ticker). "
            "Ekspor hanya sinyal hari itu, bukan panel penuh."
        )
    path = os.path.join(CACHE_DIR, f"{market}_panel.pkl")
    if not os.path.exists(path):
        raise SystemExit(f"Panel {path} belum ada — jalankan pull.py lebih dulu.")
    with open(path, "rb") as fh:
        panel = pickle.load(fh)

    out = os.path.join(API_DIR, f"market_{market}.csv")
    n = 0
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "date", "open", "high", "low", "close", "volume"])
        for code, g in panel.groupby("code"):
            g = g.sort_values("date").tail(bars)
            for r in g.itertuples(index=False):
                w.writerow([code, str(getattr(r, "date").date()),
                            float(getattr(r, "open")), float(getattr(r, "high")),
                            float(getattr(r, "low")), float(getattr(r, "close")),
                            float(getattr(r, "volume"))])
                n += 1
    size_mb = os.path.getsize(out) / 1e6
    # Meta CAKUPAN: berapa ticker yang benar-benar dapat vs ukuran universe, dan
    # berapa bar yang disimpan. Ditulis terpisah dari CSV supaya endpoint/dashboard
    # bisa menyebut "74 dari 100" apa adanya, bukan menyiratkan cakupan penuh.
    cov: dict = {}
    cov_path = os.path.join(CACHE_DIR, f"{market}_universe.json")
    if os.path.exists(cov_path):
        try:
            with open(cov_path, "r", encoding="utf-8") as fh:
                cov = json.load(fh) or {}
        except Exception:
            cov = {}
    meta = {
        "market": market,
        "bars": int(bars),
        "as_of": str(panel["date"].max().date()),
        "scanned": int(panel["code"].nunique()),
        "universe": int(cov.get("universe") or panel["code"].nunique()),
        "rows": int(n),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    meta_path = os.path.join(API_DIR, f"market_{market}_meta.json")
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"[{market}] {n:,} baris · {panel['code'].nunique()} dari {meta['universe']} "
          f"ticker · {panel['date'].max().date()} · {size_mb:.2f} MB -> {out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Ekspor snapshot pasar ke api/")
    ap.add_argument("--market", choices=["crypto", "us"], required=True)
    ap.add_argument("--bars", type=int, default=260)
    args = ap.parse_args()
    export_market(args.market, args.bars)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
