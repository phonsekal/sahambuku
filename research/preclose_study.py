#!/usr/bin/env python3
"""Ukur SELISIH harga pra-tutup (15:45 WIB) terhadap harga TUTUP RESMI.

Kenapa harus diukur, bukan diasumsikan
--------------------------------------
Satu-satunya cara mendapatkan alpha momentum apa adanya adalah membayar harga TUTUP
hari sinyal (close[t] -> close[t+h]). Bila pemindaian dilakukan setelah bursa tutup,
harga itu sudah tidak bisa didapat dan membeli di celah buka sesi berikutnya justru
menghapus alpanya (research/criteria_audit.py bagian I/J). Karena itu mode PRA-TUTUP
dibuat: memindai pukul 15:40-15:45 dan membayar harga pasar saat itu.

Mode itu hanya sah bila harga 15:45 memang MEWAKILI harga tutup. Dua hal yang bisa
membuatnya tidak mewakili, dan keduanya diukur di sini:

  1. HARGA. Pukul 15:45 masih ada 5 menit sesi reguler + lelang penutupan (yang
     menentukan harga tutup resmi IDX). Saham yang naik tajam justru yang paling
     mungkin bergerak di lelang. Jadi selisihnya diukur, TERMASUK untuk saham yang
     sedang naik >= 8% (populasi yang dipakai screener).
  2. VOLUME. Lantai nilai transaksi (Rp100 juta/hari) dihitung dari volume hari itu.
     Pukul 15:45 volume belum penuh. Karena itu diukur berapa BAGIAN dari volume
     resmi hari itu yang sudah terkumpul pada 15:45 — angkanya menunjukan seberapa
     besar nilai transaksi pra-tutup meremehkan nilai akhir.

Sumber: bar 5 menit Yahoo (curl_cffi) untuk harga, ringkasan harian IDX resmi untuk
harga tutup (lelang) dan volume resmi. Jendela 5m Yahoo hanya memuat beberapa sesi
terakhir; itu batas sampelnya dan disebut di keluaran.

Jalankan:
  .venv/bin/python research/preclose_study.py --limit 140
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics as st
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "api"))

import panel as P  # noqa: E402

WIB = timezone(timedelta(hours=7))


def load_api():
    spec = importlib.util.spec_from_file_location(
        "idx_api", os.path.join(ROOT, "api", "index.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def bars_5m(A, ticker: str):
    """Bar 5 menit 5 sesi terakhir, waktu dikonversi ke WIB."""
    data = A._yahoo_chart_cffi(ticker, interval="5m", rng="5d")
    if not data:
        return None
    rows = []
    for b in data.get("bars") or []:
        t = b.get("time")
        if not t:
            continue
        dt_wib = datetime.fromtimestamp(int(t), WIB)
        rows.append({"dt": dt_wib, "d": dt_wib.date(), "hm": dt_wib.hour * 60 + dt_wib.minute,
                     "price": float(b["close"]), "high": float(b.get("high") or b["close"]),
                     "volume": float(b.get("volume") or 0)})
    return rows


# Jam potong yang dibandingkan. Pertanyaan yang dijawab: bila memindai/membeli pada
# jam ini, seberapa jauh harganya dari harga TUTUP RESMI, dan berapa banyak sinyal yang
# masih memenuhi syaratnya saat penutupan? Semua dihitung dari bar yang SUDAH diambil,
# jadi menambah jam potong tidak menambah satu pun permintaan ke sumber data.
CUTS_DEFAULT = (15 * 60 + 0, 15 * 60 + 30, 15 * 60 + 40, 15 * 60 + 45)


def analyse_one(A, code: str, panel: pd.DataFrame, cut_hm: int = 15 * 60 + 40,
                trigger_pct: float = 8.0, cuts=CUTS_DEFAULT):
    ticker = code if "." in code else f"{code}.JK"
    rows = bars_5m(A, ticker)
    if not rows:
        return []
    g = panel[panel["code"] == code].sort_values("date")
    if g.empty:
        return []
    out = []
    by_day = {}
    for r in rows:
        by_day.setdefault(r["d"], []).append(r)
    for d, rs in by_day.items():
        rs.sort(key=lambda r: r["hm"])
        # bar terakhir SESI REGULER (sampai 15:49) pada atau sebelum jam pemindaian
        pre = [r for r in rs if r["hm"] <= cut_hm]
        # bar terakhir sesi reguler (harga penutupan sesi reguler, sebelum lelang)
        reg = [r for r in rs if r["hm"] <= 15 * 60 + 49]
        if not pre or not reg:
            continue
        ts = pd.Timestamp(d)
        hit = g[g["date"] == ts]
        if hit.empty:
            continue
        official_close = float(hit["close"].iloc[0])
        official_vol = float(hit["volume"].iloc[0])
        official_high = float(hit["high"].iloc[0])
        prev_rows = g[g["date"] < ts]
        if prev_rows.empty:
            continue
        prev_close = float(prev_rows["close"].iloc[-1])
        if prev_close <= 0 or official_close <= 0:
            continue
        px_pre = pre[-1]["price"]              # harga pada/sesudah 15:40
        px_reg = reg[-1]["price"]              # harga terakhir sesi reguler (15:45-15:49)
        vol_pre = sum(r["volume"] for r in pre)
        vol_reg = sum(r["volume"] for r in reg)
        ret_pre = (px_pre / prev_close - 1) * 100
        ret_reg = (px_reg / prev_close - 1) * 100
        ret_close = (official_close / prev_close - 1) * 100
        # apakah syarat "tembus high 20 hari" yang terlihat pra-tutup bertahan?
        h20 = float(prev_rows["high"].tail(20).max()) if len(prev_rows) >= 20 else None
        # ---- harga, sinyal, dan volume pada SETIAP jam potong --------------------
        per_cut = {}
        for c in cuts:
            sel = [r for r in rs if r["hm"] <= c]
            if not sel:
                continue
            px_c = sel[-1]["price"]
            if px_c <= 0:
                continue
            ret_c = (px_c / prev_close - 1) * 100
            per_cut[c] = {
                "px": px_c, "ret": ret_c, "flag": ret_c >= trigger_pct,
                "drift": (official_close / px_c - 1) * 100,
                "volshare": (sum(r["volume"] for r in sel) / official_vol * 100)
                            if official_vol else None,
            }
        out.append({
            "code": code, "date": str(d),
            "prev_close": prev_close, "official_close": official_close,
            "px_1540": px_pre, "px_regclose": px_reg,
            "ret_1540": ret_pre, "ret_reg": ret_reg, "ret_close": ret_close,
            "drift_to_close_pct": (official_close / px_reg - 1) * 100,
            "vol_reg_session": vol_reg,
            "vol_share_at_1540": (vol_pre / official_vol * 100) if official_vol else None,
            "vol_share_reg": (vol_reg / official_vol * 100) if official_vol else None,
            "high20": h20,
            "flag_1540": ret_pre >= trigger_pct,
            "flag_close": ret_close >= trigger_pct,
            "brk_1540": (h20 is not None and px_pre >= h20),
            "brk_close": (h20 is not None and official_close >= h20),
            "brk_close_high": (h20 is not None and official_high >= h20),
            "per_cut": per_cut,
        })
    return out


def fmt(v, digits=2, sign=False):
    if v is None:
        return "-"
    return f"{v:+,.{digits}f}" if sign else f"{v:,.{digits}f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=140)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--export", default="")
    args = ap.parse_args()

    A = load_api()
    panel = P.load_panel()
    codes = sorted(panel["code"].unique())
    if len(codes) > args.limit:
        step = len(codes) / args.limit
        codes = [codes[int(i * step)] for i in range(args.limit)]
    print(f"panel: {len(panel):,} baris · {panel['date'].nunique()} tanggal "
          f"({panel['date'].min().date()} -> {panel['date'].max().date()})")
    print(f"memeriksa {len(codes)} emiten bar 5 menit (5 sesi terakhir) ...")

    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for res in ex.map(lambda c: analyse_one(A, c, panel), codes):
            rows.extend(res)
    if not rows:
        print("tidak ada bar 5m yang cocok dengan panel (jendela Yahoo terlalu pendek).")
        return
    df = pd.DataFrame(rows)
    print(f"\nsampel: {len(df):,} emiten-hari · {df['code'].nunique()} emiten · "
          f"sesi {df['date'].min()} -> {df['date'].max()}")

    # --- 0. perbandingan JAM POTONG ------------------------------------------
    # Inilah bagian yang menjawab "jam berapa sebaiknya memindai & membeli": pada tiap
    # jam potong dipakai bar terakhir yang sudah tersedia pada jam itu (bar 5 menit),
    # lalu dibandingkan dengan harga tutup RESMI dan diperiksa berapa sinyal >= 8% yang
    # masih memenuhi syaratnya saat penutupan.
    print("\n-- 0. PERBANDINGAN JAM POTONG (bar 5m vs TUTUP RESMI hari yang sama) --")
    print(f"  {'jam':<6} {'n':>6} {'rata selisih':>13} {'median':>8} {'|selisih|':>10} "
          f"{'>0,5%':>7} {'sinyal':>7} {'bertahan':>9} {'volume':>8}")
    for c in sorted({k for row in rows for k in (row.get("per_cut") or {})}):
        sel = [r["per_cut"][c] for r in rows if (r.get("per_cut") or {}).get(c)]
        if not sel:
            continue
        dr = pd.Series([s["drift"] for s in sel])
        flags = [s for s in sel if s["flag"]]
        hours = f"{c // 60:02d}:{c % 60:02d}"
        # "bertahan" = masih >= 8% pada harga TUTUP RESMI; diambil dari baris penuh
        trig = df[df["per_cut"].map(lambda d, c=c: bool((d or {}).get(c, {}).get("flag")))]
        keep = (trig["flag_close"].mean() * 100) if len(trig) else None
        vs = pd.Series([s["volshare"] for s in sel if s["volshare"] is not None])
        print(f"  {hours:<6} {len(sel):>6} {fmt(dr.mean(), 3, True):>13} "
              f"{fmt(dr.median(), 3, True):>8} {fmt(dr.abs().mean(), 3):>10} "
              f"{(dr.abs() > 0.5).mean() * 100:>6.1f}% {len(flags):>7} "
              f"{(fmt(keep, 1) + '%') if keep is not None else '-':>9} "
              f"{(fmt(vs.mean(), 1) + '%') if len(vs) else '-':>8}")
    print("  bacaan: 'rata selisih' = harga tutup resmi dibagi harga pada jam itu (positif "
          "= tutup lebih tinggi). '|selisih|' = besar penyimpangan tanpa arah. "
          "'bertahan' = sinyal >= 8% pada jam itu yang MASIH >= 8% saat tutup resmi.")

    # --- 1. selisih harga pra-tutup vs harga tutup resmi ----------------------
    print("\n-- 1. HARGA: selisih pukul 15:40 (dan akhir sesi reguler) ke TUTUP RESMI --")
    for lab, sub in (("SEMUA sampel", df),
                     ("saham naik >= 8% pada 15:40", df[df["flag_1540"]])):
        if sub.empty:
            continue
        d_reg = sub["drift_to_close_pct"]
        print(f"  {lab}: n={len(sub):,}")
        print(f"    selisih tutup resmi vs harga 15:45 : rata {fmt(d_reg.mean(), 3, True)}% · "
              f"median {fmt(d_reg.median(), 3, True)}% · "
              f"|selisih| rata {fmt(d_reg.abs().mean(), 3)}%")
        print(f"    selisih di luar +-0,5%              : "
              f"{(d_reg.abs() > 0.5).mean() * 100:.1f}% kejadian · "
              f"> +1% : {(d_reg > 1).mean() * 100:.1f}% · < -1% : {(d_reg < -1).mean() * 100:.1f}%")
        print(f"    contoh ekstrem (10 terbesar): " +
              ", ".join(f"{c} {v:+.1f}%" for c, v in
                        d_reg.abs().sort_values(ascending=False).head(10)
                        .index.to_series().map(lambda i: (df.loc[i, 'code'],
                                                          df.loc[i, 'drift_to_close_pct']))))

    # --- 2. apakah syarat pra-tutup bertahan sampai tutup? -------------------
    print("\n-- 2. APAKAH SYARAT BERTAHAN: naik >= 8% di 15:40 vs di tutup resmi --")
    trig = df[df["flag_1540"]]
    if not trig.empty:
        keep = trig["flag_close"].mean() * 100
        print(f"  sinyal >= 8% pukul 15:40 : {len(trig):,} kejadian")
        print(f"    masih >= 8% pada tutup resmi : {keep:.1f}%  "
              f"(batal: {100 - keep:.1f}%)")
        print(f"    return tutup resmi rata      : {fmt(trig['ret_close'].mean(), 2, True)}% "
              f"(vs pukul 15:40 {fmt(trig['ret_1540'].mean(), 2, True)}%)")
        # yang RAPUH (jarak tipis dari ambang) dibanding yang jauh
        fragile = trig[trig["ret_1540"] < 10]
        strong = trig[trig["ret_1540"] >= 10]
        for lab, s in (("jarak tipis (8-10%)", fragile), ("jauh (>= 10%)", strong)):
            if len(s) >= 10:
                print(f"    {lab:<22} n={len(s):>5,} · bertahan "
                      f"{s['flag_close'].mean() * 100:>5.1f}%")
    print("\n-- 3. APAKAH SYARAT 'TEMBUS HIGH 20 HARI' BERTAHAN --")
    brk = df[df["brk_1540"]]
    if not brk.empty:
        print(f"  tembus high 20 hari pukul 15:40 : {len(brk):,} kejadian")
        print(f"    tutup resmi tetap DI ATAS high20     : {brk['brk_close'].mean() * 100:.1f}%")
        print(f"    high hari itu menyentuh di atas high20: {brk['brk_close_high'].mean() * 100:.1f}%")

    # --- 4. bagian volume yang sudah masuk pada 15:40 -------------------------
    print("\n-- 4. VOLUME: berapa bagian dari volume RESMI hari itu yang ada di 15:40 --")
    vs = df["vol_share_at_1540"].dropna()
    if len(vs):
        print(f"  bagian volume pada 15:40 : rata {vs.mean():.1f}% · median {vs.median():.1f}% "
              f"· p25 {vs.quantile(0.25):.1f}% · p75 {vs.quantile(0.75):.1f}%")
        print(f"  di bawah 90%            : {(vs < 90).mean() * 100:.1f}% kejadian · "
              f"di bawah 80%: {(vs < 80).mean() * 100:.1f}%")
        print("  Artinya lantai nilai Rp100 juta pada mode pra-tutup bisa melihat angka")
        print("  yang lebih kecil dari nilai akhirnya; kandidat yang lolos tipis sebaiknya")
        print("  diperiksa ulang setelah tutup (cron momentum pukul 17:50).")

    print("\nBATASAN: (a) bar 5 menit Yahoo menutup transaksi lewat LELANG PENUTUPAN,")
    print("sehingga harga tutup resmi di sini adalah patokan yang benar dan selisihnya")
    print("adalah risiko nyata mode pra-tutup; (b) jendela 5m Yahoo hanya beberapa sesi,")
    print("jadi sampelnya kecil dan tidak mencakup semua rezim pasar; (c) sampel emiten")
    print("diambil merata dari daftar alfabetis, bukan acak berbobot.")
    if args.export:
        # Kolom per_cut berisi dict (satu entri per jam potong) dan tidak masuk akal
        # ditulis sebagai kolom CSV, jadi dikeluarkan dari ekspor.
        df.drop(columns=["per_cut"], errors="ignore").to_csv(args.export, index=False)
        print(f"diekspor: {args.export}")


if __name__ == "__main__":
    main()
