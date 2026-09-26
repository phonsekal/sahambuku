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
    .venv/bin/python research/markets/export.py --market us --state   # keadaan turunan AS
    .venv/bin/python research/markets/export.py --market etf --state  # keadaan turunan ETF

Saham AS & ETF: `--state` menulis KEADAAN TURUNAN per ticker (bukan panel penuh) ke
`api/market_<pasar>_state.csv` — close, SMA20/50/200, high 52m, ret5, nilai transaksi 20
hari, dan tiga tanda aturan yang lolos bar. Ukurannya ratusan KB (satu baris per
emiten), bukan puluhan MB, sehingga aturan AS bisa disajikan tanpa membengkakkan
repo. Panel penuh AS tetap TIDAK diekspor.
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
sys.path.insert(0, HERE)          # supaya `import study` memakai definisi indikator yang SAMA
RESEARCH = os.path.dirname(HERE)
API_DIR = os.path.join(os.path.dirname(RESEARCH), "api")
CACHE_DIR = os.path.join(RESEARCH, ".cache", "markets")

# Pasar yang TIDAK boleh diekspor sebagai PANEL PENUH. AS (~5.900 ticker) akan jadi
# ~90 MB; keadaan turunannya diekspor lewat `--state` (satu baris per emiten).
BIG_MARKETS = {"us", "etf"}

# Tanda aturan AS yang lolos bar proyek. Kunci produksi dipetakan ke NAMA aturan di
# laporan ukur supaya angka yang ditampilkan bisa ditelusuri ke barisnya.
US_STATE_RULES = [
    ("pullback_uptrend", "pullback di uptrend"),
    ("above_sma200", "di atas SMA200"),
    ("near_high52", "dekat puncak 52m"),
]
US_STATE_COLS = ["code", "date", "close", "sma20", "sma50", "sma200", "high52",
                 "dist_high52_pct", "ret5_pct", "ret1_pct", "v20_usd",
                 "pullback_uptrend", "above_sma200", "near_high52"]


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


def export_state(market: str, bars: int = 252) -> str:
    """Tulis KEADAAN TURUNAN per ticker ke api/market_<pasar>_state.csv (1 baris/ticker).

    KENAPA INI ADA: aturan AS yang lolos bar bersifat FILTER KEADAAN (di atas SMA200,
    dekat puncak 52m, pullback di uptrend), sehingga untuk menyajikannya cukup nilai
    terakhir per ticker — bukan seluruh 6,3 juta bar. Satu baris x ~5.900 emiten =
    ratusan KB, dan itu boleh masuk repo.

    Dipakai untuk DUA pasar: `us` (saham biasa) dan `etf`. Kolom & definisi aturannya
    SAMA (diambil dari `study.py`, bukan ditulis ulang), tetapi panelnya berbeda —
    jadi aturan ETF tidak diam-diam mewarisi angka saham biasa.

    Definisi indikator diambil dari `study.py` (fungsi yang SAMA dengan pengukuran),
    bukan ditulis ulang, supaya keadaan yang disajikan tidak bisa menyimpang dari yang
    diukur.
    """
    import pandas as pd
    import study as ST

    path = os.path.join(CACHE_DIR, f"{market}_panel.pkl")
    if not os.path.exists(path):
        raise SystemExit(f"Panel {path} belum ada — jalankan pull.py lebih dulu.")
    with open(path, "rb") as fh:
        panel = pickle.load(fh)

    out = os.path.join(API_DIR, f"market_{market}_state.csv")
    rows = []
    for code, g in panel.sort_values(["code", "date"]).groupby("code", sort=False):
        if len(g) < 60:
            continue
        df = g.set_index("date")
        close = df["close"].astype(float)
        vol = df["volume"].astype(float) if "volume" in df else pd.Series(0.0, index=df.index)
        sig = ST.signals_for(df)
        s20, s50, s200 = ST.sma(close, 20), ST.sma(close, 50), ST.sma(close, 200)
        hi252 = close.rolling(252, min_periods=120).max()
        v20 = (close * vol).rolling(20, min_periods=20).mean()
        c = float(close.iloc[-1])
        if not c > 0:
            continue
        h52 = hi252.iloc[-1]
        ret5 = (c / float(close.iloc[-6]) - 1.0) * 100.0 if len(close) > 5 and close.iloc[-6] else None
        ret1 = (c / float(close.iloc[-2]) - 1.0) * 100.0 if len(close) > 1 and close.iloc[-2] else None

        def _f(v, nd):
            try:
                f = float(v)
            except (TypeError, ValueError):
                return ""
            return "" if f != f else round(f, nd)      # f != f menandai NaN

        row = {
            "code": str(code), "date": str(df.index[-1].date()),
            "close": _f(c, 4),
            "sma20": _f(s20.iloc[-1], 4), "sma50": _f(s50.iloc[-1], 4),
            "sma200": _f(s200.iloc[-1], 4),
            "high52": _f(h52, 4),
            "dist_high52_pct": _f((c / h52 - 1.0) * 100.0, 2) if pd.notna(h52) else "",
            "ret5_pct": _f(ret5, 2), "ret1_pct": _f(ret1, 2),
            "v20_usd": _f(v20.iloc[-1], 0),
        }
        for key, rule in US_STATE_RULES:
            row[key] = int(bool(sig[rule].iloc[-1]))
        rows.append(row)
    if not rows:
        raise SystemExit("Tidak ada emiten dengan cukup bar untuk diekspor sebagai keadaan.")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=US_STATE_COLS)
        w.writeheader()
        w.writerows(rows)
    size_kb = os.path.getsize(out) / 1024

    cov = {}
    cov_path = os.path.join(CACHE_DIR, f"{market}_universe.json")
    if os.path.exists(cov_path):
        try:
            with open(cov_path, "r", encoding="utf-8") as fh:
                cov = json.load(fh) or {}
        except Exception:
            cov = {}
    meta = {
        "market": market, "kind": "derived_state", "bars": int(bars),
        "as_of": max(r["date"] for r in rows),
        "scanned": len(rows),
        "universe": int(cov.get("universe") or len(rows)),
        "rules": {key: rule for key, rule in US_STATE_RULES},
        "note": ("Satu baris per emiten = nilai TERAKHIR (bukan panel penuh). Panel penuh "
                 "tidak diekspor karena puluhan MB."),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(os.path.join(API_DIR, f"market_{market}_state_meta.json"), "w",
              encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"[{market}] keadaan turunan: {len(rows)} emiten · {meta['as_of']} · "
          f"{size_kb:.0f} KB -> {out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Ekspor snapshot pasar ke api/")
    ap.add_argument("--market", choices=["crypto", "us", "etf"], required=True)
    ap.add_argument("--bars", type=int, default=260)
    ap.add_argument("--state", action="store_true",
                    help="us/etf: ekspor KEADAAN TURUNAN per ticker (bukan panel penuh)")
    args = ap.parse_args()
    if args.state:
        if args.market not in ("us", "etf"):
            raise SystemExit("--state hanya untuk pasar 'us' atau 'etf' (keadaan turunan).")
        export_state(args.market, args.bars)
    else:
        export_market(args.market, args.bars)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
