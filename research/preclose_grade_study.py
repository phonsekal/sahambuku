#!/usr/bin/env python3
"""Apakah rekomendasi BELI KUAT / BELI / TUNGGU layak diberikan pada pindai pra-tutup?

Pertanyaan yang dijawab
-----------------------
Tabel pindai pra-tutup menampilkan perubahan hari ini, kelas likuiditas, skor beli,
RRR, dan rencana harian — tetapi tidak menjawab "ini boleh dibeli atau tidak".
Sebelum menambahkan label rekomendasi ke setiap baris, label itu harus dibuktikan
berurut: BELI KUAT > BELI > TUNGGU pada hasil yang benar-benar terjadi.

Yang diukur di sini TEPAT sama dengan yang dibaca pemakaian saat memindai pra-tutup:
harga masuk = harga tutup hari sinyal (mode pra-tutup ada justru supaya harga itu
masih bisa dibayar), dibandingkan dengan kelas likuiditas yang sama pada tanggal yang
sama, dan dipotong biaya 0,3% round-trip.

Cara kerja
----------
* Panel: research/panel.py (ringkasan harian IDX resmi, 2020-2026, 0 kuota, 0 jaringan).
* Kriteria: fungsi sinyal PRODUKSI dari api/index.py apa adanya (breakout, launchpad,
  volsr, momentum, momentumkuat).
* Rekomendasi: fungsi `preclose_grade` dari api/index.py APA ADANYA — bukan salinannya.
  Kalau ambangnya diubah di produksi, hasil di sini ikut berubah, jadi tidak mungkin
  laporan dan aplikasi berbeda diam-diam.

Tiga bagian
-----------
  grid   : tabulasi mentah kriteria x skor beli x kerapuhan (untuk memilih ambang).
  grade  : hasil akhir per label rekomendasi + uji holdout dua paruh + per kriteria.
  signal : KONFLIK yang paling sering ditanyakan — "Sinyal bilang HOLD tapi rekomendasi
           BELI KUAT, pakai yang mana?". Fungsi sinyal produksi (`quick_signal`) dipanggil
           pada bar saat itu (df dipotong), lalu dibandingkan dengan label per baris.

Jalankan
--------
  .venv/bin/python research/preclose_grade_study.py              # ketiganya
  .venv/bin/python research/preclose_grade_study.py --part grid
  .venv/bin/python research/preclose_grade_study.py --part grade
  .venv/bin/python research/preclose_grade_study.py --part signal --sample 1500
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "api"))

import panel as P                    # noqa: E402
import index as A                    # noqa: E402  <- kode PRODUKSI
from criteria_audit import add_excess, block_t, holdout, COST_ROUND_TRIP  # noqa: E402

CRITERIA = ("momentum", "momentumkuat", "breakout", "launchpad", "volsr")
MOMO_FLOOR = 100e6          # MOMENTUM_VALUE_FLOOR di api/index.py
FRAGILE_DAYRET = 10.0       # jarak <= 2% dari ambang 8%  -> "RAPUH" di produksi


def _score_bucket(sc: float) -> str:
    if sc >= 70:
        return "skor>=70"
    if sc >= 50:
        return "skor50-69"
    return "skor<50"


def build_rows(p: pd.DataFrame) -> pd.DataFrame:
    """Satu baris per (saham, tanggal) yang MUNGKIN muncul di pindai pra-tutup.

    Hanya memakai kolom yang juga dipakai produksi (Value untuk lantai nilai, Freq
    tidak dipakai karena memang tidak tersedia intraday).
    """
    codes = p["code"].unique()
    rows: List[pd.DataFrame] = []
    for i, code in enumerate(codes):
        g = p[p["code"] == code]
        if len(g) < 60:
            continue
        df = P.to_ohlcv(g)
        close = df["Close"].astype(float)
        value = A._value_series(df).astype(float)
        v20 = value.rolling(20).mean()
        day_ret = (close / close.shift(1) - 1.0) * 100.0
        brk = A.breakout_20_series(df).fillna(False)
        lp = A.launch_pad_series(df).fillna(False)
        vs = A.volume_sr_series(df).fillna(False)
        # Skor beli vektor + bonus Launch Pad = PERSIS yang dipakai compute_buy_score
        # saat bandarmology tidak diambil (mode pra-tutup: include_bandarmology=False).
        score = (A._buy_score_series(df).fillna(0.0)
                 + np.where(lp.to_numpy(bool),
                            float(A.BUY_SCORE_BONUS_WEIGHTS.get("Launch Pad", 0.0)), 0.0)
                 ).clip(upper=100.0)

        mom = (day_ret >= 8.0) & (value >= MOMO_FLOOR)
        frame = pd.DataFrame(index=df.index)
        frame["day_ret"] = day_ret
        frame["score"] = score
        frame["grade"] = v20.apply(lambda v: A._liquidity_grade(float(v))
                                   if pd.notna(v) else None)
        frame["v20"] = v20
        # Hanya baris yang lolos SALAH SATU kriteria pra-tutup yang dibutuhkan.
        frame["momentum"] = mom.fillna(False).to_numpy(bool)
        frame["momentumkuat"] = (mom & brk).fillna(False).to_numpy(bool)
        frame["breakout"] = brk.to_numpy(bool)
        frame["launchpad"] = lp.to_numpy(bool)
        frame["volsr"] = vs.to_numpy(bool)
        for h in (1, 5, 20):
            frame[f"fwd{h}"] = (close.shift(-h) / close - 1.0) * 100.0
        frame["code"] = code
        frame["date"] = df.index
        # Posisi bar di dalam deret: dibutuhkan bagian "signal" di bawah, karena
        # quick_signal() menilai BAR TERAKHIR dari df yang diberikan — jadi untuk
        # menilai suatu tanggal, df HARUS dipotong sampai tanggal itu.
        frame["pos"] = np.arange(len(df))
        keep = frame[list(CRITERIA)].any(axis=1)
        rows.append(frame[keep])
        if (i + 1) % 250 == 0:
            print(f"  ... {i + 1}/{len(codes)} emiten")

    S = pd.concat(rows, ignore_index=True)
    # Excess vs kelas likuiditas yang SAMA pada tanggal yang SAMA = pembanding yang
    # benar, dan itulah yang dipakai laporan. add_excess menghitungnya di panel PENUH
    # (semua saham, bukan hanya kandidat) supaya pembandingnya tidak miring.
    return S


def universe_means(p: pd.DataFrame) -> pd.DataFrame:
    """Rata-rata pasar & per kelas per tanggal dari SELURUH saham (pembanding)."""
    codes = p["code"].unique()
    rows = []
    for code in codes:
        g = p[p["code"] == code]
        if len(g) < 60:
            continue
        df = P.to_ohlcv(g)
        close = df["Close"].astype(float)
        value = A._value_series(df).astype(float)
        v20 = value.rolling(20).mean()
        fr = pd.DataFrame(index=df.index)
        fr["code"] = code
        fr["date"] = df.index
        fr["grade"] = v20.apply(lambda v: A._liquidity_grade(float(v))
                                if pd.notna(v) else None)
        fr["v20"] = v20
        for h in (1, 5, 20):
            fr[f"fwd{h}"] = (close.shift(-h) / close - 1.0) * 100.0
        rows.append(fr)
    return pd.concat(rows, ignore_index=True)


def attach_excess(S: pd.DataFrame, U: pd.DataFrame) -> pd.DataFrame:
    for h in (1, 5, 20):
        uni = U.groupby("date")[f"fwd{h}"].mean()
        cls = U.groupby(["date", "grade"])[f"fwd{h}"].mean()
        S[f"exc{h}"] = S[f"fwd{h}"] - S["date"].map(uni)
        key = pd.MultiIndex.from_arrays([S["date"], S["grade"]])
        S[f"excg{h}"] = S[f"fwd{h}"] - pd.Series(cls.reindex(key).to_numpy(), index=S.index)
    return S


def long_rows(S: pd.DataFrame) -> pd.DataFrame:
    """Ubah ke bentuk panjang: satu baris per (saham, tanggal, kriteria yang lolos)."""
    out = []
    for crit in CRITERIA:
        sub = S[S[crit]].copy()
        sub["criteria"] = crit
        out.append(sub)
    return pd.concat(out, ignore_index=True)


def report_signal_conflict(L: pd.DataFrame, p: pd.DataFrame, sample: int,
                           seed: int = 7) -> None:
    """Pertanyaan pemakaian: kalau SINYAL bilang HOLD tetapi REKOMENDASI bilang
    BELI KUAT, yang mana yang benar?

    Diukur dengan memanggil fungsi sinyal PRODUKSI (`quick_signal`, yang dipakai kolom
    Sinyal di tabel) pada bar saat itu — jadi df dipotong sampai tanggal baris itu,
    bukan memakai bar terakhir hari ini.

    Diambil SAMPEL berstrata per label + rekomendasi, karena satu panggilan sinyal
    memakan ~7 ms dan seluruh baris kandidat ada 290 ribu.
    """
    L = L.copy()
    L["label"] = apply_grade(L)["label"]
    rng = np.random.default_rng(seed)
    picked = []
    for lab in A.PRECLOSE_GRADE_ORDER:
        sub = L[L["label"] == lab]
        if not len(sub):
            continue
        n = min(sample, len(sub))
        picked.append(sub.iloc[rng.choice(len(sub), size=n, replace=False)])
    S = pd.concat(picked, ignore_index=True)
    print(f"\n{'='*104}")
    print(f"SAMPEL untuk pengukuran sinyal: {len(S):,} baris "
          f"({', '.join(f'{k}={v}' for k, v in S['label'].value_counts().items())})")

    sigs, costs = [], []
    by_code = {c: P.to_ohlcv(g) for c, g in p.groupby("code") if len(g) >= 60}
    for row in S.itertuples(index=False):
        df = by_code.get(row.code)
        if df is None:
            sigs.append(None)
            continue
        pos = int(row.pos)
        if pos < 59:
            sigs.append(None)
            continue
        try:
            sigs.append(A.quick_signal(df.iloc[:pos + 1]))
        except Exception:
            sigs.append(None)
    S["signal"] = sigs
    S = S[S["signal"].notna()]
    net5 = S["exc5"] - COST_ROUND_TRIP * 100
    S = S.assign(net5=net5)

    print(f"\n-- A. REKOMENDASI x SINYAL (net 5 hari, kelas likuiditas sama, biaya 0,3% dipotong) --")
    order = [g for g in A.PRECLOSE_GRADE_ORDER if g in set(S["label"])]
    sigs_order = [s for s in ("STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL", "N/A")
                  if s in set(S["signal"])]
    print(f"{'label':<12}" + "".join(f"{s:>15}" for s in sigs_order))
    for lab in order:
        cells = []
        for s in sigs_order:
            g = S[(S["label"] == lab) & (S["signal"] == s)]
            cells.append(f"{g['net5'].mean():+.2f}({len(g)})" if len(g) >= 8 else "—")
        print(f"{lab:<12}" + "".join(f"{c:>15}" for c in cells))

    print(f"\n-- B. KHUSUS kasus yang ditanyakan: pada label BELI KUAT, apakah sinyal HOLD "
          f"lebih buruk dari BUY? --")
    kuat = S[S["label"] == "BELI KUAT"]
    for s in sigs_order:
        g = kuat[kuat["signal"] == s]
        if len(g) >= 8:
            print(f"   sinyal {s:<12} n={len(g):>5} · net5 {g['net5'].mean():+.2f}% "
                  f"· blok t {block_t(g['exc5'], 5):+.2f} · untung {(g['net5'] > 0).mean()*100:.1f}%")
    b = kuat[kuat["signal"].isin(["BUY", "STRONG BUY"])]["net5"]
    h = kuat[kuat["signal"] == "HOLD"]["net5"]
    if len(b) >= 8 and len(h) >= 8:
        print(f"   selisih BUY - HOLD: {b.mean() - h.mean():+.2f} poin persen "
              f"(BUY {b.mean():+.2f}% vs HOLD {h.mean():+.2f}%)")

    print(f"\n-- C. sinyal saja (tanpa melihat label) --")
    for s in sigs_order:
        g = S[S["signal"] == s]
        if len(g) >= 8:
            print(f"   {s:<12} n={len(g):>5} · net5 {g['net5'].mean():+.2f}% "
                  f"· blok t {block_t(g['exc5'], 5):+.2f}")

    print(f"\nCATATAN: sinyal dinilai pada bar yang sama dengan keputusan (bukan memakai "
          f"bar terakhir hari ini), dan sampelnya berstrata per label supaya tiap sel "
          f"terisi. n kecil (<8) tidak dicetak.")


def _fmt_cell(L: pd.DataFrame, col: str) -> str:
    v = L[col].dropna()
    if not len(v):
        return "     —"
    return f"{v.mean():+6.2f}"


def report_grid(L: pd.DataFrame) -> None:
    print(f"\n{'='*104}")
    print("GRID 1 — kriteria x kerapuhan (jarak dari ambang naik 8% pada jalur momentum)")
    print(f"{'='*104}")
    print(f"{'kriteria':<14} {'kerapuhan':<16} {'n':>8} {'n/hari':>7} "
          f"{'exc1':>8} {'exc5':>8} {'blok t5':>8} {'net5%':>7}")
    nd = L["date"].nunique()
    for crit in CRITERIA:
        sub = L[L["criteria"] == crit]
        if crit in ("momentum", "momentumkuat"):
            groups = [("rapuh (8-10%)", sub[sub["day_ret"] < FRAGILE_DAYRET]),
                      ("jauh (>=10%)", sub[sub["day_ret"] >= FRAGILE_DAYRET])]
        else:
            groups = [("(tidak diukur)", sub)]
        for lab, g in groups:
            if not len(g):
                continue
            net5 = g["exc5"].mean() - COST_ROUND_TRIP * 100
            print(f"{crit:<14} {lab:<16} {len(g):>8,} {len(g)/nd:>7.1f} "
                  f"{_fmt_cell(g, 'exc1'):>8} {_fmt_cell(g, 'exc5'):>8} "
                  f"{block_t(g['exc5'], 5):>+8.2f} {net5:>+7.2f}")

    print(f"\n{'='*104}")
    print("GRID 2 — kriteria x skor beli 0-100 (kolom '🎯 Beli' di aplikasi)")
    print(f"{'='*104}")
    print(f"{'kriteria':<14} {'skor':<12} {'n':>8} {'n/hari':>7} "
          f"{'exc1':>8} {'exc5':>8} {'blok t5':>8} {'net5%':>7}")
    for crit in CRITERIA:
        sub = L[L["criteria"] == crit]
        for bucket in ("skor>=70", "skor50-69", "skor<50"):
            g = sub[sub["score"].apply(_score_bucket) == bucket]
            if not len(g):
                continue
            net5 = g["exc5"].mean() - COST_ROUND_TRIP * 100
            print(f"{crit:<14} {bucket:<12} {len(g):>8,} {len(g)/nd:>7.1f} "
                  f"{_fmt_cell(g, 'exc1'):>8} {_fmt_cell(g, 'exc5'):>8} "
                  f"{block_t(g['exc5'], 5):>+8.2f} {net5:>+7.2f}")


def apply_grade(L: pd.DataFrame) -> pd.DataFrame:
    """Jalankan fungsi rekomendasi PRODUKSI atas setiap baris panel."""
    recs = []
    for row in L.itertuples(index=False):
        crit = row.criteria
        fragile = None
        if crit in ("momentum", "momentumkuat"):
            fragile = bool(row.day_ret < FRAGILE_DAYRET)
        rec = A.preclose_grade(crit, buy_score=float(row.score),
                               day_return_pct=float(row.day_ret), fragile=fragile)
        recs.append(rec["grade"])
    L = L.copy()
    L["label"] = recs
    return L


def report_grade(L: pd.DataFrame) -> None:
    print(f"\n{'='*104}")
    print("HASIL — rekomendasi produksi (api/index.py `preclose_grade`) diukur, bukan diasumsikan")
    print(f"{'='*104}")
    print(f"{'label':<12} {'n':>8} {'n/hari':>7} {'exc1':>7} {'exc5':>7} {'exc20':>7} "
          f"{'blok t5':>8} {'net5%':>7} {'%untung net5':>13} {'paruh awal/akhir (exc5)':>24}")
    nd = L["date"].nunique()
    order = [g for g in A.PRECLOSE_GRADE_ORDER if g in set(L["label"])]
    for lab in order:
        g = L[L["label"] == lab]
        if not len(g):
            continue
        net5 = g["exc5"] - COST_ROUND_TRIP * 100
        h1, h2 = holdout(g["exc5"], 5)
        print(f"{lab:<12} {len(g):>8,} {len(g)/nd:>7.1f} {_fmt_cell(g, 'exc1'):>7} "
              f"{_fmt_cell(g, 'exc5'):>7} {_fmt_cell(g, 'exc20'):>7} "
              f"{block_t(g['exc5'], 5):>+8.2f} {net5.mean():>+7.2f} "
              f"{(net5 > 0).mean()*100:>12.1f}% "
              f"{h1:>+11.2f} /{h2:>+9.2f}")

    print(f"\n-- pemisahan antar label (harus MENURUN dari BELI KUAT ke TUNGGU) --")
    seq = [(lab, L[L["label"] == lab]["exc5"].mean() - COST_ROUND_TRIP * 100) for lab in order]
    for lab, v in seq:
        print(f"  {lab:<12} net5 {v:+.2f}%")
    mono = all(seq[i][1] >= seq[i + 1][1] for i in range(len(seq) - 1))
    print(f"  urut menurun: {'YA' if mono else 'TIDAK'}")

    print(f"\n-- per kriteria (label x kriteria, net5%) --")
    for crit in CRITERIA:
        sub = L[L["criteria"] == crit]
        parts = []
        for lab in order:
            g = sub[sub["label"] == lab]
            if len(g):
                parts.append(f"{lab} {g['exc5'].mean() - COST_ROUND_TRIP*100:+.2f}% (n={len(g):,})")
        print(f"  {crit:<14} " + " · ".join(parts))

    print(f"\nCATATAN PEMBACAAN")
    print("  * exc5 = return 5 hari MINUS rata-rata saham KELAS LIKUIDITAS SAMA di")
    print("    tanggal yang sama; net5 = exc5 dipotong biaya 0,3% round-trip.")
    print("  * Harga masuk = harga tutup hari sinyal. Itu memang harga yang dibayar di")
    print("    mode pra-tutup (karena itu mode ini ada), dan TIDAK bisa dibayar bila")
    print("    pemindaian dilakukan setelah bursa tutup.")
    print("  * Panel masih punya survivorship bias (emiten delisting tidak ada di cache),")
    print("    jadi semua angka cenderung terlalu optimistis. Ini pembanding antar label,")
    print("    bukan janji imbal hasil.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=["grid", "grade", "signal", "both"], default="both")
    ap.add_argument("--years", type=float, default=None)
    ap.add_argument("--sample", type=int, default=1500,
                    help="baris per label yang dipakai bagian 'signal' (1 panggilan sinyal each)")
    args = ap.parse_args()

    p = P.load_panel()
    if args.years:
        cutoff = p["date"].max() - pd.Timedelta(days=int(365.25 * args.years))
        p = p[p["date"] >= cutoff]
    print(f"panel: {p['code'].nunique():,} emiten · {len(p):,} saham-hari · "
          f"{p['date'].nunique():,} tanggal · {p['date'].min().date()} s/d {p['date'].max().date()}")

    print("\nmembangun kandidat + pembanding...")
    S = build_rows(p)
    U = universe_means(p)
    S = attach_excess(S, U)
    L = long_rows(S)
    print(f"baris kandidat: {len(L):,} (dari {S['code'].nunique():,} emiten)")

    if args.part in ("grid", "both"):
        report_grid(L)
    if args.part in ("grade", "both"):
        report_grade(apply_grade(L))
    if args.part in ("signal", "both"):
        report_signal_conflict(L, p, args.sample)


if __name__ == "__main__":
    main()
