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
sys.path.insert(0, HERE)          # supaya registry `state_rules` selalu bisa diimpor
API_DIR = os.path.join(os.path.dirname(RESEARCH), "api")
REPORTS = os.path.join(HERE, "reports")

import state_rules as SR          # noqa: E402  <- SATU sumber daftar aturan keadaan

# Bar proyek (sama dengan LAYAK di dokumen metode IDX).
BAR_T20 = 2.0

# Kriteria yang BENAR-BENAR dipasang di produksi, per pasar. Sengaja daftar
# eksplisit: "lolos bar" saja belum cukup — pemasangan adalah keputusan manusia
# yang harus bisa ditelusuri, dan di sini ia dipisahkan dari pengukuran.
# Aturan keadaan yang DIPASANG untuk saham AS. Ini keputusan MANUSIA (bukan dihitung):
# tiga aturan yang lolos bar proyek. ETF tidak memakai daftar ini — pemasangannya
# dihitung dari hasil ukur ETF sendiri. Diletakkan SEBELUM INSTALLED karena dipakai
# di dalamnya (kalau dipindah ke bawah, import langsung NameError).
US_STATE_INSTALLED = ("pullback_uptrend", "above_sma200", "near_high52")

INSTALLED: Dict[str, List[dict]] = {
    "crypto": [
        {"key": "crypto_momentum_breakout", "label": "Momentum + Breakout (crypto)",
         "rule": "ret 5 hari >= +10% DAN close menembus high 20 hari"},
        {"key": "crypto_breakout", "label": "Tembus high 20 hari (crypto)",
         "rule": "close menembus high tertinggi 20 hari"},
        {"key": "crypto_momentum", "label": "Momentum 5 hari (crypto)",
         "rule": "ret 5 hari >= +10%"},
    ],
    # AS: 3 aturan lolos bar (pullback di uptrend, di atas SMA200, dekat puncak
    # 52m). Dulu sengaja ditahan karena snapshot penuh AS tidak boleh diekspor;
    # sekarang disajikan lewat KEADAAN TURUNAN (api/market_us_state.csv — satu baris
    # per emiten) sehingga bisa dipasang tanpa membengkakkan repo. Batasan yang tetap
    # dibaca: verifikasi eksekusi ada di `markets.us.execution`.
    # Label & deskripsi diambil dari registry bersama supaya tidak menyimpang.
    "us": [{"key": r["key"], "label": r["label"] + " (AS)", "rule": r["desc"]}
           for r in SR.STATE_RULES if r["key"] in US_STATE_INSTALLED],
    # ETF: diisi OTOMATIS di main() dari hasil ukur pasar ETF sendiri (lihat
    # STATE_RULE_CANDIDATES). Dibiarkan kosong di sini supaya tidak ada aturan ETF
    # yang tampil sebelum ada pengukurannya.
    "etf": [],
}

# SEMUA kandidat aturan keadaan (dari registry bersama), dalam bentuk yang dipakai
# untuk memasang aturan ETF OTOMATIS: hanya yang lolos bar di pasar ETF sendiri yang
# dipasang. Jadi menu ETF tidak pernah menyajikan aturan yang belum diukur di ETF, dan
# angkanya tidak diwarisi dari saham biasa (keranjang != emiten tunggal).
STATE_RULE_CANDIDATES = [{"key": r["key"], "label": r["label"] + " (ETF)",
                          "rule": r["rule"]} for r in SR.STATE_RULES]

# Kenapa aturan yang lolos bar belum tentu dipasang. Diukur dengan bar proyek,
# tetapi pemasangan menuntut SATU hal lagi: aturan itu bisa DISAJIKAN dan sudah
# masuk akal secara biaya. Keduanya diperiksa di sini.
INSTALL_NOTE: Dict[str, str] = {
    "us": ("3 aturan lolos bar DAN kini dipasang, setelah dua penghalang lama "
           "dibereskan: (1) jalur penyajian — snapshot penuh AS (~5.800 ticker) tetap "
           "TIDAK diekspor, yang diekspor hanya KEADAAN TURUNAN satu baris per emiten "
           "(close, SMA20/50/200, high52, ret5, + tanda aturan) sehingga ratusan KB, "
           "bukan puluhan MB; (2) verifikasi eksekusi ada di markets.us.execution "
           "(likuiditas, sisa alpha setelah slippage, dan masuk di open besok). "
           "Net20 aturan terlemah memang tipis (+0,04% di study); angkanya dibaca apa "
           "adanya bersama hasil verifikasi, bukan disembunyikan."),
    "crypto": ("7 aturan lolos bar, 3 dipasang. Aturan tren yang lolos bar tetapi "
               "paruh pertamanya negatif (tembus high50, tren naik + tembus high20, "
               "puncak 52m baru) sengaja tidak dipakai."),
    "etf": ("ETF diukur TERPISAH dari saham biasa (keranjang, bukan emiten tunggal), "
            "dan terhadap BENCHMARK PASAR SPY — bukan rata-rata lintas-ETF. Alasannya: "
            "universe ETF heterogen (leveraged, inverse, komoditas, obligasi), sehingga "
            "rata-rata lintas-ETF condong ke produk berleverage dan membuat SEMUA aturan "
            "tampak negatif; itu cacat pembanding, bukan temuan pasar. SEMUA aturan "
            "keadaan yang bisa disajikan diuji, dan hanya yang LOLOS BAR di pasar ETF "
            "sendiri yang dipasang (diisi otomatis oleh summary.py) — angka saham biasa "
            "tidak pernah dipinjam untuk ETF. Bila tidak ada yang lolos, menu ETF "
            "sengaja kosong dan itu ditampilkan apa adanya."),
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


def installed_for_state_market(lolos_bar: Optional[List[str]]) -> List[dict]:
    """Aturan keadaan yang DIPASANG untuk pasar us/etf: yang LOLOS BAR dan bisa disajikan.

    Dipisah dari `main()` supaya keputusan ini bisa diuji tanpa menjalankan seluruh
    pipeline. Untuk ETF ia satu-satunya gerbang: aturan yang belum lolos bar di data
    ETF tidak akan pernah tampil di menu, dan angkanya tidak diwarisi dari saham biasa.
    """
    passed = set(lolos_bar or [])
    return [dict(c) for c in STATE_RULE_CANDIDATES if c["rule"] in passed]


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
        "install_note": INSTALL_NOTE,
        # Registry aturan keadaan (kunci kolom -> nama aturan, label, deskripsi).
        # Ditulis ke JSON supaya api/index.py bisa menyajikan aturan yang lolos bar
        # TANPA menyalin daftarnya (sumber kebenarannya tetap state_rules.py).
        "state_rules": SR.as_registry(),
        "markets": {},
    }
    for market in ("crypto", "us", "etf"):
        rep = parse_report(os.path.join(REPORTS, f"report_{market}.txt"))
        if rep:
            # ETF: pemasangan dihitung dari hasil ukur ETF sendiri (lolos bar), bukan
            # diketik manual — supaya tidak mungkin memasang aturan yang belum diukur.
            if market == "etf":
                INSTALLED["etf"] = installed_for_state_market(rep.get("lolos_bar"))
            # Verifikasi EKSEKUSI (bila ada) ditempelkan ke pasar AS: likuiditas,
            # slippage, dan masuk-di-open — supaya pemasangan bisa diperiksa hasilnya,
            # bukan hanya lolos bar statistik.
            exec_path = os.path.join(API_DIR, f"market_{market}_exec.json")
            if os.path.exists(exec_path):
                try:
                    with open(exec_path, "r", encoding="utf-8") as fh:
                        rep["execution"] = json.load(fh)
                except Exception:
                    pass
            # Cakupan dari export.py (mis. 91 dari 100 koin) supaya angka di dashboard
            # tidak terbaca seolah seluruh universe sudah diukur. AS tidak punya
            # market_us.csv (panel penuh ditolak), jadi cakupannya diambil dari meta
            # KEADAAN TURUNAN (market_us_state_meta.json).
            for meta_name in (f"market_{market}_meta.json",
                              f"market_{market}_state_meta.json"):
                meta_path = os.path.join(API_DIR, meta_name)
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as fh:
                            rep["coverage"] = json.load(fh)
                        break
                    except Exception:
                        pass
            out["markets"][market] = rep
            cov = rep.get("coverage") or {}
            cov_txt = (f" · cakupan {cov.get('scanned')}/{cov.get('universe')} ticker"
                       if cov else "")
            print(f"[{market}] {rep.get('tickers')} ticker · "
                  f"{len(rep['rules'])} aturan diukur · "
                  f"{len(rep['lolos_bar'])} lolos bar · "
                  f"{len(INSTALLED.get(market, []))} dipasang{cov_txt}")
    os.makedirs(API_DIR, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(args.out) / 1024
    print(f"-> {args.out} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
