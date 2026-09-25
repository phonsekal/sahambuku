#!/usr/bin/env python3
"""Uji POLA CHART KLASIK (Edianto Ong, Technical Analysis for Mega Profit, Bab 20-21)
pada panel harian IDX lokal — 0 kuota, 0 jaringan.

Pertanyaan yang dijawab
-----------------------
Buku itu memuat daftar pola yang panjang: Head & Shoulders, Double/Triple Top-Bottom,
Triangle (ascending/descending/symmetrical), Pennant, Flag, Wedge, Rectangle, Cup &
Handle, lalu Gaps (common, break-away, run-away, exhaustion). Selama ini aplikasi hanya
memakai Launch Pad dan Volume S&R (versi Coachinvestasi) serta breakout 20 hari.
Mana di antara pola itu yang BENAR-BENAR menambah alpha di IDX, dan mana yang cuma
terlihat bagus kalau digambar tangan?

Kenapa harus diukur, bukan dikutip
----------------------------------
Pola visual tidak bisa "dibaca" dari ringkasan harian tanpa gambar, jadi tiap pola di
sini diterjemahkan menjadi ATURAN GEOMETRIS yang bisa dihitung: trough/puncak di
sepertiga jendela, ambang kemiringan, dan PENEMBUSAN GARIS sebagai pemicu (buku:
"penembusan sah = harga PENUTUPAN di luar garis", hal 57). Jadi yang diukur adalah
"apakah inti geometris polanya terukur", BUKAN "apakah polanya persis sama seperti
yang dilihat mata di grafik". Perbedaan itu disebut di setiap baris laporan.

Jalankan:
    .venv/bin/python research/chart_pattern_study.py
    .venv/bin/python research/chart_pattern_study.py --limit 250   # cepat
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

import panel as P                      # noqa: E402
import index as A                      # noqa: E402  <- kode PRODUKSI, bukan replika
import criteria_audit as CA            # noqa: E402  <- metrik & pembanding yang sama

COST = CA.COST_ROUND_TRIP              # 0,3% putar dua arah, sama dengan /api/backtest


# ---------------------------------------------------------------------------
# 1. ALAT GEOMETRI
# ---------------------------------------------------------------------------

def thirds(df: pd.DataFrame, w: int) -> Dict[str, pd.Series]:
    """Puncak & dasar tiap sepertiga jendela 3w bar (paling kanan = sekarang).

    Inilah pengganti "mata": pola chart pada dasarnya perbandingan puncak/dasar pada
    beberapa bagian grafik. Jendela dibagi tiga supaya pola dengan DUA titik (double
    top/bottom) dan TIGA titik (head & shoulders) bisa dibaca dari kerangka yang sama.
    """
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    return {
        # third 1 = paling lama, third 3 = paling baru
        "h1": high.shift(2 * w).rolling(w).max(),
        "h2": high.shift(w).rolling(w).max(),
        "h3": high.rolling(w).max(),
        "l1": low.shift(2 * w).rolling(w).min(),
        "l2": low.shift(w).rolling(w).min(),
        "l3": low.rolling(w).min(),
    }


def halves(df: pd.DataFrame, w: int) -> Dict[str, pd.Series]:
    """Puncak & dasar dua paruh jendela 2w bar — dipakai pola yang hanya butuh 2 titik."""
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    return {"h1": high.shift(w).rolling(w).max(), "h2": high.rolling(w).max(),
            "l1": low.shift(w).rolling(w).min(), "l2": low.rolling(w).min()}


def _line_ok(level: pd.Series) -> pd.Series:
    """Garis yang dipakai sebagai pemicu TIDAK boleh memuat bar sinyalnya sendiri.

    Kenapa ini ditegaskan: puncak/dasar jendela yang berakhir di bar SEKARANG selalu
    memuat bar itu, sehingga "close menembus puncak jendela" menjadi mustahil secara
    matematis (close <= high, dan close >= low). Tanpa penggeseran ini, empat pola
    (segitiga, wedge, rectangle, dan H&S versi atas) melaporkan NOL kejadian — bukan
    karena polanya tidak ada, tetapi karena syaratnya tidak mungkin terpenuhi. Itu
    jenis kesalahan yang tampak seperti "pola langka" kalau tidak diperiksa.
    """
    return level.shift(1)


def cross_up(close: pd.Series, level: pd.Series) -> pd.Series:
    """True HANYA pada bar penembusan (close melewati level), bukan selama di atasnya.

    Ini penting: kalau sinyalnya berupa KEADAAN ("harga di atas garis"), satu pola akan
    terhitung berkali-kali dan angkanya jadi tidak bisa dibandingkan dengan sinyal
    peristiwa lain. Buku juga menyebut penembusan yang sah sebagai peristiwa, bukan
    keadaan (hal 57).
    """
    return ((close > level) & (close.shift(1) <= level)).fillna(False)


def cross_dn(close: pd.Series, level: pd.Series) -> pd.Series:
    return ((close < level) & (close.shift(1) >= level)).fillna(False)


def _rel(a: pd.Series, b: pd.Series) -> pd.Series:
    """Selisih relatif a terhadap b (dalam persen), aman terhadap nol."""
    den = b.abs().replace(0, np.nan)
    return ((a - b) / den) * 100.0


# ---------------------------------------------------------------------------
# 2. DETEKTOR POLA = KODE PRODUKSI, BUKAN SALINAN
#
# Seluruh aturan pola ada di `api/index.py` (`chart_pattern_components`), jadi angka
# yang diukur di sini adalah angka yang benar-benar dijalankan aplikasi. Kalau suatu
# saat aturannya diubah di produksi, laporan ini ikut berubah — dan itu memang yang
# diinginkan: tidak ada dua definisi yang bisa berbeda diam-diam.
# ---------------------------------------------------------------------------
from_api = A.chart_pattern_components          # noqa: E402

# Daftar untuk LAPORAN sengaja memuat pola yang DITOLAK juga (rectangle, gap): laporan
# harus menunjukkan apa yang tidak lolos, bukan hanya yang lolos.
PATTERNS_UP = ("double_bottom", "inverse_head_shoulders", "ascending_triangle",
               "symmetrical_triangle", "falling_wedge", "rectangle_up", "flag",
               "cup_handle", "gap_up")
PATTERNS_DOWN = ("double_top", "head_shoulders", "descending_triangle",
                 "rising_wedge", "rectangle_dn")
PATTERN_LABELS = A.CHART_PATTERN_LABELS


def all_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """Semua detektor pola untuk satu emiten (delegasi ke produksi)."""
    return from_api(df)


# ---------------------------------------------------------------------------
# 3. KUMPULKAN SATU BARIS PER (SAHAM, TANGGAL)
# ---------------------------------------------------------------------------

def build(limit: int | None = None, verbose: bool = True) -> pd.DataFrame:
    p = P.load_panel()
    codes = sorted(p["code"].unique())
    if limit:
        codes = codes[:limit]
    rows: List[pd.DataFrame] = []
    for i, code in enumerate(codes):
        g = p[p["code"] == code]
        if len(g) < 260:
            continue
        df = P.to_ohlcv(g)
        close = df["Close"].astype(float)
        value = A._value_series(df).astype(float)
        v20 = value.rolling(20).mean()

        f = pd.DataFrame(index=df.index)
        f["code"] = code
        f["date"] = df.index
        f["v20"] = v20.to_numpy()
        f["grade"] = v20.apply(lambda v: CA._liquidity_class(float(v)) if pd.notna(v) else None).to_numpy()
        f["day_ret"] = ((close / close.shift(1) - 1.0) * 100.0).to_numpy()
        f["mom"] = ((f["day_ret"] >= 8.0) & (value >= 100e6)).fillna(False).to_numpy()
        # Pembanding BEBAS pola, supaya "apakah polanya menambah" bisa dijawab:
        # breakout 20 hari adalah pembanding paling wajar untuk pola bersifat breakout.
        f["brk20"] = A.breakout_20_series(df).to_numpy(bool)
        f["momkuat"] = (f["mom"] & f["brk20"]).to_numpy(bool)
        f["lpad"] = A.launch_pad_series(df).to_numpy(bool)
        f = f.join(all_patterns(df))

        for h in CA.ALL_HORIZONS:
            f[f"fwd{h}"] = (close.shift(-h) / close - 1.0).to_numpy() * 100.0
        # Kolom eksekusi di jalur BUKA (dipakai CA.add_excess dan bagian D). Penjaganya
        # SAMA dengan criteria_audit.build_signal_frame: kolom Open ringkasan IDX berisi
        # 0 untuk "tidak tercatat", dan 0 itu bukan harga — memakainya apa adanya
        # menghasilkan return -100% yang palsu.
        op = df["Open"].astype(float)
        nxt_ok = (op.shift(-1) > 0) & (op.shift(-1) < close * 5)
        nxt = op.shift(-1).where(nxt_ok)
        f["fwd1_open"] = ((nxt / close - 1.0) * 100.0).to_numpy()
        f["open_next_ok"] = nxt_ok.fillna(False).to_numpy(bool)
        f["gap1"] = ((nxt / close - 1.0) * 100.0).to_numpy()
        for k in (1, 2, 3, 5):
            f[f"oc{k}"] = ((close.shift(-k) / nxt - 1.0) * 100.0).to_numpy()
            f[f"cc{k}"] = ((close.shift(-(k + 1)) / close.shift(-1) - 1.0) * 100.0).to_numpy()
        rows.append(f)
        if verbose and (i + 1) % 100 == 0:
            print(f"  ... {i + 1}/{len(codes)} emiten")
    S = pd.concat(rows, ignore_index=True)
    S = CA.add_excess(S)
    return S


# ---------------------------------------------------------------------------
# 4. LAPORAN
# ---------------------------------------------------------------------------
def row_stats(S: pd.DataFrame, mask: pd.Series, h: int = 5, min_n: int = 30) -> Dict:
    mask = mask.fillna(False)
    n = int(mask.sum())
    if n < min_n:
        return {"n": n, "enough": False}
    per = S.loc[mask].groupby("date")[f"excg{h}"].mean()
    ha, hb = CA.holdout(per, h)
    net5 = S.loc[mask, "fwd5"].mean() - COST * 100
    win = (S.loc[mask, "fwd5"] > COST * 100).mean() * 100
    return {
        "n": n, "enough": True,
        "per_day": n / max(1, S["date"].nunique()),
        "alpha5": float(per.mean()), "t": CA.block_t(per, h),
        "early": ha, "late": hb,
        "net5": float(net5), "win5": float(win),
        "net1": float(S.loc[mask, "fwd1"].mean() - COST * 100),
        "net20": float(S.loc[mask, "fwd20"].mean() - COST * 100),
        "years_pos": int(sum(
            1 for y in sorted(S["year"].unique())
            if (int((mask & (S["year"] == y)).sum()) >= 20
                and S.loc[mask & (S["year"] == y)].groupby("date")[f"excg{h}"].mean().mean() > 0))),
        "years": int(sum(1 for y in sorted(S["year"].unique())
                         if int((mask & (S["year"] == y)).sum()) >= 20)),
    }


def print_table(S: pd.DataFrame, names, baseline: Dict) -> None:
    print(f"  {'pola':<28} {'n':>7} {'/hari':>6} {'alpha5%':>8} {'blok t':>7} "
          f"{'paruh awal/akhir':>18} {'net5%':>7} {'%untung':>8} {'thn+':>7}")
    for name in names:
        r = row_stats(S, S[name])
        if not r["enough"]:
            print(f"  {PATTERN_LABELS.get(name, name):<28} {r['n']:>7,}  (sampel < 30)")
            continue
        pos = f"{r['years_pos']}/{r['years']}"
        print(f"  {PATTERN_LABELS.get(name, name):<28} {r['n']:>7,} {r['per_day']:>6.1f} "
              f"{r['alpha5']:>+8.2f} {r['t']:>+7.2f} "
              f"{r['early']:>+8.2f}/{r['late']:<+8.2f} {r['net5']:>+7.2f} "
              f"{r['win5']:>7.1f}% {pos:>7}")
    # Pembanding: bukan pola, tetapi syarat breakout yang sudah dipakai produksi.
    for label, key in (("PEMBANDING breakout 20 hari", "brk20"),
                       ("PEMBANDING momentum+breakout", "momkuat"),
                       ("PEMBANDING Launch Pad (sudah dipakai)", "lpad")):
        r = baseline.get(key)
        if not r or not r["enough"]:
            continue
        print(f"  {label:<28} {r['n']:>7,} {r['per_day']:>6.1f} {r['alpha5']:>+8.2f} "
              f"{r['t']:>+7.2f} {r['early']:>+8.2f}/{r['late']:<+8.2f} {r['net5']:>+7.2f} "
              f"{r['win5']:>7.1f}% {r['years_pos']:>3}/{r['years']:<3}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    print("Membangun panel uji pola chart ...")
    S = build(limit=args.limit)
    S["year"] = pd.to_datetime(S["date"]).dt.year
    nd = S["date"].nunique()
    print(f"\n{len(S):,} saham-hari · {S['code'].nunique()} emiten · {nd} tanggal · "
          f"{S['date'].min().date()} -> {S['date'].max().date()}")

    # Pembanding dihitung SEKALI, dipakai di semua tabel.
    baseline = {k: row_stats(S, S[k]) for k in ("brk20", "momkuat", "lpad")}

    print(f"\n{'='*124}\nA. POLA BULLISH — apakah pemicunya menambah alpha 5 hari "
          f"(vs kelas likuiditas yang sama)?\n{'='*124}")
    print(f"  Cakupan tiap pola (bagian saham-hari):")
    for name in PATTERNS_UP:
        print(f"    {PATTERN_LABELS[name]:<28} {S[name].mean()*100:8.3f}%  ({int(S[name].sum()):,} baris)")
    print()
    print_table(S, PATTERNS_UP, baseline)

    print(f"\n{'='*124}\nB. POLA BEARISH — dipakai sebagai tanda MENGHINDAR: return harus NEGATIF "
          f"supaya berguna\n{'='*124}")
    print(f"  Cakupan tiap pola (bagian saham-hari):")
    for name in PATTERNS_DOWN:
        print(f"    {PATTERN_LABELS[name]:<28} {S[name].mean()*100:8.3f}%  ({int(S[name].sum()):,} baris)")
    print()
    print_table(S, PATTERNS_DOWN, baseline)

    # --- C. Yang penting: apakah polanya menambah SETELAH digabung syarat lain --------
    print(f"\n{'='*124}\nC. APAKAH POLANYA MENAMBAH? pola x momentum+breakout vs momentum+breakout "
          f"sendirian\n{'='*124}")
    base = S["momkuat"]
    rb = row_stats(S, base)
    print(f"  {'pilihan':<36} {'n':>7} {'/hari':>6} {'alpha5%':>8} {'blok t':>7} {'net5%':>7} {'%untung':>8}")
    if rb["enough"]:
        print(f"  {'momentum+breakout (dasar)':<36} {rb['n']:>7,} {rb['per_day']:>6.1f} "
              f"{rb['alpha5']:>+8.2f} {rb['t']:>+7.2f} {rb['net5']:>+7.2f} {rb['win5']:>7.1f}%")
    for name in PATTERNS_UP + PATTERNS_DOWN:
        r = row_stats(S, base & S[name])
        if not r["enough"]:
            print(f"  {'dasar + ' + PATTERN_LABELS[name]:<36} {r['n']:>7,}  (sampel < 30)")
            continue
        flag = "  <- lebih baik" if rb["enough"] and r["alpha5"] > rb["alpha5"] else ""
        print(f"  {'dasar + ' + PATTERN_LABELS[name]:<36} {r['n']:>7,} {r['per_day']:>6.1f} "
              f"{r['alpha5']:>+8.2f} {r['t']:>+7.2f} {r['net5']:>+7.2f} {r['win5']:>7.1f}%{flag}")

    # --- D. Masuk besok (bisa dieksekusi) atau masuk di tutup (tidak bisa) -----------
    print(f"\n{'='*124}\nD. HARGA MASUK: tutup hari sinyal (tidak bisa dibeli setelah bursa tutup) vs "
          f"celah buka besok\n{'='*124}")
    print("  Diukur supaya tidak ada klaim alpha yang tidak bisa dieksekusi. Kolom Open")
    print("  ringkasan IDX kosong untuk banyak baris, jadi hanya baris dengan open wajar.")
    for name in PATTERNS_UP:
        r = row_stats(S, S[name])
        if not r["enough"]:
            continue
        mask = S[name].fillna(False) & S["open_next_ok"].fillna(False)
        if int(mask.sum()) < 30:
            continue
        gap = S.loc[mask, "gap1"].mean()
        cc1 = S.loc[mask, "cc1"].mean()
        print(f"  {PATTERN_LABELS[name]:<28} n={int(mask.sum()):>6,}  celah buka (gap1) "
              f"{gap:>+6.2f}%  masuk di buka lalu tutup H+1 (cc1) {cc1:>+6.2f}%")

    print(f"\n{'='*124}\nE. RINGKASAN\n{'='*124}")
    print("  Pola dianggap LAYAK bila: alpha5 positif dengan blok t >= +2, positif di")
    print("  KEDUA paruh waktu, dan tetap positif setelah biaya 0,3%.")
    for name in PATTERNS_UP + PATTERNS_DOWN:
        r = row_stats(S, S[name])
        if not r["enough"]:
            continue
        layak = (r["alpha5"] > 0 and r["t"] >= 2 and r["early"] > 0 and r["late"] > 0
                 and r["net5"] > 0)
        print(f"  {'LAYAK' if layak else 'tidak':<6} {PATTERN_LABELS[name]:<28} "
              f"alpha5 {r['alpha5']:>+7.2f}%  t {r['t']:>+6.2f}  "
              f"paruh {r['early']:>+6.2f}/{r['late']:<+6.2f}  net5 {r['net5']:>+6.2f}%")
    print("\nCatatan: pengukuran ini memakai GEOMETRI (puncak/dasar per sepertiga jendela +")
    print("penembusan harga penutupan), bukan pengenalan gambar. Jadi hasilnya menjawab")
    print("\"apakah inti aturannya terukur\", bukan \"apakah polanya identik dengan yang terlihat\".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
