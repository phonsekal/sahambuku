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
import time
import urllib.request
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
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
    key = (ticker.upper(), period)
    now = time.time()
    hit = CACHE.get(key)
    if hit and now - hit["ts"] < CACHE_TTL_SECONDS:
        return hit["df"]
    try:
        df = yf.download(ticker, period=period, interval="1d",
                         auto_adjust=True, progress=False, threads=False)
    except Exception as exc:
        msg = str(exc).lower()
        if "rate" in msg or "too many" in msg:
            raise HTTPException(
                429,
                detail=f"yfinance sedang rate-limited untuk {ticker}. Coba lagi beberapa saat kemudian.",
            )
        raise HTTPException(502, detail=f"yfinance gagal mengunduh data {ticker}: {exc}")
    if df is None or df.empty:
        raise HTTPException(404, detail=(
            f"Data tidak ditemukan untuk ticker '{ticker}'. Periksa kode saham "
            "(mis. BBCA.JK, TLKM.JK, AAPL, TSLA)."
        ))
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).tail(500)
    if len(df) < 30:
        raise HTTPException(422, detail="Data historis terlalu sedikit untuk analisis (min 30 bar).")
    CACHE[key] = {"ts": now, "df": df}
    return df


# ---------------------------------------------------------------------------
# 6. SCREENER SELURUH SAHAM IDX + INTEGRASI BROKER SUMMARY API
# ---------------------------------------------------------------------------

IDX_TICKER_CSV_URL = (
    "https://raw.githubusercontent.com/wildangunawan/Dataset-Saham-IDX/"
    "master/List%20Emiten/all.csv"
)

# Universe "liquid": konstituen LQ45 (dipakai sebagai fallback & mode cepat)
IDX_LIQUID_TICKERS = [
    "ACES", "ADRO", "AKRA", "AMRT", "ANTM", "ARTO", "ASII", "BBCA", "BBNI", "BBRI",
    "BBTN", "BMRI", "BRPT", "CPIN", "CTRA", "ESSA", "EXCL", "GGRM", "HRUM", "ICBP",
    "INCO", "INDF", "INKP", "ISAT", "ITMG", "JPFA", "JSMR", "KLBF", "MAPI", "MDKA",
    "MEDC", "PGAS", "PTBA", "SIDO", "SMGR", "TLKM", "TOWR", "UNTR", "UNVR", "BRIS",
    "MAPA", "GOTO", "PGEO", "MBMA", "AMMN",
]

# Broker Summary API eksternal (opsional). Aktif jika BROKER_SUMMARY_API_URL di-set.
BROKER_API_URL = os.environ.get("BROKER_SUMMARY_API_URL", "").strip().rstrip("/")
BROKER_API_KEY = os.environ.get("BROKER_SUMMARY_API_KEY", "").strip()

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


def _download_batch(tickers: List[str], period: str) -> Dict[str, pd.DataFrame]:
    """Unduh OHLCV beberapa ticker dalam satu panggilan Yahoo (lebih cepat & hemat rate-limit)."""
    if not tickers:
        return {}
    try:
        raw = yf.download(tickers, period=period, interval="1d", auto_adjust=True,
                          group_by="ticker", progress=False, threads=False)
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
        "price_ge_50": bool(m["last"] >= 50),
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


def _normalize_bs_rows(rows: Any) -> List[dict]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append({
            "broker": str(r.get("broker") or r.get("Broker") or r.get("code") or "?"),
            "volume": float(r.get("volume") or r.get("Volume") or r.get("lot") or 0),
            "avg_price": float(r.get("avg_price") or r.get("AvgPrice") or r.get("avg") or 0),
            "value": float(r.get("value") or r.get("Value") or 0) or None,
        })
    return out


def fetch_broker_summary(ticker: str) -> Optional[dict]:
    """Ambil Broker Summary dari API eksternal (aktif jika BROKER_SUMMARY_API_URL di-set).

    Kontrak API (GET):
      {BROKER_SUMMARY_API_URL}/broker-summary/{TICKER}?days=30
    Respons JSON:
      {
        "ticker": "BBCA", "date": "2026-09-09", "last_price": 10100,
        "buyers":  [{"broker": "RX", "volume": 382000, "avg_price": 10100}],
        "sellers": [{"broker": "XL", "volume": 100000, "avg_price": 10050}],
        "history": [{"date": "2026-09-09", "bandar_buy_value": 382000000000}, ...]
      }
    "history" bersifat opsional; dipakai untuk deret BandarValue kriteria swing.
    """
    if not BROKER_API_URL:
        return None
    code = ticker.upper().replace(".JK", "")
    url = f"{BROKER_API_URL}/broker-summary/{code}?days=30"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        if BROKER_API_KEY:
            req.add_header("Authorization", f"Bearer {BROKER_API_KEY}")
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    last_price = data.get("last_price") or data.get("lastPrice") or data.get("close")
    payload = {
        "last_price": float(last_price) if last_price else 0.0,
        "buyers": _normalize_bs_rows(data.get("buyers")),
        "sellers": _normalize_bs_rows(data.get("sellers")),
    }
    analysis = analyze_broker_summary(payload)

    history = data.get("history") or []
    bandar_series: List[float] = []
    for h in history:
        if isinstance(h, dict):
            v = h.get("bandar_buy_value") or h.get("bandarValue") or h.get("value")
            if v is not None:
                bandar_series.append(float(v))
    analysis["bandar_series"] = bandar_series
    analysis["source"] = BROKER_API_URL
    return analysis


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


def _scan(tickers: List[str], criteria: str, period: str, include_signal: bool) -> dict:
    matched: List[dict] = []
    scanned = skipped = bandar_used = 0
    for i in range(0, len(tickers), 10):
        chunk = tickers[i:i + 10]
        frames = _download_batch(chunk, period)
        for tk in chunk:
            df = frames.get(tk)
            if df is None:
                skipped += 1
                continue
            scanned += 1
            bs = None
            bandar_series = None
            if BROKER_API_URL:
                bs = fetch_broker_summary(tk)
                if bs:
                    bandar_used += 1
                    bandar_series = bs.get("bandar_series") or None
            result = run_screener(df, criteria, bandar_series)
            item = {"ticker": tk, **result["metrics"], "criteria_met": result["criteria_met"]}
            if include_signal:
                item["signal"] = quick_signal(df)
            if bs:
                item["bandarmology"] = {
                    "status": bs.get("status"),
                    "scenario": bs.get("scenario"),
                    "bandar_avg_price": bs.get("bandar_avg_price"),
                    "top_buyer_value_share_pct": bs.get("top_buyer_value_share_pct"),
                }
            if result["eligible"]:
                matched.append(item)
        time.sleep(0.1)
    return {
        "scanned": scanned,
        "skipped": skipped,
        "bandarmology_checked": bandar_used,
        "results": matched,
    }


# ---------------------------------------------------------------------------
# 7. FASTAPI APP
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CoachInvestasi Strategy API",
    description="API analisis saham: Technical Analysis + Bandarmology ala Coach Investasi 2025-2026.",
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
        "service": "CoachInvestasi Strategy API",
        "endpoints": [
            "GET  /api/health",
            "GET  /api/analyze/{ticker}?period=1y",
            "POST /api/bandarmology/analyze",
            "GET  /api/screener/tickers?universe=all|liquid",
            "GET  /api/screener?criteria=all|swing|scalping|bsjp&universe=liquid|all&limit=20&offset=0",
            "POST /api/screener",
        ],
        "docs": "/docs",
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "CoachInvestasi Strategy API",
        "time": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


@app.get("/api/analyze/{ticker}")
def analyze(
    ticker: str,
    period: str = Query("1y", pattern="^(1mo|3mo|6mo|1y|2y|5y)$"),
    risk_amount: float = Query(5_000_000, gt=0),
):
    df = fetch_data(ticker, period)
    close = df["Close"]
    last_price = float(close.iloc[-1])
    prev_price = float(close.iloc[-2])
    change_pct = (last_price / prev_price - 1) * 100 if prev_price > 0 else None

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
        "bandarmology_note": BANDARMOLOGY_NOTE,
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
    criteria: str = Query("all", pattern="^(all|swing|scalping|bsjp)$"),
    universe: str = Query("liquid", pattern="^(all|liquid)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    period: str = Query("3mo", pattern="^(1mo|3mo|6mo|1y)$"),
    include_signal: bool = Query(True),
):
    """Scan saham dengan kriteria screener Coachinvestasi.

    Gunakan offset/limit berulang-ulang untuk memindai SELURUH kode saham
    (total_tickers & next_offset disediakan untuk paginasi).
    """
    all_tickers = load_idx_tickers(universe)
    window = all_tickers[offset:offset + limit]
    if not window:
        raise HTTPException(404, "Offset melebihi jumlah ticker.")

    scan = _scan(window, criteria, period, include_signal)
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
            "Broker Summary API aktif (BROKER_SUMMARY_API_URL ter-set)." if BROKER_API_URL else
            "Broker Summary API belum dikonfigurasi (set BROKER_SUMMARY_API_URL); "
            "kriteria swing memakai proksi nilai transaksi."
        ),
        "disclaimer": DISCLAIMER,
    }


@app.post("/api/screener")
def screener_post(payload: ScreenerRequest):
    """Scan daftar ticker khusus (bisa dari hasil screener lain / watchlist manual)."""
    if not payload.tickers:
        raise HTTPException(422, "List tickers tidak boleh kosong.")
    tickers = [t.upper() if "." in t else f"{t.upper()}.JK" for t in payload.tickers][:100]
    scan = _scan(tickers, payload.criteria, payload.period, payload.include_signal)
    return {
        "criteria": payload.criteria,
        "period": payload.period,
        "requested": len(tickers),
        "scanned": scan["scanned"],
        "skipped": scan["skipped"],
        "bandarmology_checked": scan["bandarmology_checked"],
        "results": scan["results"],
        "bandarmology_note": (
            "Broker Summary API aktif (BROKER_SUMMARY_API_URL ter-set)." if BROKER_API_URL else
            "Broker Summary API belum dikonfigurasi (set BROKER_SUMMARY_API_URL); "
            "kriteria swing memakai proksi nilai transaksi."
        ),
        "disclaimer": DISCLAIMER,
    }