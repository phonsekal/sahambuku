#!/usr/bin/env python3
"""Registry aturan KEADAAN — SATU sumber kebenaran untuk aturan yang bisa disajikan.

Kenapa ada
----------
Aturan pasar AS/ETF disajikan lewat **keadaan turunan** (satu baris per ticker), jadi
tiap aturan yang ingin bisa dipindai harus punya: (1) kunci kolom yang aman untuk CSV,
(2) nama aturan yang SAMA dengan yang diukur `study.py`, (3) label & deskripsi untuk
manusia.

Agar aturan yang LOLOS BAR di sebuah pasar bisa muncul TANPA mengubah kode di banyak
tempat, daftar itu disimpan di sini satu kali, lalu:
  * `export.py`   menulis kolomnya ke `api/market_<pasar>_state.csv`,
  * `summary.py`  menyalinnya ke `api/market_study.json` (kunci `state_rules`), dan
  * `api/index.py` membacanya dari JSON itu saat request (dengan cadangan 3 aturan lama).

Kalau daftar ini ditulis ulang di tiap berkas, ia PASTI menyimpang — itulah masalah
yang berkas ini hilangkan. Kuncinya juga diuji sama persis dengan kunci di
`study.signals_for()` (lihat `tests/test_markets_state.py`).

`rule` HARUS sama persis dengan nama sinyal di `study.signals_for()`.
"""

from __future__ import annotations

from typing import Dict, List

# Urutan di sini = urutan pilihan di UI. Tiga aturan yang sudah lolos bar di AS
# diletakkan lebih dulu supaya tampilan lama tidak berpindah; sisanya menyusul
# begini saja (kandidat yang bisa dilayani bila lolos bar di pasar lain).
STATE_RULES: List[Dict[str, str]] = [
    {"key": "pullback_uptrend", "rule": "pullback di uptrend",
     "label": "Pullback di uptrend",
     "desc": "close di atas SMA200 DAN |close/SMA20 - 1| <= 3% DAN RSI14 antara 35-65"},
    {"key": "above_sma200", "rule": "di atas SMA200",
     "label": "Di atas SMA200",
     "desc": "close di atas SMA200"},
    {"key": "near_high52", "rule": "dekat puncak 52m",
     "label": "Dekat puncak 52 minggu",
     "desc": "close >= 95% dari high 52 minggu"},
    {"key": "above_sma20", "rule": "di atas SMA20",
     "label": "Di atas SMA20", "desc": "close di atas SMA20"},
    {"key": "above_sma50", "rule": "di atas SMA50",
     "label": "Di atas SMA50", "desc": "close di atas SMA50"},
    {"key": "uptrend_sma20_50", "rule": "tren naik (SMA20>50)",
     "label": "Tren naik SMA20>SMA50", "desc": "close > SMA20 DAN SMA20 > SMA50"},
    {"key": "breakout20", "rule": "tembus high20",
     "label": "Tembus high 20 hari",
     "desc": "close di atas high tertinggi 20 hari sebelumnya"},
    {"key": "breakout50", "rule": "tembus high50",
     "label": "Tembus high 50 hari",
     "desc": "close di atas high tertinggi 50 hari sebelumnya"},
    {"key": "new_high52", "rule": "puncak 52m baru",
     "label": "Puncak 52 minggu baru", "desc": "close >= high 52 minggu"},
    {"key": "golden_cross_50_200", "rule": "golden cross 50/200",
     "label": "Golden cross 50/200",
     "desc": "SMA50 memotong ke atas SMA200 pada bar terakhir"},
    {"key": "rsi_lt_30", "rule": "RSI<30 (jenuh jual)",
     "label": "RSI14 < 30 (jenuh jual)", "desc": "RSI14 di bawah 30"},
    {"key": "rsi_recover_30", "rule": "RSI pulih >30",
     "label": "RSI14 pulih melewati 30",
     "desc": "RSI14 menembus ke atas 30 pada bar terakhir"},
    {"key": "mom1d_ge_3", "rule": "mom1d>=3%",
     "label": "Momentum 1 hari >= 3%", "desc": "ret 1 hari >= +3%"},
    {"key": "mom1d_ge_5", "rule": "mom1d>=5%",
     "label": "Momentum 1 hari >= 5%", "desc": "ret 1 hari >= +5%"},
    {"key": "mom5d_ge_10", "rule": "mom5d>=10%",
     "label": "Momentum 5 hari >= 10%", "desc": "ret 5 hari >= +10%"},
    {"key": "mom5d_breakout20", "rule": "mom5d>=10% + tembus high20",
     "label": "Momentum 5 hari + tembus high20",
     "desc": "ret 5 hari >= +10% DAN close menembus high 20 hari"},
    {"key": "uptrend_breakout20", "rule": "tren naik + tembus high20",
     "label": "Tren naik + tembus high20",
     "desc": "close > SMA20 DAN SMA20 > SMA50 DAN close > high20"},
    {"key": "vol_2x_ma20", "rule": "volume 2x MA20",
     "label": "Volume 2x MA20",
     "desc": "volume >= 2x rata-rata volume 20 hari"},
    {"key": "drop5d_above_sma200", "rule": "turun 5d>=10% di atas SMA200",
     "label": "Turun 5 hari >= 10% tapi di atas SMA200",
     "desc": "ret 5 hari <= -10% DAN close masih di atas SMA200"},
    # `control` = pembanding arah pasar, BUKAN kandidat beli. Ditandai supaya ia tidak
    # pernah ditawarkan sebagai penyaring di mode "belum lolos bar" (lihat api/index.py).
    {"key": "new_low52_control", "rule": "dasar 52m baru (kontrol)",
     "label": "Dasar 52 minggu baru (kontrol)",
     "desc": "close <= low 52 minggu (kontrol arah pasar)", "control": True},
]

STATE_KEYS: List[str] = [r["key"] for r in STATE_RULES]
RULE_NAMES: Dict[str, str] = {r["key"]: r["rule"] for r in STATE_RULES}


def as_registry() -> Dict[str, Dict[str, str]]:
    """Bentuk yang ditulis/dibaca JSON: kunci kolom -> {rule, label, desc, control?}.

    `control: True` hanya ditulis bila ada — penanda bahwa aturan itu pembanding arah
    pasar, bukan kandidat beli.
    """
    out: Dict[str, Dict[str, str]] = {}
    for r in STATE_RULES:
        meta = {"rule": r["rule"], "label": r["label"], "desc": r["desc"]}
        if r.get("control"):
            meta["control"] = True
        out[r["key"]] = meta
    return out
