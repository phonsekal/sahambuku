#!/usr/bin/env python3
"""Kalibrasi ulang ambang penyaring ukuran tiket -> api/ticket_thresholds.json.

Kenapa ini ada
--------------
`api/index.py` membuang saham yang ukuran tiketnya (nilai / jumlah transaksi)
berada di kuintil 20% terendah DI DALAM kelas likuiditasnya. Ambangnya berupa
angka TETAP per kelas, sedangkan distribusi residual bergeser seiring waktu.

Uji out-of-sample (research/combo_study.py, Bagian E) menunjukkan ambang tetap
KALAH dari potong-20%-lintas-saham di ketiga kelas:

    kelas          ambang tetap   potong 20% lintas-saham
    SANGAT LIKUID     +1,26%            +1,45%
    LIKUID            +3,04%            +3,37%
    CUKUP             +3,34%            +3,74%

Produksi menganalisis SATU saham per permintaan, jadi kuintil lintas-saham tidak
tersedia saat itu. Karena itu ambangnya dikalibrasi ulang secara BERKALA: skrip
ini menghitung kuintil-20 dari data terbaru dan menulisnya ke
`api/ticket_thresholds.json`, yang otomatis menggantikan angka bawaan di
`api/index.py` saat aplikasi dimuat. Kalau berkasnya tidak ada/rusak, aplikasi
tetap jalan dengan angka bawaan.

Dijalankan oleh .github/workflows/ticket-thresholds.yml (bulanan), dan bisa
dijalankan manual:

    .venv/bin/python scripts/calibrate_ticket_thresholds.py --days 250
    .venv/bin/python scripts/calibrate_ticket_thresholds.py --days 90 --dry-run

Sumber data: endpoint resmi IDX (GetStockSummary), 1 permintaan = 1 hari bursa =
seluruh pasar. TIDAK memakai kuota penyedia pihak ketiga.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api.index as ai  # noqa: E402

CACHE_DIR = os.path.join(HERE, ".cache", "ticket_calib")
OUT_FILE = os.path.join(ROOT, "api", "ticket_thresholds.json")

IDX_URL = "https://www.idx.co.id/primary/TradingSummary/GetStockSummary"
HEADERS = {
    "Referer": "https://www.idx.co.id/en/market-data/trading-summary/broker-summary/",
    "Accept": "application/json, text/plain, */*",
}

_state = {"requests": 0, "cache": 0, "empty": 0, "throttled": 0, "blocked": False}


def _make_fetcher(delay: float, budget: int):
    from curl_cffi import requests as cr

    def fetch_day(day: dt.date) -> Optional[List[dict]]:
        ds = day.strftime("%Y%m%d")
        path = os.path.join(CACHE_DIR, f"{ds}.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    _state["cache"] += 1
                    return json.load(fh).get("data")
            except Exception:
                pass
        if _state["requests"] >= budget or _state["blocked"]:
            return None
        _state["requests"] += 1
        url = f"{IDX_URL}?date={ds}&length=2000&start=0"
        for attempt in range(3):
            try:
                r = cr.get(url, headers=HEADERS, impersonate="chrome", timeout=30)
                if r.status_code == 200 and r.text.strip().startswith("{"):
                    d = r.json()
                    os.makedirs(CACHE_DIR, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as fh:
                        json.dump(d, fh)
                    if not d.get("data"):
                        _state["empty"] += 1
                    if delay:
                        time.sleep(delay)
                    return d.get("data")
                if r.status_code == 429:
                    # IDX membatasi per jendela waktu, bukan per tanggal: setelah kena,
                    # SEMUA permintaan gagal selama beberapa menit. Berhenti rapi
                    # daripada membakar permintaan yang pasti gagal.
                    _state["throttled"] += 1
                    if _state["throttled"] >= 5:
                        _state["blocked"] = True
                        return None
                    return None
                time.sleep(1.5 + attempt + delay)
            except Exception:
                time.sleep(1.5 + attempt + delay)
        return None

    return fetch_day


def build_frame(years_days: int, workers: int, delay: float, budget: int) -> pd.DataFrame:
    fetch_day = _make_fetcher(delay, budget)
    end = dt.date.today()
    start = end - dt.timedelta(days=int(years_days * 365.25 / 250) + 45)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    days = [d for d in days if d.weekday() < 5]

    rows: List[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for day, data in zip(days, ex.map(fetch_day, days)):
            for r in data or []:
                try:
                    v = float(r.get("Value") or 0.0)
                    f = float(r.get("Frequency") or 0.0)
                    if v <= 0 or f <= 0:
                        continue
                    code = str(r.get("StockCode") or "")
                    if len(code) != 4:
                        continue
                    rows.append({"code": code, "date": day,
                                 "value": v, "freq": f})
                except Exception:
                    continue
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Kalibrasi ambang penyaring tiket")
    ap.add_argument("--days", type=int, default=250,
                    help="jumlah hari bursa terakhir yang dipakai (default 250 ~ 1 tahun)")
    ap.add_argument("--quantile", type=float, default=0.20,
                    help="kuantil yang dijadikan ambang (default 0,20 = kuintil terendah)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--delay", type=float, default=0.45,
                    help="jeda sopan antar permintaan; >0 mencegah HTTP 429")
    ap.add_argument("--budget", type=int, default=400,
                    help="batas permintaan jaringan (cache tidak dihitung)")
    ap.add_argument("--out", default=OUT_FILE)
    ap.add_argument("--dry-run", action="store_true",
                    help="hitung & tampilkan saja, jangan tulis berkas")
    args = ap.parse_args()

    print(f"== KALIBRASI AMBANG TIKET (kuantil {args.quantile:.2f}) ==")
    t0 = time.time()
    df = build_frame(args.days, args.workers, args.delay, args.budget)
    print(f"  {len(df):,} saham-hari, request={_state['requests']} "
          f"cache={_state['cache']} kosong={_state['empty']} "
          f"throttle={_state['throttled']} ({time.time()-t0:.0f} dtk)")
    if _state["blocked"]:
        print("  !! IDX membalas 429. Jalankan lagi beberapa menit kemudian "
              "(cache menyimpan progres).")
    if not len(df) or df["date"].nunique() < 25:
        print(f"  data tidak cukup ({df['date'].nunique() if len(df) else 0} tanggal) "
              f"-> TIDAK menulis berkas, aplikasi tetap memakai angka bawaan.")
        return

    # Batasi ke N hari bursa TERAKHIR supaya yang dipakai memang kondisi terkini.
    # Kalau jumlah tanggal yang berhasil ditarik lebih sedikit dari yang diminta
    # (mis. sebagian diblokir 429), pakai saja semuanya -- jangan gagal.
    dates = sorted(df["date"].unique())
    if args.days < len(dates):
        df = df[df["date"] >= dates[-args.days]]
    res = ai.calibrate_ticket_floors(df, quantile=args.quantile)
    floors, meta = res["floors"], res["meta"]

    print(f"  rentang dipakai: {meta.get('data_from')} s/d {meta.get('data_to')} "
          f"({meta.get('n_stock_days'):,} saham-hari)")
    print("  sampel per kelas:", meta.get("samples"))
    print(f"\n  {'kelas':<15} {'ambang bawaan':>14} {'ambang baru':>13} {'selisih':>9}")
    for grade in ("SANGAT LIKUID", "LIKUID", "CUKUP"):
        old = ai.TICKET_RESID_FLOOR_BY_GRADE_DEFAULT.get(grade)
        new = floors.get(grade)
        if new is None:
            print(f"  {grade:<15} {old:>14.4f} {'—':>13}   (sampel kurang, dibiarkan)")
            continue
        print(f"  {grade:<15} {old:>14.4f} {new:>13.4f} {new-old:>+9.4f}")

    if not floors:
        print("\n  tidak ada kelas yang sampelnya cukup -> tidak menulis berkas.")
        return
    # Kelas yang sampelnya kurang tetap memakai ambang bawaan. Berkas ini ditulis
    # LENGKAP (bawaan + hasil kalibrasi) supaya aplikasi tidak perlu menggabung.
    merged = {**ai.TICKET_RESID_FLOOR_BY_GRADE_DEFAULT, **floors}
    payload = {"floors": merged, "meta": {
        **meta,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "days_requested": args.days,
        "calibrated": sorted(floors),
        "note": ("Ambang = kuantil resid tiket per kelas likuiditas dari data IDX "
                 "resmi terbaru. Kelas yang tidak tercantum di `calibrated` masih "
                 "memakai angka bawaan api/index.py."),
    }}
    if args.dry_run:
        print("\n  --dry-run: berkas TIDAK ditulis.")
        print(json.dumps(payload["floors"], indent=2))
        return
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print(f"\n  ditulis: {args.out}")


if __name__ == "__main__":
    main()
