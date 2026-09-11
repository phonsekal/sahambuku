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
  GET  /api/fundamentals/{ticker}   -> fundamental (dimuat saat tombol diklik; hemat kuota)
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
import urllib.error
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


def bollinger_bands(series: pd.Series, period: int = 20, mult: float = 2.0):
    """Bollinger Bands (20, 2): middle = SMA(period), band = SMA +/- mult*std.

    Konvensi John Bollinger: deviasi standar dihitung dari populasi (ddof=0).
    """
    mid = series.rolling(window=period, min_periods=period).mean()
    sd = series.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + mult * sd
    lower = mid - mult * sd
    return upper, mid, lower


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


BUY_SCORE_WEIGHTS = {
    "Trend naik (harga > SMA20/SMA50)": 20,
    "Golden Cross SMA20/50 (<=5 hari)": 10,
    "RSI di zona sehat (40-65, bonus 50-60)": 15,
    "MACD bullish (hist > 0 & naik)": 10,
    "Volume > VolumeMA20": 10,
    "Pola candlestick bullish": 10,
    "Dekat support (<=2x ATR)": 10,
    "Momentum 5 hari sehat (0-12%)": 5,
    "Launch Pad / Drop Base Rally": 10,
    "Bandarmology ACC (bila ada)": 15,
    "Likuiditas (nilai rata-rata 20 hari)": 10,
}


def _buy_score_series(df: pd.DataFrame) -> pd.Series:
    """Skor komposit sinyal beli 0-100 per bar (vektor, untuk backtest).

    Komponen yang bisa dihitung vektor; tanpa S&R cluster & bandarmology
    (tidak ada riwayat). Dipakai juga oleh compute_buy_score untuk bar terakhir.
    """
    close = df["Close"].astype(float)
    vol = df["Volume"].astype(float)
    o = df["Open"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    s20 = sma(close, 20)
    s50 = sma(close, 50)
    r = rsi(close, 14)
    _, _, hist = macd(close)
    vma = sma(vol, 20)
    atr14 = atr(df, 14)
    ret5 = close.pct_change(5) * 100

    golden = (s20 > s50) & (s20.shift(1) <= s50.shift(1))
    gc5 = golden.rolling(5).max().fillna(0)

    body = (close - o).abs()
    rng = (h - l)
    lo = pd.concat([o, close], axis=1).min(axis=1)
    hi = pd.concat([o, close], axis=1).max(axis=1)
    lower_sh = lo - l
    upper_sh = h - hi
    hammer = (rng > 0) & (lower_sh >= 2 * body) & (upper_sh <= 0.35 * body)
    engulf = (close.shift(1) < o.shift(1)) & (close > o) & (body > body.shift(1)) & (body.shift(1) > 0)
    candle = (hammer | engulf).fillna(False).astype(float)

    low20 = close.rolling(20).min()
    near_low = ((close - low20) <= 2 * atr14).fillna(False).astype(float)

    pts = pd.Series(0.0, index=df.index)
    pts = pts + np.where((close > s20).fillna(False), 12, 0)
    pts = pts + np.where((close > s50).fillna(False), 8, 0)
    pts = pts + gc5 * 10
    pts = pts + np.where((r >= 40) & (r <= 65), 10, np.where((r >= 30) & (r < 40), 5, 0))
    pts = pts + np.where((r >= 50) & (r <= 60), 5, 0)
    pts = pts + np.where((hist > 0) & (hist >= hist.shift(1)), 10, np.where(hist > 0, 5, 0))
    pts = pts + np.where(vol > vma, 10, np.where(vol > 0.8 * vma, 5, 0))
    pts = pts + candle * 10
    pts = pts + near_low * 10
    pts = pts + np.where((ret5 >= 0) & (ret5 <= 12), 5, np.where((ret5 > 12) & (ret5 <= 20), 2, 0))
    # Likuiditas: saham dengan nilai transaksi rata-rata tinggi = mudah masuk/keluar.
    val20 = _value_series(df).rolling(20).mean()
    pts = pts + np.where(val20 >= 10e9, 10, np.where(val20 >= 1e9, 7,
                                                     np.where(val20 >= 100e6, 4, 0)))
    return pts.clip(upper=100)


def compute_buy_score(df: pd.DataFrame, bandarmology: Optional[dict] = None) -> dict:
    """Skor komposit KUALITAS BELI 0-100 untuk bar terakhir (optimasi screener & analisis).

    Gabungan konfirmasi ala buku (trend, cross, RSI, MACD, volume, candlestick,
    dekat support, momentum, special pattern) + bandarmology ACC bila tersedia.
    BEDA dari sinyal (BUY/SELL/HOLD): skor menilai KUALITAS saham, sinyal menilai
    momentum saat ini — skor tinggi + sinyal SELL = saham kuat sedang koreksi.
    Label: >=70 KUALITAS BELI KUAT · 50-69 KUALITAS BELI (KONFIRMASI) ·
    30-49 KUALITAS NETRAL · <30 KUALITAS HINDARI.
    """
    s = _buy_score_series(df)
    score = float(s.iloc[-1]) if len(s) else 0.0

    comps: Dict[str, float] = {}
    close = df["Close"].astype(float)
    last = float(close.iloc[-1])
    n = len(df)
    ret5 = (last / float(close.iloc[-6]) - 1) * 100 if n >= 6 and close.iloc[-6] > 0 else 0.0
    atr14 = float(atr(df, 14).iloc[-1]) or last * 0.02
    cr = cross_events(df)

    pts = 0.0
    t = 0.0
    s20 = float(sma(close, 20).iloc[-1]); s50 = float(sma(close, 50).iloc[-1])
    if not np.isnan(s20) and last > s20:
        t += 12
    if not np.isnan(s50) and last > s50:
        t += 8
    comps["Trend naik (harga > SMA20/SMA50)"] = t; pts += t

    g = 10.0 if cr.get("golden_cross_days_ago") is not None else 0.0
    comps["Golden Cross SMA20/50 (<=5 hari)"] = g; pts += g

    r = float(rsi(close, 14).iloc[-1])
    rp = 0.0
    if 40 <= r <= 65:
        rp = 10.0
    elif 30 <= r < 40 or 65 < r <= 70:
        rp = 5.0
    if 50 <= r <= 60:
        rp = min(rp + 5.0, 15.0)
    comps["RSI di zona sehat (40-65, bonus 50-60)"] = rp; pts += rp

    macd_line, sig_line, hist = macd(close)
    h_now = float(hist.iloc[-1]); h_prev = float(hist.iloc[-2]) if n >= 2 else h_now
    m = 10.0 if h_now > 0 and h_now >= h_prev else (5.0 if h_now > 0 else 0.0)
    comps["MACD bullish (hist > 0 & naik)"] = m; pts += m

    vol_s = df["Volume"].astype(float)
    vma = float(sma(vol_s, 20).iloc[-1]) if n >= 20 else float(vol_s.mean())
    vr = float(vol_s.iloc[-1]) / vma if vma > 0 else 0.0
    v = 10.0 if vr > 1.0 else (5.0 if vr > 0.8 else 0.0)
    comps["Volume > VolumeMA20"] = v; pts += v

    pats = detect_candlestick_patterns(df).get("patterns", [])
    cnd = 10.0 if any(p["type"] == "bullish" for p in pats) else 0.0
    comps["Pola candlestick bullish"] = cnd; pts += cnd

    sr = find_sr_zones(df)
    supports = [z["price"] for z in sr if z["type"] == "support" and z["price"] < last]
    near = 10.0 if supports and (last - max(supports)) <= 2 * atr14 else 0.0
    comps["Dekat support (<=2x ATR)"] = near; pts += near

    mo = 5.0 if 0 <= ret5 <= 12 else (2.0 if 12 < ret5 <= 20 else 0.0)
    comps["Momentum 5 hari sehat (0-12%)"] = mo; pts += mo

    lp = launch_pad(df)
    dbr = drop_base_rally(df)
    sp = 10.0 if (lp.get("detected") or dbr.get("detected")) else 0.0
    comps["Launch Pad / Drop Base Rally"] = sp; pts += sp

    bd = 0.0
    bstatus = str((bandarmology or {}).get("status") or "")
    if bstatus.startswith("ACC"):
        bd = 15.0
    comps["Bandarmology ACC (bila ada)"] = bd; pts += bd

    val20 = float(_value_series(df).rolling(20).mean().iloc[-1]) if n >= 20 else float(_value_series(df).mean())
    liq = 10.0 if val20 >= 10e9 else (7.0 if val20 >= 1e9 else (4.0 if val20 >= 100e6 else 0.0))
    comps["Likuiditas (nilai rata-rata 20 hari)"] = liq; pts += liq

    score = min(100.0, pts)
    label = ("KUALITAS BELI KUAT" if score >= 70 else
             "KUALITAS BELI (KONFIRMASI)" if score >= 50 else
             "KUALITAS NETRAL" if score >= 30 else "KUALITAS HINDARI")
    return {
        "score": num(score, 0),
        "label": label,
        "components": comps,
        "rsi14": num(r, 1),
        "ret5_pct": num(ret5, 2),
        "volume_ratio": num(vr, 2),
        "liquidity_grade": _liquidity_grade(val20),
        "note": "Skor komposit KUALITAS BELI (0-100): >=70 KUALITAS BELI KUAT, 50-69 KUALITAS BELI (KONFIRMASI), <50 tunggu. Berbeda dari Sinyal (BUY/SELL/HOLD): skor menilai kualitas saham, sinyal menilai momentum saat ini.",
    }


def _value_series(df: pd.DataFrame) -> pd.Series:
    """Nilai transaksi harian (IDR). Prioritas kolom Value (IDX Edge/GitHub),
    fallback proksi Close x Volume."""
    if "Value" in df.columns:
        v = df["Value"].astype(float)
        if v.notna().sum() >= len(df) * 0.8:
            return v.fillna(0.0)
    return df["Close"].astype(float) * df["Volume"].astype(float)


def _liquidity_grade(value_20d: float) -> str:
    """Kelas likuiditas berdasar nilai transaksi rata-rata 20 hari (IDR).

    Sangat likuid >= Rp 10 miliar/hari; Likuid >= 1 miliar; Cukup >= 100 juta;
    Kurang likuid < 100 juta (berisiko sulit entry/exit tanpa mempengaruhi harga).
    """
    if value_20d >= 10e9:
        return "SANGAT LIKUID"
    if value_20d >= 1e9:
        return "LIKUID"
    if value_20d >= 100e6:
        return "CUKUP"
    return "KURANG LIKUID"


def _liquidity_metrics(df: pd.DataFrame) -> dict:
    """Metrik likuiditas: rata-rata nilai & volume 20 hari + kelas likuiditas."""
    value = _value_series(df)
    vol = df["Volume"].astype(float)
    avg_value = float(value.rolling(20).mean().iloc[-1]) if len(df) >= 20 else float(value.mean())
    avg_vol = float(vol.rolling(20).mean().iloc[-1]) if len(df) >= 20 else float(vol.mean())
    grade = _liquidity_grade(avg_value)
    return {
        "avg_value_20d": avg_value,
        "avg_volume_20d": avg_vol,
        "grade": grade,
        "note": ("Kelas likuiditas dari nilai transaksi rata-rata 20 hari: "),
    }


def risk_management(last_price: float, sr_zones: List[dict], action: str, atr_value: float,
                    risk_amount: float = 5_000_000.0, force_long: bool = False) -> dict:
    """Risk management (Bab 8): SL di luar S&R, TP di S&R berikutnya, RRR min 1:2, position sizing.

    force_long=True => TP/SL selalu arah long (TP di atas, SL di bawah). Dipakai untuk
    portofolio & notifikasi karena pemegang saham (long) tidak relevan dengan TP/SL
    arah short: sebelumnya saat sinyal SELL, TP berada di BAWAH harga sehingga alert
    "TP tercapai" menyala terus dan bergantian dengan "SL tersentuh" (bug notif).
    """
    atr_v = atr_value if atr_value and atr_value > 0 else last_price * 0.02
    supports = sorted([z["price"] for z in sr_zones if z["type"] == "support" and z["price"] < last_price], reverse=True)
    resistances = sorted([z["price"] for z in sr_zones if z["type"] == "resistance" and z["price"] > last_price])

    if action in ("SELL", "STRONG SELL") and not force_long:
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


# --- Konsistensi sumber data ---
# Sinyal harus stabil untuk data yang sama. Bila yfinance sempat rate-limited lalu
# analisis berpindah ke IDX Edge (harga mentah), indikator bisa berubah walau pasar
# belum buka. Karena itu sumber yang berhasil untuk suatu saham dipakai ulang
# sepanjang hari bursa (disimpan di Upstash bila sinkronisasi aktif).
_SOURCE_PREF: Dict[str, dict] = {}
SOURCE_PREF_PREFIX = "ci:tsrc:"
STICKY_SOURCES = ("yfinance", "idx-edge-pro")


def _today_wib() -> str:
    """Tanggal WIB (UTC+7) sebagai batas hari bursa IDX."""
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 7 * 3600))


def _source_pref_get(ticker: str) -> Optional[str]:
    """Sumber data yang sudah dipakai hari ini untuk ticker ini (None bila belum)."""
    tk = ticker.upper()
    rec = _SOURCE_PREF.get(tk)
    if not (isinstance(rec, dict) and rec.get("date") == _today_wib()):
        rec = None
        if SYNC_ENABLED:
            raw = _upstash_get(SOURCE_PREF_PREFIX + tk)
            if raw:
                try:
                    cand = json.loads(raw)
                except Exception:
                    cand = None
                if isinstance(cand, dict) and cand.get("date") == _today_wib():
                    rec = cand
                    _SOURCE_PREF[tk] = cand
    src = (rec or {}).get("src")
    # Dataset GitHub (bukan real-time) tidak dipaku: begitu sumber real-time
    # pulih, data harus kembali mutakhir.
    return src if src in STICKY_SOURCES else None


def _source_pref_set(ticker: str, source: str) -> None:
    """Catat sumber data hari ini agar analisis berikutnya tetap memakainya."""
    base = (source or "").split(" ")[0].strip()
    if base not in STICKY_SOURCES:
        return
    rec = {"src": base, "date": _today_wib()}
    _SOURCE_PREF[ticker.upper()] = rec
    if SYNC_ENABLED:
        _upstash_set(SOURCE_PREF_PREFIX + ticker.upper(), json.dumps(rec), ttl=86400)


def fetch_data(ticker: str, period: str, yf_timeout: Optional[float] = None) -> pd.DataFrame:
    """Unduh OHLCV dengan fallback berantai:
    1) yfinance (real-time, dibatasi timeout) -> 2) IDX Edge PRO -> 3) dataset GitHub IDX.
    Kode tanpa titik (mis. TLKM) otomatis dicoba dengan suffix .JK bila gagal.
    Sumber akhir dicatat di df.attrs['source'].

    Harga diambil APA ADANYA (auto_adjust=False) supaya nilainya sama dengan
    IDX Edge/dataset dan dengan chart di platform broker; kalau memakai harga
    hasil penyesuaian dividen, support/resistance & sinyal ikut bergeser saat
    sumber data berpindah.

    yf_timeout: batas waktu khusus utk yfinance (None = YFINANCE_TIMEOUT global).
    Jalur backtest/matriks memakai timeout lebih pendek karena di Vercel yfinance
    hampir selalu diblokir; menunggu 25 dtk per ticker membuat matriks LQ45
    melewati batas durasi function (504).
    """
    key = (ticker.upper(), period)
    now = time.time()
    hit = CACHE.get(key)
    if hit and now - hit["ts"] < CACHE_TTL_SECONDS:
        return hit["df"]
    yf_timeout = yf_timeout if yf_timeout is not None else YFINANCE_TIMEOUT
    prefer = _source_pref_get(ticker)

    def _attempt(tk: str):
        """Coba satu varian ticker; kembalikan (df, source, yf_err).

        prefer (jika ada) dicoba lebih dulu agar sumber data tidak berpindah dan
        sinyal tidak berubah tanpa data baru.
        """
        yf_err = None
        if prefer == "idx-edge-pro" and IDX_EDGE_API_KEYS:
            df_edge = fetch_idx_history(tk)
            if df_edge is not None and not df_edge.empty:
                return df_edge, "idx-edge-pro", None
        try:
            df = _call_with_timeout(
                lambda: yf.download(tk, period=period, interval="1d",
                                    auto_adjust=False, progress=False, threads=False),
                yf_timeout,
            )
            if df is None:
                yf_err = "yfinance timeout / tidak ada data"
        except Exception as exc:
            df = None
            yf_err = str(exc)

        source = "yfinance"
        if df is None or df.empty:
            if IDX_EDGE_API_KEYS and prefer != "idx-edge-pro":
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
    _source_pref_set(ticker, source)
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
IDX_EDGE_BROKER_TTL = 24 * 3600     # broker summary/akumulasi dicache 24 jam (data harian ~17:00 WIB)

# Circuit breaker kuota harian IDX Edge (1000 req/hari/key): begitu API menjawab
# "kuota habis", semua panggilan berikutnya di-short-circuit sampai reset (WIB).
IDX_EDGE_QUOTA_UNTIL = 0.0


def _idx_edge_quota_until() -> float:
    """Epoch waktu reset kuota harian (tengah malam WIB / UTC+7)."""
    try:
        import datetime
        now = datetime.datetime.utcnow() + datetime.timedelta(hours=7)
        nxt = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return (nxt - datetime.timedelta(hours=7)).timestamp()
    except Exception:
        return time.time() + 6 * 3600


_IDX_QUOTA_CACHE_TS = 0.0
_IDX_QUOTA_CACHE_VAL = False


def _quota_persist() -> None:
    """Simpan status kuota habis sampai tengah malam WIB (berlaku lintas instance)."""
    if SYNC_ENABLED:
        ttl = max(60, int(_idx_edge_quota_until() - time.time()))
        _upstash_set("ci:idx_quota_out", "1", ttl=ttl)


def idx_edge_quota_out() -> bool:
    """True jika kuota harian API IDX Edge sedang habis (sampai tengah malam WIB).

    Cek memori dulu; bila belum tahu, tanya Upstash (state persisten lintas
    instance/cold start) maksimal 1x per menit.
    """
    global IDX_EDGE_QUOTA_UNTIL, _IDX_QUOTA_CACHE_TS, _IDX_QUOTA_CACHE_VAL
    if time.time() < IDX_EDGE_QUOTA_UNTIL:
        return True
    if time.time() - _IDX_QUOTA_CACHE_TS > 60:
        _IDX_QUOTA_CACHE_TS = time.time()
        _IDX_QUOTA_CACHE_VAL = bool(_upstash_get("ci:idx_quota_out"))
        if _IDX_QUOTA_CACHE_VAL:
            IDX_EDGE_QUOTA_UNTIL = _idx_edge_quota_until()
    return _IDX_QUOTA_CACHE_VAL


def _bandarmology_note(ticker: str = "") -> str:
    """Catatan jujur kenapa data bandarmology tidak tampil (bukan instruksi membingungkan)."""
    if idx_edge_quota_out():
        return ("Kuota harian API Broker Summary sudah habis hari ini — "
                "bandarmology otomatis kembali besok. Bisa tambah limit di "
                "stock.arjum.com → Usage Analytics & Limit.")
    if not IDX_EDGE_API_KEYS:
        return BANDARMOLOGY_NOTE
    t = strip_suffix(ticker) if ticker else ""
    return (f"Data Broker Summary IDX belum tersedia untuk {t} saat ini — bisa karena "
            "data baru dirilis ~17:00 WIB atau saham kurang likuid.")


def _screener_bandar_note() -> str:
    """Catatan bandarmology untuk hasil screener (tahu kondisi kuota API)."""
    if idx_edge_quota_out():
        return ("Kuota harian API Broker Summary sudah habis hari ini — "
                "kriteria BANDAR & kolom bandarmology aktif kembali besok.")
    if IDX_EDGE_API_KEYS:
        return ("Broker Summary & akumulasi bandar aktif dari IDX Edge PRO (kuota "
                "sesuai paket akun; data dicache 24 jam; broker summary hanya "
                "diambil untuk saham yang lolos).")
    return ("Broker Summary API belum dikonfigurasi (set IDX_EDGE_API_KEYS di Vercel); "
            "kriteria swing memakai proksi nilai transaksi.")

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


def _normalize_holdings(portfolio: Optional[list]) -> Optional[list]:
    """Normalisasi posisi: qty SELALU disimpan dalam LEMBAR.

    Data lama (tanpa field `unit`) disimpan dari form yang berlabel "lot" sehingga
    isinya LOT -> kalikan 100 (1 lot = 100 lembar) agar nilai portofolio benar.
    Idempoten: posisi yang sudah punya `unit` tidak diubah.
    """
    if not portfolio:
        return portfolio
    out: List[dict] = []
    for h in portfolio:
        if not isinstance(h, dict):
            continue
        h = dict(h)
        try:
            qty = float(h.get("qty") or 0)
        except Exception:
            qty = 0
        unit = str(h.get("unit") or "")
        if not unit:
            qty = qty * 100
            h["unit"] = "lot"
        h["qty"] = qty
        out.append(h)
    return out


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


def _rp_id(v: Any) -> str:
    """Format angka gaya Indonesia (ribuan '.', tanpa nol ekor) untuk pesan."""
    try:
        s = f"{float(v):,.2f}".rstrip("0").rstrip(".")
        return s.replace(",", ".")
    except (TypeError, ValueError):
        return str(v)


def _telegram_action_plan(ap: Optional[dict], max_steps: int = 3) -> str:
    """Ringkasan rencana aksi untuk Telegram: tunggu apa & di harga berapa."""
    if not ap:
        return ""
    lines: List[str] = []
    kes = str(ap.get("kesimpulan") or "").strip()
    if kes:
        lines.append(kes)

    # Setup swing + harga trigger pasti (pullback vs breakout).
    setup = ap.get("setup") or {}
    if setup.get("jenis"):
        trg = setup.get("trigger") or {}
        lv = f" @ {_rp_id(trg.get('level'))}" if trg.get("level") not in (None, "") else ""
        lines.append(f"🧩 Setup: {setup['jenis']}{lv}")
        if trg.get("syarat"):
            lines.append(f"   {trg['syarat']}")
        if setup.get("harga_entry") not in (None, "") or setup.get("batas_kejar") not in (None, ""):
            lines.append(f"💰 Entry rencana {_rp_id(setup.get('harga_entry'))} · "
                         f"🚫 jangan kejar di atas {_rp_id(setup.get('batas_kejar'))}")

    steps = ap.get("langkah") or []
    wajib = [s for s in steps if s.get("wajib")] or steps
    for k in wajib[:max_steps]:
        lv = f" @ {_rp_id(k.get('level'))}" if k.get("level") not in (None, "") else ""
        tag = " (wajib)" if k.get("wajib") else ""
        lines.append(f"• {k.get('syarat')}{lv}{tag}")

    zona = ap.get("zona_entry") or {}
    if zona.get("low") not in (None, ""):
        z = _rp_id(zona["low"])
        if zona.get("high") not in (None, "") and zona["high"] != zona["low"]:
            z += "–" + _rp_id(zona["high"])
        lines.append(f"🎯 Zona pantau/entry: {z}")

    tail = []
    tp = ap.get("target") or {}
    bat = ap.get("pembatalan") or {}
    if tp.get("tp1") not in (None, ""):
        s = f"✅ TP1 {_rp_id(tp['tp1'])}"
        if tp.get("tp2") not in (None, ""):
            s += f", TP2 {_rp_id(tp['tp2'])}"
        tail.append(s)
    if bat.get("level") not in (None, ""):
        tail.append(f"🛑 SL {_rp_id(bat['level'])}")
    if tail:
        lines.append(" · ".join(tail))

    kel = ap.get("kelayakan") or {}
    if kel.get("rrr") not in (None, ""):
        rrr_txt = f"⚖️ RRR 1:{_rp_id(kel['rrr'])}"
        rrr_txt += " ✓ layak" if kel.get("layak") else " ✗ di bawah 1:2"
        if ap.get("time_stop_hari"):
            rrr_txt += f" · ⏳ batas {ap['time_stop_hari']} hari"
        lines.append(rrr_txt)

    tm = ap.get("timing") or {}
    if tm.get("skor") not in (None, ""):
        lines.append(f"⏱ Timing masuk: {_rp_id(tm['skor'])}/100 ({tm.get('label', '')})")

    konf = ap.get("konflik") or []
    if konf:
        lines.append(f"⚠ {konf[0]}")
    if not lines:
        return ""
    return "📋 Rencana aksi (tunggu apa & di harga berapa):\n" + "\n".join(lines)


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


def _download_github_csv(ticker: str, max_rows: Optional[int] = 500) -> Optional[pd.DataFrame]:
    """Fallback data historis IDX dari dataset publik GitHub (2019-2025).

    Dipakai saat Yahoo Finance memblokir/rate-limit IP datacenter (mis. Vercel).
    Kolom utama: date, open_price, high, low, close, volume, value, foreign_buy, foreign_sell.
    max_rows=None mengembalikan seluruh riwayat (~1350 bar, 2019-2025) untuk backtest.
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
        df = df.sort_index()
        if max_rows is not None:
            df = df.tail(max_rows)
        if len(df) < 30:
            return None
        # Cache riwayat penuh (dipakai berulang oleh backtest & matriks).
        try:
            key = ("gh-full", code)
            CACHE[key] = {"ts": time.time(), "df": df}
        except Exception:
            pass
        return df
    except Exception:
        # Coba pakai cache bila ada (jaringan gagal di tengah matriks)
        try:
            hit = CACHE.get(("gh-full", code))
            if hit and time.time() - hit["ts"] < 6 * 3600:
                return hit["df"]
        except Exception:
            pass
        return None


def _fetch_backtest_history(ticker: str, years: int) -> Optional[pd.DataFrame]:
    """Riwayat untuk backtest: fetch_data('5y') diperluas bila sumber pendek.

    Saat yfinance diblokir, fallback IDX Edge hanya mengembalikan ~200 bar
    (~10 bulan) dan dataset GitHub 500 bar — tidak cukup utk backtest 2-5 tahun.
    Di sini riwayat lama (2019-2025) dari dataset GitHub digabungkan di bawah
    data terbaru (yfinance/IDX Edge menang pada tanggal yang tumpang tindih).
    """
    try:
        df = fetch_data(ticker, "5y", yf_timeout=BACKTEST_YF_TIMEOUT)
    except Exception:
        return None
    if len(df) >= years * 260:
        return df
    try:
        gh = _download_github_csv(ticker, max_rows=None)
    except Exception:
        gh = None
    if gh is None or len(gh) <= len(df):
        return df
    # JANGAN gabungkan bila harga melompat drastis di perbatasan data lama (GitHub)
    # dengan data baru (yfinance/IDX Edge). Ini terjadi saat aksi korporasi
    # (split/dividen) belum disesuaikan di dataset lama: harga lama (mis. 37) vs
    # baru (mis. 179) beda >2x -> menggabungkannya membuat indikator & backtest
    # kacau. Kalau melompat, pakai data baru saja (riwayat lebih pendek tapi valid).
    try:
        gh_last = float(gh["Close"].iloc[-1])
        df_first = float(df["Close"].iloc[0])
        if gh_last > 0 and df_first > 0:
            ratio = df_first / gh_last
            if ratio > 2.0 or ratio < 0.5:
                return df
    except Exception:
        pass
    combined = pd.concat([gh, df[~df.index.isin(gh.index)]])
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    return combined if len(combined) > len(df) else df


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

    # --- Setup harga swing (bukan hanya BandarValue/nilai transaksi) ---
    # Swing sebaiknya hanya diambil saat HARGA menunjukkan setup: pullback sehat ke
    # SMA20 dalam tren naik, atau breakout dengan volume. Tanpa ini, screener bisa
    # memunculkan saham yang harganya justru menempel resistance.
    try:
        _close_s = df["Close"].astype(float)
        _vol_s = df["Volume"].astype(float)
        _last_s = float(_close_s.iloc[-1])
        _s20_s = float(sma(_close_s, 20).iloc[-1]) if len(df) >= 20 else None
        _s50_s = float(sma(_close_s, 50).iloc[-1]) if len(df) >= 50 else None
        _rsi_s = float(rsi(_close_s, 14).iloc[-1]) if len(df) >= 15 else None
        _vma_s = float(sma(_vol_s, 20).iloc[-1]) if len(df) >= 20 else None
        _trend_up = bool(_s20_s and _s50_s and _last_s > _s20_s > _s50_s)
        _pullback = bool(_trend_up and _s20_s and abs(_last_s / _s20_s - 1) <= 0.03
                         and _rsi_s is not None and 35 <= _rsi_s <= 68)
        _breakout = bool(_s20_s and _last_s >= _s20_s and _vma_s
                         and float(_vol_s.iloc[-1]) >= 1.5 * _vma_s
                         and (_s50_s is None or _s20_s > _s50_s))
        price_setup = {
            "trend_up": _trend_up, "pullback": _pullback, "breakout": _breakout,
            "ok": bool(_pullback or _breakout),
            "note": ("Setup harga swing: pullback sehat ke SMA20 dalam tren naik, atau breakout "
                     "dengan volume ≥1,5× MA20."),
        }
    except Exception:
        price_setup = {"trend_up": False, "pullback": False, "breakout": False, "ok": False,
                       "note": "Setup harga swing tidak dapat dihitung."}
    checks["price_setup"] = price_setup

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
        swing["eligible"] = bool(all(swing[k] for k in
                                     ("bandar_value_gt_ma20", "value_ma20_ge_10b",
                                      "prev_bandar_le_now", "bandar_ma10_gt_ma20"))
                                 and price_setup["ok"])
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
        swing["eligible"] = bool(all(swing[k] for k in
                                     ("value_gt_ma20", "value_ma20_ge_10b",
                                      "prev_value_le_now", "value_ma10_gt_ma20"))
                                 and price_setup["ok"])
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

    # Konfirmasi ala buku (Bab 5-8): harga > SMA20 (bias naik), RSI < 70 (tidak
    # mengejar overbought), volume > VolumeMA20 (ada tenaga beli). Dipakai sebagai
    # filter opsional (require_confirm) dan kolom pada hasil screener.
    try:
        close_s = df["Close"].astype(float)
        vol_s = df["Volume"].astype(float)
        sma20_v = float(sma(close_s, 20).iloc[-1]) if len(df) >= 20 else float(close_s.mean())
        vma20_v = float(sma(vol_s, 20).iloc[-1]) if len(df) >= 20 else float(vol_s.mean())
        rsi14_v = float(rsi(close_s, 14).iloc[-1]) if len(df) >= 15 else None
        book_confirm = {
            "price_above_sma20": bool(float(close_s.iloc[-1]) > sma20_v),
            "rsi_below_70": bool(rsi14_v < 70) if rsi14_v is not None else False,
            "volume_above_ma20": bool(float(vol_s.iloc[-1]) > vma20_v),
            "rsi14": num(rsi14_v, 1) if rsi14_v is not None else None,
            "sma20": num(sma20_v, 2),
        }
        book_confirm["ok"] = all(book_confirm[k] for k in
                                  ("price_above_sma20", "rsi_below_70", "volume_above_ma20"))
    except Exception:
        book_confirm = {"price_above_sma20": False, "rsi_below_70": False,
                        "volume_above_ma20": False, "ok": False, "rsi14": None, "sma20": None}

    # Likuiditas: rata-rata nilai transaksi 20 hari + kelas (dipakai sebagai
    # komponen skor beli & kolom screener agar sinyal tidak muncul di saham
    # yang sulit masuk/keluar).
    liq = _liquidity_metrics(df)

    return {
        "eligible": eligible,
        "criteria_met": met,
        "checks": checks,
        "book_confirm": book_confirm,
        "metrics": {
            "price": num(m["last"], 2),
            "day_return_pct": num(m["day_ret"], 2),
            "estimated_value_idr": num(m["est_value"], 0),
            "volume_ratio_to_ma20": num(m["vol_ratio"], 2),
            "avg_value_20d": num(liq["avg_value_20d"], 0),
            "liquidity_grade": liq["grade"],
        },
    }


def _idx_edge_key_fp(key: str) -> str:
    """Fingerprint key (jangan simpan key asli di penyimpanan eksternal)."""
    import hashlib
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# Key yang kuotanya habis (per hari). Disimpan sebagai fingerprint di Upstash
# agar berlaku lintas instance/cold start, lalu di-skip dalam rotasi.
IDX_EDGE_DEAD_KEYS: set = set()
_IDX_DEAD_KEYS_LOADED = False


def _persist_dead_keys() -> None:
    """Simpan daftar key yang kuotanya habis sampai reset WIB (agar dicoba lagi besok)."""
    if SYNC_ENABLED:
        ttl = max(60, int(_idx_edge_quota_until() - time.time()))
        _upstash_set("ci:idx_edge_dead_keys",
                     json.dumps(sorted(IDX_EDGE_DEAD_KEYS)), ttl=ttl)


def _ensure_dead_keys_loaded() -> None:
    global IDX_EDGE_DEAD_KEYS, _IDX_DEAD_KEYS_LOADED
    if _IDX_DEAD_KEYS_LOADED:
        return
    _IDX_DEAD_KEYS_LOADED = True
    raw = _upstash_get("ci:idx_edge_dead_keys") if SYNC_ENABLED else None
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                IDX_EDGE_DEAD_KEYS = {str(x) for x in parsed}
        except Exception:
            pass


def _idx_edge_live_keys() -> List[str]:
    """Semua key yang kuotanya masih tersedia."""
    _ensure_dead_keys_loaded()
    return [k for k in IDX_EDGE_API_KEYS if _idx_edge_key_fp(k) not in IDX_EDGE_DEAD_KEYS]


def _idx_edge_next_key() -> Optional[str]:
    """Rotasi key API (membagi kuota antar key), melewati key yang kuotanya habis."""
    global _IDX_EDGE_KEY_IDX
    live = _idx_edge_live_keys()
    if not live:
        return None
    key = live[_IDX_EDGE_KEY_IDX % len(live)]
    _IDX_EDGE_KEY_IDX += 1
    return key


def idx_edge_get(path: str, params: Dict[str, Any], cache_key: str = "",
                 ttl: int = IDX_EDGE_HIST_TTL) -> Optional[dict]:
    """GET ke IDX Edge PRO API: rotasi antar key, otomatis melewati key yang
    kuotanya habis (429), dan hanya mengaktifkan circuit breaker global bila
    SEMUA key habis."""
    global IDX_EDGE_QUOTA_UNTIL
    if not IDX_EDGE_API_KEYS:
        return None
    _ensure_dead_keys_loaded()
    if time.time() < IDX_EDGE_QUOTA_UNTIL:
        return None  # semua key kuotanya habis -> short-circuit sampai reset WIB
    ck = cache_key or f"{path}:{json.dumps(params, sort_keys=True)}"
    now = time.time()
    hit = IDX_EDGE_CACHE.get(ck)
    if hit and now - hit["ts"] < ttl:
        return hit["data"]
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{IDX_EDGE_API_URL}{path}" + (f"?{qs}" if qs else "")
    tried_all = True
    for key in _idx_edge_live_keys():
        try:
            req = urllib.request.Request(url, headers={"X-API-Key": key, "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # API menjawab 429 (kuota key ini habis) -> skip, coba key lain
            try:
                err = json.loads(e.read().decode("utf-8"))
            except Exception:
                err = {}
            if isinstance(err, dict) and isinstance(err.get("detail"), str) \
                    and "kuota" in err["detail"].lower():
                IDX_EDGE_DEAD_KEYS.add(_idx_edge_key_fp(key))
                _persist_dead_keys()
                continue
            return None
        except Exception:
            return None
        if not isinstance(data, dict):
            continue
        if isinstance(data.get("detail"), str) and "kuota" in data["detail"].lower():
            IDX_EDGE_DEAD_KEYS.add(_idx_edge_key_fp(key))
            _persist_dead_keys()
            continue
        IDX_EDGE_CACHE[ck] = {"ts": now, "data": data}
        return data
    # Semua key yang dicoba habis kuotanya -> matikan sementara sampai reset WIB
    if tried_all:
        IDX_EDGE_QUOTA_UNTIL = _idx_edge_quota_until()
        _quota_persist()
    return None


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
# Jalur backtest/matriks: di Vercel yfinance hampir selalu diblokir/rate-limit,
# jadi menunggu timeout global (25 dtk) per ticker adalah pemborosan besar.
# Timeout pendek -> cepat pindah ke fallback (IDX Edge/GitHub).
BACKTEST_YF_TIMEOUT = 8
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


def fetch_ihsg(period: str = "max") -> Optional[pd.DataFrame]:
    """Indeks Harga Saham Gabungan (^JKSE) harian — untuk filter kondisi pasar.

    Sumber: yfinance (primary) -> Yahoo chart API via curl_cffi (fallback).
    Dicache 6 jam. Return df berkolom Open/High/Low/Close/Volume.
    """
    key = ("^JKSE", period)
    now = time.time()
    hit = CACHE.get(key)
    if hit and now - hit["ts"] < 6 * 3600:
        return hit["df"]
    df = None
    try:
        df = _call_with_timeout(
            lambda: yf.download("^JKSE", period=period, interval="1d",
                                auto_adjust=True, progress=False, threads=False),
            YFINANCE_TIMEOUT,
        )
    except Exception:
        df = None
    if df is None or df.empty:
        chart = _yahoo_chart_cffi("^JKSE", interval="1d", rng="10y")
        if chart and chart.get("bars"):
            bars = chart["bars"]
            df = pd.DataFrame(bars)
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(
                "Asia/Jakarta").dt.tz_localize(None)
            df = df.rename(columns={"time": "Date", "open": "Open", "high": "High",
                                    "low": "Low", "close": "Close", "volume": "Volume"})
            df = df.set_index("Date").sort_index()
            df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Close"])
    if len(df) < 30:
        return None
    df.attrs["source"] = "yfinance"
    CACHE[key] = {"ts": now, "df": df}
    return df


def _ihsg_series():
    """Deret close & MA200 IHSG untuk backtest (diselaraskan per tanggal).
    Returns (close_series, ma200_series) atau (None, None)."""
    df = fetch_ihsg("max")
    if df is None:
        return None, None
    close = df["Close"].astype(float)
    ma200 = sma(close, 200)
    return close, ma200


def _ihsg_regime() -> dict:
    """Kondisi pasar saat ini: IHSG di atas/bawah MA200 (filter regime buku)."""
    df = fetch_ihsg("2y")
    if df is None:
        return {"trend": None, "note": "Data IHSG tidak tersedia saat ini."}
    close = df["Close"].astype(float)
    ma = float(sma(close, 200).iloc[-1])
    last = float(close.iloc[-1])
    trend = "bull" if last > ma else "bear"
    return {
        "trend": trend,
        "close": num(last, 0),
        "ma200": num(ma, 0),
        "date": str(close.index[-1].date()) if hasattr(close.index[-1], "date") else str(close.index[-1]),
        "source": df.attrs.get("source", "yfinance"),
        "note": ("Filter regime: sinyal beli hanya diproses saat IHSG di ATAS MA200 "
                 "(pasar bullish). Saat IHSG di bawah MA200, peluang sinyal palsu naik."),
    }


def _weekly_trend_series(df: pd.DataFrame) -> pd.Series:
    """Tren mingguan (multi-timeframe): close mingguan > SMA20 mingguan, dibawa
    (ffill) ke tiap bar harian. True = tren naik jangka menengah."""
    close = df["Close"].astype(float)
    wk = close.resample("W-FRI").last()
    wk_ma = sma(wk, 20)
    wk_trend = (wk > wk_ma).fillna(False)
    # gabungkan kembali ke index harian TANPA lookahead: tiap hari memakai nilai
    # minggunya sendiri (antara batas minggu sebelumnya dan batas minggu ini).
    out = pd.Series(False, index=df.index, dtype=bool)
    prev_ts = None
    for ts, val in wk_trend.items():
        if prev_ts is None:
            out.loc[out.index <= ts] = val
        else:
            out.loc[(out.index > prev_ts) & (out.index <= ts)] = val
        prev_ts = ts
    return out


def _divergence_series(df: pd.DataFrame, period: int = 14, carry: int = 3):
    """Divergensi RSI (bullish/bearish) sebagai deret boolean per bar, untuk backtest.

    Swing low/high fractal (sama dengan rsi_divergence) dipasangkan: baris kedua
    menandai terbentuknya divergensi, lalu sinyal dipertahankan 'carry' hari.
    Returns (bull_series, bear_series) pd.Series bool pada index df.
    """
    r = rsi(df["Close"], period).to_numpy(dtype=float)
    close = df["Close"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    high = df["High"].to_numpy(dtype=float)
    n = len(df)
    sl, sh = [], []
    for i in range(2, n - 2):
        if low[i] == low[i - 2:i + 3].min() and low[i] < low[i - 1] and low[i] < low[i + 1]:
            sl.append((i, float(low[i]), float(r[i])))
        if high[i] == high[i - 2:i + 3].max() and high[i] > high[i - 1] and high[i] > high[i + 1]:
            sh.append((i, float(high[i]), float(r[i])))
    bull = np.zeros(n, dtype=bool)
    bear = np.zeros(n, dtype=bool)
    for j in range(1, len(sl)):
        i1, p1, r1 = sl[j - 1]
        i2, p2, r2 = sl[j]
        if p2 < p1 and r2 > r1:
            bull[max(0, i2 - carry):i2 + 1] = True
    for j in range(1, len(sh)):
        i1, p1, r1 = sh[j - 1]
        i2, p2, r2 = sh[j]
        if p2 > p1 and r2 < r1:
            bear[max(0, i2 - carry):i2 + 1] = True
    return pd.Series(bull, index=df.index), pd.Series(bear, index=df.index)


FMP_API_KEY = os.environ.get("FMP_API_KEY", "").strip()
FMP_CACHE: Dict[str, dict] = {}
FMP_TTL = 24 * 3600  # fundamental berubah per kuartal -> cache 1 hari cukup
FMP_QUOTA_UNTIL = 0.0  # circuit breaker saat FMP membalas HTTP 429 (kuota habis)


def _fmp_get(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """GET ke Financial Modeling Prep (FMP) API 'stable' (v3 legacy sudah nonaktif).

    None bila key belum di-set / gagal / kuota habis / simbol di luar cakupan paket
    (mis. IDX .JK butuh paket berbayar FMP). Endpoint: /stable/quote, /stable/ratios,
    /stable/key-metrics, /stable/income-statement, /stable/dividends, /stable/splits.

    Cache memakai kunci path+params (bukan hanya path) supaya data antar simbol
    tidak saling tertukar.
    """
    global FMP_QUOTA_UNTIL
    if not FMP_API_KEY:
        return None
    if time.time() < FMP_QUOTA_UNTIL:
        return None  # kuota harian FMP habis -> jangan panggil lagi hari ini
    now = time.time()
    key = path + "?" + urllib.parse.urlencode(sorted((params or {}).items()))
    hit = FMP_CACHE.get(key)
    if hit and now - hit["ts"] < FMP_TTL:
        return hit["data"]
    url = f"https://financialmodelingprep.com/stable/{path}?apikey={FMP_API_KEY}"
    if params:
        url += "&" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            # Paket gratis FMP dibatasi kuota harian -> breaker hingga reset WIB.
            FMP_QUOTA_UNTIL = _idx_edge_quota_until()
        return None
    except Exception:
        return None
    if isinstance(data, dict) and isinstance(data.get("Error Message"), str):
        return None
    FMP_CACHE[key] = {"ts": now, "data": data}
    return data


def _fetch_fmp_fundamentals(sym: str) -> Optional[dict]:
    """PE/PBV/ROE/EPS/revenue/laba + YoY + dividen + split dari FMP (FMP_API_KEY).

    sym = simbol apa adanya (mis. 'AAPL'). Paket FMP yang terpasang tidak
    mencakup bursa IDX (.JK) sehingga saham Indonesia diambil dari Yahoo.
    """
    if not FMP_API_KEY or not sym:
        return None
    out: Dict[str, Any] = {}
    quote = _fmp_get("quote", {"symbol": sym})
    if isinstance(quote, list) and quote:
        q = quote[0]
        for k in ("marketCap", "name", "price", "yearHigh", "yearLow"):
            if q.get(k) is not None:
                out[k] = q.get(k)
    ratios = _fmp_get("ratios", {"symbol": sym, "period": "annual", "limit": 1})
    if isinstance(ratios, list) and ratios:
        r = ratios[0]
        for k in ("priceToEarningsRatio", "priceToBookRatio", "dividendYieldPercentage",
                  "dividendYield", "dividendPerShare", "currentRatio", "debtToEquityRatio",
                  "netProfitMargin"):
            if r.get(k) is not None:
                out.setdefault(k, r.get(k))
    km = _fmp_get("key-metrics", {"symbol": sym, "period": "annual", "limit": 1})
    if isinstance(km, list) and km:
        if km[0].get("returnOnEquity") is not None:
            out["returnOnEquity"] = km[0]["returnOnEquity"]

    # --- Perbandingan laporan laba-rugi YoY (tahunan & kuartalan) ---
    def _pct(cur, prev_val) -> Optional[float]:
        try:
            if cur is None or not prev_val:
                return None
            return float((float(cur) / float(prev_val) - 1) * 100)
        except Exception:
            return None

    inc_ann = _fmp_get("income-statement", {"symbol": sym, "period": "annual", "limit": 3})
    if isinstance(inc_ann, list) and inc_ann:
        # nilai laporan terakhir untuk rasio ringkas (EPS, revenue, laba, periode)
        top = inc_ann[0]
        for k in ("revenue", "netIncome", "eps", "grossProfit", "operatingIncome"):
            if top.get(k) is not None:
                out.setdefault(k, top.get(k))
        if top.get("date"):
            out["period_end"] = str(top["date"])[:10]
        ann = []
        for i, row in enumerate(inc_ann):
            prev = inc_ann[i + 1] if i + 1 < len(inc_ann) else None
            rev, ni, e, g = row.get("revenue"), row.get("netIncome"), row.get("eps"), row.get("grossProfit")
            ann.append({
                "year": str(row.get("calendarYear") or str(row.get("date", ""))[:4]),
                "period": str(row.get("date", ""))[:10],
                "revenue": rev, "net_income": ni, "eps": e,
                "gross_margin_pct": num((g / rev * 100), 1) if g is not None and rev else None,
                "revenue_yoy_pct": num(_pct(rev, prev.get("revenue")), 1) if prev else None,
                "net_income_yoy_pct": num(_pct(ni, prev.get("netIncome")), 1) if prev else None,
            })
        out["financials_annual"] = ann

    inc_q = _fmp_get("income-statement", {"symbol": sym, "period": "quarter", "limit": 8})
    if isinstance(inc_q, list) and inc_q:
        qtr = []
        for i, row in enumerate(inc_q):
            prev = inc_q[i + 4] if i + 4 < len(inc_q) else None  # kuartal sama tahun lalu
            rev, ni, e = row.get("revenue"), row.get("netIncome"), row.get("eps")
            fy = str(row.get("fiscalYear") or str(row.get("date", ""))[:4])
            fp = str(row.get("period") or "")
            if not fp and row.get("date"):
                fp = "Q" + str((pd.Timestamp(row["date"]).month - 1) // 3 + 1)
            qtr.append({
                "period": f"{fy}-{fp}",
                "revenue": rev, "net_income": ni, "eps": e,
                "revenue_yoy_pct": num(_pct(rev, prev.get("revenue")), 1) if prev else None,
                "net_income_yoy_pct": num(_pct(ni, prev.get("netIncome")), 1) if prev else None,
            })
        out["financials_quarterly"] = qtr

    # --- Riwayat dividen ---
    div = _fmp_get("dividends", {"symbol": sym, "limit": 12})
    if isinstance(div, list) and div:
        fmp_div = [
            {"date": str(x.get("date", ""))[:10], "amount": x.get("dividend"),
             "adj": x.get("adjDividend"),
             "payment": str(x.get("paymentDate", ""))[:10] or None}
            for x in div[:12]
        ]
        out["dividends"] = _classify_dividends(fmp_div)
        out["dividends_annual"] = _dividend_annual_recap(out["dividends"])

    # --- Aksi korporasi: stock split ---
    split = _fmp_get("splits", {"symbol": sym, "limit": 12})
    if isinstance(split, list) and split:
        out["splits"] = [
            {"date": str(x.get("date", ""))[:10],
             "ratio": f"{x.get('numerator')}:{x.get('denominator')}"}
            for x in split[:12]
        ]
    return out or None


# ---------------------------------------------------------------------------
# 12b. FUNDAMENTAL & AKSI KORPORASI (Yahoo Finance + FMP)
# ---------------------------------------------------------------------------
# Sumber data:
#   * Yahoo Finance (quoteSummary + fundamentals-timeseries + chart events)
#     -> MENCANGKUP saham IDX (.JK): PE, PBV, EPS, ROE, margin, dividen,
#        perbandingan laporan YoY tahunan/kuartalan, dan aksi korporasi
#        (stock split / reverse split).
#   * Financial Modeling Prep (FMP_API_KEY) -> pelengkap; paket FMP yang
#     terpasang TIDAK mencakup bursa Indonesia, jadi rasio IDX diambil dari
#     Yahoo. FMP tetap dipakai bila simbol di luar IDX (mis. saham AS).

YF_FUND_CACHE: Dict[str, dict] = {}
YF_FUND_TTL = 6 * 3600  # laporan keuangan berubah per kuartal -> cache 6 jam
_YAHOO_HEADERS = {"User-Agent": _YAHOO_UA, "Accept": "application/json,text/plain,*/*"}
_YF_SESSION_LOCK = threading.Lock()
_YF_SESSION: Dict[str, Any] = {"session": None, "crumb": "", "ts": 0.0}


def _yoy_pct(cur: Any, prev_val: Any) -> Optional[float]:
    """Perubahan relatif (%) terhadap periode pembanding; None bila tak lengkap."""
    try:
        if cur is None or not prev_val:
            return None
        return (float(cur) / float(prev_val) - 1) * 100
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _epoch_date(epoch: Any) -> Optional[str]:
    """Epoch detik -> 'YYYY-MM-DD' (UTC, sesuai konvensi Yahoo)."""
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(float(epoch)))
    except (TypeError, ValueError, OSError):
        return None


def _yahoo_session():
    """Session curl_cffi + crumb Yahoo (wajib utk quoteSummary & fundamentals).

    Kredensial dipakai ulang 30 menit agar tidak berulang meminta crumb
    (Yahoo membatasi permintaan crumb per IP).
    """
    try:
        from curl_cffi import requests as cr
    except Exception:
        return None, ""
    now = time.time()
    with _YF_SESSION_LOCK:
        sess = _YF_SESSION.get("session")
        crumb = str(_YF_SESSION.get("crumb") or "")
        if sess is not None and crumb and now - float(_YF_SESSION.get("ts") or 0) < 1800:
            return sess, crumb
        try:
            sess = cr.Session(impersonate="chrome")
            try:
                sess.get("https://fc.yahoo.com", timeout=8, headers=_YAHOO_HEADERS)
            except Exception:
                pass
            r = sess.get("https://query1.finance.yahoo.com/v1/test/getcrumb",
                         timeout=10, headers=_YAHOO_HEADERS)
            crumb = (r.text or "").strip()
            if r.status_code != 200 or not crumb or "Too Many" in crumb:
                return None, ""
        except Exception:
            return None, ""
        _YF_SESSION.update({"session": sess, "crumb": crumb, "ts": now})
        return sess, crumb


def _yahoo_api(url: str) -> Optional[dict]:
    """GET JSON dari Yahoo memakai session + crumb (None bila gagal/dibatasi)."""
    sess, crumb = _yahoo_session()
    if sess is None or not crumb:
        return None
    full = url + ("&" if "?" in url else "?") + "crumb=" + urllib.parse.quote(crumb)
    try:
        r = sess.get(full, timeout=15, headers=_YAHOO_HEADERS)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _yahoo_quote_summary(ticker: str, modules: str) -> dict:
    """quoteSummary Yahoo (rasio, key statistics, laporan ringkas, kalender)."""
    j = _yahoo_api(f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
                   f"{urllib.parse.quote(ticker)}?modules={modules}")
    res = ((j or {}).get("quoteSummary") or {}).get("result") or []
    return res[0] if res and isinstance(res[0], dict) else {}


def _yahoo_timeseries(ticker: str, types: List[str], light: bool = False) -> Dict[str, list]:
    """Time series fundamental Yahoo -> {tipe: [(tanggal, nilai), ...]} urut naik."""
    now = int(time.time())
    span = 2 * 365 if light else 8 * 365
    j = _yahoo_api("https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/"
                   f"timeseries/{urllib.parse.quote(ticker)}?symbol={urllib.parse.quote(ticker)}"
                   f"&type={','.join(types)}&period1={now - span * 86400}&period2={now}")
    res = ((j or {}).get("timeseries") or {}).get("result") or []
    out: Dict[str, list] = {}
    for item in res:
        if not isinstance(item, dict):
            continue
        keys = (item.get("meta") or {}).get("type") or []
        key = keys[0] if keys else ""
        if not key:
            continue
        rows: List[tuple] = []
        for x in item.get(key) or []:
            if not isinstance(x, dict):
                continue
            val = x.get("reportedValue")
            if isinstance(val, dict):
                val = val.get("raw")
            if x.get("asOfDate") and val is not None:
                try:
                    rows.append((str(x["asOfDate"])[:10], float(val)))
                except (TypeError, ValueError):
                    continue
        rows.sort(key=lambda p: p[0])
        out[key] = rows
    return out


def _yahoo_events(ticker: str) -> dict:
    """Riwayat dividen & aksi korporasi (stock split) dari chart events Yahoo."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}"
           "?range=10y&interval=1mo&events=div,splits")
    try:
        from curl_cffi import requests as cr
        r = cr.get(url, impersonate="chrome", timeout=12, headers=_YAHOO_HEADERS)
        if r.status_code != 200:
            return {}
        j = r.json()
    except Exception:
        return {}
    res = (j.get("chart") or {}).get("result") or []
    return (res[0].get("events") or {}) if res else {}


def _qs_num(src: Any, key: str) -> Optional[float]:
    """Ambil angka dari quoteSummary ({'raw': x}) / dict biasa."""
    v = src.get(key) if isinstance(src, dict) else None
    if isinstance(v, dict):
        v = v.get("raw")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _classify_dividends(rows: List[dict]) -> List[dict]:
    """Tandai tiap dividen: Final (tahunan) atau Interim.

    Yahoo tidak menyediakan jenis dividen, jadi dipakai konvensi IDX:
      * dividen FINAL (tahunan) disetujui RUPS dan dibayar Jan-Jun; karena itu
        ex-date Jan-Jun diatribusikan ke tahun buku sebelumnya;
      * dividen INTERIM dibayar di dalam tahun berjalan (Jul-Des) — jadi setiap
        ex-date Jul-Des pasti interim;
      * bila pada Jan-Jun ada beberapa pembayaran, nominal terbesar = final.
    Hasilnya perkiraan berbasis pola, bukan data resmi jenis dividen.
    """
    for r in rows:
        d = str(r.get("date") or "")
        try:
            y, m = int(d[:4]), int(d[5:7])
        except (TypeError, ValueError):
            r["fiscal_year"] = None
            r["type"] = None
            continue
        r["fiscal_year"] = y - 1 if m <= 6 else y
        r["type"] = "Final (tahunan)" if m <= 6 else "Interim"

    # Final hanya boleh satu per tahun buku: di antara kandidat Jan-Jun, ambil
    # nominal terbesar sebagai final, sisanya interim.
    finals: Dict[int, List[dict]] = {}
    for r in rows:
        if r.get("type") == "Final (tahunan)" and r.get("fiscal_year"):
            finals.setdefault(r["fiscal_year"], []).append(r)
    for items in finals.values():
        if len(items) < 2:
            continue
        top = max(items, key=lambda x: (x.get("amount") or 0.0, x.get("date") or ""))
        for r in items:
            if r is not top:
                r["type"] = "Interim"
    return rows


def _dividend_annual_recap(rows: List[dict]) -> List[dict]:
    """Rekap dividen per tahun buku: total, jumlah pembayaran, final vs interim.

    Dipakai agar bisa langsung dibaca "tahun ini bagi dividen berapa" tanpa
    menjumlahkan baris satu per satu.
    """
    by_year: Dict[int, dict] = {}
    for r in rows or []:
        fy = r.get("fiscal_year")
        if not fy:
            continue
        amt = float(r.get("amount") or 0.0)
        g = by_year.setdefault(int(fy), {"fiscal_year": int(fy), "total": 0.0,
                                         "count": 0, "final": 0.0, "interim": 0.0})
        g["total"] += amt
        g["count"] += 1
        if r.get("type") == "Interim":
            g["interim"] += amt
        else:
            g["final"] += amt
    out = []
    for fy in sorted(by_year, reverse=True):
        g = by_year[fy]
        out.append({
            "fiscal_year": fy,
            "total_per_share": num(g["total"], 2),
            "count": g["count"],
            "final_per_share": num(g["final"], 2) if g["final"] else None,
            "interim_per_share": num(g["interim"], 2) if g["interim"] else None,
        })
    return out


def _fetch_yahoo_fundamentals(ticker: str, light: bool = False) -> Optional[dict]:
    """Fundamental emiten dari Yahoo Finance (mencakup saham IDX '.JK').

    light=True -> hanya rasio ringkas (1 request) untuk endpoint massal seperti
    /api/quotes; light=False -> sekaligus laporan YoY tahunan & kuartalan,
    riwayat dividen, dan aksi korporasi.
    """
    if not ticker:
        return None
    key_tk = ticker.upper()
    hit = YF_FUND_CACHE.get(key_tk)
    if hit and time.time() - hit["ts"] < YF_FUND_TTL:
        return hit["data"]

    mods = "price,summaryDetail,defaultKeyStatistics,financialData"
    if not light:
        mods += ",incomeStatementHistory,incomeStatementHistoryQuarterly"
    types = ["annualTotalRevenue", "annualNetIncome", "annualDilutedEPS", "annualBasicEPS",
             "quarterlyTotalRevenue", "quarterlyNetIncome", "quarterlyDilutedEPS", "quarterlyBasicEPS"]

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_qs = ex.submit(_yahoo_quote_summary, key_tk, mods)
        f_ts = ex.submit(_yahoo_timeseries, key_tk, types, light)
        f_ev = None if light else ex.submit(_yahoo_events, key_tk)
        qs = f_qs.result()
        ts = f_ts.result()
        ev = f_ev.result() if f_ev else {}

    if not qs:
        YF_FUND_CACHE[key_tk] = {"ts": time.time(), "data": None}
        return None

    sd = qs.get("summaryDetail") or {}
    ks = qs.get("defaultKeyStatistics") or {}
    fd = qs.get("financialData") or {}
    pr = qs.get("price") or {}
    out: Dict[str, Any] = {}

    name = pr.get("longName") or pr.get("shortName")
    if name:
        out["name"] = str(name)
    for src_key, out_key in (("marketCap", "marketCap"), ("sharesOutstanding", "sharesOutstanding")):
        val = _qs_num(pr, src_key) or _qs_num(ks, src_key)
        if val:
            out[out_key] = val

    # Mata uang: sebagian emiten IDX melaporkan dalam USD (mis. AMMN) sehingga
    # PBV/book value bawaan Yahoo bisa tidak sekonsisten harga (jadi salah).
    quote_ccy = str(pr.get("currency") or "")
    fin_ccy = str(fd.get("financialCurrency") or "")
    same_ccy = bool(quote_ccy and fin_ccy and quote_ccy == fin_ccy)
    if quote_ccy:
        out["currency"] = quote_ccy
    if fin_ccy:
        out["financialCurrency"] = fin_ccy
    px = _qs_num(pr, "regularMarketPrice") or _qs_num(fd, "currentPrice")
    book = _qs_num(ks, "bookValue")
    if book is not None and same_ccy and book > 0:
        out["bookValue"] = book
    pbv = (px / book) if (same_ccy and book and px) else _qs_num(ks, "priceToBook")
    if pbv is not None and 0 < pbv <= 200:
        out["priceToBookRatio"] = pbv

    # --- Rasio keuangan utama (PE, EPS, ROE/ROA, margin) ---
    ratios = (
        (_qs_num(sd, "trailingPE") or _qs_num(ks, "trailingPE"), "priceToEarningsRatio"),
        (_qs_num(ks, "forwardPE") or _qs_num(sd, "forwardPE"), "forwardPE"),
        (_qs_num(ks, "trailingEps"), "eps"),
        (_qs_num(ks, "forwardEps"), "forwardEps"),
        (_qs_num(fd, "returnOnEquity") or _qs_num(ks, "returnOnEquity"), "returnOnEquity"),
        (_qs_num(fd, "returnOnAssets"), "returnOnAssets"),
        (_qs_num(fd, "profitMargins") or _qs_num(ks, "profitMargins"), "netProfitMargin"),
        (_qs_num(fd, "totalRevenue"), "revenue"),
    )
    for val, out_key in ratios:
        if val is not None:
            out[out_key] = val

    # --- Dividen ---
    dy = _qs_num(sd, "dividendYield")
    if dy is not None:
        out["dividendYieldPercentage"] = dy * 100
    dr = _qs_num(sd, "dividendRate")
    if dr is not None:
        out["dividendPerShare"] = dr
    po = _qs_num(sd, "payoutRatio")
    if po is not None:
        out["payoutRatioPercentage"] = po * 100
    five = _qs_num(sd, "fiveYearAvgDividendYield")
    if five is not None:
        out["fiveYearAvgDividendYieldPercentage"] = five
    exd = _epoch_date(_qs_num(sd, "exDividendDate"))
    if exd:
        out["exDividendDate"] = exd

    if light:
        YF_FUND_CACHE[key_tk] = {"ts": time.time(), "data": out or None}
        return out or None

    # --- Susun laporan laba-rugi per tahun & per kuartal ---
    def _by_period(keys: List[str], quarterly: bool) -> Dict[Any, float]:
        for k in keys:
            vals = ts.get(k) or []
            if not vals:
                continue
            mapped: Dict[Any, float] = {}
            for date, val in vals:
                try:
                    yr = int(date[:4])
                    if quarterly:
                        mapped[(yr, (int(date[5:7]) - 1) // 3 + 1)] = val
                    else:
                        mapped[yr] = val
                except (TypeError, ValueError):
                    continue
            if mapped:
                return mapped
        return {}

    rev_a = _by_period(["annualTotalRevenue"], False)
    ni_a = _by_period(["annualNetIncome"], False)
    eps_a = _by_period(["annualDilutedEPS", "annualBasicEPS"], False)
    rev_q = _by_period(["quarterlyTotalRevenue"], True)
    ni_q = _by_period(["quarterlyNetIncome"], True)
    eps_q = _by_period(["quarterlyDilutedEPS", "quarterlyBasicEPS"], True)

    # Cadangan bila timeseries dibatasi: pakai laporan ringkas quoteSummary.
    if not rev_a:
        for row in (qs.get("incomeStatementHistory") or {}).get("incomeStatementHistory") or []:
            yr = _epoch_date(_qs_num(row, "endDate"))
            if not yr:
                continue
            y = int(yr[:4])
            r_, n_ = _qs_num(row, "totalRevenue"), _qs_num(row, "netIncome")
            if r_ is not None:
                rev_a[y] = r_
            if n_ is not None:
                ni_a[y] = n_
    if not rev_q:
        for row in (qs.get("incomeStatementHistoryQuarterly") or {}).get("incomeStatementHistory") or []:
            d = _epoch_date(_qs_num(row, "endDate"))
            if not d:
                continue
            kq = (int(d[:4]), (int(d[5:7]) - 1) // 3 + 1)
            r_, n_ = _qs_num(row, "totalRevenue"), _qs_num(row, "netIncome")
            if r_ is not None:
                rev_q[kq] = r_
            if n_ is not None:
                ni_q[kq] = n_

    years = sorted(set(rev_a) | set(ni_a) | set(eps_a), reverse=True)
    ann = []
    for yr in years:
        prev = yr - 1
        ann.append({
            "year": str(yr),
            "period": f"{yr}-12-31",
            "revenue": rev_a.get(yr), "net_income": ni_a.get(yr), "eps": eps_a.get(yr),
            "gross_margin_pct": None,
            "revenue_yoy_pct": num(_yoy_pct(rev_a.get(yr), rev_a.get(prev)), 1) if prev in rev_a else None,
            "net_income_yoy_pct": num(_yoy_pct(ni_a.get(yr), ni_a.get(prev)), 1) if prev in ni_a else None,
        })
    if ann:
        out["financials_annual"] = ann

    quarters = sorted(set(rev_q) | set(ni_q) | set(eps_q), reverse=True)[:8]
    qtr = []
    for (yr, q) in quarters:
        prev = (yr - 1, q)
        qtr.append({
            "period": f"{yr}-Q{q}",
            "revenue": rev_q.get((yr, q)), "net_income": ni_q.get((yr, q)), "eps": eps_q.get((yr, q)),
            "revenue_yoy_pct": num(_yoy_pct(rev_q.get((yr, q)), rev_q.get(prev)), 1) if prev in rev_q else None,
            "net_income_yoy_pct": num(_yoy_pct(ni_q.get((yr, q)), ni_q.get(prev)), 1) if prev in ni_q else None,
        })
    if qtr:
        out["financials_quarterly"] = qtr

    # Laba bersih 12 bulan terakhir = jumlah 4 kuartal berurutan (cadangan: tahunan).
    def _prev_q(p: tuple) -> tuple:
        return (p[0] - 1, 4) if p[1] == 1 else (p[0], p[1] - 1)

    last4 = quarters[:4]
    contiguous = (len(last4) == 4 and all(k in ni_q for k in last4)
                  and all(last4[i + 1] == _prev_q(last4[i]) for i in range(3)))
    if contiguous:
        out["netIncome"] = sum(ni_q[k] for k in last4)
    elif years and ni_a.get(years[0]) is not None:
        out["netIncome"] = ni_a[years[0]]

    # Periode laporan terakhir yang tersedia (akhir kuartal/tahun fiskal).
    q_end = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
    if quarters:
        out["period_end"] = f"{quarters[0][0]}-{q_end.get(quarters[0][1], '12-31')}"
    elif years:
        out["period_end"] = f"{years[0]}-12-31"

    # --- Riwayat dividen (10 tahun) ---
    divs = sorted((ev.get("dividends") or {}).values(),
                  key=lambda x: x.get("date") or 0, reverse=True)
    rows = []
    for x in divs[:12]:
        d = _epoch_date(x.get("date"))
        if d and x.get("amount") is not None:
            rows.append({"date": d, "amount": num(x.get("amount"), 2),
                         "adj": num(x.get("amount"), 2), "payment": None})
    if rows:
        out["dividends"] = _classify_dividends(rows)
        out["dividends_annual"] = _dividend_annual_recap(out["dividends"])

    # --- Aksi korporasi: stock split / reverse split ---
    sps = sorted((ev.get("splits") or {}).values(),
                 key=lambda x: x.get("date") or 0, reverse=True)
    rows = []
    for x in sps[:12]:
        d = _epoch_date(x.get("date"))
        if not d:
            continue
        ratio = x.get("splitRatio") or f"{x.get('numerator')}:{x.get('denominator')}"
        rows.append({"date": d, "ratio": str(ratio)})
    if rows:
        out["splits"] = rows

    YF_FUND_CACHE[key_tk] = {"ts": time.time(), "data": out or None}
    return out or None


_IDX_PROFILE_CACHE: Dict[str, dict] = {}
_IDX_SECTOR_CACHE: Dict[str, str] = {}
_PROFILE_LOADED = False
_SECTOR_LOADED = False
_PROFILE_LOCK = threading.Lock()


def _load_company_profiles() -> Dict[str, dict]:
    """Profil emiten IDX dari dataset publik: nama, tanggal listing, papan, saham beredar."""
    global _PROFILE_LOADED
    if _PROFILE_LOADED:
        return _IDX_PROFILE_CACHE
    with _PROFILE_LOCK:
        if _PROFILE_LOADED:
            return _IDX_PROFILE_CACHE
        try:
            req = urllib.request.Request(IDX_TICKER_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                csv_data = resp.read()
            df_csv = pd.read_csv(io.BytesIO(csv_data))
            for _, row in df_csv.iterrows():
                code = str(row.get("code", "")).strip().upper()
                if not code:
                    continue
                _IDX_PROFILE_CACHE[code] = {
                    "name": str(row.get("name", "")).strip(),
                    "listing_date": str(row.get("listingDate", ""))[:10],
                    "board": str(row.get("listingBoard", "")).strip(),
                    "shares": float(row.get("shares") or 0.0),
                }
        except Exception:
            pass
        _PROFILE_LOADED = True
        return _IDX_PROFILE_CACHE


def _load_sector_map() -> Dict[str, str]:
    """Peta kode saham -> sektor (dari 11 file Sectors dataset IDX)."""
    global _SECTOR_LOADED
    if _SECTOR_LOADED:
        return _IDX_SECTOR_CACHE
    with _PROFILE_LOCK:
        if _SECTOR_LOADED:
            return _IDX_SECTOR_CACHE
        base = "https://raw.githubusercontent.com/wildangunawan/Dataset-Saham-IDX/master/List%20Emiten/Sectors/{name}.csv"
        sectors = ["Basic Materials", "Consumer Cyclicals", "Consumer Non-Cyclicals", "Energy",
                   "Financials", "Healthcare", "Industrials", "Infrastructures",
                   "Properties & Real Estate", "Technology", "Transportation & Logistic"]
        for s in sectors:
            try:
                req = urllib.request.Request(base.format(name=urllib.parse.quote(s)),
                                             headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    csv_data = resp.read()
                df_csv = pd.read_csv(io.BytesIO(csv_data))
                col = "code" if "code" in df_csv.columns else df_csv.columns[0]
                for c in df_csv[col].tolist():
                    _IDX_SECTOR_CACHE[str(c).strip().upper()] = s
            except Exception:
                continue
        _SECTOR_LOADED = True
        return _IDX_SECTOR_CACHE


def fetch_fundamentals(ticker: str, last_price: Optional[float] = None,
                        light: bool = False) -> dict:
    """Fundamental emiten (profil + sektor + rasio + laporan + aksi korporasi).

    Sumber: dataset publik IDX (profil & sektor), Yahoo Finance (rasio keuangan,
    perbandingan laporan YoY tahunan/kuartalan, riwayat dividen, stock split),
    dan Financial Modeling Prep sebagai pelengkap untuk bursa di luar IDX.
    light=True: hanya rasio ringkas (tanpa laporan & aksi korporasi) untuk
    endpoint massal seperti /api/quotes.
    """
    code = ticker.upper().replace(".JK", "")
    profiles = _load_company_profiles()
    sectors = _load_sector_map()
    prof = profiles.get(code) or {}
    name = prof.get("name") or ""
    if not name:
        # fallback nama dari meta Yahoo (chart API)
        try:
            chart = _yahoo_chart_cffi(ticker, interval="1d", rng="1mo")
            name = ((chart or {}).get("meta") or {}).get("longName") or ""
        except Exception:
            name = ""
    shares = prof.get("shares") or 0.0
    market_cap = shares * last_price if shares and last_price else None
    # 52 minggu dari Yahoo chart meta (best-effort)
    w52 = {"high": None, "low": None}
    try:
        chart = _yahoo_chart_cffi(ticker, interval="1d", rng="1y")
        meta = (chart or {}).get("meta") or {}
        w52["high"] = meta.get("fiftyTwoWeekHigh")
        w52["low"] = meta.get("fiftyTwoWeekLow")
    except Exception:
        pass
    pct_from_high = None
    if w52["high"] and last_price:
        pct_from_high = (last_price / float(w52["high"]) - 1) * 100

    # --- Fundamental: Yahoo Finance (utama, mencakup IDX) + FMP (pelengkap) ---
    # Kode tanpa titik diperlakukan sebagai emiten IDX bila ada di dataset IDX
    # (kode seperti 'BBCA' juga dipakai instrumen lain di bursa asing).
    sym_yf = ticker.upper()
    if "." not in sym_yf and (code in profiles or code in sectors):
        sym_yf = f"{code}.JK"
    yf_fund = _fetch_yahoo_fundamentals(sym_yf, light=light) or {}
    if not yf_fund and "." not in sym_yf:
        yf_fund = _fetch_yahoo_fundamentals(f"{code}.JK", light=light) or {}
    # Paket FMP yang terpasang tidak mencakup bursa IDX (.JK) sehingga FMP
    # hanya dicoba untuk simbol di bursa lain (mis. saham AS).
    fmp = {} if ".JK" in ticker.upper() else (_fetch_fmp_fundamentals(ticker.upper()) or {})

    if yf_fund.get("name"):
        name = yf_fund["name"]
    elif fmp.get("name"):
        name = fmp["name"]
    for src in (yf_fund, fmp):
        if src.get("marketCap"):
            market_cap = float(src["marketCap"])
            break
    for src in (yf_fund, fmp):
        if src.get("sharesOutstanding"):
            shares = float(src["sharesOutstanding"])
            break

    def pick(*keys: str):
        """Nilai pertama yang tersedia: Yahoo diutamakan, lalu FMP."""
        for src in (yf_fund, fmp):
            for k in keys:
                if src.get(k) is not None:
                    return src[k]
        return None

    out = {
        "code": code,
        "name": name or None,
        "sector": sectors.get(code),
        "listing_date": prof.get("listing_date") or None,
        "board": prof.get("board") or None,
        "shares_outstanding": num(shares, 0) if shares else None,
        "market_cap": num(market_cap, 0) if market_cap else None,
        "fifty_two_week": {
            "high": num(float(w52["high"]), 0) if w52["high"] else None,
            "low": num(float(w52["low"]), 0) if w52["low"] else None,
            "pct_from_high": num(pct_from_high, 1) if pct_from_high is not None else None,
        },
    }

    dy_raw = pick("dividendYieldPercentage")
    if dy_raw is None:
        dy = pick("dividendYield")
        dy_raw = float(dy) * 100 if dy is not None else None
    roe, roa = pick("returnOnEquity"), pick("returnOnAssets")
    npm = pick("netProfitMargin")
    out.update({
        "pe": num(pick("priceToEarningsRatio"), 2),
        "forward_pe": num(pick("forwardPE"), 2),
        "price_to_book": num(pick("priceToBookRatio"), 2),
        "book_value": num(pick("bookValue"), 2),
        "roe_pct": num(float(roe) * 100, 1) if roe is not None else None,
        "roa_pct": num(float(roa) * 100, 1) if roa is not None else None,
        "net_profit_margin_pct": num(float(npm) * 100, 1) if npm is not None else None,
        "eps": num(pick("eps"), 2),
        "forward_eps": num(pick("forwardEps"), 2),
        "revenue": num(pick("revenue"), 0),
        "net_income": num(pick("netIncome"), 0),
        "payout_ratio_pct": num(pick("payoutRatioPercentage"), 1),
        "dividend_yield_pct": num(dy_raw, 2) if dy_raw is not None else None,
        "dividend_per_share": num(pick("dividendPerShare"), 2),
        "ex_dividend_date": pick("exDividendDate"),
        "currency": pick("currency"),
        "financial_currency": pick("financialCurrency"),
        "period_end": pick("period_end"),
        "financials_annual": pick("financials_annual") or [],
        "financials_quarterly": pick("financials_quarterly") or [],
        "dividends": pick("dividends") or [],
        "dividends_annual": pick("dividends_annual") or [],
        "splits": pick("splits") or [],
    })

    sources = []
    if yf_fund:
        sources.append("Yahoo Finance")
    if fmp:
        sources.append("Financial Modeling Prep (FMP)")
    out["data_source"] = " + ".join(sources) or None
    if sources:
        out["note"] = ("Profil, sektor & kapitalisasi pasar dari dataset publik IDX + Yahoo. "
                        "Rasio keuangan (PE, PBV, EPS, ROE/ROA), perbandingan laporan "
                        "YoY tahunan & kuartalan, riwayat dividen, serta aksi korporasi "
                        "(stock split) dari " + " + ".join(sources)
                        + ", periode laporan terakhir " + str(out.get("period_end") or "—") + ".")
    else:
        out["note"] = ("Profil & kapitalisasi pasar dari dataset publik IDX + Yahoo. "
                       "Rasio keuangan sedang tidak tersedia (sumber fundamental "
                       "dibatasi) — coba beberapa saat lagi.")
    return out


def _scan_action_plan(df: pd.DataFrame, action: str,
                      bandarmology: Optional[dict] = None,
                      liquidity_grade: Optional[str] = None) -> Optional[dict]:
    """Rencana aksi ringkas untuk hasil scan: tunggu apa & di harga berapa.

    Dipakai hanya untuk kandidat yang lolos kriteria (jumlahnya sedikit) supaya
    beban scan tidak membengkak. Isinya level harga nyata (SMA, S&R, Fibonacci,
    Bollinger, AVG bandar) sehingga jelas kapan rencana valid atau batal.
    """
    try:
        close = df["Close"].astype(float)
        last = float(close.iloc[-1])
        s20 = num(sma(close, 20).iloc[-1], 2)
        s50 = num(sma(close, 50).iloc[-1], 2)
        s200 = num(sma(close, 200).iloc[-1], 2)
        r14 = num(rsi(close, 14).iloc[-1], 2)
        _, _, hist = macd(close)
        atr14 = num(atr(df, 14).iloc[-1], 2)
        bb_up, bb_mid, bb_lo = bollinger_bands(close, 20, 2.0)
        bb_up_v, bb_mid_v, bb_lo_v = float(bb_up.iloc[-1]), float(bb_mid.iloc[-1]), float(bb_lo.iloc[-1])
        bb_range = (bb_up_v - bb_lo_v) if bb_up_v > bb_lo_v else 0.0
        bb_bw = bb_range / bb_mid_v if bb_mid_v and bb_mid_v > 0 else 0.0
        bb_bw_hist = ((bb_up - bb_lo) / bb_mid.replace(0, float("nan"))).dropna()
        bb_squeeze = bool(len(bb_bw_hist) >= 30 and bb_bw <= float(bb_bw_hist.tail(30).quantile(0.2)))
        sr_zones = find_sr_zones(df)
        fib = fibonacci_levels(df)
        vol = volume_analysis(df)
        weekly_series = _weekly_trend_series(df)
        weekly_up = bool(weekly_series.iloc[-1]) if len(weekly_series) else False
        wk_close = close.resample("W-FRI").last()
        wk_ma = sma(wk_close, 20)
        weekly = {
            "up": weekly_up,
            "close": num(float(wk_close.iloc[-1]), 0) if len(wk_close) else None,
            "sma20": num(float(wk_ma.iloc[-1]), 0) if len(wk_ma) and not np.isnan(wk_ma.iloc[-1]) else None,
        }
        atr_v = float(atr14) if atr14 else 0.0
        rm = risk_management(last, sr_zones, action, atr_v, 5_000_000, force_long=True)
        out = build_action_plan(
            last_price=last, action=action, trend=detect_trend(df), sr_zones=sr_zones,
            fib=fib, weekly=weekly, bandarmology=bandarmology, regime=_ihsg_regime(), rm=rm,
            ind={
                "sma20": s20, "sma50": s50, "sma200": s200, "rsi14": r14, "atr14": atr14,
                "macd": {"histogram": num(hist.iloc[-1], 4)}, "volume": vol,
                "bollinger": {"upper": num(bb_up_v, 2), "lower": num(bb_lo_v, 2),
                              "squeeze": bb_squeeze},
            },
            liquidity_grade=liquidity_grade,
            data_date=str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1]),
        )
        # Ringkas payload scan: hanya yang penting untuk keputusan.
        return {
            "kesimpulan": out.get("kesimpulan"),
            "setup": out.get("setup"),
            "timing": out.get("timing"),
            "langkah": (out.get("langkah") or [])[:5],
            "zona_entry": out.get("zona_entry"),
            "pembatalan": out.get("pembatalan"),
            "target": out.get("target"),
            "trailing": out.get("trailing"),
            "time_stop_hari": out.get("time_stop_hari"),
            "kelayakan": out.get("kelayakan"),
            "level_referensi": out.get("level_referensi"),
            "konflik": out.get("konflik") or [],
            "data_date": out.get("data_date"),
        }
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
    item = {"ticker": tk, "source": src, **result["metrics"], "criteria_met": result["criteria_met"],
            "book_confirm": result.get("book_confirm")}
    item["data_date"] = (str(df.index[-1].date()) if hasattr(df.index[-1], "date")
                          else str(df.index[-1]))
    if include_signal or criteria == "koreksi":
        item["signal"] = quick_signal(df)

    # Broker Summary (Bab 3-7): ditampilkan bila lolos kriteria, atau wajib untuk
    # kriteria "bandar" (ACC + value share Top Buyer >= 60% per buku) dan
    # "koreksi" (butuh AVG bandar & status ACC/DIS untuk level pantauan).
    bandar_used = 0
    need_bandar = criteria in ("bandar", "koreksi") or (include_bandarmology and result["eligible"])
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

    # Skor komposit sinyal beli (optimasi: multi-konfirmasi, lihat compute_buy_score).
    item["buy_score"] = compute_buy_score(df, item.get("bandarmology"))

    def _attach_plan(eligible: bool, act: str = "") -> None:
        """Lampirkan rencana aksi hanya untuk kandidat yang lolos kriteria."""
        if not eligible or criteria not in ("buy", "koreksi", "bandar", "swing"):
            return
        action = act or str(item.get("signal") or ("BUY" if criteria == "buy" else "HOLD"))
        plan = _scan_action_plan(df, action, item.get("bandarmology"),
                                 (item.get("buy_score") or {}).get("liquidity_grade"))
        if plan:
            item["action_plan"] = plan

    if criteria == "bandar":
        # Kriteria Bandarmology buku (Bab 3-7): akumulasi + value share Top Buyer >= 60%.
        b = item.get("bandarmology") or {}
        bandar_ok = bool(b.get("status", "").startswith("ACC") and b.get("value_share_significant"))
        item["criteria_met"] = ["BANDAR"] if bandar_ok else []
        _attach_plan(bandar_ok)
        return {"tk": tk, "skipped": False, "item": item,
                "eligible": bandar_ok, "bandar_used": bandar_used}

    if criteria == "buy":
        # Sinyal beli kuat = skor komposit >= 70 (multi-konfirmasi).
        sc = (item.get("buy_score") or {}).get("score") or 0
        label = "BUY KUAT" if sc >= 70 else ("BUY KONF" if sc >= 50 else "")
        item["criteria_met"] = [label] if label else []
        _attach_plan(sc >= 70)
        return {"tk": tk, "skipped": False, "item": item,
                "eligible": sc >= 70, "bandar_used": bandar_used}

    if criteria == "koreksi":
        # Kandidat BELI KOREKSI: kualitas kuat (skor >= 70) + momentum sedang
        # koreksi (sinyal SELL). BUKAN ajakan beli sekarang — daftar pantau:
        # tunggu konfirmasi reversal (MACD cross up / bertahan di support /
        # breakout AVG bandar) sebelum entry. AVG bandar = support psikologis.
        sc = (item.get("buy_score") or {}).get("score") or 0
        sig = str(item.get("signal") or "")
        b = item.get("bandarmology") or {}
        last = float(df["Close"].iloc[-1])
        sr = find_sr_zones(df)
        sup = [z["price"] for z in sr if z["type"] == "support" and z["price"] < last]
        bavg = b.get("bandar_avg_price")
        koreksi_ok = sc >= 70 and sig in ("SELL", "STRONG SELL")
        item["koreksi_info"] = {
            "nearest_support": num(max(sup), 2) if sup else None,
            "support_distance_pct": num((last / max(sup) - 1) * 100, 1) if sup else None,
            "bandar_avg_price": bavg,
            "bandar_avg_distance_pct": num((last / bavg - 1) * 100, 1) if bavg else None,
            "bandar_status": b.get("status"),
        }
        item["criteria_met"] = ["BELI KOREKSI"] if koreksi_ok else []
        _attach_plan(koreksi_ok, act=str(item.get("signal") or "HOLD"))
        return {"tk": tk, "skipped": False, "item": item,
                "eligible": koreksi_ok, "bandar_used": bandar_used}

    _attach_plan(bool(result["eligible"]))
    return {"tk": tk, "skipped": False, "item": item,
            "eligible": bool(result["eligible"]), "bandar_used": bandar_used}


def _scan(tickers: List[str], criteria: str, period: str, include_signal: bool,
          include_bandarmology: bool = True, require_confirm: bool = False,
          require_regime: bool = False) -> dict:
    matched: List[dict] = []
    scanned = skipped = bandar_used = 0
    # Filter kondisi pasar (IHSG vs MA200) — dihitung sekali, berlaku untuk semua saham.
    regime = _ihsg_regime() if require_regime else None
    regime_blocked = bool(require_regime and regime and regime.get("trend") == "bear")
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
                if regime_blocked:
                    continue  # IHSG di bawah MA200: tahan semua sinyal beli
                if require_confirm and not (out["item"].get("book_confirm") or {}).get("ok"):
                    continue
                matched.append(out["item"])
    return {
        "scanned": scanned,
        "skipped": skipped,
        "bandarmology_checked": bandar_used,
        "market_regime": regime,
        "regime_blocked": regime_blocked,
        "results": matched,
    }


def _structural_levels(low: pd.Series, high: pd.Series, sup_window: int = 20,
                       res_window: int = 120):
    """Support/resistance bergulir untuk backtest (rolling low/high, digeser 1 bar).

    Digeser 1 bar supaya level pada bar i hanya memakai data s/d bar i-1 (tanpa
    look-ahead). Dipakai sebagai pengganti find_sr_zones yang terlalu mahal bila
    dihitung per bar.

    Resistance memakai jendela lebih panjang (default 120 bar ≈ 6 bulan): high
    20-bar terlalu dekat di tren naik sehingga gerbang RRR≥2 menjadi terlalu ketat
    dan hampir semua trade terbuang. Support tetap 20 bar (level terdekat).
    """
    sup = low.rolling(sup_window, min_periods=5).min().shift(1)
    res = high.rolling(res_window, min_periods=20).max().shift(1)
    return sup, res


def _simulate_trade(i: int, n: int, entry: float, atr_v: float,
                    low: pd.Series, high: pd.Series, close: pd.Series, max_hold: int, *,
                    plan_mode: bool = True,
                    sup_s: Optional[pd.Series] = None, res_s: Optional[pd.Series] = None,
                    rr_min: float = 2.0, trail_mult: float = 2.0,
                    cost_pct: float = 0.003) -> dict:
    """Simulasi 1 trade; menyamakan exit backtest dengan rencana aksi nyata.

    plan_mode=True (default, selaras analisis):
      * SL struktural = support terdekat - 0,3×ATR (dibatasi maks 3×ATR);
      * TP di resistance terdekat + 0,3×ATR;
      * gerbang RRR: trade DILEWATI bila ruang ke resistance < rr_min;
      * setelah profit mencapai 1R, SL digeser ke break-even;
      * lalu trailing stop trail_mult×ATR dari puncak.
    plan_mode=False (mode lama): SL 2×ATR tetap, TP 2R tetap.
    Mengembalikan {"skip": True, ...} bila tak layak, atau r/alasan/sl/tp/risk/rrr.
    """
    atr_v = float(atr_v) if atr_v and not np.isnan(atr_v) and atr_v > 0 else entry * 0.02
    res = None
    sup = None
    try:
        if res_s is not None and not pd.isna(res_s.iloc[i]):
            res = float(res_s.iloc[i])
        if sup_s is not None and not pd.isna(sup_s.iloc[i]):
            sup = float(sup_s.iloc[i])
    except Exception:
        res = sup = None

    if plan_mode:
        sl = (sup - 0.3 * atr_v) if sup else (entry - 2 * atr_v)
        if entry - sl > 3 * atr_v:
            sl = entry - 2 * atr_v  # stop terlalu lebar -> batasi
        if sl >= entry:
            sl = entry - 2 * atr_v
        risk = entry - sl
        if risk <= 0:
            return {"skip": True, "alasan": "risk<=0"}
        if res is not None:
            room = res + 0.3 * atr_v - entry
            if room <= 0:
                return {"skip": True, "alasan": "harga di/atas resistance"}
            if room / risk < rr_min:
                return {"skip": True, "alasan": f"RRR {room / risk:.2f} < {rr_min:g}"}
            tp = res + 0.3 * atr_v
        else:
            tp = entry + rr_min * risk
    else:
        risk = max(atr_v * 2, entry * 0.005)
        sl, tp = entry - risk, entry + 2 * risk

    cost_r = (entry * cost_pct) / risk
    stop = sl
    peak = entry
    be_done = False
    reason = "timeout"
    exit_price = None
    for j in range(i + 1, min(i + 1 + max_hold, n)):
        hi = float(high.iloc[j])
        lo = float(low.iloc[j])
        if hi > peak:
            peak = hi
        if lo <= stop:
            reason = "sl"
            exit_price = stop
            break
        if hi >= tp:
            reason = "tp"
            exit_price = tp
            break
        if plan_mode:
            if not be_done and peak >= entry + risk:
                stop = max(stop, entry)
                be_done = True
            if be_done:
                stop = max(stop, peak - trail_mult * atr_v)
    if exit_price is None:
        exit_price = float(close.iloc[min(i + max_hold, n - 1)])
    r = (exit_price - entry) / risk - cost_r
    return {"r": r, "alasan": reason, "sl": sl, "tp": tp, "risk": risk,
            "rrr": (tp - entry) / risk if risk > 0 else None, "be": be_done}


def _backtest_one_safe(*args, **kwargs) -> Optional[dict]:
    """Wrapper aman utk _backtest_one: ticker bermasalah dilewati, bukan crash seluruh backtest."""
    try:
        return _backtest_one(*args, **kwargs)
    except Exception:
        return None


def _backtest_one(ticker: str, criteria: str, years: int,
                  confirm: bool = True, regime: bool = True, bb_confirm: bool = True,
                  div_vol: bool = True, weekly: bool = True, costs: bool = True,
                  ihsg_align: Optional[pd.DataFrame] = None,
                  cost_pct: float = 0.003,
                  plan_mode: bool = True, rr_min: float = 2.0,
                  trail_mult: float = 2.0) -> Optional[dict]:
    """Backtest 1 ticker: sinyal di harga tutup -> SL 2xATR, TP 2R (RRR 1:2, Bab 8).
    scalping/bsjp: hold maks 5 hari; swing/buy: 20 hari. Timeout keluar di harga
    tutup hari terakhir hold (dihitung terpisah dari win/loss).

    Filter optimasi (semua opsional, dijelaskan di UI):
    - confirm    : konfirmasi ala buku (harga > SMA20, RSI < 70, volume > MA20).
    - regime     : IHSG > MA200 pada hari sinyal (tidak melawan pasar bear).
    - bb_confirm : harga di bawah band atas Bollinger & MA20 > MA50 (tidak mengejar overbought).
    - div_vol    : divergensi RSI bullish ATAU RSI < 70, dan volume > rata-rata.
    - weekly     : tren mingguan naik (close > SMA20 mingguan) — multi-timeframe.
    - costs      : biaya + slippage 0,3% round-trip (0,15%/sisi) + timeout exit di close.
    """
    try:
        df = _fetch_backtest_history(ticker, years)
    except Exception:
        return None
    if df is None or len(df) < 60:
        return None
    # SMA20 mingguan butuh riwayat sebelum jendela years -> hitung di data penuh dulu.
    weekly_trend = _weekly_trend_series(df)
    cutoff = df.index[-1] - pd.DateOffset(years=years)
    df = df[df.index >= cutoff]
    weekly_trend = weekly_trend.reindex(df.index, method="ffill")
    if len(df) < 40:
        return None

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    vol = df["Volume"].astype(float)
    value = _value_series(df)
    atr_s = atr(df, 14)
    vma20 = vol.rolling(20).mean()
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    rsi_s = rsi(close, 14)
    value_ma10 = value.rolling(10).mean()
    value_ma20 = value.rolling(20).mean()
    bb_up, bb_mid, bb_lo = bollinger_bands(close, 20, 2.0)
    bull_div, _ = _divergence_series(df)

    bscore = _buy_score_series(df) if criteria == "buy" else None

    # Filter regime IHSG: sejajarkan close & MA200 IHSG ke index df (ffill).
    ihsg_ok = None
    if regime and ihsg_align is not None and len(ihsg_align):
        try:
            s = ihsg_align.reindex(df.index, method="ffill")
            ihsg_ok = (s["c"] > s["m"]).to_numpy(dtype=bool)
        except Exception:
            ihsg_ok = None

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
        elif criteria == "buy":
            # Optimasi sinyal beli: skor komposit multi-konfirmasi (vektor).
            hit = float(bscore.iloc[i]) >= 70.0
        else:  # swing (proksi nilai transaksi)
            hit = (float(value.iloc[i]) > float(value_ma20.iloc[i])
                   and float(value_ma20.iloc[i]) >= 10e9
                   and float(value.iloc[i - 1]) <= float(value.iloc[i])
                   and float(value_ma10.iloc[i]) > float(value_ma20.iloc[i]))
        if hit and confirm and criteria != "buy":
            # Konfirmasi ala buku: bias naik + tidak mengejar overbought + volume hidup.
            hit = (float(close.iloc[i]) > float(sma20.iloc[i])
                   and float(rsi_s.iloc[i]) < 70.0
                   and float(vol.iloc[i]) > float(vma20.iloc[i]))
        if hit and bb_confirm and criteria in ("swing", "buy"):
            # Konfirmasi Bollinger (hanya utk strategi tren swing/buy): entry saat
            # harga MASIH DI DALAM band (di bawah band atas = tidak mengejar harga
            # overbought) & MA20 > MA50. Tidak berlaku utk scalping/BSJP: strategi
            # momentum justru muncul setelah kenaikan besar (di atas band atas).
            hit = (not np.isnan(bb_up.iloc[i]) and not np.isnan(bb_lo.iloc[i])
                   and float(close.iloc[i]) < float(bb_up.iloc[i])
                   and float(close.iloc[i]) > float(bb_lo.iloc[i])
                   and not np.isnan(sma20.iloc[i]) and not np.isnan(sma50.iloc[i])
                   and float(sma20.iloc[i]) > float(sma50.iloc[i]))
        if hit and div_vol:
            # Gerbang divergensi RSI bullish (<=3 hari) ATAU RSI tidak overbought
            # (RSI < 70, tidak mengejar harga jenuh), plus volume di atas rata-rata.
            # Catatan: skor beli >=70 sudah mensyaratkan RSI zona sehat, jadi gate ini
            # menahan sinyal yang volumenya mati atau RSI-nya sudah jenuh (>=70).
            hit = ((bool(bull_div.iloc[i]) or (not np.isnan(rsi_s.iloc[i]) and float(rsi_s.iloc[i]) < 70.0))
                   and float(vol.iloc[i]) > float(vma20.iloc[i]))
        if hit and weekly:
            hit = bool(weekly_trend.iloc[i])
        if hit and regime and ihsg_ok is not None:
            hit = bool(ihsg_ok[i])
        if hit:
            triggers.append(i)
    if not triggers:
        return {"ticker": ticker, "trades": 0}

    max_hold = 5 if criteria in ("scalping", "bsjp") else 20
    # Mode plan (SL struktural + gerbang RRR + BE + trailing) hanya untuk swing/buy.
    plan_eff = bool(plan_mode and criteria not in ("scalping", "bsjp"))
    sup_s, res_s = _structural_levels(low, high, 20) if plan_eff else (None, None)
    wins = losses = timeouts = skipped = 0
    r_sum = 0.0
    for i in triggers:
        entry = float(close.iloc[i])
        a = float(atr_s.iloc[i])
        out = _simulate_trade(
            i, n, entry, a, low, high, close, max_hold,
            plan_mode=plan_eff, sup_s=sup_s, res_s=res_s, rr_min=rr_min,
            trail_mult=trail_mult, cost_pct=(cost_pct if costs else 0.0),
        )
        if out.get("skip"):
            skipped += 1
            continue
        # Timeout tetap dihitung TERPISAH dari win/loss agar win rate tidak
        # terinflasi; kontribusi R-nya tetap masuk rata-rata R per trade.
        if out["alasan"] == "timeout":
            timeouts += 1
        elif out["r"] > 0:
            wins += 1
        else:
            losses += 1
        r_sum += out["r"]
    total = wins + losses + timeouts
    decided = wins + losses
    return {
        "ticker": ticker,
        "trades": total,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "skipped_rrr": skipped,
        "win_rate_pct": num(wins / decided * 100, 1) if decided else None,
        "avg_r": num(r_sum / total, 2) if total else None,
        "max_hold_days": max_hold,
    }


MATRIX_KEYS = ("confirm", "regime", "bb", "div", "weekly")


def _matrix_one_safe(tk: str, criteria: str, years: int,
                     ihsg_align: Optional[pd.DataFrame] = None) -> Optional[dict]:
    """Wrapper aman: satu ticker bermasalah tidak boleh meng-gagalkan seluruh matriks.

    Di Vercel data fallback (IDX Edge/GitHub) bisa punya format berbeda per ticker
    sehingga satu ticker bisa memicu exception di kalkulasi. Ticker yang gagal
    dilewati (None), sisanya tetap diproses — mencegah HTTP 500 seluruh endpoint."""
    try:
        return _matrix_one(tk, criteria, years, ihsg_align)
    except Exception:
        return None


def _matrix_one(tk: str, criteria: str, years: int,
                ihsg_align: Optional[pd.DataFrame] = None,
                plan_mode: bool = True, rr_min: float = 2.0,
                trail_mult: float = 2.0) -> Optional[dict]:
    """Matriks 32 kombinasi utk 1 ticker (dipanggil paralel per ticker).

    Data diambil sekali lalu 32 kombinasi dievaluasi dari deret yang sama.
    Returns {combo_key: agg} atau None bila data tidak cukup."""
    import itertools
    try:
        df = _fetch_backtest_history(tk, years)
    except Exception:
        return None
    if df is None or len(df) < 60:
        return None
    cutoff = df.index[-1] - pd.DateOffset(years=years)
    sub = df[df.index >= cutoff]
    if len(sub) < 40:
        return None

    close = sub["Close"].astype(float); high = sub["High"].astype(float)
    low = sub["Low"].astype(float); vol = sub["Volume"].astype(float)
    value = _value_series(sub)
    atr_s = atr(sub, 14)
    vma20 = vol.rolling(20).mean(); sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean(); rsi_s = rsi(close, 14)
    value_ma10 = value.rolling(10).mean(); value_ma20 = value.rolling(20).mean()
    bb_up, _, bb_lo = bollinger_bands(close, 20, 2.0)
    bull_div, _ = _divergence_series(sub)
    bscore = _buy_score_series(sub) if criteria == "buy" else None
    weekly_trend = _weekly_trend_series(df).reindex(sub.index, method="ffill")
    ihsg_ok = None
    if ihsg_align is not None and len(ihsg_align):
        try:
            s = ihsg_align.reindex(sub.index, method="ffill")
            ihsg_ok = (s["c"] > s["m"]).to_numpy(dtype=bool)
        except Exception:
            ihsg_ok = None

    n = len(sub)
    max_hold = 5 if criteria in ("scalping", "bsjp") else 20
    plan_eff = bool(plan_mode and criteria not in ("scalping", "bsjp"))
    sup_s, res_s = _structural_levels(low, high, 20) if plan_eff else (None, None)
    combos = list(itertools.product((True, False), repeat=5))
    out: Dict[str, dict] = {}
    for combo in combos:
        confirm, use_reg, use_bb, use_div, use_wk = combo
        ck = "-".join("1" if c else "0" for c in combo)
        triggers = []
        for i in range(20, n - 1):
            last, prev = float(close.iloc[i]), float(close.iloc[i - 1])
            day_ret = (last / prev - 1) * 100 if prev > 0 else 0.0
            v = float(value.iloc[i])
            vr = float(vol.iloc[i]) / float(vma20.iloc[i]) if vma20.iloc[i] > 0 else 0.0
            if criteria == "scalping":
                hit = v >= 1e9 and day_ret >= 10.0 and last > 50
            elif criteria == "bsjp":
                hit = v >= 5e9 and day_ret >= 8.0 and vr >= 2.0
            elif criteria == "buy":
                hit = float(bscore.iloc[i]) >= 70.0
            else:
                hit = (float(value.iloc[i]) > float(value_ma20.iloc[i])
                       and float(value_ma20.iloc[i]) >= 10e9
                       and float(value.iloc[i - 1]) <= float(value.iloc[i])
                       and float(value_ma10.iloc[i]) > float(value_ma20.iloc[i]))
            if hit and confirm and criteria != "buy":
                hit = (float(close.iloc[i]) > float(sma20.iloc[i])
                       and float(rsi_s.iloc[i]) < 70.0
                       and float(vol.iloc[i]) > float(vma20.iloc[i]))
            if hit and use_bb and criteria in ("swing", "buy"):
                hit = (not np.isnan(bb_up.iloc[i]) and not np.isnan(bb_lo.iloc[i])
                       and float(close.iloc[i]) < float(bb_up.iloc[i])
                       and float(close.iloc[i]) > float(bb_lo.iloc[i])
                       and float(sma20.iloc[i]) > float(sma50.iloc[i]))
            if hit and use_div:
                hit = ((bool(bull_div.iloc[i]) or (not np.isnan(rsi_s.iloc[i]) and float(rsi_s.iloc[i]) < 70.0))
                       and float(vol.iloc[i]) > float(vma20.iloc[i]))
            if hit and use_wk:
                hit = bool(weekly_trend.iloc[i])
            if hit and use_reg and ihsg_ok is not None:
                hit = bool(ihsg_ok[i])
            if hit:
                triggers.append(i)
        if not triggers:
            continue
        a = {"trades": 0, "wins": 0, "losses": 0, "timeouts": 0, "skipped": 0, "r": 0.0}
        for i in triggers:
            entry = float(close.iloc[i])
            atr_v = float(atr_s.iloc[i])
            if np.isnan(atr_v):
                atr_v = entry * 0.02
            sim = _simulate_trade(
                i, n, entry, atr_v, low, high, close, max_hold,
                plan_mode=plan_eff, sup_s=sup_s, res_s=res_s, rr_min=rr_min,
                trail_mult=trail_mult, cost_pct=0.003,
            )
            if sim.get("skip"):
                a["skipped"] += 1
                continue
            if sim["alasan"] == "timeout":
                a["timeouts"] += 1
            elif sim["r"] > 0:
                a["wins"] += 1
            else:
                a["losses"] += 1
            a["r"] += sim["r"]
            a["trades"] += 1
        out[ck] = a
    return out if out else None


def _matrix_finish(agg: Dict[str, dict]) -> dict:
    """Rangkum agg {combo_key: totals} menjadi daftar combo + konfigurasi terbaik."""
    results = []
    for ck, a in agg.items():
        decided = a["wins"] + a["losses"]
        results.append({
            "key": ck,
            "labels": dict(zip(MATRIX_KEYS, [x == "1" for x in ck.split("-")])),
            "trades": a["trades"],
            "wins": a["wins"],
            "losses": a["losses"],
            "timeouts": a["timeouts"],
            "skipped": a.get("skipped", 0),
            "win_rate_pct": num(a["wins"] / decided * 100, 1) if decided else None,
            "avg_r": num(a["r"] / a["trades"], 2) if a["trades"] else None,
        })
    # Konfigurasi terbaik: avg R tertinggi dengan minimal 5 trade DAN minimal 1
    # trade yang dituntaskan SL/TP (bukan hanya timeout). Kalau tak ada, ambil
    # yang trade-nya terbanyak. Sisanya urut avg_r menurun.
    candidates = [r for r in results if r["trades"] >= 5 and (r["wins"] + r["losses"]) > 0]
    best_key = None
    if candidates:
        best = max(candidates, key=lambda r: (r["avg_r"] or -99, r["wins"] + r["losses"]))
        best_key = best["key"]
    if best_key is None:
        decided_any = [r for r in results if (r["wins"] + r["losses"]) > 0]
        if decided_any:
            best_key = max(decided_any, key=lambda r: (r["avg_r"] or -99))["key"]
        elif results:
            best_key = max(results, key=lambda r: r["trades"])["key"]
    results.sort(key=lambda r: (r["avg_r"] or -99), reverse=True)
    return {"best_key": best_key, "combos": results}


def _backtest_matrix(tickers: List[str], criteria: str, years: int,
                     ihsg_align: Optional[pd.DataFrame] = None,
                     plan_mode: bool = True, rr_min: float = 2.0,
                     trail_mult: float = 2.0) -> dict:
    """Matriks 32 kombinasi filter, PARALEL per ticker (fix timeout 504).

    criteria='all' menghitung 4 kriteria sekaligus (swing, scalping, bsjp, buy);
    data yang sudah diunduh dipakai ulang (cache proses) utk kriteria lain.
    Biaya 0,3% round-trip selalu diterapkan agar avg_r realistis.
    """
    import itertools
    criteria_list = ["swing", "scalping", "bsjp", "buy"] if criteria == "all" else [criteria]
    all_keys = list(itertools.product((True, False), repeat=5))
    per_criteria: Dict[str, dict] = {}
    overall: Dict[str, dict] = {}
    checked_total = 0
    for crit in criteria_list:
        agg = {("-".join("1" if c else "0" for c in combo)): {"trades": 0, "wins": 0, "losses": 0,
                                                              "timeouts": 0, "skipped": 0, "r": 0.0}
               for combo in all_keys}
        checked = 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            for one in ex.map(lambda tk: _matrix_one_safe(tk, crit, years, ihsg_align,
                                                          plan_mode, rr_min, trail_mult), tickers):
                if one is None:
                    continue
                checked += 1
                for ck, a in one.items():
                    d = agg[ck]
                    d["trades"] += a["trades"]
                    d["wins"] += a["wins"]
                    d["losses"] += a["losses"]
                    d["timeouts"] += a["timeouts"]
                    d["skipped"] += a.get("skipped", 0)
                    d["r"] += a["r"]
        checked_total = checked
        fin = _matrix_finish(agg)
        per_criteria[crit] = {"tickers_checked": checked, **fin}
        # simpan yang terbaik dari kriteria ini utk perbandingan lintas kriteria
        best = next((r for r in fin["combos"] if r["key"] == fin["best_key"]), None)
        overall[crit] = {"criteria": crit, "key": fin["best_key"], **(best or {})}

    if criteria == "all":
        dec = [o for o in overall.values() if o.get("key") and (o.get("wins", 0) + o.get("losses", 0)) > 0]
        pool = dec if dec else list(overall.values())
        best_overall = max(pool, key=lambda o: (o.get("avg_r") or -99, o.get("trades", 0))) if pool else None
        return {
            "criteria": "all",
            "years": years,
            "tickers_checked": checked_total,
            "per_criteria": per_criteria,
            "best_overall": best_overall,
            "note": ("Matriks 4 kriteria x 32 kombinasi filter (konfirmasi buku x IHSG>MA200 x "
                     "Bollinger x divergensi+volume x tren mingguan) dihitung PARALEL. Biaya+"
                     "slippage 0,3% round-trip selalu termasuk. Kinerja masa lalu BUKAN jaminan masa depan."),
        }
    return {
        "criteria": criteria,
        "years": years,
        "tickers_checked": checked_total,
        **per_criteria[criteria],
        "note": ("Matriks 32 kombinasi filter (konfirmasi buku x IHSG>MA200 x Bollinger x "
                 "divergensi+volume x tren mingguan). Biaya+slippage 0,3% round-trip selalu "
                 "termasuk. Win rate dihitung dari trade yang dituntaskan (SL/TP); timeout "
                 "keluar di harga tutup dan dihitung di avg R. Kombinasi terbaik = avg R "
                 "tertinggi dengan minimal 5 trade. Kinerja masa lalu BUKAN jaminan masa depan."),
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
    """Alamat utama langsung membuka dashboard (tidak perlu /dashboard).

    Bila dashboard.html tidak ada (mis. deploy API saja), kembalikan daftar
    endpoint JSON agar alamat utama tetap berguna.
    """
    if os.path.exists(DASHBOARD_PATH):
        return FileResponse(DASHBOARD_PATH, media_type="text/html")
    return api_info()


@app.get("/api/info")
def api_info():
    """Daftar endpoint API (versi JSON dari halaman utama)."""
    return {
        "service": "dedesaputra_invst Strategy API",
        "dashboard": "/",
        "endpoints": [
            "GET  /api/health",
            "GET  /api/analyze/{ticker}?period=1y&fundamentals=off|ringkas|full",
            "GET  /api/fundamentals/{ticker}?level=ringkas|full",
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


@app.get("/dashboard")
def dashboard_alias():
    """Alias lama: /dashboard -> halaman utama."""
    return root()


DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard.html")


@app.get("/dashboard")
def dashboard():
    """Halaman dashboard web sederhana (HTML statis + vanilla JS)."""
    if not os.path.exists(DASHBOARD_PATH):
        raise HTTPException(404, "File dashboard.html tidak ditemukan.")
    return FileResponse(DASHBOARD_PATH, media_type="text/html")


def _fmp_selfcheck() -> dict:
    """Uji nyata kunci FMP: cek simbol saham AS dan simbol IDX (.JK).

    Dipakai endpoint /api/health?fmp_check=1 supaya kelihatan apakah kunci FMP
    benar-benar hidup dan bursa mana saja yang tercakup paketnya.
    """
    if not FMP_API_KEY:
        return {"key_set": False, "note": "FMP_API_KEY belum di-set."}
    us = _fmp_get("quote", {"symbol": "AAPL"})
    if us is None and time.time() < FMP_QUOTA_UNTIL:
        return {"key_set": True, "kuota": "habis (HTTP 429) — otomatis aktif lagi setelah kuota reset",
                "saham_as": "tidak dapat diuji", "saham_idx": "tidak dapat diuji"}
    idx = _fmp_get("quote", {"symbol": "BBCA.JK"})
    return {
        "key_set": True,
        "saham_as": "ok" if isinstance(us, list) and us else "gagal/tidak tercakup",
        "saham_idx": "ok" if isinstance(idx, list) and idx else "tidak tercakup paket",
        "catatan": ("Paket FMP gratis tidak mencakup bursa Indonesia, jadi rasio "
                    "fundamental saham IDX diambil dari Yahoo Finance."),
    }


@app.get("/api/health")
def health(fmp_check: bool = Query(False, description="Uji kunci FMP ke API-nya")):
    out = {
        "sync_enabled": SYNC_ENABLED,
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "idx_edge_quota_out": idx_edge_quota_out(),
        "idx_edge_keys": len(IDX_EDGE_API_KEYS),
        "fmp_configured": bool(FMP_API_KEY),
        "fmp_quota_out": time.time() < FMP_QUOTA_UNTIL,
        "fundamental_source": "Yahoo Finance (mencakup IDX) + FMP untuk bursa lain",
        "market_regime": _ihsg_regime(),
        "status": "ok",
        "service": "dedesaputra_invst Strategy API",
        "time": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }
    if fmp_check:
        out["fmp_check"] = _fmp_selfcheck()
    return out


def build_action_plan(*, last_price: float, action: str, trend: dict, sr_zones: List[dict],
                      fib: dict, weekly: dict, bandarmology: Optional[dict],
                      regime: Optional[dict], rm: Optional[dict], ind: dict,
                      liquidity_grade: Optional[str] = None, data_date: str = "") -> dict:
    """Panduan aksi: apa yang harus ditunggu dan di harga berapa.

    Bukan ajakan beli/jual. Isinya syarat konfirmasi yang bisa dicek sendiri,
    memakai level hasil hitungan terakhir (SMA, Support/Resistance, Fibonacci,
    Bollinger, AVG bandar) supaya jelas kapan rencana ini valid atau batal.
    """
    def _rp(v: Any, nd: int = 0) -> str:
        return f"{float(v):,.{nd}f}".replace(",", ".") if isinstance(v, (int, float)) else "—"

    zones = sr_zones or []
    res = [z for z in zones if z.get("price") and z["price"] > last_price and z.get("type") == "resistance"]
    sup = [z for z in zones if z.get("price") and z["price"] < last_price and z.get("type") == "support"]
    res_above = min(res, key=lambda z: z["price"]) if res else None
    sup_below = max(sup, key=lambda z: z["price"]) if sup else None
    lv_res = res_above["price"] if res_above else None
    lv_sup = sup_below["price"] if sup_below else None

    s20, s50 = ind.get("sma20"), ind.get("sma50")
    rsi = ind.get("rsi14")
    hist = (ind.get("macd") or {}).get("histogram")
    vol_ratio = (ind.get("volume") or {}).get("ratio_to_ma20")
    bb = ind.get("bollinger") or {}
    atr14 = ind.get("atr14")
    bavg = (bandarmology or {}).get("bandar_avg_price")
    bstatus = (bandarmology or {}).get("status")
    fib_level = (fib or {}).get("nearest_level") or {}
    lv_fib = fib_level.get("price")
    opsi_mingguan = f"harga {_rp(weekly.get('close'))} vs SMA20 mingguan {_rp(weekly.get('sma20'))}"

    aksi = str(action or "HOLD").upper()
    steps: List[dict] = []

    def step(syarat: str, sekarang: str, level: Optional[float] = None, wajib: bool = False) -> None:
        steps.append({"syarat": syarat, "sekarang": sekarang,
                      "level": num(level, 2) if level else None, "wajib": bool(wajib)})

    vol_txt = f"{vol_ratio:.2f}× MA20" if isinstance(vol_ratio, (int, float)) else "—"
    if "SELL" in aksi:
        kesimpulan = ("Belum waktunya entry. Sinyal masih jual — tunggu pembalikan tren "
                      "terkonfirmasi lebih dulu, jangan menebak dasar (bottom fishing).")
        step("MACD memotong garis sinyal ke atas (histogram berubah positif)",
             f"histogram {_rp(hist, 3)}", None, True)
        step("Harga ditutup kembali di atas SMA20",
             f"harga {_rp(last_price)} vs SMA20 {_rp(s20)}", s20, True)
        step("Volume saat harga naik minimal 1,5× rata-rata 20 hari", vol_txt)
        step("RSI(14) kembali di atas 50", _rp(rsi, 1), 50.0)
        if lv_res:
            step("Breakout menutup di atas resistance terdekat (area ini jadi support baru)",
                 f"harga {_rp(last_price)} vs resistance {_rp(lv_res)}", lv_res)
        if bavg:
            step(f"Harga bertahan/rebound di AVG bandar ({bstatus or 'ACC/DIS'})",
                 f"AVG bandar {_rp(bavg)}", bavg)
    elif "BUY" in aksi:
        kesimpulan = ("Momentum beli aktif — entry boleh bertahap selama syarat konfirmasi "
                      "masih terpenuhi; jangan kejar harga di atas band atas.")
        step("Harga bertahan di atas SMA20", f"harga {_rp(last_price)} vs SMA20 {_rp(s20)}", s20, True)
        step("Volume minimal 1,5× rata-rata 20 hari saat menembus level", vol_txt, None, True)
        step("RSI(14) masih sehat (di bawah 70, tidak overbought)", _rp(rsi, 1), 70.0)
        if lv_sup:
            step("Koreksi sehat tidak menembus support terdekat (batas toleransi)",
                 f"support {_rp(lv_sup)}", lv_sup, True)
        if bavg:
            step("Harga masih di area AVG bandar (titik masuk bandar)", f"AVG bandar {_rp(bavg)}", bavg)
    else:
        kesimpulan = ("Sinyal netral — belum ada alasan kuat untuk masuk. Tunggu salah satu "
                      "skenario: breakout dengan volume, atau pantulan di support.")
        step("Skenario 1 — breakout menutup di atas resistance dengan volume ≥1,5× MA20",
             f"resistance {_rp(lv_res)} (harga sekarang {_rp(last_price)})", lv_res, True)
        step("Skenario 2 — pantulan di support dengan candle bullish + RSI naik",
             f"support {_rp(lv_sup)}", lv_sup)
        step("MACD di atas garis sinyal (konfirmasi momentum)", f"histogram {_rp(hist, 3)}")
        step("Tren mingguan searah (close di atas SMA20 mingguan)", opsi_mingguan)

    # Zona support terdekat — dipakai hanya sebagai fallback bila belum ada setup rapi.
    # Level Fibonacci hanya dipakai bila memang DI BAWAH harga; kalau tidak, levelnya
    # bisa di atas harga dan menghasilkan "zona entry" yang menyesatkan.
    if lv_sup:
        zona_sup = {"low": num(sup_below["band"][0] if sup_below.get("band") else lv_sup, 2),
                    "high": num(sup_below["band"][1] if sup_below.get("band") else lv_sup, 2)}
    elif isinstance(lv_fib, (int, float)) and lv_fib < last_price:
        zona_sup = {"low": num(lv_fib, 2), "high": num(lv_fib, 2)}
    else:
        zona_sup = {"low": None, "high": None}

    sl = (rm or {}).get("stop_loss") or (last_price - 2 * atr14 if isinstance(atr14, (int, float)) and atr14 > 0 else None)
    tp = (rm or {}).get("take_profit")

    # --- Level plan swing: SL struktural, TP bertingkat, gerbang RRR, trailing ---
    atr_v = float(atr14) if isinstance(atr14, (int, float)) and atr14 and atr14 > 0 else last_price * 0.02
    res_list = sorted([z["price"] for z in res if z.get("price")], key=lambda x: x)
    tp1 = (res_list[0] + 0.3 * atr_v) if res_list else (tp or last_price + 2 * atr_v)
    tp2 = (res_list[1] + 0.3 * atr_v) if len(res_list) > 1 else (tp1 + 1.5 * atr_v)
    # sl_v, RRR, dan kelayakan dihitung SETELAH setup, dari HARGA ENTRY yang
    # direncanakan (pullback: SMA20/zona, breakout: resistance) — bukan harga sekarang.

    # --- Setup swing: pullback di tren naik vs breakout ---
    rsi_ok = isinstance(rsi, (int, float)) and 35 <= rsi <= 68
    trend_up = bool(trend.get("direction") == "uptrend" or
                    (isinstance(s20, (int, float)) and isinstance(s50, (int, float)) and last_price > s20 > s50))
    dist_s20 = abs(last_price / s20 - 1) * 100 if isinstance(s20, (int, float)) and s20 else None
    dist_sup = ((last_price - lv_sup) / last_price * 100) if lv_sup else None
    dist_res = ((lv_res - last_price) / last_price * 100) if lv_res else None
    vol_ok = isinstance(vol_ratio, (int, float)) and vol_ratio >= 1.5
    near_pull = ((dist_s20 is not None and dist_s20 <= 3.0)
                 or (dist_sup is not None and dist_sup <= 3.0))
    bb_upper = bb.get("upper")
    overbought = isinstance(bb_upper, (int, float)) and last_price > bb_upper

    if trend_up and near_pull and rsi_ok and not overbought:
        setup = {"jenis": "Pullback di tren naik", "kualitas": "terbaik",
                 "deskripsi": ("Harga mundur sehat ke area SMA20/support dalam tren naik — ini titik masuk "
                               "swing dengan peluang terbaik. Beli saat muncul kekuatan kembali, bukan saat "
                               "harga masih jatuh."),
                 "trigger": {
                     "level": num(max([v for v in (s20, lv_sup) if isinstance(v, (int, float))] or [last_price]), 2),
                     "syarat": ("Close kembali di atas level ini (di atas SMA20/zona support) DENGAN candle "
                                "bullish (hammer/engulfing) dan volume ≥1,5× MA20"),
                     "sekarang": f"harga {_rp(last_price)} · RSI {_rp(rsi, 1)} · volume {vol_txt}"}}
    elif lv_res and dist_res is not None and dist_res <= 2.0 and (vol_ok or bb.get("squeeze")) and not overbought:
        setup = {"jenis": "Breakout", "kualitas": "bagus",
                 "deskripsi": ("Harga menekan resistance dengan tenaga. Entry agresif saat close menembus "
                               "resistance; entry lebih aman saat pullback pertama ke level breakout."),
                 "trigger": {
                     "level": num(lv_res, 2),
                     "syarat": "Close DI ATAS resistance ini dengan volume ≥1,5× MA20 (bukan sekadar menyentuh)",
                     "sekarang": f"harga {_rp(last_price)} vs resistance {_rp(lv_res)} · volume {vol_txt}"}}
    else:
        setup = {"jenis": "Tunggu (belum ada setup)", "kualitas": "belum",
                 "deskripsi": ("Belum ada setup pullback maupun breakout yang rapi. Menunggu lebih baik "
                               "daripada memaksa entry."),
                 "trigger": {
                     "level": num((lv_res if lv_res else tp1), 2),
                     "syarat": ("Pullback ke SMA20/support (cari pantulan), ATAU close menembus resistance "
                                "dengan volume ≥1,5× MA20"),
                     "sekarang": f"harga {_rp(last_price)} · RSI {_rp(rsi, 1)}"}}
    # --- Harga acuan entry & zona SESUAI SETUP (bukan support terjauh) ---
    # Contoh: sinyal BUY di 4.320 dengan SMA20 4.264 -> zona entry ±SMA20 (4.218–4.295),
    # bukan support jauh 4.040–4.090 yang mustahil tersentuh saat momentum naik.
    # Penting: RRR dihitung dari harga entry yang DIRENCANAKAN. Bila harga sudah naik
    # di atas zona, entry tetap di rencana (tunggu pullback), bukan dikejar.
    if setup["jenis"].startswith("Pullback"):
        entry_ref = float(setup["trigger"].get("level") or last_price)
        z_low, z_high = entry_ref - 0.6 * atr_v, entry_ref + 0.4 * atr_v
        sl_v = z_low - 1.0 * atr_v
        zona_note = ("Zona beli saat harga pullback ke SMA20/zona support lalu memantul. Masuk hanya bila "
                     "muncul candle bullish + volume; close di bawah batas bawah = rencana batal.")
    elif setup["jenis"] == "Breakout":
        entry_ref = float(setup["trigger"].get("level") or last_price)
        z_low, z_high = entry_ref - 0.2 * atr_v, entry_ref + 0.5 * atr_v
        sl_v = entry_ref - 1.5 * atr_v
        zona_note = ("Zona entry breakout: beli saat close menembus resistance (agresif), atau lebih aman "
                     "saat pullback pertama ke level breakout.")
    else:
        entry_ref = last_price
        z_low, z_high = last_price - 0.5 * atr_v, last_price + 0.5 * atr_v
        sl_v = last_price - 2.0 * atr_v
        zona_note = ("Belum ada setup rapi. Tunggu pullback ke SMA20/support dengan pantulan, atau close "
                     "menembus resistance dengan volume.")
    # Batasi risiko maksimal 2×ATR agar stop tidak terlalu lebar (RRR tetap sehat).
    if entry_ref - sl_v > 2.0 * atr_v:
        sl_v = entry_ref - 2.0 * atr_v
    max_chase = entry_ref + 1.0 * atr_v
    _res_txt = (f" Alternatif breakout: close di atas {_rp(lv_res)} dengan volume ≥1,5× MA20."
                if lv_res else "")
    if setup["jenis"] == "Tunggu (belum ada setup)":
        if isinstance(s20, (int, float)) and s20 and last_price >= s20:
            # Di atas SMA20 tanpa setup: pantau area PULLBACK ke SMA20 (bukan support
            # terjauh) agar level yang ditampilkan tetap masuk akal.
            zona = {"low": num(s20 - 0.6 * atr_v, 2), "high": num(s20 + 0.4 * atr_v, 2),
                    "catatan": ("Belum ada setup — pantau PULLBACK ke SMA20 di area ini, lalu tunggu "
                                "candle bullish + volume sebelum masuk." + _res_txt)}
        elif isinstance(s20, (int, float)) and s20:
            # Di bawah SMA20 (tren belum naik): level pertama yang perlu DIREBUT adalah SMA20,
            # bukan support terjauh atau resistance yang bisa jauh di atas. Ini level reversal.
            zona = {"low": num(s20 - 0.4 * atr_v, 2), "high": num(s20 + 0.4 * atr_v, 2),
                    "catatan": ("Belum ada setup beli (harga masih di bawah SMA20). Level pertama yang "
                                "perlu direbut: SMA20. Tunggu harga kembali DI ATAS SMA20 dengan volume"
                                + (f", lalu konfirmasi breakout di atas {_rp(lv_res)}." if lv_res else "."))}
        elif lv_res:
            # Tanpa SMA20: tampilkan level BREAKOUT yang perlu ditembus (bukan zona beli).
            zona = {"low": num(lv_res - 0.3 * atr_v, 2), "high": num(lv_res + 0.5 * atr_v, 2),
                    "catatan": ("Belum ada setup beli. Zona ini adalah level BREAKOUT yang dipantau — "
                                "masuk hanya bila harga ditutup DI ATAS resistance dengan volume ≥1,5× MA20.")}
        elif zona_sup.get("low") is not None:
            zona = dict(zona_sup)
            zona["catatan"] = ("Belum ada setup rapi — area pantau support. " + zona_note + _res_txt)
        else:
            zona = {"low": num(last_price - 0.5 * atr_v, 2), "high": num(last_price + 0.5 * atr_v, 2),
                    "catatan": "Belum ada level jelas (belum ada support/resistance valid) — tunggu arah terkonfirmasi."}
    else:
        zona = {"low": num(z_low, 2), "high": num(z_high, 2), "catatan": zona_note}
    risk_v = entry_ref - sl_v
    reward_v = tp1 - entry_ref
    rrr = (reward_v / risk_v) if risk_v > 0 else None
    layak = bool(rrr is not None and rrr >= 2.0 and tp1 > entry_ref)
    setup["layak_entry"] = bool(layak and setup["jenis"] != "Tunggu (belum ada setup)")
    setup["harga_entry"] = num(entry_ref, 2)
    setup["batas_kejar"] = num(max_chase, 2)

    # --- Timing score: seberapa tepat WAKTUNYA masuk (0-100) ---
    t_parts: Dict[str, float] = {}
    p_s20 = 0.0
    if dist_s20 is not None:
        p_s20 = 25.0 if dist_s20 <= 2 else (18.0 if dist_s20 <= 4 else (8.0 if dist_s20 <= 7 else 0.0))
    t_parts["Dekat SMA20 (pullback sehat)"] = p_s20
    p_rsi = (20.0 if (isinstance(rsi, (int, float)) and 40 <= rsi <= 60) else
             (12.0 if (isinstance(rsi, (int, float)) and ((30 <= rsi < 40) or (60 < rsi <= 70))) else 0.0))
    t_parts["RSI zona sehat (40-60)"] = p_rsi
    p_ma = (20.0 if (isinstance(s20, (int, float)) and isinstance(s50, (int, float)) and last_price > s20 > s50) else
            (10.0 if (isinstance(s20, (int, float)) and last_price > s20) else 0.0))
    t_parts["Struktur MA naik (harga>SMA20>SMA50)"] = p_ma
    p_macd = 20.0 if (isinstance(hist, (int, float)) and hist > 0) else 0.0
    t_parts["MACD histogram positif"] = p_macd
    p_vol = 15.0 if vol_ok else (8.0 if isinstance(vol_ratio, (int, float)) and vol_ratio >= 1.0 else 0.0)
    t_parts["Volume mendukung (≥MA20)"] = p_vol
    timing_score = min(100.0, p_s20 + p_rsi + p_ma + p_macd + p_vol)
    timing = {
        "skor": num(timing_score, 0),
        "label": ("WAKTU BAIK" if timing_score >= 70 else "CUKUP" if timing_score >= 45 else "BELUM TEPAT"),
        "komponen": t_parts,
        "catatan": ("Timing menilai ketepatan WAKTU masuk (bukan kualitas saham): posisi harga vs SMA20, RSI, "
                    "struktur MA, MACD, dan volume. Skor tinggi + setup valid = titik masuk terbaik."),
    }

    konflik: List[str] = []
    if weekly:
        if weekly.get("up") and "SELL" in aksi:
            konflik.append("Tren mingguan masih naik — sinyal jual harian bisa hanya koreksi sehat; "
                           "konfirmasi dulu di chart mingguan.")
        if (not weekly.get("up")) and "BUY" in aksi:
            konflik.append("Melawan arus mingguan: tren mingguan masih turun.")
    if regime and str(regime.get("trend")) == "bear":
        konflik.append("IHSG di bawah MA200 (pasar bear) — filter regime menahan sinyal beli dan "
                       "peluang sinyal palsu naik.")
    if bstatus and str(bstatus).upper().startswith("ACC") and "SELL" in aksi:
        konflik.append(f"Bandar masih akumulasi (AVG {_rp(bavg)}) — koreksi bisa jadi kesempatan, "
                       "tapi tetap tunggu konfirmasi reversal sebelum entry.")
    if bstatus and str(bstatus).upper().startswith("DIS") and "BUY" in aksi:
        konflik.append("Bandar sedang distribusi (DIS) — risiko jual bandar, perketat stop loss.")
    if bb.get("squeeze"):
        konflik.append("Bollinger menyempit (squeeze) — potensi breakout; tunggu arah keluar band "
                       f"({_rp(bb.get('lower'))}–{_rp(bb.get('upper'))}).")
    if liquidity_grade and str(liquidity_grade).lower().startswith(("kurang", "tipis")):
        konflik.append("Likuiditas tipis — pakai order kecil dan hati-hati spread lebar.")
    # Gerbang RRR: ruang ke resistance harus minimal 1:2 dari risiko.
    if rrr is not None and not layak:
        konflik.append(f"RRR hanya 1:{_rp(rrr, 2)} (< 1:2) — ruang ke resistance terlalu dekat. Tunggu harga "
                       "koreksi ke support agar rasio membaik, atau lewati trade ini.")
    elif rrr is None:
        konflik.append("RRR tidak dapat dihitung (stop loss belum valid) — jangan entry sebelum SL jelas.")

    return {
        "kesimpulan": kesimpulan,
        "langkah": steps,
        "setup": setup,
        "timing": timing,
        "zona_entry": zona,
        "pembatalan": ({
            "level": num(sl_v, 2),
            "catatan": ("Rencana batal bila harga ditutup di bawah level ini (SL di bawah zona entry, "
                        "maks 2× ATR). Dihitung dari harga entry rencana, bukan harga sekarang."),
        } if sl_v else None),
        "target": {"tp1": num(tp1, 2), "tp2": num(tp2, 2),
                   "catatan": ("TP1 di resistance terdekat (jual sebagian, geser SL ke break-even), "
                               "TP2 di resistance berikutnya/Fibonacci.")},
        "trailing": {
            "jenis": "Trailing 2×ATR dari puncak (mulai setelah TP1 / profit ≥1R)",
            "level_saat_ini": num(max([v for v in (s20, last_price - 2 * atr_v) if isinstance(v, (int, float))]), 2),
            "catatan": "Setelah TP1 tercapai, geser SL ke harga entry (break-even), lalu ikuti kenaikan.",
        },
        "time_stop_hari": 20,
        "kelayakan": {
            "rrr": num(rrr, 2), "rr_min": 2.0, "layak": layak,
            "alasan": ("Ruang ke resistance memadai (RRR ≥ 1:2)." if layak else
                       ("Ruang ke resistance terlalu dekat — tunggu koreksi atau lewati." if rrr is not None else
                        "Stop loss belum valid sehingga RRR tak dapat dihitung.")),
        },
        "level_referensi": {
            "resistance_terdekat": num(lv_res, 2),
            "support_terdekat": num(lv_sup, 2),
            "sma20": num(s20, 2), "sma50": num(s50, 2),
            "fib_terdekat": num(lv_fib, 2),
            "avg_bandar": num(bavg, 2),
            "bb_atas": num(bb.get("upper"), 2), "bb_bawah": num(bb.get("lower"), 2),
        },
        "konflik": konflik,
        "data_date": data_date,
        "catatan": ("Level dihitung dari data harian s/d " + (data_date or "—") +
                    ". Bila sumber data berganti (mis. yfinance -> IDX Edge), angkanya bisa "
                    "bergeser sedikit — cek label sumber di header analisis."),
        "disclaimer": DISCLAIMER,
    }


def _analyze_core(ticker: str, period: str, risk_amount: float,
                  light: bool = False, fund_level: str = "full") -> dict:
    """Analisis lengkap 1 saham.

    light=True -> tanpa bandarmology (hemat kuota IDX Edge).
    fund_level -> 'full' (rasio + laporan YoY + dividen + aksi korporasi),
    'ringkas' (hanya rasio, 1 permintaan), atau 'off' (tanpa fundamental).
    """
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
    # Deteksi data historis TIDAK sinkron dengan harga live. Ini terjadi saat
    # yfinance diblokir (mis. di Vercel) sehingga data historis berasal dari
    # dataset lama yang belum disesuaikan aksi korporasi (split/dividen) atau
    # berakhir jauh di masa lalu: close historis (mis. 37) vs harga live (179)
    # bisa beda >25% -> seluruh indikator & TP/SL jadi TIDAK VALID.
    data_warning = None
    hist_last = float(close.iloc[-1])
    if has_quote and hist_last > 0:
        dev = abs(quote["last_price"] / hist_last - 1) * 100
        if dev > 25:
            data_warning = (
                f"Data historis tidak sinkron dengan harga live: close historis "
                f"{num(hist_last, 2)} vs harga live {num(quote['last_price'], 2)} "
                f"(beda {num(dev, 0)}%). Kemungkinan aksi korporasi (split/dividen) "
                f"belum disesuaikan di dataset lama, atau data historis berakhir jauh "
                f"di masa lalu. Indikator & TP/SL di bawah TIDAK valid — jangan "
                f"dipakai untuk keputusan beli/jual."
            )
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
    bb_up, bb_mid, bb_lo = bollinger_bands(close, 20, 2.0)
    bb_up_v, bb_mid_v, bb_lo_v = float(bb_up.iloc[-1]), float(bb_mid.iloc[-1]), float(bb_lo.iloc[-1])
    bb_range = (bb_up_v - bb_lo_v) if bb_up_v > bb_lo_v else 0.0
    pct_b = ((last_price - bb_lo_v) / bb_range) if bb_range > 0 else 0.5
    # Bandwidth & squeeze: band sempit = volatilitas rendah, sinyal potensi breakout.
    bb_bw = bb_range / bb_mid_v if bb_mid_v and bb_mid_v > 0 else 0.0
    bb_bw_hist = ((bb_up - bb_lo) / bb_mid.replace(0, float("nan"))).dropna()
    bb_squeeze = bool(len(bb_bw_hist) >= 30 and bb_bw <= float(bb_bw_hist.tail(30).quantile(0.2)))

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
    # force_long=True: TP/SL selalu arah long (konsisten utk portofolio/notifikasi,
    # mencegah alert "TP tercapai" palsu saat sinyal SELL membalik arah TP/SL).
    rm = risk_management(last_price, sr_zones, signal["action"], float(atr14), risk_amount,
                         force_long=True)
    # Bila data historis tidak sinkron dengan harga live, semua indikator & sinyal
    # dihitung dari data yang salah -> ditangguhkan agar tidak menyesatkan (kasus
    # ESTI: close historis 37 vs harga live 179 menghasilkan STRONG BUY palsu).
    if data_warning:
        signal = {
            "action": "HOLD",
            "score": 0.0,
            "strength": "lemah",
            "reasons": [data_warning],
            "rule": "Analisis ditangguhkan karena data historis tidak sinkron dengan harga live.",
        }
        rm = None

    # --- Bandarmology (IDX Edge PRO, jika key di-set; dilewati saat light) ---
    bandarmology = None
    # Normalisasi varian bursa IDX (.JK) untuk input pendek tanpa titik (mis. BBCA).
    bandar_tk = ticker.upper()
    if ".JK" not in bandar_tk and "." not in bandar_tk and bandar_tk.isalnum() and len(bandar_tk) <= 5:
        bandar_tk += ".JK"
    bandarmology_note = _bandarmology_note(bandar_tk) if IDX_EDGE_API_KEYS else BANDARMOLOGY_NOTE
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

    # --- Multi-timeframe (tren mingguan) & kondisi pasar (IHSG) ---
    weekly_series = _weekly_trend_series(df)
    weekly_up = bool(weekly_series.iloc[-1]) if len(weekly_series) else False
    wk_close = df["Close"].astype(float).resample("W-FRI").last()
    wk_ma = sma(wk_close, 20)
    weekly = {
        "trend": "naik" if weekly_up else "turun",
        "up": weekly_up,
        "close": num(float(wk_close.iloc[-1]), 0) if len(wk_close) else None,
        "sma20": num(float(wk_ma.iloc[-1]), 0) if len(wk_ma) and not np.isnan(wk_ma.iloc[-1]) else None,
        "note": ("Multi-timeframe: sinyal harian sebaiknya searah tren mingguan "
                 "(close > SMA20 mingguan) agar tidak melawan arus jangka menengah."),
    }
    regime = _ihsg_regime()
    liquidity = _liquidity_metrics(df)
    data_date = str(close.index[-1].date()) if hasattr(close.index[-1], "date") else str(close.index[-1])

    # Panduan aksi: syarat konfirmasi + level harga nyata (tunggu apa, di harga berapa).
    action_plan = None
    if not data_warning:
        action_plan = build_action_plan(
            last_price=last_price, action=signal.get("action", "HOLD"), trend=trend,
            sr_zones=sr_zones, fib=fib, weekly=weekly, bandarmology=bandarmology,
            regime=regime, rm=rm,
            ind={
                "sma20": num(s20, 2), "sma50": num(s50, 2), "sma200": num(s200, 2),
                "rsi14": num(r14, 2), "atr14": num(atr14, 2),
                "macd": {"histogram": num(hist.iloc[-1], 4)},
                "volume": vol,
                "bollinger": {"upper": num(bb_up_v, 2), "lower": num(bb_lo_v, 2),
                              "squeeze": bb_squeeze},
            },
            liquidity_grade=liquidity.get("grade"), data_date=data_date,
        )

    # --- Selaraskan Risk Management dengan Rencana Aksi ---
    # SL/TP/entry memakai harga entry yang DIRENCANAKAN (pullback: SMA20; breakout:
    # resistance), bukan harga sekarang, supaya kartu Risk Management, kolom SL
    # portofolio, dan notifikasi memakai level yang sama dengan Rencana Aksi.
    if action_plan:
        _sp = action_plan.get("setup") or {}
        _bat = action_plan.get("pembatalan") or {}
        _tgt = action_plan.get("target") or {}
        _kel = action_plan.get("kelayakan") or {}
        _e, _slp, _tpp = _sp.get("harga_entry"), _bat.get("level"), _tgt.get("tp1")
        if (isinstance(_e, (int, float)) and isinstance(_slp, (int, float))
                and isinstance(_tpp, (int, float)) and _e > _slp):
            _risk = _e - _slp
            _pct = _risk / _e if _e > 0 else None
            rm = {
                "direction": "long",
                "entry": num(_e, 2),
                "stop_loss": num(_slp, 2),
                "take_profit": num(_tpp, 2),
                "take_profit_2": num(_tgt.get("tp2"), 2),
                "risk_reward_ratio": num(_kel.get("rrr"), 2),
                "max_position_idr": num(risk_amount / _pct, 0) if _pct else None,
                "position_sizing_note": (
                    "Maks Risk per Trade / %SL = Maks Posisi (contoh buku: Rp5.000.000 / 20% = "
                    f"Rp25.000.000). Di sini memakai risiko Rp{risk_amount:,.0f} dan SL rencana "
                    "aksi (dihitung dari harga entry rencana, bukan harga sekarang)."),
                "rrr_note": "RRR minimal 1:2 (jika < 2, trade sebaiknya dilewati).",
                "aligned_with_action_plan": True,
            }

    return {
        "ticker": ticker.upper(),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "disclaimer": DISCLAIMER,
        "data_warning": data_warning,
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
            "bollinger": {
                "upper": num(bb_up_v, 2),
                "middle": num(bb_mid_v, 2),
                "lower": num(bb_lo_v, 2),
                "pct_b": num(pct_b, 2),
                "bandwidth": num(bb_bw, 4),
                "squeeze": bb_squeeze,
                "position": ("di atas band atas (overbought)" if last_price > bb_up_v else
                             "di band atas" if pct_b >= 0.8 else
                             "tengah band" if 0.2 < pct_b < 0.8 else
                             "di band bawah" if pct_b > 0 else
                             "di bawah band bawah (oversold)"),
                "note": "BB(20,2): harga di atas band atas = jenuh beli; di bawah band bawah = jenuh jual; squeeze (bandwidth rendah) = potensi breakout.",
            },
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
        "action_plan": action_plan,
        "buy_score": compute_buy_score(df, bandarmology if not light else None),
        "risk_management": rm,
        "bandarmology": bandarmology,
        "bandarmology_note": bandarmology_note,
        "liquidity": {
            "avg_value_20d": num(liquidity["avg_value_20d"], 0),
            "avg_volume_20d": num(liquidity["avg_volume_20d"], 0),
            "grade": liquidity["grade"],
            "note": "Kelas likuiditas dari nilai transaksi rata-rata 20 hari (IDR).",
        },
        "weekly": weekly,
        "market_regime": regime,
        "fundamentals": (None if fund_level == "off"
                         else fetch_fundamentals(ticker, last_price,
                                                 light=fund_level == "ringkas")),
    }


@app.get("/api/analyze/{ticker}")
def analyze(
    ticker: str,
    period: str = Query("1y", pattern="^(1mo|3mo|6mo|1y|2y|5y)$"),
    risk_amount: float = Query(5_000_000, gt=0),
    fundamentals: str = Query("off", pattern="^(full|ringkas|off)$",
                              description="Kedalaman fundamental; default off agar hemat kuota "
                                          "(dashboard memuatnya saat tombol Fundamental diklik)"),
):
    return _analyze_core(ticker, period, risk_amount, light=False, fund_level=fundamentals)


@app.get("/api/fundamentals/{ticker}")
def fundamentals_only(
    ticker: str,
    level: str = Query("full", pattern="^(ringkas|full)$"),
    price: float = Query(0, ge=0, description="Harga terakhir (opsional; untuk kapitalisasi & yield)"),
):
    """Fundamental emiten dimuat atas permintaan (dipakai tombol di tab Analisis).

    Dipisah dari /api/analyze supaya tab Analisis tidak memanggil sumber fundamental
    (FMP/Yahoo) pada setiap analisis — hemat kuota API. level=ringkas hanya rasio
    (1 permintaan); level=full menambah laporan YoY, dividen, dan aksi korporasi.
    """
    f = fetch_fundamentals(ticker, price or None, light=(level == "ringkas"))
    if not f:
        raise HTTPException(502, ("Data fundamental sedang tidak tersedia (sumber dibatasi "
                                  "atau kuota habis). Coba lagi beberapa saat."))
    return {"ticker": ticker.upper(), "level": level, "fundamentals": f,
            "disclaimer": DISCLAIMER}


@app.get("/api/quotes")
def quotes(
    tickers: str = Query(..., description="Kode saham dipisah koma, maks 15. Contoh: BBCA,TLKM,BBRI"),
    period: str = Query("1y", pattern="^(1mo|3mo|6mo|1y|2y|5y)$"),
    with_bandar: bool = Query(False, description="Sertakan Broker Summary (memakai kuota IDX Edge)"),
    fundamentals: str = Query("off", pattern="^(full|ringkas|off)$",
                              description="Kedalaman fundamental: off|ringkas|full (opsional)"),
):
    """Kutipan + sinyal + TP/SL untuk portofolio/watchlist (paralel, hemat kuota).

    fundamentals=off (default) -> kutipan ringan; 'ringkas' menambah rasio
    (PE/PBV/EPS/ROE/dividen); 'full' menambah laporan YoY, dividen, dan aksi
    korporasi. Rasio lengkap per saham tersedia di /api/analyze/{ticker}.
    """
    codes = [c.strip().upper() for c in tickers.split(",") if c.strip()][:15]
    if not codes:
        raise HTTPException(422, "Parameter tickers tidak boleh kosong.")

    def one(code: str) -> dict:
        try:
            d = _analyze_core(code, period, 5_000_000, light=not with_bandar,
                              fund_level=fundamentals)
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


class KoreksiWatchRequest(BaseModel):
    ticker: str
    action: str = "add"  # add | remove


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
    ok = sync_save(key, _normalize_holdings(payload.portfolio), payload.watchlist)
    if not ok:
        raise HTTPException(502, "Gagal menyimpan ke penyimpanan cloud. Coba lagi.")
    return {"ok": True, "sync_enabled": True}


def _portfolio_alerts(portfolio: List[dict]) -> List[dict]:
    """Hitung alert TP/SL/overbought/sinyal jual untuk daftar posisi (paralel)."""
    portfolio = _normalize_holdings(portfolio) or []
    if not portfolio:
        return []

    def one(h: dict) -> List[dict]:
        tk = str(h.get("ticker", "")).upper()
        if not tk:
            return []
        try:
            d = _analyze_core(tk, "3mo", 5_000_000, light=True, fund_level="off")
        except Exception:
            return []
        price = d["market"]["last_price"]
        rm = d.get("risk_management") or {}
        rsi = (d.get("indicators") or {}).get("rsi14")
        sig = (d.get("signal") or {}).get("action", "")
        plan_txt = _telegram_action_plan(d.get("action_plan"), max_steps=2)
        base = {"ticker": strip_suffix(tk), "price": num(price, 2),
                "qty": h.get("qty"), "avg": h.get("avg")}
        out: List[dict] = []
        # TP/SL server dihitung relatif harga pasar SEKARANG, bukan harga beli (avg),
        # dan TP bisa hanya +1% di atas harga (saat harga menempel resistance). Agar
        # alert "Dekat TP"/"TP tercapai" tidak menyala untuk posisi minus: TP hanya
        # bermakna bila posisi SUDAH profit nyata (harga sekarang > harga beli).
        avg = h.get("avg")
        tp_valid = bool(rm.get("take_profit")) and (not avg or price > avg)
        if rm.get("stop_loss") and price <= rm["stop_loss"]:
            out.append({**base, "type": "SL", "level": num(rm["stop_loss"], 2),
                        "message": f"🛑 {strip_suffix(tk)} menyentuh STOP LOSS ({num(rm['stop_loss'], 2)}) — harga {num(price, 2)}"})
        if tp_valid and price >= rm["take_profit"]:
            out.append({**base, "type": "TP", "level": num(rm["take_profit"], 2),
                        "message": f"🎯 {strip_suffix(tk)} mencapai TAKE PROFIT ({num(rm['take_profit'], 2)}) — harga {num(price, 2)}"})
        if tp_valid and rm["stop_loss"] < price < rm["take_profit"] and price >= rm["take_profit"] * 0.98:
            out.append({**base, "type": "TP_NEAR", "level": num(rm["take_profit"], 2),
                        "message": f"🔥 {strip_suffix(tk)} hampir TP (≤2% dari {num(rm['take_profit'], 2)})"})
        if rsi is not None and rsi > 70:
            out.append({**base, "type": "OB", "rsi": num(rsi, 1),
                        "message": f"⚠️ {strip_suffix(tk)} overbought (RSI {num(rsi, 1)}) — waspada koreksi"})
        if sig in ("SELL", "STRONG SELL"):
            msg = f"⬇️ {strip_suffix(tk)} sinyal {sig} — pertimbangkan take profit / cut loss"
            if plan_txt:
                msg += "\n\n" + plan_txt
            out.append({**base, "type": "SELL", "signal": sig,
                        "action_plan": d.get("action_plan"), "message": msg})
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


KOREKSI_WATCH_KEY = "ci:koreksi_watch"


def _koreksi_watch_load() -> List[str]:
    """Daftar ticker yang dipantau utk konfirmasi reversal (kandidat beli koreksi)."""
    if not SYNC_ENABLED:
        return []
    parsed = _unwrap_json(_upstash_get(KOREKSI_WATCH_KEY), [])
    out: List[str] = []
    for t in (parsed if isinstance(parsed, list) else []):
        if isinstance(t, str) and t.strip():
            out.append(t.strip().upper())
    return out[:100]


def _koreksi_watch_save(lst: List[str]) -> bool:
    if not SYNC_ENABLED:
        return False
    return _upstash_set(KOREKSI_WATCH_KEY, json.dumps(lst[:100]))


@app.post("/api/koreksi/watch")
def koreksi_watch(payload: KoreksiWatchRequest):
    """Tambah/hapus ticker ke pantauan konfirmasi reversal (kandidat beli koreksi)."""
    if not SYNC_ENABLED:
        raise HTTPException(502, "Penyimpanan cloud belum dikonfigurasi (UPSTASH_REDIS_REST_URL/TOKEN).")
    tk = payload.ticker.strip().upper()
    if not tk:
        raise HTTPException(422, "Ticker wajib diisi.")
    lst = _koreksi_watch_load()
    action = (payload.action or "add").lower()
    if action == "remove":
        lst = [t for t in lst if t != tk]
    elif tk not in lst:
        lst.append(tk)
    ok = _koreksi_watch_save(lst)
    if not ok:
        raise HTTPException(502, "Gagal menyimpan pantauan. Coba lagi.")
    return {"ok": True, "action": action, "ticker": tk, "watched": lst}


@app.get("/api/koreksi/watch")
def koreksi_watch_list(period: str = Query("3mo", pattern="^(1mo|3mo|6mo|1y)$")):
    """Status live saham dalam pantauan koreksi: sinyal, skor kualitas, support terdekat."""
    lst = _koreksi_watch_load()
    if not lst:
        return {"watched": [], "results": [], "sync_enabled": SYNC_ENABLED}

    def one(tk: str) -> dict:
        try:
            d = _analyze_core(tk, period, 5_000_000, light=True, fund_level="off")
            price = float(d["market"]["last_price"])
            sr = (d.get("support_resistance") or {}).get("zones") or []
            sup = [z["price"] for z in sr if z["type"] == "support" and z["price"] < price]
            ap = d.get("action_plan") or {}
            setup = ap.get("setup") or {}
            kel = ap.get("kelayakan") or {}
            return {
                "ticker": tk, "ok": True,
                "price": num(price, 2),
                "signal": (d.get("signal") or {}).get("action"),
                "score": (d.get("buy_score") or {}).get("score"),
                "nearest_support": num(max(sup), 2) if sup else None,
                "support_distance_pct": num((price / max(sup) - 1) * 100, 1) if sup else None,
                # Info setup swing agar panel pantauan menampilkan status entry.
                "setup": setup.get("jenis"),
                "layak_entry": bool(setup.get("layak_entry")),
                "trigger": num((setup.get("trigger") or {}).get("level"), 2),
                "rrr": num(kel.get("rrr"), 2),
                "rrr_layak": bool(kel.get("layak")),
                "timing": num((ap.get("timing") or {}).get("skor"), 0),
            }
        except Exception:
            return {"ticker": tk, "ok": False}

    with ThreadPoolExecutor(max_workers=min(6, len(lst))) as ex:
        results = list(ex.map(one, lst))
    return {"watched": lst, "results": results, "sync_enabled": SYNC_ENABLED}


@app.get("/api/cron/koreksi")
def cron_koreksi(request: Request, secret: str = Query("")):
    """Cron: cek saham dalam pantauan kandidat beli koreksi; kirim Telegram saat
    (1) konfirmasi reversal (sinyal berubah SELL -> BUY/STRONG BUY) atau
    (2) harga memasuki zona entry support (masih fase koreksi).
    Dedupe sekali per tipe per hari; sinyal terakhir disimpan utk deteksi perubahan."""
    auth = request.headers.get("authorization", "")
    bearer_ok = bool(CRON_SECRET) and auth == f"Bearer {CRON_SECRET}"
    if CRON_SECRET and secret != CRON_SECRET and not bearer_ok:
        raise HTTPException(403, "Forbidden")
    if not SYNC_ENABLED:
        return {"skipped": True, "reason": "Penyimpanan cloud belum dikonfigurasi."}
    lst = _koreksi_watch_load()
    if not lst:
        return {"checked": 0, "telegram_sent": 0, "watched": 0,
                "note": "Pantauan koreksi kosong — tambahkan kandidat lewat dashboard."}

    sent = 0
    checked = 0
    today = time.strftime("%Y-%m-%d")
    for tk in lst[:50]:
        try:
            d = _analyze_core(tk, "3mo", 5_000_000, light=True, fund_level="off")
        except Exception:
            continue
        checked += 1
        sig = (d.get("signal") or {}).get("action", "")
        score = (d.get("buy_score") or {}).get("score") or 0
        price = float(d["market"]["last_price"])
        sr = (d.get("support_resistance") or {}).get("zones") or []
        sup = [z["price"] for z in sr if z["type"] == "support" and z["price"] < price]
        nearest_sup = max(sup) if sup else None

        state_key = f"ci:koreksi_state:{tk}"
        prev = str(_upstash_get(state_key) or "")
        ap = d.get("action_plan") or {}
        plan_txt = _telegram_action_plan(ap)
        plan_block = ("\n\n" + plan_txt) if plan_txt else ""

        # (1) Konfirmasi reversal: sebelumnya SELL/STRONG SELL, sekarang BUY/STRONG BUY.
        if prev in ("SELL", "STRONG SELL") and sig in ("BUY", "STRONG BUY"):
            dedupe = f"ci:notif:koreksi:{tk}:REVERSAL:{today}"
            if not _upstash_get(dedupe):
                msg = (f"🔄 KONFIRMASI REVERSAL: {strip_suffix(tk)}\n"
                       f"Sinyal berubah {prev} → {sig} · kualitas {num(score, 0)}\n"
                       f"Harga {num(price, 2)} — verifikasi volume & breakout AVG bandar sebelum entry."
                       + plan_block)
                if _telegram_send(msg + "\n\n(dedesaputra_invst)"):
                    _upstash_set(dedupe, "1", ttl=86400)
                    sent += 1
        # (2) Harga memasuki zona entry (support), masih fase koreksi.
        elif nearest_sup and price <= nearest_sup * 1.02 and sig in ("SELL", "STRONG SELL", "HOLD"):
            dedupe = f"ci:notif:koreksi:{tk}:SUPPORT:{today}"
            if not _upstash_get(dedupe):
                msg = (f"🎯 {strip_suffix(tk)} mendekati zona entry: support {num(nearest_sup, 2)} "
                       f"(harga {num(price, 2)})\nKualitas {num(score, 0)} · sinyal {sig} — tunggu konfirmasi reversal."
                       + plan_block)
                if _telegram_send(msg + "\n\n(dedesaputra_invst)"):
                    _upstash_set(dedupe, "1", ttl=86400)
                    sent += 1

        # (3) Setup swing siap entry: setup valid (pullback/breakout) + RRR ≥ 1:2
        #     + timing bagus. Ini notifikasi proaktif "titik masuk terbaik".
        setup = ap.get("setup") or {}
        kel = ap.get("kelayakan") or {}
        tmg = ap.get("timing") or {}
        trg = setup.get("trigger") or {}
        if (setup.get("layak_entry") and kel.get("layak")
                and setup.get("jenis") in ("Pullback di tren naik", "Breakout")):
            dedupe = f"ci:notif:koreksi:{tk}:SETUP:{today}"
            if not _upstash_get(dedupe):
                trig = f" · trigger @ {num(trg.get('level'), 2)}" if trg.get("level") else ""
                msg = (f"🎯 SETUP SWING SIAP: {strip_suffix(tk)}\n"
                       f"{setup.get('jenis')} · timing {num(tmg.get('skor'), 0)}/100 "
                       f"({tmg.get('label') or '—'})\n"
                       f"Harga {num(price, 2)}{trig} · RRR 1:{num(kel.get('rrr'), 2)}")
                if _telegram_send(msg + plan_block + "\n\n(dedesaputra_invst)"):
                    _upstash_set(dedupe, "1", ttl=86400)
                    sent += 1

        if sig != prev:
            _upstash_set(state_key, sig, ttl=86400 * 3)

    return {"checked": checked, "telegram_sent": sent, "watched": len(lst),
            "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
            "time": time.strftime("%Y-%m-%d %H:%M:%S %Z")}


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
    criteria: str = Query("swing", pattern="^(scalping|bsjp|swing|buy|all)$"),
    universe: str = Query("liquid", pattern="^(all|liquid)$"),
    years: int = Query(2, ge=1, le=5),
    limit: int = Query(20, ge=1, le=100),
    confirm: bool = Query(True, description="Konfirmasi ala buku: harga>SMA20, RSI<70, volume>MA20"),
    regime: bool = Query(True, description="Filter IHSG: hanya sinyal saat IHSG di atas MA200"),
    bb_confirm: bool = Query(True, description="Konfirmasi Bollinger: entry band bawah/tengah saat tren naik"),
    div_vol: bool = Query(True, description="Gerbang divergensi RSI bullish + volume > rata-rata"),
    weekly: bool = Query(True, description="Multi-timeframe: tren mingguan naik (close > SMA20 mingguan)"),
    costs: bool = Query(True, description="Biaya + slippage 0,3% round-trip"),
    plan_mode: bool = Query(True, description="Exit selaras rencana aksi: SL struktural (support-0,3×ATR), gerbang RRR≥2, geser SL ke break-even, trailing 2×ATR"),
    rr_min: float = Query(2.0, ge=1.0, le=5.0, description="RRR minimum agar trade diambil (mode plan)"),
    trail_mult: float = Query(2.0, ge=0.5, le=5.0, description="Pengali ATR untuk trailing stop (mode plan)"),
    tickers_param: str = Query("", alias="tickers",
                               description="Daftar kode kustom dipisah koma (maks 45); menimpa universe"),
):
    """Estimasi win rate historis per kriteria screener (edukasi, bukan jaminan masa depan).
    Sinyal -> entry di harga tutup. plan_mode=True (default) menyamakan exit dengan
    rencana aksi: SL struktural support-0,3×ATR, TP di resistance, gerbang RRR≥2,
    geser SL ke break-even setelah 1R, lalu trailing 2×ATR. plan_mode=False memakai
    mode lama: SL 2×ATR, TP 2R tetap. Hold maks 5/20 hari.
    Filter optimasi (regime/bb_confirm/div_vol/weekly) mempersempit sinyal ke kondisi
    yang lebih terkonfirmasi; costs menambahkan biaya+slippage 0,3% round-trip.
    Kriteria 'all' menjalankan 4 kriteria sekaligus dan mengembalikan hasil terbaik
    (avg R tertinggi dengan trade yang dituntaskan SL/TP).
    Kriteria 'bandar' tidak dapat diuji: Broker Summary hanya snapshot hari ini."""
    if criteria == "all":
        best = None
        for c in ("swing", "scalping", "bsjp", "buy"):
            r = backtest(c, universe, years, limit, confirm, regime, bb_confirm,
                         div_vol, weekly, costs, plan_mode, rr_min, trail_mult, tickers_param)
            if r.get("total_trades", 0) <= 0:
                continue
            if best is None:
                best = r
                continue
            bd = best.get("wins", 0) + best.get("losses", 0)
            rd = r.get("wins", 0) + r.get("losses", 0)
            if rd > 0 and (bd == 0 or (r.get("avg_r") or -99) > (best.get("avg_r") or -99)):
                best = r
        if best is None:
            return {"criteria": "all", "total_trades": 0, "wins": 0, "losses": 0,
                    "timeouts": 0, "win_rate_pct": None, "avg_r": None, "per_ticker": [],
                    "note": "Tidak ada sinyal yang lolos di semua kriteria dengan filter ini.",
                    "disclaimer": DISCLAIMER}
        best["criteria_note"] = ("criteria=all: hasil terbaik dari 4 kriteria (swing/scalping/BSJP/buy) "
                                 "dengan konfigurasi filter ini (avg R tertinggi, trade tuntas).")
        return best
    if tickers_param:
        tickers = [f"{t.strip().upper()}.JK" if "." not in t.strip().upper() else t.strip().upper()
                   for t in tickers_param.split(",") if t.strip()][:45]
    else:
        tickers = load_idx_tickers(universe)
        tickers = tickers[:100] if universe == "all" else tickers[:limit]
    # Deret IHSG diambil SEKALI untuk seluruh backtest (ffill per ticker di dalam).
    ihsg_align = None
    if regime:
        try:
            ic, im = _ihsg_series()
            if ic is not None and im is not None:
                s = pd.concat([ic.rename("c"), im.rename("m")], axis=1).dropna()
                if len(s):
                    ihsg_align = s
        except Exception:
            ihsg_align = None
    results: List[dict] = []
    tot_skipped = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(lambda t: _backtest_one_safe(t, criteria, years, confirm, regime, bb_confirm,
                                                     div_vol, weekly, costs, ihsg_align,
                                                     plan_mode=plan_mode, rr_min=rr_min,
                                                     trail_mult=trail_mult), tickers):
            if not r:
                continue
            # Kandidat yang DILEWATI (RRR<2) tetap dihitung walau tak ada trade,
            # supaya terlihat berapa banyak sinyal yang disaring gerbang RRR.
            tot_skipped += r.get("skipped_rrr", 0)
            if r.get("trades", 0) > 0:
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
        "config": {
            "regime": bool(regime),
            "bb_confirm": bool(bb_confirm),
            "div_vol": bool(div_vol),
            "weekly": bool(weekly),
            "costs": bool(costs),
            "cost_pct": 0.3,
        },
        "regime_note": (None if not regime or ihsg_align is None else
                         "Filter IHSG aktif (data IHSG tersedia, sinyal hanya saat IHSG > MA200)."),
        "tickers_checked": len(tickers),
        "tickers_with_signals": len(results),
        "total_trades": tot_trades,
        "wins": tot_wins,
        "losses": tot_losses,
        "timeouts": tot_timeouts,
        "skipped_rrr": tot_skipped,
        "plan_mode": bool(plan_mode),
        "rr_min": float(rr_min),
        "trail_mult": float(trail_mult),
        "win_rate_pct": num(tot_wins / decided * 100, 1) if decided else None,
        "avg_r": num(avg_r, 2),
        "per_ticker": results[:15],
        "note": ("Backtest: entry harga tutup saat sinyal. "
                 + (f"Mode PLAN: SL di support-0,3×ATR (maks 3×ATR), TP di resistance terdekat-0,3×ATR, "
                    f"trade dilewati bila RRR < {rr_min:g} (skipped_rrr), geser SL ke break-even setelah 1R, "
                    f"lalu trailing {trail_mult:g}×ATR. "
                    if plan_mode else
                    "Mode LAMA: SL 2×ATR, TP 2R (RRR 1:2). ")
                 + "hold maks 5 hari (scalping/BSJP) / 20 hari (swing/buy); timeout keluar di close. "
                 "Filter optimasi aktif: "
                 + ("IHSG>MA200 " if regime else "") + ("BB band bawah/tengah+tren naik " if bb_confirm else "")
                 + ("divergensi RSI+volume " if div_vol else "") + ("tren mingguan " if weekly else "")
                 + ("| biaya+slippage 0,3% round-trip " if costs else "tanpa biaya") + ". "
                 "Rentan survivorship bias & aksi korporasi. Kinerja masa lalu BUKAN jaminan masa depan. "
                 "Kriteria 'bandar' tidak diuji: Broker Summary hanya snapshot hari ini tanpa riwayat. "
                 "Kriteria 'buy' memakai skor komposit multi-konfirmasi (tanpa bandarmology historis). "
                 "Win rate dihitung dari trade yang dituntaskan; timeout dihitung terpisah."),
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/backtest/matrix")
def backtest_matrix(
    criteria: str = Query("buy", pattern="^(scalping|bsjp|swing|buy|all)$"),
    universe: str = Query("liquid", pattern="^(all|liquid)$"),
    years: int = Query(2, ge=1, le=5),
    limit: int = Query(15, ge=1, le=100),
    plan_mode: bool = Query(True, description="Exit selaras rencana aksi (SL struktural, RRR≥2, BE, trailing)"),
    rr_min: float = Query(2.0, ge=1.0, le=5.0, description="RRR minimum agar trade diambil (mode plan)"),
    trail_mult: float = Query(2.0, ge=0.5, le=5.0, description="Pengali ATR untuk trailing stop"),
    tickers_param: str = Query("", alias="tickers",
                               description="Daftar kode kustom dipisah koma (maks 30); menimpa universe"),
):
    """Matriks win rate semua 32 kombinasi filter sekaligus (lihat _backtest_matrix).

    criteria='all' menghitung 4 kriteria x 32 kombinasi (paralel per ticker).
    Tikers dibatasi 30 agar tetap muat dalam batas durasi function (60 dtk)."""
    if tickers_param:
        tickers = [f"{t.strip().upper()}.JK" if "." not in t.strip().upper() else t.strip().upper()
                   for t in tickers_param.split(",") if t.strip()][:30]
    else:
        tickers = load_idx_tickers(universe)
        tickers = (tickers[:30] if universe == "all" else tickers[:limit])
    ihsg_align = None
    try:
        ic, im = _ihsg_series()
        if ic is not None and im is not None:
            s = pd.concat([ic.rename("c"), im.rename("m")], axis=1).dropna()
            if len(s):
                ihsg_align = s
    except Exception:
        ihsg_align = None
    out = _backtest_matrix(tickers, criteria, years, ihsg_align,
                           plan_mode=plan_mode, rr_min=rr_min, trail_mult=trail_mult)
    out["disclaimer"] = DISCLAIMER
    return out


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
    require_confirm: bool = False
    require_regime: Optional[bool] = None


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
    criteria: str = Query("all", pattern="^(all|swing|scalping|bsjp|bandar|buy|koreksi)$"),
    universe: str = Query("liquid", pattern="^(all|liquid)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    period: str = Query("3mo", pattern="^(1mo|3mo|6mo|1y)$"),
    include_signal: bool = Query(True),
    include_bandarmology: bool = Query(True),
    require_confirm: bool = Query(False),
    require_regime: Optional[bool] = Query(None, description="Hanya saham saat IHSG di atas MA200. Default: aktif otomatis untuk kriteria swing/buy/koreksi."),
):
    """Scan saham dengan kriteria screener Coachinvestasi.

    require_confirm=True hanya menampilkan saham yang lolos konfirmasi buku
    (harga > SMA20, RSI < 70, volume > VolumeMA20). require_regime=True hanya
    memproses sinyal saat IHSG di atas MA200 (tidak mengejar pasar bear). Bila
    tidak diisi (None), filter regime otomatis AKTIF untuk kriteria swing/buy/
    koreksi karena strategi itu sebaiknya tidak melawan pasar bear.
    Gunakan offset/limit berulang-ulang untuk memindai SELURUH kode saham
    (total_tickers & next_offset disediakan untuk paginasi).
    """
    if require_regime is None:
        require_regime = criteria in ("swing", "buy", "koreksi")
    all_tickers = load_idx_tickers(universe)
    window = all_tickers[offset:offset + limit]
    if not window:
        raise HTTPException(404, "Offset melebihi jumlah ticker.")

    scan = _scan(window, criteria, period, include_signal, include_bandarmology,
                 require_confirm, require_regime)
    return {
        "criteria": criteria,
        "require_confirm": require_confirm,
        "require_regime": require_regime,
        "universe": universe,
        "period": period,
        "requested": len(window),
        "scanned": scan["scanned"],
        "skipped": scan["skipped"],
        "bandarmology_checked": scan["bandarmology_checked"],
        "market_regime": scan.get("market_regime"),
        "regime_blocked": scan.get("regime_blocked"),
        "total_tickers": len(all_tickers),
        "next_offset": offset + limit if offset + limit < len(all_tickers) else None,
        "results": scan["results"],
        "bandarmology_note": _screener_bandar_note(),
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
    require_regime = payload.require_regime
    if require_regime is None:
        require_regime = payload.criteria in ("swing", "buy", "koreksi")
    scan = _scan(tickers, payload.criteria, payload.period,
                 payload.include_signal, payload.include_bandarmology,
                 payload.require_confirm, require_regime)
    return {
        "criteria": payload.criteria,
        "require_confirm": payload.require_confirm,
        "require_regime": require_regime,
        "period": payload.period,
        "requested": len(tickers),
        "scanned": scan["scanned"],
        "skipped": scan["skipped"],
        "bandarmology_checked": scan["bandarmology_checked"],
        "market_regime": scan.get("market_regime"),
        "regime_blocked": scan.get("regime_blocked"),
        "results": scan["results"],
        "bandarmology_note": _screener_bandar_note(),
        "disclaimer": DISCLAIMER,
    }