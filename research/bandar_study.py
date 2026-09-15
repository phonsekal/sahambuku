#!/usr/bin/env python3
"""Uji: bisakah "akumulasi diam-diam satu broker" dideteksi dan apakah ia memprediksi?

Latar belakang
--------------
api/index.py menghitung `BandarValue` = JUMLAH nval seluruh broker per tanggal
(fetch_idx_accumulation), lalu memakainya untuk kriteria Swing/BANDAR. Cara itu
buta terhadap pola yang justru paling sering dimaksud orang:

    "broker X membeli saham ini terus-menerus selama seminggu, tanpa menjual"

Contoh nyata (10 hari terakhir, sebelum uji ini dibuat):
  PTBA  AK/UBS         10/10 hari beli, net/gross 1,00, net +463,86 M
        BandarValue aplikasi hanya +227,56 M  -> terdilusi 2x oleh arus broker lain
  BBRI  YU/CGS          8/10 hari beli, net/gross 0,86, net +372,80 M
        BandarValue aplikasi hanya +135,59 M  -> terdilusi 2,7x
Ketiganya (PTBA/TINS/BBRI) TIDAK lolos screener BANDAR kita.

Script ini menguji dua hal:
  1. apakah detektor per-broker ini bisa dihitung point-in-time (tanpa melihat masa depan);
  2. apakah ia memprediksi excess return lebih baik daripada BandarValue agregat.

Batasan yang wajib diingat
--------------------------
* /api/broker-accumulation hanya menyediakan 80 hari perdagangan TERAKHIR
  (limit 60..2000 memberi titik yang sama). Jadi ini BUKAN backtest multi-tahun;
  sekitar 50 tanggal yang bisa dipakai. Ini penyaringan, bukan bukti final.
* Hanya broker teratas per saham yang dikirim API, bukan seluruh pasar.
* Universe = daftar emiten saat ini -> survivorship bias.

HASIL (jalankan 11 Sep 2026, seluruh pasar)
------------------------------------------
Data: 951 saham, 322.155 baris broker-hari, 62.054 saham-tanggal, 77 tanggal
(2026-05-20 s/d 2026-09-11) setelah data basi disaring.

1) DETEKTORNYA BEKERJA (deskriptif). Ditemukan kasus nyata yang lolos dari
   metrik agregat aplikasi:
     PTBA  AK/UBS  10/10 hari beli, net/gross 1,00, net +463,86 M (BandarValue app: +227,56 M)
     BBRI  YU/CGS   8/10 hari beli, net/gross 0,86, net +372,80 M (BandarValue app: +135,59 M)
   Screener BANDAR aplikasi TIDAK meloloskan PTBA/TINS/BBRI.

2) TAPI TIDAK ADA BUKTI PREDIKSI YANG DAPAT DIPERCAYA. Angka mentah tampak
   dramatis (flag -> exc20 = -0,54% t=-3,02; IC silent_rel20 vs exc20 = -0,0490
   t=-5,68), TETAPI itu artefak jendela TUMPANG-TINDIH: 77 hari bursa hanya
   memberi 15 blok 5-hari dan 3 blok 20-hari. Setelah stride = horizon:
     flag -> exc5  (w=5/10/20)  : +0,04% (t=+0,46) / -0,05% (t=-0,30) / -0,52% (t=-1,26)
     flag -> exc20 (w=10)       : -2,76% (t=-1,20), hanya 3 blok
     BandarValue agregat -> exc20: -1,39% (t=-1,48), hanya 3 blok
     IC silent_rel20 vs exc20   : -0,0490 [-tumpang] -> +0,0006 [blok]  <-- murni noise
   Metrik agregat yang sudah dipakai aplikasi TIDAK lebih buruk maupun lebih baik.

Kesimpulan: nilainya DESKRIPTIF, bukan prediktif. Tampilan per-broker jelas lebih
informatif daripada agregat yang saling menutupi, tetapi TIDAK boleh dipasang
sebagai kriteria beli. Jendela 80 hari memang terlalu pendek untuk menguji sinyal
berhorizon bulanan -- jangan ulangi uji ini tanpa data riwayat yang lebih panjang.

Jalankan:
  .venv/bin/python research/bandar_study.py --budget 3000
  .venv/bin/python research/bandar_study.py --codes PTBA,TINS,BBRI --budget 50
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".cache", "brokar")

IDX_URL = os.environ.get("IDX_EDGE_API_URL", "https://stock.arjum.com").strip().rstrip("/")
IDX_KEYS = [k.strip() for k in os.environ.get("IDX_EDGE_API_KEYS", "").split(",") if k.strip()]

MIN_STOCKS_PER_DATE = 100      # tanggal dengan saham lebih sedikit = data basi
WINDOWS = (5, 10, 20)          # panjang jendela formasi (hari)
CONS_MIN = 0.7                 # minimal 70% hari harus net beli
RATIO_MIN = 0.5                # net/gross minimal 0.5 (tidak bolak-balik)
BUDGET = 3000

_lock = threading.Lock()
_state = {"requests": 0, "cache": 0, "stopped": False}


# ---------------------------------------------------------------------------
# 1. AKSES API
# ---------------------------------------------------------------------------

def _take() -> bool:
    with _lock:
        if _state["stopped"] or _state["requests"] >= BUDGET:
            _state["stopped"] = True
            return False
        _state["requests"] += 1
        return True


def api_get(path: str, params: Dict) -> Optional[dict]:
    if not IDX_KEYS:
        raise SystemExit("IDX_EDGE_API_KEYS kosong; isi di .env.local")
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    for attempt in range(len(IDX_KEYS)):
        if not _take():
            return None
        key = IDX_KEYS[attempt % len(IDX_KEYS)]
        try:
            req = urllib.request.Request(f"{IDX_URL}{path}?{qs}",
                                         headers={"X-API-Key": key, "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                continue
            return None
        except Exception:
            return None
    return None


def fetch_accum(code: str) -> Optional[dict]:
    path = os.path.join(CACHE_DIR, f"{code}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                _state["cache"] += 1
                return json.load(fh)
        except Exception:
            pass
    data = api_get(f"/api/broker-accumulation/{code}", {"limit": 90})
    if data:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except Exception:
            pass
    return data


# ---------------------------------------------------------------------------
# 2. BENTUK DATA PANJANG (code, date, broker, nval)
# ---------------------------------------------------------------------------

def build_long(codes: List[str], workers: int = 8) -> Tuple[pd.DataFrame, Dict[str, int]]:
    rows: List[dict] = []
    nbroker: Dict[str, int] = {}

    def work(code: str):
        d = fetch_accum(code)
        if not d or not isinstance(d.get("series"), list):
            return code, []
        out = []
        for br in d["series"]:
            bc = str(br.get("broker_code") or "?")
            nm = br.get("broker_name") or bc
            for p in br.get("points") or []:
                out.append({
                    "code": code, "date": str(p.get("date"))[:10], "broker": bc, "name": nm,
                    "nval": float(p.get("nval") or 0.0),
                    "nvol": float(p.get("nvol") or 0.0),
                    "bavg": float(p.get("bavg") or 0.0),
                    "savg": float(p.get("savg") or 0.0),
                })
        return code, out

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for code, out in ex.map(work, codes):
            if out:
                nbroker[code] = len({r["broker"] for r in out})
                rows.extend(out)
            if _state["stopped"]:
                print("  [stop] anggaran request habis.")
                break

    df = pd.DataFrame(rows)
    if len(df):
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.sort_values(["code", "broker", "date"]).reset_index(drop=True)
    return df, nbroker


# ---------------------------------------------------------------------------
# 3. SINYAL POINT-IN-TIME (hanya memakai data s/d tanggal itu)
# ---------------------------------------------------------------------------

def build_signals(L: pd.DataFrame, win: int) -> pd.DataFrame:
    """Per (saham, tanggal): statistik per-broker dari `win` hari SEBELUMNYA.

    `consistency` = porsi hari broker itu net beli.
    `net_ratio`   = jumlah nval / jumlah |nval| (1,00 = tidak pernah menjual secara net).
    """
    g = L.copy()
    g["is_buy"] = (g["nval"] > 0).astype(float)
    g["abs"] = g["nval"].abs()
    grp = g.groupby(["code", "broker"], sort=False)
    g["net"] = grp["nval"].transform(lambda s: s.rolling(win, min_periods=win).sum())
    g["gross"] = grp["abs"].transform(lambda s: s.rolling(win, min_periods=win).sum())
    g["cons"] = grp["is_buy"].transform(lambda s: s.rolling(win, min_periods=win).mean())
    g = g.dropna(subset=["net", "gross", "cons"])
    g = g[g["gross"] > 0]

    g["ratio"] = g["net"] / g["gross"]
    cand = g[(g["cons"] >= CONS_MIN) & (g["ratio"] >= RATIO_MIN) & (g["net"] > 0)]

    # sinyal per saham-tanggal
    best = (cand.sort_values("net", ascending=False)
                 .groupby(["code", "date"], as_index=False)
                 .first()[["code", "date", "broker", "name", "net", "ratio", "cons"]]
                 .rename(columns={"broker": "top_broker", "name": "top_name",
                                  "net": f"silent_net{win}", "ratio": f"silent_ratio{win}",
                                  "cons": f"silent_cons{win}"}))

    agg = (g.groupby(["code", "date"], as_index=False)
            .agg(**{f"bandar_agg{win}": ("net", "sum"),
                    f"n_broker{win}": ("broker", "nunique"),
                    f"max_share{win}": ("net", lambda s: float(s.max()))}))
    out = agg.merge(best, on=["code", "date"], how="left")
    out[f"silent_flag{win}"] = out[f"silent_net{win}"].notna().astype(float)
    out[f"silent_net{win}"] = out[f"silent_net{win}"].fillna(0.0)
    out[f"silent_ratio{win}"] = out[f"silent_ratio{win}"].fillna(0.0)
    out[f"silent_cons{win}"] = out[f"silent_cons{win}"].fillna(0.0)
    out["top_broker"] = out["top_broker"].fillna("")
    return out


# ---------------------------------------------------------------------------
# 4. HARGA & HASIL KE DEPAN
# ---------------------------------------------------------------------------

def load_prices(codes: List[str], years: int = 1) -> Tuple[Dict[str, pd.Series], pd.Series]:
    from backtest_rs import _fetch_yahoo, _norm  # type: ignore

    out: Dict[str, pd.Series] = {}

    def work(code: str):
        df = _fetch_yahoo(f"{code}.JK", f"{years}y")
        if df is None or df.empty:
            return code, None, None
        df = _norm(df)
        val = df["Close"] * df["Volume"] if "Volume" in df.columns else None
        return code, df["Close"], val

    val_map: Dict[str, pd.Series] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for code, close, val in ex.map(work, codes):
            if close is not None:
                out[code] = close
                if val is not None:
                    val_map[code] = val

    ih = _fetch_yahoo("^JKSE", f"{years}y")
    idx = _norm(ih)["Close"] if (ih is not None and not ih.empty) else pd.Series(dtype=float)
    return out, idx, val_map  # type: ignore


def attach_outcomes(S: pd.DataFrame, prices, idx, val_map,
                    horizons=(1, 5, 20)) -> pd.DataFrame:
    S = S.copy()
    have_idx = idx is not None and not idx.empty
    chunks = []
    for code, g in S.groupby("code"):
        px = prices.get(code)
        if px is None or px.empty:
            continue
        px = px.copy()
        px.index = pd.to_datetime(px.index).normalize()
        px = px[~px.index.duplicated(keep="last")].sort_index()
        ih = None
        if have_idx:
            ih = idx.copy()
            ih.index = pd.to_datetime(ih.index).normalize()
            ih = ih[~ih.index.duplicated(keep="last")].sort_index()
            ih = ih.reindex(ih.index.union(px.index)).ffill().reindex(px.index).ffill()

        g = g.copy()
        g["dt"] = pd.to_datetime(g["date"]).dt.normalize()
        pos = px.index.searchsorted(g["dt"].values)
        ok = (pos >= 0) & (pos < len(px))
        ok &= np.asarray(px.index[pos.clip(max=len(px) - 1)] == g["dt"].values)
        g = g[ok].copy()
        pos = pos[ok]
        if not len(g):
            continue
        close0 = px.values[pos]
        for h in horizons:
            valid = pos + h < len(px)
            g[f"fwd{h}"] = np.where(valid, px.values[np.clip(pos + h, 0, len(px) - 1)] / close0 - 1.0, np.nan)
            if ih is not None:
                g[f"exc{h}"] = np.where(
                    valid,
                    g[f"fwd{h}"].values - (ih.values[np.clip(pos + h, 0, len(px) - 1)] / ih.values[pos] - 1.0),
                    np.nan)
            else:
                g[f"exc{h}"] = g[f"fwd{h}"]
        vm = val_map.get(code)
        if vm is not None and len(vm):
            v = vm.copy()
            v.index = pd.to_datetime(v.index).normalize()
            v = v[~v.index.duplicated(keep="last")].sort_index()
            v20 = v.rolling(20, min_periods=10).mean()
            v20 = v20.reindex(v20.index.union(px.index)).ffill().reindex(px.index).ffill()
            g["avg_value20"] = v20.values[pos]
        chunks.append(g)
    return pd.concat(chunks, ignore_index=True) if chunks else S


# ---------------------------------------------------------------------------
# 5. STATISTIK (diklasteskan per tanggal)
# ---------------------------------------------------------------------------

def evaluate_flag(D: pd.DataFrame, mask: pd.Series, outcome: str, label: str,
                  stride: int = 1) -> None:
    """Rata-rata (flagged - universe) per tanggal, lalu rata-rata lintas tanggal.

    stride > 1 mengambil setiap tanggal ke-`stride` saja. Untuk horizon 20 hari ini
    WAJIB: hasil 20 hari di tanggal berurutan saling tumpang-tindih (t dan t+1
    berbagi 19 dari 20 hari), sehingga t-stat tanpa stride bisa membengkak ~sqrt(h)
    kali lipat. stride = horizon mendekati sampel blok tidak-tumpang-tindih.
    """
    d = D[mask.fillna(False)].dropna(subset=[outcome])
    alls = D.dropna(subset=[outcome])
    if stride > 1:
        dst = np.sort(D["date"].unique())[::stride]
        d = d[d["date"].isin(dst)]
        alls = alls[alls["date"].isin(dst)]
    if not len(d):
        print(f"  {label:<42} (tidak ada yang flagged)")
        return
    per_date = []
    for dt, g in d.groupby("date"):
        uni = alls[alls["date"] == dt][outcome]
        if len(uni) < 20:
            continue
        per_date.append(g[outcome].mean() - uni.mean())
    if len(per_date) < 3:
        print(f"  {label:<42} {len(d):>6} obs, tanggal terlalu sedikit")
        return
    a = np.array(per_date, dtype=float)
    t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))
    pos_share = float((a > 0).mean())
    verdict = "konsisten" if pos_share >= 0.6 else ("campur" if pos_share >= 0.4 else "melawan")
    print(f"  {label:<42} n={len(d):>6}  tanggal={len(a):>3}  "
          f"rata2 excess {a.mean()*100:+.2f}%  t={t:+.2f}  {verdict} ({pos_share*100:.0f}% tanggal positif)")


def ic_by_date(D: pd.DataFrame, metric: str, outcome: str,
               stride: int = 1) -> Tuple[float, float, int]:
    ics = []
    if stride > 1:
        dst = np.sort(D["date"].unique())[::stride]
        D = D[D["date"].isin(dst)]
    for _, g in D.groupby("date"):
        s = g[[metric, outcome]].dropna()
        if len(s) < 10 or s[metric].nunique() < 3 or s[outcome].nunique() < 3:
            continue
        ic = s[metric].rank().corr(s[outcome].rank())
        if not np.isnan(ic):
            ics.append(ic)
    if len(ics) < 3:
        return np.nan, np.nan, len(ics)
    a = np.array(ics, dtype=float)
    return float(a.mean()), float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))), len(a)


def quintile(D: pd.DataFrame, metric: str, outcome: str) -> None:
    parts = []
    for _, g in D.groupby("date"):
        s = g[[metric, outcome]].dropna()
        if len(s) < 30 or s[metric].nunique() < 5:
            continue
        try:
            q = pd.qcut(s[metric].rank(method="first"), 5, labels=False)
        except Exception:
            continue
        parts.append(s[outcome].groupby(q).mean())
    if not parts:
        print(f"  {metric}: tanggal terlalu sedikit")
        return
    avg = pd.concat(parts, axis=1).mean(axis=1)
    print(f"  {metric:<20} " + "  ".join(f"Q{int(k)+1}={v*100:+.2f}%" for k, v in avg.items()))


# ---------------------------------------------------------------------------
# 6. MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    global BUDGET, MIN_STOCKS_PER_DATE
    ap = argparse.ArgumentParser(description="Uji akumulasi diam-diam satu broker")
    ap.add_argument("--codes", default="", help="daftar kode dipisah koma; kosong = seluruh pasar")
    ap.add_argument("--budget", type=int, default=3000, help="batas request (kuota dibagi app)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--min-stocks-per-date", type=int, default=100,
                    help="buang tanggal yang dihuni kurang dari N saham (data basi)")
    args = ap.parse_args()
    BUDGET = args.budget
    MIN_STOCKS_PER_DATE = args.min_stocks_per_date

    codes = [c.strip().upper() for c in args.codes.split(",") if c.strip()]
    if not codes:
        from api.index import load_idx_tickers  # type: ignore
        codes = [t.replace(".JK", "") for t in load_idx_tickers("all")]
    print(f"== UJI AKUMULASI DIAM-DIAM ==\n  saham: {len(codes)}  anggaran: {BUDGET} request")

    t0 = time.time()
    L, nbroker = build_long(codes, args.workers)
    print(f"  data: {len(L):,} baris broker-hari, {L['code'].nunique() if len(L) else 0} saham, "
          f"rentang {L['date'].min().date() if len(L) else '-'} s/d {L['date'].max().date() if len(L) else '-'}")
    print(f"  request={_state['requests']} cache={_state['cache']} ({time.time()-t0:.0f} dtk)")
    if not len(L):
        print("  tidak ada data."); return
    nb = pd.Series(list(nbroker.values()))
    print(f"  broker per saham: median {nb.median():.0f}, min {nb.min()}, maks {nb.max()}  "
          f"(API hanya mengirim broker teratas)")

    sig = None
    for w in WINDOWS:
        s = build_signals(L, w)
        sig = s if sig is None else sig.merge(s, on=["code", "date"], how="outer", suffixes=("", f"_{w}"))
    sig = sig.loc[:, ~sig.columns.duplicated()]
    print(f"  sinyal: {len(sig):,} saham-tanggal")

    print("\n  memuat harga...")
    prices, idx, val_map = load_prices(sorted(sig["code"].unique()))
    D = attach_outcomes(sig, prices, idx, val_map)
    if "avg_value20" in D.columns:
        for w in WINDOWS:
            D[f"silent_rel{w}"] = D[f"silent_net{w}"] / D["avg_value20"].replace(0, np.nan)
            D[f"bandar_rel{w}"] = D[f"bandar_agg{w}"] / D["avg_value20"].replace(0, np.nan)
    print(f"  {len(D):,} baris sebelum saring ({D['code'].nunique()} saham, {D['date'].nunique()} tanggal)")

    # Buang tanggal yang hanya dihuni segelintir saham. API mengembalikan DATA BASI
    # untuk saham tidak aktif (mis. MTRA: 17 titik, semuanya Feb 2020), dan tanggal
    # basi itu kalau dibiarkan ikut jadi "cluster tanggal" sehingga t-stat menyesatkan.
    per_date = D.groupby("date")["code"].nunique()
    keep = per_date[per_date >= MIN_STOCKS_PER_DATE].index
    D = D[D["date"].isin(keep)].copy()
    print(f"  saring >= {MIN_STOCKS_PER_DATE} saham/tanggal -> {len(D):,} baris, "
          f"{D['date'].nunique()} tanggal ({D['date'].min().date()} s/d {D['date'].max().date()})")

    for w in WINDOWS:
        print(f"\n=== JENDELA {w} HARI ===")
        print(f"  flag aktif: {int(D[f'silent_flag{w}'].sum()):,} dari {len(D):,} saham-tanggal")
        print("\n  1) apakah flag memprediksi? (diklasteskan per tanggal)")
        for h in (1, 5, 20):
            evaluate_flag(D, D[f"silent_flag{w}"] == 1.0, f"exc{h}",
                          f"akumulator diam-diam -> exc{h}", stride=1)
        # pembanding: metrik agregat yang dipakai aplikasi sekarang
        med = D[f"bandar_rel{w}"].median()
        evaluate_flag(D, D[f"bandar_rel{w}"] > med, "exc20",
                      "BandarValue agregat (app sekarang) -> exc20", stride=1)

        print("\n  1b) blok TIDAK tumpang-tindih (stride = horizon) -- yang ini yang jujur")
        for h in (5, 20):
            evaluate_flag(D, D[f"silent_flag{w}"] == 1.0, f"exc{h}",
                          f"akumulator diam-diam -> exc{h} [blok]", stride=h)
        evaluate_flag(D, D[f"bandar_rel{w}"] > med, "exc20",
                      "BandarValue agregat -> exc20 [blok]", stride=20)

        print("\n  2) IC per tanggal (stride=1 tumpang-tindih; [blok] = tidak)")
        for metric in (f"silent_rel{w}", f"silent_cons{w}", f"silent_ratio{w}",
                       f"bandar_rel{w}"):
            for h in (5, 20):
                ic, t, n = ic_by_date(D, metric, f"exc{h}")
                if not np.isnan(ic):
                    mark = "  <-" if abs(ic) > 0.03 else ""
                    icb, tb, nb = ic_by_date(D, metric, f"exc{h}", stride=h)
                    blok = f"   [blok] IC {icb:+.4f} t={tb:+5.2f} ({nb} tgl)" if not np.isnan(icb) else ""
                    print(f"    {metric:<18} vs exc{h:<2}: IC {ic:+.4f}  t={t:+5.2f}  ({n} tanggal){mark}{blok}")

        print("\n  3) kuintil")
        quintile(D, f"silent_rel{w}", "exc5")
        quintile(D, f"silent_rel{w}", "exc20")
        quintile(D, f"bandar_rel{w}", "exc20")

    print("\n-- BATASAN --")
    print("  * Jendela inti ~80 hari perdagangan (batas API) -> satu rezim. Ini penyaringan,")
    print("    BUKAN bukti final setara backtest_rs.py.")
    print("  * API mengirim HANYA broker teratas per saham (median 6 broker), jadi akumulator")
    print("    kecil bisa terlewat sama sekali.")
    print("  * API juga mengembalikan data basi untuk saham tidak aktif -> tanggal dengan")
    print("    sedikit saham sudah disaring (lihat baris 'saring >= N saham/tanggal').")
    print("  * Universe = emiten saat ini -> survivorship bias.")


if __name__ == "__main__":
    main()
