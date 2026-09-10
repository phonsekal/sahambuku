"""
CoachInvestasi Strategy API
============================
Backend FastAPI yang menerjemahkan strategi dari e-book
"Technical Analysis & Bandarmology Analysis Coach Investasi 2025-2026" menjadi
logika algoritmik yang siap di-deploy ke Vercel Serverless Functions.

Fitur:
  * Data harga OHLCV real-time/historis via yfinance.
  * Indikator: SMA, EMA, RSI, MACD, ATR, Volume MA (dihitung manual, ringan).
  * Strategi buku: Support/Resistance, Dow Theory, pola candlestick,
    Golden/Death Cross, Fibonacci Retracement, The Launch Pad, Drop Base Rally,
    risk management (RRR & position sizing), dan screener Stockbit.
  * Bandarmology: analisis Broker Summary (AVG bandar, value share, skenario idaman).

Endpoint:
  GET  /api/health                  -> status API
  GET  /api/analyze/{ticker}        -> analisis lengkap + sinyal BUY/SELL/HOLD
  POST /api/bandarmology/analyze    -> analisis Broker Summary (input manual)

Menjalankan lokal:
  uvicorn api.index:app --reload
"""

from __future__ import annotations

import io
import json
import math
import os
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# 0. KONSTANTA
# ---------------------------------------------------------------------------

DISCLAIMER = (
    "Hasil analisis ini murni edukasi berdasarkan buku "
    "'Technical Analysis & Bandarmology Analysis Coach Investasi 2025-2026'. "
    "BUKAN saran beli/jual. Selalu lakukan riset mandiri (DYOR)."
)

BANDARMOLOGY_NOTE = (
    "Broker Summary (data bandarmology) dirilis ~17:00 WIB oleh bursa dan TIDAK "
    "tersedia melalui yfinance. Gunakan endpoint POST /api/bandarmology/analyze "
    "dengan data manual dari Stockbit/IDX, lalu kombinasikan dengan hasil "
    "analisis teknikal di sini (filosofi Double-Tap Coachinvestasi)."
)

CACHE: Dict[tuple, dict] = {}
CACHE_TTL_SECONDS = 5 * 60

# ---------------------------------------------------------------------------
# 1. BANTUAN UMUM
# ---------------------------------------------------------------------------


def strip_suffix(ticker: str) -> str:
    """Hapus suffix bursa (.JK) untuk tampilan."""
    t = str(ticker or "").upper()
    return t[:-3] if t.endswith(".JK") else t


def num(value: Any, nd: int = 4) -> Optional[float]:
    """Konversi aman numpy -> float (None jika NaN/inf)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


# ---------------------------------------------------------------------------
# 2. INDIKATOR TEKNIKAL (dihitung manual dengan pandas/numpy)
# ---------------------------------------------------------------------------


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder smoothing)."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    out[avg_loss == 0] = 100.0
    out[avg_gain == 0] = 0.0
    return out.clip(0.0, 100.0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD: garis MACD = EMA(fast) - EMA(slow); sinyal = EMA(signal) dari MACD."""
    line = ema(series, fast) - ema(series, slow)
    sig = ema(line, signal)
    hist = line - sig
    return line, sig, hist


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    h, l, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def find_swings(df: pd.DataFrame, k: int = 2):
    """Swing High & Swing Low (fractal: puncak/lembah lokal dengan k bar kiri-kanan)."""
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    sh, sl = [], []
    n = len(df)
    for i in range(k, n - k):
        if highs[i] == highs[i - k:i + k + 1].max() and highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            sh.append((i, float(highs[i])))
        if lows[i] == lows[i - k:i + k + 1].min() and lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            sl.append((i, float(lows[i])))
    return sh, sl


# ---------------------------------------------------------------------------
# 3. STRATEGI TEKNIKAL (Bab 1-9 Beginner, Bab 10-13 Advanced)
# ---------------------------------------------------------------------------


def detect_trend(df: pd.DataFrame, lookback: int = 120) -> dict:
    """Teori Dow (Bab 3): uptrend = HH/HL, downtrend = LH/LL, selainnya sideways."""
    n = len(df)
    lookback = min(lookback, n)
    start = n - lookback
    sh, sl = find_swings(df, k=2)
    sh = [(i, p) for i, p in sh if i >= start]
    sl = [(i, p) for i, p in sl if i >= start]

    direction, pattern = "sideways", "data tidak cukup"
    if len(sh) >= 2 and len(sl) >= 2:
        highs = [p for _, p in sh[-3:]]
        lows = [p for _, p in sl[-3:]]
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            direction, pattern = "uptrend", "HH/HL (Higher Highs & Higher Lows)"
        elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            direction, pattern = "downtrend", "LH/LL (Lower Highs & Lower Lows)"
        else:
            pattern = "mixed (konsolidasi)"

    close = df["Close"]
    last = float(close.iloc[-1])
    s20, s50 = sma(close, 20), sma(close, 50)
    if not np.isnan(s20.iloc[-1]) and not np.isnan(s50.iloc[-1]):
        if last > s20.iloc[-1] > s50.iloc[-1]:
            ma_align = "bullish (Harga > SMA20 > SMA50)"
        elif last < s20.iloc[-1] < s50.iloc[-1]:
            ma_align = "bearish (Harga < SMA20 < SMA50)"
        else:
            ma_align = "mixed"
    else:
        ma_align = "unknown"

    return {
        "direction": direction,
        "dow_pattern": pattern,
        "ma_alignment": ma_align,
        "last_swing_highs": [num(p, 2) for _, p in sh[-3:]],
        "last_swing_lows": [num(p, 2) for _, p in sl[-3:]],
        "description": (
            "Tren naik: beli saat koreksi ke support/MA. Tren turun: hindari beli, "
            "cari distribusi. Sideways: tunggu breakout dengan volume."
        ),
    }


def find_sr_zones(df: pd.DataFrame, lookback: int = 120, tol_pct: float = 0.015) -> List[dict]:
    """Level Support & Resistance (Bab 1): cluster swing high/low menjadi zona harga."""
    n = len(df)
    start = max(0, n - lookback)
    sh, sl = find_swings(df, k=2)
    points = [{"idx": i, "price": p, "type": "resistance"} for i, p in sh if i >= start]
    points += [{"idx": i, "price": p, "type": "support"} for i, p in sl if i >= start]
    if not points:
        return []

    pts = sorted(points, key=lambda x: x["price"])
    groups, cur = [], [pts[0]]
    for pt in pts[1:]:
        if abs(pt["price"] - cur[0]["price"]) / cur[0]["price"] <= tol_pct:
            cur.append(pt)
        else:
            groups.append(cur)
            cur = [pt]
    groups.append(cur)

    out = []
    for g in groups:
        prices = [p["price"] for p in g]
        center = sum(prices) / len(prices)
        types = [p["type"] for p in g]
        typ = "support" if types.count("support") >= types.count("resistance") else "resistance"
        touches = len(g)
        recency = sum(1 for p in g if p["idx"] >= n - 20)
        out.append({
            "price": num(center, 2),
            "type": typ,
            "touches": touches,
            "strength": num(touches + 0.5 * recency, 2),
            "band": [num(min(prices), 2), num(max(prices), 2)],
        })

    strong = [z for z in out if z["touches"] >= 2]
    strong.sort(key=lambda z: -z["strength"])
    if not strong:
        strong = sorted(out, key=lambda z: -z["strength"])[:3]
    return strong[:6]


def volume_sr_levels(df: pd.DataFrame, lookback: int = 250, top_n: int = 3) -> List[dict]:
    """Strategi Volume untuk S&R (Bab 11): candle volume tertinggi -> Support/Resistance area."""
    n = len(df)
    start = max(0, n - lookback)
    sub = df.iloc[start:]
    top = sub.nlargest(top_n, "Volume")
    levels = []
    for idx, row in top.iterrows():
        levels.append({
            "date": str(idx.date()) if hasattr(idx, "date") else str(idx),
            "volume": num(row["Volume"], 0),
            "support": num(row["Low"], 2),
            "resistance": num(row["High"], 2),
            "note": "High candle = Resistance, Low candle = Support (area, bukan garis)",
        })
    return levels


def detect_candlestick_patterns(df: pd.DataFrame) -> dict:
    """Pola candlestick (Bab 4): Doji, Hammer, Shooting Star, Engulfing, Harami, Star, Soldiers."""
    o = df["Open"].to_numpy(dtype=float)
    h = df["High"].to_numpy(dtype=float)
    l = df["Low"].to_numpy(dtype=float)
    c = df["Close"].to_numpy(dtype=float)
    n = len(df)

    def body(i):
        return abs(c[i] - o[i])

    def rng(i):
        return h[i] - l[i]

    def green(i):
        return c[i] > o[i]

    def red(i):
        return c[i] < o[i]

    def small(i, frac=0.3):
        r = rng(i)
        return r > 0 and body(i) <= frac * r

    patterns = []
    i = n - 1

    # --- Single candle ---
    if rng(i) > 0:
        r = rng(i)
        b = body(i)
        upper = h[i] - max(o[i], c[i])
        lower = min(o[i], c[i]) - l[i]
        if b <= 0.1 * r:
            patterns.append({"pattern": "Doji", "type": "neutral",
                             "note": "Keraguan (indecision); sinyal pembalikan jika muncul di akhir tren."})
        if lower >= 2.0 * b and upper <= 0.35 * b:
            patterns.append({"pattern": "Hammer", "type": "bullish",
                             "note": "Bullish reversal; ideal di area support."})
        if upper >= 2.0 * b and lower <= 0.35 * b:
            patterns.append({"pattern": "Shooting Star", "type": "bearish",
                             "note": "Bearish reversal; ideal di area resistance."})

    # --- Two candles ---
    if n >= 2:
        j = i - 1
        if red(j) and green(i) and body(i) > body(j) > 0:
            patterns.append({"pattern": "Bullish Engulfing", "type": "bullish",
                             "note": "Tekanan jual dikalahkan pembeli massif; kuat di support."})
        if green(j) and red(i) and body(i) > body(j) > 0:
            patterns.append({"pattern": "Bearish Engulfing", "type": "bearish",
                             "note": "Tekanan beli dikalahkan penjual massif; kuat di resistance."})
        if red(j) and green(i) and body(j) > body(i) > 0 and small(i):
            patterns.append({"pattern": "Bullish Harami", "type": "bullish",
                             "note": "Momentum jual melambat; peringatan pembalikan."})
        if green(j) and red(i) and body(j) > body(i) > 0 and small(i):
            patterns.append({"pattern": "Bearish Harami", "type": "bearish",
                             "note": "Momentum beli melambat; peringatan pembalikan."})

    # --- Three candles ---
    if n >= 3:
        a, b_, d = i - 2, i - 1, i
        big_a = rng(a) > 0 and body(a) >= 0.6 * rng(a)
        if red(a) and big_a and small(b_) and green(d) and c[d] > o[a]:
            patterns.append({"pattern": "Morning Star", "type": "bullish",
                             "note": "Bullish reversal sangat kuat (downtrend -> pembeli kendali)."})
        if green(a) and big_a and small(b_) and red(d) and c[d] < o[a]:
            patterns.append({"pattern": "Evening Star", "type": "bearish",
                             "note": "Bearish reversal sangat kuat (uptrend -> penjual kendali)."})
        if all(green(x) for x in (a, b_, d)) and c[b_] > c[a] and c[d] > c[b_] and \
                all(rng(x) > 0 and body(x) >= 0.5 * rng(x) for x in (a, b_, d)):
            patterns.append({"pattern": "Three White Soldiers", "type": "bullish",
                             "note": "Pembeli agresif bertahap; konfirmasi tren naik baru."})

    last = df.iloc[-1]
    return {
        "last_candle": {
            "date": str(last.name.date()) if hasattr(last.name, "date") else str(last.name),
            "open": num(last["Open"], 2), "high": num(last["High"], 2),
            "low": num(last["Low"], 2), "close": num(last["Close"], 2),
            "change_pct": num((float(last["Close"]) / float(df["Close"].iloc[-2]) - 1) * 100, 2) if len(df) >= 2 else None,
            "color": "green (bullish)" if last["Close"] > last["Open"] else "red (bearish)",
        },
        "patterns": patterns,
    }


def fibonacci_levels(df: pd.DataFrame, lookback: int = 120) -> dict:
    """Fibonacci Retracement (Bab 12): level 23.6/38.2/50/61.8/78.6 + target eksternal 1.272/1.618."""
    n = len(df)
    start = max(0, n - lookback)
    sh, sl = find_swings(df, k=2)
    sh_in = [p for i, p in sh if i >= start]
    sl_in = [p for i, p in sl if i >= start]
    if not sh_in or not sl_in:
        return {"note": "Swing points tidak cukup untuk menghitung Fibonacci."}

    a = min(sl_in)
    b = max(sh_in)
    direction = "bullish" if b > a else "bearish"
    span = b - a
    if span <= 0:
        return {"note": "Range harga nol; Fibonacci tidak dapat dihitung."}

    ratios = [0.236, 0.382, 0.5, 0.618, 0.786]
    levels = []
    for r in ratios:
        price = b - r * span if direction == "bullish" else a + r * span
        levels.append({"ratio": r, "price": num(price, 2)})

    ext = []
    for r in (1.272, 1.618):
        price = b + (r - 1.0) * span if direction == "bullish" else a - (r - 1.0) * span
        ext.append({"ratio": r, "price": num(price, 2)})

    last = float(df["Close"].iloc[-1])
    nearest = min(levels, key=lambda lv: abs(lv["price"] - last))
    below = [lv for lv in levels if lv["price"] <= last]
    above = [lv for lv in levels if lv["price"] >= last]

    return {
        "direction": direction,
        "swing_low": num(a, 2),
        "swing_high": num(b, 2),
        "levels": levels,
        "external_targets": ext,
        "nearest_level": {
            "ratio": nearest["ratio"],
            "price": nearest["price"],
            "distance_pct": num(abs(nearest["price"] - last) / last * 100, 2) if last else None,
        },
        "nearest_fib_support": num(max(below, key=lambda lv: lv["price"])["price"], 2) if below else None,
        "nearest_fib_resistance": num(min(above, key=lambda lv: lv["price"])["price"], 2) if above else None,
        "target_note": ("Untuk saham Indonesia, level 1.272 adalah target ideal (tips Coach)."
                        if direction == "bullish" else "Target eksternal untuk bearish."),
    }


def rsi_divergence(df: pd.DataFrame, period: int = 14, lookback: int = 60) -> dict:
    """Divergence RSI (Bab 10.2): harga HH tetapi RSI LH (bearish), atau LL tetapi RSI HL (bullish)."""
    r = rsi(df["Close"], period).to_numpy(dtype=float)
    close = df["Close"].to_numpy(dtype=float)
    n = len(df)
    start = max(0, n - lookback)

    sl, sh = [], []
    for i in range(2, n - 2):
        if close[i] == close[i - 2:i + 3].min() and close[i] < close[i - 1] and close[i] < close[i + 1]:
            sl.append((i, float(close[i]), float(r[i])))
        if close[i] == close[i - 2:i + 3].max() and close[i] > close[i - 1] and close[i] > close[i + 1]:
            sh.append((i, float(close[i]), float(r[i])))
    sl = [x for x in sl if x[0] >= start][-2:]
    sh = [x for x in sh if x[0] >= start][-2:]

    result = {"bullish": False, "bearish": False, "description": None}
    if len(sl) >= 2:
        _, p1, r1 = sl[0]
        _, p2, r2 = sl[1]
        if p2 < p1 and r2 > r1:
            result.update(bullish=True,
                          description="Bullish Divergence: harga membuat Lower Low tapi RSI Higher Low (potensi pantulan naik).")
    if len(sh) >= 2:
        _, p1, r1 = sh[0]
        _, p2, r2 = sh[1]
        if p2 > p1 and r2 < r1:
            result.update(bearish=True,
                          description="Bearish Divergence: harga membuat Higher High tapi RSI Lower High (potensi koreksi turun).")
    return result


def cross_events(df: pd.DataFrame, fast_p: int = 20, slow_p: int = 50, window: int = 5) -> dict:
    """Golden Cross / Death Cross SMA cepat vs lambat (Bab 5.3 & 10.1)."""
    diff = sma(df["Close"], fast_p) - sma(df["Close"], slow_p)
    last = len(df) - 1
    golden = death = None
    for i in range(last, max(last - window, 0), -1):
        if np.isnan(diff.iloc[i]) or (i > 0 and np.isnan(diff.iloc[i - 1])):
            continue
        if diff.iloc[i - 1] <= 0 < diff.iloc[i]:
            golden = last - i
            break
        if diff.iloc[i - 1] >= 0 > diff.iloc[i]:
            death = last - i
            break
    return {"golden_cross_days_ago": golden, "death_cross_days_ago": death}


def volume_analysis(df: pd.DataFrame, mult: float = 1.5) -> dict:
    """Analisis volume (Bab 11 & screener): rasio volume terakhir vs MA20."""
    v = df["Volume"]
    vma = sma(v, 20)
    last_v = float(v.iloc[-1])
    last_vma = float(vma.iloc[-1]) if not np.isnan(vma.iloc[-1]) else last_v
    ratio = last_v / last_vma if last_vma > 0 else 0.0
    return {
        "last_volume": num(last_v, 0),
        "volume_ma20": num(last_vma, 0),
        "ratio_to_ma20": num(ratio, 2),
        "spike": bool(ratio >= mult),
        "note": ("Volume >= 2x MA20 = konfirmasi tenaga beli besar (kriteria 'Beli Sore Jual Pagi')."
                 if ratio >= 2 else "Volume belum eksplosif (belum ada konfirmasi breakout)."),
    }


def launch_pad(df: pd.DataFrame) -> dict:
    """Special Pattern 1 (Bab 6.2): uptrend -> konsolidasi menyempit -> breakout volume tinggi."""
    n = len(df)
    if n < 45:
        return {"detected": False, "phase": "insufficient_data", "description": "Data historis kurang dari 45 bar."}

    close = df["Close"]
    cons_start = n - 15
    base = df.iloc[cons_start:]
    prev_win = df.iloc[cons_start - 15:cons_start]

    prior = close.iloc[cons_start - 1]
    prior_start = close.iloc[cons_start - 20]
    prior_gain = (prior / prior_start - 1) * 100 if prior_start > 0 else 0.0

    base_range = float(base["High"].max() - base["Low"].min())
    prev_range = float(prev_win["High"].max() - prev_win["Low"].min())
    contraction = base_range / prev_range if prev_range > 0 else 1.0

    base_high_excl_last = float(base.iloc[:-1]["High"].max())
    last_close = float(close.iloc[-1])
    vma = float(sma(df["Volume"], 20).iloc[-1])
    vol_ratio = float(df["Volume"].iloc[-1]) / vma if vma and vma > 0 else 0.0
    broke = last_close > base_high_excl_last

    if prior_gain >= 15 and contraction <= 0.8 and broke and vol_ratio >= 1.5:
        phase, detected = "breakout", True
    elif prior_gain >= 15 and contraction <= 0.85:
        phase, detected = "forming", False
    else:
        phase, detected = "none", False

    return {
        "detected": detected,
        "phase": phase,
        "prior_gain_pct": num(prior_gain, 2),
        "contraction_ratio": num(contraction, 2),
        "breakout_price": num(base_high_excl_last, 2),
        "volume_ratio": num(vol_ratio, 2),
        "description": (
            "The Launch Pad terkonfirmasi: uptrend sebelumnya, rentang harga menyempit "
            "(energi terkunci), dan breakout dengan volume signifikan. Cut-loss jika "
            "harga close di bawah area base/support terdekat." if detected else
            ("Base The Launch Pad sedang terbentuk (tunggu breakout + volume serta cek Broker Summary)."
             if phase == "forming" else "Tidak terdeteksi pola The Launch Pad.")
        ),
    }


def drop_base_rally(df: pd.DataFrame) -> dict:
    """Special Pattern 2 (Bab 6.3): turun dalam -> base di support -> volume naik -> rally."""
    n = len(df)
    if n < 35:
        return {"detected": False, "phase": "insufficient_data", "description": "Data historis kurang dari 35 bar."}

    close = df["Close"]
    base_start = n - 10
    base = df.iloc[base_start:]

    prior = close.iloc[base_start - 1]
    prior_start = close.iloc[base_start - 15]
    prior_drop = (prior / prior_start - 1) * 100 if prior_start > 0 else 0.0

    base_low = float(base["Low"].min())
    prior_low = float(df["Low"].iloc[base_start - 15:base_start].min())
    no_new_ll = bool(base_low >= prior_low * 0.995)

    base_high_excl_last = float(base.iloc[:-1]["High"].max())
    last_close = float(close.iloc[-1])
    vma = float(sma(df["Volume"], 20).iloc[-1])
    vol_ratio = float(df["Volume"].iloc[-1]) / vma if vma and vma > 0 else 0.0
    rallied = bool(last_close > base_high_excl_last)

    detected = bool(prior_drop <= -10 and no_new_ll and rallied and vol_ratio >= 1.5)

    return {
        "detected": detected,
        "prior_drop_pct": num(prior_drop, 2),
        "no_lower_low": no_new_ll,
        "breakout_price": num(base_high_excl_last, 2),
        "volume_ratio": num(vol_ratio, 2),
        "description": (
            "Drop Base Rally terkonfirmasi: penurunan dalam tanpa Lower Low, base di area "
            "support, volume meningkat sebelum rally. Periksa siapa Top Buyer-nya (money flow)."
            if detected else
            "Tidak terdeteksi pola Drop Base Rally (syarat: drop tanpa lower low + base + volume naik)."
        ),
    }


def screener_hints(df: pd.DataFrame, ticker: str) -> dict:
    """Kriteria preset screener Stockbit Coachinvestasi (halaman awal buku)."""
    close, vol = df["Close"], df["Volume"]
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(df) >= 2 else last
    day_ret = (last / prev - 1) * 100 if prev > 0 else 0.0
    est_value = last * float(vol.iloc[-1])
    vma = float(sma(vol, 20).iloc[-1]) if len(df) >= 20 else 0.0
    vol_ratio = float(vol.iloc[-1]) / vma if vma > 0 else 0.0
    is_idr = ".JK" in ticker.upper()

    checks = {
        "beli_sore_jual_pagi": {
            "value_ge_5b": bool(est_value >= 5e9),
            "day_return_ge_8pct": bool(day_ret >= 8.0),
            "volume_ge_2x_ma20": bool(vol_ratio >= 2.0),
            "eligible": bool(est_value >= 5e9 and day_ret >= 8.0 and vol_ratio >= 2.0) if is_idr else None,
        },
        "scalping": {
            "value_ge_1b": bool(est_value >= 1e9),
            "day_return_ge_10pct": bool(day_ret >= 10.0),
            "price_ge_50": bool(last >= 50),
            "eligible": bool(est_value >= 1e9 and day_ret >= 10.0 and last >= 50) if is_idr else None,
        },
    }
    return {
        "day_return_pct": num(day_ret, 2),
        "estimated_value_idr": num(est_value, 0),
        "volume_ratio_to_ma20": num(vol_ratio, 2),
        "checks": checks,
        "note": "Estimasi Value = Close x Volume (kriteria hanya bermakna untuk saham IDX / .JK).",
    }


def compute_signal(df: pd.DataFrame, trend: dict, sr_zones: List[dict], candles: dict,
                   fib: dict, div: dict, cross: dict, vol: dict, lp: dict, dbr: dict) -> dict:
    """Sinyal gabungan BUY/SELL/HOLD (Bab 9 integrasi + strategi advanced)."""
    score = 0.0
    reasons: List[str] = []
    close = df["Close"]
    n = len(df)
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])

    s20 = sma(close, 20).iloc[-1]
    s50 = sma(close, 50).iloc[-1]
    r = float(rsi(close, 14).iloc[-1])
    r_prev = float(rsi(close, 14).iloc[-2])
    macd_line, sig_line, hist = macd(close)
    m, s = float(macd_line.iloc[-1]), float(sig_line.iloc[-1])
    diff_arr = (macd_line - sig_line).to_numpy(dtype=float)

    # --- MA / trend ---
    if not np.isnan(s20) and not np.isnan(s50):
        if last > s20 > s50:
            score += 1.5
            reasons.append("Harga > SMA20 > SMA50 (tren naik MA)")
        elif last < s20 < s50:
            score -= 1.5
            reasons.append("Harga < SMA20 < SMA50 (tren turun MA)")
    if cross["golden_cross_days_ago"] is not None:
        score += 2
        reasons.append(f"Golden Cross SMA20/SMA50 terjadi {cross['golden_cross_days_ago']} hari lalu")
    if cross["death_cross_days_ago"] is not None:
        score -= 2
        reasons.append(f"Death Cross SMA20/SMA50 terjadi {cross['death_cross_days_ago']} hari lalu")

    # --- RSI ---
    if r < 30:
        score += 1.5
        reasons.append(f"RSI {r:.1f} < 30 (oversold, cari pantulan)")
    elif r > 70:
        score -= 1.5
        reasons.append(f"RSI {r:.1f} > 70 (overbought, waspada koreksi)")
    if r_prev <= 30 < r:
        score += 2
        reasons.append("RSI menembus kembali di atas 30 (sinyal beli)")
    if r_prev >= 70 > r:
        score -= 2
        reasons.append("RSI turun menembus 70 (sinyal jual)")
    if div.get("bullish"):
        score += 2
        reasons.append(div["description"])
    if div.get("bearish"):
        score -= 2
        reasons.append(div["description"])

    # --- MACD ---
    if m > s:
        score += 1
        reasons.append("MACD di atas garis sinyal")
    else:
        score -= 1
        reasons.append("MACD di bawah garis sinyal")
    for i in range(n - 3, n):
        if diff_arr[i - 1] <= 0 < diff_arr[i]:
            score += 2
            reasons.append("MACD memotong garis sinyal ke atas (sinyal beli)")
            break
        if diff_arr[i - 1] >= 0 > diff_arr[i]:
            score -= 2
            reasons.append("MACD memotong garis sinyal ke bawah (sinyal jual)")
            break

    # --- Candlestick di area S&R ---
    near_support = any(abs(last - z["price"]) / last <= 0.02 for z in sr_zones if z["type"] == "support")
    near_resistance = any(abs(last - z["price"]) / last <= 0.02 for z in sr_zones if z["type"] == "resistance")
    bullish_pat = any(p["type"] == "bullish" for p in candles["patterns"])
    bearish_pat = any(p["type"] == "bearish" for p in candles["patterns"])
    if bullish_pat and near_support:
        score += 2
        reasons.append("Pola candlestick bullish reversal di area support (konfirmasi Bab 9.1)")
    if bearish_pat and near_resistance:
        score -= 2
        reasons.append("Pola candlestick bearish reversal di area resistance")

    # --- Volume ---
    if vol["spike"]:
        if last >= prev:
            score += 1
            reasons.append("Volume spike mengonfirmasi kenaikan harga")
        else:
            score -= 1
            reasons.append("Volume spike pada hari turun (waspada distribusi)")

    # --- Fibonacci confluence ---
    fib_sup = fib.get("nearest_fib_support")
    if fib_sup and trend["direction"] == "uptrend" and abs(last - fib_sup) / last <= 0.02:
        score += 1
        reasons.append("Harga di area retracement Fibonacci (support) dalam uptrend")

    # --- Trend alignment (Dow Theory) ---
    if trend["direction"] == "uptrend":
        score += 1
        reasons.append("Tren naik (Dow Theory: HH/HL)")
    elif trend["direction"] == "downtrend":
        score -= 1
        reasons.append("Tren turun (Dow Theory: LH/LL)")

    # --- Special patterns ---
    if lp.get("phase") == "breakout":
        score += 3
        reasons.append("Breakout The Launch Pad dengan volume (energi terkunci terlepas)")
    if dbr.get("detected"):
        score += 2
        reasons.append("Pola Drop Base Rally terkonfirmasi (base di support + volume)")

    if score >= 6:
        action = "STRONG BUY"
    elif score >= 3:
        action = "BUY"
    elif score <= -6:
        action = "STRONG SELL"
    elif score <= -3:
        action = "SELL"
    else:
        action = "HOLD"

    strength = "kuat" if abs(score) >= 6 else ("sedang" if abs(score) >= 3 else "lemah")
    return {
        "action": action,
        "score": round(score, 2),
        "strength": strength,
        "reasons": reasons,
        "rule": ("Skor >= 3 -> BUY, skor >= 6 -> STRONG BUY; skor <= -3 -> SELL, <= -6 -> STRONG SELL."
                 "Kombinasi MA + RSI + MACD + konfirmasi candlestick/volume sesuai buku."),
    }


def risk_management(last_price: float, sr_zones: List[dict], action: str, atr_value: float,
                    risk_amount: float = 5_000_000.0) -> dict:
    """Risk management (Bab 8): SL di luar S&R, TP di S&R berikutnya, RRR min 1:2, position sizing."""
    atr_v = atr_value if atr_value and atr_value > 0 else last_price * 0.02
    supports = sorted([z["price"] for z in sr_zones if z["type"] == "support" and z["price"] < last_price], reverse=True)
    resistances = sorted([z["price"] for z in sr_zones if z["type"] == "resistance" and z["price"] > last_price])

    if action in ("SELL", "STRONG SELL"):
        entry = last_price
        sl = resistances[0] + 0.3 * atr_v if resistances else entry + 2 * atr_v
        tp = supports[0] - 0.3 * atr_v if supports else entry - 2 * atr_v
        direction = "short"
    else:
        entry = last_price
        sl = supports[0] - 0.3 * atr_v if supports else entry - 2 * atr_v
        tp = resistances[0] + 0.3 * atr_v if resistances else entry + 2 * atr_v
        direction = "long"

    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rrr = reward / risk if risk > 0 else None
    pct_risk = risk / entry if entry > 0 else None
    max_position = risk_amount / pct_risk if pct_risk else None

    return {
        "direction": direction,
        "entry": num(entry, 2),
        "stop_loss": num(sl, 2),
        "take_profit": num(tp, 2),
        "risk_reward_ratio": num(rrr, 2),
        "max_position_idr": num(max_position, 0),
        "position_sizing_note": (
            "Maks Risk per Trade / %SL = Maks Posisi (contoh buku: Rp5.000.000 / 20% = Rp25.000.000). "
            f"Di sini dihitung dengan contoh risk Rp{risk_amount:,.0f}."
        ),
        "rrr_note": "RRR minimal 1:2 (jika < 2, trade sebaiknya dilewati).",
    }


# ---------------------------------------------------------------------------
# 4. BANDARMOLOGY (Bab 4 & 7: Broker Summary)
# ---------------------------------------------------------------------------

STRONG_FOREIGN = {"AK", "BK", "ZP", "YU"}
WEAK_FOREIGN = {"KK", "CP", "YP"}
STRONG_LOCAL = {"BB", "MG", "KI", "IF"}
WEAK_LOCAL = {"XL", "XC", "YP", "PD"}
STRONG_BUMN = {"CC"}
RETAIL_BROKERS = {"XL", "XC", "YP", "PD", "KK", "CP", "NI"}


def classify_broker(code: str) -> str:
    """Klasifikasi kekuatan broker per buku: kuat / lemah / netral."""
    code = (code or "").strip().upper()
    if code in STRONG_FOREIGN or code in STRONG_LOCAL or code in STRONG_BUMN:
        return "kuat"
    if code in WEAK_FOREIGN or code in WEAK_LOCAL or code in RETAIL_BROKERS:
        return "lemah"
    return "netral"


def _row_value(row: dict) -> float:
    value = row.get("value")
    if value:
        return float(value)
    return float(row.get("volume", 0)) * float(row.get("avg_price", 0))


def analyze_broker_summary(payload: dict) -> dict:
    """
    Analisis Broker Summary sesuai buku:
      - Status ACC/DIS dari net value.
      - AVG (harga rata-rata tertimbang) broker bandar (Bab 4.1).
      - Value share top buyer vs total (Bab 7: >= 60% = sangat signifikan).
      - Skenario idaman: broker kuat akumulasi, mayoritas penjual adalah ritel/lemah.
    """
    buyers = payload.get("buyers", [])
    sellers = payload.get("sellers", [])
    last_price = payload.get("last_price")

    tot_buy = sum(_row_value(b) for b in buyers)
    tot_sell = sum(_row_value(b) for b in sellers)
    net_value = tot_buy - tot_sell
    if net_value > 0:
        status = "ACC (Akumulasi)"
    elif net_value < 0:
        status = "DIS (Distribusi)"
    else:
        status = "Netral"

    top_buyers = sorted(buyers, key=_row_value, reverse=True)[:5]
    total_vol = sum(float(b.get("volume", 0)) for b in top_buyers)
    total_val = sum(_row_value(b) for b in top_buyers)
    avg_price = total_val / total_vol if total_vol > 0 else None

    top1_value = _row_value(top_buyers[0]) if top_buyers else 0.0
    share = top1_value / tot_buy * 100 if tot_buy > 0 else 0.0

    strong_buyers = [b for b in buyers if classify_broker(b.get("broker", "")) == "kuat"]
    weak_buyers = [b for b in buyers if classify_broker(b.get("broker", "")) == "lemah"]
    strong_sellers = [b for b in sellers if classify_broker(b.get("broker", "")) == "kuat"]
    weak_sellers = [b for b in sellers if classify_broker(b.get("broker", "")) == "lemah"]

    idaman = (
        status.startswith("ACC") and len(strong_buyers) > 0
        and len(weak_sellers) > 0 and len(weak_sellers) >= len(strong_sellers)
    )
    share_significant = share >= 60.0

    if idaman:
        scenario = "Skenario Idaman: Big Accumulation (bandar/institusi menampung, ritel menjual)"
    elif status.startswith("ACC"):
        scenario = "Akumulasi terdeteksi, namun belum skenario idaman (periksa siapa buyer & seller)."
    elif status.startswith("DIS"):
        scenario = "Distribusi terdeteksi: waspadai penurunan harga (harga mendekati AVG distributor = resistance psikologis)."
    else:
        scenario = "Netral: tidak ada tekanan beli/jual dominan."

    distance_avg = None
    if avg_price and last_price:
        distance_avg = (float(last_price) / avg_price - 1) * 100

    return {
        "status": status,
        "net_value": num(net_value, 0),
        "tot_buy_value": num(tot_buy, 0),
        "tot_sell_value": num(tot_sell, 0),
        "top_buyers": [
            {"broker": b.get("broker"), "volume": num(b.get("volume"), 0),
             "avg_price": num(b.get("avg_price"), 2), "value": num(_row_value(b), 0),
             "kekuatan": classify_broker(b.get("broker", ""))}
            for b in top_buyers
        ],
        "top_sellers": [
            {"broker": b.get("broker"), "volume": num(b.get("volume"), 0),
             "avg_price": num(b.get("avg_price"), 2), "value": num(_row_value(b), 0),
             "kekuatan": classify_broker(b.get("broker", ""))}
            for b in sorted(sellers, key=_row_value, reverse=True)[:5]
        ],
        "bandar_avg_price": num(avg_price, 2),
        "bandar_avg_distance_pct": num(distance_avg, 2),
        "top_buyer_value_share_pct": num(share, 2),
        "value_share_significant": share_significant,
        "scenario": scenario,
        "interpretation": (
            "AVG bandar dekat harga saat ini = risiko kecil (bandar belum untung banyak), "
            "boleh mengikuti AVG bandar." if distance_avg is not None and abs(distance_avg) < 3 else
            "AVG bandar sudah jauh dari harga saat ini; pertimbangkan risiko koreksi."
        ),
        "rule": (
            "Cek: siapa yang akumulasi/distribusi, AVG beli bandar, kombinasi dengan analisis "
            "teknikal (price action, volume, chart pattern)."
        ),
    }


# ---------------------------------------------------------------------------
# 5. DATA FETCH (yfinance + cache)
# ---------------------------------------------------------------------------


def fetch_data(ticker: str, period: str) -> pd.DataFrame:
    """Unduh OHLCV dengan fallback berantai:
    1) yfinance (real-time, dibatasi timeout) -> 2) IDX Edge PRO -> 3) dataset GitHub IDX.
    Kode tanpa titik (mis. TLKM) otomatis dicoba dengan suffix .JK bila gagal.
    Sumber akhir dicatat di df.attrs['source'].
    """
    key = (ticker.upper(), period)
    now = time.time()
    hit = CACHE.get(key)
    if hit and now - hit["ts"] < CACHE_TTL_SECONDS:
        return hit["df"]

    def _attempt(tk: str):
        """Coba satu varian ticker; kembalikan (df, source, yf_err)."""
        yf_err = None
        try:
            df = _call_with_timeout(
                lambda: yf.download(tk, period=period, interval="1d",
                                    auto_adjust=True, progress=False, threads=False),
                YFINANCE_TIMEOUT,
            )
            if df is None:
                yf_err = "yfinance timeout / tidak ada data"
        except Exception as exc:
            df = None
            yf_err = str(exc)

        source = "yfinance"
        if df is None or df.empty:
            if IDX_EDGE_API_KEYS:
                df = fetch_idx_history(tk)
                if df is not None:
                    source = "idx-edge-pro" + (" (yfinance gagal)" if yf_err else "")
            if df is None or df.empty:
                fallback = _download_github_csv(tk)
                if fallback is not None:
                    df = fallback
                    source = "github-dataset" + (" (yfinance gagal)" if yf_err else "")
        return df, source, yf_err

    # Kode pendek tanpa titik (mis. BBCA/TLKM): prioritas varian .JK bursa IDX,
    # lalu kode asli (mis. AAPL). Menghindari salah instrumen dengan nama sama di bursa lain.
    if "." not in ticker.upper() and ticker.upper().isalnum() and len(ticker) <= 5:
        df, source, yf_err = _attempt(ticker.upper() + ".JK")
        if df is None or df.empty:
            df, source, yf_err = _attempt(ticker)
    else:
        df, source, yf_err = _attempt(ticker)

    if df is None or df.empty:
        if yf_err and ("rate" in yf_err.lower() or "too many" in yf_err.lower()):
            raise HTTPException(
                429,
                detail=f"yfinance sedang rate-limited untuk {ticker} dan fallback data tidak tersedia. "
                       "Coba lagi beberapa saat kemudian.",
            )
        raise HTTPException(404, detail=(
            f"Data tidak ditemukan untuk ticker '{ticker}'. Periksa kode saham "
            "(mis. BBCA.JK, TLKM.JK, AAPL, TSLA)."
        ))

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if "Close" in df.columns:
        df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).tail(500)
    if len(df) < 30:
        raise HTTPException(422, detail="Data historis terlalu sedikit untuk analisis (min 30 bar).")
    df.attrs["source"] = source
    CACHE[key] = {"ts": now, "df": df}
    return df


# ---------------------------------------------------------------------------
# 6. SCREENER SELURUH SAHAM IDX + INTEGRASI BROKER SUMMARY API
# ---------------------------------------------------------------------------

IDX_TICKER_CSV_URL = (
    "https://raw.githubusercontent.com/wildangunawan/Dataset-Saham-IDX/"
    "master/List%20Emiten/all.csv"
)
IDX_HIST_CSV_BASE = (
    "https://raw.githubusercontent.com/wildangunawan/Dataset-Saham-IDX/"
    "master/Saham/Semua"
)
GITHUB_DATASET_NOTE = (
    "Sumber data: dataset publik IDX (wildangunawan/Dataset-Saham-IDX, 2019-2025), "
    "bukan real-time. Harga mungkin belum disesuaikan aksi korporasi (split/dividen)."
)

# Universe "liquid": konstituen LQ45 (dipakai sebagai fallback & mode cepat)
IDX_LIQUID_TICKERS = [
    "ACES", "ADRO", "AKRA", "AMRT", "ANTM", "ARTO", "ASII", "BBCA", "BBNI", "BBRI",
    "BBTN", "BMRI", "BRPT", "CPIN", "CTRA", "ESSA", "EXCL", "GGRM", "HRUM", "ICBP",
    "INCO", "INDF", "INKP", "ISAT", "ITMG", "JPFA", "JSMR", "KLBF", "MAPI", "MDKA",
    "MEDC", "PGAS", "PTBA", "SIDO", "SMGR", "TLKM", "TOWR", "UNTR", "UNVR", "BRIS",
    "MAPA", "GOTO", "PGEO", "MBMA", "AMMN",
]

# --- IDX Edge PRO API (Broker Summary & data real-time IDX) ---
# Aktif jika IDX_EDGE_API_KEYS di-set (pisahkan beberapa key dengan koma;
# key dirotasi otomatis untuk membagi kuota harian ~1000 req/key).
IDX_EDGE_API_URL = os.environ.get("IDX_EDGE_API_URL", "https://stock.arjum.com").strip().rstrip("/")
IDX_EDGE_API_KEYS = [k.strip() for k in os.environ.get("IDX_EDGE_API_KEYS", "").split(",") if k.strip()]
_IDX_EDGE_KEY_IDX = 0

IDX_EDGE_CACHE: Dict[str, dict] = {}
IDX_EDGE_HIST_TTL = 6 * 3600        # data OHLCV dicache 6 jam
IDX_EDGE_BROKER_TTL = 12 * 3600     # broker summary/akumulasi dicache 12 jam (data harian)

# ---------------------------------------------------------------------------
# 5c. SINKRONISASI ANTAR PERANGKAT (Upstash Redis REST — tanpa SDK)
# ---------------------------------------------------------------------------
UPSTASH_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
UPSTASH_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()
SYNC_ENABLED = bool(UPSTASH_REST_URL and UPSTASH_REST_TOKEN)

# Notifikasi TP/SL via Telegram (opsional; set TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
CRON_SECRET = os.environ.get("CRON_SECRET", "").strip()


def _upstash_get(key: str) -> Optional[str]:
    """GET nilai string dari Upstash Redis REST. None bila belum ada/gagal."""
    if not SYNC_ENABLED:
        return None
    try:
        req = urllib.request.Request(
            UPSTASH_REST_URL,
            data=json.dumps(["GET", key]).encode("utf-8"),
            headers={"Authorization": f"Bearer {UPSTASH_REST_TOKEN}",
                     "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=8) as resp:
            j = json.loads(resp.read().decode("utf-8"))
        val = (j or {}).get("result")
        return val if isinstance(val, str) else None
    except Exception:
        return None


def _upstash_set(key: str, value: str, ttl: int = 0) -> bool:
    """SET string di Upstash Redis REST pakai format command array
    (POST ke base URL dengan body ["SET", key, value, ...]); ttl 0 = tanpa kedaluwarsa."""
    if not SYNC_ENABLED:
        return False
    try:
        cmd = ["SET", key, value]
        if ttl > 0:
            cmd += ["EX", str(int(ttl))]
        req = urllib.request.Request(
            UPSTASH_REST_URL,
            data=json.dumps(cmd).encode("utf-8"),
            headers={"Authorization": f"Bearer {UPSTASH_REST_TOKEN}",
                     "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=8) as resp:
            j = json.loads(resp.read().decode("utf-8"))
        return (j or {}).get("result") == "OK"
    except Exception:
        return False


def _unwrap_json(raw: Optional[str], default):
    """Parse JSON string; buka pembungkus {"value": "..."} bila ada (kompatibilitas)."""
    if not raw:
        return default
    try:
        d = json.loads(raw)
    except Exception:
        return default
    if isinstance(d, dict) and isinstance(d.get("value"), str):
        try:
            d = json.loads(d["value"])
        except Exception:
            return default
    return d


def sync_load(key: str) -> dict:
    """Muat data tersinkron untuk sebuah sync key."""
    raw = _upstash_get(f"ci:{key}")
    if not raw:
        return {"portfolio": None, "watchlist": None, "exists": False}
    d = _unwrap_json(raw, None)
    if not isinstance(d, dict):
        return {"portfolio": None, "watchlist": None, "exists": True}
    return {"portfolio": d.get("portfolio"), "watchlist": d.get("watchlist"),
            "exists": True}


def sync_save(key: str, portfolio: Optional[list] = None,
              watchlist: Optional[list] = None) -> bool:
    """Simpan data tersinkron + daftarkan key (untuk cron alert)."""
    if not SYNC_ENABLED:
        return False
    data: dict = {}
    if portfolio is not None:
        data["portfolio"] = portfolio
    if watchlist is not None:
        data["watchlist"] = watchlist
    ok = _upstash_set(f"ci:{key}", json.dumps(data, ensure_ascii=False))
    if ok:
        parsed = _unwrap_json(_upstash_get("ci:keys"), [])
        keys = [k for k in parsed if isinstance(k, str)] if isinstance(parsed, list) else []
        if key not in keys:
            keys.append(key)
            _upstash_set("ci:keys", json.dumps(keys))
    return ok


def _telegram_send(text: str) -> bool:
    """Kirim pesan Telegram (opsional)."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        body = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text,
                           "disable_web_page_preview": True}).encode("utf-8")
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False

TICKER_CACHE: Dict[str, dict] = {}
TICKER_CACHE_TTL_SECONDS = 24 * 3600


def load_idx_tickers(universe: str = "all") -> List[str]:
    """Daftar kode saham IDX (dengan suffix .JK untuk yfinance).

    Sumber: dataset publik IDX (wildangunawan/Dataset-Saham-IDX); jika gagal,
    fallback ke konstituen LQ45.
    """
    universe = (universe or "all").lower()
    if universe == "liquid":
        return [f"{t}.JK" for t in IDX_LIQUID_TICKERS]

    now = time.time()
    hit = TICKER_CACHE.get("all")
    if hit and now - hit["ts"] < TICKER_CACHE_TTL_SECONDS:
        return hit["tickers"]

    codes = []
    try:
        req = urllib.request.Request(IDX_TICKER_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            csv_data = resp.read()
        df_csv = pd.read_csv(io.BytesIO(csv_data))
        col = "code" if "code" in df_csv.columns else df_csv.columns[0]
        codes = [str(c).strip().upper() for c in df_csv[col].tolist()]
        codes = [c for c in codes if c and c.isalnum() and 2 <= len(c) <= 5]
    except Exception:
        codes = list(IDX_LIQUID_TICKERS)

    tickers = [f"{c}.JK" for c in codes]
    TICKER_CACHE["all"] = {"ts": now, "tickers": tickers}
    return tickers


def _download_github_csv(ticker: str) -> Optional[pd.DataFrame]:
    """Fallback data historis IDX dari dataset publik GitHub (2019-2025).

    Dipakai saat Yahoo Finance memblokir/rate-limit IP datacenter (mis. Vercel).
    Kolom utama: date, open_price, high, low, close, volume, value, foreign_buy, foreign_sell.
    """
    if ".JK" not in ticker.upper():
        return None
    code = ticker.upper().replace(".JK", "")
    url = f"{IDX_HIST_CSV_BASE}/{code}.csv"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            csv_data = resp.read()
        df = pd.read_csv(io.BytesIO(csv_data), parse_dates=[0])
        df = df.set_index(df.columns[0])
        df.columns = [str(c).strip().upper() for c in df.columns]
        rename = {"OPEN_PRICE": "Open", "HIGH": "High", "LOW": "Low",
                  "CLOSE": "Close", "VOLUME": "Volume", "VALUE": "Value"}
        df = df.rename(columns=rename)
        keep = [c for c in ["Open", "High", "Low", "Close", "Volume", "Value"] if c in df.columns]
        df = df[keep].dropna(subset=["Open", "High", "Low", "Close"])
        if "Volume" in df.columns:
            df["Volume"] = df["Volume"].fillna(0.0)
        df.index = pd.to_datetime(df.index)
        df = df.sort_index().tail(500)
        if len(df) < 30:
            return None
        return df
    except Exception:
        return None


def _download_batch(tickers: List[str], period: str) -> Dict[str, pd.DataFrame]:
    """Unduh OHLCV beberapa ticker dalam satu panggilan Yahoo (lebih cepat & hemat rate-limit)."""
    if not tickers:
        return {}
    try:
        raw = _call_with_timeout(
            lambda: yf.download(tickers, period=period, interval="1d", auto_adjust=True,
                                group_by="ticker", progress=False, threads=False),
            YFINANCE_TIMEOUT,
        )
    except Exception:
        return {}
    if raw is None or raw.empty:
        return {}

    result: Dict[str, pd.DataFrame] = {}
    if isinstance(raw.columns, pd.MultiIndex) and raw.columns.nlevels == 2:
        for tk in tickers:
            try:
                sub = raw[tk]
                if isinstance(sub.columns, pd.MultiIndex):
                    sub.columns = sub.columns.get_level_values(-1)
                sub = sub.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
                if len(sub) >= 25:
                    result[tk] = sub.tail(250)
            except Exception:
                continue
        return result

    df = raw.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    if len(df) >= 25:
        result[tickers[0]] = df.tail(250)
    return result


def _screener_metrics(df: pd.DataFrame) -> dict:
    close, vol = df["Close"], df["Volume"]
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    day_ret = (last / prev - 1) * 100 if prev > 0 else 0.0
    if "Value" in df.columns and not np.isnan(float(df["Value"].iloc[-1])):
        est_value = float(df["Value"].iloc[-1])
    else:
        est_value = last * float(vol.iloc[-1])
    vma = float(sma(vol, 20).iloc[-1]) if len(df) >= 20 else float(vol.mean())
    vol_ratio = float(vol.iloc[-1]) / vma if vma > 0 else 0.0
    return {"last": last, "day_ret": day_ret, "est_value": est_value, "vol_ratio": vol_ratio}


def run_screener(df: pd.DataFrame, criteria: str = "all",
                 bandar_values: Optional[List[float]] = None) -> dict:
    """Kriteria preset screener Coachinvestasi (halaman awal buku).

    - scalping : Value > 1M IDR, 1DayPriceReturns >= 10%, Price >= 50.
    - bsjp     : Value >= 5M IDR, 1DayPriceReturns >= 8%, Volume >= 2x VolumeMA20.
    - swing    : butuh deret BandarValue (dari API Broker Summary); tanpa API,
                 memakai proksi nilai transaksi total (ditandai jelas).
    """
    m = _screener_metrics(df)
    met: List[str] = []
    checks: Dict[str, dict] = {}

    # --- Scalping Trade Watchlist ---
    scalping = {
        "value_ge_1b": bool(m["est_value"] >= 1e9),
        "day_return_ge_10pct": bool(m["day_ret"] >= 10.0),
        "price_gt_50": bool(m["last"] > 50),
    }
    scalping["eligible"] = all(scalping.values())
    if scalping["eligible"]:
        met.append("SCALPING")
    checks["scalping"] = scalping

    # --- Beli Sore Jual Pagi ---
    bsjp = {
        "value_ge_5b": bool(m["est_value"] >= 5e9),
        "day_return_ge_8pct": bool(m["day_ret"] >= 8.0),
        "volume_ge_2x_ma20": bool(m["vol_ratio"] >= 2.0),
    }
    bsjp["eligible"] = all(bsjp.values())
    if bsjp["eligible"]:
        met.append("BSJP (Beli Sore Jual Pagi)")
    checks["bsjp"] = bsjp

    # --- Swing Trade Watchlist (butuh BandarValue; tanpa API gunakan proksi) ---
    swing: dict = {}
    if bandar_values and len(bandar_values) >= 21:
        bv = pd.Series([float(v) for v in bandar_values])
        bv_ma10 = float(bv.rolling(10).mean().iloc[-1])
        bv_ma20 = float(bv.rolling(20).mean().iloc[-1])
        value_series = df["Volume"] * df["Close"]
        value_ma20 = float(sma(value_series, 20).iloc[-1])
        swing = {
            "bandar_value_gt_ma20": bool(float(bv.iloc[-1]) > bv_ma20),
            "value_ma20_ge_10b": bool(value_ma20 >= 10e9),
            "prev_bandar_le_now": bool(float(bv.iloc[-2]) <= float(bv.iloc[-1])),
            "bandar_ma10_gt_ma20": bool(bv_ma10 > bv_ma20),
            "note": "Kriteria BandarValue memakai data Broker Summary API.",
        }
        swing["eligible"] = all(swing[k] for k in
                                 ("bandar_value_gt_ma20", "value_ma20_ge_10b",
                                  "prev_bandar_le_now", "bandar_ma10_gt_ma20"))
        if swing["eligible"]:
            met.append("SWING")
    else:
        value_series = df["Volume"] * df["Close"]
        vma10 = float(value_series.rolling(10).mean().iloc[-1])
        vma20 = float(value_series.rolling(20).mean().iloc[-1])
        swing = {
            "value_gt_ma20": bool(float(value_series.iloc[-1]) > vma20),
            "value_ma20_ge_10b": bool(vma20 >= 10e9),
            "prev_value_le_now": bool(float(value_series.iloc[-2]) <= float(value_series.iloc[-1])),
            "value_ma10_gt_ma20": bool(vma10 > vma20),
            "note": "Proksi tanpa data Broker Summary (BandarValue tidak tersedia via yfinance).",
        }
        swing["eligible"] = all(swing[k] for k in
                                 ("value_gt_ma20", "value_ma20_ge_10b",
                                  "prev_value_le_now", "value_ma10_gt_ma20"))
        if swing["eligible"]:
            met.append("SWING (proksi)")
    checks["swing"] = swing

    if criteria == "scalping":
        eligible = bool(scalping["eligible"])
    elif criteria == "bsjp":
        eligible = bool(bsjp["eligible"])
    elif criteria == "swing":
        eligible = bool(swing.get("eligible", False))
    else:
        eligible = bool(met)

    return {
        "eligible": eligible,
        "criteria_met": met,
        "checks": checks,
        "metrics": {
            "price": num(m["last"], 2),
            "day_return_pct": num(m["day_ret"], 2),
            "estimated_value_idr": num(m["est_value"], 0),
            "volume_ratio_to_ma20": num(m["vol_ratio"], 2),
        },
    }


def _idx_edge_next_key() -> Optional[str]:
    """Rotasi key API (membagi kuota harian antar key)."""
    global _IDX_EDGE_KEY_IDX
    if not IDX_EDGE_API_KEYS:
        return None
    key = IDX_EDGE_API_KEYS[_IDX_EDGE_KEY_IDX % len(IDX_EDGE_API_KEYS)]
    _IDX_EDGE_KEY_IDX += 1
    return key


def idx_edge_get(path: str, params: Dict[str, Any], cache_key: str = "",
                 ttl: int = IDX_EDGE_HIST_TTL) -> Optional[dict]:
    """GET ke IDX Edge PRO API dengan header X-API-Key, rotasi key, dan cache in-memory."""
    if not IDX_EDGE_API_KEYS:
        return None
    ck = cache_key or f"{path}:{json.dumps(params, sort_keys=True)}"
    now = time.time()
    hit = IDX_EDGE_CACHE.get(ck)
    if hit and now - hit["ts"] < ttl:
        return hit["data"]
    key = _idx_edge_next_key()
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{IDX_EDGE_API_URL}{path}" + (f"?{qs}" if qs else "")
    try:
        req = urllib.request.Request(url, headers={"X-API-Key": key, "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    IDX_EDGE_CACHE[ck] = {"ts": now, "data": data}
    return data


def fetch_idx_history(ticker: str, limit: int = 200) -> Optional[pd.DataFrame]:
    """OHLCV real-time IDX dari IDX Edge PRO (/api/history/{code}).

    Kolom tambahan: Value (nilai transaksi harian) dan n_foreign (net foreign).
    """
    if ".JK" not in ticker.upper():
        return None
    code = ticker.upper().replace(".JK", "")
    data = idx_edge_get(f"/api/history/{code}", {"frame": "daily", "limit": limit},
                        cache_key=f"hist:{code}")
    if not data or not isinstance(data.get("rows"), list) or not data["rows"]:
        return None
    rows = []
    for r in data["rows"]:
        try:
            rows.append({
                "date": pd.Timestamp(r["date"]),
                "Open": float(r["open"]), "High": float(r["high"]),
                "Low": float(r["low"]), "Close": float(r["close"]),
                "Volume": float(r["volume"]),
                "Value": float(r.get("value") or 0.0),
                "n_foreign": float(r.get("n_foreign") or 0.0),
            })
        except Exception:
            continue
    if len(rows) < 30:
        return None
    df = pd.DataFrame(rows).set_index("date").sort_index().tail(500)
    df.attrs["source"] = "idx-edge-pro"
    return df


def fetch_idx_broker_summary(ticker: str) -> Optional[dict]:
    """Broker Summary dari IDX Edge PRO (/api/broker-summary/{code}).

    Diadaptasi ke analisis buku: buyer/seller per broker (bval/bvol/sval/svol),
    AVG harga bandar tertimbang, value share Top Buyer, dan skenario idaman.
    """
    if ".JK" not in ticker.upper():
        return None
    code = ticker.upper().replace(".JK", "")
    today = time.strftime("%Y-%m-%d")
    start = (pd.Timestamp(today) - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    data = idx_edge_get(f"/api/broker-summary/{code}",
                        {"start_date": start, "end_date": today, "flow": "all"},
                        cache_key=f"bs:{code}", ttl=IDX_EDGE_BROKER_TTL)
    if not data or not isinstance(data.get("brokers"), list) or not data["brokers"]:
        return None

    buyers, sellers = [], []
    broker_names = {}
    for b in data["brokers"]:
        try:
            bc = str(b.get("broker_code") or "?")
            broker_names[bc] = b.get("broker_name") or bc
            bval = float(b.get("bval") or 0); bvol = float(b.get("bvol") or 0)
            sval = float(b.get("sval") or 0); svol = float(b.get("svol") or 0)
            if bval > 0 and bvol > 0:
                buyers.append({"broker": bc, "volume": bvol, "avg_price": bval / bvol,
                               "value": bval, "nval": float(b.get("nval") or 0)})
            if sval > 0 and svol > 0:
                sellers.append({"broker": bc, "volume": svol, "avg_price": sval / svol,
                                "value": sval, "nval": float(b.get("nval") or 0)})
        except Exception:
            continue
    if not buyers:
        return None

    analysis = analyze_broker_summary({"last_price": None, "buyers": buyers, "sellers": sellers})
    analysis["source"] = "idx-edge-pro"
    analysis["broker_names"] = broker_names
    return analysis


def fetch_idx_accumulation(ticker: str, days: int = 60) -> Optional[dict]:
    """Deret harian nilai akumulasi bandar (/api/broker-accumulation/{code}).

    BandarValue harian = jumlah seluruh net value (nval) broker per tanggal;
    dipakai untuk kriteria Swing Watchlist buku: BandarValue vs MA10/MA20.
    """
    if ".JK" not in ticker.upper():
        return None
    code = ticker.upper().replace(".JK", "")
    data = idx_edge_get(f"/api/broker-accumulation/{code}", {"limit": days},
                        cache_key=f"acc:{code}", ttl=IDX_EDGE_BROKER_TTL)
    if not data or not isinstance(data.get("series"), list):
        return None

    by_date: Dict[str, float] = {}
    top: Dict[str, dict] = {}
    for br in data["series"]:
        bc = str(br.get("broker_code") or "?")
        for pt in br.get("points") or []:
            d = str(pt.get("date"))
            nv = float(pt.get("nval") or 0)
            by_date[d] = by_date.get(d, 0.0) + nv
            if bc not in top or abs(nv) > abs(top[bc].get("nval", 0)):
                top[bc] = {"nval": nv, "cum": float(pt.get("cum_nval") or 0),
                           "name": br.get("broker_name") or bc}
    if not by_date:
        return None

    series = pd.Series({pd.Timestamp(d): v for d, v in by_date.items()}).sort_index()
    accum = [float(x) for x in series.clip(lower=0).tolist()]
    dates = [d.strftime("%Y-%m-%d") for d in series.index]
    top_broker = max(top.items(), key=lambda kv: kv[1]["cum"]) if top else None
    return {
        "bandar_value_series": [float(x) for x in series.tolist()],
        "bandar_accum_series": accum,
        "dates": dates,
        "last_bandar_value": float(series.iloc[-1]) if len(series) else 0.0,
        "top_accumulating_broker": {"broker": top_broker[0], **top_broker[1]} if top_broker else None,
    }


def quick_signal(df: pd.DataFrame) -> str:
    """Sinyal ringkas (BUY/SELL/HOLD) untuk screener."""
    try:
        sig = compute_signal(
            df,
            detect_trend(df),
            find_sr_zones(df),
            detect_candlestick_patterns(df),
            fibonacci_levels(df),
            rsi_divergence(df),
            cross_events(df),
            volume_analysis(df),
            launch_pad(df),
            drop_base_rally(df),
        )
        return sig["action"]
    except Exception:
        return "N/A"


def _get_ticker_data(tk: str, period: str):
    """Data OHLCV per ticker:
    1) IDX Edge PRO (real-time IDX; menghindari yfinance yang menggantung di
       server karena Yahoo memblokir IP datacenter),
    2) yfinance (untuk ticker non-IDX / bila IDX Edge tidak dikonfigurasi),
    3) dataset publik GitHub.
    """
    if ".JK" in tk.upper() and IDX_EDGE_API_KEYS:
        df = fetch_idx_history(tk)
        if df is not None:
            return df, "idx-edge-pro"
    df = _download_batch([tk], period).get(tk)
    if df is not None:
        return df, "yfinance"
    if ".JK" in tk.upper():
        df = _download_github_csv(tk)
        if df is not None:
            return df, "github-dataset"
    return None, None


def _call_with_timeout(fn, timeout: float, *args, **kwargs):
    """Jalankan fn dengan batas waktu; kembalikan None bila lewat batas.

    Thread-nya dibuat daemon sehingga tidak menahan proses walau menggantung.
    """
    box: dict = {}

    def runner():
        try:
            box["v"] = fn(*args, **kwargs)
        except Exception as e:
            box["e"] = e

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return None
    if "e" in box:
        raise box["e"]
    return box.get("v")


YFINANCE_TIMEOUT = 25
LIVE_QUOTE_TIMEOUT = 10

# Intraday (5m) dari Yahoo via curl_cffi (impersonasi browser) — jalur ini lolos
# blokir/rate-limit yang menimpa yfinance biasa (requests/urllib) dari IP datacenter.
YAHOO_INTRADAY_CACHE: Dict[str, dict] = {}
YAHOO_INTRADAY_TTL = 45  # detik (bar 5m hanya berubah tiap 5 menit)

_YAHOO_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _yahoo_chart_cffi(ticker: str, interval: str = "5m", rng: str = "1d") -> Optional[dict]:
    """Yahoo chart API via curl_cffi (impersonasi chrome) -> dict {meta, bars}."""
    try:
        from curl_cffi import requests as cr
    except Exception:
        return None
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(ticker)}?interval={interval}&range={rng}")
    try:
        r = cr.get(url, impersonate="chrome", timeout=6,
                   headers={"User-Agent": _YAHOO_UA,
                            "Accept": "application/json,text/plain,*/*"})
        if r.status_code != 200:
            return None
        j = r.json()
        res = (j.get("chart") or {}).get("result") or []
        if not res:
            return None
        r0 = res[0]
        meta = r0.get("meta") or {}
        # marketState tidak ada di endpoint chart -> deteksi dari sesi reguler.
        _ctp = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
        _now = time.time()
        _s, _e = _ctp.get("start"), _ctp.get("end")
        meta["marketState"] = "REGULAR" if (_s and _e and _s <= _now <= _e) else None
        ts = r0.get("timestamp") or []
        q0 = ((r0.get("indicators") or {}).get("quote") or [{}])[0] or {}
        o, h, l, c, v = (q0.get("open"), q0.get("high"), q0.get("low"),
                         q0.get("close"), q0.get("volume"))
        bars = []
        for i, t in enumerate(ts):
            if not (c and i < len(c) and c[i] is not None):
                continue
            bars.append({
                "time": int(t),
                "open": num(o[i], 2) if o and i < len(o) and o[i] is not None else num(c[i], 2),
                "high": num(h[i], 2) if h and i < len(h) and h[i] is not None else num(c[i], 2),
                "low": num(l[i], 2) if l and i < len(l) and l[i] is not None else num(c[i], 2),
                "close": num(c[i], 2),
                "volume": int(v[i]) if v and i < len(v) and v[i] is not None else 0,
            })
        if not bars:
            return None
        return {"meta": meta, "bars": bars}
    except Exception:
        return None


def fetch_intraday(ticker: str) -> Optional[dict]:
    """Bar 5m hari ini + meta pasar dari Yahoo (curl_cffi), cache 45 dtk.
    Kode pendek tanpa titik dicoba dengan suffix .JK dulu (bursa IDX)."""
    t = ticker.upper()
    now = time.time()
    hit = YAHOO_INTRADAY_CACHE.get(t)
    if hit and now - hit["ts"] < YAHOO_INTRADAY_TTL:
        return hit["data"]
    variants = [t + ".JK", t] if ("." not in t and t.isalnum() and len(t) <= 5) else [t]
    for v in variants:
        data = _yahoo_chart_cffi(v)
        if data:
            YAHOO_INTRADAY_CACHE[t] = {"ts": now, "data": data}
            return data
    return None


def _fetch_live_quote_inner(ticker: str) -> Optional[dict]:
    """Kutipan harga real-time dari Yahoo (chart 5m via curl_cffi). None bila gagal."""
    try:
        data = fetch_intraday(ticker)
        if not data:
            return None
        meta = data.get("meta") or {}
        px = meta.get("regularMarketPrice") or data["bars"][-1]["close"]
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        if not px or px <= 0:
            return None
        return {
            "last_price": float(px),
            "previous_close": float(prev) if prev else None,
            "change_pct": (px / prev - 1) * 100 if prev and prev > 0 else None,
            "source": "yahoo-live",
            "as_of": meta.get("regularMarketTime"),
            "market_state": meta.get("marketState"),
            "long_name": meta.get("longName"),
        }
    except Exception:
        return None


def fetch_live_quote(ticker: str) -> Optional[dict]:
    """Best-effort harga real-time (curl_cffi, timeout singkat agar tak memperlambat API)."""
    try:
        return _call_with_timeout(_fetch_live_quote_inner, LIVE_QUOTE_TIMEOUT, ticker)
    except Exception:
        return None


def _scan_worker(tk: str, criteria: str, period: str, include_signal: bool,
                 include_bandarmology: bool):
    """Proses 1 ticker (dijalankan paralel via ThreadPoolExecutor)."""
    df, src = _get_ticker_data(tk, period)
    if df is None:
        return {"tk": tk, "skipped": True}

    bandar_series = None
    if criteria in ("swing", "bandar", "all") and ".JK" in tk.upper() and IDX_EDGE_API_KEYS:
        acc = fetch_idx_accumulation(tk)
        if acc and acc.get("bandar_accum_series"):
            bandar_series = acc["bandar_accum_series"]

    result = run_screener(df, criteria, bandar_series)
    item = {"ticker": tk, "source": src, **result["metrics"], "criteria_met": result["criteria_met"]}
    item["data_date"] = (str(df.index[-1].date()) if hasattr(df.index[-1], "date")
                          else str(df.index[-1]))
    if include_signal:
        item["signal"] = quick_signal(df)

    # Broker Summary (Bab 3-7): ditampilkan bila lolos kriteria, atau wajib untuk
    # kriteria "bandar" (ACC + value share Top Buyer >= 60% per buku).
    bandar_used = 0
    need_bandar = criteria == "bandar" or (include_bandarmology and result["eligible"])
    if need_bandar and ".JK" in tk.upper() and IDX_EDGE_API_KEYS:
        bs = fetch_idx_broker_summary(tk)
        if bs:
            bandar_used = 1
            item["bandarmology"] = {
                "status": bs.get("status"),
                "scenario": bs.get("scenario"),
                "bandar_avg_price": bs.get("bandar_avg_price"),
                "bandar_avg_distance_pct": bs.get("bandar_avg_distance_pct"),
                "top_buyer_value_share_pct": bs.get("top_buyer_value_share_pct"),
                "value_share_significant": bs.get("value_share_significant"),
            }

    if criteria == "bandar":
        # Kriteria Bandarmology buku (Bab 3-7): akumulasi + value share Top Buyer >= 60%.
        b = item.get("bandarmology") or {}
        bandar_ok = bool(b.get("status", "").startswith("ACC") and b.get("value_share_significant"))
        item["criteria_met"] = ["BANDAR"] if bandar_ok else []
        return {"tk": tk, "skipped": False, "item": item,
                "eligible": bandar_ok, "bandar_used": bandar_used}

    return {"tk": tk, "skipped": False, "item": item,
            "eligible": bool(result["eligible"]), "bandar_used": bandar_used}


def _scan(tickers: List[str], criteria: str, period: str, include_signal: bool,
          include_bandarmology: bool = True) -> dict:
    matched: List[dict] = []
    scanned = skipped = bandar_used = 0
    workers = min(8, max(1, len(tickers)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_scan_worker, tk, criteria, period,
                             include_signal, include_bandarmology) for tk in tickers]
        for f in futures:
            try:
                out = f.result()
            except Exception:
                skipped += 1
                continue
            if out.get("skipped"):
                skipped += 1
                continue
            scanned += 1
            bandar_used += out.get("bandar_used", 0)
            if out.get("eligible"):
                matched.append(out["item"])
    return {
        "scanned": scanned,
        "skipped": skipped,
        "bandarmology_checked": bandar_used,
        "results": matched,
    }


def _backtest_one(ticker: str, criteria: str, years: int,
                  confirm: bool = True) -> Optional[dict]:
    """Backtest sederhana 1 ticker: sinyal di harga tutup -> SL 2xATR, TP 2R (RRR 1:2, Bab 8).
    scalping/bsjp: hold maks 5 hari; swing: 20 hari. Timeout dihitung terpisah.
    confirm=True = konfirmasi ala buku: harga > SMA20 (bias naik), RSI < 70 (tidak
    mengejar overbought), volume > VolumeMA20 (ada tenaga beli)."""
    try:
        df = fetch_data(ticker, "5y")
    except Exception:
        return None
    if len(df) < 60:
        return None
    cutoff = df.index[-1] - pd.DateOffset(years=years)
    df = df[df.index >= cutoff]
    if len(df) < 40:
        return None

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    vol = df["Volume"].astype(float)
    value = df["Value"].astype(float) if "Value" in df.columns else close * vol
    atr_s = atr(df, 14)
    vma20 = vol.rolling(20).mean()
    sma20 = close.rolling(20).mean()
    rsi_s = rsi(close, 14)
    value_ma10 = value.rolling(10).mean()
    value_ma20 = value.rolling(20).mean()

    triggers = []
    n = len(df)
    for i in range(20, n - 1):
        last, prev = float(close.iloc[i]), float(close.iloc[i - 1])
        day_ret = (last / prev - 1) * 100 if prev > 0 else 0.0
        v = float(value.iloc[i])
        vr = float(vol.iloc[i]) / float(vma20.iloc[i]) if vma20.iloc[i] > 0 else 0.0
        if criteria == "scalping":
            hit = v >= 1e9 and day_ret >= 10.0 and last > 50
        elif criteria == "bsjp":
            hit = v >= 5e9 and day_ret >= 8.0 and vr >= 2.0
        else:  # swing (proksi nilai transaksi)
            hit = (float(value.iloc[i]) > float(value_ma20.iloc[i])
                   and float(value_ma20.iloc[i]) >= 10e9
                   and float(value.iloc[i - 1]) <= float(value.iloc[i])
                   and float(value_ma10.iloc[i]) > float(value_ma20.iloc[i]))
        if hit and confirm:
            # Konfirmasi ala buku: bias naik + tidak mengejar overbought + volume hidup.
            hit = (float(close.iloc[i]) > float(sma20.iloc[i])
                   and float(rsi_s.iloc[i]) < 70.0
                   and float(vol.iloc[i]) > float(vma20.iloc[i]))
        if hit:
            triggers.append(i)
    if not triggers:
        return {"ticker": ticker, "trades": 0}

    max_hold = 5 if criteria in ("scalping", "bsjp") else 20
    wins = losses = timeouts = 0
    r_sum = 0.0
    for i in triggers:
        entry = float(close.iloc[i])
        a = float(atr_s.iloc[i])
        atr_v = a if not np.isnan(a) else entry * 0.02
        risk = max(atr_v * 2, entry * 0.005)
        sl, tp = entry - risk, entry + 2 * risk
        outcome = None
        for j in range(i + 1, min(i + 1 + max_hold, n)):
            if float(low.iloc[j]) <= sl:
                outcome = -1.0
                break
            if float(high.iloc[j]) >= tp:
                outcome = 2.0
                break
        if outcome is None:
            timeouts += 1
        elif outcome > 0:
            wins += 1
            r_sum += outcome
        else:
            losses += 1
            r_sum += outcome
    total = wins + losses + timeouts
    decided = wins + losses
    return {
        "ticker": ticker,
        "trades": total,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "win_rate_pct": num(wins / decided * 100, 1) if decided else None,
        "avg_r": num(r_sum / total, 2) if total else None,
        "max_hold_days": max_hold,
    }


# ---------------------------------------------------------------------------
# 7. FASTAPI APP
# ---------------------------------------------------------------------------

app = FastAPI(
    title="dedesaputra_invst Strategy API",
    description="API analisis saham: Technical Analysis + Bandarmology (edukasi).",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "service": "dedesaputra_invst Strategy API",
        "dashboard": "/dashboard",
        "endpoints": [
            "GET  /api/health",
            "GET  /api/analyze/{ticker}?period=1y",
            "GET  /api/quotes?tickers=BBCA,TLKM&with_bandar=0",
            "GET  /api/sync?key=KODE_SINKRON",
            "PUT  /api/sync?key=KODE_SINKRON",
            "POST /api/portfolio/alerts",
            "GET  /api/cron/alerts",
            "POST /api/bandarmology/analyze",
            "GET  /api/chart/{ticker}?period=1y&limit=120&interval=daily|intraday",
            "GET  /api/screener/tickers?universe=all|liquid",
            "GET  /api/screener?criteria=all|swing|scalping|bsjp&universe=liquid|all&limit=20&offset=0",
            "POST /api/screener",
        ],
        "docs": "/docs",
    }


DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard.html")


@app.get("/dashboard")
def dashboard():
    """Halaman dashboard web sederhana (HTML statis + vanilla JS)."""
    if not os.path.exists(DASHBOARD_PATH):
        raise HTTPException(404, "File dashboard.html tidak ditemukan.")
    return FileResponse(DASHBOARD_PATH, media_type="text/html")


@app.get("/api/health")
def health():
    return {
        "sync_enabled": SYNC_ENABLED,
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "status": "ok",
        "service": "dedesaputra_invst Strategy API",
        "time": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


def _analyze_core(ticker: str, period: str, risk_amount: float,
                  light: bool = False) -> dict:
    """Analisis lengkap 1 saham. light=True -> tanpa bandarmology (hemat kuota IDX Edge)."""
    df = fetch_data(ticker, period)
    close = df["Close"]
    last_price = float(close.iloc[-1])
    prev_price = float(close.iloc[-2])
    change_pct = (last_price / prev_price - 1) * 100 if prev_price > 0 else None

    # Kutipan harga live (Yahoo 5m via curl_cffi; best-effort).
    quote = fetch_live_quote(ticker)
    has_quote = bool(quote and quote.get("last_price"))
    # Live = kutipan intraday saat pasar sedang buka (REGULAR).
    is_live = bool(has_quote and quote.get("market_state") == "REGULAR")
    if has_quote:
        last_price = quote["last_price"]
        if quote.get("change_pct") is not None:
            change_pct = quote["change_pct"]

    # --- indikator ---
    s20, s50, s200 = sma(close, 20).iloc[-1], sma(close, 50).iloc[-1], sma(close, 200).iloc[-1]
    e20 = ema(close, 20).iloc[-1]
    r14 = rsi(close, 14).iloc[-1]
    macd_line, sig_line, hist = macd(close)
    atr14 = atr(df, 14).iloc[-1]

    trend = detect_trend(df)
    sr_zones = find_sr_zones(df)
    vol_sr = volume_sr_levels(df)
    candles = detect_candlestick_patterns(df)
    fib = fibonacci_levels(df)
    div = rsi_divergence(df)
    cross = cross_events(df)
    vol = volume_analysis(df)
    lp = launch_pad(df)
    dbr = drop_base_rally(df)
    screen = screener_hints(df, ticker)
    signal = compute_signal(df, trend, sr_zones, candles, fib, div, cross, vol, lp, dbr)
    rm = risk_management(last_price, sr_zones, signal["action"], float(atr14), risk_amount)

    # --- Bandarmology (IDX Edge PRO, jika key di-set; dilewati saat light) ---
    bandarmology = None
    bandarmology_note = BANDARMOLOGY_NOTE
    # Normalisasi varian bursa IDX (.JK) untuk input pendek tanpa titik (mis. BBCA).
    bandar_tk = ticker.upper()
    if ".JK" not in bandar_tk and "." not in bandar_tk and bandar_tk.isalnum() and len(bandar_tk) <= 5:
        bandar_tk += ".JK"
    if not light and IDX_EDGE_API_KEYS and ".JK" in bandar_tk:
        bandarmology = {"source": "IDX Edge PRO"}
        bs = fetch_idx_broker_summary(bandar_tk)
        if bs:
            bandarmology.update({
                "status": bs.get("status"),
                "scenario": bs.get("scenario"),
                "bandar_avg_price": bs.get("bandar_avg_price"),
                "bandar_avg_distance_pct": bs.get("bandar_avg_distance_pct"),
                "top_buyer_value_share_pct": bs.get("top_buyer_value_share_pct"),
                "value_share_significant": bs.get("value_share_significant"),
                "top_buyers": bs.get("top_buyers", [])[:5],
                "top_sellers": bs.get("top_sellers", [])[:5],
                "interpretation": bs.get("interpretation"),
            })
        acc = fetch_idx_accumulation(bandar_tk)
        if acc:
            bandarmology["bandar_accumulation"] = {
                "last_bandar_value": num(acc.get("last_bandar_value"), 0),
                "bandar_value_last_10": [num(x, 0) for x in (acc.get("bandar_value_series") or [])[-10:]],
                "top_accumulating_broker": acc.get("top_accumulating_broker"),
                "note": "BandarValue harian = jumlah net value seluruh broker (kriteria swing: vs MA10/MA20).",
            }
        if "n_foreign" in df.columns:
            nf = df["n_foreign"].astype(float)
            bandarmology["net_foreign"] = {
                "today": num(nf.iloc[-1], 0),
                "sum_5d": num(nf.tail(5).sum(), 0),
                "sum_20d": num(nf.tail(20).sum(), 0),
                "note": "Net foreign (foreign buy - foreign sell) dari data harian IDX Edge PRO.",
            }
        if bandarmology == {"source": "IDX Edge PRO"}:
            bandarmology = None
    if bandarmology:
        bandarmology_note = "Broker Summary, akumulasi bandar & net foreign dari IDX Edge PRO (real-time)."

    return {
        "ticker": ticker.upper(),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "disclaimer": DISCLAIMER,
        "market": {
            "period": period,
            "bars": len(df),
            "last_price": num(last_price, 2),
            "change_pct": num(change_pct, 2),
            "date": str(close.index[-1].date()) if hasattr(close.index[-1], "date") else str(close.index[-1]),
            "source": df.attrs.get("source", "yfinance"),
            "is_live": is_live,
            "quote_source": quote.get("source") if has_quote else None,
            "quote_as_of": quote.get("as_of") if has_quote else None,
            "market_state": quote.get("market_state") if has_quote else None,
            "note": GITHUB_DATASET_NOTE if "github" in str(df.attrs.get("source", "")) else None,
        },
        "indicators": {
            "sma20": num(s20, 2),
            "sma50": num(s50, 2),
            "sma200": num(s200, 2),
            "ema20": num(e20, 2),
            "rsi14": num(r14, 2),
            "macd": {
                "line": num(macd_line.iloc[-1], 4),
                "signal": num(sig_line.iloc[-1], 4),
                "histogram": num(hist.iloc[-1], 4),
            },
            "atr14": num(atr14, 2),
            "volume": vol,
        },
        "trend": trend,
        "support_resistance": {
            "zones": sr_zones,
            "volume_levels": vol_sr,
            "note": "S&R adalah AREA (zona), bukan garis tunggal (Bab 1).",
        },
        "fibonacci": fib,
        "candlestick": candles,
        "rsi_divergence": div,
        "special_patterns": {"launch_pad": lp, "drop_base_rally": dbr},
        "screener_hints": screen,
        "signal": signal,
        "risk_management": rm,
        "bandarmology": bandarmology,
        "bandarmology_note": bandarmology_note,
    }


@app.get("/api/analyze/{ticker}")
def analyze(
    ticker: str,
    period: str = Query("1y", pattern="^(1mo|3mo|6mo|1y|2y|5y)$"),
    risk_amount: float = Query(5_000_000, gt=0),
):
    return _analyze_core(ticker, period, risk_amount, light=False)


@app.get("/api/quotes")
def quotes(
    tickers: str = Query(..., description="Kode saham dipisah koma, maks 15. Contoh: BBCA,TLKM,BBRI"),
    period: str = Query("1y", pattern="^(1mo|3mo|6mo|1y|2y|5y)$"),
    with_bandar: bool = Query(False, description="Sertakan Broker Summary (memakai kuota IDX Edge)"),
):
    """Kutipan + sinyal + TP/SL untuk portofolio/watchlist (paralel, hemat kuota)."""
    codes = [c.strip().upper() for c in tickers.split(",") if c.strip()][:15]
    if not codes:
        raise HTTPException(422, "Parameter tickers tidak boleh kosong.")

    def one(code: str) -> dict:
        try:
            d = _analyze_core(code, period, 5_000_000, light=not with_bandar)
            return {"ticker": code, "ok": True, "data": d}
        except HTTPException as exc:
            return {"ticker": code, "ok": False, "error": str(exc.detail)[:150]}
        except Exception:
            return {"ticker": code, "ok": False, "error": "Gagal memproses ticker."}

    workers = min(8, max(1, len(codes)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, codes))

    return {
        "requested": len(codes),
        "bandarmology_checked": bool(with_bandar and IDX_EDGE_API_KEYS),
        "results": results,
        "note": ("Live price = Yahoo 5m (saat pasar buka); sinyal/TP/SL dari data harian "
                 "IDX Edge PRO/yfinance (cache 6 jam). with_bandar=1 memakai kuota "
                 "Broker Summary IDX Edge."),
        "disclaimer": DISCLAIMER,
    }


class SyncPayload(BaseModel):
    portfolio: Optional[List[dict]] = None
    watchlist: Optional[List[str]] = None


class AlertsRequest(BaseModel):
    key: Optional[str] = None
    portfolio: Optional[List[dict]] = None


@app.get("/api/sync")
def sync_get(key: str = Query(..., min_length=6, max_length=128)):
    """Muat portofolio & watchlist tersinkron (antar perangkat)."""
    return {"sync_enabled": SYNC_ENABLED, **sync_load(key)}


@app.put("/api/sync")
def sync_put(key: str = Query(..., min_length=6, max_length=128), payload: SyncPayload = None):
    """Simpan portofolio & watchlist ke penyimpanan cloud."""
    if payload is None:
        raise HTTPException(422, "Body JSON wajib diisi.")
    if not SYNC_ENABLED:
        return {"ok": False, "sync_enabled": False,
                "note": "Penyimpanan cloud belum dikonfigurasi (UPSTASH_REDIS_REST_URL/TOKEN)."}
    ok = sync_save(key, payload.portfolio, payload.watchlist)
    if not ok:
        raise HTTPException(502, "Gagal menyimpan ke penyimpanan cloud. Coba lagi.")
    return {"ok": True, "sync_enabled": True}


def _portfolio_alerts(portfolio: List[dict]) -> List[dict]:
    """Hitung alert TP/SL/overbought/sinyal jual untuk daftar posisi (paralel)."""
    if not portfolio:
        return []

    def one(h: dict) -> List[dict]:
        tk = str(h.get("ticker", "")).upper()
        if not tk:
            return []
        try:
            d = _analyze_core(tk, "3mo", 5_000_000, light=True)
        except Exception:
            return []
        price = d["market"]["last_price"]
        rm = d.get("risk_management") or {}
        rsi = (d.get("indicators") or {}).get("rsi14")
        sig = (d.get("signal") or {}).get("action", "")
        base = {"ticker": strip_suffix(tk), "price": num(price, 2),
                "qty": h.get("qty"), "avg": h.get("avg")}
        out: List[dict] = []
        if rm.get("stop_loss") and price <= rm["stop_loss"]:
            out.append({**base, "type": "SL", "level": num(rm["stop_loss"], 2),
                        "message": f"🛑 {strip_suffix(tk)} menyentuh STOP LOSS ({num(rm['stop_loss'], 2)}) — harga {num(price, 2)}"})
        if rm.get("take_profit") and price >= rm["take_profit"]:
            out.append({**base, "type": "TP", "level": num(rm["take_profit"], 2),
                        "message": f"🎯 {strip_suffix(tk)} mencapai TAKE PROFIT ({num(rm['take_profit'], 2)}) — harga {num(price, 2)}"})
        if rm.get("take_profit") and rm["stop_loss"] < price < rm["take_profit"] and price >= rm["take_profit"] * 0.98:
            out.append({**base, "type": "TP_NEAR", "level": num(rm["take_profit"], 2),
                        "message": f"🔥 {strip_suffix(tk)} hampir TP (≤2% dari {num(rm['take_profit'], 2)})"})
        if rsi is not None and rsi > 70:
            out.append({**base, "type": "OB", "rsi": num(rsi, 1),
                        "message": f"⚠️ {strip_suffix(tk)} overbought (RSI {num(rsi, 1)}) — waspada koreksi"})
        if sig in ("SELL", "STRONG SELL"):
            out.append({**base, "type": "SELL", "signal": sig,
                        "message": f"⬇️ {strip_suffix(tk)} sinyal {sig} — pertimbangkan take profit / cut loss"})
        return out

    alerts: List[dict] = []
    with ThreadPoolExecutor(max_workers=min(6, len(portfolio))) as ex:
        for res in ex.map(one, portfolio):
            if res:
                alerts.extend(res)
    return alerts


@app.post("/api/portfolio/alerts")
def portfolio_alerts(payload: AlertsRequest):
    """Cek posisi terhadap TP/SL & sinyal jual (dipakai dashboard untuk notifikasi)."""
    items = payload.portfolio
    if items is None and payload.key:
        items = (sync_load(payload.key) or {}).get("portfolio")
    if not items:
        return {"checked": 0, "alerts": [], "note": "Tidak ada posisi untuk diperiksa."}
    alerts = _portfolio_alerts(items)
    return {"checked": len(items), "alerts": alerts, "disclaimer": DISCLAIMER}


@app.get("/api/cron/alerts")
def cron_alerts(request: Request, secret: str = Query("")):
    """Cron (mis. Vercel Cron tiap 15 menit): cek semua portofolio tersinkron dan
    kirim notifikasi Telegram bila TP/SL tersentuh (sekali per tipe per hari)."""
    # Otorisasi: bila CRON_SECRET di-set, hanya terima
    # (a) Authorization: Bearer <CRON_SECRET> — dikirim otomatis oleh Vercel Cron, atau
    # (b) ?secret=<CRON_SECRET> — untuk scheduler eksternal / pemicu manual.
    # Header x-vercel-cron TIDAK dipercaya karena bisa dipalsukan.
    auth = request.headers.get("authorization", "")
    bearer_ok = bool(CRON_SECRET) and auth == f"Bearer {CRON_SECRET}"
    if CRON_SECRET and secret != CRON_SECRET and not bearer_ok:
        raise HTTPException(403, "Forbidden")
    if not SYNC_ENABLED:
        return {"skipped": True, "reason": "Penyimpanan cloud belum dikonfigurasi."}

    parsed_keys = _unwrap_json(_upstash_get("ci:keys"), [])
    keys = [k for k in parsed_keys if isinstance(k, str)] if isinstance(parsed_keys, list) else []

    sent = 0
    checked = 0
    today = time.strftime("%Y-%m-%d")
    for key in keys[:50]:
        data = sync_load(key)
        portfolio = data.get("portfolio") or []
        if not portfolio:
            continue
        checked += 1
        for a in _portfolio_alerts(portfolio):
            dedupe = f"ci:notif:{key}:{a.get('ticker')}:{a.get('type')}:{today}"
            if _upstash_get(dedupe):
                continue
            if _telegram_send(a.get("message", "Alerta") + "\n\n(dedesaputra_invst)"):
                _upstash_set(dedupe, "1", ttl=86400)
                sent += 1
            else:
                break
    return {"checked_portfolios": checked, "telegram_sent": sent,
            "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
            "time": time.strftime("%Y-%m-%d %H:%M:%S %Z")}


@app.get("/api/backtest")
def backtest(
    criteria: str = Query("swing", pattern="^(scalping|bsjp|swing)$"),
    universe: str = Query("liquid", pattern="^(all|liquid)$"),
    years: int = Query(2, ge=1, le=5),
    limit: int = Query(20, ge=1, le=100),
    confirm: bool = Query(True, description="Konfirmasi ala buku: harga>SMA20, RSI<70, volume>MA20"),
    tickers_param: str = Query("", alias="tickers",
                               description="Daftar kode kustom dipisah koma (maks 45); menimpa universe"),
):
    """Estimasi win rate historis per kriteria screener (eduksi, bukan jaminan masa depan).
    Sinyal -> entry di harga tutup, SL 2xATR, TP 2R (RRR 1:2), hold maks 5/20 hari.
    confirm=1 menambahkan konfirmasi ala buku (harga>SMA20, RSI<70, volume>rata-rata).
    Kriteria 'bandar' tidak dapat diuji: Broker Summary hanya snapshot hari ini."""
    if tickers_param:
        tickers = [f"{t.strip().upper()}.JK" if "." not in t.strip().upper() else t.strip().upper()
                   for t in tickers_param.split(",") if t.strip()][:45]
    else:
        tickers = load_idx_tickers(universe)
        tickers = tickers[:100] if universe == "all" else tickers[:limit]
    results: List[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(lambda t: _backtest_one(t, criteria, years, confirm), tickers):
            if r and r.get("trades", 0) > 0:
                results.append(r)
    tot_trades = sum(r["trades"] for r in results)
    tot_wins = sum(r["wins"] for r in results)
    tot_losses = sum(r["losses"] for r in results)
    tot_timeouts = sum(r["timeouts"] for r in results)
    decided = tot_wins + tot_losses
    results.sort(key=lambda r: r["trades"], reverse=True)
    avg_r = (sum(r["avg_r"] or 0 for r in results) / len(results)
             if results else None)
    return {
        "criteria": criteria,
        "years": years,
        "confirm": bool(confirm),
        "tickers_checked": len(tickers),
        "tickers_with_signals": len(results),
        "total_trades": tot_trades,
        "wins": tot_wins,
        "losses": tot_losses,
        "timeouts": tot_timeouts,
        "win_rate_pct": num(tot_wins / decided * 100, 1) if decided else None,
        "avg_r": num(avg_r, 2),
        "per_ticker": results[:15],
        "note": ("Backtest sederhana: entry harga tutup saat sinyal, SL 2xATR, TP 2R "
                 "(RRR 1:2 sesuai buku), hold maks 5 hari (scalping/BSJP) / 20 hari (swing). "
                 "Tidak memperhitungkan biaya/slippage/aksi korporasi dan rentan survivorship bias. "
                 "Kinerja masa lalu BUKAN jaminan masa depan. Kriteria 'bandar' tidak diuji: "
                 "Broker Summary hanya snapshot hari ini tanpa riwayat."),
        "disclaimer": DISCLAIMER,
    }


class BrokerRow(BaseModel):
    broker: str
    volume: float = 0.0
    avg_price: float = 0.0
    value: Optional[float] = None


class BrokerSummaryPayload(BaseModel):
    last_price: float
    buyers: List[BrokerRow]
    sellers: List[BrokerRow] = []


@app.post("/api/bandarmology/analyze")
def bandarmology_analyze(payload: BrokerSummaryPayload):
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    result = analyze_broker_summary(data)
    result["disclaimer"] = DISCLAIMER
    return result


class ScreenerRequest(BaseModel):
    tickers: List[str]
    criteria: str = "all"
    period: str = "3mo"
    include_signal: bool = True
    include_bandarmology: bool = True


@app.get("/api/screener/tickers")
def screener_tickers(universe: str = Query("all", pattern="^(all|liquid)$")):
    """Daftar kode saham IDX yang akan discan (all = seluruh emiten, liquid = LQ45)."""
    tickers = load_idx_tickers(universe)
    return {
        "universe": universe,
        "total": len(tickers),
        "tickers": tickers,
        "note": "Sumber: dataset publik IDX (fallback LQ45). Suffix .JK untuk yfinance.",
    }


@app.get("/api/screener")
def screener(
    criteria: str = Query("all", pattern="^(all|swing|scalping|bsjp|bandar)$"),
    universe: str = Query("liquid", pattern="^(all|liquid)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    period: str = Query("3mo", pattern="^(1mo|3mo|6mo|1y)$"),
    include_signal: bool = Query(True),
    include_bandarmology: bool = Query(True),
):
    """Scan saham dengan kriteria screener Coachinvestasi.

    Gunakan offset/limit berulang-ulang untuk memindai SELURUH kode saham
    (total_tickers & next_offset disediakan untuk paginasi).
    """
    all_tickers = load_idx_tickers(universe)
    window = all_tickers[offset:offset + limit]
    if not window:
        raise HTTPException(404, "Offset melebihi jumlah ticker.")

    scan = _scan(window, criteria, period, include_signal, include_bandarmology)
    return {
        "criteria": criteria,
        "universe": universe,
        "period": period,
        "requested": len(window),
        "scanned": scan["scanned"],
        "skipped": scan["skipped"],
        "bandarmology_checked": scan["bandarmology_checked"],
        "total_tickers": len(all_tickers),
        "next_offset": offset + limit if offset + limit < len(all_tickers) else None,
        "results": scan["results"],
        "bandarmology_note": (
            "Broker Summary & akumulasi bandar aktif dari IDX Edge PRO (kuota ~1000 req/hari/key; "
            "data dicache 6-12 jam; broker summary hanya diambil untuk saham yang lolos)."
            if IDX_EDGE_API_KEYS else
            "Broker Summary API belum dikonfigurasi (set IDX_EDGE_API_KEYS di Vercel); "
            "kriteria swing memakai proksi nilai transaksi."
        ),
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/chart/{ticker}")
def chart(
    ticker: str,
    period: str = Query("1y", pattern="^(1mo|3mo|6mo|1y|2y|5y)$"),
    limit: int = Query(120, ge=20, le=500),
    interval: str = Query("daily", pattern="^(daily|intraday)$"),
):
    """Data OHLCV grafik. interval=daily -> data harian (sumber sama dengan analyze);
    interval=intraday -> bar 5m hari ini dari Yahoo (curl_cffi, cache 45 dtk)."""
    if interval == "intraday":
        data = fetch_intraday(ticker)
        if not data:
            raise HTTPException(502, detail=(
                "Data intraday tidak tersedia saat ini (Yahoo sedang membatasi akses). "
                "Gunakan interval=daily untuk data harian."))
        meta = data.get("meta") or {}
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        last = meta.get("regularMarketPrice") or data["bars"][-1]["close"]
        return {
            "ticker": ticker.upper(),
            "interval": "intraday",
            "source": "yahoo-live",
            "market_state": meta.get("marketState"),
            "is_market_open": meta.get("marketState") == "REGULAR",
            "last_price": num(last, 2),
            "previous_close": num(prev, 2) if prev else None,
            "change_pct": num((last / prev - 1) * 100, 2) if prev and prev > 0 else None,
            "as_of": meta.get("regularMarketTime"),
            "bars": data["bars"][-150:],
            "disclaimer": DISCLAIMER,
        }
    df = fetch_data(ticker, period)
    n = min(limit, len(df))
    sub = df.tail(n)
    bars = [{
        "date": str(idx.date()) if hasattr(idx, "date") else str(idx),
        "open": num(r["Open"], 2), "high": num(r["High"], 2),
        "low": num(r["Low"], 2), "close": num(r["Close"], 2),
        "volume": num(r["Volume"], 0),
    } for idx, r in sub.iterrows()]
    return {
        "ticker": ticker.upper(),
        "interval": "daily",
        "source": df.attrs.get("source", "yfinance"),
        "data_date": str(sub.index[-1].date()) if hasattr(sub.index[-1], "date") else str(sub.index[-1]),
        "bars": bars,
        "disclaimer": DISCLAIMER,
    }


@app.post("/api/screener")
def screener_post(payload: ScreenerRequest):
    """Scan daftar ticker khusus (bisa dari hasil screener lain / watchlist manual)."""
    if not payload.tickers:
        raise HTTPException(422, "List tickers tidak boleh kosong.")
    tickers = [t.upper() if "." in t else f"{t.upper()}.JK" for t in payload.tickers][:100]
    scan = _scan(tickers, payload.criteria, payload.period,
                 payload.include_signal, payload.include_bandarmology)
    return {
        "criteria": payload.criteria,
        "period": payload.period,
        "requested": len(tickers),
        "scanned": scan["scanned"],
        "skipped": scan["skipped"],
        "bandarmology_checked": scan["bandarmology_checked"],
        "results": scan["results"],
        "bandarmology_note": (
            "Broker Summary & akumulasi bandar aktif dari IDX Edge PRO." if IDX_EDGE_API_KEYS else
            "Broker Summary API belum dikonfigurasi (set IDX_EDGE_API_KEYS di Vercel); "
            "kriteria swing memakai proksi nilai transaksi."
        ),
        "disclaimer": DISCLAIMER,
    }