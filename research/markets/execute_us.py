#!/usr/bin/env python3
"""Verifikasi EKSEKUSI aturan saham AS & ETF (likuiditas + slippage + masuk di open).

Kenapa ada
----------
`study.py` mengukur KONDISI historis: alpha 20 hari setelah sinyal muncul, memakai
harga close. Itu menjawab "apakah sinyalnya punya daya prediksi", tetapi belum
menjawab "apakah hasilnya bisa DIDAPAT saat berdagang sungguhan". Tiga pertanyaan
yang belum dijawab di sana dan dijawab di sini:

  1. LIKUIDITAS — berapa besar nilai transaksi harian emiten yang sinyalnya menyala?
     Alpha +0,4% tidak berarti apa-apa kalau transaksinya tipis sehingga masuk/keluar
     menggerakkan harga.
  2. SLIPPAGE — berapa sisa alpha setelah biaya 0,3% DITAMBAH slippage nyata
     (0,2% / 0,5% / 1,0% round-trip)? Ini yang menentukan apakah net20 masih positif.
  3. MASUK DI OPEN — study memakai close hari sinyal (tidak bisa dibeli, sinyalnya
     baru diketahui setelah tutup). Di sini diuji masuk di OPEN hari berikutnya,
     keluar 20 bar kemudian, memakai pembanding pasar yang sama.

Berkas ini TIDAK memasang apa pun. Ia hanya mengukur; pemasangan diputuskan manusia.

Jalankan:
    .venv/bin/python research/markets/execute_us.py               # seluruh panel AS
    .venv/bin/python research/markets/execute_us.py --market etf  # panel ETF
    .venv/bin/python research/markets/execute_us.py --min-value 0 # tanpa saringan likuiditas
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from datetime import datetime, timezone
from typing import Dict, List

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, RESEARCH)

import study as ST          # noqa: E402  <- indikator & panel yang SAMA dengan pengukuran

CACHE_DIR = os.path.join(RESEARCH, ".cache", "markets")
REPORTS = os.path.join(HERE, "reports")
API_DIR = os.path.join(os.path.dirname(RESEARCH), "api")

# Aturan yang lolos bar proyek untuk AS; ETF memakai definisi keadaan yang SAMA
# (nama HARUS sama dengan laporan ukur) supaya angka bisa ditelusuri ke barisnya.
RULES = ["pullback di uptrend", "di atas SMA200", "dekat puncak 52m"]
SLIPPAGE_PCT = (0.0, 0.2, 0.5, 1.0)  # tambahan round-trip yang diuji
MIN_VALUE_USD = 1_000_000
LIQUID_BUCKETS = (1_000_000.0, 5_000_000.0, 20_000_000.0)


def build_exec_frame(panel: pd.DataFrame, min_value_usd: float) -> pd.DataFrame:
    """Satu baris per (kode, tanggal) berisi sinyal + return (close & open) + v20."""
    frames: List[pd.DataFrame] = []
    groups = panel.sort_values(["code", "date"]).groupby("code", sort=False)
    n_codes = panel["code"].nunique()
    for i, (code, g) in enumerate(groups):
        if len(g) < 60:
            continue
        df = g.set_index("date")
        sig = ST.signals_for(df)
        close = df["close"].astype(float)
        op = df["open"].astype(float) if "open" in df else close
        vol = df["volume"].astype(float) if "volume" in df else pd.Series(0.0, index=df.index)
        v20 = (close * vol).rolling(20, min_periods=20).mean()
        d = pd.DataFrame({"code": code, "date": df.index})
        for name in RULES:
            d[name] = sig[name].to_numpy()
        d["v20"] = v20.to_numpy()
        # Keluar di close 20 bar kemudian, masuk di close hari sinyal (definisi study).
        d["fwd20"] = (close.shift(-20) / close - 1.0).mul(100.0).clip(-100, 100).to_numpy()
        # Masuk di OPEN hari berikutnya (yang benar-benar bisa dibeli) lalu keluar 20 bar.
        d["open20"] = (close.shift(-20) / op.shift(-1) - 1.0).mul(100.0).clip(-100, 100).to_numpy()
        if min_value_usd:
            d = d[d["v20"] >= min_value_usd]
        frames.append(d)
        if (i + 1) % 1000 == 0:
            print(f"  ... {i + 1}/{n_codes} ticker")
    if not frames:
        raise SystemExit("Panel terlalu pendek untuk diverifikasi (butuh >=60 bar/ticker).")
    S = pd.concat(frames, ignore_index=True)
    # Pembanding pasar: rata-rata seluruh panel pada tanggal yang sama (kelas tunggal,
    # sama seperti study.py) supaya angkanya bisa dibandingkan langsung.
    for col in ("fwd20", "open20"):
        S[f"exc_{col}"] = S[col] - S.groupby("date")[col].transform("mean")
    return S


def _net(mean_raw: float, slippage: float, cost_pct: float) -> float:
    """Net20 setelah biaya tetap + slippage tambahan (keduanya dalam persen)."""
    return mean_raw - cost_pct - slippage


def measure(S: pd.DataFrame, cost_pct: float) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for name in RULES:
        m = S[name].astype(bool)
        if not m.any():
            out[name] = {"n": 0}
            continue
        v20 = S.loc[m, "v20"].astype(float)
        raw_close = float(S.loc[m, "fwd20"].mean())
        exc_close = float(S.loc[m, "exc_fwd20"].mean())
        raw_open = float(S.loc[m, "open20"].mean())
        exc_open = float(S.loc[m, "exc_open20"].mean())
        out[name] = {
            "n": int(m.sum()),
            "liquidity": {
                "v20_mean_usd": round(float(v20.mean()), 0),
                "v20_median_usd": round(float(v20.median()), 0),
                "v20_p10_usd": round(float(v20.quantile(0.10)), 0),
                "share_ge_1m": round(float((v20 >= 1_000_000).mean()) * 100, 1),
                "share_ge_5m": round(float((v20 >= 5_000_000).mean()) * 100, 1),
                "share_ge_20m": round(float((v20 >= 20_000_000).mean()) * 100, 1),
            },
            "close_entry": {
                "raw20_pct": round(raw_close, 2),
                "excess20_pct": round(exc_close, 2),
                "net_by_slippage": {str(s): round(_net(raw_close, s, cost_pct), 2)
                                    for s in SLIPPAGE_PCT},
            },
            "open_entry": {
                "raw20_pct": round(raw_open, 2),
                "excess20_pct": round(exc_open, 2),
                "net_by_slippage": {str(s): round(_net(raw_open, s, cost_pct), 2)
                                    for s in SLIPPAGE_PCT},
            },
        }
    return out


def report_text(res: Dict[str, dict], market: str, tickers: int, bars: int,
                cost_pct: float) -> str:
    lines = [f"=== VERIFIKASI EKSEKUSI {market.upper()} · {tickers} ticker · "
             f"{bars:,} baris · biaya {cost_pct}% · return dipotong +/-100%",
             "Pertanyaan: apakah alpha study BERTAHAN saat likuiditas, slippage, dan "
             "masuk-di-open diperhitungkan?",
             f"{'aturan':<22}{'n':>10}  {'v20 median':>12}{'>=5jt':>7}  "
             f"{'net close':>10}{'net open':>10}  {'open +slip 0,5%':>16}"]
    for name in RULES:
        r = res.get(name) or {}
        if not r.get("n"):
            lines.append(f"{name:<22}{0:>10}  (tidak ada sinyal)")
            continue
        liq, ce, oe = r["liquidity"], r["close_entry"], r["open_entry"]
        lines.append(
            f"{name:<22}{r['n']:>10,}  "
            f"{liq['v20_median_usd'] / 1e6:>10.1f}jt{liq['share_ge_5m']:>6.0f}%  "
            f"{ce['net_by_slippage']['0.0']:>+10.2f}{oe['net_by_slippage']['0.0']:>+10.2f}  "
            f"{oe['net_by_slippage']['0.5']:>+16.2f}")
    lines.append("")
    lines.append("Bacaan: 'net close' = seperti study (masuk di close hari sinyal). "
                 "'net open' = masuk di open besok (yang benar-benar bisa dibeli). "
                 "Kalau 'net open' di slippage 0,5% sudah <= 0, aturan itu belum layak "
                 "dipasang ke menu tanpa eksekusi yang jauh lebih baik.")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Verifikasi eksekusi aturan AS/ETF")
    ap.add_argument("--market", choices=["us", "etf"], default="us")
    ap.add_argument("--min-value", type=float, default=MIN_VALUE_USD,
                    help="saringan nilai transaksi harian (USD); 0 = tanpa saringan")
    args = ap.parse_args()

    path = os.path.join(CACHE_DIR, f"{args.market}_panel.pkl")
    if not os.path.exists(path):
        raise SystemExit(f"Panel {path} belum ada — jalankan pull.py lebih dulu.")
    with open(path, "rb") as fh:
        panel = pickle.load(fh)
    print(f"Panel {args.market}: {len(panel):,} baris · {panel['code'].nunique()} ticker")

    cost_pct = ST.COST.get(args.market, 0.003) * 100.0
    S = build_exec_frame(panel, args.min_value)
    res = measure(S, cost_pct)
    text = report_text(res, args.market, int(panel["code"].nunique()),
                       int(len(panel)), cost_pct)
    os.makedirs(REPORTS, exist_ok=True)
    rep_name = f"report_{args.market}_exec.txt"
    with open(os.path.join(REPORTS, rep_name), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "market": args.market,
        "cost_pct": cost_pct,
        "slippage_pct_tested": list(SLIPPAGE_PCT),
        "min_value_usd": args.min_value,
        "bars": int(len(S)),
        "note": ("Verifikasi eksekusi, BUKAN pemasangan. Menjawab likuiditas, sisa alpha "
                 "setelah slippage, dan apakah alpha bertahan bila masuk di open besok."),
        "rules": res,
    }
    out_json = os.path.join(API_DIR, f"market_{args.market}_exec.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"-> {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
