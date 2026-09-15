#!/usr/bin/env python3
"""Riset: bisakah endpoint IDX Edge /api/done-details dipakai untuk aplikasi kita?

Latar belakang
--------------
Arjum menambah endpoint `GET /api/done-details` (order flow per transaksi:
jam, papan RG/NG, harga, lot, nilai, kode broker beli/jual, F/D, dan kolom
`action` = pihak agresif hasil tick rule).

Dua hal yang TIDAK bisa dijawab dengan data yang sudah kita punya:
  1. berapa bagian nilai harian yang berasal dari papan NG (negosiasi/crossing)
     -> kelas likuiditas kita (api/index.py `_liquidity_grade`) bisa melebih-lebihkan
        likuiditas yang benar-benar bisa dieksekusi;
  2. apakah kenaikan harga ditopang agresi beli (mengejar offer) atau hanya
     drift pasif -> `action`.

Yang perlu diingat sebelum membaca hasil
----------------------------------------
Endpoint order flow menyimpan 30 hari perdagangan terakhir saja, jadi skrip ini
BUKAN backtest multi-tahun dan tidak bisa jadi bukti setara backtest_rs.py. Angka
yang keluar adalah *penyaringan awal*: cukup untuk membuang ide yang jelas tidak
ada gunanya, TIDAK cukup untuk mengklaim sebuah edge.

(Catatan: untuk pertanyaan PORSI BLOK NEGOSIASI (NG) ada sumber yang jauh lebih
baik dan lebih panjang -- ringkasan harian resmi IDX, seluruh pasar, 1 request per
hari bursa, sejak 2020. Lihat research/idx_daily_summary.py.)

Temuan verifikasi (11 Sep 2026, sudah diuji langsung ke API)
-----------------------------------------------------------
1. PARAMETER `date` WAJIB. Tanpa `date`, API mengembalikan agregat MULTI-HARI
   (BBCA: total 662.537) tapi tetap melaporkan satu tanggal ("2026-09-11").
   Dengan `date=2026-09-11` jawabannya 39.814. Dokumentasi menyesatkan di sini;
   siapa pun yang mempercayainya akan menghitung angka sampah.
2. Data diurutkan TERBARU DULU, jadi halaman 1 = jendela pasca-penutupan
   (BBCA 16:08-16:26, TINS 16:02-16:19).
3. Seluruh transaksi papan NG terjadi di jendela pasca-penutupan. Diuji pada TINS
   dengan pull PENUH 144 halaman: NG hanya di halaman 1, halaman 2-144 = nol NG.
   Nilai NG halaman 1 (7,50 M) identik dengan nilai NG sehari penuh (7,50 M).
   -> Porsi NG sehari bisa diukur dengan 1 REQUEST, bukan 144.
4. `action` bisa direproduksi mandiri: pembeli agresor bila nomor order beli >
   nomor order jual. Cocok 100% dengan kolom API pada semua sampel yang diuji.
5. (DIKOREKSI) Sempat saya simpulkan kelas likuiditas kita tercemar blok NG.
   Itu SALAH -- lihat H1 di bawah. Yang benar: nilai 4,47 M/hari milik STAR
   MEMANG mengandung 99,3% NG, tapi angka yang dibaca aplikasi bukan itu,
   melainkan nilai pasar REGULER (30 juta), sehingga labelnya sudah tepat.
   Jadi NG tetap informasi yang sah, hanya bukan cacat pada kode kita.
   Dan porsi NG SELURUH pasar bisa diukur bertahun-tahun, bukan 30 hari:
   lihat research/idx_daily_summary.py (sumber resmi IDX, 1 request/hari).
6. Harga kuota pull penuh: BBCA 399, TINS 144, ITMG 20, STAR 2 request per
   saham-hari. Mode halaman-1 = 1 request per saham-hari (21.000/hari -> seluruh
   universe IDX x 28 tanggal masuk anggaran).

Hasil RISET (semua NEGATIF — jangan diulang tanpa alasan baru)
------------------------------------------------------------
Diuji 11 Sep 2026 dengan key produksi, seluruh biaya ~1.500 request:

  H1 "kelas likuiditas tercemar NG"        -> SALAH. /api/history sudah RG-saja:
     TINS history 257,35 B = RG pull penuh 257,35 B; STAR history 30 juta = RG 30 juta.
     Aplikasi melaporkan STAR CUKUP (bukan LIKUID). Tidak ada yang perlu diperbaiki.
  H2 "nilai harian berbeda antar sumber"   -> SALAH. Close x Volume (yfinance) vs
     Value IDX Edge: rasio 0,99-1,04x pada BBCA/TLKM/STAR/GOTO/JSMR/KLBF/TINS.
     Fallback _value_series konsisten dengan IDX Edge.
  H3 "nilai besar tapi transaksi sedikit"  -> SALAH. 0 dari 1.240 saham-hari LQ45;
     1 dari 79 saham mikro (TAXI, 1,37 M / 406 transaksi - normal).
  H4 "order flow punya daya prediksi"      -> TIDAK ADA. 1.240 saham-hari, IC per
     tanggal vs excess return: agresor imbalance +0,0002 (h1) / -0,0028 (h5) /
     -0,022 (h20); porsi NG +0,039 (h1, t=1,15); jumlah transaksi +0,021 (h1).
     Sebaran kuintil tidak monoton. Tidak lolos dari noise.

Kesimpulan: endpoint ini valid dan rapi, tapi informasinya sudah terkandung dalam
/api/history + /api/broker-summary yang kita pakai, atau tidak punya daya prediksi.
Satu-satunya nilai praktisnya: diagnostik manual 1 request untuk memeriksa penutupan
satu saham (blok negosiasi atau bukan), dan pengetahuan soal jebakan `date` di atas.

Sub-perintah
------------
  --probe        1 saham 1 tanggal, cetak bentuk balasan apa adanya (verifikasi skema)
  --cost         ukur harga kuota: berapa halaman untuk satu saham-hari
  --pull-full    tarik SELURUH halaman (untuk memverifikasi asumsi halaman-1)
  (default)      mode halaman-1: 1 request/saham-hari, lalu uji daya prediksi

Jalankan:
  .venv/bin/python research/orderflow_study.py --probe --codes BBCA
  .venv/bin/python research/orderflow_study.py --cost  --codes BBCA,BBRI,ANTM,PTBA,MEDC
  .venv/bin/python research/orderflow_study.py --days 28 --budget 4000          # halaman-1
  .venv/bin/python research/orderflow_study.py --pull-full --codes TINS,STAR    # verifikasi
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
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".cache", "orderflow")
OUT_CSV = os.path.join(HERE, ".cache", "orderflow_agg.csv")

IDX_URL = os.environ.get("IDX_EDGE_API_URL", "https://stock.arjum.com").strip().rstrip("/")
IDX_KEYS = [k.strip() for k in os.environ.get("IDX_EDGE_API_KEYS", "").split(",") if k.strip()]
PER_PAGE = 100  # maksimum yang diizinkan API

# Kuota ini DIBAGI dengan aplikasi produksi (key yang sama). Melewati batas
# berarti bandarmology & sumber data IDX Edge di aplikasi live ikut mati
# sampai reset 00:00 WIB.
BUDGET = 8000

_lock = threading.Lock()
_state = {"requests": 0, "cache_hits": 0, "stopped": False}


# ---------------------------------------------------------------------------
# 1. AKSES API (+ cache lokal supaya re-run gratis)
# ---------------------------------------------------------------------------

def _cache_path(code: str, date: str, page: int) -> str:
    return os.path.join(CACHE_DIR, f"{code}_{date}_{page}.json")


def _cache_get(code: str, date: str, page: int) -> Optional[dict]:
    path = _cache_path(code, date, page)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _cache_put(code: str, date: str, page: int, data: dict) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(code, date, page), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:
        pass


def _take_budget() -> bool:
    """Ambil 1 jatah request; False kalau anggaran sudah habis (hentikan riset)."""
    with _lock:
        if _state["stopped"] or _state["requests"] >= BUDGET:
            _state["stopped"] = True
            return False
        _state["requests"] += 1
        return True


def api_get(path: str, params: Dict[str, Any], key_idx: int = 0) -> Optional[dict]:
    """GET ke IDX Edge dengan rotasi key; menghormati anggaran request."""
    if not IDX_KEYS:
        raise SystemExit(
            "IDX_EDGE_API_KEYS kosong. Isi dulu di .env.local (file sudah di-gitignore), contoh:\n"
            "  IDX_EDGE_API_KEYS=sk_live_xxxxxxxx"
        )
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{IDX_URL}{path}" + (f"?{qs}" if qs else "")
    for attempt in range(len(IDX_KEYS)):
        if not _take_budget():
            return None
        key = IDX_KEYS[(key_idx + attempt) % len(IDX_KEYS)]
        try:
            req = urllib.request.Request(url, headers={"X-API-Key": key, "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8")[:200]
            except Exception:
                pass
            if e.code in (401, 403):
                raise SystemExit(f"API key ditolak (HTTP {e.code}): {body}")
            if e.code == 429:
                print(f"  [kuota] key#{attempt+1} habis, coba key lain... ({body})")
                continue
            print(f"  [warn] {path} HTTP {e.code}: {body}")
            return None
        except Exception as exc:
            print(f"  [warn] {path} gagal: {exc}")
            return None
    print("  [kuota] semua key habis untuk hari ini.")
    _state["stopped"] = True
    return None


def fetch_page(code: str, date: Optional[str], page: int) -> Optional[dict]:
    """Satu halaman done-details (memakai cache lokal bila ada)."""
    if date:
        hit = _cache_get(code, date, page)
        if hit is not None:
            _state["cache_hits"] += 1
            return hit
    params: Dict[str, Any] = {"code": code, "page": page, "per_page": PER_PAGE}
    if date:
        params["date"] = date
    data = api_get("/api/done-details", params)
    if data and date:
        _cache_put(code, date, page, data)
    return data


def fetch_dates(code: str) -> List[str]:
    """Tanggal perdagangan yang tersedia untuk satu saham (menangani beberapa skema)."""
    data = api_get("/api/done-details/dates", {"code": code})
    if not isinstance(data, dict):
        return []
    for k in ("dates", "data", "rows"):
        v = data.get(k)
        if isinstance(v, list) and v:
            out = []
            for item in v:
                if isinstance(item, str):
                    out.append(item[:10])
                elif isinstance(item, dict):
                    for kk in ("date", "market_date", "tanggal"):
                        if item.get(kk):
                            out.append(str(item[kk])[:10])
                            break
            if out:
                return sorted(set(out))
    return []


# ---------------------------------------------------------------------------
# 2. AGREGASI ORDER FLOW SATU SAHAM-HARI
# ---------------------------------------------------------------------------

def _row_metrics(rows: List[dict]) -> Dict[str, float]:
    """Ringkas satu daftar transaksi (satu halaman atau seluruh hari)."""
    rg_b = ng_b = 0.0
    buy_v = sell_v = 0.0
    for_buy = for_sell = 0.0
    biggest = 0.0
    for r in rows:
        try:
            val = float(r.get("value_raw") or 0.0)
        except Exception:
            continue
        if val <= 0:
            continue
        board = str(r.get("market_board") or "RG").upper()
        agg = str(r.get("action") or "").upper()
        bt = str(r.get("buyer_type") or "").upper()
        st = str(r.get("seller_type") or "").upper()
        biggest = max(biggest, val)
        if board == "NG":
            ng_b += val
        else:
            rg_b += val
        if agg == "BUY":
            buy_v += val
        elif agg == "SELL":
            sell_v += val
        if bt == "F":
            for_buy += val
        if st == "F":
            for_sell += val
    total = rg_b + ng_b
    return {
        "value_sum": total,
        "value_rg": rg_b,
        "value_ng": ng_b,
        "ng_share": (ng_b / total) if total > 0 else np.nan,
        "agg_buy": buy_v,
        "agg_sell": sell_v,
        "agg_imb": ((buy_v - sell_v) / (buy_v + sell_v)) if (buy_v + sell_v) > 0 else np.nan,
        "for_buy": for_buy,
        "for_sell": for_sell,
        "for_net_ratio": ((for_buy - for_sell) / total) if total > 0 else np.nan,
        "n_rows": float(len(rows)),
        "avg_trade": (total / len(rows)) if rows else np.nan,
        "max_trade": biggest,
        "max_trade_share": (biggest / total) if total > 0 else np.nan,
    }


def fetch_day(code: str, date: str, max_pages: int) -> Optional[dict]:
    """Ambil satu saham-hari; kembalikan agregat + cakupan (coverage)."""
    first = fetch_page(code, date, 1)
    if not first or not isinstance(first.get("data"), list):
        return None
    total_tx = int(first.get("total") or 0)
    total_pages = int(first.get("total_pages") or 1)
    pages = min(total_pages, max_pages)

    rows: List[dict] = list(first["data"])
    for p in range(2, pages + 1):
        if _state["stopped"]:
            break
        nxt = fetch_page(code, date, p)
        if not nxt or not isinstance(nxt.get("data"), list) or not nxt["data"]:
            break
        rows.extend(nxt["data"])

    m = _row_metrics(rows)
    m.update({
        "code": code,
        "date": date,
        "total_tx": float(total_tx),
        "total_pages": float(total_pages),
        "pages_fetched": float(pages),
        "coverage": (len(rows) / total_tx) if total_tx > 0 else np.nan,
        "full": 1.0 if pages >= total_pages else 0.0,
    })
    # Jendela penutupan = halaman 1 saja (data diurutkan terbaru dulu).
    cl = _row_metrics(first["data"])
    m.update({
        "cl_ng_share": cl["ng_share"],
        "cl_agg_imb": cl["agg_imb"],
        "cl_value": cl["value_sum"],
    })
    return m


# ---------------------------------------------------------------------------
# 3. HARGA & EXCESS RETURN KE DEPAN
# ---------------------------------------------------------------------------

def load_prices(codes: List[str], years: int = 2) -> Tuple[Dict[str, pd.Series], pd.Series]:
    """Harga penutupan harian saham + IHSG (dipakai untuk hasil ke depan)."""
    from backtest_rs import _fetch_yahoo, _norm  # type: ignore

    out: Dict[str, pd.Series] = {}
    idx = None

    def work(code: str):
        df = _fetch_yahoo(f"{code}.JK", f"{years}y")
        if df is None or df.empty:
            return code, None
        df = _norm(df)
        return code, df["Close"]

    with ThreadPoolExecutor(max_workers=6) as ex:
        for code, series in ex.map(work, codes):
            if series is not None:
                out[code] = series

    ih = _fetch_yahoo("^JKSE", f"{years}y")
    if ih is not None and not ih.empty:
        idx = _norm(ih)["Close"]
    else:
        idx = pd.Series(dtype=float)
    return out, idx


def attach_forward(A: pd.DataFrame, prices: Dict[str, pd.Series], idx: pd.Series,
                   horizons: Tuple[int, ...] = (1, 5, 20)) -> pd.DataFrame:
    """Tempelkan excess return ke depan (saham - IHSG) untuk tiap horizon.

    IHSG diselaraskan ke kalender saham dengan reindex+ffill supaya posisi bar
    tidak pernah bergeser (penyebab off-by-one yang dulu membuat sinyal offset).
    """
    A = A.copy()
    A["date"] = pd.to_datetime(A["date"])
    have_idx = idx is not None and not idx.empty
    rows = []
    for code, g in A.groupby("code"):
        px = prices.get(code)
        if px is None or px.empty:
            continue
        px = px.copy()
        px.index = pd.to_datetime(px.index).normalize()
        px = px[~px.index.duplicated(keep="last")].sort_index()
        if have_idx:
            ih = idx.copy()
            ih.index = pd.to_datetime(ih.index).normalize()
            ih = ih[~ih.index.duplicated(keep="last")].sort_index()
            ih = ih.reindex(ih.index.union(px.index)).ffill().reindex(px.index).ffill()
        else:
            ih = None

        g = g.copy()
        g["dt"] = g["date"].dt.normalize()
        pos = px.index.searchsorted(g["dt"].values)
        ok = (pos >= 0) & (pos < len(px) - 1)
        ok &= np.asarray(px.index[pos.clip(max=len(px) - 1)] == g["dt"].values)
        g = g[ok].copy()
        pos = pos[ok]
        close0 = px.values[pos]
        for h in horizons:
            valid = pos + h < len(px)
            fwd = np.full(len(g), np.nan)
            if valid.any():
                fwd[valid] = px.values[pos[valid] + h] / close0[valid] - 1.0
            g[f"fwd{h}"] = fwd
            if ih is not None:
                ibase = ih.values[pos]
                g[f"exc{h}"] = np.where(valid, fwd - (ih.values[np.clip(pos + h, 0, len(px) - 1)] / ibase - 1.0), np.nan)
            else:
                g[f"exc{h}"] = fwd
        rows.append(g)
    return pd.concat(rows, ignore_index=True) if rows else A


# ---------------------------------------------------------------------------
# 4. STATISTIK: IC PER TANGGAL (diklasteskan supaya tidak signifikan palsu)
# ---------------------------------------------------------------------------

def ic_by_date(D: pd.DataFrame, metric: str, outcome: str) -> Tuple[float, float, int]:
    """Rata-rata IC Spearman lintas tanggal + t-stat (per tanggal = 1 observasi).

    Spearman dihitung sebagai korelasi Pearson atas peringkat, supaya scipy
    tidak dibutuhkan (lingkungan venv proyek ini tidak memasang scipy).
    """
    ics = []
    for _, g in D.groupby("date"):
        s = g[[metric, outcome]].dropna()
        if len(s) < 8:
            continue
        if s[metric].nunique() < 3 or s[outcome].nunique() < 3:
            continue
        ic = s[metric].rank().corr(s[outcome].rank())
        if not np.isnan(ic):
            ics.append(ic)
    if len(ics) < 3:
        return np.nan, np.nan, len(ics)
    arr = np.array(ics, dtype=float)
    return float(arr.mean()), float(arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr)))), len(arr)


def quintile_spread(D: pd.DataFrame, metric: str, outcome: str) -> None:
    """Rata-rata hasil ke depan per kuintil metrik (dihitung relatif per tanggal)."""
    parts = []
    for _, g in D.groupby("date"):
        s = g[[metric, outcome]].dropna()
        if len(s) < 10 or s[metric].nunique() < 5:
            continue
        try:
            q = pd.qcut(s[metric].rank(method="first"), 5, labels=False)
        except Exception:
            continue
        parts.append(s[outcome].groupby(q).mean())
    if not parts:
        return
    avg = pd.concat(parts, axis=1).mean(axis=1)
    print(f"  kuintil 1 (terendah) .. 5 (tertinggi) untuk {metric}: "
          + "  ".join(f"Q{int(k)+1}={v*100:+.2f}%" for k, v in avg.items()))


# ---------------------------------------------------------------------------
# 5. MODE
# ---------------------------------------------------------------------------

def probe(codes: List[str]) -> None:
    """Verifikasi bentuk balasan endpoint apa adanya (jalankan ini lebih dulu!)."""
    print("== PROBE: bentuk balasan /api/done-details ==")
    code = codes[0]
    dates = fetch_dates(code)
    print(f"  /api/done-details/dates?code={code} -> {len(dates)} tanggal; contoh: {dates[:5]}")
    date = dates[-1] if dates else None
    data = fetch_page(code, date, 1)
    if not data:
        print("  !! tidak ada balasan. Cek key/kuota.")
        return
    print(f"  keys: {sorted(data.keys())}")
    print(f"  code={data.get('code')} date={data.get('date')} total={data.get('total')} "
          f"per_page={data.get('per_page')} total_pages={data.get('total_pages')}")
    rows = data.get("data") or []
    if rows:
        print(f"  contoh baris (1 dari {len(rows)}):")
        print("   ", json.dumps(rows[0], ensure_ascii=False))
    boards = pd.Series([str(r.get("market_board")) for r in rows]).value_counts().to_dict()
    acts = pd.Series([str(r.get("action")) for r in rows]).value_counts().to_dict()
    print(f"  market_board pada halaman ini: {boards}")
    print(f"  action pada halaman ini: {acts}")


def cost(codes: List[str], date: Optional[str] = None) -> None:
    """Ukur harga kuota: berapa request untuk satu saham-hari (perlu FULL pull)."""
    print("== COST: berapa request per saham-hari ==")
    print(f"  anggaran {BUDGET} request; kuota akun Anda dibagi dengan aplikasi produksi.")
    total_pages = 0
    for code in codes:
        dates = fetch_dates(code)
        d = date or (dates[-1] if dates else None)
        if not d:
            print(f"  {code}: tidak ada tanggal tersedia, lewati")
            continue
        first = fetch_page(code, d, 1)
        if not first:
            print(f"  {code}: gagal")
            continue
        tp = int(first.get("total_pages") or 1)
        tt = int(first.get("total") or 0)
        total_pages += tp
        ng = _row_metrics(first.get("data") or [])["ng_share"]
        ng_txt = f"; porsi NG di halaman penutupan {ng*100:.1f}%" if ng == ng else ""
        print(f"  {code} {d}: {tt:,} transaksi -> {tp} halaman ({tp*PER_PAGE:,} baris){ng_txt}")
    print(f"  total request untuk {len(codes)} saham-hari termahal: {total_pages}")
    if total_pages:
        print(f"  dengan kuota 21.000/hari: ~{21000/total_pages:.0f} saham-hari penuh per hari")


def study(codes: List[str], days: int, max_pages: int, workers: int) -> None:
    """Studi penuh: agregat order flow vs excess return ke depan."""
    print("== STUDI ORDER FLOW ==")
    print(f"  anggaran {BUDGET} request · maks {max_pages} halaman/saham-hari · {days} hari terakhir")

    tasks: List[Tuple[str, str]] = []
    for code in codes:
        dates = fetch_dates(code)[-days:]
        for d in dates:
            tasks.append((code, d))
        print(f"  {code}: {len(dates)} tanggal")

    if not tasks:
        print("  tidak ada tanggal tersedia; jalankan --probe dulu.")
        return

    print(f"  total saham-hari: {len(tasks)} (estimasi mulai {time.strftime('%H:%M:%S')})")
    results: List[dict] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(lambda t: fetch_day(t[0], t[1], max_pages), tasks):
            done += 1
            if res:
                results.append(res)
            if done % 25 == 0:
                print(f"    {done}/{len(tasks)} · request={_state['requests']} "
                      f"cache={_state['cache_hits']} · {time.strftime('%H:%M:%S')}")
            if _state["stopped"]:
                print("  dihentikan: anggaran request habis.")
                break

    if not results:
        print("  tidak ada data terkumpul.")
        return

    A = pd.DataFrame(results)
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    A.to_csv(OUT_CSV, index=False)
    print(f"\n  {len(A)} saham-hari tersimpan di {OUT_CSV}")
    print(f"  request terpakai: {_state['requests']} (cache: {_state['cache_hits']})")

    full = A[A["full"] == 1.0]
    page1 = full.empty  # mode halaman-1: porsi NG diambil dari jendela penutupan
    ng_col = "cl_ng_share" if page1 else "ng_share"

    print("\n-- 1) DESKRIPTIF: seberapa besar papan NG? --")
    if page1:
        print("  MODE HALAMAN-1: porsi NG diukur dari jendela penutupan. Ini SAH karena")
        print("  seluruh transaksi NG terjadi pasca-penutupan (diuji pada TINS: NG halaman 1")
        print("  = 7,50 M = NG sehari penuh). Kalau ragu, verifikasi dengan --pull-full.")
        print(f"  cakupan transaksi rata-rata: {A['coverage'].mean()*100:.2f}% "
              f"(hanya jendela penutupan, bukan seluruh hari)")
    else:
        print(f"  cakupan penuh: {len(full)} saham-hari (pull seluruh halaman)")
    base = A
    # CATATAN PENTING: `cl_ng_share` = porsi NG DI DALAM jendela penutupan, BUKAN
    # porsi NG sehari penuh. Penyebut yang benar untuk sehari = nilai RG harian,
    # dan itu sudah tersedia di /api/history (terbukti RG-saja). Jangan pakai
    # angka di bawah sebagai "porsi NG harian" -- itu kesalahan yang pernah
    # dibuat di riset ini (TINS: 86% di jendela vs 2,8% sehari).
    print(f"  porsi NG DI JENDELA PENUTUPAN: median {base[ng_col].median()*100:.2f}%, "
          f"p90 {base[ng_col].quantile(0.9)*100:.2f}%, maks {base[ng_col].max()*100:.2f}%")
    print(f"  (nilai NG-nya sendiri = nilai NG sehari penuh; hanya penyebutnya yang berbeda)")
    for thr in (0.5, 0.9):
        n = int((base[ng_col] > thr).sum())
        print(f"    jendela penutupan > {thr*100:>4.0f}% NG: {n:>4} dari {len(base)} saham-hari")
    big = base[base["value_ng"] > 1e9]
    if len(big):
        print(f"  blok negosiasi > Rp1 miliar pada penutupan: {len(big)} dari {len(base)} saham-hari")
        for _, r in big.sort_values("value_ng", ascending=False).head(8).iterrows():
            print(f"    {r['code']:<5} {r['date']}  NG {r['value_ng']/1e9:>8,.2f} M  "
                  f"transaksi {int(r['total_tx']):>7,}  jendela {r['cl_value']/1e9:>7,.2f} M")

    print("\n-- 2) UKURAN TRANSAKSI (tekstur ritel vs institusi) --")
    for code, g in base.groupby("code"):
        g2 = g.sort_values("date")
        last = g2.iloc[-1]
        print(f"  {code:<5} tx/hari {last['total_tx']:>7,.0f}  "
              f"trade rata-rata {last['avg_trade']/1e6:>8.2f} jt  "
              f"NG penutupan {last[ng_col]*100:5.1f}%  "
              f"blok NG {last['value_ng']/1e9:.3f} M")

    print("\n-- 2) HARGA KE DEPAN --")
    prices, idx = load_prices(sorted(A["code"].unique()))
    D = attach_forward(A, prices, idx)
    print(f"  {len(D)} baris dengan data harga ({len(prices)} saham)")

    print("\n-- 3) DAYA PREDIKSI (IC per tanggal; >0,03 mulai menarik, <0 buang) --")
    for metric in ("ng_share", "cl_ng_share", "agg_imb", "cl_agg_imb", "for_net_ratio",
                   "max_trade_share", "avg_trade", "total_tx", "n_rows"):
        for h in (1, 5, 20):
            ic, t, n = ic_by_date(D, metric, f"exc{h}")
            if np.isnan(ic):
                continue
            flag = "  <-" if abs(ic) > 0.03 else ""
            print(f"  {metric:>16} vs exc{h:<2} : IC {ic:+.4f}  t={t:+5.2f}  ({n} tanggal){flag}")

    print("\n-- 4) KUINTIL (relatif per tanggal) --")
    for metric in ("cl_ng_share", "agg_imb", "cl_agg_imb", "total_tx"):
        quintile_spread(D, metric, "exc5")
        quintile_spread(D, metric, "exc20")

    print("\n-- BATASAN (wajib dibaca) --")
    print("  * Hanya ~30 hari perdagangan -> satu rezim pasar. Ini penyaringan awal,")
    print("    BUKAN bukti setara backtest 5 tahun. Jangan jadikan dasar cron/screener.")
    print("  * `action` adalah hasil tick rule dari nomor order bursa, bukan data bid/ask asli.")
    print("  * Dalam mode halaman-1, porsi NG sah sebagai NG harian (NG hanya muncul")
    print("    pasca-penutupan), tetapi agregat nilai/agresor hanya mencakup jendela itu.")


def main() -> None:
    global BUDGET
    ap = argparse.ArgumentParser(description="Riset order flow IDX Edge (done-details)")
    ap.add_argument("--codes", default="", help="daftar kode dipisah koma; kosong = LQ45")
    ap.add_argument("--days", type=int, default=28, help="jumlah hari terakhir per saham (maks ~30)")
    ap.add_argument("--max-pages", type=int, default=1,
                    help="halaman per saham-hari (1 = mode halaman-1, hemat kuota)")
    ap.add_argument("--pull-full", action="store_true",
                    help="tarik semua halaman (mahal; untuk verifikasi asumsi halaman-1)")
    ap.add_argument("--budget", type=int, default=8000, help="batas request (kuota dibagi app produksi)")
    ap.add_argument("--workers", type=int, default=4, help="paralelisme request")
    ap.add_argument("--probe", action="store_true", help="cetak bentuk balasan endpoint")
    ap.add_argument("--cost", action="store_true", help="ukur harga kuota per saham-hari")
    args = ap.parse_args()

    BUDGET = args.budget
    max_pages = 10_000 if args.pull_full else args.max_pages
    codes = [c.strip().upper() for c in args.codes.split(",") if c.strip()]
    if not codes:
        from api.index import IDX_LIQUID_TICKERS  # type: ignore
        codes = list(IDX_LIQUID_TICKERS)

    if args.probe:
        probe(codes)
    elif args.cost:
        cost(codes)
    else:
        study(codes, args.days, max_pages, args.workers)


if __name__ == "__main__":
    main()
