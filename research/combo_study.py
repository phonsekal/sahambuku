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

  # Bagian E: kalibrasi per kelas vs global, out-of-sample (0 kuota IDX)
  .venv/bin/python research/combo_study.py --part calib

  # Bagian G: audit SCALPING & BSJP (0 kuota IDX)
  .venv/bin/python research/combo_study.py --part audit

  # Bagian H: audit bonus skor beli Launch Pad / Drop Base Rally (0 kuota IDX)
  .venv/bin/python research/combo_study.py --part special

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

BAGIAN E — KALIBRASI PER KELAS: TIDAK LEBIH BAIK. JANGAN DIULANG.
  Pertanyaan: produksi memakai SATU koefisien global (6,6712 / 0,3681) lalu ambang
  berbeda per kelas. Apakah lebih baik menaksir koefisiennya PER KELAS?

  Desain: koefisien DAN ambang hanya dipelajari dari PARUH AWAL tanggal
  (2021-12 -> 2024-02), seluruh penilaian di PARUH AKHIR (2024-02 -> 2026-06).
  Tiga varian: (A) konstanta produksi apa adanya, (B) global ditaksir ulang,
  (C) per kelas likuiditas.

  JEBAKAN YANG HARUS DIHINDARI: ambang tetap yang dipelajari di paruh awal
  memotong 25% kandidat di paruh akhir untuk varian A tetapi 31% untuk varian C.
  Dibandingkan begitu saja, varian C tampak unggul di SANGAT LIKUID
  (+1,71% vs +1,26%) — dan itu SELURUHNYA artefak kedalaman, bukan mutu regresi.
  Setelah kedalaman disamakan (potong tepat 20% lintas-saham per tanggal):
    SANGAT LIKUID  A +1,45% (blok +4,00) | B +1,48% (blok +4,06) | C +1,40% (+4,17)
    LIKUID         A +3,37% (blok +16,16)| B +3,36% (blok +16,30)| C +3,37% (+15,87)
    CUKUP          A +3,74% (blok +17,43)| B +3,74% (blok +17,22)| C +3,48% (+15,52)
  -> A dan B setara; C setara atau LEBIH BURUK (CUKUP). Jadi kalibrasi per kelas
     tidak menambah apa pun. Koefisien produksi juga TIDAK basi (A = B).
  Sebab teknis: rentang v20 DI DALAM satu kelas itu sempit, jadi regresi per kelas
  berkondisi buruk dan koefisiennya liar (SANGAT LIKUID 2,06/0,573 vs CUKUP
  10,10/0,207) — padahal keduanya menaksir hubungan yang sama.
  KEPUTUSAN: PERTAHANKAN satu regresi global + ambang per kelas. Jangan ganti.

  DUA TEMUAN SAMPINGAN YANG BERGUNA:
  1. Menyaring kelas KURANG LIKUID MERUGIKAN, kini terbukti out-of-sample:
     +6,26% -> +5,94..+6,09% di ketiga varian. Ini alasan kuat kenapa kelas itu
     memang tidak disentuh di produksi (bukan sekadar "belum diuji").
  2. Ambang TETAP produksi kalah dari potong-20%-lintas-saham di ketiga kelas
     (mis. LIKUID +3,04% vs +3,37%). Artinya ambangnya melenceng seiring waktu,
     karena distribusi residual bergeser. Produksi tidak bisa menghitung kuintil
     lintas-saham saat menganalisis satu saham, jadi perbaikannya adalah
     penkalibrasian ulang berkala.
     SUDAH DIKERJAKAN: scripts/calibrate_ticket_thresholds.py menghitung kuintil-20
     per kelas dari 250 hari bursa terakhir (sumber IDX resmi, tanpa kuota pihak
     ketiga) dan menulis api/ticket_thresholds.json, yang otomatis menggantikan
     angka bawaan saat aplikasi dimuat (fallback ke bawaan bila berkas hilang/rusak).
     .github/workflows/ticket-thresholds.yml menjalankannya tiap tanggal 1.
     Contoh hasil pertama (15 Sep 2026): SANGAT -0,5176 -> -0,5073;
     LIKUID -0,5056 -> -0,5591; CUKUP -0,2853 -> -0,2938.
     CATATAN: jendela 250 hari jauh lebih stabil daripada 90 hari (yang memberi
     -0,585/-0,622/-0,359) — jangan kalibrasi dari jendela terlalu pendek.
     Angka di atas BELUM di-backtest ulang; yang terbukti adalah bahwa potong-20%
     mengalahkan ambang tetap, bukan bahwa ambang hasil kalibrasi ini optimal.

BAGIAN B — akumulator diam-diam: TIDAK BISA DIUJI, bukan "gagal".
  Data broker hanya tersedia 80-90 hari terakhir dan yang SEGAR mulai
  2026-05-13, sedangkan R (backtest) berakhir 2026-06-22 karena horizon 60 hari
  membuang 60 sesi terakhir. Irisannya cuma **23 tanggal** -> apa pun hasilnya
  tidak berarti. Menambah `recent_days` besar hanya memasukkan tanggal BASI
  (saham tidak aktif) dan membuat irisan tampak lebar padahal palsu.
  Kesimpulan: kombinasi tiket x akumulator-diam-diam tidak bisa diuji dari sini;
  yang bisa dikatakan hanya bahwa akumulator diam-diam SENDIRI sudah tidak punya
  daya prediksi (research/bandar_study.py, 77 hari).

BAGIAN G — AUDIT SCALPING & BSJP (kriteria yang belum pernah diukur).
  Keduanya murni kondisi harga+volume, jadi bisa direplikasi 5 tahun (berbeda dari
  BANDAR yang butuh data broker). 833.343 saham-hari, 897 emiten, 1.081 tanggal.

  JEBAKAN YANG HAMPIR MENYESATKAN SAYA: alpha diukur terhadap SELURUH pasar, dan
  rata-rata pasar itu dinaikkan oleh mikro-cap yang premnya besar di IDX. Jadi
  "alpha negatif" TIDAK berarti kandidatnya turun. Return absolut (yang menentukan
  bagi trader) menunjukkan hal yang berlawanan arah:
                     abs1      abs5      abs20
    seluruh pasar   +0,07%    +0,42%    +1,69%
    SCALPING pasar  +0,13%    +1,06%    +1,86%
    SCALPING+likuid+tiket +0,51% +1,84%  +1,96%
    BSJP pasar      -0,19%    +0,39%    +1,59%
    BSJP+likuid+tiket +0,53%  +1,53%    +2,77%
  Kesimpulan: kedua kriteria TIDAK membuang uang. Yang benar-benar bermasalah hanya
  satu hal spesifik di bawah ini.

  1) PREMIS BSJP GAGAL DI SELURUH PASAR. BSJP = "beli sore, jual pagi", jadi ukuran
     yang relevan adalah return HARI BERIKUTNYA. Di seluruh pasar: abs1 -0,19%
     (baseline +0,07%), excess -0,49% t=-3,51 -> NYATA dan negatif. Artinya membeli
     saat close lalu menjual pagi harinya RATA-RATA RUGI. Baru setelah dibatasi ke
     SANGAT LIKUID + buang tiket kecil, abs1 berbalik positif (+0,53%).

  2) DI DALAM universe SANGAT LIKUID keduanya memang positif (pembanding dibatasi
     ke kelas yang sama, jadi bukan premi ukuran):
       h5 : SCALPING penuh +0,82% (blok +2,09) -> +buang tiket +1,55% (blok +5,31)
            BSJP penuh     +0,72% (blok +2,13) -> +buang tiket +1,48% (blok +3,68)
       h20: SCALPING+buang tiket +3,05% (blok +2,90); BSJP+buang tiket +3,87% (+4,94)
     Holdout h5 kedua kriteria: KEDUA paruh positif. Jadi penyaring tiket kurang
     lebih MENGGANDAKAN hasilnya (+0,82 -> +1,55 dan +0,72 -> +1,48).

  3) SYARAT TAMBAHANNYA NYARIS TIDAK BERGUNA. Di SANGAT LIKUID: hanya syarat
     momentum (return harian >= 10%) sudah memberi h5 +0,73% (blok +2,11);
     SCALPING penuh (yang menambah nilai >= Rp1 M & harga > 50) hanya +0,82%.
     Jadi ambang nilai/harga itu tidak menambah apa pun yang berarti.

  KEPUTUSAN: TIDAK ada kode produksi yang diubah untuk kriteria ini. Yang ditemukan
  bukan bug, melainkan (a) premis BSJP lemah di seluruh pasar -- itu sifat strategi,
  bukan kesalahan implementasi, dan (b) ambang nilai/harga yang mubazir. Keduanya
  perlu keputusan produk (batasi ke likuid akan membuat daftarnya nyaris selalu
  kosong), bukan perubahan senyap. Datanya ada di sini kalau mau diputuskan.

BAGIAN H — AUDIT BONUS SKOR BELI YANG HANYA ADA DI JALUR LIVE (buku Bab 6.2/6.3).
  Bukti buku (PDF Coachinvestasi) yang diuji di sini: "Special Pattern 1: The Launch
  Pad" (uptrend dulu -> rentang menyempit -> breakout base dengan volume) dan
  "Special Pattern 2: Drop Base Rally" (turun dalam -> base -> rally). Keduanya
  dipakai api/index.py sebagai bonus skor beli (+10), tetapi komentar kode sendiri
  mengakui keduanya "belum bisa di-backtest". 25 dari 100 poin skor beli ternyata
  diberikan tanpa bukti. Sekarang bisa: syaratnya direplikasi vektor point-in-time,
  dan offsetnya DIVERIFIKASI bar-per-bar terhadap fungsi live (10.286 bar x 9 emiten,
  0 selisih — salah satu versi uji pertama meleset 7 sinyal karena shift(35)
  seharusnya shift(34)). 897 emiten, 833.423 saham-hari, 1.081 tanggal.

  1) LAUNCH PAD (replika produksi): PUNYA DAYA PREDIKSI. n=182 di SANGAT LIKUID,
     alpha5 +2,40% (blok t+2,64), alpha20 +6,05% (blok t+2,52); ABSOLUT abs20 +5,06%
     sementara baseline SANGAT LIKUID -0,37%. Holdout DUA paruh positif, termasuk
     horizon 20 hari (+9,79% t+2,2 dan +3,03% t+1,0). Kejadiannya jarang: 182 dari
     833.423 saham-hari (~1,2/hari se-pasar), jadi memang untuk diburu.

  2) DROP BASE RALLY: TIDAK ADA BUKTI. n=446, alpha20 +0,97% (blok t+0,64), ABSOLUT
     abs20 -0,86% (LEBIH BURUK dari baseline), dan holdout paruh AWAL negatif
     (-0,97% t-0,4). Bonusnya karena itu diturunkan ke 0 poin; polanya tetap dihitung
     dan ditampilkan sebagai informasi.

  3) VERSI BUKU YANG LEBIH KETAT TIDAK LEBIH BAIK. Syarat buku tambahan (sudah naik
     >= 20% sebelum base + base tidak boleh menembus high/low jendela sebelumnya)
     hanya memunculkan 44 kejadian dengan blok t+0,69 -> versi produksi dipertahankan
     (ambang 15%). Pelajaran umum: memperketat syarat "biar sesuai buku" memangkas
     sampel 76% tanpa memperbaiki hasil.

  4) EFEK BONUS PADA PERINGKAT SKOR: KECIL. Dari 22.900 saham-hari berskor >= 70,
     bonus +10 hanya menambah 94 (0,4%), dan Top-10 peringkatnya identik jumlahnya.
     Kelompok "masuk HANYA karena bonus" alpha20 +3,97% (blok t+1,42) — positif tapi
     tidak signifikan. Artinya bobot 10 itu aman-aman saja, bukan pengungkit besar;
     yang penting adalah polanya TIDAK salah diberi poin saat tidak ada bukti.

  KEPUTUSAN (dipakai di produksi): bonus Launch Pad tetap 10, bonus Drop Base Rally
  jadi 0, dan "The Launch Pad" dinaikkan statusnya menjadi KRITERIA SCREENER
  ("launchpad") supaya pola terkuat di aplikasi bisa dicari se-pasar, bukan hanya
  terlihat saat satu saham dianalisis.

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


# ---------------------------------------------------------------------------
# 3c. BAGIAN E — KALIBRASI REGRESI TIKET: GLOBAL vs PER KELAS LIKUIDITAS
# ---------------------------------------------------------------------------
# Produksi sekarang memakai SATU koefisien global (6,6712 / 0,3681) lalu ambang
# yang berbeda per kelas. Pertanyaan: apakah lebih baik menaksir koefisiennya
# PER KELAS, sehingga residualnya sebanding antar kelas?
#
# Desain (supaya tidak menilai diri sendiri): koefisien DAN ambang dipelajari
# hanya dari PARUH AWAL tanggal, lalu seluruh penilaian dilakukan di PARUH AKHIR
# yang sama sekali tidak dilihat saat menaksir. Tiga varian dibandingkan:
#   (A) konstanta produksi apa adanya (6,6712 / 0,3681)
#   (B) global, ditaksir ulang di paruh awal
#   (C) per kelas likuiditas, ditaksir di paruh awal

GRADE_EDGES = [-1, 100e6, 1e9, 10e9, 1e15]
GRADE_LABELS = ["KURANG LIKUID", "CUKUP", "LIKUID", "SANGAT LIKUID"]
GRADE_BANDS = {g: (GRADE_EDGES[i], GRADE_EDGES[i + 1])
               for i, g in enumerate(GRADE_LABELS)}


def _ols_log(rows: pd.DataFrame):
    """Regresi log(tiket) ~ log(v20). Mengembalikan (intercept, slope) atau None."""
    if rows is None or len(rows) < 500:
        return None
    x = np.log(rows["v20"].clip(lower=1).astype(float))
    y = np.log(rows["ticket"].clip(lower=1).astype(float))
    ok = np.isfinite(x) & np.isfinite(y)
    if int(ok.sum()) < 500:
        return None
    slope, intercept = np.polyfit(x[ok], y[ok], 1)
    return float(intercept), float(slope)


def _resid(df: pd.DataFrame, coef, grade_coef=None) -> pd.Series:
    """Residual; grade_coef (dict kelas->koefisien) mengalahkan coef bila ada."""
    lv = np.log(df["v20"].clip(lower=1).astype(float))
    lt = np.log(df["ticket"].clip(lower=1).astype(float))
    out = lt - (coef[0] + coef[1] * lv)
    if grade_coef:
        for g, c in grade_coef.items():
            if c is None:
                continue
            m = (df["grade"] == g).to_numpy()
            if m.any():
                out = out.where(~m, lt - (c[0] + c[1] * lv))
    return out


def part_calib(years: int, workers: int, top: int) -> None:
    print("== BAGIAN E: kalibrasi regresi tiket — global vs per kelas (OOS) ==")
    prod_coef = (TICKET_REG_INTERCEPT, TICKET_REG_SLOPE)

    M.BUDGET = 0
    T = build_ticket(M.fetch_range(years, workers, False))
    ih, data = B.load_data(years, "all", workers)
    frames = [B.build_rows(tk, df, ih) for tk, df in data.items()]
    R = pd.concat([f for f in frames if f is not None], ignore_index=True)
    R = attach_ticket(R, T)
    R["date"] = pd.to_datetime(R["date"]).dt.normalize()
    R["grade"] = pd.cut(R["v20"], GRADE_EDGES, labels=GRADE_LABELS)
    have = R["v20"].notna() & R["ticket"].notna() & (R["grade"].notna())

    ds = np.sort(R.loc[have, "date"].unique())
    mid = ds[len(ds) // 2]
    tr = R[have & (R["date"] < mid)]
    te = R[have & (R["date"] >= mid)].copy()
    print(f"  latih {len(tr):,} baris ({tr['date'].min().date()} s/d {tr['date'].max().date()})")
    print(f"  uji   {len(te):,} baris ({te['date'].min().date()} s/d {te['date'].max().date()})")

    coef_global = _ols_log(tr)
    coef_class = {g: _ols_log(tr[tr["grade"] == g]) for g in GRADE_LABELS}
    print(f"\n  koefisien (intercept, slope):")
    print(f"    produksi apa adanya : {prod_coef[0]:.4f}, {prod_coef[1]:.4f}")
    if coef_global:
        print(f"    global (paruh awal) : {coef_global[0]:.4f}, {coef_global[1]:.4f}")
    for g in GRADE_LABELS:
        c = coef_class[g]
        print(f"    {g:<15}     : " + ("— (sampel kurang)" if c is None
              else f"{c[0]:.4f}, {c[1]:.4f}"))

    variants = [("A produksi", prod_coef, None), ("B global-refit", coef_global, None),
                ("C per kelas", coef_global, coef_class)]
    for label, _c, _gc in variants:
        te["r_" + label[0]] = _resid(te, _c, _gc)
    # Ambang 20% terendah per kelas, DIPELAJARI DARI PARUH AWAL saja.
    thr = {}
    for label, c, gc in variants:
        trr = tr.copy()
        trr["rr"] = _resid(trr, c, gc)
        thr[label[0]] = {g: float(trr.loc[trr["grade"] == g, "rr"].quantile(0.2))
                         for g in GRADE_LABELS if (trr["grade"] == g).sum() > 500}

    print(f"\n  ambang 20% per kelas (dari paruh AWAL):")
    for label, _c, _gc in variants:
        print(f"    {label:<14} " + ", ".join(f"{g[:6]}={v:+.3f}" for g, v in thr[label[0]].items()))

    print(f"\n  --- IC residual vs exc, diukur HANYA di paruh AKHIR ---")
    print(f"  {'kelas':<15} {'varian':<14} {'blok IC h5':>16} {'blok IC h20':>16}")
    for g in GRADE_LABELS:
        S = te[te["grade"] == g]
        if len(S) < 5000:
            continue
        for label, _c, _gc in variants:
            col = "r_" + label[0]
            ic5, t5, _ = M.ic_by_date(S, col, "exc5", stride=5)
            ic20, t20, _ = M.ic_by_date(S, col, "exc20", stride=20)
            print(f"  {g:<15} {label:<14} {ic5:>+10.4f}(t{t5:+4.1f}) {ic20:>+10.4f}(t{t20:+4.1f})")
        print()

    print("  --- alpha filter di paruh AKHIR (pembanding = kelas yang sama) ---")
    for g in GRADE_LABELS:
        S = te[te["grade"] == g]
        if len(S) < 5000:
            continue
        gidx = S.index
        uni = pd.Series(False, index=R.index); uni.loc[gidx] = True
        print(f"\n   [{g}] n={len(S):,}")
        specs = [("  RS Leader (tanpa filter)", (S["leader"] == 1))]
        for label, _c, _gc in variants:
            # .astype("object") penting: map() pada kolom Categorical mengembalikan
            # Categorical lagi, dan membandingkannya dengan angka melempar TypeError.
            lim = S["grade"].astype("object").map(thr[label[0]])
            small = (S["r_" + label[0]] <= lim).fillna(False)
            specs.append((f"  + filter {label}", (S["leader"] == 1) & ~small))
        # PENTING — kedalaman filter harus DISAMAKAN. Ambang tetap yang dipelajari
        # di paruh awal bisa memotong 25% di paruh akhir untuk satu varian dan 31%
        # untuk varian lain; perbedaan alpha lalu berasal dari KEDALAMAN, bukan dari
        # mutu regresinya. Karena itu tiap varian juga diuji pada kuintil-20
        # lintas-saham PER TANGGAL (memotong tepat 20% di tanggal itu).
        specs.append(("", None))
        for label, _c, _gc in variants:
            col = "r_" + label[0]
            q20 = S.groupby("date")[col].transform(lambda s: s.quantile(0.2))
            specs.append((f"  + filter {label} (kedalaman 20%)",
                          (S["leader"] == 1) & (S[col] > q20).fillna(False)))

        uni_te = uni.reindex(te.index).fillna(False)
        for lab, m in specs:
            if m is None:
                print("    (kedalaman disamakan — memotong tepat 20% per tanggal)")
                continue
            mm_full = pd.Series(False, index=R.index)
            mm_full.loc[m[m].index] = True
            ev = B.evaluate(R, mm_full, lab, universe_mask=uni)
            r = next((x for x in ev if x["h"] == 20), None)
            if not r:
                print(f"    {lab:<36} —")
                continue
            m_te = m.reindex(te.index).fillna(False)
            bt = block_t(te, m_te, uni_te, 20)
            print(f"    {lab:<36} n={r['n']:>6,} alpha {r['alpha']:>+6.2f}% "
                  f"t={r['t']:>+5.2f} blok {bt:>+6.2f}")


# ---------------------------------------------------------------------------
# 3d. BAGIAN G — AUDIT KRITERIA SCREENER YANG BELUM PERNAH DIUJI
# ---------------------------------------------------------------------------
# SCALPING dan BSJP adalah dua kriteria buku yang sudah lama ada di aplikasi tetapi
# BELUM PERNAH diukur daya prediksinya. Keduanya murni kondisi harga+volume, jadi
# bisa direplikasi di backtest 5 tahun (beda dengan BANDAR yang butuh data broker).

def scalp_bsjp_flags(tk: str, df: pd.DataFrame) -> pd.DataFrame:
    """Replika persis kondisi harga/volume SCALPING dan BSJP (api/index.py).

    SCALPING : nilai >= Rp1 M  & return harian >= 10% & harga > 50
    BSJP     : nilai >= Rp5 M  & return harian >= 8%  & volume >= 2x VolumeMA20
    `nilai` = close nominal x volume -- sama dengan jalur cadangan aplikasi ketika
    kolom Value dari API tidak ada (di backtest memang tidak ada).
    """
    c = df["Close"].astype(float)
    v = df["Volume"].astype(float).fillna(0.0)
    val = c * v
    ret = c.pct_change() * 100.0
    vr = v / v.rolling(20).mean().replace(0, np.nan)
    out = pd.DataFrame({
        "scalping": ((val >= 1e9) & (ret >= 10.0) & (c > 50)).fillna(False),
        "bsjp": ((val >= 5e9) & (ret >= 8.0) & (vr >= 2.0)).fillna(False),
    })
    out["tk"] = tk
    return out.reset_index().rename(columns={"index": "date"})


def part_audit(years: int, workers: int, top: int) -> None:
    print("== BAGIAN G: audit SCALPING & BSJP (kriteria yang belum pernah diukur) ==")

    M.BUDGET = 0
    T = build_ticket(M.fetch_range(years, workers, False))
    ih, data = B.load_data(years, "all", workers)
    frames = []
    for tk, df in data.items():
        r = B.build_rows(tk, df, ih)
        if r is None:
            continue
        r["date"] = pd.to_datetime(r["date"]).dt.normalize()
        f = scalp_bsjp_flags(tk, df)
        f["date"] = pd.to_datetime(f["date"]).dt.normalize()
        frames.append(r.merge(f, on=["tk", "date"], how="left"))
    R = pd.concat(frames, ignore_index=True)
    R = attach_ticket(R, T)
    have = R["v20"].notna()
    R["small"] = R["small"].fillna(False)
    for col in ("scalping", "bsjp"):
        R[col] = R[col].fillna(False)
    print(f"  observasi: {len(R):,} saham-hari, {R['code'].nunique()} emiten, "
          f"{R['date'].nunique()} tanggal ({R['date'].min().date()} s/d {R['date'].max().date()})")

    sgt = have & (R["v20"] >= SGT_LIKUID)
    print("\n  jumlah kandidat (dari total saham-hari):")
    for col in ("scalping", "bsjp"):
        n = int(R[col].sum())
        print(f"    {col:<9} {n:>7,} ({n/len(R)*100:.2f}%)  · di SANGAT LIKUID: "
              f"{int((R[col] & sgt).sum()):,}")

    # Pembanding = SELURUH pasar (kriteria ini tidak mensyaratkan likuiditas), jadi
    # universe_mask = semua True. Harus Series boolean, bukan DataFrame.
    uni = pd.Series(True, index=R.index)
    for col in ("scalping", "bsjp"):
        specs = [
            (f"{col.upper()} (semua pasar)", R[col]),
            (f"{col.upper()} + SANGAT LIKUID", R[col] & sgt),
            (f"{col.upper()} + buang tiket kecil", R[col] & ~R["small"]),
            (f"{col.upper()} + SANGAT LIKUID + tiket", R[col] & sgt & ~R["small"]),
        ]
        print(f"\n=== {col.upper()} ===")
        compare(R, specs, uni, horizons=(1, 5, 20))
        print(f"\n  Holdout paruh waktu ({col.upper()}):")
        for h in (5, 20):
            for lab, m in specs:
                print(f"    {lab:<34} h{h:<2} {holdout_split(R, m, uni, h)}")
        print(f"\n  Alpha per tahun ({col.upper()}, horizon 5):")
        for lab, m in specs[:2]:
            print(f"    {lab:<34} {per_year(R, m, uni, 5)}")

    # Kendali penting: apakah "naik >=10% hari ini" saja sudah cukup? Kalau ya,
    # syarat nilai/harga/volume tidak menambah apa pun.
    # PENTING: "alpha negatif" != "kandidatnya turun". Pembanding seluruh pasar
    # didominasi mikro-cap yang premnya besar, jadi kriteria bisa kalah dari rata-rata
    # pasar TETAPI tetap naik secara absolut. Bagi trader, absolut yang menentukan.
    print("\n=== RETURN ABSOLUT (bukan excess) — apakah kandidatnya naik? ===")
    abs_rows = [("seluruh pasar (baseline)", pd.Series(True, index=R.index))]
    for col in ("scalping", "bsjp"):
        abs_rows.append((col.upper() + " (semua pasar)", R[col]))
        abs_rows.append((col.upper() + " + SANGAT LIKUID", R[col] & sgt))
        abs_rows.append((col.upper() + " + SANGAT LIKUID + tiket", R[col] & sgt & ~R["small"]))
    print(f"  {'variasi':<36} {'n':>7} {'abs1':>7} {'abs5':>7} {'abs20':>7}")
    for lab, m in abs_rows:
        sel = R.loc[m.fillna(False)]
        if not len(sel):
            print(f"  {lab:<36} {'-':>7}")
            continue
        v = " ".join(f"{sel[f'abs{h}'].mean():>+6.2f}%" for h in (1, 5, 20))
        print(f"  {lab:<36} {len(sel):>7,} {v}")

    print("\n=== KENDALI: apakah syarat tambahan menambah sesuatu? (seluruh pasar) ===")
    ctrl = R["ret"] * 100.0 if "ret" in R.columns else None
    if ctrl is not None:
        specs = [
            ("tanpa syarat apa pun (seluruh pasar)", pd.Series(True, index=R.index)),
            ("hanya return harian >= 10%", (ctrl >= 10.0).fillna(False)),
            ("hanya return >= 10% & volume >= 2x",
             ((ctrl >= 10.0) & (R["vr"] >= 2.0)).fillna(False)),
            ("SCALPING penuh", R["scalping"]),
            ("BSJP penuh", R["bsjp"]),
        ]
        compare(R, specs, uni, horizons=(1, 5))

    # KELEMBAPAN PENTING: pada uji di atas pembandingnya SELURUH pasar, sehingga
    # "menang" bisa cuma premi ukuran (saham likuid memang mengalahkan mikro-cap).
    # Di sini pembandingnya DIBATASI ke SANGAT LIKUID, jadi yang terukur adalah
    # apakah kriteria itu menambah sesuatu DI DALAM kelas yang bisa ditransaksikan.
    print("\n=== UJI ULANG DI DALAM UNIVERSE SANGAT LIKUID (pembanding = SANGAT LIKUID) ===")
    Rl = R[sgt]
    unil = pd.Series(True, index=Rl.index)
    if ctrl is not None:
        cl = ctrl.loc[Rl.index]
        specs = [
            ("SANGAT LIKUID (tanpa kriteria)", unil),
            ("hanya return harian >= 10%", (cl >= 10.0).fillna(False)),
            ("hanya return >= 10% & volume >= 2x",
             ((cl >= 10.0) & (Rl["vr"] >= 2.0)).fillna(False)),
            ("SCALPING penuh", Rl["scalping"]),
            ("SCALPING + buang tiket kecil", Rl["scalping"] & ~Rl["small"]),
            ("BSJP penuh", Rl["bsjp"]),
            ("BSJP + buang tiket kecil", Rl["bsjp"] & ~Rl["small"]),
        ]
        compare(Rl, specs, unil, horizons=(1, 5, 20))
        print("\n  Holdout paruh waktu (di dalam SANGAT LIKUID):")
        for h in (5, 20):
            for lab, m in specs:
                print(f"    {lab:<34} h{h:<2} {holdout_split(Rl, m, unil, h)}")


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


# ---------------------------------------------------------------------------
# 3e. BAGIAN H — AUDIT BONUS SKOR BELI YANG HANYA ADA DI JALUR LIVE
# ---------------------------------------------------------------------------
# api/index.py punya BUY_SCORE_BONUS_WEIGHTS:
#     "Launch Pad / Drop Base Rally": 10
#     "Bandarmology ACC (bila ada)":  15
# Komentar di kode itu sendiri mengakui keduanya "belum bisa di-backtest sebagai deret
# waktu (fungsi live, bukan vektor)". Artinya 25 dari 100 poin skor beli diberikan
# TANPA bukti — padahal setiap komponen lain di skor yang sama sudah lewat audit.
#
# Di sini keduanya dibuat bisa diuji: syarat pola direplikasi vektor point-in-time,
# lalu diukur (1) daya prediksi polanya sendiri, (2) apakah bonus +10 benar-benar
# memperbaiki peringkat skor beli, dan (3) versi BUKU yang lebih ketat apakah lebih
# baik daripada versi produksi. Bonus Bandarmology (+15) tidak diuji di sini karena
# butuh riwayat Broker Summary yang cuma 80 hari — sudah tersimpulkan di
# research/bandar_study.py bahwa kriteria BANDAR tidak menunjukkan daya prediksi.

def special_pattern_flags(tk: str, df: pd.DataFrame) -> pd.DataFrame:
    """Replika vektor `launch_pad()` & `drop_base_rally()` api/index.py, point-in-time.

    Offsetnya HARUS sama dengan jalur live (di sana dipakai .iloc[-N:]):
      Launch Pad : base 15 bar (t-14..t), jendela pembanding 15 bar (t-29..t-15),
                   prior gain = close[t-15]/close[t-34]-1, breakout = close[t] >
                   max(high[t-14..t-1]), volume >= 1,5x VolumeMA20.
      Drop Base  : base 10 bar (t-9..t), pembanding low 15 bar (t-24..t-10),
                   prior drop = close[t-10]/close[t-24]-1, tanpa lower low, breakout.
    Offset ini sudah diverifikasi bar-per-bar terhadap fungsi live di api/index.py
    (8.112 bar, 7 emiten): 0 selisih. Salah offset satu bar saja langsung
    menghasilkan sinyal palsu, jadi jangan diubah tanpa menjalankan ulang uji itu.

    Varian BUKU yang lebih ketat (Bab 6.2/6.3): prior gain >= 20% (bukan 15%),
    tidak ada high/low base yang melebihi high/low jendela sebelumnya, dan pada Drop
    Base Rally volume merah di fase base tidak dominan ("hindari penurunan harga
    dengan volume merah yang tinggi besar").
    """
    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    v = df["Volume"].astype(float).fillna(0.0)
    vr = v / v.rolling(20).mean().replace(0, np.nan)

    # --- The Launch Pad (Bab 6.2) ---
    base_hi, base_lo = h.rolling(15).max(), l.rolling(15).min()
    prev_hi, prev_lo = h.rolling(15).max().shift(15), l.rolling(15).min().shift(15)
    contraction = (base_hi - base_lo) / (prev_hi - prev_lo).replace(0, np.nan)
    base_hi_excl_last = h.rolling(14).max().shift(1)
    prior_gain = (c.shift(15) / c.shift(34) - 1.0) * 100.0
    broke = c > base_hi_excl_last

    lp_prod = ((prior_gain >= 15) & (contraction <= 0.8) & broke & (vr >= 1.5)).fillna(False)
    lp_forming = ((prior_gain >= 15) & (contraction <= 0.85)).fillna(False)
    no_break = (base_hi <= prev_hi) & (base_lo >= prev_lo)
    lp_book = ((prior_gain >= 20) & (contraction <= 0.8) & no_break & broke
               & (vr >= 1.5)).fillna(False)

    # --- Drop Base Rally (Bab 6.3) ---
    prior_drop = (c.shift(10) / c.shift(24) - 1.0) * 100.0
    no_new_ll = l.rolling(10).min() >= l.rolling(15).min().shift(10) * 0.995
    rallied = c > h.rolling(9).max().shift(1)
    dbr_prod = ((prior_drop <= -10) & no_new_ll & rallied & (vr >= 1.5)).fillna(False)
    red = (c < c.shift(1)).fillna(False)
    red_vol_share = (v.where(red, 0.0).rolling(10).mean()
                     / v.rolling(10).mean().replace(0, np.nan))
    dbr_book = (dbr_prod & (red_vol_share <= 0.5).fillna(False)).fillna(False)

    out = pd.DataFrame({
        "lp_prod": lp_prod, "lp_forming": lp_forming, "lp_book": lp_book,
        "dbr_prod": dbr_prod, "dbr_book": dbr_book,
    })
    out["tk"] = tk
    return out.reset_index().rename(columns={"index": "date"})


def part_special(years: int, workers: int) -> None:
    print("== BAGIAN H: audit bonus skor beli jalur live (Launch Pad / Drop Base Rally) ==")

    ih, data = B.load_data(years, "all", workers)
    frames = []
    for tk, df in data.items():
        r = B.build_rows(tk, df, ih)
        if r is None:
            continue
        r["date"] = pd.to_datetime(r["date"]).dt.normalize()
        f = special_pattern_flags(tk, df)
        f["date"] = pd.to_datetime(f["date"]).dt.normalize()
        frames.append(r.merge(f, on=["tk", "date"], how="left"))
    R = pd.concat(frames, ignore_index=True)
    pats = ("lp_prod", "lp_forming", "lp_book", "dbr_prod", "dbr_book")
    for col in pats:
        R[col] = R[col].fillna(False)
    print(f"  observasi: {len(R):,} saham-hari, {R['tk'].nunique()} emiten, "
          f"{R['date'].nunique()} tanggal ({R['date'].min().date()} s/d {R['date'].max().date()})")

    # Universe = SANGAT LIKUID (nilai 20 hari >= Rp10 M), sama seperti Bagian A/G.
    # Dipakai val20 dari backtest (harga Adj x volume) supaya tidak bergantung cache IDX.
    sgt = (R["val20"] >= 10e9).fillna(False)
    uni = sgt
    Rl = R[sgt].copy()
    unil = pd.Series(True, index=Rl.index)

    print("\n  jumlah kemunculan pola (seluruh pasar vs SANGAT LIKUID):")
    for col in pats:
        print(f"    {col:<11} {int(R[col].sum()):>7,}  ·  {int((R[col] & sgt).sum()):>6,}")

    uni_all = pd.Series(True, index=R.index)
    specs = [
        ("Launch Pad (replika produksi)", R["lp_prod"]),
        ("Launch Pad + SANGAT LIKUID", R["lp_prod"] & sgt),
        ("Launch Pad versi BUKU (ketat)", R["lp_book"] & sgt),
        ("Drop Base Rally (produksi)", R["dbr_prod"] & sgt),
        ("Drop Base Rally versi BUKU", R["dbr_book"] & sgt),
        ("gabungan produksi (LP|DBR)", (R["lp_prod"] | R["dbr_prod"]) & sgt),
        ("(kontrol) base menyempit saja", R["lp_forming"] & sgt),
    ]
    print("\n=== daya prediksi pola (pembanding = SANGAT LIKUID) ===")
    compare(Rl, [(lab, m.loc[Rl.index]) for lab, m in specs], unil, horizons=(5, 20))

    print("\n  Holdout paruh waktu di dalam SANGAT LIKUID:")
    for h in (5, 20):
        for lab, m in specs:
            print(f"    {lab:<34} h{h:<2} {holdout_split(Rl, m.loc[Rl.index], unil, h)}")

    # Return ABSOLUT: pembanding SANGAT LIKUID bisa saja turun, jadi alpha positif
    # belum tentu berarti kandidatnya naik (pelajaran dari Bagian G).
    print("\n=== RETURN ABSOLUT di dalam SANGAT LIKUID ===")
    print(f"  {'variasi':<34} {'n':>7} {'abs5':>8} {'abs20':>8} {'>0 (5h)':>8}")
    base = Rl
    rows = [("SANGAT LIKUID (baseline)", unil)] + \
           [(lab, m.loc[Rl.index]) for lab, m in specs]
    for lab, m in rows:
        sel = base.loc[m.fillna(False)]
        if not len(sel):
            print(f"  {lab:<34} {'-':>7}")
            continue
        print(f"  {lab:<34} {len(sel):>7,} {sel['abs5'].mean():>+7.2f}% "
              f"{sel['abs20'].mean():>+7.2f}% {(sel['abs5'] > 0).mean() * 100:>7.1f}%")

    # --- inti Bagian H: apakah bonus +10 memperbaiki PERINGKAT skor beli? ---
    print("\n=== pengaruh bonus +10 pada skor beli aplikasi (vektor, tanpa bonus ACC) ===")
    bs = R["buy_score"].fillna(0.0)
    bonus = 10.0 * (R["lp_prod"] | R["dbr_prod"]).astype(float)
    Rl = Rl.copy()
    Rl["bs_plain"] = bs.loc[Rl.index]
    Rl["bs_bonus"] = (bs.loc[Rl.index] + bonus.loc[Rl.index]).clip(upper=100.0)
    masuk_hanya_karena_bonus = (Rl["bs_bonus"] >= 70) & (Rl["bs_plain"] < 70)
    sudah_lolos = (Rl["bs_plain"] >= 70)
    print(f"  skor >= 70 tanpa bonus : {int(sudah_lolos.sum()):,} saham-hari")
    print(f"  skor >= 70 dengan bonus: {int((Rl['bs_bonus'] >= 70).sum()):,} "
          f"(+{int(masuk_hanya_karena_bonus.sum()):,} masuk HANYA karena bonus)")

    bs_specs = [
        ("skor >= 70 (tanpa bonus)", sudah_lolos),
        ("skor >= 70 (dengan bonus +10)", Rl["bs_bonus"] >= 70),
        ("  masuk HANYA karena bonus", masuk_hanya_karena_bonus),
        ("Top-10 skor (tanpa bonus)", B.top_n_mask(Rl, "bs_plain", 10)),
        ("Top-10 skor (dengan bonus)", B.top_n_mask(Rl, "bs_bonus", 10)),
    ]
    compare(Rl, bs_specs, pd.Series(True, index=Rl.index), horizons=(5, 20))
    print("\n  Holdout paruh waktu (efek bonus):")
    for h in (5, 20):
        for lab, m in bs_specs:
            print(f"    {lab:<34} h{h:<2} {holdout_split(Rl, m, pd.Series(True, index=Rl.index), h)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Uji kombinasi filter tiket x akumulator diam-diam")
    ap.add_argument("--part", default="ticket",
                    choices=["ticket", "silent", "swing", "bands", "calib", "audit",
                             "special"])
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
    elif args.part == "calib":
        part_calib(args.years, args.workers, args.top)
    elif args.part == "audit":
        part_audit(args.years, args.workers, args.top)
    elif args.part == "special":
        part_special(args.years, args.workers)
    else:
        part_silent(args.years, args.workers, args.top, args.universe, args.win,
                    args.codes, args.recent_days)


if __name__ == "__main__":
    main()
