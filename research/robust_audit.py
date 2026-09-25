#!/usr/bin/env python3
"""Uji ulang SEMUA kriteria lama dengan UKURAN TAHAN-OUTLIER (0 kuota, 0 jaringan).

Kenapa ada
----------
Seluruh angka alpha di aplikasi ini dihitung dengan RATA-RATA return lebih (excg) lawan
kelas likuiditas yang sama. Cara itu benar untuk pertanyaan "kalau saya ambil acak satu
saham dari kelompok ini, rata-ratanya berapa", tetapi di panel penuh (~1,3 juta
saham-hari, banyak mikro-cap) rata-rata mudah ditentukan oleh beberapa saham dengan
return ekstrem. Itu bukan teori: saat aturan fundamental diukur dengan kedua ukuran,
kategorinya BERBEDA TANDA untuk ROE, untuk P/E, dan untuk turnaround — dan pada kasus
turnaround, kontrol negatifnya ("laba -> rugi") justru menghasilkan rata-rata LEBIH
BESAR daripada sinyalnya. Artinya angka rata-rata itu milik kelompoknya, bukan milik
aturannya (lihat research/fundamental_study.py bagian F).

Skrip ini menjalankan pemeriksaan yang sama untuk kriteria yang SUDAH dipakai aplikasi
(momentum, breakout, Launch Pad, SVR, reversal, skor beli, dsb) supaya pertanyaan
"apakah angka lamanya bertahan kalau ekor return dibuang" dijawab data, bukan keyakinan.

Cara membaca hasilnya
---------------------
* Kolom RATA-RATA = cara lama (sama dengan research/criteria_audit.py).
* Kolom MEDIAN  = selisih median per tanggal lawan median SELURUH baris yang bisa
  dinilai pada tanggal itu (definisi di `criteria_audit.median_excess`, satu definisi
  untuk seluruh riset).
* SEPAKAT  = kedua ukuran positif. Aturan itu kuat terhadap ekor return.
* TIPIS     = kedua positif tetapi salah satunya tidak lolos blok t >= 2.
* RAPUH     = tanda kedua ukuran BERBEDA, atau hanya rata-rata yang positif.
  Artinya hasilnya bergantung pada beberapa saham ekstrem — untuk kriteria seperti ini,
  angka di dokumentasi aplikasi TIDAK boleh dibaca sebagai "keunggulan yang tipikal".
* NEGATIF   = keduanya negatif.

Jalankan:
    .venv/bin/python research/robust_audit.py
    .venv/bin/python research/robust_audit.py --limit 200     # uji cepat
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "api"))

import panel as P            # noqa: E402
import criteria_audit as CA  # noqa: E402  <- pembangun panel sinyal yang sama

MIN_N = 30


def stats(S: pd.DataFrame, mask: pd.Series, h: int) -> Optional[Dict]:
    mask = mask.fillna(False)
    n = int(mask.sum())
    if n < MIN_N:
        return None
    per = S.loc[mask].groupby("date")[f"excg{h}"].mean()
    med = CA.median_excess(S, mask, h)
    ha, hb = CA.holdout(per, h)
    hm_a, hm_b = CA.holdout(med, h)
    return {
        "n": n,
        "per_day": n / max(1, S["date"].nunique()),
        "mean": float(per.mean()), "t": CA.block_t(per, h), "early": ha, "late": hb,
        "med": float(med.mean()), "t_med": CA.block_t(med, h),
        "med_early": hm_a, "med_late": hm_b,
        "net": float(S.loc[mask, f"fwd{h}"].mean() - CA.COST_ROUND_TRIP * 100),
    }


def verdict(r5: Optional[Dict], r20: Optional[Dict]) -> str:
    """Putusan dari DUA ukuran. 'EKOR' dipisah dari 'RAPUH' dengan sengaja: keduanya
    punya tanda yang berbeda, tetapi artinya berlainan.
    """
    if not r20:
        return "sampel kecil"
    ok_mean = r20["mean"] > 0 and r20["t"] >= 2
    ok_med = r20["med"] > 0 and r20["t_med"] >= 2
    if r20["mean"] <= 0 and r20["med"] <= 0:
        return "NEGATIF"
    if ok_mean and ok_med:
        return "SEPAKAT"
    if r20["mean"] > 0 and r20["med"] > 0:
        return "TIPIS"
    if ok_mean:
        # Rata-rata kuat tetapi saham TIPIKAL di kelompok ini tertinggal: hasilnya milik
        # ekor keuntungan. Untuk portofolio berdisiplin ini bisa tetap berguna, tetapi
        # 'saya ambil satu sinyal' tidak sama dengan 'saya ambil rata-ratanya'.
        return "EKOR"
    return "RAPUH"


def main() -> int:
    ap = argparse.ArgumentParser(description="Uji ulang kriteria lama dgn ukuran tahan-outlier")
    ap.add_argument("--limit", type=int, default=None, help="batasi jumlah emiten (uji cepat)")
    args = ap.parse_args()

    p = P.load_panel()
    if args.limit:
        keep = sorted(p["code"].unique())[: args.limit]
        p = p[p["code"].isin(keep)]
    print(f"Membangun panel sinyal: {p['code'].nunique()} emiten ...")
    S = CA.build_signal_frame(p)
    S["year"] = pd.to_datetime(S["date"]).dt.year
    S = CA.add_excess(S)
    nd = S["date"].nunique()

    # Pembanding dasar: SELURUH baris yang bisa dinilai (dipakai oleh kolom MEDIAN).
    base = pd.Series(True, index=S.index)
    print(f"\n{len(S):,} saham-hari · {S['code'].nunique()} emiten · {nd} tanggal · "
          f"{S['date'].min().date()} -> {S['date'].max().date()}")
    print("Angka alpha selalu lawan KELAS LIKUIDITAS yang sama pada tanggal yang sama.\n")
    print("=" * 132)
    print("H20 (horizon yang dipakai untuk menyimpulkan): apakah angka rata-rata bertahan?")
    print("=" * 132)
    print(f"  {'kriteria':<16} {'n':>8} {'/hari':>6} {'RATA-RATA':>10} {'t':>7} "
          f"{'MEDIAN':>9} {'t':>7} {'paruh median':>16} {'net20%':>7}  putusan")
    order = CA.CRITERIA_ORDER
    summary: List[Tuple[str, str, Dict, Dict]] = []
    for name in order:
        r20 = stats(S, S[name], 20)
        r5 = stats(S, S[name], 5)
        if not r20:
            print(f"  {name:<16} (sampel < {MIN_N})")
            continue
        v = verdict(r5, r20)
        summary.append((name, v, r5 or {}, r20))
        print(f"  {name:<16} {r20['n']:>8,} {r20['per_day']:>6.1f} {r20['mean']:>+10.2f} "
              f"{r20['t']:>+7.1f} {r20['med']:>+9.2f} {r20['t_med']:>+7.1f} "
              f"{r20['med_early']:>+7.2f}/{r20['med_late']:<+7.2f} {r20['net']:>+7.2f}  {v}")

    print("\n" + "=" * 132)
    print("H5 (horizon pendek)")
    print("=" * 132)
    print(f"  {'kriteria':<16} {'n':>8} {'/hari':>6} {'RATA-RATA':>10} {'t':>7} "
          f"{'MEDIAN':>9} {'t':>7} {'paruh median':>16}")
    for name, _v, r5, _r20 in summary:
        if not r5:
            continue
        print(f"  {name:<16} {r5['n']:>8,} {r5['per_day']:>6.1f} {r5['mean']:>+10.2f} "
              f"{r5['t']:>+7.1f} {r5['med']:>+9.2f} {r5['t_med']:>+7.1f} "
              f"{r5['med_early']:>+7.2f}/{r5['med_late']:<+7.2f}")

    # -------------------------------------------------------------- KONTROL ARAH
    # Kalau "momentum NAIK" positif hanya karena pergerakan besar menarik ekor kanan,
    # maka "momentum TURUN" harus menunjukkan pola yang sama. Kontrol ini yang membedakan
    # "arahnya bekerja" dari "besar gerakannya bekerja" — dan itu pertanyaan yang
    # menentukan, karena seluruh keluarga kriteria momentum berdiri di atas asumsi pertama.
    print("\n" + "=" * 132)
    print("KONTROL ARAH — apakah yang bekerja arahnya, atau sekadar 'ada gerakan besar'?")
    print("=" * 132)
    # Ambang nilai disamakan dengan definisi produksi: `momentum>=8%` = naik >= 8% DAN
    # nilai >= Rp100 juta. Untuk kelompok "turun" dan "|gerakan|" dipakai lantai nilai
    # rata-rata 20 hari (v20) karena nilai HARI ITU tidak disimpan di panel sinyal.
    floor = S["v20"] >= 100e6
    ctrl = {
        "naik >= 8% (yang dipakai)": S["momentum>=8%"],
        "TURUN <= -8% (kontrol arah)": (S["day_ret"] <= -8.0) & floor,
        "|gerakan| >= 8% (tanpa arah)": (S["day_ret"].abs() >= 8.0) & floor,
        "naik >= 8% & tembus high20": S["momentumkuat"],
        "naik >= 8% tapi TIDAK tembus": (S["momentum>=8%"] & ~S["breakout"]),
    }
    print(f"  {'kelompok':<32} {'n':>8} {'RATA-RATA':>10} {'t':>7} {'MEDIAN':>9} "
          f"{'t':>7} {'net20%':>7} {'%untung20':>10}")
    for label, mask in ctrl.items():
        m = pd.Series(mask, index=S.index).fillna(False)
        r = stats(S, m, 20)
        if not r:
            print(f"  {label:<32} (sampel < {MIN_N})")
            continue
        win = float((S.loc[m, "fwd20"] > CA.COST_ROUND_TRIP * 100).mean() * 100)
        print(f"  {label:<32} {r['n']:>8,} {r['mean']:>+10.2f} {r['t']:>+7.1f} "
              f"{r['med']:>+9.2f} {r['t_med']:>+7.1f} {r['net']:>+7.2f} {win:>9.1f}%")

    print("\n" + "=" * 132)
    print("RINGKASAN PUTUSAN (h20)")
    print("=" * 132)
    for v in ("SEPAKAT", "TIPIS", "EKOR", "RAPUH", "NEGATIF"):
        names = [n for n, vv, _a, _b in summary if vv == v]
        if names:
            print(f"  {v:<8} ({len(names):>2}): {', '.join(names)}")
    print("\n  TINDAK LANJUT yang benar untuk kriteria RAPUH, dalam urutan manfaat:")
    print("   1. tulis di dokumentasi aplikasi bahwa angkanya adalah RATA-RATA yang ditarik")
    print("      ekor, bukan hasil yang tipikal (angka '%untung' sudah memberi petunjuk itu);")
    print("   2. uji dengan pembanding yang lebih ketat (mis. saham berukuran sama, bukan")
    print("      sekelas likuiditas) sebelum menyimpulkan aturannya lemah;")
    print("   3. JANGAN mengubah ambangnya sampai lolos di ukuran tahan-outlier — itu")
    print("      bentuk pencarian pola di masa lalu yang paling mudah tidak disadari.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
