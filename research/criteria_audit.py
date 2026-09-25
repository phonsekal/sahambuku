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
  .venv/bin/python research/criteria_audit.py --short   # horizon 1/2/3/5 hari saja

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

HORIZON PENDEK (--short): apakah bisa dipakai harian, dan apakah filter tiket +
rezim bull tetap berguna di 1-5 hari? YA untuk keduanya.

  Bersih biaya 0,3% round-trip (net%) dan % kejadian yang untung setelah biaya:

  varian                          n/hari    h1 net   h3 net   h5 net   %untung h1
  momentum>=8% (semua)              27,6     +0,87    +2,16    +3,02      40,3%
  momentum>=8% & kelas >=CUKUP       18,9     +0,76    +1,87    +2,60      41,3%
  momentum>=8% & kelas >=LIKUID      11,9     +0,32    +1,16    +1,66      38,6%
  buy>=70                           61,8     +0,22    +0,97    +1,48      40,1%
  buy>=70 & buang tiket             54,7     +0,30    +1,11    +1,66      41,1%
  buy>=70 & tiket & bull            39,7     +0,47    +1,51    +2,20      41,9%
  bsjp & buang tiket                 5,2     +0,26    +0,88    +1,14      37,4%
  volsr & buang tiket               50,4     −0,23    +0,10    +0,47      35,7%
  launchpad & buang tiket            0,5     +0,78    +2,24    +2,94      45,9%

  1. FILTER TIKET + REZIM BULL BERTAHAN di horizon pendek, dan efeknya MONOTON
     makin panjang horizon: untuk buy>=70, net 1 hari +0,22 -> +0,30 (tiket) ->
     +0,47 (tiket+rezim); pada 5 hari +1,48 -> +1,66 -> +2,20 (blok t +33,1).
     Jadi kombinasi ini bukan artefak horizon panjang.
  2. PERSENTASE KEJADIAN YANG UNTUNG SETELAH BIAYA SELALU DI BAWAH 50%
     (35,7%-45,9%). Artinya rata-rata positif itu DITARIK OLEH EKOR KEUNTUNGAN,
     bukan oleh tingkat keberhasilan. Ini konsekuensi praktis yang paling penting:
     stop-loss dan ukuran posisi menentukan hasil, bukan hit-rate. Siapa pun yang
     memakai daftar ini tanpa disiplin stop akan mendapat angka yang jauh lebih buruk
     daripada tabel di atas.
  3. volsr TIDAK layak dipakai di 1-3 hari (net negatif di h1, alpha ~0) — ia sinyal
     20 hari. BSJP di 1 hari (+0,26%) jauh di bawah momentum murni (+0,87%),
     konsisten dengan temuan bahwa syarat tambahannya mubazir.
  4. launchpad tetap paling efisien per kejadian (+0,78 net di 1 hari, 45,9% untung),
     tetapi tetap paling jarang (0,5 sinyal/hari).

GABUNGAN (--combos): yang paling penting dari seluruh audit ini.

  momentum>=8% x breakout 20 hari  n irisan 17.726 (~11/hari)
    momentum saja (tanpa breakout)     h1 -0,00  h5 +0,13  h20 +0,93   net5 +1,44%
    breakout saja (tanpa momentum)     h1 -0,01  h5 -0,14  h20 -1,06   net5 +0,07%
    IRISAN keduanya                    h1 +1,64  h5 +3,58  h20 +6,07   net5 +5,35%
    blok t h1 +16,45 / h5 +22,25 / h20 +12,56 · holdout h5 +2,64/+4,52
    positif di KETUJUH tahun (2020-2026) · semua kelas likuiditas (SANGAT +2,88)
    buang tiket +5,22% vs tiket kecil +1,62% · bull +5,71% vs bear +1,77%

  momentum>=8% x volsr              n irisan 1.964
    momentum saja h5 +1,42 | volsr saja h5 -0,11 | IRISAN h5 +7,28 (t=+12,8), net5 +8,92%
  momentum>=8% x buy>=70            n irisan 21.826
    momentum saja h5 +0,64 | buy saja h5 +0,38 | IRISAN h5 +2,32 (t=+19,3), net5 +4,04%
  momentum>=8% x launchpad          n irisan   560
    momentum saja h5 +1,55 | launchpad saja h5 +2,20 | IRISAN h5 +1,84  <-- TIDAK ADA SINERGI

  KESIMPULAN: momentum SENDIRI hampir tidak berisi (-0,00 sampai +0,13 pada 1-5
  hari). Yang berisi adalah momentum DI DALAM struktur naik. Karena itu kriteria
  "momentumkuat" (momentum + breakout) ditambahkan sebagai kriteria tersendiri, dan
  deskripsi kriteria "momentum" menyebut bahwa keunggulannya berasal dari subset itu.
  Pengecualian yang jujur: dengan launchpad TIDAK ada sinergi (irisan < sisi terbaik),
  jadi jangan menggabungkan keduanya hanya karena keduanya terdengar kuat.

HARGA MASUK (--exec / --exec2) — bagian I & J, dan ini membatalkan cara membaca
semua angka di atas bila pemindaian dilakukan setelah bursa tutup.

  Seluruh tabel sebelumnya mengukur close[t] -> close[t+h], yaitu MENGHARAPKAN bisa
  membeli di harga tutup hari sinyal. Pemindaian pukul 18.00 tidak bisa itu. Uji
  langsung, momentumkuat, 15.259 sinyal 2020-2026:

    jalur masuk                                 abs%  alphaK%  blok t   net%
    [asumsi lama] close[t] -> close[t+5]       +4,92    +4,05  +18,23   +4,62
    tunggu: close[t+1] -> close[t+4]           +2,03    +1,55   +5,53   +1,73
    tunggu: close[t+1] -> close[t+6]           +2,74    +1,98   +8,26   +2,44
    kejar OPEN t+1 -> close[t+1]               -1,81    -1,60   -7,46   -2,11
    kejar OPEN t+1 -> close[t+5]               +0,24    -0,15   -2,62   -0,06

  Mekanismenya (subsampel ber-harga-open, 3.938 sinyal): celah buka rata-rata
  +3,24%, lalu fade intraday -1,81%. Jadi SELURUH alpha momentum adalah satu malam
  celah buka, bukan tren beberapa hari. Setelah celah diambil, sisanya nol/negatif.

  Menunggu diskon TIDAK menolong (14.511 sinyal ber-Low, semua tahun):
    limit = harga close sinyal  terisi 75,7%  alpha H+3 -1,54%  blok t -9,69
    limit 2% di bawah close     terisi 61,9%  alpha H+3 -3,67%  blok t -18,38
    limit 5% di bawah close     terisi 44,4%  alpha H+3 -5,69%  blok t -36,45
    limit 8% di bawah close     terisi 18,4%  alpha H+3 -8,21%  blok t -40,51
    pembanding semua sinyal                alpha H+3 +3,39%  blok t +17,61
  Yang tampak "murah" memang sedang jatuh, bukan sedang diskon. Jadi jangan pasang
  batas harga di bawah harga sinyal sebagai syarat masuk.

  Jalur yang bertahan dan dipakai produksi: BELI DI HARGA TUTUP SESI BERIKUTNYA
  (+1,55% relatif kelas dalam 3 hari, blok t +5,53, net +1,73%, positif di 5 dari 7
  tahun dan di kedua paruh). Aturan ini dipasang di momentum_info.entry_rule dan
  kolom Rencana Harian. Kalau ingin alpha PENUH, pindai SEBELUM bursa tutup.

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
* Uji harga masuk (bagian I/J) hanya bisa memakai tahun yang punya kolom OpenPrice:
  3.938 dari 15.259 sinyal momentumkuat, dan 93% di antaranya dari 2025-2026
  (bagian J-c). Bagian J-f menjawab ini dengan sampel PENUH 15.259 sinyal (tanpa
  syarat kolom open) dan hasilnya tetap positif, jadi kesimpulan "tunggu tutup sesi
  berikutnya" tidak bergantung pada tahun yang kebetulan punya kolom open.
* Asumsi eksekusi bagian J masih ideal: harga tutup sesi berikutnya diasumsikan
  bisa didapat (di bursa ini likuiditas eceran besar, tetapi pada saham tipis
  volume saat tutup bisa menggeser harga). Realisasinya lebih buruk dari tabel.
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
SHORT_HORIZONS = (1, 2, 3, 5)   # horizon pendek: menjawab "apakah bisa dipakai harian?"
ALL_HORIZONS = tuple(sorted(set(HORIZONS) | set(SHORT_HORIZONS)))
COST_ROUND_TRIP = 0.003    # 0,3% (0,15%/sisi) — sama dengan default /api/backtest
LIQUID_GRADES = ("CUKUP", "LIKUID", "SANGAT LIKUID")
TOP_GRADES = ("LIKUID", "SANGAT LIKUID")


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


def median_excess(S: pd.DataFrame, mask: pd.Series, h: int,
                  base: Optional[pd.Series] = None) -> pd.Series:
    """Deret per-tanggal UKURAN TAHAN-OUTLIER: median return-lebih kelompok DIKURANGI
    median populasi dasar pada tanggal yang sama.

    KENAPA SELISIH, BUKAN MEDIAN MENTAH: `excg{h}` sudah dikurangi RATA-RATA kelas pada
    tanggal itu, sedangkan distribusi return saham miring ke kanan — akibatnya median
    `excg` NEGATIF untuk hampir semua kelompok, termasuk yang jelas lebih baik daripada
    pembandingnya. Kalau median mentah dipakai, tidak ada aturan yang bisa lolos, dan itu
    bukan temuan tentang pasarnya melainkan cacat alat ukur (ketahuan saat mengukur aturan
    fundamental: semua baris "median" ~−0,6% sampai −4%). Selisih terhadap dasar membuang
    kemiringan itu karena keduanya memikulnya sama.

    Dipakai bersama ukuran rata-rata: kalau keduanya BERBEDA TANDA, hasilnya ditentukan
    beberapa saham ekstrem dan aturannya tidak boleh dipasang.
    """
    mask = mask.fillna(False)
    sub = S.loc[mask].groupby("date")[f"excg{h}"].median()
    ref_mask = base if base is not None else pd.Series(True, index=S.index)
    ref = S.loc[ref_mask.fillna(False)].groupby("date")[f"excg{h}"].median()
    return sub.sub(ref.reindex(sub.index))


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

    # --- momentumkuat: DEFINISI SAMA dengan kriteria produksi (momentum + breakout).
    # Tanpa baris ini, kriteria termuda di aplikasi tidak ikut teraudit.
    out["momentumkuat"] = (out["momentum>=8%"] & out["breakout"]
                           & (value >= 100e6)).fillna(False)
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
        low = df["Low"].astype(float)
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
        for h in ALL_HORIZONS:
            frame[f"fwd{h}"] = (close.shift(-h) / close - 1.0).to_numpy() * 100.0
            no = op.shift(-h).where(op.shift(-h) > 0)
            frame[f"fwd{h}_open"] = (no / close - 1.0).to_numpy() * 100.0
        frame["open_ok"] = op_ok.to_numpy(bool)
        # BSJP apa adanya: beli di close hari ini, jual di OPEN besok.
        nxt_ok = (op.shift(-1) > 0) & (op.shift(-1) < close * 5)
        frame["fwd1_open"] = ((op.shift(-1).where(nxt_ok) / close - 1.0) * 100.0).to_numpy()
        frame["open_next_ok"] = nxt_ok.to_numpy(bool)

        # --- EKSEKUSI NYATA (bagian I) ---------------------------------------
        # Sinyal dihitung dari close[t]. Kalau pemindaian dilakukan SETELAH bursa
        # tutup, harga close[t] sudah tidak bisa dibeli — yang tersedia adalah OPEN
        # t+1. Jadi diukur dua jalur masuk yang benar-benar bisa dieksekusi:
        #   gap1 : celah buka besok terhadap close hari sinyal (bisa > 0 = kejar).
        #   oc{k}: masuk di OPEN t+1, keluar di CLOSE hari ke-k setelah masuk.
        #   cc{k}: masuk di CLOSE t+1 (menunggu sehari), keluar k hari setelahnya.
        nxt = op.shift(-1).where(nxt_ok)
        frame["gap1"] = ((nxt / close - 1.0) * 100.0).to_numpy()
        # Order limit di harga close sinyal: terisi bila LOW besok menyentuh harga itu.
        # Kolom Low jauh lebih lengkap daripada Open, jadi aturan ini bisa diuji pada
        # sampel jauh lebih besar (lihat laporan bagian J-e).
        low_ok = (low > 0) & (low < close * 5)
        lw = low.shift(-1).where(low_ok.shift(-1))
        with np.errstate(all="ignore"):
            frame["lowratio1"] = ((lw / close - 1.0) * 100.0).to_numpy()
        frame["low_next_ok"] = low_ok.shift(-1).fillna(False).to_numpy(bool)
        for k in (1, 2, 3, 5):
            frame[f"oc{k}"] = ((close.shift(-k) / nxt - 1.0) * 100.0).to_numpy()
            frame[f"cc{k}"] = ((close.shift(-(k + 1)) / close.shift(-1) - 1.0) * 100.0).to_numpy()
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
    for h in ALL_HORIZONS:
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

    # Jalur eksekusi (bagian I) juga harus dibandingkan dengan KELAS LIKUIDITAS yang
    # sama, kalau tidak tabelnya tidak sebanding dengan tabel close->close: saham
    # yang melonjak itu mayoritas kelas kecil, dan kelas kecil naik lebih tinggi dari
    # pasar, jadi selisih mentah terhadap pasar akan menyanjung jalur mana pun.
    for col in ("oc1", "oc2", "oc3", "oc5", "cc1", "cc3", "cc5"):
        if col not in S.columns:
            continue
        cc = f"excg_{col}"
        cls = S.groupby(["date", "grade"])[col].transform("mean")
        S[cc] = S[col] - cls
        S[f"exc_{col}"] = S[col] - S["date"].map(S.groupby("date")[col].mean())

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
    "buy>=70", "buy>=50", "momentumkuat", "momentum>=10%", "momentum>=8%",
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


def report_exec2(S: pd.DataFrame) -> None:
    """BAGIAN J — KENAPA HARGA MASUK MENENTUKAN SEMUANYA.

    Bagian I menunjukkan masuk di OPEN besok menghapus seluruh alpha. Di sini
    dijelaskan MEKANISME-nya dan diuji apakah masih ada jalur masuk yang sah:
      (a) berapa besar celah buka, dan dari mana asalnya (satu malam, bukan tren);
      (b) ke mana harga bergerak SETELAH celah itu (fade intraday);
      (c) apakah masuk di CLOSE besok (setelah fade) masih bertahan sebagai alpha
          relatif KELAS likuiditas, di semua tahun, dan di kedua paruh;
      (d) aturan order yang bisa dijalankan: limit di harga close sinyal.
    """
    base = S["momentumkuat"].fillna(False)
    ok = base & S["open_next_ok"] & S["gap1"].notna()
    print("\n-- J. MEKANISME: dari mana alpha momentumkuat sebenarnya berasal --")
    print(f"  Sinyal momentumkuat: {int(base.sum()):,} · subsampel ber-OPEN: "
          f"{int(ok.sum()):,} ({100 * ok.sum() / max(base.sum(), 1):.1f}%)")
    yr = S.loc[ok, "date"].dt.year.value_counts().sort_index()
    print("  sebaran tahun subsampel ber-OPEN: " +
          " · ".join(f"{y}:{int(c):,}" for y, c in yr.items()))

    # (a) penguraian satu malam + hari sinyal
    gap = S.loc[ok, "gap1"].mean()
    fade = S.loc[ok, "oc1"].mean()
    print(f"\n  (a) Penguraian: close hari sinyal -> OPEN besok   : {gap:+.2f}%")
    print(f"      open besok -> close hari itu (fade intraday)  : {fade:+.2f}%")
    tot = (1 + gap / 100.0) * (1 + fade / 100.0) * 100.0 - 100.0
    print(f"      -> close hari sinyal -> close besok          : {tot:+.2f}%")
    print("      Artinya: alpha sebesar itu adalah SATU MALAM celah buka, bukan tren")
    print("      beberapa hari. Kalau celahnya tidak bisa Anda dapat, hampir tidak ada sisa.")

    # (b) sisa setelah celah, per jalur, relatif kelas
    print(f"\n  (b) Jalur masuk, alpha relatif KELAS yang sama (blok t dari rata-rata harian):")
    print(f"      {'jalur masuk':<40} {'abs%':>7} {'alphaK%':>8} {'blok t9':>8} {'net%':>7} {'%untung':>8}")
    for lab, col, exc, hz in (
            ("[lama] close[t] -> close[t+5]", "fwd5", "excg5", 5),
            ("open[t+1] -> close[t+1]  (kejar buka)", "oc1", "excg_oc1", 1),
            ("open[t+1] -> close[t+5]", "oc5", "excg_oc5", 5),
            ("close[t+1] -> close[t+2]  (tunggu 1 hari)", "cc1", "excg_cc1", 1),
            ("close[t+1] -> close[t+4]", "cc3", "excg_cc3", 3),
            ("close[t+1] -> close[t+6]", "cc5", "excg_cc5", 5)):
        per = S.loc[ok].groupby("date")[exc].mean()
        net = S.loc[ok, col] - COST_ROUND_TRIP * 100
        print(f"      {lab:<40} {S.loc[ok, col].mean():>+7.2f} {S.loc[ok, exc].mean():>+8.2f} "
              f"{block_t(per, 1 if hz <= 1 else max(hz, 3)):>+8.2f} {net.mean():>+7.2f} "
              f"{(net > 0).mean() * 100:>7.1f}%")

    # (c) apakah jalur close[t+1] bertahan lintas tahun dan lintas paruh?
    print(f"\n  (c) Ketahanan jalur tunggu-1-hari (close[t+1] -> close[t+4], alpha kelas):")
    sub = S.loc[ok].copy()
    sub["y"] = sub["date"].dt.year
    print(f"      {'tahun':<8} {'n':>7} {'open->close H+2':>16} {'close+1->close+4':>17} "
          f"{'blok t':>7}")
    for y, g in sub.groupby("y"):
        if len(g) < 200:
            continue
        per = g.groupby("date")["excg_cc3"].mean()
        print(f"      {y:<8} {len(g):>7,} {g['excg_oc2'].mean():>+16.2f} "
              f"{g['excg_cc3'].mean():>+17.2f} {block_t(per, 3):>+7.2f}")
    half = len(sub) // 2
    sub = sub.sort_values("date")
    for lab, g in (("paruh awal", sub.iloc[:half]), ("paruh akhir", sub.iloc[half:])):
        print(f"      {lab:<8} {len(g):>7,} {g['excg_oc2'].mean():>+16.2f} "
              f"{g['excg_cc3'].mean():>+17.2f} {'-':>7}")

    # (d) aturan order: limit di harga close sinyal (hanya terisi bila harga turun)
    print(f"\n  (d) Aturan order yang bisa dijalankan (limit di harga close sinyal):")
    print(f"      {'aturan':<44} {'n':>7} {'terisi':>7} {'alphaK H+3':>11} {'net%':>7}")
    for lab, cond in (("limit terisi bila open besok <= close sinyal", S["gap1"] <= 0),
                      ("open besok tidak naik >2%", S["gap1"] <= 2.0),
                      ("open besok tidak naik >5%", S["gap1"] <= 5.0),
                      ("semua (tanpa batas)", S["gap1"].notna())):
        m = ok & cond
        if int(m.sum()) < 100:
            continue
        per = S.loc[m].groupby("date")["excg_oc3"].mean()
        net = S.loc[m, "oc3"] - COST_ROUND_TRIP * 100
        print(f"      {lab:<44} {int(m.sum()):>7,} "
              f"{100 * m.sum() / ok.sum():>6.1f}% {S.loc[m, 'excg_oc3'].mean():>+11.2f} "
              f"{net.mean():>+7.2f}")
    # (f) SAMPEL PENUH (tanpa syarat harga OPEN) — jalur tunggu-1-hari diuji di
    # seluruh 2020-2026, bukan hanya di tahun-tahun yang punya harga pembukaan.
    full = base & S["fwd5"].notna()
    print(f"\n  (f) Sampel penuh {int(full.sum()):,} sinyal — jalur tunggu-1-hari vs asumsi lama:")
    print(f"      {'jalur':<34} {'abs%':>7} {'alphaK%':>8} {'blok t':>7} {'net%':>7} "
          f"{'%untung':>8} {'awal/akhir':>13}")
    for lab, col, exc, hz in (("[lama] close[t] -> close[t+5]", "fwd5", "excg5", 5),
                              ("tunggu: close[t+1] -> close[t+4]", "cc3", "excg_cc3", 3),
                              ("tunggu: close[t+1] -> close[t+6]", "cc5", "excg_cc5", 5)):
        m = full & S[col].notna()
        per = S.loc[m].groupby("date")[exc].mean()
        net = S.loc[m, col] - COST_ROUND_TRIP * 100
        h_aw, h_ak = holdout(S.loc[m, exc].reset_index(drop=True), hz)
        print(f"      {lab:<34} {S.loc[m, col].mean():>+7.2f} {S.loc[m, exc].mean():>+8.2f} "
              f"{block_t(per, hz):>+7.2f} {net.mean():>+7.2f} {(net > 0).mean() * 100:>7.1f}% "
              f"{h_aw:>+6.2f}/{h_ak:>+6.2f}")
    yrf = S.loc[full].copy()
    yrf["y"] = yrf["date"].dt.year
    print(f"      per tahun (alpha kelas), jalur tunggu H+3: " + " · ".join(
        f"{int(y)}: {g['excg_cc3'].mean():+.2f}" for y, g in yrf.groupby("y") if len(g) >= 200))

    # (e) ORDER LIMIT di harga close sinyal, menunggu sepanjang hari besok.
    # Kolom Low jauh lebih lengkap daripada Open, jadi aturan ini diuji pada sampel
    # yang jauh lebih besar dan mencakup semua tahun.
    print(f"\n  (e) Order limit di harga close sinyal (terisi bila LOW besok menyentuh):")
    lm = base & S["low_next_ok"] & S["lowratio1"].notna()
    yr2 = S.loc[lm, "date"].dt.year.value_counts().sort_index()
    print(f"      sinyal momentumkuat ber-LOW: {int(lm.sum()):,} "
          f"({100 * lm.sum() / max(int(base.sum()), 1):.1f}% dari seluruh sinyal)")
    print("      sebaran tahun: " + " · ".join(f"{y}:{int(c):,}" for y, c in yr2.items()))
    print(f"      {'limit harga':<26} {'terisi':>7} {'H+3 abs%':>9} {'alphaK%':>8} "
          f"{'blok t':>7} {'net%':>7} {'%untung':>8}")
    for cut, lab in ((0.0, "= close sinyal"), (-2.0, "2% di bawah close"),
                     (-5.0, "5% di bawah close"), (-8.0, "8% di bawah close")):
        m = lm & (S["lowratio1"] <= cut) & S["fwd3"].notna()
        if int(m.sum()) < 100:
            print(f"      {lab:<26} {int(m.sum()):>7,}  (sampel kecil)")
            continue
        per = S.loc[m].groupby("date")["excg3"].mean()
        net = S.loc[m, "fwd3"] - COST_ROUND_TRIP * 100
        print(f"      {lab:<26} {100 * m.sum() / lm.sum():>6.1f}% {S.loc[m, 'fwd3'].mean():>+9.2f} "
              f"{S.loc[m, 'excg3'].mean():>+8.2f} {block_t(per, 3):>+7.2f} "
              f"{net.mean():>+7.2f} {(net > 0).mean() * 100:>7.1f}%")
    # Pembanding: berapa hasilnya kalau limit itu TIDAK terisi alias kita tidak beli.
    lm3 = lm & S["fwd3"].notna()
    if int(lm3.sum()) >= 100:
        print(f"      {'[pembanding] semua sinyal ber-LOW':<26} {'100.0%':>7} "
              f"{S.loc[lm3, 'fwd3'].mean():>+9.2f} {S.loc[lm3, 'excg3'].mean():>+8.2f} "
              f"{block_t(S.loc[lm3].groupby('date')['excg3'].mean(), 3):>+7.2f} "
              f"{(S.loc[lm3, 'fwd3'] - COST_ROUND_TRIP * 100).mean():>+7.2f}")

    print("      (blok t3: " + " · ".join(
        f"{lab}: {block_t(S.loc[ok & cond].groupby('date')['excg_oc3'].mean(), 3):+.2f}"
        for lab, cond in (("limit", S["gap1"] <= 0), ("<=+2%", S["gap1"] <= 2.0)))
        + ")")


def report_exec(S: pd.DataFrame) -> None:
    """HARGA MASUK YANG BENAR-BENAR TERSEDIA (bagian I).

    Pertanyaan yang dijawab: kalau pemindaian dilakukan SETELAH bursa tutup (mis.
    18.00), harga entry apa yang realistis, dan apakah hasilnya masih ada setelah
    celah buka (gap) diperhitungkan?

    Kenapa ini wajib diuji dan tidak boleh diasumsikan: seluruh tabel sebelumnya
    mengukur close[t] -> close[t+h], yaitu MENGHARAPKAN kita bisa membeli di harga
    close hari sinyal. Itu tidak mungkin bila sinyal ditemukan malam hari. Di sini
    diukur tiga jalur masuk atas sinyal yang sama.
    """
    base = S["momentumkuat"].fillna(False)
    ok = base & S["open_next_ok"] & S["gap1"].notna()
    print(f"\n-- I. EKSEKUSI NYATA: masuk besok buka vs asumsi lama (close hari sinyal) --")
    print(f"  Sinyal momentumkuat: {int(base.sum()):,} · yang punya OPEN besok: "
          f"{int(ok.sum()):,} ({100 * ok.sum() / max(base.sum(), 1):.1f}%) · "
          f"rentang {S.loc[ok, 'date'].min().date()} -> {S.loc[ok, 'date'].max().date()}")

    for name, m in (("momentumkuat", ok),
                    ("buy>=70", S["buy>=70"].fillna(False) & S["open_next_ok"] & S["gap1"].notna()),
                    ("momentum (tanpa breakout)",
                     S["momentum>=8%"].fillna(False) & ~S["breakout"].fillna(False)
                     & S["open_next_ok"] & S["gap1"].notna())):
        if int(m.sum()) < 100:
            continue
        print(f"\n  {name}: n={int(m.sum()):,}")
        print(f"    {'jalur masuk':<34} {'abs%':>7} {'alphaK%':>8} {'blok t9':>8} "
              f"{'net%':>7} {'%untung':>8}")
        # Asumsi lama (tidak bisa dieksekusi bila memindai malam)
        r5 = S.loc[m, "fwd5"]
        print(f"    {'[asumsi lama] close hari sinyal':<34} {r5.mean():>+7.2f} "
              f"{S.loc[m, 'excg5'].mean():>+8.2f} {'-':>8} {r5.mean()-0.3:>+7.2f} "
              f"{(r5-0.3>0).mean()*100:>7.1f}%")
        # Jalur yang benar-benar bisa dieksekusi: masuk di OPEN besok
        for k, lab in ((1, "masuk OPEN besok -> close H+1"),
                       (2, "masuk OPEN besok -> close H+2"),
                       (3, "masuk OPEN besok -> close H+3"),
                       (5, "masuk OPEN besok -> close H+5")):
            col = f"oc{k}"
            per = S.loc[m].groupby("date")[col].mean()
            net = S.loc[m, col] - COST_ROUND_TRIP * 100
            print(f"    {lab:<34} {S.loc[m, col].mean():>+7.2f} {per.mean():>+8.2f} "
                  f"{block_t(per, max(k, 2)):>+8.2f} {net.mean():>+7.2f} "
                  f"{(net>0).mean()*100:>7.1f}%")
        # Menunggu sehari: masuk di CLOSE besok
        for k in (1, 3, 5):
            col = f"cc{k}"
            sub = S.loc[m & S[col].notna()]
            net = sub[col] - COST_ROUND_TRIP * 100
            print(f"    {'masuk CLOSE besok -> close H+'+str(k):<34} {sub[col].mean():>+7.2f} "
                  f"{sub.groupby('date')[col].mean().mean():>+8.2f} {'-':>8} "
                  f"{net.mean():>+7.2f} {(net>0).mean()*100:>7.1f}%")

    # Celah buka: seberapa besar, dan mulai berapa besar alpha-nya hilang?
    print(f"\n  CELAH BUKA (OPEN besok vs close hari sinyal) untuk momentumkuat:")
    g = S.loc[ok, "gap1"]
    q = g.quantile([0.10, 0.25, 0.50, 0.75, 0.90])
    print(f"    rata-rata {g.mean():+.2f}% · median {q[0.50]:+.2f}% · "
          f"p10 {q[0.10]:+.2f}% · p25 {q[0.25]:+.2f}% · p75 {q[0.75]:+.2f}% · p90 {q[0.90]:+.2f}%")
    print(f"    {'celah buka':<22} {'n':>7} {'lanjut H+1':>11} {'lanjut H+3':>11} {'lanjut H+5':>11} {'%untung H+3':>12}")
    buckets = [(-1e9, 0.0, "<= 0% (buka turun)"),
               (0.0, 2.0, "0 s/d +2%"),
               (2.0, 5.0, "+2% s/d +5%"),
               (5.0, 1e9, "> +5% (gap besar)")]
    for lo, hi, lab in buckets:
        sub = S.loc[ok & (S["gap1"] > lo) & (S["gap1"] <= hi)]
        if len(sub) < 50:
            print(f"    {lab:<22} {len(sub):>7,}  (sampel kecil)")
            continue
        net3 = sub["oc3"] - COST_ROUND_TRIP * 100
        print(f"    {lab:<22} {len(sub):>7,} {sub['oc1'].mean():>+11.2f} "
              f"{sub['oc3'].mean():>+11.2f} {sub['oc5'].mean():>+11.2f} "
              f"{(net3>0).mean()*100:>11.1f}%")
    print("\n  CATATAN: 'blok t9' memakai pembagi blok minimum 2 karena horizon pendek; "
          "'alphaK' pada jalur open TIDAK dikurangi celah buka, sedangkan kolom 'abs%' "
          "sudah termasuk celah itu (masuk di harga open, bukan di harga close kemarin).")


def report_short(S: pd.DataFrame) -> None:
    """Horizon PENDEK (1-3-5 hari) untuk kombinasi yang paling menjanjikan.

    Kenapa terpisah: pada horizon 1 hari, biaya 0,3% round-trip itu BESAR relatif
    terhadap alpha, dan yang penting bukan cuma rata-ratanya tetapi berapa persen
    kejadian yang BENAR-BENAR untung setelah biaya. Di sini ketiganya dilaporkan.
    """
    n_dates = S["date"].nunique()
    variants = [
        ("momentum>=8% (semua)", S["momentum>=8%"]),
        (("momentum>=8% & kelas >=CUKUP"), S["momentum>=8%"] & S["grade"].isin(LIQUID_GRADES)),
        (("momentum>=8% & kelas >=LIKUID"), S["momentum>=8%"] & S["grade"].isin(TOP_GRADES)),
        (("momentum>=10% & kelas >=CUKUP"), S["momentum>=10%"] & S["grade"].isin(LIQUID_GRADES)),
        ("buy>=70", S["buy>=70"]),
        ("buy>=70 & buang tiket", S["buy>=70"] & ~S["ticket_small"]),
        ("buy>=70 & tiket & bull", S["buy>=70"] & ~S["ticket_small"] & S["regime_bull"]),
        ("bsjp & buang tiket", S["bsjp"] & ~S["ticket_small"]),
        ("volsr & buang tiket", S["volsr"] & ~S["ticket_small"]),
        ("launchpad & buang tiket", S["launchpad"] & ~S["ticket_small"]),
    ]
    print(f"\n-- G. HORIZON PENDEK (1/2/3/5 hari), alpha vs KELAS + bersih biaya 0,3% --")
    for h in SHORT_HORIZONS:
        print(f"\n  horizon {h} hari   {'n':>8} {'/hari':>6} {'abs%':>7} {'alphaK%':>8} "
              f"{'blok t':>7} {'net%':>7} {'%untung':>8}")
        for label, m in variants:
            m = m.fillna(False)
            n = int(m.sum())
            if n < 200:
                print(f"  {label:<30} {n:>8,}  (sampel terlalu kecil)")
                continue
            per = S.loc[m].groupby("date")[f"excg{h}"].mean()
            abs_h = S.loc[m, f"fwd{h}"].mean()
            net = S.loc[m, f"fwd{h}"] - COST_ROUND_TRIP * 100
            print(f"  {label:<30} {n:>8,} {n / n_dates:>6.1f} {abs_h:>+7.2f} {per.mean():>+8.2f} "
                  f"{block_t(per, h):>+7.2f} {net.mean():>+7.2f} {(net > 0).mean() * 100:>7.1f}%")

    print(f"\n  CATATAN: 'net%' = return absolut dikurangi 0,3% round-trip. Pada horizon 1 hari,\n"
          f"  biaya itu memakan sebagian besar alpha, jadi '%untung' (persentase kejadian\n"
          f"  yang positif SETELAH biaya) lebih penting daripada rata-ratanya.")


def report_combos(S: pd.DataFrame) -> None:
    """Apakah MENGGABUNGKAN kriteria menambah alpha, atau cuma menumpuk syarat?

    Yang dibandingkan: tiap kriteria SENDIRI, IRISANNYA dengan momentum (dua-duanya
    benar), dan kasus momentum yang TIDAK punya pola itu. Kalau gabungan lebih baik
    daripada keduanya, ada sinergi; kalau irisan hanya mewarisi salah satu, itu
    penumpukan syarat (sampel mengecil tanpa imbalan).
    """
    conds = [
        ("momentum>=8%", S["momentum>=8%"]),
        ("launchpad", S["launchpad"]),
        ("volsr", S["volsr"]),
        ("breakout", S["breakout"]),
        ("buy>=70", S["buy>=70"]),
    ]
    combos = []
    for name, m in conds[1:]:
        mom = S["momentum>=8%"].fillna(False)
        mm = m.fillna(False)
        combos.append((name, mom, mm))

    print(f"\n-- H. GABUNGAN: momentum x pola lain (alpha H1/H5/H20 vs KELAS, blok t) --")
    for name, mom, other in combos:
        both = mom & other
        print(f"\n  {name}:  n(momentum)={int(mom.sum()):,} n({name})={int(other.sum()):,} "
              f"n(irisan)={int(both.sum()):,}")
        rows = [("momentum saja", mom & ~other), (f"{name} saja", other & ~mom),
                ("IRISAN keduanya", both)]
        for label, m in rows:
            n = int(m.sum())
            if n < 30:
                print(f"    {label:<20} {n:>7,}  (sampel terlalu kecil untuk disimpulkan)")
                continue
            cells = []
            for h in (1, 5, 20):
                per = S.loc[m].groupby("date")[f"excg{h}"].mean()
                cells.append(f"h{h} {per.mean():+6.2f} (t{block_t(per, h):+5.1f})")
            net5 = (S.loc[m, "fwd5"] - COST_ROUND_TRIP * 100).mean()
            print(f"    {label:<20} {n:>7,}  " + "  ".join(cells) + f"  net5 {net5:+.2f}%")
        if int(both.sum()) >= 30:
            # Apakah irisan benar-benar lebih baik, atau hanya mewarisi salah satu sisi?
            per_b = S.loc[both].groupby("date")["excg5"].mean()
            per_m = S.loc[mom & ~other].groupby("date")["excg5"].mean()
            per_o = S.loc[other & ~mom].groupby("date")["excg5"].mean()
            best_single = max(per_m.mean(), per_o.mean())
            verdict = ("ADA SINERGI (irisan > kedua sisi)" if per_b.mean() > best_single
                       else "TIDAK ADA SINERGI (irisan <= sisi terbaik; syarat cuma menumpuk)")
            print(f"    -> h5: irisan {per_b.mean():+.2f}% vs sisi terbaik {best_single:+.2f}%  => {verdict}")


COMBO_SIZES = []


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit head-to-head kriteria screener")
    ap.add_argument("--years", type=float, default=None,
                    help="batasi ke N tahun terakhir (default: seluruh cache)")
    ap.add_argument("--min-grade", default=None,
                    choices=["CUKUP", "LIKUID", "SANGAT LIKUID"])
    ap.add_argument("--min-bars", type=int, default=60)
    ap.add_argument("--short", action="store_true",
                    help="hanya jalankan tabel horizon pendek (1/2/3/5 hari)")
    ap.add_argument("--combos", action="store_true",
                    help="hanya jalankan uji gabungan momentum x pola lain")
    ap.add_argument("--exec", action="store_true",
                    help="hanya jalankan uji eksekusi nyata (masuk di open besok)")
    ap.add_argument("--exec2", action="store_true",
                    help="mekanisme celah buka + aturan order (bagian J)")
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
    if args.short:
        # Horizon pendek: cukup buang baris yang fwd5 belum tersedia.
        S = S[S["fwd5"].notna()].reset_index(drop=True)
        report_short(S)
        return
    if args.combos:
        S = S[S["fwd20"].notna()].reset_index(drop=True)
        report_combos(S)
        return
    if args.exec:
        S = S[S["fwd5"].notna()].reset_index(drop=True)
        report_exec(S)
        return
    if args.exec2:
        S = S[S["fwd5"].notna()].reset_index(drop=True)
        report_exec(S)
        report_exec2(S)
        return
    S = S[S["fwd20"].notna()].reset_index(drop=True)
    report_criteria(S, args.min_grade, args.years)
    report_short(S)


if __name__ == "__main__":
    main()
