#!/usr/bin/env python3
"""Backtest multi-tahun untuk sinyal "kekuatan relatif vs IHSG" (RS line).

Latar belakang
--------------
Validasi cepat (Sep 2026) menunjukkan kriteria "RS Leader" yang dipakai di
api/index.py TIDAK punya daya prediksi jangka pendek (IC Spearman skor RS ≈ -0,07
terhadap excess return besoknya). Script ini menguji hipotesis itu secara serius
dengan riwayat 2-5 tahun dan beberapa varian sinyal, supaya keputusan
mengaktifkan cron/notifikasi berbasis bukti, bukan firasat.

Apa yang diukur
---------------
Untuk setiap (saham, hari) dihitung sinyal HANYA dari data s/d hari itu, lalu
dibandingkan dengan hasil ke depan pada beberapa horizon (1/5/10/20 hari bursa):

  * abs_h  : return absolut saham (jujur soal "masih merah")
  * exc_h  : return saham - return IHSG pada periode sama ("mengalahkan pasar")

Statistik di-cluster PER TANGGAL: tiap tanggal dihitung rata-rata sinyal dikurangi
rata-rata seluruh universe pada tanggal itu, lalu dirata-rata lintas tanggal
dengan t-stat. Ini mencegah signifikansi palsu akibat korelasi silang antar saham
pada hari yang sama.

Varian yang diuji
-----------------
RS Leader (definisi sekarang) · RS Leader + likuiditas · RS line rekor 20 hari ·
hijau saat IHSG merah (dengan/tanpa volume & likuiditas) · volume spike ·
tren naik · top-N peringkat skor/alpha/momentum.

Batasan (penting)
-----------------
1. Universe = daftar emiten SAAT INI -> ada survivorship bias (emiten yang
   delisting tidak ikut terhitung, hasil cenderung terlalu optimistis).
2. Angka "abs_h" belum dipotong biaya; transaksi IDX realistis ~0,3% round-trip.
3. Harga memakai adjusted close (split/dividen sudah disesuaikan).

Jalankan:
  .venv/bin/python research/backtest_rs.py --years 5
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from curl_cffi import requests as cr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api.index as ai  # noqa: E402  (load_idx_tickers + helper indeks harga)

HORIZONS = (1, 5, 10, 20, 60)
WARMUP = 60  # bar minimum sebelum sinyal dianggap valid


# ---------------------------------------------------------------------------
# 1. DATA
# ---------------------------------------------------------------------------

def _fetch_yahoo(symbol: str, rng: str, attempts: int = 3) -> Optional[pd.DataFrame]:
    """OHLCV + adjusted close harian dari Yahoo chart API (via curl_cffi)."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range={rng}&interval=1d")
    for _ in range(attempts):
        try:
            res = cr.get(url, impersonate="chrome", timeout=20).json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            ac = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose")
            idx = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert(
                "Asia/Jakarta").tz_localize(None)
            df = pd.DataFrame({"Open": q["open"], "Close": q["close"], "Adj": ac,
                               "High": q["high"], "Low": q["low"], "Volume": q["volume"]}, index=idx)
            return df.dropna(subset=["Close"])
        except Exception:
            time.sleep(0.4)
    return None


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    """Index harian -> tanggal (tanpa jam/timezone), buang duplikat."""
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out = df.copy()
    out.index = idx.normalize()
    return out[~out.index.duplicated(keep="last")].sort_index()


def load_data(years: int, universe: str, workers: int) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    rng = f"{years}y"
    tickers = ai.load_idx_tickers(universe)
    print(f"[1/3] Mengunduh {len(tickers)} saham ({rng}) + IHSG…", flush=True)
    ih_raw = _fetch_yahoo("^JKSE", f"{max(years, 2)}y")
    if ih_raw is None:
        raise SystemExit("Gagal mengunduh IHSG.")
    ih = _norm(ih_raw)
    if "Adj" not in ih or ih["Adj"].isna().all():
        ih["Adj"] = ih["Close"]

    t0 = time.time()
    data: Dict[str, pd.DataFrame] = {}
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for tk, df in ex.map(lambda t: (t, _fetch_yahoo(t, rng)), tickers):
            if df is None or len(df) < WARMUP + 25:
                failed += 1
                continue
            df = _norm(df)
            if df["Volume"].fillna(0).sum() <= 0:
                failed += 1
                continue
            if "Adj" not in df or df["Adj"].isna().all():
                df["Adj"] = df["Close"]
            data[tk] = df
    print(f"      {len(data)} saham siap, {failed} dilewati, {time.time() - t0:.0f}s")
    return ih, data


# ---------------------------------------------------------------------------
# 2. SINYAL (vektor, tanpa look-ahead: semua memakai data s/d bar itu)
# ---------------------------------------------------------------------------

def build_rows(tk: str, df: pd.DataFrame, ih: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Hitung sinyal + hasil ke depan untuk 1 saham. Semua operasi vektor."""
    out = pd.DataFrame(index=df.index)
    c = df["Adj"].astype(float)
    v = df["Volume"].astype(float).fillna(0.0)
    i = ih["Adj"].reindex(out.index, method="ffill").astype(float)
    out["c"] = c
    out["i"] = i
    out["px"] = df["Close"].astype(float)  # harga nominal (untuk proxy market cap)
    valid = c.notna() & i.notna() & (v > 0)

    out["ret"] = c.pct_change()
    i_ret = i.pct_change()

    # --- RS line & struktur ---
    rs = c / i
    rs_ma20 = rs.rolling(20).mean()
    out["above_rsma"] = (rs > rs_ma20)
    out["new20"] = (rs >= rs.rolling(20).max() - 1e-12)
    out["new60"] = (rs >= rs.rolling(60).max() - 1e-12)

    for n in (5, 20, 60):
        out[f"a{n}"] = c.pct_change(n) - i.pct_change(n)

    s20 = c.rolling(20).mean()
    s50 = c.rolling(50).mean()
    out["above20"] = c > s20
    out["s20gt50"] = s20 > s50
    out["trend_up"] = out["above20"] & out["s20gt50"]

    # --- volume & likuiditas (rupiah) ---
    vol_ma20 = v.rolling(20).mean()
    out["vr"] = v / vol_ma20.replace(0, np.nan)
    val20 = (c * v).rolling(20).mean()
    out["val20"] = val20
    out["liq"] = val20 >= 1e9  # >= Rp 1 miliar/hari

    # --- Sinyal tambahan khusus saham likuid ---
    # Harga Open/High disesuaikan (adjclose/close) supaya split & dividen tidak
    # memunculkan "gap" palsu.
    adjf = df["Adj"].astype(float) / df["Close"].astype(float)
    op = df["Open"].astype(float) * adjf
    hi_px = df["High"].astype(float) * adjf
    prev_high = hi_px.shift(1)
    out["gap_up_any"] = (op > prev_high * 1.02).fillna(False)
    out["gap_up"] = (out["gap_up_any"] & (out["vr"] >= 1.5)).fillna(False)
    out["breakout20"] = (c > hi_px.rolling(20).max().shift(1)).fillna(False)
    bb_mid = c.rolling(20).mean()
    bb_w = (4.0 * c.rolling(20).std(ddof=0)) / bb_mid.replace(0, np.nan)
    out["squeeze"] = (bb_w <= bb_w.rolling(60).quantile(0.2)).fillna(False)
    out["squeeze_break"] = (out["squeeze"].shift(1).fillna(False) & out["breakout20"]).fillna(False)
    out["rvol15"] = (out["vr"] >= 1.5).fillna(False)
    out["rvol20"] = (out["vr"] >= 2.0).fillna(False)
    out["rvol30"] = (out["vr"] >= 3.0).fillna(False)

    # --- Komponen skor beli aplikasi (untuk audit per komponen) ---
    try:
        comp = ai.buy_score_components(df).reindex(out.index)
        for col in comp.columns:
            out[f"bs_{col}"] = comp[col].astype("float32")
    except Exception:
        pass

    # --- hari merah IHSG ---
    red = i_ret < 0
    green = out["ret"] > 0
    out["green_on_red"] = green & red
    out["gor_vol"] = out["green_on_red"] & (out["vr"] >= 1.5)
    out["volspike"] = out["vr"] >= 2.0
    red_sum = red.rolling(20).sum().replace(0, np.nan)
    out["red_win"] = (green & red).rolling(20).sum() / red_sum

    # --- skor RS (sama dengan relative_strength di api/index.py) ---
    score = (25 * out["new20"].fillna(False).astype(float)
             + 10 * out["new60"].fillna(False).astype(float)
             + 15 * out["above_rsma"].fillna(False).astype(float)
             + 10 * (out["a5"] > 0).fillna(False).astype(float)
             + 5 * (out["a5"] >= 5).fillna(False).astype(float)
             + 15 * (out["a20"] > 0).fillna(False).astype(float)
             + 10 * (out["a20"] >= 10).fillna(False).astype(float)
             + 10 * (out["red_win"] >= 0.6).fillna(False).astype(float)
             + 5 * out["above20"].fillna(False).astype(float)
             + 5 * out["s20gt50"].fillna(False).astype(float))
    out["score"] = score.clip(upper=100)

    outperform = ((out["a5"] >= 5) | (out["a20"] >= 10) | (out["red_win"] >= 0.5))
    out["leader"] = (out["above_rsma"].fillna(False)
                     & (out["a5"] > 0).fillna(False)
                     & (out["score"] >= 60)
                     & (out["new20"].fillna(False) | outperform.fillna(False)))
    out["mom5"] = out["a5"]  # alpha 5 hari = proxy momentum relatif

    # Skor beli komposit milik aplikasi (api/index.py) — diuji agar apple-to-apple
    # dengan kriteria "BUY KUAT (skor >= 70)" yang sudah dipakai dashboard.
    try:
        out["buy_score"] = ai._buy_score_series(df).reindex(out.index)
    except Exception:
        out["buy_score"] = np.nan

    # --- Kandidat skor beli v2 (REPLIKA script) --------------------------------
    # Sejak Sept 2026 formula ini SUDAH dipakai di api/index.py (skor beli v2), jadi
    # kolom `buy_score` (dari ai._buy_score_series) dan `buy_score_cand` (replika di
    # sini) seharusnya SAMA. Selisihnya dicek di main() sebagai regression test: kalau
    # berbeda, salah satu jalur (live vs backtest) sudah bergeser.
    #
    # Audit 5 tahun di universe likuid (alpha20 / t-stat) → dasar penimbangan:
    #   Harga > SMA20          +0.26 / +3.1  → bobot 15
    #   Harga > SMA50          +0.15 / +2.0  → bobot 10
    #   SMA20 > SMA50          (tren naik  +0.56 / +5.0) → bobot 5
    #   RSI zona tengah 40-65  -0.31 / -8.7  → DIBALIK: RSI > 60 (kekuatan)
    #   MACD hist>0 & naik     +0.22 / +3.0  → bobot 10
    #   Volume vs VMA20        +0.33 / +5.5  → bobot 15 (komponen terkuat)
    #   Pola candlestick       -0.01 / -0.1  → DIBUANG
    #   Dekat support          -0.42 / -7.9  → DIBUANG (bias mean-reversion)
    #   Breakout high 20 hari  +1.91 / +3.9  → bobot 15 (sinyal likuid terbaik)
    #   Momentum 5 hari sehat  +0.14 / +2.1  → bobot 5
    try:
        # Semua dihitung dari harga MENTAH (Close/High), sama seperti aplikasi:
        # api/index.py tidak punya kolom Adjusted close, jadi komponen skor di sana
        # memakai harga nominal + nilai transaksi = Close x Volume.
        c_raw = df["Close"].astype(float)
        h_raw = df["High"].astype(float)
        r14 = ai.rsi(c_raw, 14)
        _, _, hh = ai.macd(c_raw)
        s20_raw = c_raw.rolling(20).mean()
        s50_raw = c_raw.rolling(50).mean()
        vma20 = v.rolling(20).mean()
        vr2 = v / vma20.replace(0, np.nan)
        ret5b = c_raw.pct_change(5) * 100
        val20_raw = (c_raw * v).rolling(20).mean()
        brk_raw = c_raw > h_raw.rolling(20).max().shift(1)

        def _w(cond, val):
            s = pd.Series(cond, index=out.index)
            return s.fillna(False).astype(bool).astype(float) * float(val)

        macd_rising = (hh > 0) & (hh >= hh.shift(1))
        v2 = pd.DataFrame(index=out.index)
        v2["trend20"] = _w(c_raw > s20_raw, 15)
        v2["trend50"] = _w(c_raw > s50_raw, 10)
        v2["align"] = _w(s20_raw > s50_raw, 5)
        v2["rsi"] = _w(r14 > 60, 10) + _w((r14 > 50) & (r14 <= 60), 5)
        v2["macd"] = _w(macd_rising, 10) + _w((hh > 0) & ~macd_rising.fillna(False), 5)
        v2["volume"] = _w(vr2 >= 1.5, 15) + _w((vr2 >= 1.0) & (vr2 < 1.5), 10) + _w((vr2 >= 0.8) & (vr2 < 1.0), 4)
        v2["breakout"] = _w(brk_raw, 15)
        v2["momentum"] = _w((ret5b >= 0) & (ret5b <= 20), 5)
        v2["liquidity"] = _w(val20_raw >= 10e9, 15) + _w((val20_raw >= 1e9) & (val20_raw < 10e9), 10) + _w((val20_raw >= 100e6) & (val20_raw < 1e9), 5)
        comp2 = v2.fillna(0.0)
        for col in comp2.columns:
            out[f"v2_{col}"] = comp2[col].astype("float32")
        out["buy_score_cand"] = comp2.sum(axis=1).clip(upper=100.0)
    except Exception:
        out["buy_score_cand"] = np.nan

    # --- hasil ke depan (absolut & excess vs IHSG), semua horizon ---
    fwd_ok = pd.Series(True, index=out.index)
    for h in HORIZONS:
        fwd = c.shift(-h) / c - 1.0
        ih_fwd = i.shift(-h) / i - 1.0
        out[f"abs{h}"] = fwd * 100.0
        out[f"exc{h}"] = (fwd - ih_fwd) * 100.0
        out[f"ih{h}"] = ih_fwd * 100.0  # benchmark IHSG pada jendela yang sama
        fwd_ok &= fwd.notna()

    # hanya bar yang "layak": cukup warmup, ada transaksi, dan hasil depan tersedia
    pos = pd.Series(np.arange(len(out)), index=out.index)
    keep = valid & fwd_ok & (pos >= WARMUP)
    out = out[keep]
    if out.empty:
        return None
    out["tk"] = tk
    return out.reset_index().rename(columns={"index": "date"})


# ---------------------------------------------------------------------------
# 3. EVALUASI (cluster per tanggal)
# ---------------------------------------------------------------------------

def top_n_mask(R: pd.DataFrame, column: str, n: int) -> pd.Series:
    """Mask True untuk n nilai `column` tertinggi pada setiap tanggal."""
    rank = R.groupby("date")[column].rank(ascending=False, method="first")
    return rank <= n


def evaluate(R: pd.DataFrame, mask: pd.Series, label: str,
             universe_mask: Optional[pd.Series] = None) -> List[dict]:
    """Statistik sinyal, di-cluster per tanggal. universe_mask membatasi PEMBANDING
    (mis. hanya saham likuid) supaya alpha diukur relatif ke universe yang relevan."""
    rows = []
    uni_cache: Dict[int, pd.Series] = {}
    base = R if universe_mask is None else R.loc[universe_mask.fillna(False)]
    for h in HORIZONS:
        sel = R.loc[mask.fillna(False), ["date", f"exc{h}", f"abs{h}"]].dropna()
        if len(sel) < 30:
            continue
        if h not in uni_cache:
            uni_cache[h] = base.groupby("date")[f"exc{h}"].mean()
        uni = uni_cache[h]
        per_date = sel.groupby("date")[f"exc{h}"].mean()
        diff = (per_date - uni).dropna()
        n_dates = len(diff)
        sd = diff.std(ddof=1)
        tstat = float(diff.mean() / (sd / math.sqrt(n_dates))) if n_dates > 1 and sd > 0 else float("nan")
        rows.append({
            "variant": label, "h": h, "n": len(sel),
            "per_hari": len(sel) / max(n_dates, 1),
            "abs": sel[f"abs{h}"].mean(),
            "hit_abs": (sel[f"abs{h}"] > 0).mean() * 100,
            "exc": sel[f"exc{h}"].mean(),
            "hit_exc": (sel[f"exc{h}"] > 0).mean() * 100,
            "alpha": diff.mean(), "t": tstat, "hari": n_dates,
        })
    return rows


def print_report(R: pd.DataFrame) -> None:
    universe_abs = {h: R[f"abs{h}"].mean() for h in HORIZONS}
    print("\n[3/3] HASIL BACKTEST")
    print("=" * 104)
    print("Baseline universe (semua saham x hari):")
    print("   " + " | ".join(f"abs{h}h {universe_abs[h]:+.2f}%" for h in HORIZONS)
          + f"   (n={len(R):,} observasi, {R['date'].nunique()} tanggal)")

    variants: List[Tuple[str, pd.Series]] = [
        ("RS Leader (definisi sekarang)", R["leader"] == 1),
        ("RS Leader + likuiditas >=1M", (R["leader"] == 1) & R["liq"]),
        ("RS line rekor 20h", R["new20"] == 1),
        ("RS line rekor 20h + likuid", (R["new20"] == 1) & R["liq"]),
        ("Hijau saat IHSG merah", R["green_on_red"] == 1),
        ("Hijau saat merah + vol 1.5x", R["gor_vol"] == 1),
        ("Hijau saat merah + vol + likuid", R["gor_vol"] & R["liq"]),
        ("Volume spike >=2x MA20", R["volspike"] == 1),
        ("Volume spike + likuid", (R["volspike"] == 1) & R["liq"]),
        ("Tren naik C>SMA20>SMA50", R["trend_up"] == 1),
        ("RS > MA20 (paling longgar)", R["above_rsma"] == 1),
        ("Top-10 skor RS / hari", top_n_mask(R, "score", 10)),
        ("Top-10 alpha20 / hari", top_n_mask(R, "a20", 10)),
        ("Top-10 momentum 5h / hari", top_n_mask(R, "mom5", 10)),
        ("Bottom-10 alpha20 / hari", R.groupby("date")["a20"].rank(method="first") <= 10),
    ]

    for label, mask in variants:
        rows = evaluate(R, mask, label)
        if not rows:
            print(f"\n{label}: data tidak cukup")
            continue
        print(f"\n{label}")
        print(f"   {'h':>3} {'n':>8} {'perhari':>8} {'abs%':>7} {'hijau%':>7} "
              f"{'exc%':>7} {'>IHSG%':>7} {'alpha%':>7} {'t-stat':>7}")
        for r in rows:
            print(f"   {r['h']:>3} {r['n']:>8,} {r['per_hari']:>8.1f} {r['abs']:>+7.2f} "
                  f"{r['hit_abs']:>7.1f} {r['exc']:>+7.2f} {r['hit_exc']:>7.1f} "
                  f"{r['alpha']:>+7.2f} {r['t']:>+7.2f}")

    # --- Stabilitas per tahun: alpha bisa saja hanya datang dari 1 tahun ---
    R["_year"] = pd.to_datetime(R["date"]).dt.year
    uni5 = R.groupby("date")["exc5"].mean()
    uni20 = R.groupby("date")["exc20"].mean()
    key_variants: List[Tuple[str, pd.Series]] = [
        ("RS Leader (luas)", R["leader"] == 1),
        ("RS Leader + likuid", (R["leader"] == 1) & R["liq"]),
        ("Top-10 skor RS", top_n_mask(R, "score", 10)),
        ("Top-10 alpha20", top_n_mask(R, "a20", 10)),
        ("Hijau saat merah", R["green_on_red"] == 1),
        ("Tren naik", R["trend_up"] == 1),
    ]
    print("\nStabilitas per tahun (alpha% vs universe) — 'positif' = berapa tahun yang untung:")
    print(f"   {'varian':<20}{'horizon':>8}" + "".join(f"{y:>9}" for y in sorted(R["_year"].unique()))
          + f"{'positif':>9}")
    for label, mask in key_variants:
        sel = R.loc[mask.fillna(False)]
        for h, uni in ((5, uni5), (20, uni20)):
            cells = []
            for y, g in sel.groupby("_year"):
                d = (g.groupby("date")[f"exc{h}"].mean() - uni).dropna()
                cells.append(d.mean() if len(d) else float("nan"))
            pos = sum(1 for c in cells if c > 0)
            print(f"   {label:<20}{'h=' + str(h):>8}"
                  + "".join(f"{c:>+9.2f}" for c in cells)
                  + f"{pos:>6}/{len(cells)}")

    print("\nIC Spearman rata-rata (korelasi sinyal harian vs excess return depan):")
    ic_cols = ["score", "a5", "a20", "a60", "vr", "val20", "red_win"]
    print(f"   {'sinyal':<10}" + "".join(f"{'h=' + str(h):>10}" for h in HORIZONS))
    for col in ic_cols:
        line = f"   {col:<10}"
        for h in HORIZONS:
            ics = []
            for _, g in R.groupby("date"):
                if g[col].nunique() < 5:
                    continue
                ics.append(g[col].rank().corr(g[f"exc{h}"].rank()))
            line += f"{np.nanmean(ics):>+10.3f}" if ics else f"{'n/a':>10}"
        print(line)
    print("\nCatatan: alpha% = keunggulan vs rata-rata universe pada tanggal yang sama;"
          "\nt-stat dihitung lintas tanggal (|t| > 2 ≈ layak dipercaya)."
          "\nabs% belum dipotong biaya ~0,3% round-trip; ada survivorship bias.")


def load_meta() -> pd.DataFrame:
    """Metadata emiten dari dataset IDX: shares (proxy market cap), listingDate, papan."""
    import io
    import urllib.request
    try:
        req = urllib.request.Request(ai.IDX_TICKER_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
        m = pd.read_csv(io.BytesIO(raw))
        m["code"] = m["code"].astype(str).str.upper().str.strip()
        m["listingDate"] = pd.to_datetime(m["listingDate"], errors="coerce")
        return m[["code", "name", "listingDate", "shares", "listingBoard"]]
    except Exception:
        return pd.DataFrame(columns=["code", "name", "listingDate", "shares", "listingBoard"])


def print_breakdown(R: pd.DataFrame) -> None:
    """Di kelompok mana alpha RS Leader benar-benar berada (likuiditas/size/papan/kohort)?"""
    L = R[R["leader"] == 1].copy()
    if L.empty:
        return
    uni = R.groupby("date")["exc5"].mean()

    def alpha_of(sub: pd.DataFrame) -> Tuple[Optional[float], int]:
        if len(sub) < 200:
            return None, len(sub)
        d = (sub.groupby("date")["exc5"].mean() - uni).dropna()
        return (float(d.mean()) if len(d) else None), len(sub)

    buckets: Dict[str, pd.Series] = {}
    buckets["Likuiditas (Rp/hari)"] = pd.cut(
        L["val20"], [0, 1e8, 1e9, 1e10, np.inf],
        labels=["<100jt", "100jt-1M", "1-10M", ">10M"])
    if "shares" in L.columns:
        buckets["Kapitalisasi (proxy)"] = pd.cut(
            L["px"] * L["shares"], [0, 1e12, 1e13, 1e14, np.inf],
            labels=["<Rp1T", "1-10T", "10-100T", ">100T"])
    if "listingBoard" in L.columns:
        buckets["Papan pencatatan"] = L["listingBoard"].fillna("?")
    if "first_year" in L.columns:
        buckets["Kohort listing"] = np.where(L["first_year"] <= 2021, "lama (>=5 th)", "baru (<5 th)")

    print("\nAMBANG LIKUIDITAS — KAPAN EDGE-NYA HILANG (RS Leader, alpha h=5):")
    n_dates = max(R["date"].nunique(), 1)
    print(f"   {'ambang':<16}{'n':>9}{'per-hari':>10}{'alpha h5':>11}")
    for floor, lab in ((0.0, "tanpa filter"), (1e8, ">=100 juta"), (1e9, ">=1 miliar"),
                       (5e9, ">=5 miliar"), (1e10, ">=10 miliar")):
        sub = L if floor == 0 else L[L["val20"] >= floor]
        a, n = alpha_of(sub)
        shown = "n/a" if a is None else f"{a:+.2f}%"
        print(f"   {lab:<16}{n:>9,}{n / n_dates:>10.1f}{shown:>11}")

    print("\nDI MANA ALPHA RS LEADER BERADA (alpha h=5 vs universe, acuan luas = +1,20%):")
    for title, g in buckets.items():
        s = pd.Series(np.asarray(g), index=L.index)
        print(f"   {title}:")
        for k in pd.unique(s.dropna()):
            sub = L[s == k]
            a, n = alpha_of(sub)
            if a is None:
                print(f"      {str(k):<14} n={n:>7,}  (data kurang)")
            else:
                print(f"      {str(k):<14} n={n:>7,}  alpha h5 {a:+.2f}%")


def simulate_equity(R: pd.DataFrame, mask: pd.Series, label: str,
                    top_n: int = 10, hold: int = 5, cost: float = 0.003,
                    rank_col: Optional[str] = "score", min_names: int = 5,
                    quiet: bool = False) -> dict:
    """Portofolio non-overlapping: tiap `hold` hari bursa pilih N nama teratas (bila
    rank_col=None -> SELURUH nama yang lolos mask, bobot sama), biaya 0,3% x turnover.
    Pembanding: IHSG buy&hold dan universe sama-rata.

    CAGR memakai waktu berjalan yang SEBENARNYA (window yang dilewati ikut dihitung),
    jadi strategi yang sering tidak punya sinyal tidak bisa terlihat untung hanya
    karena banyak duduk di kas.

    Mengembalikan dict berisi angka + jalur ekuitas (dipakai
    scripts/export_pattern_sim.py untuk mengisi api/pattern_sim.json), atau {} bila
    tidak ada sinyal.
    """
    cols = ["date", "tk", f"abs{hold}", f"ih{hold}"] + ([rank_col] if rank_col else [])
    sel = R.loc[mask.fillna(False), cols].dropna()
    if sel.empty:
        if not quiet:
            print(f"\n{label}: tidak ada sinyal.")
        return {}
    allw = R[["date", f"abs{hold}", f"ih{hold}"]].dropna()
    uni_w = {pd.Timestamp(k): float(v) for k, v in
             allw.groupby("date")[f"abs{hold}"].mean().items()}
    ih_w = {pd.Timestamp(k): float(v) for k, v in
            allw.groupby("date")[f"ih{hold}"].mean().items()}
    dates = [pd.Timestamp(x) for x in sorted(allw["date"].unique())]
    by_date = {pd.Timestamp(d): g for d, g in sel.groupby("date")}

    eq = bh = uq = 1.0
    eqs: List[float] = [1.0]
    bhs: List[float] = [1.0]
    uqs: List[float] = [1.0]
    turns: List[float] = []
    prev: set = set()
    windows = invested = 0
    i = 60
    while i < len(dates) - hold:
        d = dates[i]
        g = by_date.get(d)
        i += hold
        windows += 1
        # Pembanding (IHSG & universe) SELALU berjalan tiap window, termasuk saat
        # strategi tidak punya sinyal — supaya tidak terlihat untung hanya karena
        # duduk di kas (buy&hold tetap terinvestasi penuh).
        bh *= (1.0 + ih_w.get(d, 0.0) / 100.0)
        uq *= (1.0 + uni_w.get(d, 0.0) / 100.0)
        if g is None or len(g) < min_names:
            eqs.append(eq); bhs.append(bh); uqs.append(uq)
            continue
        picks = g.nlargest(min(top_n, len(g)), rank_col) if rank_col else g
        tks = list(picks["tk"])
        invested += 1
        turn = 1.0 if not prev else len(set(tks) - prev) / max(len(tks), 1)
        r = float(picks[f"abs{hold}"].mean()) / 100.0 - cost * turn
        eq *= (1.0 + r)
        turns.append(turn)
        eqs.append(eq); bhs.append(bh); uqs.append(uq)
        prev = set(tks)

    ppy = 252.0 / hold
    years = max(windows * hold / 252.0, 1e-9)

    def stats(path: List[float]) -> Tuple[float, float, float, float]:
        p = np.array(path, dtype=float)
        if len(p) < 3:
            return (float("nan"),) * 4
        r = np.diff(p) / p[:-1]
        cagr = (p[-1] / p[0]) ** (1.0 / years) - 1.0
        sd = float(np.std(r, ddof=1))
        vol = sd * math.sqrt(ppy)
        sharpe = float(np.mean(r) / sd * math.sqrt(ppy)) if sd > 0 else float("nan")
        peak = np.maximum.accumulate(p)
        mdd = float(((p - peak) / peak).min())
        return cagr * 100, vol * 100, sharpe, mdd * 100

    mode = f"top-{top_n} by {rank_col}" if rank_col else "seluruh sinyal (bobot sama)"
    if not quiet:
        print(f"\nEKUITAS: {label} — {mode}, hold {hold} hari, biaya 0,3% x turnover")
        print(f"   {'strategi':<22}{'total':>10}{'CAGR%':>9}{'vol%':>8}{'Sharpe':>8}{'MDD%':>8}")
        for name, path in (("Sinyal", eqs), ("IHSG (benchmark)", bhs), ("Universe sama-rata", uqs)):
            cagr, vol, sharpe, mdd = stats(path)
            tot = (path[-1] - 1) * 100 if path else float("nan")
            print(f"   {name:<22}{tot:>+9.1f}%{cagr:>9.1f}{vol:>8.1f}{sharpe:>8.2f}{mdd:>8.1f}")
        if turns:
            print(f"   turnover rata-rata per rebalance: {np.mean(turns) * 100:.0f}%  "
                  f"(biaya per rebalance ~{np.mean(turns) * cost * 100:.2f}%)")
        print(f"   window: {invested}/{windows} terisi ({invested / max(windows, 1) * 100:.0f}%), "
              f"~{ppy:.0f} rebalance/tahun, durasi {years:.1f} tahun")
    sc, bc, uc = stats(eqs), stats(bhs), stats(uqs)
    return {
        "label": label,
        "mode": mode,
        "hold": hold,
        "cost_pct": cost * 100,
        "n_events": int(mask.fillna(False).sum()),
        "total_pct": (eqs[-1] - 1) * 100 if eqs else None,
        "cagr_pct": sc[0], "vol_pct": sc[1], "sharpe": sc[2], "mdd_pct": sc[3],
        "bench_total_pct": (bhs[-1] - 1) * 100 if bhs else None,
        "bench_cagr_pct": bc[0], "bench_mdd_pct": bc[3],
        "universe_total_pct": (uqs[-1] - 1) * 100 if uqs else None,
        "universe_cagr_pct": uc[0],
        "windows": windows,
        "invested": invested,
        "fill_pct": invested / max(windows, 1) * 100,
        "turnover_avg_pct": float(np.mean(turns) * 100) if turns else None,
        "cost_per_rebalance_pct": float(np.mean(turns) * cost * 100) if turns else None,
        "rebalance_per_year": ppy,
        "years": years,
        "equity": [float(x) for x in eqs],
        "bench": [float(x) for x in bhs],
        "universe": [float(x) for x in uqs],
    }


def print_signal_audit(R: pd.DataFrame, floor: float = 1e10) -> None:
    """Audit per komponen skor beli + sinyal likuid baru, semuanya di universe likuid
    point-in-time. Format ringkas: alpha pada beberapa horizon + t-stat di h=20."""
    liq = R["val20"] >= floor
    sub = R[liq]
    if sub.empty:
        print(f"\nAudit sinyal: tidak ada data likuid >= Rp{floor/1e9:.0f}M.")
        return
    print(f"\n{'=' * 104}")
    print(f"AUDIT KOMPONEN SKOR BELI & SINYAL BARU — universe likuid >= Rp{floor / 1e9:.0f}M/hari")
    print(f"   {sub['tk'].nunique()} saham | {len(sub):,} observasi | {sub['date'].nunique()} tanggal")

    comp_variants = []
    for key, lab in ai.BUY_SCORE_LABELS.items():
        col = f"bs_{key}"
        if col in R.columns:
            comp_variants.append((f"[komponen] {lab[:22]}", (R[col] > 0) & liq))
    if "bs_total" in R.columns:
        comp_variants.append(("[TOTAL] skor beli >= 70", (R["bs_total"] >= 70) & liq))
        comp_variants.append(("[TOTAL] skor beli >= 50", (R["bs_total"] >= 50) & liq))

    new_variants = []
    for key, lab in (("gap_up_any", "Gap up >=2% (tanpa volume)"),
                     ("gap_up", "Gap up >=2% + vol 1.5x"),
                     ("breakout20", "Breakout high 20 hari"),
                     ("squeeze", "Squeeze volatilitas (BB 20%)"),
                     ("squeeze_break", "Squeeze lalu breakout"),
                     ("rvol15", "Relative volume >=1.5x"),
                     ("rvol20", "Relative volume >=2x"),
                     ("rvol30", "Relative volume >=3x")):
        if key in R.columns:
            new_variants.append((lab, R[key].fillna(False) & liq))

    ref_variants = [
        ("[acuan] RS Leader", (R["leader"] == 1) & liq),
        ("[acuan] RS rekor 20h", (R["new20"] == 1) & liq),
        ("[acuan] Tren naik", (R["trend_up"] == 1) & liq),
        ("[acuan] RS > MA20", (R["above_rsma"] == 1) & liq),
    ]

    hdr = (f"   {'sinyal':<30}{'n':>8}{'/hari':>7}{'alpha5':>9}{'alpha20':>10}"
           f"{'t(20)':>8}{'abs20':>8}{'hijau20':>9}")
    for title, group in (("KOMPONEN SKOR BELI APLIKASI", comp_variants),
                         ("SINYAL LIKUID BARU", new_variants),
                         ("ACUAN (dari riset sebelumnya)", ref_variants)):
        print(f"\n{title}")
        print(hdr)
        for label, mask in group:
            rows = {r["h"]: r for r in evaluate(R, mask, label, universe_mask=liq)}
            r20 = rows.get(20)
            if not r20:
                print(f"   {label:<30}{'data kurang':>8}")
                continue
            r5 = rows.get(5)
            print(f"   {label:<30}{r20['n']:>8,}{r20['per_hari']:>7.1f}"
                  f"{(r5['alpha'] if r5 else float('nan')):>+9.2f}{r20['alpha']:>+10.2f}"
                  f"{r20['t']:>+8.2f}{r20['abs']:>+8.2f}{r20['hit_abs']:>9.1f}")


def print_tradeable(R: pd.DataFrame, floor: float = 1e10) -> None:
    """Uji sinyal HANYA pada saham yang LIKUID SAAT ITU (point-in-time, bebas look-ahead):
    pembanding dan populasi sama-sama dibatasi ke nilai transaksi >= `floor`.
    Ini ujian yang menentukan: edge RS hilang di saham likuid (lihat AMBANG LIKUIDITAS),
    jadi apakah ada sinyal lain yang bertahan?"""
    liq = R["val20"] >= floor
    sub = R[liq]
    if sub.empty:
        print(f"\nUniverse likuid >= Rp{floor/1e9:.0f}M: tidak ada data.")
        return
    print(f"\n{'=' * 104}")
    print(f"UNIVERSE TRADEABLE — nilai transaksi >= Rp{floor / 1e9:.0f} miliar/hari (point-in-time)")
    print(f"   {sub['tk'].nunique()} saham | {len(sub):,} observasi | {sub['date'].nunique()} tanggal")
    print("   Baseline universe likuid: "
          + " | ".join(f"abs{h}h {sub[f'abs{h}'].mean():+.2f}%" for h in HORIZONS))

    variants: List[Tuple[str, pd.Series]] = [
        ("RS Leader", (R["leader"] == 1) & liq),
        ("RS rekor 20h", (R["new20"] == 1) & liq),
        ("Tren naik C>SMA20>SMA50", (R["trend_up"] == 1) & liq),
        ("RS > MA20", (R["above_rsma"] == 1) & liq),
        ("Top-10 skor RS / hari", top_n_mask(R, "score", 10) & liq),
        ("Skor beli app >= 70 (BUY KUAT)", (R["buy_score"] >= 70) & liq),
        ("Skor beli app >= 50", (R["buy_score"] >= 50) & liq),
        ("Top-10 skor beli app / hari", top_n_mask(R, "buy_score", 10) & liq),
        ("Skor beli kandidat >= 70", (R["buy_score_cand"] >= 70) & liq),
        ("Skor beli kandidat >= 50", (R["buy_score_cand"] >= 50) & liq),
        ("Top-10 skor beli kandidat / hari", top_n_mask(R, "buy_score_cand", 10) & liq),
        ("Breakout 20h (sinyal likuid)", R["breakout20"].fillna(False) & liq),
        ("Breakout 20h + volume 1.5x", R["breakout20"].fillna(False) & (R["vr"] >= 1.5) & liq),
    ]
    for label, mask in variants:
        rows = evaluate(R, mask, label, universe_mask=liq)
        if not rows:
            print(f"\n{label}: data tidak cukup")
            continue
        print(f"\n{label}")
        print(f"   {'h':>3} {'n':>8} {'perhari':>8} {'abs%':>7} {'hijau%':>7} "
              f"{'exc%':>7} {'>IHSG%':>7} {'alpha%':>7} {'t-stat':>7}")
        for r in rows:
            print(f"   {r['h']:>3} {r['n']:>8,} {r['per_hari']:>8.1f} {r['abs']:>+7.2f} "
                  f"{r['hit_abs']:>7.1f} {r['exc']:>+7.2f} {r['hit_exc']:>7.1f} "
                  f"{r['alpha']:>+7.2f} {r['t']:>+7.2f}")


def print_holdout(R: pd.DataFrame, floor: float = 1e10, split: float = 0.6) -> None:
    """Uji anti-overfitting: perbaikan skor diusulkan dari audit SELURUH sampel
    (in-sample). Di sini periode dibelah waktu: 60% awal (tempat 'belajar') vs
    40% akhir (holdout). Kalau v2 hanya menang di paruh pertama, itu curve-fitting."""
    R = R.copy()
    R["_d"] = pd.to_datetime(R["date"])
    ds = np.array(sorted(R["_d"].unique()))
    cut = ds[int(len(ds) * split)]
    early = R["_d"] < cut
    late = ~early
    liq = R["val20"] >= floor
    print(f"\n{'=' * 104}")
    print(f"HOLDOUT WAKTU — 60% awal ({pd.Timestamp(ds[0]).date()}..{pd.Timestamp(ds[-1]).date()} dipotong "
          f"{pd.Timestamp(cut).date()}) vs 40% akhir, likuid >= Rp{floor/1e9:.0f}M")
    rows_v = [
        ("Skor beli app >= 70", (R["buy_score"] >= 70)),
        ("Skor beli app >= 50", (R["buy_score"] >= 50)),
        ("Top-10 skor beli app", top_n_mask(R, "buy_score", 10)),
        ("Top-10 alpha20", top_n_mask(R, "a20", 10)),
        ("Breakout 20h", R["breakout20"].fillna(False)),
        ("RS Leader", R["leader"] == 1),
    ]
    print(f"   {'varian':<24}{'alpha20 awal':>14}{'t':>7}   {'alpha20 holdout':>16}{'t':>7}{'n/hari':>8}")
    for label, mask in rows_v:
        out_cells = []
        for seg, uni_mask in ((early, early & liq), (late, late & liq)):
            m = mask & seg & liq
            got = {r["h"]: r for r in evaluate(R, m, label, universe_mask=uni_mask)}
            r20 = got.get(20)
            out_cells.append((r20["alpha"] if r20 else float("nan"),
                              r20["t"] if r20 else float("nan"),
                              r20["per_hari"] if r20 else float("nan")))
        a, b = out_cells
        print(f"   {label:<24}{a[0]:>+13.2f}%{a[1]:>+7.2f}   {b[0]:>+15.2f}%{b[1]:>+7.2f}{b[2]:>8.1f}")


def print_walk_forward(R: pd.DataFrame, variants: List[Tuple[str, pd.Series]]) -> None:
    """Pilih varian TERBAIK hanya dari tahun-tahun sebelumnya, lalu uji tahun berikutnya.
    Menguji apakah 'memilih yang historis terbaik' benar-benar berguna."""
    R = R.copy()
    R["_y"] = pd.to_datetime(R["date"]).dt.year
    uni = R.groupby("date")["exc5"].mean()
    years = sorted(R["_y"].unique())
    table: Dict[str, Dict[int, float]] = {}
    for label, mask in variants:
        sel = R.loc[mask.fillna(False)]
        per_year: Dict[int, float] = {}
        for y in years:
            g = sel[sel["_y"] == y]
            if len(g) < 200:
                continue
            d = (g.groupby("date")["exc5"].mean() - uni).dropna()
            if len(d):
                per_year[int(y)] = float(d.mean())
        table[label] = per_year

    print("\nWALK-FORWARD (pilih varian terbaik dari tahun sebelumnya, uji tahun berikutnya):")
    print(f"   {'tahun uji':<12}{'varian terpilih':<26}{'alpha h5 (out-of-sample)':>24}")
    oos: List[float] = []
    for i, y in enumerate(years):
        if i == 0:
            continue
        prior = [(lbl, tbl[y_prev]) for lbl, tbl in table.items()
                 for y_prev in [years[i - 1]] if y_prev in tbl]
        if not prior:
            continue
        best = max(prior, key=lambda t: t[1])
        val = table[best[0]].get(int(y))
        if val is None:
            continue
        oos.append(val)
        print(f"   {y:<12}{best[0][:24]:<26}{val:>+23.2f}%")
    if oos:
        pos = sum(1 for x in oos if x > 0)
        print(f"   -> rata-rata out-of-sample {np.mean(oos):+.2f}%  |  positif {pos}/{len(oos)} tahun")
    print("   (bandingkan: 'RS Leader luas' h5 dipakai membabi-buta = +1,20% rata-rata 5 tahun)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest multi-tahun sinyal kekuatan relatif vs IHSG.")
    ap.add_argument("--years", type=int, default=5, choices=[2, 3, 5])
    ap.add_argument("--universe", default="all", choices=["all", "liquid"])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--top", type=int, default=10, help="jumlah saham teratas per rebalance")
    args = ap.parse_args()

    ih, data = load_data(args.years, args.universe, args.workers)
    print(f"[2/3] Menghitung sinyal + hasil ke depan untuk {len(data)} saham…", flush=True)
    t0 = time.time()
    frames: List[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(lambda kv: build_rows(kv[0], kv[1], ih), list(data.items())):
            if r is not None:
                frames.append(r)
    R = pd.concat(frames, ignore_index=True)
    print(f"      {len(R):,} observasi saham x hari dalam {time.time() - t0:.0f}s")

    # Metadata emiten + kohort listing (mitigasi survivorship bias secara parsial)
    meta = load_meta()
    R["code"] = R["tk"].str.replace(".JK", "", regex=False)
    if len(meta):
        R = R.merge(meta, on="code", how="left")
    first_year = R.groupby("code")["date"].min().dt.year.rename("first_year")
    R = R.merge(first_year, on="code", how="left")

    # Regression test: formula skor beli di api/index.py harus IDENTIK dengan replika
    # yang divalidasi di script ini. Kalau bergeser, angka backtest tidak lagi berlaku.
    if "buy_score_cand" in R.columns and R["buy_score"].notna().any():
        diff = (R["buy_score"] - R["buy_score_cand"]).abs()
        print(f"\n[cek] skor beli API vs replika script: rata-rata selisih {diff.mean():.4f} "
              f"| maks {diff.max():.1f} | identik (<0.01) pada {100 * (diff < 0.01).mean():.1f}% bar")

    print_report(R)
    print_breakdown(R)

    top_n = args.top
    simulate_equity(R, R["leader"] == 1, "RS Leader (semua yang lolos)", top_n=top_n, rank_col=None)
    simulate_equity(R, top_n_mask(R, "score", top_n), f"Top-{top_n} skor RS (tanpa filter)", top_n=top_n)
    simulate_equity(R, top_n_mask(R, "score", top_n) & R["liq"], f"Top-{top_n} skor RS + likuid >=1M", top_n=top_n)
    simulate_equity(R, top_n_mask(R, "score", top_n) & (R["val20"] >= 5e9),
                    f"Top-{top_n} skor RS + likuid >=5M", top_n=top_n)

    variants: List[Tuple[str, pd.Series]] = [
        ("RS Leader luas", R["leader"] == 1),
        ("RS Leader + likuid", (R["leader"] == 1) & R["liq"]),
        ("RS rekor 20h", R["new20"] == 1),
        ("Hijau saat merah", R["green_on_red"] == 1),
        ("Hijau merah + vol", R["gor_vol"] == 1),
        ("Volume spike", R["volspike"] == 1),
        ("Tren naik", R["trend_up"] == 1),
        (f"Top-{top_n} skor RS", top_n_mask(R, "score", top_n)),
        (f"Top-{top_n} alpha20", top_n_mask(R, "a20", top_n)),
    ]
    print_walk_forward(R, variants)

    # --- Audit komponen skor beli + sinyal likuid baru (menentukan perbaikan skor) ---
    print_signal_audit(R, floor=1e10)

    # --- Arah yang tradeable: uji pada saham likuid point-in-time + horizon panjang ---
    print_tradeable(R, floor=5e9)
    print_tradeable(R, floor=1e10)
    simulate_equity(R, (R["new20"] == 1) & (R["val20"] >= 1e10),
                    "RS rekor 20h + likuid >=10M", top_n=top_n, hold=20, rank_col=None)
    simulate_equity(R, top_n_mask(R, "score", top_n) & (R["val20"] >= 1e10),
                    "Top-10 skor RS + likuid >=10M", top_n=top_n, hold=20, min_names=3)
    simulate_equity(R, (R["buy_score"] >= 70) & (R["val20"] >= 1e10),
                    "Skor beli app >=70 + likuid >=10M", top_n=top_n, hold=20, rank_col=None)
    simulate_equity(R, top_n_mask(R, "buy_score", top_n) & (R["val20"] >= 1e10),
                    f"Top-{top_n} skor beli app + likuid >=10M", top_n=top_n, hold=20)
    simulate_equity(R, R["breakout20"].fillna(False) & (R["val20"] >= 1e10),
                    "Breakout 20h + likuid >=10M", top_n=top_n, hold=20, rank_col=None)
    simulate_equity(R, R["breakout20"].fillna(False) & (R["vr"] >= 1.5) & (R["val20"] >= 1e10),
                    "Breakout 20h + vol 1.5x + likuid >=10M", top_n=top_n, hold=20, rank_col=None)

    # --- Anti-overfitting: uji holdout waktu untuk skor beli v1 vs v2 ---
    print_holdout(R, floor=1e10)


if __name__ == "__main__":
    main()
