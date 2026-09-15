#!/usr/bin/env python3
"""Uji KOMBINASI: filter ukuran tiket x akumulator diam-diam di atas sinyal RS aplikasi.

Pertanyaan yang dijawab
-----------------------
Aplikasi punya tiga hal yang belum pernah diuji BERSAMA:
  1. sinyal masuk yang sudah dipakai (RS Leader / Top-10 skor RS / skor beli app),
  2. filter ukuran tiket (`skip_small_ticket`) — replika `avg_ticket_size()` di
     api/index.py, memakai Freq IDX Edge. Sudah lolos uji lintas rezim di
     research/idx_daily_summary.py (1.610 tanggal, 2020-2026),
  3. penanda akumulator diam-diam per broker (`silent`) — research/bandar_study.py,
     riwayatnya HANYA 80 hari bursa.

Yang diuji di sini: apakah menambahkan (2), lalu (3), benar-benar memperbaiki
hasil sinyal (1) — atau menambah kerumitan tanpa manfaat.

Cara menjalankan
----------------
  # Bagian A: filter tiket di atas sinyal RS, riwayat panjang, 0 kuota
  .venv/bin/python research/combo_study.py --part ticket

  # Bagian B: akumulator diam-diam (butuh kuota Arjum, ~1 request/saham)
  .venv/bin/python research/combo_study.py --part silent --universe liquid

  # Bagian D: layakkah filter tiket diperluas ke kelas LIKUID/CUKUP? (0 kuota IDX)
  .venv/bin/python research/combo_study.py --part bands

HASIL (dijalankan 14 Sep 2026)
-----------------------------
BAGIAN A — filter tiket: POSITIF dan tahan uji. Universe SANGAT LIKUID, alpha
vs universe yang sama (bukan vs seluruh pasar), 5 tahun, 897 saham:

  sinyal                      tanpa filter        + buang tiket kecil
  RS Leader + SANGAT LIKUID   +1,04% (blok t+3,19) +1,49% (blok t+5,13)
  Top-10 skor RS              +1,96% (blok t+3,83) +2,47% (blok t+4,06)
  RS rekor 20h                +1,14% (blok t+2,81) +1,47% (blok t+3,95)
  skor beli app >=70          +0,67% (blok t+2,78) +1,28% (blok t+5,78)
  (semua di horizon 20 hari; n turun ~10% karena tiket kecil dibuang)

  Kontrol "HANYA tiket kecil" pada sinyal yang sama: -2,03% (h20), dan per tahun
  2023 -5,1 / 2024 -7,7 / 2026 -10,5 -> kelompok yang dibuang memang buruk.

  Uji holdout paruh waktu (h20) memperbaiki KEDUA paruh pada keempat sinyal.
  Contoh paling jelas, skor beli app >=70: awal +0,08% -> +0,77%; akhir
  +0,97% -> +1,54%. Pada h5 perbaikannya kecil (+0,02 s/d +0,11 pp).

  Rincian per tahun (h20) menunjukkan filter MEMBALIK tahun yang negatif, bukan
  cuma menambah di tahun yang sudah bagus: RS Leader 2023 -0,7 -> +0,1 dan
  2026 -1,5 -> -0,5; skor beli app >=70 2026 -1,7 -> +0,1.

  Artinya: ini memang layak dipakai sebagai PENYARING masuk, dan sudah aktif di
  aplikasi (skip_small_ticket=True). Aplikasi membatasinya ke kelas SANGAT
  LIKUID — dan itulah yang diuji di sini.

BAGIAN C — kriteria SWING (jalur proksi, 5 tahun): POSITIF, sejalan dengan A.
  SWING mensyaratkan nilai 20 hari >= Rp10 M, jadi SEMUA kandidatnya kelas SANGAT
  LIKUID — persis populasi yang disasar filter. Hasilnya (h20):
    SWING + SANGAT LIKUID        +0,83% (blok t=+1,90)
    + buang tiket kecil          +1,45% (blok t=+3,32)   <- +0,62 pp
    (kontrol) HANYA tiket kecil  -2,56%; holdout -5,49% / -1,51%
  Filter membuang 550 dari 4.533 kandidat (12,1%). Holdout h20: paruh awal
  -0,54% -> +0,42% (tanda dibalik jadi positif), paruh akhir +1,54% -> +1,99%.
  Catatan: yang diuji adalah jalur PROKSI (harga+volume), karena jalur utama SWING
  memakai deret BandarValue yang riwayatnya cuma 80 hari. Kondisi harga/volume
  identik di kedua jalur, jadi kesimpulannya berlaku untuk kriteria SWING.

BANDAR — TIDAK BISA DI-BACKTEST (bukan "gagal").
  Kriteria BANDAR ditentukan oleh Broker Summary HARI ITU (status ACC + value share
  Top Buyer >= 60%), bukan deret waktu -> tidak ada riwayat untuk diuji.
  Kandidatnya cenderung kelas LIKUID/CUKUP karena BANDAR tidak mensyaratkan
  likuiditas. Awalnya ini membuat filter tidak menggigit (200 saham, 2 halaman:
  0 dibuang) — keadaan itu BERUBAH setelah Bagian D memperluas filter (lihat di
  bawah): pada pemindaian live yang sama filter kini membuang kandidat BANDAR.

BAGIAN D — FILTER TIKET DIPERLUAS KE KELAS LIKUID & CUKUP: DIDUKUNG BUKTI.
  Pertanyaan: efek tiket memang ada di SANGAT LIKUID (bagian A), tapi apakah ia
  khas kelas itu, atau berlaku juga di bawahnya? Ternyata berlaku juga.

  1) Residual tiket masih memprediksi DI DALAM tiap kelas (IC blok, h5):
       SANGAT LIKUID +0,0636 (t=+4,9) | LIKUID +0,0740 (t=+7,3) | CUKUP +0,0489 (t=+5,0)
     Setelah variasi ukuran DI DALAM kelas dikeluarkan (residual ganda), IC-nya
     TETAP positif: +0,0794 (t=+15,4) / +0,0915 (t=+19,0) / +0,0441 (t=+9,5).
     Jadi bukan proksi ukuran.
     PERINGATAN SATUAN: kolom exc5/exc20 dari backtest_rs SUDAH persen; jangan
     dikali 100 lagi (versi pertama laporan ini salah 100x karena itu).

  2) Kuintil resid BERSYARAT SINYAL (populasi kandidat, yang relevan bagi filter)
     NAIK monoton di LIKUID dan CUKUP, dan Q1 selalu yang terburuk:
       SANGAT LIKUID Q1 +1,31% -> Q5 +1,39% (Q3 +2,38%)
       LIKUID        Q1 +1,62% -> Q5 +4,79%
       CUKUP         Q1 +5,47% -> Q5 +9,86%
     Kuintil TANPA syarat sinyal tetap tidak monoton (di CUKUP bahkan Q5-Q1
     negatif) — konsisten dengan sifat "penyaring, bukan peringkat" dari bagian A.

  3) Alpha sinyal (h20, pembanding = kelas yang sama) dengan ambang TETAP per kelas
     (SANGAT LIKUID -0,5176 TIDAK diubah; LIKUID -0,5056; CUKUP -0,2853):
                     RS Leader          skor beli >=70
       SANGAT LIKUID  +1,14 -> +1,56     +0,77 -> +1,34
       LIKUID         +2,07 -> +2,76     +1,52 -> +2,80
       CUKUP          +2,90 -> +3,44     +3,44 -> +3,83
     SEMUANYA membaik di KEDUA paruh holdout. Kontrol "HANYA 20% terendah"
     negatif di LIKUID (-0,79%) dan CUKUP (-0,51%); di SANGAT LIKUID +1,25% tapi
     t=+1,50 (tidak nyata) -> kelompok yang dibuang memang buruk.
     Biaya: kandidat berkurang ~4-6% (SANGAT LIKUID 12.921 -> 11.730).

  KENAPA AMBANG TETAP, BUKAN KUINTIL LINTAS-SAHAM: produksi menganalisis SATU
  saham dalam satu permintaan, jadi kuintil lintas-saham tidak tersedia saat itu.
  Karena itulah dipakai ambang tetap = kuintil-20 resid DI DALAM kelas itu.

  CATATAN JUJUR: kelas CUKUP adalah yang paling lemah dasarnya (IC blok h20
  t=+1,7, dan kuintil tanpa syarat sinyal tidak monoton). Yang menyelamatkannya
  adalah kuintil BERSYARAT sinyal yang monoton naik dan holdout dua paruh yang
  sama-sama membaik. Kalau ingin lebih konservatif, batasi filter ke LIKUID+.

  DITERAPKAN di api/index.py: TICKET_RESID_FLOOR_BY_GRADE. Kelas KURANG LIKUID
  TIDAK disentuh (tidak ada bukti di sana).

BAGIAN B — akumulator diam-diam: TIDAK BISA DIUJI, bukan "gagal".
  Data broker hanya tersedia 80-90 hari terakhir dan yang SEGAR mulai
  2026-05-13, sedangkan R (backtest) berakhir 2026-06-22 karena horizon 60 hari
  membuang 60 sesi terakhir. Irisannya cuma **23 tanggal** -> apa pun hasilnya
  tidak berarti. Menambah `recent_days` besar hanya memasukkan tanggal BASI
  (saham tidak aktif) dan membuat irisan tampak lebar padahal palsu.
  Kesimpulan: kombinasi tiket x akumulator-diam-diam tidak bisa diuji dari sini;
  yang bisa dikatakan hanya bahwa akumulator diam-diam SENDIRI sudah tidak punya
  daya prediksi (research/bandar_study.py, 77 hari).

Aturan bukti yang dipakai script ini
------------------------------------
- alpha = excess return vs IHSG, di-cluster per tanggal (t-stat dari sebaran
  alpha harian), dan pembandingnya DIBATASI ke universe yang sama (saham
  SANGAT LIKUID) supaya tidak membandingkan apel dengan jeruk.
- horizon 20 hari TUMPANG-TINDI -> t-nya tidak boleh dibaca apa adanya. Karena
  itu blok tidak tumpang-tindih (stride = horizon) ikut dicetak.
- holdout paruh waktu wajib: sinyal yang cuma hidup di satu paruh adalah noise.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest_rs as B  # noqa: E402
import idx_daily_summary as M  # noqa: E402

# Konstanta filter tiket — HARUS sama dengan api/index.py
TICKET_REG_INTERCEPT = 6.6712
TICKET_REG_SLOPE = 0.3681
TICKET_RESID_FLOOR = -0.5176
SGT_LIKUID = 10e9          # ambang "SANGAT LIKUID" (Rp 10 M/hari)
SEG_GAP_DAYS = 10          # jarak hari yang memutus segmen kontigu


# ---------------------------------------------------------------------------
# 1. FILTER UKURAN TIKET (replika avg_ticket_size, point-in-time)
# ---------------------------------------------------------------------------

def build_ticket(idxd: pd.DataFrame) -> pd.DataFrame:
    """Replika `avg_ticket_size()` api/index.py dari cache ringkasan IDX.

    Aplikasi memakai 20 baris TERAKHIR dari payload /api/history. Di sini sama:
    rata-rata bergerak 20 sesi, dihitung DALAM segmen kontigu (cache IDX punya
    lubang; rata-rata yang melintasi lubang akan memakai data yang tidak
    berurutan dan menghasilkan tiket palsu).
    """
    d = idxd[["code", "date", "value", "freq"]].copy()
    d = d[(d["freq"] > 0) & (d["value"] > 0)].sort_values(["code", "date"])
    gap = d.groupby("code")["date"].diff().dt.days
    d["seg"] = (gap.isna() | (gap > SEG_GAP_DAYS)).groupby(d["code"]).cumsum()

    g = d.groupby(["code", "seg"], sort=False)
    d["v20"] = g["value"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    sv = g["value"].transform(lambda s: s.rolling(20, min_periods=20).sum())
    sf = g["freq"].transform(lambda s: s.rolling(20, min_periods=20).sum())
    d["ticket"] = sv / sf.replace(0, np.nan)

    ok = (d["v20"] > 0) & (d["ticket"] > 0)
    d["resid"] = np.where(
        ok,
        np.log(d["ticket"].where(ok)) - (TICKET_REG_INTERCEPT
                                         + TICKET_REG_SLOPE * np.log(d["v20"].where(ok))),
        np.nan,
    )
    d["small"] = ((d["v20"] >= SGT_LIKUID) & (d["resid"] <= TICKET_RESID_FLOOR)).fillna(False)
    return d[["code", "date", "v20", "ticket", "resid", "small"]]


def attach_ticket(R: pd.DataFrame, T: pd.DataFrame) -> pd.DataFrame:
    R = R.copy()
    R["code"] = R["tk"].str.replace(".JK", "", regex=False)
    R["date"] = pd.to_datetime(R["date"]).dt.normalize()
    return R.merge(T, on=["code", "date"], how="left")


# ---------------------------------------------------------------------------
# 2. CETAK PERBANDINGAN
# ---------------------------------------------------------------------------

def compare(R: pd.DataFrame, specs: List[Tuple[str, pd.Series]],
            uni: pd.Series, horizons=(5, 20)) -> None:
    """alpha vs universe yang sama, per horizon, plus blok tidak tumpang-tindih."""
    for h in horizons:
        print(f"\n  --- horizon {h} hari (pembanding: universe yang sama) ---")
        print(f"  {'variasi':<40} {'n':>7} {'/hari':>6} {'alpha%':>7} {'t':>6} "
              f"{'blok t':>7} {'>IHSG%':>7}")
        for label, mask in specs:
            rows = B.evaluate(R, mask, label, universe_mask=uni)
            r = next((x for x in rows if x["h"] == h), None)
            if r is None:
                print(f"  {label:<40} {'-':>7}")
                continue
            bt = block_t(R, mask, uni, h)
            print(f"  {label:<40} {r['n']:>7,} {r['per_hari']:>6.1f} {r['alpha']:>+7.2f} "
                  f"{r['t']:>+6.2f} {bt:>+7.2f} {r['hit_exc']:>7.1f}")


def block_t(R: pd.DataFrame, mask: pd.Series, uni: pd.Series, h: int) -> float:
    """t-stat dari blok tidak tumpang-tindih (stride = h) — koreksi tumpang-tindih."""
    per = (R.loc[mask.fillna(False)].groupby("date")[f"exc{h}"].mean()
           - R.loc[uni.fillna(False)].groupby("date")[f"exc{h}"].mean()).dropna()
    if len(per) < 2 * h:
        return float("nan")
    blocks = [per.iloc[i::h].mean() for i in range(h)]
    a = np.array(blocks)
    a = a[~np.isnan(a)]
    if len(a) < 3 or a.std(ddof=1) == 0:
        return float("nan")
    return float(a.mean() / (a.std(ddof=1) / math.sqrt(len(a))))


def per_year(R: pd.DataFrame, mask: pd.Series, uni: pd.Series, h: int = 20) -> str:
    """Apakah alpha datang dari satu tahun saja, atau tersebar?"""
    out = []
    for y, part in R.groupby(R["date"].dt.year):
        if part["date"].nunique() < 20:
            continue
        rows = B.evaluate(part, mask.loc[part.index], str(y), universe_mask=uni.loc[part.index])
        r = next((x for x in rows if x["h"] == h), None)
        if r:
            out.append(f"{y}:{r['alpha']:+.1f}")
    return " ".join(out)


def holdout_split(R: pd.DataFrame, mask: pd.Series, uni: pd.Series, h: int = 5) -> str:
    """Apakah alpha bertahan di KEDUA paruh waktu (bukan cuma satu)."""
    ds = np.sort(R["date"].unique())
    mid = ds[len(ds) // 2]
    out = []
    for lab, part in (("awal", R[R["date"] < mid]), ("akhir", R[R["date"] >= mid])):
        m = mask.loc[part.index]
        u = uni.loc[part.index]
        rows = B.evaluate(part, m, lab, universe_mask=u)
        r = next((x for x in rows if x["h"] == h), None)
        out.append(f"{lab} {r['alpha']:+.2f}%(t{r['t']:+.1f})" if r else f"{lab} -")
    return " | ".join(out)


# ---------------------------------------------------------------------------
# 3. BAGIAN A — FILTER TIKET DI ATAS SINYAL RS (0 KUOTA)
# ---------------------------------------------------------------------------

def part_ticket(years: int, workers: int, top: int) -> None:
    print("== BAGIAN A: filter ukuran tiket di atas sinyal RS ==")

    M.BUDGET = 0
    idxd = M.fetch_range(years, workers, False)
    T = build_ticket(idxd)
    print(f"  data tiket: {len(T):,} saham-hari, {T['code'].nunique()} emiten, "
          f"{T['date'].nunique()} tanggal")

    ih, data = B.load_data(years, "all", workers)
    frames = []
    for tk, df in data.items():
        r = B.build_rows(tk, df, ih)
        if r is not None:
            frames.append(r)
    R = pd.concat(frames, ignore_index=True)
    R = attach_ticket(R, T)
    have = R["v20"].notna()
    print(f"  observasi gabungan (RS x tiket): {have.sum():,} dari {len(R):,} baris")

    uni = (R["v20"] >= SGT_LIKUID) & have          # universe pembanding: SANGAT LIKUID
    small = R["small"].fillna(False) & have
    print(f"  universe SANGAT LIKUID: {int(uni.sum()):,} saham-hari; "
          f"ditandai tiket kecil: {int((small & uni).sum()):,}")

    specs = [
        ("BASIS: RS Leader + SANGAT LIKUID", uni & (R["leader"] == 1)),
        ("+ buang tiket kecil", uni & (R["leader"] == 1) & ~small),
        ("  (kontrol) HANYA tiket kecil", uni & (R["leader"] == 1) & small),
        ("BASIS: Top-%d skor RS" % top, uni & B.top_n_mask(R, "score", top)),
        ("+ buang tiket kecil", uni & B.top_n_mask(R, "score", top) & ~small),
        ("  (kontrol) HANYA tiket kecil", uni & B.top_n_mask(R, "score", top) & small),
        ("BASIS: RS rekor 20h", uni & R["new20"].fillna(False)),
        ("+ buang tiket kecil", uni & R["new20"].fillna(False) & ~small),
        ("BASIS: skor beli app >=70", uni & (R["buy_score"] >= 70)),
        ("+ buang tiket kecil", uni & (R["buy_score"] >= 70) & ~small),
    ]
    compare(R, specs, uni, horizons=(5, 20))

    for h in (5, 20):
        print(f"\n  Holdout paruh waktu (horizon {h}h):")
        for label, mask in specs:
            print(f"    {label:<40} {holdout_split(R, mask, uni, h)}")

    print("\n  Alpha per tahun (horizon 20h) — apakah hanya dari satu tahun:")
    for label, mask in specs:
        print(f"    {label:<40} {per_year(R, mask, uni, 20)}")


# ---------------------------------------------------------------------------
# 4. BAGIAN B — AKUMULATOR DIAM-DIAM DI ATAS SINYAL (butuh kuota)
# ---------------------------------------------------------------------------

def part_silent(years: int, workers: int, top: int, universe: str, win: int,
                codes_arg: str = "", recent_days: int = 75) -> None:
    import bandar_study as S  # impor di dalam fungsi: modul ini menyentuh jaringan

    print("== BAGIAN B: akumulator diam-diam di atas sinyal RS ==")
    codes = [c.strip().upper() for c in codes_arg.split(",") if c.strip()]
    if not codes:
        from api.index import load_idx_tickers  # type: ignore
        codes = [t.replace(".JK", "") for t in load_idx_tickers(universe)]
    L, _nb = S.build_long(codes, workers)        # riwayat maksimum 80 hari
    if L is None or not len(L):
        print("  data broker tidak tersedia."); return

    sig = S.build_signals(L, win)
    sig["date"] = pd.to_datetime(sig["date"]).dt.normalize()
    # API mengembalikan data BASI untuk saham tidak aktif (ditemukan di bandar_study.py),
    # jadi jendela dibatasi ke periode yang benar-benar segar. Sinyalnya sudah
    # point-in-time, jadi menyaring SETELAH pembentukan tidak merusak apa pun.
    mx = sig["date"].max()
    if recent_days:
        sig = sig[sig["date"] >= mx - pd.Timedelta(days=recent_days)]
    print(f"  sinyal broker: {len(sig):,} saham-hari, {sig['code'].nunique()} emiten, "
          f"{sig['date'].nunique()} tanggal "
          f"(flag aktif {int(sig[f'silent_flag{win}'].sum()):,})")
    print(f"  jendela segar: {sig['date'].min().date()} s/d {mx.date()} "
          f"(batas {recent_days} hari)")

    M.BUDGET = 0
    idxd = M.fetch_range(years, workers, False)
    T = build_ticket(idxd)

    ih, data = B.load_data(years, "all", workers)
    frames = [B.build_rows(tk, df, ih) for tk, df in data.items()]
    R = pd.concat([f for f in frames if f is not None], ignore_index=True)
    R = attach_ticket(R, T)
    R = R.merge(sig[["code", "date", f"silent_flag{win}",
                     f"silent_net{win}", f"silent_cons{win}"]],
                on=["code", "date"], how="inner")

    if not len(R):
        print("  tidak ada irisan tanggal antara broker (80 hari) dan data lain."); return

    have = R["v20"].notna()
    uni = (R["v20"] >= SGT_LIKUID) & have
    small = R["small"].fillna(False) & have
    silent = R[f"silent_flag{win}"] == 1.0
    print(f"  irisan: {len(R):,} saham-hari, {R['date'].nunique()} tanggal "
          f"({R['date'].min().date()} s/d {R['date'].max().date()})")
    print(f"  universe SANGAT LIKUID: {int(uni.sum()):,}; flag silent: {int((silent & uni).sum()):,}")

    specs = [
        ("BASIS: RS Leader + SANGAT LIKUID", uni & (R["leader"] == 1)),
        ("+ buang tiket kecil", uni & (R["leader"] == 1) & ~small),
        ("+ wajib akumulator diam-diam", uni & (R["leader"] == 1) & silent),
        ("+ KEDUANYA", uni & (R["leader"] == 1) & ~small & silent),
        ("BASIS: Top-%d skor RS" % top, uni & B.top_n_mask(R, "score", top)),
        ("+ KEDUANYA", uni & B.top_n_mask(R, "score", top) & ~small & silent),
    ]
    compare(R, specs, uni, horizons=(5, 20))


# ---------------------------------------------------------------------------
# 3b. BAGIAN D — APAKAH FILTER TIKET LAYAK DIPERLUAS KE KELAS BAWAH?
# ---------------------------------------------------------------------------
# Filter produksi mensyaratkan `grade == "SANGAT LIKUID"` (>= Rp10 M/hari). Pertanyaan
# yang wajar: kalau efeknya nyata, kenapa tidak dipakai juga di LIKUID dan CUKUP?
# Bagian ini mengujinya, dan sekaligus mengukur berapa kandidat yang akan terdampak.

BANDS = [
    ("SANGAT LIKUID >=Rp10M", 10e9, 1e15),
    ("LIKUID Rp1-10M", 1e9, 10e9),
    ("CUKUP Rp100jt-1M", 100e6, 1e9),
]


def part_bands(years: int, workers: int, top: int) -> None:
    print("== BAGIAN D: layakkah filter tiket DIPERLUAS ke kelas LIKUID/CUKUP? ==")

    M.BUDGET = 0
    T = build_ticket(M.fetch_range(years, workers, False))
    ih, data = B.load_data(years, "all", workers)
    frames = [B.build_rows(tk, df, ih) for tk, df in data.items()]
    R = pd.concat([f for f in frames if f is not None], ignore_index=True)
    R = attach_ticket(R, T)
    have = R["v20"].notna()
    print(f"  observasi (RS x tiket): {int(have.sum()):,} dari {len(R):,}")

    # CATATAN SATUAN: kolom exc5/exc20 dari backtest_rs.build_rows SUDAH dalam persen
    # (out[f"exc{h}"] = ... * 100.0). Karena itu jangan dikali 100 lagi di sini.
    def _q_resid(S: pd.DataFrame, label: str, min_n: int = 50) -> None:
        """Kuintil exc20 menurut `resid` DI DALAM kelas — tidak dikali 100 (sudah persen)."""
        parts = []
        for _, g in S.groupby("date"):
            s = g[["resid", "exc20"]].dropna()
            if len(s) < min_n:
                continue
            try:
                q = pd.qcut(s["resid"].rank(method="first"), 5, labels=False)
            except Exception:
                continue
            parts.append(s["exc20"].groupby(q).mean())
        if not parts:
            print(f"  {label:<46} kuintil: —")
            return
        avg = pd.concat(parts, axis=1).mean(axis=1)
        txt = "  ".join(f"Q{int(k)+1}={v:+.2f}%" for k, v in avg.items())
        q1, q5 = float(avg.get(0, np.nan)), float(avg.get(4, np.nan))
        print(f"  {label:<46} {txt}  (Q5-Q1 {(q5-q1):+.2f} pp)")

    print("\n  -- 1) Apakah RESIDUAL tiket masih memprediksi DI DALAM tiap kelas? --")
    print("     (IC blok tidak tumpang-tindih; kuintil resid DI DALAM kelas itu)")
    print(f"  {'kelas':<24} {'n':>10} {'blok IC h5':>15} {'blok IC h20':>16}")
    for name, lo, hi in BANDS:
        bm = have & (R["v20"] >= lo) & (R["v20"] < hi)
        S = R.loc[bm]
        if len(S) < 5000:
            print(f"  {name:<24} {len(S):>10,}  sampel kurang — lewati")
            continue
        ic5, t5, n5 = M.ic_by_date(S, "resid", "exc5", stride=5)
        ic20, t20, n20 = M.ic_by_date(S, "resid", "exc20", stride=20)
        print(f"  {name:<24} {len(S):>10,} {ic5:>+9.4f}(t{t5:+4.1f}) "
              f"{ic20:>+10.4f}(t{t20:+4.1f}) ({n5}/{n20} blok)")
        # Kuintil TANPA syarat sinyal (seluruh kelas) ...
        _q_resid(S, f"    kuintil SEMUA saham kelas ini:")
        # ... dan kuintil BERSYARAT: inilah yang relevan bagi filter (populasi kandidat).
        keep = (R["leader"] == 1) | (R["buy_score"] >= 70)
        _q_resid(R.loc[bm & keep],
                 "    kuintil kandidat sinyal (RS Leader / skor>=70):", min_n=20)
        # Ambang 20% terendah per kelas — calon konstanta bila filter diperluas.
        q20 = float(S["resid"].quantile(0.2))
        print(f"    resid kuintil-20 tiap kelas: {q20:+.4f}")
        # Apakah efeknya cuma proksi ukuran DI DALAM kelas? Buang variasi v20 pakai residual ganda.
        W = S[["date", "resid", "v20", "exc20"]].dropna()
        W["lv"] = np.log(W["v20"].clip(lower=1))
        rr = []
        for _, g in W.groupby("date"):
            if len(g) < 50:
                continue
            rx, rc = g["resid"].rank(), g["lv"].rank()
            rr.append((rx - np.polyval(np.polyfit(rc, rx, 1), rc)).corr(g["exc20"].rank()))
        if rr:
            a = np.array(rr)
            print(f"    resid SETELAH ukuran-dalam-kelas dikeluarkan: IC {a.mean():+.4f} "
                  f"t={a.mean()/(a.std(ddof=1)/np.sqrt(len(a))):+5.2f} ({len(a)} tgl)")


    print("\n  -- 2) Dampak bila filter DIPERLUAS: alpha sinyal per kelas likuiditas --")
    small_prod = (R["resid"] <= TICKET_RESID_FLOOR).fillna(False) & have   # ambang produksi

    # RANCANGAN PRODUKSI: produksi menganalisis SATU saham, jadi kuintil lintas-saham
    # TIDAK bisa dihitung saat itu. Ambangnya harus angka TETAP. Di bawah ini ambang
    # tetap per kelas = kuintil-20 resid kelas itu (diukur dari data ini, tercetak di
    # bagian 1). Kelas SANGAT LIKUID SENGAJA TIDAK DIUBAH (-0,5176, sudah live).
    # Kunci memakai NAMA KELAS produksi (api/index.py _liquidity_grade), bukan label BANDS.
    grade_of_band = {name: g for (name, _lo, _hi), g in
                     zip(BANDS, ("SANGAT LIKUID", "LIKUID", "CUKUP"))}
    floor_by_grade = {}
    for nm, lo_, hi_ in BANDS:
        bm_ = have & (R["v20"] >= lo_) & (R["v20"] < hi_)
        if int(bm_.sum()) < 5000:
            continue
        g = grade_of_band[nm]
        # SANGAT LIKUID dibiarkan memakai konstanta produksi yang sudah live.
        floor_by_grade[g] = (TICKET_RESID_FLOOR if g == "SANGAT LIKUID"
                             else float(R.loc[bm_, "resid"].quantile(0.2)))
    print("  ambang tetap per kelas (nama kelas produksi): "
          + ", ".join(f"{k}={v:+.4f}" for k, v in floor_by_grade.items()))
    is_sgt = have & (R["v20"] >= 10e9)
    is_lik = have & (R["v20"] >= 1e9) & (R["v20"] < 10e9)
    is_cuk = have & (R["v20"] >= 100e6) & (R["v20"] < 1e9)
    small_v2 = (
        (is_sgt & (R["resid"] <= floor_by_grade["SANGAT LIKUID"]))   # tidak berubah
        | (is_lik & (R["resid"] <= floor_by_grade["LIKUID"]))
        | (is_cuk & (R["resid"] <= floor_by_grade["CUKUP"]))
    ).fillna(False) & have

    for name, lo, hi in BANDS:
        bm = have & (R["v20"] >= lo) & (R["v20"] < hi)
        if int(bm.sum()) < 5000:
            continue
        pct = R.loc[bm, "resid"].groupby(R.loc[bm, "date"]).rank(pct=True)
        small_band = (pct <= 0.2).reindex(R.index).fillna(False)   # kuantil 20% terendah DI DALAM kelas
        n_sig = int((bm & (R["leader"] == 1)).sum())
        print(f"\n   [{name}] kandidat RS Leader: {n_sig:,} saham-hari")
        specs = [
            ("  RS Leader (tanpa filter)", bm & (R["leader"] == 1)),
            ("  + ampth tetap produksi LAMA (-0,5176)", bm & (R["leader"] == 1) & ~small_prod),
            ("  + RANCANGAN: ambang tetap per kelas", bm & (R["leader"] == 1) & ~small_v2),
            ("  + buang 20% resid terendah (ideal)", bm & (R["leader"] == 1) & ~small_band),
            ("  (kontrol) HANYA 20% terendah", bm & (R["leader"] == 1) & small_band),
            ("  skor beli app >=70 (tanpa filter)", bm & (R["buy_score"] >= 70)),
            ("  + RANCANGAN: ambang tetap per kelas", bm & (R["buy_score"] >= 70) & ~small_v2),
            ("  + buang 20% resid terendah (ideal)", bm & (R["buy_score"] >= 70) & ~small_band),
        ]
        compare(R, specs, bm, horizons=(20,))
        for label, mask in specs:
            print(f"    {label:<36} {holdout_split(R, mask, bm, 20)}")


def swing_flags(tk: str, df: pd.DataFrame) -> pd.DataFrame:
    """Replika jalur PROKSI kriteria SWING (api/index.py, tanpa data Broker Summary).

    Produksi memakai jalur ini bila deret BandarValue tidak tersedia; jalur utama
    memakai akumulasi broker yang riwayatnya cuma 80 hari sehingga tidak bisa
    di-backtest. Kondisi harga/volumenya sama persis di kedua jalur, jadi menguji
    proxy-nya tetap mengukur apakah filter tiket menolong SWING.
    """
    import api.index as ai  # noqa: E402

    c = df["Adj"].astype(float)
    v = df["Volume"].astype(float).fillna(0.0)
    val = c * v
    s20, s50 = c.rolling(20).mean(), c.rolling(50).mean()
    r14 = ai.rsi(c, 14)
    vma20 = v.rolling(20).mean()
    valma10, valma20 = val.rolling(10).mean(), val.rolling(20).mean()

    trend_up = (c > s20) & (s20 > s50)
    pullback = trend_up & ((c / s20 - 1.0).abs() <= 0.03) & (r14 >= 35) & (r14 <= 68)
    breakout = (c >= s20) & (v >= 1.5 * vma20.replace(0, np.nan)) & (s20 > s50)
    setup_ok = (pullback | breakout).fillna(False)

    ok = (val > valma20) & (valma20 >= 10e9) & (val.shift(1) <= val) & (valma10 > valma20)
    out = pd.DataFrame({"swing": (ok.fillna(False) & setup_ok)})
    out["tk"] = tk
    return out.reset_index().rename(columns={"index": "date"})


def part_swing(years: int, workers: int, top: int) -> None:
    print("== BAGIAN C: filter ukuran tiket di atas kriteria SWING (jalur proksi) ==")

    M.BUDGET = 0
    T = build_ticket(M.fetch_range(years, workers, False))

    ih, data = B.load_data(years, "all", workers)
    frames = []
    for tk, df in data.items():
        r = B.build_rows(tk, df, ih)
        if r is None:
            continue
        r["date"] = pd.to_datetime(r["date"]).dt.normalize()
        sw = swing_flags(tk, df)
        sw["date"] = pd.to_datetime(sw["date"]).dt.normalize()
        # build_rows sudah memangkas bar terakhir (butuh hasil ke depan); kolom SWING
        # digabung setelahnya supaya penandanya tetap point-in-time.
        r = r.merge(sw, on=["tk", "date"], how="left")
        frames.append(r)
    R = pd.concat(frames, ignore_index=True)
    R = attach_ticket(R, T)

    have = R["v20"].notna()
    uni = (R["v20"] >= SGT_LIKUID) & have
    small = R["small"].fillna(False) & have
    sw = R["swing"].fillna(False)
    n_sw = int((sw & uni).sum())
    n_sm = int((sw & uni & small).sum())
    print(f"  kandidat SWING di universe SANGAT LIKUID: {n_sw:,} saham-hari")
    print(f"  di antaranya bertiket kecil (akan dibuang filter): {n_sm:,} "
          f"({100 * n_sm / max(n_sw, 1):.1f}%)")

    specs = [
        ("SWING (proksi) + SANGAT LIKUID", uni & sw),
        ("+ buang tiket kecil", uni & sw & ~small),
        ("  (kontrol) HANYA tiket kecil", uni & sw & small),
    ]
    compare(R, specs, uni, horizons=(5, 20))
    for h in (5, 20):
        print(f"\n  Holdout paruh waktu (horizon {h}h):")
        for label, mask in specs:
            print(f"    {label:<40} {holdout_split(R, mask, uni, h)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Uji kombinasi filter tiket x akumulator diam-diam")
    ap.add_argument("--part", default="ticket",
                    choices=["ticket", "silent", "swing", "bands"])
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--universe", default="all", choices=["all", "liquid"])
    ap.add_argument("--codes", default="", help="daftar kode dipisah koma untuk --part silent")
    ap.add_argument("--win", type=int, default=10, help="jendela hari untuk flag silent")
    ap.add_argument("--recent-days", type=int, default=75,
                    help="batasi ke N hari terakhir (data basi disingkirkan); 0 = semua")
    args = ap.parse_args()

    if args.part == "ticket":
        part_ticket(args.years, args.workers, args.top)
    elif args.part == "swing":
        part_swing(args.years, args.workers, args.top)
    elif args.part == "bands":
        part_bands(args.years, args.workers, args.top)
    else:
        part_silent(args.years, args.workers, args.top, args.universe, args.win,
                    args.codes, args.recent_days)


if __name__ == "__main__":
    main()
