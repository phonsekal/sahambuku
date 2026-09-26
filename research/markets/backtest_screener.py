#!/usr/bin/env python3
"""Backtest AKURASI screener: apakah kandidat yang disajikan benar-benar punya daya prediksi.

Kenapa ada
----------
Screener hanya menyajikan aturan yang sudah LOLOS BAR proyek (`lolos_bar`). Tetapi
"lolos bar" adalah KLAIM: ketika aturan itu menyala pada tanggal t, harga cenderung
naik melebihi pasar. Berkas ini menguji klaim itu secara walk-forward pada panel yang
SAMA yang dipakai `study.py`, lalu membandingkan angkanya dengan yang dilaporkan
`api/market_study.json` — supaya angka yang tampil di dashboard bisa diperiksa, bukan
hanya dipercaya.

Dua hal yang dijawab di sini:
  1. DAYA PREDIKSI (walk-forward) — untuk tiap aturan yang DISAJIKAN screener:
     n sinyal, rata-rata & median return 20 hari ke depan (excess vs rata-rata pasar
     pada tanggal yang sama), blok t, net setelah biaya, kedua paruh waktu, dan
     HIT RATE (bagian hari-sinyal dengan excess positif).
  2. KECOCOKAN dengan study.py — angka di sini HARUS sama dengan api/market_study.json.
     Kalau tidak sama, ada yang menyimpang antara yang disajikan dan yang diukur.

Ini BUKAN janji keuntungan: aturan ini FILTER KEADAAN (daftar kandidat), dan alpha-nya
kecil untuk AS/ETF. Yang diuji adalah apakah klaim statistiknya jujur.

Jalankan:
    .venv/bin/python research/markets/backtest_screener.py --market crypto
    .venv/bin/python research/markets/backtest_screener.py --market us --panel research/.cache/markets/us_backtest_panel.pkl
    .venv/bin/python research/markets/backtest_screener.py --market etf --min-value 0
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import sys
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
API_DIR = os.path.join(os.path.dirname(RESEARCH), "api")
REPORTS = os.path.join(HERE, "reports")
sys.path.insert(0, HERE)

import state_rules as SR          # noqa: E402
import study as ST                # noqa: E402

# Aturan crypto yang DISAJIKAN /api/markets/crypto/screener (lihat CRYPTO_RULE_NAME
# di api/index.py). Ditulis di sini supaya backtest bisa jalan tanpa impor FastAPI.
CRYPTO_SERVED = ["mom5d>=10% + tembus high20", "tembus high20", "mom5d>=10%"]


def served_rules(market: str, study_json: dict) -> List[str]:
    """Nama aturan yang benar-benar disajikan screener untuk pasar ini."""
    if market == "crypto":
        return list(CRYPTO_SERVED)
    reg = study_json.get("state_rules") or {}
    lolos = set((((study_json.get("markets") or {}).get(market) or {})
                 .get("lolos_bar")) or [])
    return [m["rule"] for m in reg.values() if m.get("rule") in lolos]


def study_row(study_json: dict, market: str, rule: str) -> Optional[dict]:
    for row in (((study_json.get("markets") or {}).get(market) or {})
                .get("rules") or []):
        if row.get("aturan") == rule:
            return row
    return None


def _load_panel(market: str, panel_path: Optional[str]) -> pd.DataFrame:
    path = panel_path or os.path.join(RESEARCH, ".cache", "markets",
                                      f"{market}_panel.pkl")
    if not os.path.exists(path):
        raise SystemExit(f"Panel {path} belum ada — tarik dulu lewat pull.py, atau "
                         f"jalankan backtest ini di runner CI.")
    with open(path, "rb") as fh:
        return pickle.load(fh)


def hit_rate(S: pd.DataFrame, mask: pd.Series, h: int = 20) -> float:
    """Bagian hari-sinyal dengan excess return positif (bukan per-ticker)."""
    per = S.loc[mask].groupby("date")[f"excg{h}"].mean()
    if not len(per):
        return float("nan")
    return float((per > 0).mean() * 100.0)


def as_dict(row: pd.Series, h: int = 20) -> Dict[str, float]:
    out: Dict[str, float] = {"n": int(row["n"])}
    for c in (f"a{h}", f"m{h}", f"t{h}", f"net{h}"):
        v = row.get(c)
        out[c] = float(v) if v is not None and np.isfinite(v) else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest akurasi screener")
    ap.add_argument("--market", choices=["us", "etf", "crypto"], required=True)
    ap.add_argument("--panel", default=None,
                    help="panel .pkl alternatif (default cache research/.cache/markets)")
    ap.add_argument("--min-value", type=float, default=ST.MIN_VALUE_USD,
                    help="saringan nilai transaksi harian (USD); 0 = tanpa saringan")
    ap.add_argument("--h", type=int, default=20, help="horizon hari (default 20)")
    ap.add_argument("--all-rules", action="store_true",
                    help="uji SEMUA sinyal, bukan hanya yang disajikan screener "
                         "(dipakai untuk memeriksa keputusan 'menu kosong', mis. ETF)")
    ap.add_argument("--out", default=None, help="berkas JSON keluaran")
    args = ap.parse_args()

    study_json = {}
    sp = os.path.join(API_DIR, "market_study.json")
    if os.path.exists(sp):
        with open(sp, "r", encoding="utf-8") as fh:
            study_json = json.load(fh)

    rules = served_rules(args.market, study_json)
    if not rules and not args.all_rules:
        print(f"[{args.market}] tidak ada aturan yang disajikan screener — "
              "tidak ada yang perlu di-backtest (ini keadaan yang benar bila belum "
              "ada aturan lolos bar). Jalankan dengan --all-rules untuk memeriksa "
              "keputusan 'menu kosong' itu.")
        return 0

    panel = _load_panel(args.market, args.panel)
    print(f"Panel {args.market}: {len(panel):,} baris · {panel['code'].nunique()} ticker "
          f"· {panel['date'].min().date()} -> {panel['date'].max().date()}")
    S = ST.build_frame(panel, args.market)
    if args.all_rules:
        rules = [c for c in S.columns if c not in ("code", "date", "v20")
                 and not c.startswith(("fwd", "excg"))]
    tab = ST.measure(S, args.market, rules, min_value_usd=args.min_value)

    # Panel yang dipakai di sini BISA berbeda dari panel yang dipakai CI untuk
    # menulis study (mis. cache lokal lebih tua / cakupan berbeda). Bila ukuran
    # panelnya tidak sama, angkanya memang tidak bisa dibandingkan apa adanya —
    # itu ditandai terang-terangan, bukan dianggap "menyimpang".
    study_mkt = (study_json.get("markets") or {}).get(args.market) or {}
    # "Sama" = jumlah ticker identik DAN jumlah baris berbeda <1% (penarikan ulang
    # bisa menambah/mengurangi beberapa bar terakhir tanpa mengubah isi ukuran).
    st_rows = int(study_mkt.get("rows") or -1)
    rows_ok = st_rows > 0 and abs(len(S) - st_rows) <= 0.01 * st_rows
    panel_sama = (int(S["code"].nunique()) == int(study_mkt.get("tickers") or -1)
                  and rows_ok)
    provenance = (f"panel backtest {S['code'].nunique()} ticker / {len(S):,} baris vs "
                  f"study {study_mkt.get('tickers')} ticker / {study_mkt.get('rows')} baris")
    print(f"Provenans: {provenance}")
    if not panel_sama:
        print("Panel BERBEDA dari yang diukur CI -> kecocokan angka tidak bisa diuji "
              "pada jalur ini; tarik panel yang sama (pull.py) lalu jalankan ulang.")

    h = args.h
    print(f"\n=== BACKTEST SCREENER · {args.market.upper()} · {S['code'].nunique()} ticker "
          f"· biaya {ST.COST.get(args.market, 0.003) * 100:.1f}% · "
          f"min nilai {args.min_value:,.0f} USD · horizon {h} hari")
    print(f"{'aturan':<34}{'n':>8}  {'a20':>7}{'m20':>7}{'t20':>6}"
          f"  {'net20':>7}  {'hit20':>6}  paruh        vs study")

    results = []
    all_match = True
    for _, r in tab.iterrows():
        rule = r["aturan"]
        mask = S[rule].astype(bool) & (S["v20"] >= args.min_value
                                       if args.min_value else True)
        hr = hit_rate(S, mask, h)
        a, m, t, net = (r.get(f"a{h}"), r.get(f"m{h}"), r.get(f"t{h}"), r.get(f"net{h}"))
        srow = study_row(study_json, args.market, rule)
        if not panel_sama:
            verdict_txt = "panel beda"
            match = True          # tidak bisa diuji, bukan berarti salah
        elif srow is None:
            verdict_txt = "tidak ada di study"
            match = False
        else:
            match = all(abs(float(x) - float(y)) < 0.02 for x, y in
                        ((a, srow.get(f"a{h}", math.nan)), (m, srow.get(f"m{h}", math.nan)),
                         (net, srow.get(f"net{h}", math.nan)))
                        if x is not None and np.isfinite(x) and y is not None)
            verdict_txt = "COCOK" if match else "BEDA"
        all_match = all_match and match
        print(f"{rule:<34}{int(r['n']):>8,}  {a:>+7.2f}{m:>+7.2f}{t:>+6.1f}"
              f"  {net:>+7.2f}  {hr:>5.1f}%  {r['p20']:>11}  {verdict_txt}")
        results.append({**as_dict(r, h), "aturan": rule, "hit20_pct": hr,
                        "match_study": match, "half20": r.get("p20"),
                        "putusan": r.get("putusan")})

    print("\nCatatan: a/m20 = excess return vs rata-rata pasar pada tanggal yang sama "
          "(median = tahan-outlier). net20 sudah dikurangi biaya. hit20 = bagian "
          "hari-sinyal dengan excess positif. Angka HARUS sama dengan study.py; "
          "\"BEDA\" berarti ada yang menyimpang.")

    out = {"market": args.market, "tickers": int(S["code"].nunique()),
           "rows": int(len(S)), "horizon": h, "min_value_usd": args.min_value,
           "panel_sama_dengan_study": bool(panel_sama), "provenance": provenance,
           "rules": results, "all_match_study": bool(all_match and panel_sama),
           "note": ("Backtest walk-forward aturan yang disajikan screener; mesin ukur "
                    "yang sama dengan study.py. Filter keadaan, bukan sinyal beli.")}
    out_path = args.out or os.path.join(REPORTS, f"backtest_{args.market}.json")
    os.makedirs(REPORTS, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"-> {out_path}")
    if not panel_sama:
        print("KECOCOKAN study.py: TIDAK DIUJI (panel berbeda dari yang diukur CI)")
        return 0
    print("KECOCOKAN study.py:", "SEMUA COCOK" if all_match else "ADA YANG BEDA")
    return 0 if all_match else 1


if __name__ == "__main__":
    raise SystemExit(main())
