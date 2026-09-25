#!/usr/bin/env python3
"""Tarik fundamental IDX Edge ke cache lokal -- sekali saja, lalu riset jadi 0 kuota.

Kenapa ada
----------
`research/panel.py` memberi harga & volume seluruh pasar sejak 2020 (gratis, dari
ringkasan harian resmi IDX), tetapi TIDAK ada satu pun angka fundamental di sana.
Sumber fundamental yang tersedia di akun kita cuma IDX Edge PRO (endpoint resmi
Arjum), dan tiap saham = 1 permintaan. Jadi:

    hasil riset fundamental = biaya kuota X requests, SEKALI.
    sesudah cache terisi, mengulang riset = 0 permintaan.

Itu sebabnya penarikan dipisah dari pengukuran: `fundamental_study.py` membaca
cache ini dan tidak pernah menyentuh jaringan.

Endpoint yang dipakai (diverifikasi langsung 24 Sep 2026)
--------------------------------------------------------
* GET /api/market-cap?page=N&per_page=50[&date=YYYY-MM-DD]
    - 963 emiten hari ini; `date` BEKERJA (bukan cuma hari ini) -> ini yang
      membuat P/E & P/B bisa diukur POINT-IN-TIME, bukan hanya hari ini.
      Terbukti: date=2025-01-02 -> BREN market_cap 1.267 T (bukan angka 2026).
    - per_page maksimum 50 (per_page=100 -> HTTP 422 "less_than_equal").
    - Isi: code, name, close, listed_shares, market_cap, turnover_ratio.
* GET /api/financial-statements/{code}?report_type=INCOME_STATEMENT|BALANCE_SHEET
                                   &period=annually&limit=12
    - `annually` hanya berisi 4 tahun (FY 2022 .. FY 2025 untuk BBCA);
      `quarterly` berisi 17 kuartal (Q1 2022 .. Q1 2026). Karena yang dibutuhkan
      untuk P/E & pertumbuhan (dan P/B) cuma tahunan, satu permintaan per
      report_type per saham sudah cukup.

Batas kuota
-----------
x-ratelimit-limit = 21.000/hari (header balasan Arjum) dan kuota ini DIBAGI dengan
aplikasi produksi. Seluruh penarikan dihitung terhadap SATU anggaran `--budget`
(bawaan 3.000) yang mencakup market-cap DAN laporan:
  * market-cap = ~20 permintaan per tanggal (per_page 50, ~950 emiten per hari);
  * laporan    = 2 permintaan per emiten (laba-rugi + neraca), jadi seluruh pasar
                 ~1.900 permintaan — itulah sebabnya default 3.000 belum menuntaskan
                 seluruh pasar dalam satu kali jalan.
Skrip berhenti sendiri begitu anggaran tercapai, dan bisa dilanjutkan kapan saja
karena setiap balasan disimpan per berkas.

Pemakaian
---------
  .venv/bin/python research/fundamentals_pull.py --budget 4000
  .venv/bin/python research/fundamentals_pull.py --codes BBCA,BBRI,TLKM
  .venv/bin/python research/fundamentals_pull.py --dates 2024-06-28,2025-01-02

Output (semua di research/.cache/fundamentals/, di-gitignore)
  statements/{code}_{REPORT}.json   balasan mentah per saham per jenis laporan
  mcap/{date}_{page}.json           balasan mentah per tanggal per halaman
  statements.csv                    ringkasan angka (per kode per tahun fiskal)
  mcap_history.csv                  ringkasan market cap (per tanggal per kode)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache", "fundamentals")
STMT_DIR = os.path.join(CACHE, "statements")
MCAP_DIR = os.path.join(CACHE, "mcap")

IDX_URL = os.environ.get("IDX_EDGE_API_URL", "https://stock.arjum.com").strip().rstrip("/")
IDX_KEYS = [k.strip() for k in os.environ.get("IDX_EDGE_API_KEYS", "").split(",") if k.strip()]
PER_PAGE = 50            # maksimum yang diizinkan /api/market-cap
REPORTS = ("INCOME_STATEMENT", "BALANCE_SHEET")

_lock = threading.Lock()
_state = {"requests": 0, "cache": 0, "dead": False, "budget": 0}


def _take_budget() -> bool:
    with _lock:
        if _state["dead"] or _state["requests"] >= _state["budget"]:
            _state["dead"] = True
            return False
        _state["requests"] += 1
        return True


def api_get(path: str, params: Dict[str, Any]) -> Optional[dict]:
    """GET IDX Edge dengan rotasi key + anggaran request. None = gagal/anggaran habis."""
    if not IDX_KEYS:
        raise SystemExit("IDX_EDGE_API_KEYS kosong; isi di .env.local (sudah di-gitignore).")
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{IDX_URL}{path}" + (f"?{qs}" if qs else "")
    for attempt in range(len(IDX_KEYS)):
        if not _take_budget():
            return None
        key = IDX_KEYS[attempt % len(IDX_KEYS)]
        try:
            req = urllib.request.Request(url, headers={"X-API-Key": key, "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                continue
            if exc.code in (401, 403):
                raise SystemExit(f"API key ditolak (HTTP {exc.code}).")
            return None
        except Exception:
            return None
    _state["dead"] = True
    return None


def _read_json(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _write_json(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 1. AMBIL: market cap (termasuk historis) dan laporan tahunan
# ---------------------------------------------------------------------------

def market_cap_page(date: Optional[str], page: int) -> Optional[dict]:
    """Satu halaman /api/market-cap (cache per tanggal+halaman)."""
    tag = date or "latest"
    path = os.path.join(MCAP_DIR, f"{tag}_{page}.json")
    hit = _read_json(path)
    if hit is not None:
        _state["cache"] += 1
        return hit
    params: Dict[str, Any] = {"page": page, "per_page": PER_PAGE}
    if date:
        params["date"] = date
    data = api_get("/api/market-cap", params)
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        _write_json(path, data)
        return data
    return None


def pull_market_cap(date: Optional[str]) -> List[dict]:
    """Seluruh halaman untuk satu tanggal -> daftar baris."""
    first = market_cap_page(date, 1)
    if not first:
        return []
    pages = int(first.get("total_pages") or 1)
    rows = list(first.get("data") or [])
    for page in range(2, pages + 1):
        if _state["dead"]:
            break
        more = market_cap_page(date, page)
        if more:
            rows.extend(more.get("data") or [])
    return rows


def statement(code: str, report: str) -> Optional[dict]:
    """Laporan tahunan satu saham (cache per kode+jenis laporan)."""
    path = os.path.join(STMT_DIR, f"{code}_{report}.json")
    hit = _read_json(path)
    if hit is not None:
        _state["cache"] += 1
        return hit
    data = api_get(f"/api/financial-statements/{code}",
                   {"report_type": report, "period": "annually", "limit": 12})
    if isinstance(data, dict):
        _write_json(path, data)
        return data
    return None


# ---------------------------------------------------------------------------
# 2. BACA: ratakan dict bersarang -> peta "a > b > c" (sama seperti frontend)
# ---------------------------------------------------------------------------

def flatten(node: Any, prefix: str = "", out: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Ratakan laporan bersarang jadi peta jalur -> angka.

    Jalur memakai pemisah " > " supaya identik dengan cara aplikasi web Arjum
    memilih angka (helper `Ax`/`Nx` di bundel frontend-nya), sehingga definisi
    kita dan definisi yang dilihat pengguna di sana tidak berbeda.
    """
    out = {} if out is None else out
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix} > {key}" if prefix else key
            if isinstance(value, (dict, list)):
                flatten(value, path, out)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                out[path] = float(value)
    return out


def pick(flat: Dict[str, float], candidates: Tuple[str, ...]) -> Optional[float]:
    """Nilai pertama yang cocok (persis, lalu akhiran " > nama")."""
    for cand in candidates:
        if cand in flat:
            return flat[cand]
    for cand in candidates:
        suffix = " > " + cand
        for path, value in flat.items():
            if path == cand or path.endswith(suffix):
                return value
    return None


NET_INCOME = ("laba_rugi_yang_dapat_diatribusikan_kepada_entitas_induk", "laba_rugi")
REVENUE = ("penjualan_dan_pendapatan_usaha", "pendapatan_bunga", "pendapatan_usaha")
# Catatan penting soal urutan: `laba_rugi_per_saham > total` adalah wadah, bukan
# angka — nilainya 0,0 pada hampir semua emiten. Terbukti di BBRI: jalur itu 0,0
# padahal EPS sesungguhnya 375. Karena itu sub-total yang diutamakan, dan wadah
# `... > total` TIDAK boleh dipakai (kalau dipakai, P/E seluruh pasar jadi tak hingga).
EPS = ("laba_rugi_per_saham > laba_rugi_per_saham_dilusian > total",
       "laba_rugi_per_saham > laba_per_saham_dasar_diatribusikan_kepada_pemilik_entitas_induk > total",
       "laba_rugi_per_saham > laba_rugi_per_saham_dilusian > "
       "laba_rugi_per_saham_dilusian_dari_operasi_yang_dilanjutkan",
       "laba_rugi_per_saham > laba_per_saham_dasar_diatribusikan_kepada_pemilik_entitas_induk > "
       "laba_rugi_per_saham_dasar_dari_operasi_yang_dilanjutkan")
EQUITY = ("ekuitas > total", "ekuitas")
ASSETS = ("aset > total", "aset")
LIABILITIES = ("liabilitas > total", "liabilitas")


def statement_rows(code: str, report: str, data: dict) -> List[dict]:
    """Baris ringkas (satu baris per tahun fiskal) dari satu balasan laporan."""
    rows: List[dict] = []
    for item in (data.get("items") or []):
        flat = flatten(item.get("data") or {})
        row = {
            "code": code, "report": report,
            "year": item.get("year"), "quarter": item.get("quarter"),
            "label": item.get("label"), "fetched_at": item.get("fetched_at"),
            "net_income": None, "revenue": None, "eps": None,
            "equity": None, "assets": None, "liabilities": None,
        }
        if report == "INCOME_STATEMENT":
            row["net_income"] = pick(flat, NET_INCOME)
            row["revenue"] = pick(flat, REVENUE)
            row["eps"] = pick(flat, EPS)
        elif report == "BALANCE_SHEET":
            row["equity"] = pick(flat, EQUITY)
            row["assets"] = pick(flat, ASSETS)
            row["liabilities"] = pick(flat, LIABILITIES)
        rows.append(row)
    return rows


STMT_COLS = ["code", "report", "year", "quarter", "label", "fetched_at",
             "net_income", "revenue", "eps", "equity", "assets", "liabilities"]
MCAP_COLS = ["date", "code", "name", "close", "listed_shares", "market_cap",
             "turnover_ratio"]


def write_csv(path: str, cols: List[str], rows: List[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c) for c in cols})


def default_dates() -> List[str]:
    """Awal tiap kuartal 2022..2026 — 4 titik setahun, cukup untuk uji per kuartal.

    Sengaja BUKAN harian: uji aturan P/E/PEG cukup dievaluasi tiap kuartal (laporan
    fundamentalnya memang terbit per kuartal), dan tiap tanggal berharga 20 request.
    """
    out: List[str] = []
    for year in range(2022, 2027):
        for month in (1, 4, 7, 10):
            day = f"{year}-{month:02d}-04"      # awal bulan -> lewat hari libur 1-3
            out.append(day)
    return [d for d in out if d <= "2026-09-24"]


def load_all_statements() -> List[dict]:
    """Baca SEMUA berkas laporan di cache (0 jaringan) -> baris ringkas."""
    rows: List[dict] = []
    if not os.path.isdir(STMT_DIR):
        return rows
    for name in sorted(os.listdir(STMT_DIR)):
        if not name.endswith(".json"):
            continue
        code, report = name[:-5].split("_", 1)
        data = _read_json(os.path.join(STMT_DIR, name))
        if isinstance(data, dict):
            rows.extend(statement_rows(code, report, data))
    return rows


def load_all_mcap() -> List[dict]:
    """Baca SEMUA berkas market-cap di cache -> baris ringkas."""
    rows: List[dict] = []
    if not os.path.isdir(MCAP_DIR):
        return rows
    for name in sorted(os.listdir(MCAP_DIR)):
        if not name.endswith(".json"):
            continue
        data = _read_json(os.path.join(MCAP_DIR, name))
        if not isinstance(data, dict):
            continue
        date = data.get("date")
        for entry in (data.get("data") or []):
            rows.append({
                "date": date, "code": entry.get("code"), "name": entry.get("name"),
                "close": entry.get("close"),
                "listed_shares": entry.get("listed_shares"),
                "market_cap": entry.get("market_cap"),
                "turnover_ratio": entry.get("turnover_ratio"),
            })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Tarik fundamental IDX Edge ke cache lokal")
    ap.add_argument("--codes", default="", help="subset kode (koma); kosong = seluruh pasar")
    ap.add_argument("--dates", default="", help="tanggal market-cap (koma); kosong = kuartalan")
    ap.add_argument("--budget", type=int, default=3000, help="batas request hari ini")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-mcap", action="store_true", help="lewati penarikan market-cap")
    ap.add_argument("--summary", action="store_true", help="hanya tulis ulang CSV dari cache")
    args = ap.parse_args()
    _state["budget"] = max(1, args.budget)
    t0 = time.time()

    codes = [c.strip().upper() for c in args.codes.split(",") if c.strip()]
    if not args.no_mcap and not args.summary:
        if not codes:
            rows = pull_market_cap(None)
            codes = sorted({r.get("code") for r in rows if r.get("code")})
            print(f"[mcap] universe terbaru: {len(codes)} kode ({len(rows)} baris)")
        dates = [d.strip() for d in args.dates.split(",") if d.strip()] or default_dates()
        for date in dates:
            if _state["dead"]:
                print("[mcap] anggaran habis, sisa tanggal ditunda (jalankan ulang nanti).")
                break
            got = pull_market_cap(date)
            print(f"[mcap] {date}: {len(got)} baris "
                  f"(request {_state['requests']}, cache {_state['cache']})")

    if not args.summary and codes:
        jobs = [(code, report) for code in codes for report in REPORTS]
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(statement, code, report): (code, report) for code, report in jobs}
            for fut in as_completed(futures):
                done += 1
                if done % 100 == 0:
                    print(f"[stmt] {done}/{len(jobs)} selesai "
                          f"(request {_state['requests']}, cache {_state['cache']})")
                try:
                    fut.result()
                except Exception:
                    pass
        print(f"[stmt] selesai {done}/{len(jobs)}")

    stmt_rows = load_all_statements()
    mcap_rows = load_all_mcap()
    write_csv(os.path.join(CACHE, "statements.csv"), STMT_COLS, stmt_rows)
    write_csv(os.path.join(CACHE, "mcap_history.csv"), MCAP_COLS, mcap_rows)
    codes_stmt = len({r["code"] for r in stmt_rows})
    years = sorted({str(r["year"]) for r in stmt_rows if r.get("year")})
    dates_ok = sorted({r["date"] for r in mcap_rows if r.get("date")})
    print(f"\n[cache] statements.csv: {len(stmt_rows)} baris / {codes_stmt} kode / FY {years}")
    print(f"[cache] mcap_history.csv: {len(mcap_rows)} baris / {len(dates_ok)} tanggal "
          f"({dates_ok[0] if dates_ok else '-'} .. {dates_ok[-1] if dates_ok else '-'})")
    print(f"[kuota] request dipakai: {_state['requests']} (cache hit {_state['cache']})"
          f" / {_state['budget']} dalam {time.time() - t0:.0f} detik")


if __name__ == "__main__":
    sys.exit(main())
