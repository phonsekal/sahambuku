#!/usr/bin/env python3
"""Panel OHLCV se-pasar dari cache ringkasan harian IDX resmi (0 kuota, 0 jaringan).

Kenapa ada
----------
Audit kriteria screener butuh SATU dataset yang dipakai SEMUA kriteria dengan jam
(u.f. "jam" = rentang tanggal) yang identik. Kalau tiap kriteria diuji di sampel
berbeda, perbandingannya tidak sah. Sumber gratis yang paling lengkap sudah ada
lokal: `research/.cache/idxsummary/*.json` — unggahan ringkasan harian IDX resmi,
SE-PASAR (600-960 emiten/hari), sejak 2019-09 sampai hari terakhir sinkronisasi.

Isi tiap berkas = baris per emiten dengan Open/High/Low/Close/Volume/Value/Frequency.
Kolom `Frequency` (jumlah transaksi) penting: dari situ ukuran tiket
(`avg_ticket_size` di api/index.py) bisa direplikasi tanpa permintaan tambahan.

Dipakai oleh
------------
* research/criteria_audit.py  — uji head-to-head semua kriteria screener.

Cache: research/.cache/panel.pkl (di-ignore git). Hapus berkas itu untuk membangun
ulang. Fungsi `load_panel()` mengembalikan DataFrame:
    code, date, open, high, low, close, prev, volume, value, freq, ng_share
"""

from __future__ import annotations

import glob
import json
import os
import pickle
from typing import Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SUMMARY_DIR = os.path.join(HERE, ".cache", "idxsummary")
PANEL_PKL = os.path.join(HERE, ".cache", "panel.pkl")

KEEP = {
    "StockCode": "code", "Date": "date", "Previous": "prev",
    "OpenPrice": "open", "High": "high", "Low": "low", "Close": "close",
    "Volume": "volume", "Value": "value", "Frequency": "freq",
}


def build_panel(verbose: bool = True) -> pd.DataFrame:
    """Baca SEMUA berkas ringkasan harian -> satu panel panjang (per saham-hari)."""
    files = sorted(glob.glob(os.path.join(SUMMARY_DIR, "*.json")))
    if not files:
        raise SystemExit(f"Tidak ada cache di {SUMMARY_DIR} — jalankan "
                         f"research/idx_daily_summary.py lebih dulu.")
    frames = []
    for i, path in enumerate(files):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh).get("data") or []
        except Exception:
            continue
        if not data:
            continue
        frame = pd.DataFrame(data)
        cols = {k: v for k, v in KEEP.items() if k in frame.columns}
        frame = frame[list(cols)].rename(columns=cols)
        frames.append(frame)
        if verbose and (i + 1) % 300 == 0:
            print(f"  ... {i + 1}/{len(files)} berkas")
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
    panel["code"] = panel["code"].astype(str).str.strip().str.upper()
    for c in ("prev", "open", "high", "low", "close", "volume", "value", "freq"):
        panel[c] = pd.to_numeric(panel[c], errors="coerce")
    panel = panel.dropna(subset=["close", "open", "high", "low"])
    panel = panel[panel["close"] > 0]
    panel = panel.sort_values(["code", "date"]).drop_duplicates(["code", "date"])
    panel = panel.reset_index(drop=True)
    return panel


def load_panel(rebuild: bool = False) -> pd.DataFrame:
    """Panel dari cache .pkl; bangun lalu simpan bila belum ada / diminta ulang."""
    if not rebuild and os.path.exists(PANEL_PKL):
        try:
            with open(PANEL_PKL, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            pass
    panel = build_panel()
    try:
        with open(PANEL_PKL, "wb") as fh:
            pickle.dump(panel, fh, protocol=4)
    except Exception:
        pass
    return panel


def to_ohlcv(group: pd.DataFrame) -> pd.DataFrame:
    """Ubah irisan satu emiten menjadi DataFrame OHLCV ber-indeks tanggal.

    Persis kolom yang dibaca api/index.py: Open/High/Low/Close/Volume/Value/Freq,
    sehingga fungsi sinyal produksi bisa dipanggil langsung atas data ini.
    """
    g = group.sort_values("date")
    out = pd.DataFrame({
        "Open": g["open"].to_numpy(float),
        "High": g["high"].to_numpy(float),
        "Low": g["low"].to_numpy(float),
        "Close": g["close"].to_numpy(float),
        "Volume": g["volume"].fillna(0.0).to_numpy(float),
        "Value": g["value"].fillna(0.0).to_numpy(float),
        "Freq": g["freq"].fillna(0.0).to_numpy(float),
    }, index=pd.DatetimeIndex(g["date"]))
    out.attrs["source"] = "idx-daily-summary"
    return out


def _main() -> None:
    p = load_panel(rebuild=True)
    print(f"panel: {len(p):,} saham-hari · {p['code'].nunique()} emiten · "
          f"{p['date'].nunique()} tanggal")
    print(f"rentang: {p['date'].min().date()} -> {p['date'].max().date()}")
    print(f"kolom  : {list(p.columns)}")
    print(f"cache  : {PANEL_PKL}")


if __name__ == "__main__":
    _main()
