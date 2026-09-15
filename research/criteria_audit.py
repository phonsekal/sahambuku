#!/usr/bin/env python3
"""Audit head-to-head SEMUA kriteria screener di data & jam yang identik (0 kuota).

Pertanyaan yang dijawab
-----------------------
"Mana metode yang paling optimal dipakai: swing, scalping, BSJP, atau yang lain?"

Masalah pengukuran sebelumnya: tiap kriteria diuji di script berbeda, jendela
berbeda, dan pembanding berbeda, sehingga hasilnya tidak bisa dibandingkan
langsung. Script ini menyatukan semuanya:

  * SATU panel: research/panel.py (ringkasan harian IDX resmi, 2020-2026,
    ~989 emiten, ~1,36 juta saham-hari, OHLCV lengkap).
  * SATU fungsi sinyal per kriteria — yang diimpor LANGSUNG dari api/index.py,
    jadi yang diuji benar-benar kode yang jalan di produksi (bukan replika).
  * SATU metrik: return absolut, return relatif ke pasar yang sama pada tanggal
    yang sama, t-stat blok TIDAK TUMPANG-TINDIH (stride = horizon), holdout dua
    paruh, dan ekspektasi BERSIH setelah biaya 0,3% round-trip.

Dua hal yang khusus diuji di sini dan belum pernah:
  1. PREMIS BSJP apa adanya — namanya "Beli Sore Jual PAGI", jadi ukuran yang
     benar adalah OPEN hari berikutnya, bukan close. Panel ini punya kolom open,
     sehingga janji namanya bisa diuji langsung.
  2. Kedua kriteria premis harga+volume (SCALPING, BSJP) diuji di SAMPEL dan JAM
     yang sama dengan kriteria pola buku (launchpad/reversal/volsr) dan skor beli,
     sehingga peringkatnya sah.

Jalankan:
  .venv/bin/python research/criteria_audit.py                 # seluruh pasar
  .venv/bin/python research/criteria_audit.py --min-grade SANGAT LIKUID
  .venv/bin/python research/criteria_audit.py --years 5

HASIL (dijalankan 15 Sep 2026 — panel 1,34 juta saham-hari, 981 emiten, 1.590
 tanggal, 2020-01-02 s/d 2026-09-11; versi 5 tahun diperiksa juga supaya tanda
 yang cuma muncul sekali bisa ditangkap)
--------------------------------------------------------------------------------
Alpha H20 terhadap KELAS LIKUIDITAS yang sama pada tanggal yang sama
(jendela penuh -> jendela 5 tahun):

  kriteria          penuh   5 thn   blok t   paruh awal/akhir     catatan
  launchpad         +4,53   +3,81   +3,09    +5,45 / +3,62   terkuat & konsisten
  momentum>=8%      +3,08   +3,84  +15,07    +0,54 / +5,62   TIDAK stabil
  buy>=70           +1,83   +2,05  +11,47    +1,11 / +2,54   konsisten
  buy>=50           +1,13   +1,31  +21,86    +0,71 / +1,55   konsisten
  volsr             +0,62   +0,68   +8,20    +0,61 / +0,63   paling stabil
  bsjp              +0,49   -0,07   +1,27    +0,59 / +0,38   tidak nyata
  scalping          +0,47   -0,45   +1,15    +1,44 / -0,49   tidak nyata
  swing (proksi)    +0,45   +0,03   +1,42    +0,38 / +0,52   tidak nyata
  reversal          +0,15   +0,80   +1,51    +0,04 / +0,26   melemah di jendela penuh
  breakout          -0,08   +0,25   -1,72    -0,43 / +0,27   BERBALIK TANDA antar jendela

Temuan yang paling penting:
1. SWING (jalur proksi) = TIDAK punya edge terukur (t=+1,42 penuh, t=+0,10 pada
   5 tahun). Ini kriteria yang paling sering dipakai orang; di UI ia tampil setara
   dengan yang lain, padahal buktinya nol. Jalur BandarValue tidak bisa diuji
   (riwayat broker hanya 80 sesi).
2. BREAKOUT BERBALIK TANDA hanya karena jendela diperpanjang (+0,25 -> -0,08).
   Dokumen lama menyebutnya alfa +1,94% (t=+3,95) pada 5 tahun saham likuid;
   angka itu tidak bertahan di panel penuh. Perlakukan sebagai penyaring, bukan edge.
3. SCALPING & BSJP tidak berisi di horizon 20 hari, tetapi BSJP BERISI di
   horizon 1 hari bila diukur sesuai namanya: close -> OPEN besok +1,95% absolut /
   +1,18% vs kelas (blok t=+13,9), sementara close -> close besok hanya -0,41%.
   Baris selamanya +0,21% (semua kelas, semua tahun), jadi ini bukan artefak.
   KESIMPULANNYA MENGOREKSI laporan sendiri: kesimpulan lama "premis BSJP gagal"
   (combo_study Bagian G) lahir dari mengukur close-ke-close, bukan close-ke-open.
4. Syarat tambahan SCALPING/BSJP tidak menambah alpha: kandidatnya subset dari
   syarat "return harian >= 8%" (momentum mentah +3,08% vs BSJP +0,49%).
5. FILTER DEFAULT APLIKASI MEMBANTU, dan ini terukur bukan diklaim: membuang tiket
   kecil menaikkan alpha SETIAP kriteria (BSJP +0,49 -> +2,06; buy>=70 +1,83 ->
   +2,32; momentum>=8% +3,08 -> +3,97; swing +0,45 -> +1,18), dan kelompok yang
   dibuang negatif di hampir semua kriteria. Membatasi ke rezim bull juga menaikkan
   hampir semuanya (buy>=70 +2,88 bull vs +0,04 bear). PENGECUALIAN: di kelas
   SANGAT LIKUID skor beli melemah (buy>=70 +0,24, t=+1,56) sedangkan kriteria pola
   dan breakout justru kuat (breakout +1,83 t=+4,77; launchpad +2,79 t=+2,28).
6. Alfa terkonsentrasi di kelas CUKUP dan KURANG LIKUID. Karena itu pembanding
   WAJIB kelas yang sama: diukur terhadap seluruh pasar, skor beli tampak +1,32%
   (bukan +1,83%) dan swing tampak -1,44% (padahal relatif kelasnya cuma +0,45%).

BATASAN YANG HARUS DISEBUT
--------------------------
* Panel memakai kolom Open/High/Low dari ringkasan harian IDX. Kolom OpenPrice
  KOSONG (=0) untuk ~93% baris sebelum 2025 dan ~25% setelahnya; baris tanpa open
  dibuang HANYA pada uji berbasis open (bagian C), dan angka penutup disebut apa
  adanya. Kolom `prev` dipakai untuk return harian supaya definisinya sama dengan
  produksi.
* Universe = emiten yang saat ini ada di cache -> masih ada survivorship bias
  (emiten delisting tidak ikut), jadi semua angka cenderung terlalu optimistis.
* Belum ada biaya transaksi di tabel A/B/D/E; hanya kolom net20 dan bagian C yang
  sudah memotong 0,3% round-trip.
* Kriteria bandar/silent/koreksi TIDAK bisa diuji di sini (butuh riwayat per broker
  atau Broker Summary harian). Ini bukan berarti keduanya buruk.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "api"))

import panel as P          # noqa: E402
import index as A          # noqa: E402  <- kode PRODUKSI, bukan replika

HORIZONS = (1, 5, 20)
COST_ROUND_TRIP = 0.003    # 0,3% (0,15%/sisi) — sama dengan default /api/backtest


# ---------------------------------------------------------------------------
# 1. METRIK BANTU
# ---------------------------------------------------------------------------

def _liquidity_class(v20: float) -> str:
    """Kelas likuiditas yang SAMA dengan produksi (api/index.py `_liquidity_grade`)."""
    return A._liquidity_grade(v20)


def block_t(excess_series: pd.Series, h: int) -> float:
    """t-stat dari blok tidak tumpang-tindih (stride = h).

    Horizon h hari TIDAK independen antar tanggal berturut-turut: satu peristiwa
    pasar terhitung h kali. Karena itu per-tanggal dirata-rata lalu dipecah jadi h
    blok bergiliran, dan t dihitung dari sebaran ANTAR BLOK.

    h=1 tidak punya pengelompokan yang bisa dibuat (hanya 1 blok), jadi di situ
    dipakai t-stat biasa dari deret per-tanggal — untuk return 1 hari, tanggal
    memang hampir independen sehingga tidak ada tumpang-tindih yang perlu dikoreksi.
    """
    per = excess_series.dropna()
    if len(per) < 2 * h:
        return float("nan")
    if h <= 1:
        if len(per) < 20 or per.std(ddof=1) == 0:
            return float("nan")
        return float(per.mean() / (per.std(ddof=1) / math.sqrt(len(per))))
    blocks = np.array([per.iloc[i::h].mean() for i in range(h)], dtype=float)
    blocks = blocks[~np.isnan(blocks)]
    if len(blocks) < 3 or blocks.std(ddof=1) == 0:
        return float("nan")
    return float(blocks.mean() / (blocks.std(ddof=1) / math.sqrt(len(blocks))))


def holdout(excess_series: pd.Series, h: int) -> Tuple[float, float]:
    """Alpha di paruh waktu AWAL dan AKHIR — sinyal yang cuma hidup di satu paruh = noise."""
    per = excess_series.dropna()
    if len(per) < 4 * h:
        return float("nan"), float("nan")
    mid = len(per) // 2
    return (float(per.iloc[:mid].mean()), float(per.iloc[mid:].mean()))


# ---------------------------------------------------------------------------
# 2. SINYAL PER KRITERIA (dari kode produksi)
# ---------------------------------------------------------------------------

def signals_for(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """Deret boolean per kriteria, dihitung HANYA dari data s/d bar itu.

    Semua fungsi dipanggil dari api/index.py apa adanya. Yang tidak bisa diuji di
    sini sengaja tidak dipaksakan dan dicatat alasannya di laporan:
      * bandar  : butuh Broker Summary HARI INI (tidak ada riwayat)
      * silent  : butuh riwayat per-broker (hanya 80 sesi milik pihak ketiga)
      * koreksi : daftar pantauan, bukan kriteria pemilihan
      * rs      : butuh deret IHSG; di sini dipakai indeks pasar rata-rata
                  (equal-weight) sebagai pembanding, bukan IHSG bobot kapitalisasi
    """
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    vol = df["Volume"].astype(float)
    value = A._value_series(df).astype(float)

    out: Dict[str, pd.Series] = {}

    # --- pola buku (fungsi produksi apa adanya) ---
    out["launchpad"] = A.launch_pad_series(df)
    out["reversal"] = A.role_reversal_series(df)
    out["volsr"] = A.volume_sr_series(df)
    out["breakout"] = A.breakout_20_series(df)

    # --- skor beli komposit (fungsi produksi apa adanya) ---
    bs = A._buy_score_series(df)
    out["buy>=70"] = (bs >= 70).fillna(False)
    out["buy>=50"] = (bs >= 50).fillna(False)

    # --- SCALPING & BSJP: presisi `run_screener` api/index.py ---
    prev = close.shift(1)
    day_ret = (close / prev - 1.0) * 100.0
    vma20 = vol.rolling(20).mean()
    vol_ratio = vol / vma20.replace(0, np.nan)
    v20 = value.rolling(20).mean()
    out["scalping"] = ((value >= 1e9) & (day_ret >= 10.0) & (close > 50)).fillna(False)
    out["bsjp"] = ((value >= 5e9) & (day_ret >= 8.0) & (vol_ratio >= 2.0)).fillna(False)

    # --- SWING (jalur proksi, persis `run_screener`) ---
    s20 = close.rolling(20).mean()
    s50 = close.rolling(50).mean()
    r14 = A.rsi(close, 14)
    vma10 = value.rolling(10).mean()
    trend_up = (close > s20) & (s20 > s50)
    pullback = (trend_up & ((close / s20 - 1.0).abs() <= 0.03)
                & (r14 >= 35) & (r14 <= 68))
    breakout_setup = ((close >= s20) & (vol >= 1.5 * vma20)
                      & ((s20 > s50) | s50.isna()))
    out["swing"] = ((value > v20) & (v20 >= 10e9) & (value.shift(1) <= value)
                    & (vma10 > v20) & (pullback | breakout_setup)).fillna(False)

    # --- momentum mentah (pembanding: apakah ambang SCALPING/BSJP menambah apa pun) ---
    out["momentum>=10%"] = (day_ret >= 10.0).fillna(False)
    out["momentum>=8%"] = (day_ret >= 8.0).fillna(False)
    return out


# ---------------------------------------------------------------------------
# 3. PANEL HASIL PER (SAHAM, TANGGAL) UNTUK SEMUA SINYAL
# ---------------------------------------------------------------------------

def build_signal_frame(p: pd.DataFrame, min_bars: int = 60,
                       min_grade: Optional[str] = None) -> pd.DataFrame:
    """Satu baris per (saham, tanggal) berisi SEMUA sinyal + return ke depan.

    Return ke depan dihitung dari close (mode normal) DAN dari OPEN hari
    berikutnya (khusus menguji janji nama "BSJP").
    """
    codes = p["code"].unique()
    rows: List[pd.DataFrame] = []
    grade_order = ["KURANG LIKUID", "CUKUP", "LIKUID", "SANGAT LIKUID"]
    allowed = None
    if min_grade:
        allowed = set(grade_order[grade_order.index(min_grade):])

    for i, code in enumerate(codes):
        g = p[p["code"] == code]
        if len(g) < min_bars:
            continue
        df = P.to_ohlcv(g)
        close = df["Close"].astype(float)
        op = df["Open"].astype(float)
        value = A._value_series(df).astype(float)
        v20 = value.rolling(20).mean()
        grade = v20.apply(lambda v: _liquidity_class(float(v)) if pd.notna(v) else None)
        if allowed is not None:
            keep = grade.isin(allowed)
        else:
            keep = pd.Series(True, index=df.index)

        frame = pd.DataFrame(index=df.index)
        for name, ser in signals_for(df).items():
            frame[name] = ser.reindex(df.index).fillna(False).to_numpy(bool)
        frame["code"] = code
        frame["date"] = df.index
        frame["v20"] = v20.to_numpy()
        frame["grade"] = grade.to_numpy()
        # Ukuran tiket (penyaring `skip_small_ticket` di produksi) — konstanta
        # diambil dari api/index.py supaya ambangnya tidak bisa berbeda diam-diam.
        val = value.rolling(20, min_periods=20).sum()
        frq = df["Freq"].astype(float).rolling(20, min_periods=20).sum()
        ticket = val / frq.replace(0, np.nan)
        with np.errstate(all="ignore"):
            resid = np.log(ticket.where(ticket > 0)) - (
                A.TICKET_REG_INTERCEPT + A.TICKET_REG_SLOPE * np.log(v20.where(v20 > 0)))
        floor = grade.map(A.TICKET_RESID_FLOOR_BY_GRADE)
        frame["ticket_small"] = (resid <= floor).fillna(False).to_numpy(bool)
        frame["day_ret"] = ((close / close.shift(1) - 1.0) * 100.0).to_numpy()
        # Kolom OpenPrice di ringkasan harian IDX KOSONG (=0) untuk ~93% baris sebelum
        # 2025 dan ~25% setelahnya. Angka 0 itu berarti "tidak ada harga pembukaan
        # tercatat", bukan harga nol — memakainya apa adanya memberi return -100%.
        # Karena itu semua return berbasis open dimatikan bila salah satu kakinya 0.
        op_ok = (op > 0) & (op < close * 5)   # penjaga kedua: 0 yang tersamar
        for h in HORIZONS:
            frame[f"fwd{h}"] = (close.shift(-h) / close - 1.0).to_numpy() * 100.0
            no = op.shift(-h).where(op.shift(-h) > 0)
            frame[f"fwd{h}_open"] = (no / close - 1.0).to_numpy() * 100.0
        frame["open_ok"] = op_ok.to_numpy(bool)
        # BSJP apa adanya: beli di close hari ini, jual di OPEN besok.
        nxt_ok = (op.shift(-1) > 0) & (op.shift(-1) < close * 5)
        frame["fwd1_open"] = ((op.shift(-1).where(nxt_ok) / close - 1.0) * 100.0).to_numpy()
        frame["open_next_ok"] = nxt_ok.to_numpy(bool)
        rows.append(frame[keep])
        if (i + 1) % 200 == 0:
            print(f"  ... {i + 1}/{len(codes)} emiten")

    return pd.concat(rows, ignore_index=True)


def add_excess(S: pd.DataFrame) -> pd.DataFrame:
    """Tambah tiga pembanding sekaligus, supaya bedanya kelihatan.

    exc{h}     : return saham - rata-rata SELURUH saham di tanggal itu (pasar).
    excg{h}    : return saham - rata-rata saham KELAS LIKUIDITAS YANG SAMA pada
                 tanggal itu. INI pembanding yang benar untuk keputusan praktis:
                 menjawab "kalau saya pilih saham ini, apakah saya mengalahkan
                 pilihan acak di kelas likuiditas yang sama hari itu?" — bukan
                 mengalahkan rata-rata pasar yang dinaikkan mikro-cap tak likuid.
    excog{h}   : return saham - kelas & tanggal sama, TAPI ukuran (v20) sudah
                 dikeluarkan lewat residual ganda. Memisahkan "efek tiket/ukuran"
                 dari "efek sinyal".
    """
    for h in HORIZONS:
        uni = S.groupby("date")[f"fwd{h}"].mean()
        S[f"exc{h}"] = S[f"fwd{h}"] - S["date"].map(uni)
        cls = S.groupby(["date", "grade"])[f"fwd{h}"].transform("mean")
        S[f"excg{h}"] = S[f"fwd{h}"] - cls
        # Residual ganda: buang pengaruh log(v20). Karena excg{h} SUDAH relatif ke
        # kelas+tanggal, menyisakan kemiringan terhadap log(v20) di dalam kelas.
        # Satu koefisien global cukup: yang dihapus adalah efek ukuran-dalam-kelas.
        try:
            lg = np.log(S["v20"].where(S["v20"] > 0))
            ok = lg.notna() & S[f"excg{h}"].notna()
            slope = np.polyfit(lg[ok].to_numpy(float),
                               S.loc[ok, f"excg{h}"].to_numpy(float), 1)[0]
            S[f"excog{h}"] = S[f"excg{h}"] - slope * lg.fillna(lg.mean())
        except Exception:
            S[f"excog{h}"] = S[f"excg{h}"]
    uni1 = S.groupby("date")["fwd1_open"].mean()
    S["exc1_open"] = S["fwd1_open"] - S["date"].map(uni1)
    cls1 = S.groupby(["date", "grade"])["fwd1_open"].transform("mean")
    S["excg1_open"] = S["fwd1_open"] - cls1

    # --- rezim pasar: indeks rata-rata (equal-weight) + MA200 -----------------
    # IHSG sendiri tidak ada di panel (hanya saham). Karena itu dipakai indeks
    # equal-weight se-pasar sebagai pengganti rezim; definisinya sama seperti
    # produksi (`_ihsg_regime`): TREN NAIK bila indeks di ATAS MA200-nya.
    dr = S.groupby("date")["day_ret"].mean().sort_index()
    lvl = (1.0 + dr.fillna(0.0) / 100.0).cumprod()
    ma200 = lvl.rolling(200, min_periods=100).mean()
    bull = (lvl > ma200).fillna(False)
    S["regime_bull"] = S["date"].map(bull).fillna(False)
    return S


# ---------------------------------------------------------------------------
# 4. LAPORAN
# ---------------------------------------------------------------------------

CRITERIA_ORDER = [
    "scalping", "bsjp", "swing", "breakout", "launchpad", "reversal", "volsr",
    "buy>=70", "buy>=50", "momentum>=10%", "momentum>=8%",
]


def report_criteria(S: pd.DataFrame, min_grade: Optional[str], years: Optional[float]) -> None:
    n_dates = S["date"].nunique()
    base_h = {h: S.groupby("date")[f"fwd{h}"].mean().mean() for h in HORIZONS}
    print(f"\n{'='*118}")
    print(f"UNIVERSE: {S['code'].nunique()} emiten · {len(S):,} saham-hari · {n_dates} tanggal"
          + (f" · {years:g} tahun terakhir" if years else "")
          + (f" · minimal kelas {min_grade}" if min_grade else ""))
    print(f"BASELINE pasar (rata-rata semua saham, sama-rata per tanggal): "
          f"h1 {base_h[1]:+.2f}% · h5 {base_h[5]:+.2f}% · h20 {base_h[20]:+.2f}%")
    print(f"{'='*118}")

    print(f"\n-- A. PERINGKAT MEMORY: alpha H20 vs KELAS LIKUIDITAS YANG SAMA --")
    print("  Pertanyaan yang dijawab kolom 'excK': kalau saya ambil saham ini hari itu,\n"
          "  apakah saya mengalahkan pilihan ACAK di kelas likuiditas yang sama?")
    print(f"  {'kriteria':<16} {'n':>8} {'/hari':>7} {'abs20%':>8} {'excK20%':>8} "
          f"{'blok t':>7} {'paruh':>13} {'net20%':>8}")
    rows_a = []
    for name in CRITERIA_ORDER:
        m = S[name].fillna(False)
        n = int(m.sum())
        if n < 30:
            continue
        per = S.loc[m].groupby("date")[f"excg20"].mean()
        abs20 = S.loc[m, "fwd20"].mean()
        ha, hb = holdout(per, 20)
        rows_a.append((name, n, n / n_dates, abs20, per.mean(), block_t(per, 20), ha, hb,
                       abs20 - COST_ROUND_TRIP * 100))
    for r in sorted(rows_a, key=lambda x: -(x[4] if x[4] == x[4] else -99)):
        print(f"  {r[0]:<16} {r[1]:>8,} {r[2]:>7.1f} {r[3]:>+8.2f} {r[4]:>+8.2f} "
              f"{r[5]:>+7.2f} {r[6]:>+6.2f}/{r[7]:<+6.2f} {r[8]:>+8.2f}")

    print(f"\n-- A2. PEMBANDING: alpha vs SELURUH PASAR (angka yang mudah menyesatkan) --")
    print(f"  {'kriteria':<16} {'/vmkt h20':>10} {'/kelas h20':>11} {'/kelas-residu':>14} {'kelas vs pasar':>15}")
    for r in sorted(rows_a, key=lambda x: -(x[4] if x[4] == x[4] else -99)):
        name = r[0]
        m = S[name].fillna(False)
        vm = S.loc[m].groupby("date")["exc20"].mean().mean()
        og = S.loc[m].groupby("date")["excog20"].mean().mean()
        print(f"  {name:<16} {vm:>+10.2f} {r[4]:>+11.2f} {og:>+14.2f} {vm - r[4]:>+15.2f}")

    print(f"\n-- B. SEMUA HORIZON + holdout dua paruh (alpha vs pasar) --")
    print(f"  {'kriteria':<16} {'h':>3} {'n':>8} {'alpha%':>8} {'blok t':>7} "
          f"{'paruh awal':>11} {'paruh akhir':>12}")
    for name in CRITERIA_ORDER:
        m = S[name].fillna(False)
        if int(m.sum()) < 30:
            continue
        for h in HORIZONS:
            per = S.loc[m].groupby("date")[f"exc{h}"].mean()
            if len(per) < 2:
                continue
            ha, hb = holdout(per, h)
            print(f"  {name:<16} {h:>3} {int(m.sum()):>8,} {per.mean():>+8.2f} "
                  f"{block_t(per, h):>+7.2f} {ha:>+11.2f} {hb:>+12.2f}")
        print()

    print(f"-- C. PREMIS BSJP apa adanya: beli di CLOSE, jual di OPEN besok --")
    ok = S[S["open_next_ok"] & S["open_ok"] & S["fwd1_open"].notna()]
    print(f"  Hanya baris dengan OPEN besok tercatat (kolom OpenPrice ringkasan IDX\n"
          f"  kosong untuk ~93% baris sebelum 2025): {len(ok):,} saham-hari, "
          f"{ok['date'].dt.date.min()} -> {ok['date'].dt.date.max()}")
    if len(ok):
        print(f"  {'kriteria':<16} {'n':>7} {'close->open1%':>14} {'vs kelas%':>10} {'blok t':>7} "
              f"{'net%':>7} {'close->close1':>14}")
        for name in ("bsjp", "scalping", "momentum>=8%", "momentum>=10%", "breakout"):
            sub = ok[ok[name].fillna(False)]
            if len(sub) < 30:
                continue
            per = sub.groupby("date")["excg1_open"].mean()
            raw = sub["fwd1_open"].mean()
            print(f"  {name:<16} {len(sub):>7,} {raw:>+14.2f} {per.mean():>+10.2f} "
                  f"{block_t(per, 1):>+7.2f} {raw - COST_ROUND_TRIP*100:>+7.2f} "
                  f"{sub['fwd1'].mean():>+14.2f}")

    print(f"\n-- D. PER KELAS LIKUIDITAS (absolut h20 / alpha vs KELAS & tgl sama / blok t) --")
    grades = ["KURANG LIKUID", "CUKUP", "LIKUID", "SANGAT LIKUID"]
    print(f"  {'kriteria':<16} " + " ".join(f"{g[:11]:>22}" for g in grades))
    for name in CRITERIA_ORDER:
        m0 = S[name].fillna(False)
        if int(m0.sum()) < 30:
            continue
        cells = []
        for g in grades:
            sub = S.loc[m0 & (S["grade"] == g)]
            if len(sub) < 30:
                cells.append(f"{'-':>24}")
                continue
            per = sub.groupby("date")["excg20"].mean()
            cells.append(f"{sub['fwd20'].mean():+7.2f}/{per.mean():+7.2f}/{block_t(per,20):+6.2f}")
        print(f"  {name:<16} " + " ".join(f"{c:>24}" for c in cells))

    print(f"\n-- F. FILTER DEFAULT APLIKASI: tiket kecil & rezim (alpha H20 vs KELAS) --")
    print(f"  {'kriteria':<16} {'semua':>8} {'+buang tiket':>13} {'kuintil tiket':>14} "
          f"{'bull':>8} {'bear':>8} {'n bull':>8}")
    for name in CRITERIA_ORDER:
        m0 = S[name].fillna(False)
        if int(m0.sum()) < 30:
            continue
        base = S.loc[m0].groupby("date")["excg20"].mean().mean()
        keep = S.loc[m0 & ~S["ticket_small"]]
        filt = keep.groupby("date")["excg20"].mean().mean() if len(keep) >= 30 else float("nan")
        drop = S.loc[m0 & S["ticket_small"]]
        drp = drop.groupby("date")["excg20"].mean().mean() if len(drop) >= 30 else float("nan")
        bl = S.loc[m0 & S["regime_bull"]]
        br = S.loc[m0 & ~S["regime_bull"]]
        bulls = (bl.groupby("date")["excg20"].mean().mean() if len(bl) >= 30 else float("nan"))
        bears = (br.groupby("date")["excg20"].mean().mean() if len(br) >= 30 else float("nan"))
        print(f"  {name:<16} {base:>+8.2f} {filt:>+13.2f} {drp:>+14.2f} "
              f"{bulls:>+8.2f} {bears:>+8.2f} {len(bl):>8,}")

    print(f"\n-- E. PER TAHUN (alpha h20) -- apakah edge cuma milik satu tahun? --")
    years_present = sorted(S["date"].dt.year.unique())
    print(f"  {'kriteria':<16} " + " ".join(f"{y:>7}" for y in years_present))
    for name in CRITERIA_ORDER:
        m0 = S[name].fillna(False)
        if int(m0.sum()) < 30:
            continue
        cells = []
        for y in years_present:
            sub = S.loc[m0 & (S["date"].dt.year == y)]
            cells.append(f"{sub['exc20'].mean():>+7.2f}" if len(sub) >= 30 else f"{'-':>7}")
        print(f"  {name:<16} " + " ".join(cells))


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit head-to-head kriteria screener")
    ap.add_argument("--years", type=float, default=None,
                    help="batasi ke N tahun terakhir (default: seluruh cache)")
    ap.add_argument("--min-grade", default=None,
                    choices=["CUKUP", "LIKUID", "SANGAT LIKUID"])
    ap.add_argument("--min-bars", type=int, default=60)
    args = ap.parse_args()

    p = P.load_panel()
    if args.years:
        cutoff = p["date"].max() - pd.Timedelta(days=int(365.25 * args.years))
        p = p[p["date"] >= cutoff]
    print(f"panel: {len(p):,} saham-hari · {p['code'].nunique()} emiten · "
          f"{p['date'].nunique()} tanggal")

    print("membangun bingkai sinyal ...")
    S = build_signal_frame(p, min_bars=args.min_bars, min_grade=args.min_grade)
    S = add_excess(S)
    # Buang baris yang return ke depan belum tersedia (20 sesi terakhir).
    S = S[S["fwd20"].notna()].reset_index(drop=True)
    report_criteria(S, args.min_grade, args.years)


if __name__ == "__main__":
    main()
