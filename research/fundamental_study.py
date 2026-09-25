#!/usr/bin/env python3
"""Uji aturan FUNDAMENTAL (Peter Lynch, *One Up on Wall Street*) pada panel IDX lokal
— 0 kuota, 0 jaringan (membaca cache hasil `fundamentals_pull.py`).

Pertanyaan yang dijawab
-----------------------
`BOOK_METHODS.md` §5 mencatat kategorisasi Lynch sebagai "belum bisa dipakai" karena
aplikasi ini tidak punya data fundamental. Cache fundamental sekarang sudah ada, jadi
pertanyaannya berubah menjadi: **apakah aturan Lynch benar-benar menambah alpha di IDX,
atau ia hanya nasihat yang masuk akal tetapi tidak terukur?**

Yang diuji (dan definisinya ditulis di kode ini, bukan dikutip):
  * Lima-enam kategori Lynch: fast grower, stalwart, slow grower, turnaround,
    asset play, cyclical (proksi).
  * Aturan operasional Lynch: **PEG < 1** (hal 198-199), dan "P/E jauh di atas
    pertumbuhan = tanda bahaya" (hal 199) — keduanya diukur apa adanya.
  * Faktor tunggal: P/E, P/B, ROE, earnings yield, pertumbuhan laba & pendapatan,
    ukuran (market cap) — supaya kalau kategorinya gagal, kelihatan FAKTOR mana yang
    gagal, bukan hanya labelnya.

Kenapa hasilnya harus dibaca dengan hati-hati (batas yang disebut di laporan)
---------------------------------------------------------------------------
1. Cakupannya adalah **emiten yang laporannya sudah ditarik ke cache**. Setelah
   `fundamentals_pull.py` menuntaskan pasar, itu ~928 dari 989 emiten panel — cukup
   untuk disebut pasar. Kalau cache-nya belum lengkap, laporan bagian A akan menyebut
   angka sebenarnya; memakai 117 emiten (kondisi cache sebelum penarikan penuh)
   MENGHASILKAN KESIMPULAN YANG BERBEDA, jadi jumlahnya selalu dicetak, tidak diingat.
2. Angka P/E & P/B memakai laporan **tahunan (FY)**, bukan TTM; ekuitas diambil buku
   apa adanya (tanpa penyesuaian aset tak berwujud). Pertumbuhan YoY baru bisa
   dihitung sejak FY2023 terbit.
3. Tidak ada kolom sektor, jadi "cyclical" hanya PROKSI volatilitas laba.
4. Tidak ada data dividen — Lynch mengandalkan dividen untuk slow grower, dan di sini
   yang diukur hanya return harga.

Point-in-time (ini yang membuat angkanya bukan "lihat belakang")
---------------------------------------------------------------
* Laporan FY Y baru dipakai setelah **30 April Y+1** (batas pelaporan 4 bulan OJK).
* Jumlah saham beredar diambil dari snapshot `market-cap` TERAKHIR yang tanggalnya
  <= tanggal baris — jadi tidak ada informasi dari masa depan.
* Return ke depan dihitung dari harga tutup panel, dan SELALU dibandingkan dengan
  kelas likuiditas yang sama pada tanggal yang sama (`criteria_audit.add_excess`),
  supaya efek "mikro-cap naik lebih tinggi" tidak terhitung sebagai keunggulan faktor.

Jalankan:
    .venv/bin/python research/fundamental_study.py
    .venv/bin/python research/fundamental_study.py --limit 200    # uji cepat
    .venv/bin/python research/fundamental_study.py --codes BBCA,BBRI,TLKM,ASII
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

import panel as P                      # noqa: E402
import index as A                      # noqa: E402  <- kode PRODUKSI, bukan replika
import criteria_audit as CA            # noqa: E402  <- metrik & pembanding yang sama
import fundamentals_pull as FP         # noqa: E402  <- pembaca cache (tanpa jaringan)

COST = CA.COST_ROUND_TRIP              # 0,3% putar dua arah, sama dengan /api/backtest

# Batas pelaporan tahunan IDX/OJK: 4 bulan setelah tutup buku (31 Desember) -> 30 April.
# Dipakai sebagai tanggal AMAN, bukan tanggal terbit sebenarnya (API tidak menyimpannya).
# Konsekuensinya sampel lebih pendek, dan itu disengaja: menebak tanggal terbit lebih awal
# akan memasukkan angka yang belum beredar ke dalam pengukuran.
PUB_MONTH, PUB_DAY = 4, 30

FACTORS: Dict[str, str] = {
    "pe": "P/E tahunan (harga ÷ EPS FY)",
    "pb": "P/B (market cap ÷ ekuitas)",
    "roe": "ROE (laba ÷ ekuitas)",
    "eps_yield": "Earnings yield (EPS ÷ harga)",
    "ni_growth": "Pertumbuhan laba YoY",
    "rev_growth": "Pertumbuhan pendapatan YoY",
    "peg": "PEG (P/E ÷ pertumbuhan laba)",
    "log_mcap": "Ukuran (log market cap)",
}
# Harapan tanda IC (korelasi peringkat dgn alpha masa depan). Dipakai untuk menilai
# apakah faktor bergerak ke arah yang masuk akal, bukan untuk memilih arah setelah
# melihat hasil: -1 = makin KECIL makin baik (P/E, P/B rendah; PEG <1), +1 = sebaliknya.
FACTOR_DIR: Dict[str, int] = {
    "pe": -1, "pb": -1, "peg": -1, "roe": +1, "eps_yield": +1,
    "ni_growth": +1, "rev_growth": +1, "log_mcap": 0,
}

MIN_BARS = 120      # bar minimum sebelum faktor dihitung (butuh v20)
MIN_VALID_Q = 25    # nilai valid minimum per tanggal sebelum kuantil dibentuk


# ---------------------------------------------------------------------------
# 1. BACA CACHE FUNDAMENTAL (via fungsi fundamentals_pull, 0 jaringan)
# ---------------------------------------------------------------------------

def load_annuals() -> Dict[str, Dict[int, Dict[str, float]]]:
    """{kode: {tahun: {field: nilai}}} dari laporan TAHUNAN di cache.

    Memakai `FP.load_all_statements()` apa adanya supaya definisi pemilihan angka
    (jalur "a > b > c", dst) tidak punya salinan kedua yang bisa berbeda.
    """
    out: Dict[str, Dict[int, Dict[str, float]]] = {}
    for r in FP.load_all_statements():
        if (r.get("quarter") or "").upper() != "FY":
            continue
        try:
            year = int(r.get("year"))
        except (TypeError, ValueError):
            continue
        code = str(r.get("code") or "").upper().strip()
        if not code:
            continue
        slot = out.setdefault(code, {}).setdefault(year, {})
        for field in ("net_income", "revenue", "eps", "equity", "assets", "liabilities"):
            v = r.get(field)
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                slot[field] = float(v)
    return out


def load_shares() -> pd.DataFrame:
    """Jumlah saham beredar per (tanggal snapshot, kode) — point-in-time.

    Index = tanggal snapshot, kolom = kode. Dipakai lewat `reindex(method="ffill")`
    sehingga tiap baris harga memakai jumlah saham TERAKHIR yang sudah diketahui
    pada tanggal itu (bukan jumlah saham hari ini).
    """
    rows = FP.load_all_mcap()
    if not rows:
        raise SystemExit("Cache market-cap kosong — jalankan research/fundamentals_pull.py dulu.")
    d = pd.DataFrame(rows)
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    d["code"] = d["code"].astype(str).str.upper().str.strip()
    d["listed_shares"] = pd.to_numeric(d["listed_shares"], errors="coerce")
    d = d.dropna(subset=["listed_shares"])
    d = d[d["listed_shares"] > 0]
    return d.pivot_table(index="date", columns="code", values="listed_shares", aggfunc="max")


def published_year(asof: pd.Timestamp, years: List[int]) -> Optional[int]:
    """Tahun fiskal TERAKHIR yang sudah terbit pada tanggal `asof`."""
    ok = [y for y in years if asof >= pd.Timestamp(year=y + 1, month=PUB_MONTH, day=PUB_DAY)]
    return max(ok) if ok else None


# ---------------------------------------------------------------------------
# 2. BANGUN SATU BARIS PER (SAHAM, TANGGAL) — faktor + return ke depan
# ---------------------------------------------------------------------------

def build(codes: Optional[List[str]] = None, verbose: bool = True) -> pd.DataFrame:
    panel = P.load_panel()
    ann = load_annuals()
    shares = load_shares()

    want = sorted(ann) if not codes else [c for c in codes if c in ann]
    rows: List[pd.DataFrame] = []
    for i, code in enumerate(want):
        g = panel[panel["code"] == code]
        if len(g) < MIN_BARS:
            continue
        df = P.to_ohlcv(g)
        close = df["Close"].astype(float)
        op = df["Open"].astype(float)
        value = A._value_series(df).astype(float)
        v20 = value.rolling(20).mean()
        ylist = sorted(ann[code])

        f = pd.DataFrame(index=df.index)
        f["code"] = code
        f["date"] = df.index
        f["close"] = close
        f["v20"] = v20.to_numpy()
        f["grade"] = [CA._liquidity_class(float(v)) if pd.notna(v) else None
                      for v in v20.to_numpy()]
        f["day_ret"] = ((close / close.shift(1) - 1.0) * 100.0).to_numpy()

        # --- point-in-time: tahun fiskal yang sudah terbit + jumlah saham terakhir ---
        uy = np.array([published_year(d, ylist) for d in df.index], dtype=object)
        f["fy"] = uy
        sh = shares[code].reindex(df.index, method="ffill") if code in shares.columns \
            else pd.Series(np.nan, index=df.index)
        f["listed_shares"] = sh.to_numpy()

        def series(field: str, year_arr) -> np.ndarray:
            return np.array([(ann[code].get(int(y)) or {}).get(field)
                             if y is not None else None for y in year_arr], dtype=float)

        def prev_series(field: str, year_arr) -> np.ndarray:
            """Nilai field untuk FY SETAHUN SEBELUM tahun yang sedang dipakai."""
            out = np.full(len(year_arr), np.nan)
            for k, y in enumerate(year_arr):
                if y is None:
                    continue
                prev = int(y) - 1
                if prev in ann[code]:
                    v = ann[code][prev].get(field)
                    if v is not None:
                        out[k] = float(v)
            return out

        eps = series("eps", uy)
        ni = series("net_income", uy)
        rev = series("revenue", uy)
        eq = series("equity", uy)
        eps_p = prev_series("eps", uy)
        ni_p = prev_series("net_income", uy)
        rev_p = prev_series("revenue", uy)

        mcap = f["close"].to_numpy(float) * f["listed_shares"].to_numpy(float)
        f["market_cap"] = mcap
        with np.errstate(all="ignore"):
            f["pe"] = np.where(eps > 0, close.to_numpy(float) / np.where(eps > 0, eps, np.nan), np.nan)
            f["pb"] = np.where(eq > 0, mcap / np.where(eq > 0, eq, np.nan), np.nan)
            f["roe"] = np.where(eq > 0, ni / np.where(eq > 0, eq, np.nan), np.nan)
            f["eps_yield"] = np.where(eps > 0, eps / close.to_numpy(float), np.nan)
            f["ni_growth"] = ((ni - ni_p) / np.abs(ni_p).clip(min=1e-9)) * 100.0
            f["rev_growth"] = ((rev - rev_p) / np.abs(rev_p).clip(min=1e-9)) * 100.0
            f["peg"] = np.where(f["ni_growth"] > 0, f["pe"] / f["ni_growth"], np.nan)
            f["log_mcap"] = np.log(np.where(mcap > 0, mcap, np.nan))
        # Pertumbuhan tanpa pembanding tahun sebelumnya = kosong, bukan 0: nol berarti
        # "labanya benar-benar tidak berubah", dan itu klaim yang berbeda.
        f.loc[[y is None for y in uy], "ni_growth"] = np.nan
        f["ni_prev"] = ni_p
        f["ni"] = ni

        # --- volatilitas laba per emiten (proksi "cyclical", batasnya disebut di laporan) ---
        grows = []
        for a, b in zip(ylist, ylist[1:]):
            if a + 1 != b:
                continue
            na, nb = ann[code][a].get("net_income"), ann[code][b].get("net_income")
            if na and nb is not None and na != 0:
                grows.append((nb - na) / abs(na) * 100.0)
        f["growth_vol"] = float(np.std(grows, ddof=1)) if len(grows) >= 2 else np.nan

        # --- return ke depan (kolom yang dibaca CA.add_excess apa adanya) ---
        for h in CA.ALL_HORIZONS:
            f[f"fwd{h}"] = (close.shift(-h) / close - 1.0).to_numpy() * 100.0
        nxt_ok = (op.shift(-1) > 0) & (op.shift(-1) < close * 5)
        nxt = op.shift(-1).where(nxt_ok)
        f["fwd1_open"] = ((nxt / close - 1.0) * 100.0).to_numpy()
        f["open_next_ok"] = nxt_ok.fillna(False).to_numpy(bool)
        rows.append(f)
        if verbose and (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{len(want)} emiten")

    if not rows:
        raise SystemExit("Tidak ada emiten yang bisa diukur — cache laporan kosong?")
    S = pd.concat(rows, ignore_index=True)
    S["year"] = pd.to_datetime(S["date"]).dt.year
    S = CA.add_excess(S)
    S = add_lynch_flags(S)
    return S


# ---------------------------------------------------------------------------
# 3. KATEGORI LYNCH SEBAGAI TANDA PER BARIS
#
# Ambangnya ditulis di sini, dan sengaja BUKAN "hasil tuning": kalau angka akhirnya
# diubah setelah melihat hasil, studi ini berubah jadi pencarian pola di masa lalu.
# Ukuran bersifat RELATIF DI DALAM sampel (median/kuantil per tanggal), jadi
# "besar" berarti besar di antara 119 emiten ini — bukan besar di seluruh IDX.
# ---------------------------------------------------------------------------

def add_lynch_flags(S: pd.DataFrame) -> pd.DataFrame:
    g = S.groupby("date")
    S["mcap_rank"] = g["market_cap"].rank(pct=True)
    S["pb_rank"] = g["pb"].rank(pct=True)
    S["cycl_rank"] = g["growth_vol"].rank(pct=True)
    gr, size = S["ni_growth"], S["mcap_rank"]
    # fast grower: pertumbuhan laba tinggi (Lynch: 20-25%/tahun ke atas).
    S["fast_grower"] = (gr >= 20.0).fillna(False)
    # stalwart: besar & tumbuh sedang (Lynch: ~10-12%/tahun).
    S["stalwart"] = (size >= 0.5) & (gr >= 8.0) & (gr < 20.0)
    # slow grower: besar & tumbuh lambat.
    S["slow_grower"] = (size >= 0.5) & (gr >= 0.0) & (gr < 8.0)
    # turnaround: tahun lalu rugi, tahun ini laba.
    S["turnaround"] = ((S["ni_prev"] < 0) & (S["ni"] > 0)).fillna(False)
    # asset play: harga murah terhadap nilai buku (PROKSI dari "ada sesuatu yang
    # berharga yang tidak dilihat pasar" — Lynch sendiri mengakui ini soal penilaian).
    S["asset_play"] = (S["pb_rank"] <= 0.20).fillna(False)
    # cyclical: PROKSI volatilitas laba (tidak ada data sektor).
    S["cyclical"] = (S["cycl_rank"] >= 2.0 / 3.0).fillna(False)
    for c in ("fast_grower", "stalwart", "slow_grower", "turnaround", "asset_play", "cyclical"):
        S[c] = S[c].fillna(False).astype(bool)
    # Aturan operasional Lynch (hal 198-199), diukur apa adanya.
    S["peg_lt_1"] = (S["peg"] < 1.0).fillna(False)
    S["pe_above_growth"] = ((S["pe"] > 0) & (S["ni_growth"] > 0)
                            & (S["pe"] > S["ni_growth"])).fillna(False)
    S["fast_peg_lt_1"] = S["fast_grower"] & S["peg_lt_1"]
    return S


# ---------------------------------------------------------------------------
# 4. METRIK — sama dengan studi lain (alpha vs kelas likuiditas, blok t, holdout)
# ---------------------------------------------------------------------------

def row_stats(S: pd.DataFrame, mask: pd.Series, h: int = 5, min_n: int = 30,
              base: Optional[pd.Series] = None) -> Dict:
    """Alpha lawan kelas likuiditas + blok t + holdout, untuk satu tanda (mask).

    `base` = baris yang MEMANG boleh dinilai (mis. hanya tanggal yang sudah punya
    pertumbuhan YoY). Tanpa itu, "/hari" dihitung atas seluruh 1.610 tanggal panel,
    padahal syaratnya baru ada sejak 2024 — angkanya lalu tampak "jarang" padahal
    tidak. Ini kesalahan yang ketahuan saat membaca hasil pertama, bukan setelahnya.
    """
    mask = mask.fillna(False)
    n = int(mask.sum())
    if n < min_n:
        return {"n": n, "enough": False}
    sub = S.loc[mask]
    per = sub.groupby("date")[f"excg{h}"].mean()
    # Ukuran tahan-outlier dipinjam dari criteria_audit.median_excess supaya definisinya
    # hanya SATU untuk seluruh riset ini (studi ini dan audit aturan lama memakai fungsi
    # yang sama) — penjelasan kenapa harus "selisih median", bukan median mentah, ada di
    # sana.
    per_med = CA.median_excess(S, mask, h, base)
    ha, hb = CA.holdout(per, h)
    ha_m, hb_m = CA.holdout(per_med, h)
    nd = (S.loc[base, "date"].nunique() if base is not None else S["date"].nunique())
    return {
        "n": n, "enough": True, "days": int(nd),
        "per_day": n / max(1, nd),
        "alpha": float(per.mean()), "t": CA.block_t(per, h),
        "early": ha, "late": hb,
        "alpha_med": float(per_med.mean()), "t_med": CA.block_t(per_med, h),
        "early_med": ha_m, "late_med": hb_m,
        "net": float(sub[f"fwd{h}"].mean() - COST * 100),
        "win": float((sub[f"fwd{h}"] > COST * 100).mean() * 100),
        "years_pos": int(sum(
            1 for y in sorted(S["year"].unique())
            if int((mask & (S["year"] == y)).sum()) >= 20
            and S.loc[mask & (S["year"] == y)].groupby("date")[f"excg{h}"].mean().mean() > 0)),
        "years": int(sum(1 for y in sorted(S["year"].unique())
                         if int((mask & (S["year"] == y)).sum()) >= 20)),
    }


def ic_series(S: pd.DataFrame, col: str, h: int = 5, outcome: str = "excg") -> pd.Series:
    """IC per tanggal: korelasi PERINGKAT antara faktor & alpha masa depan.

    Peringkat dihitung dulu lalu dikorelasikan dengan Pearson — itu definisi korelasi
    Spearman, dan ditulis begini karena scipy tidak ada di venv proyek ini (jangan
    menambah dependensi hanya untuk satu angka).
    """
    d = S[["date", col, f"{outcome}{h}"]].dropna()
    if d.empty:
        return pd.Series(dtype=float)
    d = d.copy()
    d["_rx"] = d.groupby("date")[col].rank()
    d["_ry"] = d.groupby("date")[f"{outcome}{h}"].rank()
    out = d.groupby("date").apply(
        lambda x: x["_rx"].corr(x["_ry"]) if len(x) >= MIN_VALID_Q else np.nan,
        include_groups=False)
    out.name = col
    return out.dropna()


def quintile_spread(S: pd.DataFrame, col: str, h: int) -> Tuple[Optional[pd.DataFrame], float]:
    """Tabel alpha per kuantil + deret Q5-Q1 per tanggal (untuk blok t)."""
    d = S[["date", col, f"excg{h}", f"fwd{h}"]].dropna()
    d = d.groupby("date").filter(lambda x: len(x) >= MIN_VALID_Q)
    if d.empty:
        return None, float("nan")
    d = d.copy()
    d["q"] = d.groupby("date")[col].transform(
        lambda x: pd.qcut(x.rank(method="first"), 5, labels=False, duplicates="drop"))
    per_date = d.groupby(["q", "date"])[f"excg{h}"].mean().unstack(0)
    per_med = d.groupby(["q", "date"])[f"excg{h}"].median().unstack(0)
    if per_date.shape[1] < 2:
        return None, float("nan")
    tab = pd.DataFrame({
        "alpha": per_date.mean(),
        "median": per_med.mean(),
        "net": d.groupby("q")[f"fwd{h}"].mean() - COST * 100,
        "n": d.groupby("q").size(),
    })
    tab.index.name = "kuantil"
    spread = per_date[per_date.columns.max()] - per_date[per_date.columns.min()]
    return tab, spread.dropna()


def fmt_q_table(S: pd.DataFrame, col: str) -> None:
    for h in (5, 20):
        tab, spread = quintile_spread(S, col, h)
        if tab is None:
            print(f"    (tidak cukup nilai valid untuk kuantil h{h})")
            continue
        cells = "  ".join(f"Q{int(q)+1} {v:+.2f}" for q, v in tab["alpha"].items())
        medc = "  ".join(f"Q{int(q)+1} {v:+.2f}" for q, v in tab["median"].items())
        t = CA.block_t(spread, h)
        print(f"    h{h} rata-rata : {cells}   Q5-Q1 {spread.mean():+.2f}% (blok t {t:+.2f})")
        print(f"    h{h} median    : {medc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Uji aturan fundamental Peter Lynch di IDX")
    ap.add_argument("--codes", default="", help="subset kode (koma); kosong = semua yang ada di cache")
    ap.add_argument("--limit", type=int, default=None, help="batasi jumlah emiten (uji cepat)")
    args = ap.parse_args()
    codes = [c.strip().upper() for c in args.codes.split(",") if c.strip()] or None
    if codes is None and args.limit:
        codes = sorted(load_annuals())[: args.limit]

    print("Membangun panel fundamental (membaca cache, 0 permintaan) ...")
    S = build(codes)
    nd = S["date"].nunique()

    print(f"\n{'='*118}\nA. CAKUPAN — angka di bawah ini HARUS dibaca bersama bagian ini\n{'='*118}")
    print(f"  {len(S):,} saham-hari · {S['code'].nunique()} emiten · {nd} tanggal · "
          f"{S['date'].min().date()} -> {S['date'].max().date()}")
    n_panel = len(P.load_panel()["code"].unique())
    print(f"  Emiten dengan laporan di cache: {S['code'].nunique()} (dari "
          f"{len(load_annuals())} yang laporannya ada; {n_panel} emiten di panel harga)")
    print("  Selisihnya = emiten yang laporannya belum ditarik / bar harganya kurang dari")
    print("  MIN_BARS. " + ("Sampel ini sudah mencakup hampir seluruh panel." if
          S["code"].nunique() >= 0.8 * n_panel else
          "Sampel ini BARU SEBAGIAN pasar — perluas cache sebelum menyimpulkan "
          "'rata-rata IDX'."))
    print(f"  Laporan tahunan yang tersedia : FY "
          f"{min(int(y) for c in load_annuals().values() for y in c)}.."
          f"{max(int(y) for c in load_annuals().values() for y in c)} "
          f"(P/E & P/B memakai FY, bukan TTM; terbit paling lambat 30 April tahun berikutnya)")
    print("  EPS/ekuitas negatif DIKELUARKAN dari faktornya (bukan diisi nol, karena nol berarti"
          " 'sangat murah' dan itu klaim palsu): "
          f"P/E kosong {int(S['pe'].isna().sum()):,}, P/B kosong {int(S['pb'].isna().sum()):,}, "
          f"pertumbuhan laba kosong {int(S['ni_growth'].isna().sum()):,} baris")
    print(f"  Tanggal pertama yang sudah punya P/E & P/B point-in-time: "
          f"{S.loc[S['pe'].notna(), 'date'].min().date() if S['pe'].notna().any() else '-'}")
    print(f"  Tanggal pertama yang sudah punya pertumbuhan YoY (butuh FY-1 terbit): "
          f"{S.loc[S['ni_growth'].notna(), 'date'].min().date() if S['ni_growth'].notna().any() else '-'}")
    print("  Perbandingan selalu: return saham - rata-rata kelas likuiditas yang SAMA di")
    print("  tanggal itu (excg), jadi keunggulan kelas mikro-cap tidak otomatis terhitung.")

    # ------------------------------------------------------------------ B. IC
    print(f"\n{'='*118}\nB. DAYA PREDIKSI FAKTOR (IC peringkat per tanggal, horizon 5 & 20 hari)\n{'='*118}")
    print("  IC = korelasi peringkat faktor hari itu dengan alpha 5/20 hari ke depan, dirata-rata")
    print("  per tanggal. Harapan tanda: P/E & P/B rendah (+) / PEG rendah (+), ROE & yield (+).")
    print(f"  {'faktor':<38} {'n tgl':>7} {'IC h5':>8} {'t':>7} {'IC h20':>8} {'t':>7}  arah")
    for col, label in FACTORS.items():
        ic5 = ic_series(S, col, 5)
        ic20 = ic_series(S, col, 20)
        if ic5.empty:
            print(f"  {label:<38} {0:>7}        -       -        -       -")
            continue
        t5 = CA.block_t(ic5, 5)
        t20 = CA.block_t(ic20, 20) if not ic20.empty else float("nan")
        dirn = {+1: "besar=baik", -1: "kecil=baik", 0: "-"}[FACTOR_DIR[col]]
        print(f"  {label:<38} {len(ic5):>7} {ic5.mean():>+8.3f} {t5:>+7.2f} "
              f"{(ic20.mean() if not ic20.empty else float('nan')):>+8.3f} {t20:>+7.2f}  {dirn}")
    print("\n  KONTROL UKURAN: IC h5 setelah efek ukuran-dalam-kelas dikeluarkan (excog5).")
    print("  Kalau angkanya mirip kolom di atas, faktor itu memang faktor — bukan ukuran yang menyamar.")
    print("  Bandingkan juga dgn 'Ukuran' di atas: di sampel ini yang menang justru yang LEBIH besar,")
    print("  jadi hasil murah/berkualitas di bawah TIDAK bisa dijelaskan sebagai efek mikro-cap.")
    print(f"  {'faktor':<38} {'n tgl':>7} {'IC ukuran-dibuang':>18} {'t':>7}  {'IC penuh':>9}")
    for col, label in FACTORS.items():
        ico = ic_series(S, col, 5, outcome="excog")
        ic5 = ic_series(S, col, 5)
        if ico.empty:
            continue
        print(f"  {label:<38} {len(ico):>7} {ico.mean():>+18.3f} "
              f"{CA.block_t(ico, 5):>+7.2f}  {ic5.mean():>+9.3f}")

    # -------------------------------------------------------------- C. KUANTIL
    print(f"\n{'='*118}\nC. BENTUK HUBUNGAN (rata-rata alpha tiap kuantil faktor, Q1 = terendah)\n{'='*118}")
    for col, label in FACTORS.items():
        print(f"  {label}")
        fmt_q_table(S, col)

    # ------------------------------------------------------------ D. KATEGORI
    print(f"\n{'='*118}\nD. KATEGORI LYNCH — apakah tiap kategori benar-benar berbeda hasilnya?\n{'='*118}")
    print("  'hari' = banyak tanggal yang MEMANG bisa dinilai (syaratnya sudah ada); '/hari' dihitung")
    print("  atas angka itu, bukan atas seluruh 1.610 tanggal panel. Pembanding tiap baris adalah")
    print("  baris yang sama-sama bisa dinilai — bukan seluruh sampel dari 2020.")
    print(f"  {'kategori':<34} {'n':>7} {'hari':>5} {'/hari':>6} {'alpha5%':>8} {'blok t':>7} "
          f"{'paruh':>17} {'net5%':>7} {'%untung':>8} {'thn+':>7}")
    cats = [("fast_grower", "Fast grower (laba +>=20%)", "ni_growth"),
            ("stalwart", "Stalwart (besar, laba +8..20%)", "ni_growth"),
            ("slow_grower", "Slow grower (besar, laba <8%)", "ni_growth"),
            ("turnaround", "Turnaround (rugi -> laba)", "ni_prev"),
            ("asset_play", "Asset play (P/B kuintil-1)", "pb"),
            ("cyclical", "Cyclical (PROKSI vol. laba)", "growth_vol")]
    base_union = S[["ni_growth", "pb", "growth_vol"]].notna().any(axis=1)
    uni = row_stats(S, base_union, base=base_union)
    print(f"  {'SEMUA BARIS YANG BISA DINILAI':<34} {uni['n']:>7,} {uni['days']:>5} "
          f"{uni['per_day']:>6.1f} {uni['alpha']:>+8.2f} {uni['t']:>+7.2f} "
          f"{uni['early']:>+8.2f}/{uni['late']:<+8.2f} {uni['net']:>+7.2f} "
          f"{uni['win']:>7.1f}% {uni['years_pos']:>3}/{uni['years']:<3}")
    for key, label, bcol in cats:
        base = S[bcol].notna()
        r = row_stats(S, S[key], base=base)
        if not r["enough"]:
            print(f"  {label:<34} {r['n']:>7,}  (sampel < 30 — tidak disimpulkan)")
            continue
        print(f"  {label:<34} {r['n']:>7,} {r['days']:>5} {r['per_day']:>6.1f} "
              f"{r['alpha']:>+8.2f} {r['t']:>+7.2f} "
              f"{r['early']:>+8.2f}/{r['late']:<+8.2f} {r['net']:>+7.2f} "
              f"{r['win']:>7.1f}% {r['years_pos']:>3}/{r['years']:<3}")

    # ------------------------------------------------------------- E. ATURAN
    print(f"\n{'='*118}\nE. ATURAN OPERASIONAL LYNCH (hal 198-199) — diukur apa adanya\n{'='*118}")
    print("  PEG < 1 = 'harga murah dibanding pertumbuhan'. P/E > pertumbuhan = tanda bahaya.")
    rules = [("peg_lt_1", "PEG < 1"),
             ("pe_above_growth", "P/E DI ATAS pertumbuhan (peringatan)"),
             ("fast_peg_lt_1", "Fast grower + PEG < 1 (favorit Lynch)")]
    base_rule = S["peg"].notna()
    for key, label in rules:
        r = row_stats(S, S[key], base=base_rule)
        if not r["enough"]:
            print(f"  {label:<44} {r['n']:>7,}  (sampel < 30 — tidak disimpulkan)")
            continue
        print(f"  {label:<44} {r['n']:>7,} {r['days']:>5} {r['per_day']:>6.1f} "
              f"{r['alpha']:>+8.2f} {r['t']:>+7.2f} "
              f"{r['early']:>+8.2f}/{r['late']:<+8.2f} {r['net']:>+7.2f} "
              f"{r['win']:>7.1f}% {r['years_pos']:>3}/{r['years']:<3}")
    print("\n  Kombinasi faktor yang sudah dipakai produksi (pembanding, bukan klaim baru):")
    for label, mask in (
            ("pertumbuhan laba +>=20% & P/E <= 15",
             (S["ni_growth"] >= 20) & (S["pe"] <= 15)),
            ("ROE >= 15% & P/E <= 15",
             (S["roe"] >= 0.15) & (S["pe"] <= 15)),
            ("P/B <= 1 & ROE >= 10%",
             (S["pb"] <= 1.0) & (S["roe"] >= 0.10))):
        r = row_stats(S, mask, base=base_rule)
        if not r["enough"]:
            print(f"  {label:<44} {r['n']:>7,}  (sampel < 30 — tidak disimpulkan)")
            continue
        print(f"  {label:<44} {r['n']:>7,} {r['days']:>5} {r['per_day']:>6.1f} "
              f"{r['alpha']:>+8.2f} {r['t']:>+7.2f} "
              f"{r['early']:>+8.2f}/{r['late']:<+8.2f} {r['net']:>+7.2f} "
              f"{r['win']:>7.1f}% {r['years_pos']:>3}/{r['years']:<3}")

    # ------------------------------------------- F. ATURAN SIAP PRODUKSI
    # Bagian ini yang menentukan apa yang boleh dipasang di aplikasi. Kuantil (bagian C)
    # bagus untuk MENGETAHUI ada tidaknya efek, tetapi tidak bisa dipasang di produksi:
    # produksi memindai saham satu per satu dan tidak punya kuantil pasar saat itu.
    # Karena itu di sini diukur aturan ber-AMBANG ABSOLUT (P/B ≤ 1, ROE ≥ 15%, dst) —
    # bentuk yang benar-benar bisa dieksekusi, dan satu-satunya yang dipasang.
    print(f"\n{'='*118}\nF. ATURAN SIAP PRODUKSI (ambang absolut, bisa dipasang apa adanya)\n{'='*118}")
    print("  Ambang ditulis SEBELUM diukur (bukan dicari yang paling bagus), lalu yang dipakai")
    print("  adalah yang lolos: alpha positif, blok t >= +2, KEDUA paruh positif, net > 0.")
    print("  'net20' = rata-rata absolut setelah biaya 0,3%; '%untung' = bagian baris yang")
    print("  untung melebihi biaya. Kolom alpha tetap lawan kelas likuiditas yang sama.")
    grid = [
        ("P/B <= 0,5", lambda S: S["pb"] <= 0.5, "pb"),
        ("P/B <= 0,75", lambda S: S["pb"] <= 0.75, "pb"),
        ("P/B <= 1,0", lambda S: S["pb"] <= 1.0, "pb"),
        ("P/B <= 1,5", lambda S: S["pb"] <= 1.5, "pb"),
        ("P/E <= 10", lambda S: S["pe"] <= 10.0, "pe"),
        ("P/E <= 15", lambda S: S["pe"] <= 15.0, "pe"),
        ("ROE >= 15%", lambda S: S["roe"] >= 0.15, "roe"),
        ("ROE >= 20%", lambda S: S["roe"] >= 0.20, "roe"),
        ("P/B <= 1 & ROE >= 10%", lambda S: (S["pb"] <= 1.0) & (S["roe"] >= 0.10), "pb"),
        ("P/B <= 1 & ROE >= 15%", lambda S: (S["pb"] <= 1.0) & (S["roe"] >= 0.15), "pb"),
        ("P/B <= 1,5 & ROE >= 15%", lambda S: (S["pb"] <= 1.5) & (S["roe"] >= 0.15), "pb"),
        ("P/E <= 10 & ROE >= 15%", lambda S: (S["pe"] <= 10.0) & (S["roe"] >= 0.15), "pe"),
        ("P/B <= 1 & likuid (CUKUP+)",
         lambda S: (S["pb"] <= 1.0) & S["grade"].isin(CA.LIQUID_GRADES), "pb"),
        ("P/B <= 1 & ROE >= 10% & likuid",
         lambda S: ((S["pb"] <= 1.0) & (S["roe"] >= 0.10)
                    & S["grade"].isin(CA.LIQUID_GRADES)), "pb"),
        # --- TURNAROUND (Lynch hal 38) + dua kontrolnya ---------------------------------
        # Kategori ini satu-satunya kategori buku yang hasil rata-ratanya positif di
        # sampel penuh (+0,32%), tetapi rata-rata saja belum cukup — karena itu diukur
        # dengan dua ukuran seperti aturan lain. Kontrolnya penting supaya "turnaround"
        # tidak diam-diam hanya berarti "saham yang sedang bangkit dari harga jatuh":
        # rugi mengecil (masih rugi) dan laba->rugi mengukur arah sebaliknya.
        ("Turnaround (rugi -> laba)",
         lambda S: (S["ni_prev"] < 0) & (S["ni"] > 0), "ni_prev"),
        ("Turnaround & likuid (CUKUP+)",
         lambda S: ((S["ni_prev"] < 0) & (S["ni"] > 0)
                    & S["grade"].isin(CA.LIQUID_GRADES)), "ni_prev"),
        ("Rugi mengecil (masih rugi)",
         lambda S: (S["ni_prev"] < 0) & (S["ni"] < 0) & (S["ni"] > S["ni_prev"]), "ni_prev"),
        ("Laba -> rugi (kontrol negatif)",
         lambda S: (S["ni_prev"] > 0) & (S["ni"] < 0), "ni_prev"),
        # Apakah nilai buku menyelamatkan turnaround? Diuji, bukan diasumsikan: kalau
        # sinyalnya memang "saham murah yang bangkit", menambahkan syarat murah harus
        # memperbaiki kedua ukuran, bukan hanya rata-ratanya.
        ("Turnaround & P/B <= 1",
         lambda S: (S["ni_prev"] < 0) & (S["ni"] > 0) & (S["pb"] <= 1.0), "ni_prev"),
        ("Turnaround & P/B <= 0,5",
         lambda S: (S["ni_prev"] < 0) & (S["ni"] > 0) & (S["pb"] <= 0.5), "ni_prev"),
        # --- PEMBONGKARAN: apakah yang bekerja "TRANSISINYA" atau cuma "murah + laba"? ---
        # Tiga baris ini memisahkan keduanya. Kalau yang unggul hanya baris pertama,
        # transisi rugi->laba memang bahan aktifnya. Kalau ketiganya mirip, label
        # "turnaround" cuma hiasan dan yang bekerja adalah nilai buku + laba positif.
        ("P/B <= 0,5 & laba POSITIF (tanpa transisi)",
         lambda S: (S["pb"] <= 0.5) & (S["ni"] > 0), "ni_prev"),
        ("P/B <= 0,5 & masih RUGI",
         lambda S: (S["pb"] <= 0.5) & (S["ni"] < 0), "ni_prev"),
        ("Laba -> rugi & P/B <= 0,5 (kontrol)",
         lambda S: (S["ni_prev"] > 0) & (S["ni"] < 0) & (S["pb"] <= 0.5), "ni_prev"),
    ]
    print("  'med' = selisih MEDIAN per tanggal lawan median populasi dasar (tahan outlier).")
    print("  Sebuah aturan dipasang hanya bila LOLOS UKURAN RATA-RATA DAN tahan-outlier:")
    print("  kalau keduanya berbeda tanda, artinya hasilnya bergantung pada beberapa saham")
    print("  ekstrem — dan itu bukan dasar yang cukup untuk dipasang di aplikasi.")
    print(f"  {'aturan':<34} {'n':>8} {'/hari':>6} {'a5 rata':>8} {'a5 med':>7} {'a5 t':>6} "
          f"{'a20 rata':>9} {'a20 med':>8} {'t med':>7} {'paruh20 med':>17} {'net20':>7} "
          f"{'thn+':>7} {'lolos':>6}")
    candidates: List[Tuple[str, Dict, Dict]] = []
    for label, fn, bcol in grid:
        base = S[bcol].notna()
        try:
            mask = fn(S).fillna(False)
        except Exception:
            continue
        r5 = row_stats(S, mask, h=5, base=base)
        r20 = row_stats(S, mask, h=20, base=base)
        if not r20["enough"] or not r5.get("enough"):
            print(f"  {label:<34} {r20['n']:>8,}  (sampel < 30 — tidak disimpulkan)")
            continue
        med20 = (r20["alpha_med"] > 0 and r20["t_med"] >= 2 and r20["early_med"] > 0
                 and r20["late_med"] > 0)
        med5 = (r5["alpha_med"] > 0 and r5["t_med"] >= 2
                and r5["early_med"] > 0 and r5["late_med"] > 0)
        mean20 = (r20["alpha"] > 0 and r20["t"] >= 2 and r20["early"] > 0
                  and r20["late"] > 0 and r20["net"] > 0)
        mean5 = (r5["alpha"] > 0 and r5["t"] >= 2 and r5["early"] > 0 and r5["late"] > 0)
        marks = (("t20" if mean20 else "-") + ("o20" if med20 else "-")
                 + ("t5" if mean5 else "-") + ("o5" if med5 else "-"))
        if mean20 and med20 and mean5 and med5:
            marks += "  <== LOLOS"
        print(f"  {label:<34} {r20['n']:>8,} {r20['per_day']:>6.1f} "
              f"{r5['alpha']:>+8.2f} {r5['alpha_med']:>+7.2f} {r5['t_med']:>+6.1f} "
              f"{r20['alpha']:>+9.2f} {r20['alpha_med']:>+8.2f} {r20['t_med']:>+7.1f} "
              f"{r20['early_med']:>+8.2f}/{r20['late_med']:<+8.2f} {r20['net']:>+7.2f} "
              f"{r20['years_pos']:>3}/{r20['years']:<3} {marks}")
        if mean20 and med20:
            candidates.append((label, r5, r20))

    if candidates:
        print("\n  RINCI KANDIDAT (yang lolos kedua ukuran di h20) — supaya keputusannya bisa diperiksa:")
        for label, r5, r20 in candidates:
            print(f"\n   {label} · {r20['n']:,} baris · {r20['per_day']:.1f}/hari · net20 {r20['net']:+.2f}% "
                  f"· untung {r20['win']:.1f}% · thn+ {r20['years_pos']}/{r20['years']}")
            for tag, r in (("h5 ", r5), ("h20", r20)):
                print(f"     {tag} rata-rata: alpha {r['alpha']:+.2f}% (t {r['t']:+.1f}, "
                      f"paruh {r['early']:+.2f}/{r['late']:+.2f})   "
                      f"median: {r['alpha_med']:+.2f}% (t {r['t_med']:+.1f}, "
                      f"paruh {r['early_med']:+.2f}/{r['late_med']:+.2f})")
    else:
        print("\n  TIDAK ada aturan yang lolos kedua ukuran di h20 — tidak ada yang dipasang "
              "sebagai kriteria baru.")

    # ---------------------------------------------------- G. SANITY CHECK
    print(f"\n{'='*118}\nG. SANITY CHECK — apakah angkanya masuk akal? (snapshot TERAKHIR)\n{'='*118}")
    last = S["date"].max()
    snap = S[S["date"] == last].copy()
    print(f"  Tanggal: {last.date()} · {len(snap)} emiten dengan harga di panel")
    print(f"  {'kode':<6} {'P/E':>8} {'P/B':>7} {'ROE%':>7} {'laba YoY%':>10} {'mcap (T)':>10}")
    known = [c for c in ("BBCA", "BBRI", "TLKM", "ASII", "UNVR", "ICBP", "INDF", "ANTM",
                         "SMGR", "AALI") if c in set(snap["code"])]
    for c in known[:8]:
        row = snap[snap["code"] == c].iloc[0]
        mc = row["market_cap"] / 1e12 if pd.notna(row["market_cap"]) else float("nan")
        print(f"  {c:<6} {row['pe']:>8.2f} {row['pb']:>7.2f} "
              f"{(row['roe'] * 100 if pd.notna(row['roe']) else float('nan')):>7.2f} "
              f"{row['ni_growth']:>10.2f} {mc:>10.2f}")
    med = S.loc[S["pe"].notna() & (S["pe"] < 200), "pe"].median()
    med_pb = S.loc[S["pb"].notna() & (S["pb"] < 100), "pb"].median()
    print(f"  Median sampel: P/E {med:.1f} · P/B {med_pb:.2f} "
          f"(P/E & P/B 'masuk akal' bila puluhan/tidak ratusan — kalau ratusan, "
          f"pemilihan jalur EPS/ekuitas yang salah)")

    # ------------------------------------------------------------- H. PUTUSAN
    print(f"\n{'='*118}\nH. RINGKASAN & BATAS\n{'='*118}")
    print("  Aturan dianggap TERUKUR BAIK bila: alpha lawan kelas positif, blok t >= +2,")
    print("  positif di KEDUA paruh waktu, dan tetap positif setelah biaya 0,3%.")
    print("  Catatan membaca t: di sini blok t mengukur KONSISTENSI (ribuan tanggal), jadi t besar")
    print("  pada alpha ~0,1% hanya berarti 'konsisten kecil'. Yang menentukan keputusan adalah")
    print("  BESAR alpha dibanding biaya 0,3% — bukan nilai t-nya.")
    print("  Batas yang berlaku untuk SEMUA angka di atas:")
    print(f"   * sampel = {S['code'].nunique()} emiten dari {n_panel} di panel harga; angka di"
          " bagian B-F berlaku untuk emiten itu saja;")
    print("   * rata-rata vs tahan-outlier bisa BERBEDA TANDA (contoh nyata: ROE, dan gerbang")
    print("     kualitas pada kombinasi P/B+ROE). Bila itu terjadi, aturannya TIDAK dipasang —")
    print("     hasil yang bergantung pada beberapa saham ekstrem bukan dasar yang cukup;")
    print("   * P/E & P/B dari laporan TAHUNAN, bukan TTM, dan tanpa penyesuaian aset;")
    print("   * tidak ada data sektor (cyclical = proksi volatilitas laba) & tanpa dividen;")
    print("   * pertumbuhan laba hanya bisa dihitung sejak FY2023 terbit -> sampel lebih pendek")
    print("     daripada faktor P/E/P/B, jadi dua kelompok itu TIDAK boleh dibandingkan n=nya.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
