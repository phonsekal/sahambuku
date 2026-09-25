#!/usr/bin/env python3
"""Ubah laporan ukur (teks) menjadi ringkasan JSON kecil di `api/market_study.json`.

Kenapa ada
----------
Dashboard harus bisa menampilkan "apa yang sudah diukur dan apa putusannya" tanpa
mengambil berkas teks dari folder riset (folder itu tidak ikut di-deploy). Jadi
laporan teks yang sudah di-commit diringkas menjadi JSON kecil di `api/`, dan
keputusan **mana aturan yang dipasang** dihitung dengan bar proyek yang SAMA
dengan kriteria IDX, bukan diketik manual:

    alpha > 0  ·  blok t >= +2  ·  net > 0  ·  KEDUA paruh positif

Aturan yang tidak lolos TETAP ditampilkan (dengan putusannya) — supaya daftar ini
tidak bisa dibaca sebagai "semua yang tampil sudah dipasang".

Jalankan:
    .venv/bin/python research/markets/summary.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
API_DIR = os.path.join(os.path.dirname(RESEARCH), "api")
REPORTS = os.path.join(HERE, "reports")

# Bar proyek (sama dengan LAYAK di dokumen metode IDX).
BAR_T20 = 2.0

# Kriteria yang BENAR-BENAR dipasang di produksi, per pasar. Sengaja daftar
# eksplisit: "lolos bar" saja belum cukup — pemasangan adalah keputusan manusia
# yang harus bisa ditelusuri, dan di sini ia dipisahkan dari pengukuran.
INSTALLED: Dict[str, List[dict]] = {
    "crypto": [
        {"key": "crypto_momentum_breakout", "label": "Momentum + Breakout (crypto)",
         "rule": "ret 5 hari >= +10% DAN close menembus high 20 hari"},
        {"key": "crypto_breakout", "label": "Tembus high 20 hari (crypto)",
         "rule": "close menembus high tertinggi 20 hari"},
        {"key": "crypto_momentum", "label": "Momentum 5 hari (crypto)",
         "rule": "ret 5 hari >= +10%"},
    ],
    "us": [],   # belum ada yang lolos bar -> sengaja kosong, bukan lupa
}

_ROW_RE = re.compile(
    r"^(?P<name>.+?)\s{2,}(?P<n>[\d,]+)\s+"
    r"(?P<a5>[+\-][\d.]+)\s+(?P<t5>[+\-][\d.]+)\s+"
    r"(?P<a20>[+\-][\d.]+)\s+(?P<m20>[+\-][\d.]+)\s+(?P<t20>[+\-][\d.]+)\s+"
    r"(?P<net20>[+\-][\d.]+)\s+(?P<halves>\S+)\s+(?P<putusan>\S+)\s*$"
)
_HEAD_RE = re.compile(
    r"^===\s*(?P<market>[A-Z]+)\s*·\s*(?P<tickers>[\d,]+)\s*ticker\s*·\s*"
    r"(?P<rows>[\d,]+)\s*baris\s*·\s*(?P<start>\S+)\s*->\s*(?P<end>\S+)\s*·\s*"
    r"biaya\s*(?P<cost>[\d.]+)%(?:\s*·\s*return dipotong di \+/-(?P<winsor>[\d.]+)%)?"
)


def _num(s: str) -> float:
    return float(s.replace("+", "").replace(",", ""))


def _halves(s: str) -> Optional[List[float]]:
    if "/" not in s:
        return None
    try:
        a, b = s.split("/", 1)
        return [float(a.replace("+", "")), float(b.replace("+", ""))]
    except ValueError:
        return None


def parse_report(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    meta: dict = {}
    rows: List[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("===") and not meta:
                m = _HEAD_RE.match(line.strip())
                if m:
                    d = m.groupdict()
                    meta = {
                        "tickers": int(d["tickers"].replace(",", "")),
                        "rows": int(d["rows"].replace(",", "")),
                        "start": d["start"], "as_of": d["end"],
                        "cost_pct": float(d["cost"]),
                        "winsor_pct": float(d["winsor"]) if d.get("winsor") else None,
                    }
                continue
            m = _ROW_RE.match(line)
            if not m:
                continue
            d = m.groupdict()
            halves = _halves(d["halves"])
            a20, m20, t20, net20 = (_num(d["a20"]), _num(d["m20"]),
                                    _num(d["t20"]), _num(d["net20"]))
            lolos = (a20 > 0 and m20 > 0 and t20 >= BAR_T20 and net20 > 0
                     and halves is not None and halves[0] > 0 and halves[1] > 0)
            rows.append({
                "aturan": d["name"].strip(),
                "n": int(d["n"].replace(",", "")),
                "a5": _num(d["a5"]), "t5": _num(d["t5"]),
                "a20": a20, "m20": m20, "t20": t20, "net20": net20,
                "halves": halves, "putusan": d["putusan"], "lolos_bar": lolos,
            })
    if not meta and not rows:
        return None
    rows.sort(key=lambda r: r["a20"], reverse=True)
    meta["rules"] = rows
    meta["lolos_bar"] = [r["aturan"] for r in rows if r["lolos_bar"]]
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description="Ringkas laporan ukur ke JSON")
    ap.add_argument("--out", default=os.path.join(API_DIR, "market_study.json"))
    args = ap.parse_args()

    out: dict = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bar_note": ("Dipasang hanya bila lolos SEMUA: alpha>0, rata-rata DAN "
                     "tahan-outlier positif, blok t >= +2, net>0, kedua paruh positif."),
        "installed": INSTALLED,
        "markets": {},
    }
    for market in ("crypto", "us"):
        rep = parse_report(os.path.join(REPORTS, f"report_{market}.txt"))
        if rep:
            out["markets"][market] = rep
            print(f"[{market}] {rep.get('tickers')} ticker · "
                  f"{len(rep['rules'])} aturan diukur · "
                  f"{len(rep['lolos_bar'])} lolos bar · "
                  f"{len(INSTALLED.get(market, []))} dipasang")
    os.makedirs(API_DIR, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(args.out) / 1024
    print(f"-> {args.out} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
