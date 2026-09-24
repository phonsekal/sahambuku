#!/usr/bin/env python3
"""Uji aturan buku (folder `buku tambahan`) di panel harian IDX lokal — 0 kuota.

Pertanyaan yang dijawab
-----------------------
Buku baru berisi banyak aturan yang bisa diterjemahkan jadi kode. Mana yang benar-benar
menambah nilai pada kriteria yang sudah terukur di aplikasi ini (`research/criteria_audit.py`),
dan mana yang cuma terdengar masuk akal?

Yang diuji, beserta halaman sumbernya:

  A. TREND TEMPLATE (Minervini, Think & Trade Like a Champion, hal 105-106)
     Delapan syarat Stage-2. Diuji sebagai GERBANG pada entri momentum/breakout yang
     sudah ada — bukan sebagai kriteria baru yang berdiri sendiri.
  B. VCP (volatilitas mengerut; Minervini hal 109-118)
     Detektor pendekatan: kontraksi makin dangkal + volume mengering + harga rapat ke
     puncak basis. Diuji sebagai gerbang pada breakout.
  C. KARAKTER VOLUME-HARGA (Ilmu Saham Biawak VVIP, hal 257)
     "Harga naik volume naik = kuat" vs "harga naik volume turun = lemah".
  D. ATURAN KELUAR dalam satuan R (Turtle Trader, hal 269-280)
     Turtle: stop 2N, keluar saat menembus low 10-hari, tanpa target.
     Aplikasi sekarang: stop 1,5N, target 2R tetap, batas 5 hari.
     Ini yang paling penting: hasil akhir ditentukan aturan keluar, bukan aturan masuk
     (audit sebelumnya: kejadian untung setelah biaya selalu di bawah 50%).

Kenapa dijalankan di panel lokal
--------------------------------
`research/panel.py` = ringkasan harian IDX resmi (2020-2026, ~989 emiten, ~1,36 juta
saham-hari). Jadi aturan buku bisa diuji TANPA memakai satu pun permintaan API, dan
pembandingnya sama dengan audit kriteria yang sudah ada.

Jalankan:
    .venv/bin/python research/book_rules_study.py
    .venv/bin/python research/book_rules_study.py --limit 250   # cepat, sampel 250 emiten
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "api"))

import panel as P                      # noqa: E402
import index as A                      # noqa: E402  <- kode PRODUKSI, bukan replika
import criteria_audit as CA            # noqa: E402  <- metrik & pembanding yang sama

COST = CA.COST_ROUND_TRIP              # 0,3% round-trip, sama dengan /api/backtest


# ---------------------------------------------------------------------------
# 1. ATURAN BUKU SEBAGAI DERET VEKTOR
# ---------------------------------------------------------------------------

def trend_template_parts(df: pd.DataFrame) -> pd.DataFrame:
    """Delapan syarat Trend Template Minervini (hal 105-106) sebagai kolom boolean.

    Syarat 7 (peringkat RS >= 70) butuh peringkat LINTAS SAHAM pada tanggal yang sama,
    jadi dihitung di `add_rs_rank()` setelah semua emiten terkumpul. Di sini hanya
    komponen harga yang dihitung.
    """
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    s50, s150, s200 = (A.sma(close, n) for n in (50, 150, 200))

    out = pd.DataFrame(index=df.index)
    out["c1_above_150_200"] = ((close > s150) & (close > s200)).fillna(False)
    out["c2_150_above_200"] = (s150 > s200).fillna(False)
    out["c3_200_rising"] = (s200 > s200.shift(22)).fillna(False)      # naik >= 1 bulan
    out["c4_50_above_all"] = ((s50 > s150) & (s50 > s200)).fillna(False)
    out["c5_25pct_off_low"] = (close >= 1.25 * low.rolling(252, min_periods=120).min()).fillna(False)
    out["c6_within_25pct_high"] = (close >= 0.75 * high.rolling(252, min_periods=120).max()).fillna(False)
    out["c8_above_50"] = (close > s50).fillna(False)
    # Bahan untuk syarat 7: kinerja 12 bulan, dinilai peringkatnya lintas emiten.
    out["ret252"] = (close / close.shift(252) - 1.0) * 100.0
    return out


def add_rs_rank(S: pd.DataFrame) -> pd.DataFrame:
    """Syarat 7: peringkat RS 0-100 (persentil kinerja 12 bulan pada tanggal yang sama).

    Buku memakai peringkat RS dari IBD; di sini dibangun sendiri dari data yang ada,
    dengan definisi yang sama: posisi kinerja 12 bulan sebuah saham terhadap SELURUH
    saham lain pada tanggal itu. Karena itu angkanya bisa dibandingkan antar tanggal.
    """
    S["rs_rank"] = S.groupby("date")["ret252"].rank(pct=True) * 100.0
    S["c7_rs70"] = (S["rs_rank"] >= 70).fillna(False)
    cols = ["c1_above_150_200", "c2_150_above_200", "c3_200_rising", "c4_50_above_all",
            "c5_25pct_off_low", "c6_within_25pct_high", "c7_rs70", "c8_above_50"]
    S["trend_template"] = S[cols].all(axis=1)
    S["tt_count"] = S[cols].sum(axis=1)
    return S


def vcp_series(df: pd.DataFrame, window: int = 60, tight: float = 0.08) -> pd.Series:
    """Pendekatan VCP Minervini (hal 109-118): basis mengerut, volume mengering, harga rapat.

    Tiga hal yang diminta buku, diterjemahkan sejujur mungkin ke data harian:

      1. ADA basis: penurunan terdalam dalam jendela >= 10% (bukan saham yang cuma naik rata).
      2. MENGERUT: penurunan terdalam paruh KEDUA jendela lebih dangkal daripada paruh
         pertama (<= 0,75x). Buku: "setiap kontraksi berikutnya kira-kira setengah
         kontraksi sebelumnya" — ambang 0,75 sengaja lebih longgar supaya pola asli
         tidak dibuang hanya karena tidak persis setengah.
      3. VOLUME MENGERING + HARGA RAPAT: rata-rata volume 10 bar terakhir < 0,8x rata-rata
         jendela, dan lebar rentang 10 bar terakhir <= 8% (buku: pivot terjadi pada
         bagian paling rapat dengan volume di bawah rata-rata 50 hari).

    Catatan kejujuran: ini BUKAN pola visual seperti di buku (tidak ada penghitungan
    kontraksi 3T/4T), jadi hasilnya harus dibaca sebagai "apakah inti idenya terukur",
    bukan "apakah pola gambar persisnya terbaca". Tidak ada gambar di panel.
    """
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    vol = df["Volume"].astype(float)
    half = window // 2

    def depth(lo: pd.Series, hi: pd.Series) -> pd.Series:
        return (1.0 - lo / hi.replace(0, np.nan)) * 100.0

    d1 = depth(low.rolling(half).min(), high.rolling(half).max())
    d1_prev = d1.shift(half)                       # paruh pertama jendela
    have_base = (d1_prev >= 10.0).fillna(False)
    contracting = ((d1 <= 0.75 * d1_prev) & (d1_prev > 0)).fillna(False)

    v10 = vol.rolling(10).mean()
    vw = vol.rolling(window).mean()
    vol_dry = (v10 <= 0.8 * vw.replace(0, np.nan)).fillna(False)

    rng10 = (high.rolling(10).max() - low.rolling(10).min()) / close.replace(0, np.nan)
    tight_now = (rng10 <= tight).fillna(False)
    near_high = (close >= 0.95 * high.rolling(window).max()).fillna(False)

    return (have_base & contracting & vol_dry & tight_now & near_high).fillna(False)


def volume_price_label(df: pd.DataFrame, look: int = 5) -> pd.DataFrame:
    """Karakter volume-harga Biawak hal 257 (dua baris yang relevan untuk entri beli).

    Buku: harga naik + volume naik = "kenaikan cukup kuat"; harga naik + volume turun =
    "kenaikan lemah". Volume diukur terhadap rata-rata 20 bar.
    """
    close = df["Close"].astype(float)
    vol = df["Volume"].astype(float)
    ret = close.pct_change(look) * 100.0
    vr = vol / A.sma(vol, 20).replace(0, np.nan)
    up = (ret > 0).fillna(False)
    out = pd.DataFrame(index=df.index)
    out["vp_strong"] = (up & (vr >= 1.0)).fillna(False)     # naik + volume naik
    out["vp_weak"] = (up & (vr < 1.0)).fillna(False)        # naik + volume turun
    return out


# ---------------------------------------------------------------------------
# 2. SIMULASI ATURAN KELUAR DALAM SATUAN R
# ---------------------------------------------------------------------------

def simulate_exit(df: pd.DataFrame, mask: pd.Series, stop_n: float, target_n: float | None,
                  max_hold: int, trail_low: int | None = None) -> pd.Series:
    """Hasil per perdagangan dalam satuan R (1R = jarak masuk..stop), bersih biaya.

    Aturan yang dipakai persis seperti yang bisa dijalankan:
      * risiko  = stop_n x ATR14 pada bar sinyal. 1R = risiko itu.
      * kalau LOW menyentuh stop  -> keluar di stop (-1R).
      * kalau HIGH menyentuh target (bila target_n diisi) -> keluar di target (+target_n/stop_n R).
        Bila keduanya tersentuh di bar yang SAMA, diasumsikan STOP lebih dulu (pesimis,
        karena urutan intraday tidak diketahui dari ringkasan harian).
      * trail_low (aturan Turtle): keluar bila harga menembus LOW terendah N bar sebelumnya,
        keluar di level itu (atau di close bila sudah lebih rendah).
      * max_hold: keluar di close bar ke-max_hold (batas waktu).

    Kenapa R dan bukan persen: ukuran posisi ditentukan risiko, jadi R adalah satuan yang
    membuat aturan masuk berbeda-beda bisa dibandingkan setara (Tharp, dan dipakai Turtle
    hal 62: ekspektasi = rata-rata untung / rata-rata risiko).
    """
    close = df["Close"].astype(float).to_numpy()
    high = df["High"].astype(float).to_numpy()
    low = df["Low"].astype(float).to_numpy()
    atr = A.atr(df, 14).to_numpy()
    n = len(df)
    out = np.full(n, np.nan)

    idx = np.flatnonzero(mask.to_numpy(bool))
    for i in idx:
        if i + 1 >= n or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        entry = close[i]
        risk = stop_n * atr[i]
        if risk <= 0 or entry <= 0:
            continue
        stop = entry - risk
        target = entry + target_n * atr[i] if target_n else None
        cost_r = COST * entry / risk        # biaya putar dua arah, diubah ke R
        res = None
        last = min(i + max_hold, n - 1)
        for j in range(i + 1, last + 1):
            if low[j] <= stop:
                res = -1.0
                break
            if target is not None and high[j] >= target:
                res = (target_n * atr[i]) / risk
                break
            if trail_low:
                k = max(0, j - trail_low)
                lvl = low[k:j].min() if j > k else None
                if lvl is not None and low[j] <= lvl:
                    px = min(close[j], lvl)
                    res = (px - entry) / risk
                    break
        if res is None:
            res = (close[last] - entry) / risk
        out[i] = res - cost_r
    return pd.Series(out, index=df.index)


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
        if len(g) < 260:          # butuh 252 bar untuk RS & 52 minggu
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
        f["brk"] = A.breakout_20_series(df).to_numpy(bool)
        f["lpad"] = A.launch_pad_series(df).to_numpy(bool)
        f["momkuat"] = (f["mom"] & f["brk"]).to_numpy(bool)

        tt = trend_template_parts(df)
        f = f.join(tt)
        f["vcp"] = vcp_series(df).to_numpy(bool)
        f = f.join(volume_price_label(df))

        # Semua horizon yang dipakai bersama audit kriteria (1,2,3,5,20) supaya
        # `criteria_audit.add_excess()` bisa dipakai apa adanya — pembandingnya
        # harus sama dengan audit yang sudah ada, bukan pembanding baru.
        for h in CA.ALL_HORIZONS:
            f[f"fwd{h}"] = (close.shift(-h) / close - 1.0).to_numpy() * 100.0
        # Kolom Open dari ringkasan IDX KOSONG (=0) untuk banyak baris sebelum 2025;
        # penjaganya sama dengan `criteria_audit.build_signal_frame` supaya angka 0
        # tidak terbaca sebagai harga (yang akan menjadi return -100%).
        op = df["Open"].astype(float)
        nxt = op.shift(-1).where((op.shift(-1) > 0) & (op.shift(-1) < close * 5))
        f["fwd1_open"] = ((nxt / close - 1.0) * 100.0).to_numpy()

        # Aturan keluar (Turtle hal 269-280 vs rencana aplikasi sekarang)
        mk = pd.Series(f["momkuat"].to_numpy(), index=df.index)
        lp = pd.Series(f["lpad"].to_numpy(), index=df.index)
        f["R_now"] = simulate_exit(df, mk, 1.5, 3.0, 5).to_numpy()
        f["R_2N_tp"] = simulate_exit(df, mk, 2.0, 4.0, 5).to_numpy()
        f["R_turtle10"] = simulate_exit(df, mk, 2.0, None, 20, trail_low=10).to_numpy()
        f["R_turtle10_15"] = simulate_exit(df, mk, 1.5, None, 20, trail_low=10).to_numpy()
        f["R_2N_20d"] = simulate_exit(df, mk, 2.0, None, 20).to_numpy()
        f["Rlp_now"] = simulate_exit(df, lp, 1.5, 3.0, 5).to_numpy()
        f["Rlp_turtle10"] = simulate_exit(df, lp, 2.0, None, 20, trail_low=10).to_numpy()

        rows.append(f)
        if verbose and (i + 1) % 100 == 0:
            print(f"  ... {i + 1}/{len(codes)} emiten")
    S = pd.concat(rows, ignore_index=True)
    S = add_rs_rank(S)
    S = CA.add_excess(S)
    return S


# ---------------------------------------------------------------------------
# 4. LAPORAN
# ---------------------------------------------------------------------------

def gate_report(S: pd.DataFrame, base: pd.Series, gate: pd.Series, name: str,
                h: int = 5, cost: bool = True) -> dict:
    """Bandingkan entri dasar vs entri yang sudah disaring gerbang buku.

    Pembandingnya KELAS LIKUIDITAS yang sama pada tanggal yang sama (kolom excg{h}),
    sama dengan audit kriteria yang sudah ada — supaya angka di sini sebanding.
    """
    col = f"excg{h}"
    n_dates = S["date"].nunique()
    m0, m1 = base.fillna(False), (base & gate).fillna(False)
    out = {"nama": name, "n": int(m0.sum()), "n_gate": int(m1.sum()),
           "per_hari": int(m0.sum()) / n_dates, "per_hari_gate": int(m1.sum()) / n_dates}
    for label, m in (("dasar", m0), ("gerbang", m1)):
        if int(m.sum()) < 30:
            out[label] = None
            continue
        per = S.loc[m].groupby("date")[col].mean()
        ha, hb = CA.holdout(per, h)
        out[label] = {"alpha": float(per.mean()), "t": CA.block_t(per, h), "awal": ha, "akhir": hb}
        sc = f"fwd{h}"
        if cost:
            out[label]["abs_net"] = float(S.loc[m, sc].mean()) - COST * 100
        else:
            out[label]["abs"] = float(S.loc[m, sc].mean())
    return out


def print_gate(S: pd.DataFrame, base: pd.Series, gate: pd.Series, name: str,
               h: int = 5) -> dict:
    """Hitung lalu cetak perbandingan dasar vs digerbang."""
    r = gate_report(S, base, gate, name, h)
    print(f"\n  {r['nama']}  (horizon {h} hari, alpha vs kelas likuiditas yang sama)")
    print(f"    dasar  : n={r['n']:>7,} ({r['per_hari']:>5.1f}/hari)", end="")
    if r["dasar"]:
        d = r["dasar"]
        print(f"  alpha {d['alpha']:+.2f}%  t {d['t']:+.2f}  paruh {d['awal']:+.2f}/{d['akhir']:+.2f}"
              f"  net {d.get('abs_net', float('nan')):+.2f}%")
    else:
        print("  (sampel < 30)")
    print(f"    digerbang: n={r['n_gate']:>7,} ({r['per_hari_gate']:>5.1f}/hari)", end="")
    if r["gerbang"]:
        g = r["gerbang"]
        print(f"  alpha {g['alpha']:+.2f}%  t {g['t']:+.2f}  paruh {g['awal']:+.2f}/{g['akhir']:+.2f}"
              f"  net {g.get('abs_net', float('nan')):+.2f}%")
    else:
        print("  (sampel < 30)")
    return r


def r_report(S: pd.DataFrame, col: str, mask: pd.Series, label: str) -> dict:
    v = S.loc[mask.fillna(False), col].dropna()
    if len(v) < 30:
        return {"label": label, "n": len(v)}
    return {
        "label": label, "n": len(v),
        "meanR": float(v.mean()), "medianR": float(v.median()),
        "win": float((v > 0).mean() * 100.0),
        "sd": float(v.std(ddof=1)),
        "t": float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v)))) if v.std(ddof=1) > 0 else float("nan"),
        "best": float(v.max()), "worst": float(v.min()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    print("Membangun panel uji aturan buku ...")
    S = build(limit=args.limit)
    print(f"\n{len(S):,} saham-hari · {S['code'].nunique()} emiten · {S['date'].nunique()} tanggal "
          f"· {S['date'].min().date()} -> {S['date'].max().date()}")

    # --- A. Trend Template sebagai gerbang -----------------------------------
    print(f"\n{'='*116}\nA. TREND TEMPLATE Minervini hal 105-106 sebagai GERBANG\n{'='*116}")
    tt_cov = S["trend_template"].mean() * 100
    print(f"  Cakupan: {tt_cov:.1f}% saham-hari lolos 8 syarat; rata-rata syarat terpenuhi "
          f"{S['tt_count'].mean():.2f}/8")
    print("  Per syarat (bagian saham-hari yang lolos):")
    for c, lab in (("c1_above_150_200", "1 harga > MA150 & MA200"),
                   ("c2_150_above_200", "2 MA150 > MA200"),
                   ("c3_200_rising", "3 MA200 naik >=1 bulan"),
                   ("c4_50_above_all", "4 MA50 > MA150 & MA200"),
                   ("c5_25pct_off_low", "5 >=25% di atas low 52 minggu"),
                   ("c6_within_25pct_high", "6 dalam 25% dari high 52 minggu"),
                   ("c7_rs70", "7 peringkat RS >= 70"),
                   ("c8_above_50", "8 harga > MA50")):
        print(f"    {lab:34s} {S[c].mean()*100:6.1f}%")
    print_gate(S, S["momkuat"], S["trend_template"], "momentum+breakout (momkuat) x Trend Template", 5)
    print_gate(S, S["brk"], S["trend_template"], "breakout 20 hari x Trend Template", 5)
    print_gate(S, S["mom"], S["trend_template"], "momentum 8% x Trend Template", 5)

    # --- A2. Versi mana yang paling efisien? ---------------------------------
    print(f"\n{'='*116}\nA2. VERSI TREND TEMPLATE: mana yang memberi alpha per baris terbaik\n{'='*116}")
    print(f"  {'versi':<34} {'n':>7} {'/hari':>6} {'net5%':>8} {'alpha5%':>9} {'t':>7} {'%untung':>8} {'h1 net':>7} {'h20 net':>8}")
    variants = [
        ("di atas MA200 (syarat 1 saja)", S["c1_above_150_200"]),
        ("MA200 naik (syarat 3)", S["c3_200_rising"]),
        ("di atas MA50 (syarat 8)", S["c8_above_50"]),
        ("RS >= 70 (syarat 7)", S["c7_rs70"]),
        ("MA200 naik + RS>=70", S["c3_200_rising"] & S["c7_rs70"]),
        (">= 6 dari 8 syarat", S["tt_count"] >= 6),
        ("8 syarat penuh", S["trend_template"]),
    ]
    for lab, gate in variants:
        m = (S["momkuat"] & gate).fillna(False)
        if int(m.sum()) < 30:
            print(f"  {lab:<34} (sampel kecil)")
            continue
        per = S.loc[m].groupby("date")["excg5"].mean()
        net5 = S.loc[m, "fwd5"].mean() - COST * 100
        net1 = S.loc[m, "fwd1"].mean() - COST * 100
        net20 = S.loc[m, "fwd20"].mean() - COST * 100
        win = (S.loc[m, "fwd5"] > COST * 100).mean() * 100
        print(f"  {lab:<34} {int(m.sum()):>7,} {int(m.sum())/S['date'].nunique():>6.1f} "
              f"{net5:>+8.2f} {per.mean():>+9.2f} {CA.block_t(per, 5):>+7.2f} {win:>7.1f}% "
              f"{net1:>+7.2f} {net20:>+8.2f}")

    # --- B. VCP sebagai gerbang ----------------------------------------------
    print(f"\n{'='*116}\nB. VCP (Minervini hal 109-118) sebagai GERBANG\n{'='*116}")
    print(f"  Cakupan VCP: {S['vcp'].mean()*100:.2f}% saham-hari ({int(S['vcp'].sum()):,} baris)")
    print_gate(S, S["brk"], S["vcp"], "breakout 20 hari x VCP", 5)
    print_gate(S, S["momkuat"], S["vcp"], "momkuat x VCP", 5)

    # --- C. Karakter volume-harga Biawak --------------------------------------
    print(f"\n{'='*116}\nC. KARAKTER VOLUME-HARGA (Biawak hal 257) sebagai GERBANG\n{'='*116}")
    print_gate(S, S["mom"], S["vp_strong"], "momentum 8% x (harga naik + volume naik)", 5)
    print_gate(S, S["mom"], S["vp_weak"], "momentum 8% x (harga naik + volume turun)", 5)

    # --- D. Aturan keluar -----------------------------------------------------
    print(f"\n{'='*116}\nD. ATURAN KELUAR dalam satuan R (Turtle hal 269-280 vs rencana aplikasi)\n{'='*116}")
    mask = S["momkuat"].fillna(False)
    print(f"  Entri: momentum+breakout ({int(mask.sum()):,} sinyal)")
    print(f"  {'aturan':<44} {'n':>7} {'meanR':>7} {'medianR':>8} {'%untung':>8} {'t':>7} {'terburuk':>9}")
    for col, lab in (("R_now", "SEKARANG: stop 1,5N, target 2R, maks 5 hari"),
                     ("R_2N_tp", "stop 2N, target 2R (=4N), maks 5 hari"),
                     ("R_2N_20d", "stop 2N, TANPA target, maks 20 hari"),
                     ("R_turtle10", "TURTLE: stop 2N, trail low 10 hari, maks 20 hari"),
                     ("R_turtle10_15", "stop 1,5N, trail low 10 hari, maks 20 hari")):
        r = r_report(S, col, mask, lab)
        if r["n"] < 30:
            print(f"  {lab:<44} (sampel kecil)")
            continue
        print(f"  {lab:<44} {r['n']:>7,} {r['meanR']:>+7.3f} {r['medianR']:>+8.3f} "
              f"{r['win']:>7.1f}% {r['t']:>+7.2f} {r['worst']:>+9.2f}")
        print(f"  {'':44} R terbaik {r['best']:+.1f}")

    # Apakah aturan Turtle bekerja lebih baik pada saham Stage-2? Diuji, bukan diasumsikan.
    tt_mask = (S["momkuat"] & S["trend_template"]).fillna(False)
    if int(tt_mask.sum()) >= 30:
        print(f"\n  Entri: momkuat + Trend Template ({int(tt_mask.sum()):,} sinyal) "
              f"— apakah saham Stage-2 lebih cocok ditahan lama?")
        print(f"  {'aturan':<44} {'n':>7} {'meanR':>7} {'medianR':>8} {'%untung':>8} {'t':>7}")
        for col, lab in (("R_now", "SEKARANG: stop 1,5N, target 2R, maks 5 hari"),
                         ("R_2N_tp", "stop 2N, target 2R (=4N), maks 5 hari"),
                         ("R_2N_20d", "stop 2N, TANPA target, maks 20 hari"),
                         ("R_turtle10", "TURTLE: stop 2N, trail low 10 hari, maks 20 hari")):
            r = r_report(S, col, tt_mask, lab)
            if r["n"] < 30:
                continue
            print(f"  {lab:<44} {r['n']:>7,} {r['meanR']:>+7.3f} {r['medianR']:>+8.3f} "
                  f"{r['win']:>7.1f}% {r['t']:>+7.2f}")

    lmask = S["lpad"].fillna(False)
    print(f"\n  Entri: Launch Pad ({int(lmask.sum()):,} sinyal) — apakah kesimpulan yang sama?")
    print(f"  {'aturan':<44} {'n':>7} {'meanR':>7} {'medianR':>8} {'%untung':>8} {'t':>7}")
    for col, lab in (("Rlp_now", "SEKARANG: stop 1,5N, target 2R, maks 5 hari"),
                     ("Rlp_turtle10", "TURTLE: stop 2N, trail low 10 hari, maks 20 hari")):
        r = r_report(S, col, lmask, lab)
        if r["n"] < 30:
            print(f"  {lab:<44} (sampel kecil)")
            continue
        print(f"  {lab:<44} {r['n']:>7,} {r['meanR']:>+7.3f} {r['medianR']:>+8.3f} "
              f"{r['win']:>7.1f}% {r['t']:>+7.2f}")

    # --- F. Uji ketahanan dua temuan yang lolos -------------------------------
    print(f"\n{'='*116}\nF. KETAHANAN: apakah dua temuan di atas hanya efek tiket kecil / satu tahun tertentu?\n{'='*116}")
    S = S.copy()
    S["ticket_small"] = S.get("ticket_small", pd.Series(False, index=S.index))
    if "ticket_small" not in S.columns or S["ticket_small"].isna().all():
        # Ukuran tiket tidak ikut dihitung di skrip ini; yang bisa dikontrol adalah
        # ukuran (v20) lewat kolom excog5 dari criteria_audit.add_excess().
        pass
    S["year"] = pd.to_datetime(S["date"]).dt.year
    print(f"  {'temuan':<40} {'alpha5':>8} {'residu-ukuran':>14} {'kelasnya':>9} {'net5%':>7}")
    for lab, m in (("momkuat (dasar)", S["momkuat"]),
                   ("momkuat + 8 syarat Stage-2", S["momkuat"] & S["trend_template"]),
                   ("momkuat + MA200 naik", S["momkuat"] & S["c3_200_rising"]),
                   ("momkuat + volume turun", S["momkuat"] & S["vp_weak"]),
                   ("momkuat + volume naik", S["momkuat"] & S["vp_strong"])):
        m = m.fillna(False)
        if int(m.sum()) < 30:
            continue
        # Rata-rata PER TANGGAL dulu (sama seperti gate_report) — bukan rata-rata baris.
        # Bedanya nyata: satu hari dengan 60 sinyal tidak boleh berbobot 60x lipat
        # dibanding hari dengan 1 sinyal, karena itu mengubah arti angkanya.
        a5 = S.loc[m].groupby("date")["excg5"].mean().mean()
        og = S.loc[m].groupby("date")["excog5"].mean().mean()
        net = S.loc[m, "fwd5"].mean() - COST * 100
        print(f"  {lab:<40} {a5:>+8.2f} {og:>+14.2f} {a5-og:>+9.2f} {net:>+7.2f}")

    print(f"\n  Positif di berapa tahun? (alpha5 vs kelas, dan n per tahun)")
    yrs = sorted(S["year"].unique())
    header = "  ".join(f"{y%100:>4d}" for y in yrs)
    print(f"  {'pilihan':<34} {header}")
    for lab, m in (("momkuat", S["momkuat"]),
                   ("momkuat + Stage-2", S["momkuat"] & S["trend_template"]),
                   ("momkuat + volume turun", S["momkuat"] & S["vp_weak"])):
        m = m.fillna(False)
        cells = []
        for y in yrs:
            mm = m & (S["year"] == y)
            if int(mm.sum()) < 20:
                cells.append("  --")
            else:
                cells.append(f"{S.loc[mm].groupby('date')['excg5'].mean().mean():>+4.1f}")
        print(f"  {lab:<34} " + "  ".join(cells))

    # --- E. Yang paling penting: apakah gerbang buku membantu SETELAH biaya ---
    print(f"\n{'='*116}\nE. RINGKASAN: rata-rata return bersih 5 hari (biaya 0,3% sudah dipotong)\n{'='*116}")
    combos = [
        ("momentum 8%", S["mom"]),
        ("momentum+breakout (momkuat)", S["momkuat"]),
        ("momkuat + Trend Template", S["momkuat"] & S["trend_template"]),
        ("momkuat + RS>=70", S["momkuat"] & S["c7_rs70"]),
        ("momkuat + di atas MA200", S["momkuat"] & S["c1_above_150_200"]),
        ("momkuat + VCP", S["momkuat"] & S["vcp"]),
        ("momkuat + volume naik", S["momkuat"] & S["vp_strong"]),
        ("LAUNCHPAD", S["lpad"]),
        ("LAUNCHPAD + Trend Template", S["lpad"] & S["trend_template"]),
        ("Stage-2 saja (Trend Template)", S["trend_template"]),
        ("Stage-2 + breakout 20 hari", S["trend_template"] & S["brk"]),
        ("momkuat + volume kering", S["momkuat"] & S["vp_weak"]),
        ("momentum 8% + volume kering", S["mom"] & S["vp_weak"]),
    ]
    print(f"  {'pilihan':<36} {'n':>7} {'/hari':>6} {'net5%':>8} {'alpha5%':>9} {'t':>7} {'%untung':>8}")
    for lab, m in combos:
        m = m.fillna(False)
        if int(m.sum()) < 30:
            print(f"  {lab:<36} (sampel kecil)")
            continue
        per = S.loc[m].groupby("date")["excg5"].mean()
        net = S.loc[m, "fwd5"].mean() - COST * 100
        win = (S.loc[m, "fwd5"] > COST * 100).mean() * 100
        print(f"  {lab:<36} {int(m.sum()):>7,} {int(m.sum())/S['date'].nunique():>6.1f} "
              f"{net:>+8.2f} {per.mean():>+9.2f} {CA.block_t(per, 5):>+7.2f} {win:>7.1f}%")
    print("\nCatatan: angka 'net5%' sudah memotong biaya putar dua arah 0,3%; '%untung' = "
          "bagian kejadian yang tetap untung SETELAH biaya.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
