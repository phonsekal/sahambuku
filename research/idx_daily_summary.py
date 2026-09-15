#!/usr/bin/env python3
"""Sumber data IDX RESMI: ringkasan harian seluruh pasar, 1 request per hari bursa.

Kenapa ini penting
------------------
Riset sebelumnya (research/orderflow_study.py) berkesimpulan porsi papan NG hanya
bisa diukur dari endpoint order flow yang riwayatnya 30 hari. Itu KELIRU: IDX
menerbitkan angka yang sama, resmi, untuk SELURUH pasar, sejak 2020.

Dibuktikan cocok persis dengan pull order-flow kita (11 Sep 2026):
    TINS  Value 257,35 M (Arjum 257,35 M) · NonRegularValue 7,505 M / 6 transaksi
          -> pull 144 halaman order flow memberi NG 7,50 M atas 6 baris
    STAR  Value 0,03 M  (Arjum 0,03 M)     · NonRegularValue 4,440 M / 2 transaksi
          -> pull penuh memberi NG 4,44 M atas 2 baris
    BBCA  Value 1.186,69 M (Arjum 1.186,69 M) · 39.802 transaksi

Jadi `Value` = nilai pasar REGULER, `NonRegular*` = blok negosiasi (NG) — persis
pemisahan yang kita perlukan, dengan riwayat bertahun-tahun, dan satu request
sudah mencakup ~963 emiten.

Apa yang TIDAK tersedia (sudah diuji, jangan diulang)
----------------------------------------------------
Rincian PER SAHAM per BROKER tidak diterbitkan IDX. Parameter `stockCode` pada
`/primary/TradingSummary/GetBrokerSummary` diabaikan — diuji 7 nama parameter
(stockCode, StockCode, code, Code, emiten, stock, symbol), semuanya mengembalikan
88 baris yang sama. `GetBrokerSummary` juga hanya per broker untuk SELURUH pasar.
Akibatnya: sinyal "akumulasi diam-diam per broker" tetap terbatas pada 80 hari
milik penyedia pihak ketiga (research/bandar_study.py) dan TIDAK bisa diuji
multi-tahun dengan sumber gratis mana pun yang kami temukan.

Diuji 14 Jul 2026, 10 nama rute kandidat (GetStockBrokerSummary,
GetBrokerSummaryByStock, GetBrokerSummaryDetail, BrokerSummary/GetBrokerSummary,
StockData/GetStockBrokerSummary, dll) -> SEMUA HTTP 503 sementara rute kontrol
GetBrokerSummary mengembalikan 200. Artinya rute itu memang tidak ada, bukan
sedang diblok. Kesimpulan: batas 80 hari untuk data per-broker-per-saham adalah
batas keras untuk sumber gratis. Jangan cari lagi.

HASIL PENTING (1.610 tanggal, 2020-01-02 s/d 2026-09-11, 1.201.760 saham-hari)
-----------------------------------------------------------------------------
Dataset diperluas 474 -> 1.000 -> **1.610 tanggal** (COVID 2020, bull 2022,
bear 2024-2026). Semua uji memakai koreksi blok tidak tumpang-tindih (stride =
horizon) karena horizon 20 hari tumpang-tindih dan t-nya tidak boleh dibaca apa
adanya. Kesimpulan di bawah ini DIHITUNG ULANG pada dataset 1.610 tanggal dan
semuanya menguat, bukan melemah (angka lama 1.000 tanggal ada di komentar git).

- `value_per_tx` di SANGAT LIKUID (dasar filter `skip_small_ticket`):
  blok IC h5 +0,0715 t=+9,18 | h20 +0,0940 t=+6,34 (1583 tgl).
  Holdout paruh awal/akhir: KEEMPATNYA tanda sama DAN lolos blok.
  Rezim IHSG vs MA200: bull +0,1225 t=+29,8 | bear +0,0733 t=+11,8 -> tanda SAMA.
  Kuintil exc20: Q1 -1,68% Q2 +0,17% Q3 +0,73% Q4 +0,51% Q5 +0,46%.
  Sisakan 20-80% tengah: +0,48%/20 hari; 30-70%: +0,61%; semua: +0,03%.
  Setelah ukuran dikeluarkan (residual ganda): IC +0,1029 t=+28,96 -> bukan
  proksi ukuran.
- `value_per_tx` di LIKUID juga kuat: blok IC h5 +0,0641 t=+11,56 | h20 +0,0703
  t=+5,68; holdout keempatnya lolos blok; bull +0,1187 / bear +0,0806 (sama).
  Inilah yang membuat filter diperluas ke LIKUID & CUKUP (lihat combo_study.py
  Bagian D) — dan sedari revisi ini kode produksinya memakai ambang PER KELAS.
- `ng_share` (porsi blok negosiasi) kini LOLOS BLOK di kelas likuid:
  LIKUID h5 +0,0242 t=+5,88 | h20 +0,0299 t=+3,43; SANGAT h5 +0,0287 t=+4,81 |
  h20 +0,0402 t=+3,12. Di SEMUA PASAR tetap lemah (h5 +0,0082 t=+2,62, h20 mati).
  Median NG = 0,00% di SEMUA kelas, ekstrem hanya 2,5% saham-hari (NG > 90%).

- Bentuknya PENYARING, bukan peringkat: kuintilnya TIDAK monoton (Q1 jelas
  terburuk, tapi Q5 bukan terbaik). Dipakai sebagai "jangan pilih saham dengan
  tiket rata-rata terkecil", BUKAN "beli yang tiketnya terbesar".

- KOREKSI PENTING (tetap berlaku di dataset 1.610 tanggal): `value_per_tx`
  **TIDAK berlaku di SEMUA PASAR**. Di seluruh pasar blok IC h20 = -0,0085
  (t=-0,68) dan tx-per-saham tetap negatif — efeknya hanya sah di kelas likuid.
  Memasangnya sebagai filter pasar-luas akan salah.

- Jumlah transaksi (`tx_total`): konsisten NEGATIF di semua universe dan di
  kedua paruh holdout, kini DENGAN blok yang lolos
  (SEMUA h5 -0,0479 t=-5,76; LIKUID -0,0409 t=-7,10; SANGAT -0,0266 t=-3,84;
  kedelapan paruh holdout semuanya tanda negatif). TAPI setelah di-residualkan
  terhadap ukuran, kuintilnya menjadi TIDAK monoton dan ekstremnya BERBALIK
  antar universe (Q5 terbaik di seluruh pasar, Q5 terburuk di kelas likuid).
  Artinya sebagian besar efek ini adalah proksi ukuran dan sisa murninya tidak
  bisa diperdagangkan. Jangan dipakai sebagai sinyal.

- `freq` (jumlah transaksi) SUDAH ada di payload /api/history Arjum, jadi
  value_per_tx bisa dihitung di aplikasi dengan NOL tambahan kuota.

CATATAN PENARIKAN
-----------------
PENARIKAN SUDAH TUNTAS: 1.827 dari 1.827 hari kerja punya file cache, tanpa
sisa. Sebarannya: 1.610 tanggal berisi data, 217 kosong.

TEMUAN PENTING — ARSIP IDX MULAI 2020-01-02, dan itu batas KERAS:
seluruh Sep-Des 2019 membalas **HTTP 200 dengan 0 emiten** (bukan 429, bukan
blokir). Diuji langsung 8 tanggal acak (2019-09-12, -09-20, -10-01, -11-01,
-11-11, -12-30) semuanya 0 emiten, sementara 2020-01-02 mengembalikan 671
emiten. Jadi jangan buang waktu mencoba menarik 2019 lagi — datanya memang
tidak diterbitkan. Jendela analisis yang sah: 2020-01-02 s/d 2026-09-11
(6,7 tahun).

Tingkat tarik yang aman dan terbukti: `--workers 2 --delay 0.45` = ~525
permintaan dalam 228 detik TANPA satu pun 429. Setelah kena 429, cooldown
perlu lebih dari 150 detik (dua percobaan dengan jeda 150 detik masih ditolak).
(Cache di research/.cache/idxsummary/ — sudah ter-ignore git.)

Jalankan:
  .venv/bin/python research/idx_daily_summary.py --years 7 --budget 0   # analisis dari cache
  .venv/bin/python research/idx_daily_summary.py --years 7 --reverse --budget 300 --fetch-only
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from curl_cffi import requests as cr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".cache", "idxsummary")
OUT_CSV = os.path.join(HERE, ".cache", "idx_daily_summary.csv")

IDX_URL = "https://www.idx.co.id/primary/TradingSummary/GetStockSummary"
HEADERS = {
    "Referer": "https://www.idx.co.id/en/market-data/trading-summary/broker-summary/",
    "Accept": "application/json, text/plain, */*",
}

BUDGET = 900
DELAY = 0.0        # jeda sopan antar permintaan (detik); >0 mencegah HTTP 429
HORIZONS = (5, 20)

_lock = threading.Lock()
_state = {"requests": 0, "cache": 0, "stale": 0, "empty": 0, "throttled": 0, "blocked": False}


# ---------------------------------------------------------------------------
# 1. TARIK DATA (1 request = 1 hari bursa = seluruh pasar)
# ---------------------------------------------------------------------------

def _take() -> bool:
    with _lock:
        if _state["requests"] >= BUDGET or _state["blocked"]:
            return False
        _state["requests"] += 1
        return True


def _note_throttle(status: int) -> None:
    """IDX membalas 429 bila kita terlalu banyak meminta (terbukti 14 Sep 2026).

    Ia bukan kegagalan per-tanggal: setelah kena, SEMUA permintaan gagal selama
    beberapa waktu. Jadi lebih baik berhenti rapi dan melaporkan, daripada
    membakar ribuan permintaan yang pasti gagal. Progres yang sudah di-cache aman.
    """
    with _lock:
        _state["throttled"] += 1
        if _state["throttled"] >= 5:
            _state["blocked"] = True


def fetch_day(day: dt.date) -> Optional[List[dict]]:
    ds = day.strftime("%Y%m%d")
    path = os.path.join(CACHE_DIR, f"{ds}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            _state["cache"] += 1
            return d.get("data")
        except Exception:
            pass
    if not _take():
        return None
    url = f"{IDX_URL}?date={ds}&length=2000&start=0"
    for attempt in range(3):
        try:
            r = cr.get(url, headers=HEADERS, impersonate="chrome", timeout=30)
            if r.status_code == 200 and r.text.strip().startswith("{"):
                d = r.json()
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(d, fh)
                if not d.get("data"):
                    _state["empty"] += 1
                if DELAY:
                    time.sleep(DELAY)
                return d.get("data")
            if r.status_code == 429:
                _note_throttle(429)
                return None          # tidak ada gunanya mengulang saat di-throttle
            time.sleep(2 + attempt + DELAY)
        except Exception:
            time.sleep(2 + attempt + DELAY)
    _state["stale"] += 1
    return None


def fetch_range(years: int, workers: int = 4, reverse: bool = False) -> pd.DataFrame:
    end = dt.date(2026, 9, 11)
    start = end - dt.timedelta(days=int(365.25 * years))
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    days = [d for d in days if d.weekday() < 5]     # lewati akhir pekan
    if reverse:
        # Terbaru dulu: saat IDX membalas 429, yang WAJIB dihentikan adalah hari-hari
        # paling tua (yang paling sering di-throttle), sementara kemajuan tetap
        # menumpuk dari sisi terbaru. Tanpa ini, satu 429 di 2020 memblokir seluruh run.
        days = days[::-1]
        print(f"  menarik {len(days)} hari kerja (urutan TERBARU dulu, {end} s/d {start})...")
    else:
        print(f"  menarik {len(days)} hari kerja ({start} s/d {end})...")

    rows: List[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for day, data in zip(days, ex.map(fetch_day, days)):
            if not data:
                continue
            for r in data:
                try:
                    v = float(r.get("Value") or 0.0)
                    nv = float(r.get("NonRegularValue") or 0.0)
                    if v <= 0 and nv <= 0:
                        continue
                    rows.append({
                        "code": str(r.get("StockCode") or ""),
                        "date": day,
                        "value": v,                              # pasar REGULER (IDR)
                        "freq": float(r.get("Frequency") or 0.0),  # jumlah transaksi reguler
                        "ng_value": nv,                           # blok negosiasi (NG)
                        "ng_freq": float(r.get("NonRegularFrequency") or 0.0),
                        "close": float(r.get("Close") or 0.0),
                        "prev": float(r.get("Previous") or 0.0),
                        "shares": float(r.get("ListedShares") or 0.0),
                    })
                except Exception:
                    continue
    print(f"  request={_state['requests']} cache={_state['cache']} "
          f"kosong={_state['empty']} gagal={_state['stale']} throttle={_state['throttled']}")
    if _state["blocked"]:
        print("  !! IDX membalas 429 (rate limit). Berhenti rapi. Progres yang sudah")
        print("     di-cache aman — jalankan ulang beberapa menit lagi untuk melanjutkan.")
    df = pd.DataFrame(rows)
    if len(df):
        df["date"] = pd.to_datetime(df["date"])
        tot = df["value"] + df["ng_value"]
        df["ng_share"] = np.where(tot > 0, df["ng_value"] / tot, np.nan)
        df["tx_total"] = df["freq"] + df["ng_freq"]
        df["value_per_tx"] = np.where(df["freq"] > 0, df["value"] / df["freq"], np.nan)
        # Nilai harian untuk kelas likuiditas: reguler saja (persis definisi aplikasi).
        df = df[df["code"].str.len() == 4]
    return df


# ---------------------------------------------------------------------------
# 2. HARGA & HASIL KE DEPAN
# ---------------------------------------------------------------------------

def load_prices(codes: List[str], years: int):
    from backtest_rs import _fetch_yahoo, _norm  # type: ignore

    out: Dict[str, pd.Series] = {}

    def work(code: str):
        df = _fetch_yahoo(f"{code}.JK", f"{years}y")
        return code, (_norm(df)["Close"] if df is not None and not df.empty else None)

    with ThreadPoolExecutor(max_workers=8) as ex:
        for code, s in ex.map(work, codes):
            if s is not None:
                out[code] = s
    ih = _fetch_yahoo("^JKSE", f"{years}y")
    idx = _norm(ih)["Close"] if ih is not None and not ih.empty else pd.Series(dtype=float)
    return out, idx


def attach_forward(D: pd.DataFrame, prices, idx) -> pd.DataFrame:
    have = idx is not None and not idx.empty
    chunks = []
    for code, g in D.groupby("code"):
        px = prices.get(code)
        if px is None or px.empty:
            continue
        px = px.copy()
        px.index = pd.to_datetime(px.index).normalize()
        px = px[~px.index.duplicated(keep="last")].sort_index()
        ih = None
        if have:
            ih = idx.copy()
            ih.index = pd.to_datetime(ih.index).normalize()
            ih = ih[~ih.index.duplicated(keep="last")].sort_index()
            ih = ih.reindex(ih.index.union(px.index)).ffill().reindex(px.index).ffill()
        g = g.copy()
        g["date"] = pd.to_datetime(g["date"]).dt.normalize()
        pos = px.index.searchsorted(g["date"].values)
        ok = (pos >= 0) & (pos < len(px))
        ok &= np.asarray(px.index[pos.clip(max=len(px) - 1)] == g["date"].values)
        g = g[ok].copy()
        pos = pos[ok]
        if not len(g):
            continue
        c0 = px.values[pos]
        for h in HORIZONS:
            valid = pos + h < len(px)
            g[f"fwd{h}"] = np.where(valid, px.values[np.clip(pos + h, 0, len(px) - 1)] / c0 - 1.0, np.nan)
            if ih is not None:
                g[f"exc{h}"] = np.where(
                    valid,
                    g[f"fwd{h}"].values - (ih.values[np.clip(pos + h, 0, len(px) - 1)] / ih.values[pos] - 1.0),
                    np.nan)
            else:
                g[f"exc{h}"] = g[f"fwd{h}"]
        g["avg_value20"] = (g["value"].rolling(20, min_periods=10).mean()).values
        chunks.append(g)
    return pd.concat(chunks, ignore_index=True) if chunks else D


# ---------------------------------------------------------------------------
# 3. STATISTIK (klaster per tanggal; blok tidak tumpang-tindih)
# ---------------------------------------------------------------------------

def ic_by_date(D: pd.DataFrame, metric: str, outcome: str, stride: int = 1) -> Tuple[float, float, int]:
    if stride > 1:
        dst = np.sort(D["date"].unique())[::stride]
        D = D[D["date"].isin(dst)]
    ics = []
    for _, g in D.groupby("date"):
        s = g[[metric, outcome]].dropna()
        if len(s) < 20 or s[metric].nunique() < 3 or s[outcome].nunique() < 3:
            continue
        ic = s[metric].rank().corr(s[outcome].rank())
        if not np.isnan(ic):
            ics.append(ic)
    if len(ics) < 3:
        return np.nan, np.nan, len(ics)
    a = np.array(ics, dtype=float)
    return float(a.mean()), float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))), len(a)


def quintile(D: pd.DataFrame, metric: str, outcome: str) -> None:
    parts = []
    for _, g in D.groupby("date"):
        s = g[[metric, outcome]].dropna()
        if len(s) < 50:
            continue
        try:
            q = pd.qcut(s[metric].rank(method="first"), 5, labels=False)
        except Exception:
            continue
        parts.append(s[outcome].groupby(q).mean())
    if not parts:
        return
    avg = pd.concat(parts, axis=1).mean(axis=1)
    print(f"  {metric:<16} " + "  ".join(f"Q{int(k)+1}={v*100:+.2f}%" for k, v in avg.items()))


def holdout(S: pd.DataFrame, metric: str) -> None:
    """Uji stabilitas: paruh waktu pertama vs paruh kedua.

    Sinyal yang hanya hidup di satu paruh adalah temuan dalam noise, bukan sinyal.
    """
    ds = np.sort(S["date"].unique())
    if len(ds) < 40:
        return
    mid = ds[len(ds) // 2]
    out = []
    for label, part in (("awal", S[S["date"] < mid]), ("akhir", S[S["date"] >= mid])):
        for h in HORIZONS:
            ic, t, n = ic_by_date(part, metric, f"exc{h}")
            icb, tb, nb = ic_by_date(part, metric, f"exc{h}", stride=h)
            if np.isnan(ic):
                continue
            mark = ""
            if not np.isnan(icb) and abs(tb) >= 1.96:
                mark = " <=\u2713"
            out.append(f"    {label:<5} exc{h:<2} IC {ic:+.4f} t={t:+5.2f} | blok {icb:+.4f} t={tb:+5.2f} ({nb}){mark}")
    if out:
        print(f"  [holdout] {metric}")
        for line in out:
            print(line)


def residual_quintile(S: pd.DataFrame) -> None:
    """`value_per_tx` setelah dibersihkan dari ukuran — kuintil + bentuk penyaring.

    Kalau sinyalnya cuma proksi ukuran, IC-nya harus mati di sini. Kalau hidup
tapi kuintilnya tidak monoton, ia penyaring (buang satu ekstrem), bukan peringkat.
    """
    if len(S) < 5000:
        return
    W = S.copy()
    W["lx"] = np.log(W["value_per_tx"].clip(lower=1))
    W["lc"] = np.log(W["avg_value20"].clip(lower=1))
    qs, ics, trims = [], [], {k: [] for k in ((0.0, 1.0), (0.2, 0.8), (0.3, 0.7))}
    for _, g in W.groupby("date"):
        s = g[["lx", "lc", "exc20"]].dropna()
        if len(s) < 60:
            continue
        rx, rc = s["lx"].rank(), s["lc"].rank()
        resid = rx - np.polyval(np.polyfit(rc, rx, 1), rc)
        ic = resid.corr(s["exc20"].rank())
        if not np.isnan(ic):
            ics.append(ic)
        qs.append(s["exc20"].groupby(pd.qcut(resid.rank(method="first"), 5, labels=False)).mean())
        pct = resid.rank(pct=True)
        for (lo, hi) in trims:
            sel = s["exc20"][(pct > lo) & (pct <= hi)]
            if len(sel) >= 10:
                trims[(lo, hi)].append(sel.mean())
    if not qs:
        return
    a = np.array(ics)
    avg = pd.concat(qs, axis=1).mean(axis=1)
    print(f"  [residual vs ukuran] value_per_tx: IC {a.mean():+.4f} t={a.mean()/(a.std(ddof=1)/np.sqrt(len(a))):+5.2f} ({len(a)} tgl)")
    print("    kuintil exc20: " + "  ".join(f"Q{int(k)+1}={v*100:+.2f}%" for k, v in avg.items()))
    for (lo, hi), v in trims.items():
        if v:
            print(f"    sisakan {int(lo*100)}-{int(hi*100)}% tengah: {np.mean(v)*100:+.2f}% per 20 hari")


def regime_quintile(S: pd.DataFrame, idx: pd.Series, metric: str = "value_per_tx") -> None:
    """Uji LINTAS REZIM: apakah efeknya sama saat pasar naik dan saat pasar turun.

    Kandidat yang cuma hidup di satu rezim adalah artefak, bukan sinyal (pelajaran
    dari research/bandar_study.py, tempat tanda berbalik saat tumpang-tindih dibuang).
    Rezim ditentukan dari IHSG vs MA200-nya SENDIRI pada tanggal sinyal — point-in-time,
    bukan label yang dipasang setelah melihat hasil.
    """
    if idx is None or idx.empty or len(S) < 5000:
        return
    ih = idx.copy()
    ih.index = pd.to_datetime(ih.index).normalize()
    ih = ih[~ih.index.duplicated(keep="last")].sort_index()
    ma = ih.rolling(200, min_periods=120).mean()
    reg = pd.DataFrame({"ih": ih, "ma": ma}).dropna()
    reg["bull"] = reg["ih"] > reg["ma"]
    map_bull = reg["bull"].to_dict()

    W = S.copy()
    W["bull"] = pd.to_datetime(W["date"]).dt.normalize().map(map_bull)
    W = W.dropna(subset=["bull"])
    if W["bull"].nunique() < 2:
        print(f"  [rezim] hanya satu rezim terdeteksi — {int(W['bull'].sum())} baris di sukuk")
        return
    W["lx"] = np.log(W[metric].clip(lower=1))
    W["lc"] = np.log(W["avg_value20"].clip(lower=1))

    print(f"  [rezim] IHSG vs MA200 pada tanggal sinyal — {metric}")
    for label, want_bull in (("IHSG > MA200 (sukuk)", True), ("IHSG < MA200 (bear)", False)):
        R = W[W["bull"] == want_bull]
        ds = np.sort(R["date"].unique())
        if len(R) < 1000 or len(ds) < 10:
            print(f"    {label:<22} sampel kurang ({len(R):,} baris, {len(ds)} tanggal) — lewati")
            continue
        n_tick, s_tick, ok = 0, 0.0, 0
        ics = []
        for _, g in R.groupby("date"):
            s = g[["lx", "lc", "exc20"]].dropna()
            if len(s) < 40:
                continue
            rx, rc = s["lx"].rank(), s["lc"].rank()
            resid = rx - np.polyval(np.polyfit(rc, rx, 1), rc)
            pct = resid.rank(pct=True)
            lows = s["exc20"][pct <= 0.2]
            rest = s["exc20"][pct > 0.2]
            if len(lows) >= 5 and len(rest) >= 5:
                n_tick += len(lows)
                s_tick += lows.mean()
                ok += 1
            ic = resid.corr(s["exc20"].rank())
            if not np.isnan(ic):
                ics.append(ic)
        a = np.array(ics)
        t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 2 else np.nan
        base = R.groupby("date")["exc20"].mean().mean()
        low = s_tick / ok if ok else np.nan
        print(f"    {label:<22} n={len(R):>7,} ({len(ds):>3} tgl) IC {a.mean():+.4f} t={t:+5.2f} · "
              f"kuintil tiket terendah {low*100:+.2f}% vs rata-rata pasar {base*100:+.2f}%")


def main() -> None:
    global BUDGET, DELAY
    pd.set_option("display.width", 200)
    ap = argparse.ArgumentParser(description="Ringkasan harian IDX resmi (seluruh pasar)")
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--budget", type=int, default=900)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.0,
                    help="Jeda sopan antar permintaan (detik). IDX membalas HTTP 429 "
                         "bila kita meminta terlalu cepat; pakai --workers 1 --delay 3 "
                         "untuk penarikan panjang.")
    ap.add_argument("--min-stocks", type=int, default=200)
    ap.add_argument("--reverse", action="store_true",
                    help="Tarik hari TERBARU dulu (disarankan untuk penarikan panjang: "
                         "429 di tanggal tua tidak memblokir kemajuan di sisi terbaru).")
    ap.add_argument("--fetch-only", action="store_true",
                    help="Hanya tarik & simpan data (tanpa analisis). Berguna untuk "
                         "melanjutkan penarikan panjang secara bertahap dari cache.")
    args = ap.parse_args()
    BUDGET = args.budget
    DELAY = max(0.0, args.delay)

    print("== RINGKASAN HARIAN IDX RESMI ==")
    D = fetch_range(args.years, args.workers, args.reverse)
    if not len(D):
        print("  tidak ada data."); return
    print(f"  {len(D):,} baris saham-hari, {D['code'].nunique()} emiten, "
          f"{D['date'].nunique()} tanggal")

    keep = D.groupby("date")["code"].nunique()
    keep = keep[keep >= args.min_stocks].index
    D = D[D["date"].isin(keep)].copy()
    print(f"  saring >= {args.min_stocks} emiten/tanggal -> {D['date'].nunique()} tanggal "
          f"({D['date'].min().date()} s/d {D['date'].max().date()})")
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    D.to_csv(OUT_CSV, index=False)
    if args.fetch_only:
        print(f"  --fetch-only: berhenti di sini. Data tersimpan di {OUT_CSV}")
        return

    print("\n-- 1) DESKRIPTIF: porsi blok NEGOSIASI (NG) --")
    print(f"  porsi NG atas nilai: median {D['ng_share'].median()*100:.2f}%, "
          f"p90 {D['ng_share'].quantile(.9)*100:.2f}%, p99 {D['ng_share'].quantile(.99)*100:.2f}%")
    for thr in (0.2, 0.5, 0.9):
        n = int((D["ng_share"] > thr).sum())
        print(f"    NG > {thr*100:>3.0f}%: {n:>7,} dari {len(D):,} saham-hari "
              f"({n/len(D)*100:.1f}%)")
    print("\n  menurut kelas likuiditas (nilai REGULER rata-rata 20 hari):")
    D["value"] = pd.to_numeric(D["value"], errors="coerce")
    D["grade"] = pd.cut(D["avg_value20"] if "avg_value20" in D else D["value"],
                        [-1, 100e6, 1e9, 10e9, 1e15],
                        labels=["KURANG (<100jt)", "CUKUP (100jt-1M)", "LIKUID (1-10M)", "SANGAT (>=10M)"])
    agg = D.groupby("grade", observed=True).agg(n=("ng_share", "size"), ng=("ng_share", "median"))
    for g, r in agg.iterrows():
        print(f"    {str(g):<18} n={int(r['n']):>7,}  median NG {r['ng']*100:5.2f}%")

    print("\n-- 2) APAKAH PORSI NG MEMPREDIKSI? (blok tidak tumpang-tindih) --")
    prices, idx = load_prices(sorted(D["code"].unique()), int(args.years) + 1)
    D = attach_forward(D, prices, idx)
    print(f"  {len(D):,} baris dengan harga")

    # Dipecah: SEMUA pasar vs hanya yang benar-benar bisa ditransaksikan.
    # Kalau sinyalnya cuma hidup di saham mikro, ia tidak bisa dipakai.
    subsets = [
        ("SEMUA PASAR", D),
        ("LIKUID >=Rp1M/hari", D[D["avg_value20"] >= 1e9]),
        ("SANGAT LIKUID >=Rp10M/hari", D[D["avg_value20"] >= 10e9]),
    ]
    for label, S in subsets:
        if len(S) < 5000:
            continue
        print(f"\n  === {label} === n={len(S):,} "
              f"({S['code'].nunique()} emiten, {S['date'].nunique()} tanggal)")
        for metric in ("ng_share", "value_per_tx", "tx_total"):
            for h in HORIZONS:
                ic, t, n = ic_by_date(S, metric, f"exc{h}")
                icb, tb, nb = ic_by_date(S, metric, f"exc{h}", stride=h)
                if np.isnan(ic):
                    continue
                blok = f"   [blok] IC {icb:+.4f} t={tb:+5.2f} ({nb} tgl)" if not np.isnan(icb) else ""
                print(f"  {metric:<14} vs exc{h:<2}: IC {ic:+.4f} t={t:+5.2f} ({n} tgl){blok}")
        print()
        quintile(S, "ng_share", "exc20")
        quintile(S, "value_per_tx", "exc20")
        quintile(S, "tx_total", "exc20")
        holdout(S, "value_per_tx")
        holdout(S, "tx_total")
        residual_quintile(S)
        regime_quintile(S, idx)

    print("\n-- BATASAN --")
    print("  * `Value` IDX = pasar REGULER saja (sudah diverifikasi identik dengan")
    print("    sumber yang dipakai aplikasi), jadi kelas likuiditas kita tidak tercemar NG.")
    print("  * Rincian per saham PER BROKER tidak diterbitkan IDX -> sinyal akumulasi")
    print("    per broker tetap terbatas 80 hari dan tidak bisa diuji di sini.")
    print("  * Universe = emiten yang terdaftar pada tanggal itu (lebih baik dari daftar")
    print("    saat ini, tapi saham yang delisting sebelum 2020 tetap tidak ada).")


if __name__ == "__main__":
    main()
