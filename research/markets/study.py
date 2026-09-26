#!/usr/bin/env python3
"""Ukur strategi kandidat di pasar AS & crypto dengan DUA ukuran (rata-rata + tahan-outlier).

Kenapa ada
----------
Kriteria yang menang di IDX belum tentu menang di pasar lain: saham AS punya celah
overnight & jam tutup, crypto diperdagangkan 24/7 dengan volatilitas jauh lebih
tinggi dan tanpa jam tutup. Karena itu strategi per pasar harus DIUKUR, bukan
diwarisi. Berkas ini memakai disiplin yang sama dengan `criteria_audit.py`:

  * satu panel, satu pembanding (rata-rata lintas-saham pada tanggal yang sama),
  * t-stat blok TIDAK TUMPANG-TINDIH (stride = horizon),
  * holdout dua paruh waktu,
  * ekspektasi BERSIH setelah biaya,
  * DUA ukuran: rata-rata DAN tahan-outlier (median) — kalau tandanya berbeda,
    hasilnya ditentukan beberapa nama ekstrem dan aturannya TIDAK dipasang.

Perbedaan yang disengaja dari audit IDX: di sini tidak ada kelas likuiditas, jadi
pembandingnya adalah rata-rata SELURUH panel pada tanggal itu (satu kelas). Itu
menjawab pertanyaan "apakah sinyal ini mengalahkan pasar yang sama", bukan "apakah
ia mengalahkan saham sekelasnya".

Jalankan:
    .venv/bin/python research/markets/study.py --market us
    .venv/bin/python research/markets/study.py --market etf
    .venv/bin/python research/markets/study.py --market crypto
    .venv/bin/python research/markets/study.py --selftest      # tanpa jaringan
"""

from __future__ import annotations

import argparse
import math
import os
import pickle
import sys
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
sys.path.insert(0, RESEARCH)

import criteria_audit as CA  # noqa: E402  <- metrik yang sama dengan audit IDX

CACHE_DIR = os.path.join(RESEARCH, ".cache", "markets")
HORIZONS = (1, 5, 20)
# Biaya round-trip: saham AS ~0,3% (sama dengan default /api/backtest); crypto
# lebih mahal (taker Kraken ~0,26%/sisi + spread) sehingga dipakai 0,5%.
# ETF diperlakukan seperti saham AS: komisi/spread sekuritas AS, jadi 0,3%.
COST = {"us": 0.003, "etf": 0.003, "crypto": 0.005}
MIN_VALUE_USD = 1_000_000     # saring nama yang tidak bisa dieksekusi (~Rp15 M/hari)

# Batas potong (winsorize) return ke depan, dalam persen. WAJIB untuk crypto dan
# dipakai juga di AS supaya perlakuannya sama.
#
# Kenapa ada: pembandingnya adalah RATA-RATA lintas-aset pada tanggal yang sama.
# Di crypto, ekor return sangat gemuk (tidak ada auto-reject seperti bursa saham) —
# satu koin yang naik ribuan persen dalam 20 hari membuat rata-rata se-pasar ~+700%,
# sehingga SEMUA koin lain tampak -700% dan tabelnya jadi omong kosong (angka
# pertama yang keluar: a20 -2530%). Itu cacat alat ukur, bukan temuan pasar —
# persis pelajaran yang dulu melahirkan ukuran tahan-outlier di audit IDX.
# Potongan ini membatasi pengaruh satu aset pada rata-rata, dan ambangnya
# DITULIS di kepala laporan supaya tidak tersembunyi.
WINSOR_PCT = 100.0


# ---------------------------------------------------------------------------
# Indikator lokal (sengaja tidak mengimpor api/index.py: skrip ini harus bisa
# jalan tanpa IDX Edge/FastAPI dan tanpa efek samping impor)
# ---------------------------------------------------------------------------

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _load_panel(market: str) -> pd.DataFrame:
    path = os.path.join(CACHE_DIR, f"{market}_panel.pkl")
    if not os.path.exists(path):
        raise SystemExit(f"Panel {path} belum ada — jalankan pull.py lebih dulu.")
    with open(path, "rb") as fh:
        return pickle.load(fh)


def signals_for(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """Sinyal kandidat, dihitung HANYA dari data s/d bar itu (tanpa lihat depan).

    Semuanya generik (tidak terikat IDX) supaya bisa dipakai apa adanya di AS
    maupun crypto. Ambang absolut dipakai — sama seperti kriteria produksi IDX —
    supaya hasilnya bisa dipasang langsung, bukan hanya jadi peringkat.
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float) if "high" in df else close
    low = df["low"].astype(float) if "low" in df else close
    vol = df["volume"].astype(float) if "volume" in df else pd.Series(0.0, index=df.index)

    s20, s50, s200 = sma(close, 20), sma(close, 50), sma(close, 200)
    r14 = rsi(close, 14)
    prev = close.shift(1)
    ret1 = (close / prev - 1.0) * 100.0
    ret5 = (close / close.shift(5) - 1.0) * 100.0
    volma20 = vol.rolling(20, min_periods=20).mean()
    hi20 = high.rolling(20, min_periods=20).max().shift(1)
    hi50 = high.rolling(50, min_periods=50).max().shift(1)
    hi252 = close.rolling(252, min_periods=120).max()
    lo252 = close.rolling(252, min_periods=120).min()

    out: Dict[str, pd.Series] = {}
    out["mom1d>=3%"] = (ret1 >= 3.0)
    out["mom1d>=5%"] = (ret1 >= 5.0)
    out["mom5d>=10%"] = (ret5 >= 10.0)
    out["tembus high20"] = (close > hi20)
    out["tembus high50"] = (close > hi50)
    out["di atas SMA20"] = (close > s20)
    out["di atas SMA50"] = (close > s50)
    out["di atas SMA200"] = (close > s200)
    out["tren naik (SMA20>50)"] = (close > s20) & (s20 > s50)
    xs = (s50 > s200) & (s50.shift(1) <= s200.shift(1))
    out["golden cross 50/200"] = xs.fillna(False)
    out["RSI<30 (jenuh jual)"] = (r14 < 30)
    xup = (r14 > 30) & (r14.shift(1) <= 30)
    out["RSI pulih >30"] = xup.fillna(False)
    out["dekat puncak 52m"] = (close >= 0.95 * hi252)
    out["volume 2x MA20"] = (vol >= 2.0 * volma20)
    out["pullback di uptrend"] = ((close > s200) & ((close / s20 - 1.0).abs() <= 0.03)
                                  & (r14 >= 35) & (r14 <= 65))
    out["mom5d>=10% + tembus high20"] = (ret5 >= 10.0) & (close > hi20)
    out["tren naik + tembus high20"] = ((close > s20) & (s20 > s50) & (close > hi20))
    out["turun 5d>=10% di atas SMA200"] = ((ret5 <= -10.0) & (close > s200))
    out["puncak 52m baru"] = (close >= hi252)
    out["dasar 52m baru (kontrol)"] = (close <= lo252)
    for k, v in out.items():
        out[k] = v.fillna(False).astype(bool)
    _ = low  # low dipakai nanti bila menambah sinyal; simpan agar tidak dibuang
    return out


def build_frame(panel: pd.DataFrame, market: str,
                benchmark: Optional[str] = None) -> pd.DataFrame:
    """Satu baris per (kode, tanggal) berisi semua sinyal + return ke depan + excg.

    `benchmark` (kode di dalam panel, mis. SPY untuk ETF): bila diisi, return-lebih
    dihitung terhadap BENCHMARK PASAR itu, bukan terhadap rata-rata lintas-aset pada
    tanggal yang sama. Untuk universe yang heterogen (ETF: leveraged, inverse,
    komoditas, obligasi), rata-rata lintas-ETF bukan 'pasar' — ia campuran yang
    condong ke produk berleverage, sehingga SEMUA aturan tampak negatif. Benchmark
    pasar (SPY) adalah pembanding yang dimaksud proyek ini (lihat universe.BENCHMARK).
    """
    rows: List[pd.DataFrame] = []
    # groupby, BUKAN `panel[panel["code"] == code]` di dalam loop. Versi lama
    # memfilter SELURUH panel untuk tiap ticker (O(n x jumlah ticker)); di panel IDX
    # (1,4 juta baris, ~1.000 emiten) itu masih selesai, tetapi di panel AS
    # (7+ juta baris, ~5.900 ticker) ia tidak akan selesai dalam batas waktu apa pun.
    # groupby membentuk grupnya SEKALI (O(n)) lalu tiap iterasi mengambil potongannya.
    groups = panel.sort_values(["code", "date"]).groupby("code", sort=False)
    n_codes = panel["code"].nunique()
    for i, (code, g) in enumerate(groups):
        if len(g) < 60:
            continue
        df = g.set_index("date")
        sig = signals_for(df)
        close = df["close"].astype(float)
        value = close * df["volume"].astype(float) if "volume" in df else pd.Series(0.0, index=df.index)
        v20 = value.rolling(20, min_periods=20).mean()
        d = pd.DataFrame({"code": code, "date": df.index})
        for k, v in sig.items():
            d[k] = v.to_numpy()
        d["v20"] = v20.to_numpy()
        for h in HORIZONS:
            raw = (close.shift(-h) / close - 1.0) * 100.0
            d[f"fwd{h}"] = raw.clip(-WINSOR_PCT, WINSOR_PCT).to_numpy()
        rows.append(d)
        if (i + 1) % 500 == 0:
            print(f"  ... {i + 1}/{n_codes} ticker")
    if not rows:
        raise SystemExit("Panel terlalu pendek untuk diukur (butuh >=60 bar/ticker).")
    S = pd.concat(rows, ignore_index=True)
    if benchmark:
        # Pembanding PASAR: return benchmark pada tanggal yang sama. Ia harus ada di
        # panel; kalau tidak, pengukurannya tidak bisa dipertanggungjawabkan — jadi
        # GAGAL TERANG-TERANGAN, bukan diam-diam kembali ke rata-rata lintas-aset.
        bp = panel.loc[panel["code"].astype(str) == benchmark, ["date", "close"]]
        if bp.empty:
            raise SystemExit(f"Benchmark '{benchmark}' tidak ada di panel {market} — "
                             "tidak bisa diukur terhadapnya.")
        bclose = bp.sort_values("date").set_index("date")["close"].astype(float)
        bclose = bclose[~bclose.index.duplicated(keep="last")]
        for h in HORIZONS:
            bfwd = ((bclose.shift(-h) / bclose - 1.0) * 100.0).clip(-WINSOR_PCT, WINSOR_PCT)
            S[f"excg{h}"] = S[f"fwd{h}"] - S["date"].map(bfwd)
    else:
        # Pembanding: rata-rata SELURUH panel pada tanggal yang sama (satu kelas).
        for h in HORIZONS:
            S[f"excg{h}"] = S[f"fwd{h}"] - S.groupby("date")[f"fwd{h}"].transform("mean")
    _ = market
    return S


def measure(S: pd.DataFrame, market: str, signals: List[str],
            min_value_usd: float = MIN_VALUE_USD) -> pd.DataFrame:
    """Tabel hasil: rata-rata, tahan-outlier, blok t, paruh, dan net setelah biaya."""
    cost = COST.get(market, 0.003)
    tradable = S["v20"] >= min_value_usd if min_value_usd else pd.Series(True, index=S.index)
    out = []
    for name in signals:
        mask = S[name].astype(bool) & tradable
        row = {"aturan": name, "n": int(mask.sum())}
        for h in HORIZONS:
            per = S.loc[mask].groupby("date")[f"excg{h}"].mean()
            row[f"a{h}"] = float(per.mean()) if len(per) else float("nan")
            row[f"t{h}"] = CA.block_t(per, h)
            med = CA.median_excess(S, mask, h)
            row[f"m{h}"] = float(med.mean()) if len(med) else float("nan")
            h1, h2 = CA.holdout(per, h)
            row[f"p{h}"] = f"{h1:+.2f}/{h2:+.2f}" if not math.isnan(h1) else "-"
            raw = S.loc[mask, f"fwd{h}"].mean()
            row[f"net{h}"] = (float(raw) - cost * 100.0) if np.isfinite(raw) else float("nan")
        out.append(row)
    return pd.DataFrame(out)


def verdict(row: pd.Series) -> str:
    """SEPAKAT / EKOR / RAPUH dari h20 (dua ukuran yang sama seperti audit IDX)."""
    a, m, t = row.get("a20"), row.get("m20"), row.get("t20")
    if not np.isfinite(a) or not np.isfinite(m):
        return "n<kecil"
    if a > 0 and m > 0:
        return "SEPAKAT" if (np.isfinite(t) and t >= 2) else "TIPIS"
    if a > 0 and m <= 0:
        return "EKOR"
    return "RAPUH"


def report(S: pd.DataFrame, market: str, signals: List[str],
           benchmark: Optional[str] = None) -> pd.DataFrame:
    tab = measure(S, market, signals)
    tab["putusan"] = tab.apply(verdict, axis=1)
    pembanding = (f"pembanding {benchmark} (pasar)" if benchmark
                  else "pembanding rata-rata lintas-aset pada tanggal yang sama")
    print(f"\n=== {market.upper()} · {S['code'].nunique()} ticker · "
          f"{len(S):,} baris · {S['date'].min().date()} -> {S['date'].max().date()} · "
          f"biaya {COST.get(market, 0.003) * 100:.1f}% · "
          f"return dipotong di +/-{WINSOR_PCT:.0f}% · {pembanding}")
    print(f"{'aturan':<32}{'n':>8}  {'a5':>7}{'t5':>7}  {'a20':>7}{'m20':>7}{'t20':>7}"
          f"  {'net20':>7}  {'paruh20':>14}  putusan")
    for _, r in tab.sort_values("a20", ascending=False).iterrows():
        print(f"{r['aturan']:<32}{r['n']:>8,}  {r['a5']:>+7.2f}{r['t5']:>+7.1f}  "
              f"{r['a20']:>+7.2f}{r['m20']:>+7.2f}{r['t20']:>+7.1f}  "
              f"{r['net20']:>+7.2f}  {r['p20']:>14}  {r['putusan']}")
    return tab


def selftest() -> int:
    """Verifikasi mesin ukur TANPA jaringan: panel sintetis dengan edge yang diketahui.

    Panel dibuat supaya sinyal "di atas SMA20" benar-benar punya daya prediksi
    (drift +0,4%/hari saat sinyal menyala, 0 saat tidak). Kalau mesin ukur benar,
    aturan itu harus muncul positif di KEDUA ukuran; kalau tidak, ada bug di
    perhitungan return/excess, bukan di pasar.
    """
    rng = np.random.default_rng(7)
    rows = []
    for c in range(40):
        n = 400
        price = 100.0
        hist: List[float] = []
        for t in range(n):
            s20 = float(np.mean(hist[-20:])) if len(hist) >= 20 else price
            drift = 0.004 if price > s20 else 0.0
            price *= (1 + drift + rng.normal(0, 0.01))
            hist.append(price)
            rows.append({"code": f"S{c:02d}", "date": pd.Timestamp("2022-01-03")
                         + pd.Timedelta(days=t), "open": price, "high": price * 1.005,
                         "low": price * 0.995, "close": price,
                         "volume": 2_000_000.0})
    panel = pd.DataFrame(rows)
    S = build_frame(panel, "us")
    tab = measure(S, "us", ["di atas SMA20"], min_value_usd=0)
    r = tab.iloc[0]
    print(f"[selftest] {r['aturan']}: a20={r['a20']:+.2f} m20={r['m20']:+.2f} "
          f"t20={r['t20']:+.1f} net20={r['net20']:+.2f}")
    ok = np.isfinite(r["a20"]) and r["a20"] > 0 and np.isfinite(r["m20"]) and r["m20"] > 0
    print("[selftest]", "LULUS — mesin ukur mendeteksi edge yang ditanam" if ok
          else "GAGAL — mesin ukur tidak mendeteksi edge yang ditanam")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Ukur strategi kandidat AS/crypto")
    ap.add_argument("--market", choices=["us", "etf", "crypto"], default="us")
    ap.add_argument("--selftest", action="store_true", help="uji tanpa jaringan")
    ap.add_argument("--min-value", type=float, default=MIN_VALUE_USD,
                    help="saringan nilai transaksi harian (USD); 0 = tanpa saringan")
    ap.add_argument("--benchmark", default=None,
                    help="kode benchmark di panel (mis. SPY untuk ETF): return-lebih "
                         "dihitung terhadap benchmark pasar itu, bukan rata-rata lintas-aset")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    panel = _load_panel(args.market)
    print(f"Panel {args.market}: {len(panel):,} saham-hari · "
          f"{panel['code'].nunique()} ticker")
    S = build_frame(panel, args.market, benchmark=args.benchmark)
    sigs = [c for c in S.columns if c not in ("code", "date", "v20")
            and not c.startswith(("fwd", "excg"))]
    report(S, args.market, sigs, benchmark=args.benchmark)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
