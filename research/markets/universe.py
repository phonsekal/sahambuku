#!/usr/bin/env python3
"""Daftar universe untuk pipeline pasar AS & crypto (0 kunci API, 0 biaya).

Kenapa berkas ini ada
---------------------
Screener IDX memakai daftar emiten IDX (`load_idx_tickers` di api/index.py). Untuk
AS & crypto tidak ada padanannya di aplikasi, jadi daftar itu harus punya sumber
yang jelas dan bisa diperiksa. Yang dipakai di sini dipilih karena resmi/gratis dan
TIDAK butuh kunci API:

  * Saham AS  -> berkas direktori simbol resmi NASDAQ Trader
                 (nasdaqlisted.txt + otherlisted.txt). Berisi SEMUA emiten yang
                 tercatat di AS beserta bendera ETF dan "Test Issue", sehingga
                 ETF/warrant/unit bisa dibuang secara eksplisit, bukan ditebak dari
                 nama.
  * Crypto    -> CoinGecko `/coins/markets` untuk peringkat kapitalisasi pasar
                 (top-N yang benar-benar paling besar, bukan daftar statis yang basi).

Pair Kraken dipakai nanti oleh puller (`pull.py`) karena Kraken memberi OHLCV
keyless dengan format yang rapi. `kraken_altname_index()` memetakan nama yang bisa
dibaca manusia (mis. "XBTUSD") ke kunci internal Kraken ("XXBTZUSD") supaya tidak
ada tebak-tebakan nama pair.

Jalankan untuk memeriksa hasilnya tanpa menarik harga:
    .venv/bin/python research/markets/universe.py --market us --limit 20
    .venv/bin/python research/markets/universe.py --market crypto --top 20
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.request
from typing import Dict, List, Optional

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"
KRAKEN_ASSETPAIRS = "https://api.kraken.com/0/public/AssetPairs"

_UA = {"User-Agent": "Mozilla/5.0 (compatible; sahambuku-research/1.0)"}
_SYM_RE = re.compile(r"^[A-Z]{1,5}$")            # hanya simbol saham biasa
# Buang instrumen yang BUKAN saham biasa. ADR ("American Depositary Shares") tetap
# dipakai karena diperdagangkan seperti saham biasa; yang dibuang adalah surat yang
# punya hak/struktur berbeda (warrant, rights, units, preferred, notes, debenture).
_SKIP_NAME = re.compile(
    r"warrants?|\brights?\b|\bunits?\b|preferred|debentures?|\bnotes?\b",
    re.IGNORECASE,
)

# Benchmark tiap pasar. Sengaja dicatat di sini supaya studi memakai pembanding
# yang sama dengan yang ditampilkan aplikasi (bukan indeks yang berbeda-beda).
BENCHMARK = {"us": "SPY", "crypto": "BTC-USD"}

# Stablecoin & token "wrapped/staked": harganya menempel ~1 atau menyalin koin lain,
# sehingga bukan kandidat screener dan akan merusak rata-rata lintas-koin. Dibuang
# EKSPLISIT supaya daftar universe tidak diam-diam berisi aset yang tidak bergerak.
_CRYPTO_SKIP = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDE", "FDUSD", "PYUSD", "USDS",
    "USDD", "FRAX", "GUSD", "USDP", "LUSD", "SUSD", "USDY", "USDT0", "RLUSD",
    "WBTC", "WETH", "WSTETH", "STETH", "RETH", "CBETH", "WEETH", "WBETH",
    "WBNB", "BSC-USD", "CBBTC", "TBTC", "SOLVBTC", "FIGR_HELOC",
}


def _read(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_pipe(text: str) -> List[dict]:
    """Baca berkas direktori NASDAQ yang dipisah tanda '|'.

    Baris pertama = header, baris terakhir = "File Creation Time".
    """
    rows: List[dict] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return rows
    header = lines[0].split("|")
    for ln in lines[1:]:
        if ln.startswith("File Creation Time"):
            continue
        parts = ln.split("|")
        if len(parts) != len(header):
            continue
        rows.append(dict(zip(header, parts)))
    return rows


def us_universe(include_etf: bool = False, include_test: bool = False) -> List[dict]:
    """Emiten tercatat di AS (NASDAQ + bursa lain), minus ETF/warrant/unit bila diminta.

    Mengembalikan list dict {symbol, name, exchange, etf}. Sumbernya berkas resmi
    NASDAQ Trader, jadi bendera ETF dan "Test Issue" dipakai apa adanya — bukan
    disimpulkan dari nama.
    """
    out: List[dict] = []
    try:
        nasdaq = _parse_pipe(_read(NASDAQ_LISTED).decode("utf-8", "replace"))
        other = _parse_pipe(_read(OTHER_LISTED).decode("utf-8", "replace"))
    except Exception as exc:                       # jaringan gagal -> jangan diam
        print(f"[universe] GAGAL membaca berkas NASDAQ Trader: {exc}", file=sys.stderr)
        return out

    for r in nasdaq:
        sym = str(r.get("Symbol", "")).strip().upper()
        name = str(r.get("Security Name", "")).strip()
        if not _SYM_RE.match(sym):
            continue
        if r.get("Test Issue") == "Y" and not include_test:
            continue
        if r.get("ETF") == "Y" and not include_etf:
            continue
        if _SKIP_NAME.search(name):
            continue
        out.append({"symbol": sym, "name": name, "exchange": "NASDAQ",
                    "etf": r.get("ETF") == "Y"})

    for r in other:
        sym = str(r.get("ACT Symbol", "")).strip().upper()
        name = str(r.get("Security Name", "")).strip()
        if not _SYM_RE.match(sym):
            continue
        if r.get("Test Issue") == "Y" and not include_test:
            continue
        if r.get("ETF") == "Y" and not include_etf:
            continue
        if _SKIP_NAME.search(name):
            continue
        out.append({"symbol": sym, "name": name,
                    "exchange": str(r.get("Exchange", "")).strip(),
                    "etf": r.get("ETF") == "Y"})

    # Buang duplikat (simbol sama bisa muncul di kedua berkas) sambil menjaga urutan.
    seen, uniq = set(), []
    for row in out:
        if row["symbol"] in seen:
            continue
        seen.add(row["symbol"])
        uniq.append(row)
    return uniq


def crypto_universe(top: int = 100) -> List[dict]:
    """Top-N crypto menurut kapitalisasi pasar (CoinGecko), + simbol Yahoo & Kraken.

    CoinGecko `/coins/markets` dibatasi percobaan per halaman (maks 250), jadi
    jumlah besar diambil beberapa halaman.
    """
    rows: List[dict] = []
    page = 1
    while len(rows) < top:
        per = 250      # ambil penuh tiap halaman; yang dibuang (stablecoin) bisa banyak
        url = (f"{COINGECKO_MARKETS}?vs_currency=usd&order=market_cap_desc"
               f"&per_page={per}&page={page}&sparkline=false")
        try:
            data = json.loads(_read(url, timeout=30).decode("utf-8", "replace"))
        except Exception as exc:
            print(f"[universe] GAGAL membaca CoinGecko (halaman {page}): {exc}",
                  file=sys.stderr)
            break
        if not isinstance(data, list) or not data:
            break
        for c in data:
            sym = str(c.get("symbol", "")).upper()
            if not sym or sym in _CRYPTO_SKIP:
                continue
            rows.append({
                "id": c.get("id"),
                "symbol": sym,
                "name": c.get("name"),
                "rank": c.get("market_cap_rank"),
                "market_cap": c.get("market_cap"),
                "yahoo": f"{sym}-USD",
                "spot": True,          # crypto diperdagangkan 24/7 (tidak ada sesi tutup)
            })
        page += 1
        if page > 12:                     # pengaman: jangan mengulang tanpa batas
            break
    return rows[:top]


def kraken_altname_index() -> Dict[str, str]:
    """Peta nama-bisa-dibaca -> kunci pair Kraken, mis. {"ETHUSD": "XETHZUSD"}.

    Dipakai supaya `pull.py` tidak menebak nama pair. `altname` Kraken adalah nama
    ramah (XBTUSD, ETHUSD, SOLUSD, ...) sedangkan kuncinya kadang berawalan X/Z.

    Mengembalikan {} bila Kraken tidak bisa dijangkau (mis. sertifikat SSL jaringan
    lokal ditolak); pemanggil HARUS memperlakukan itu sebagai "peta tidak tersedia",
    bukan "tidak ada pair" — puller lalu memakai yfinance untuk crypto.
    """
    try:
        data = json.loads(_read(KRAKEN_ASSETPAIRS, timeout=30).decode("utf-8", "replace"))
    except Exception as exc:
        print(f"[universe] Kraken tidak terjangkau ({exc}) — pair Kraken dilewati.",
              file=sys.stderr)
        return {}
    result = (data or {}).get("result") or {}
    idx: Dict[str, str] = {}
    for key, info in result.items():
        if not isinstance(info, dict):
            continue
        if info.get("quote") not in ("ZUSD", "USD", "USDT", "ZUSDT"):
            continue
        alt = str(info.get("altname") or "").upper()
        if alt:
            idx.setdefault(alt, key)
        # juga daftarkan bentuk NAMAUSD (tanpa awalan Kraken) supaya "BTCUSD" cocok
        base = str(info.get("base") or "")
        for pref in ("X", "Z"):
            if base.startswith(pref) and len(base) > 3:
                base = base[1:]
                break
        if base:
            idx.setdefault(f"{base.upper()}USD", key)
    return idx


# Kraken memakai XBT untuk Bitcoin; ini satu-satunya alias yang benar-benar berbeda.
_KRAKEN_ALIAS = {"BTC": "XBT"}


def resolve_kraken(symbol: str, idx: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Kunci pair Kraken untuk simbol CoinGecko (mis. 'BTC' -> 'XXBTZUSD')."""
    idx = idx if idx is not None else kraken_altname_index()
    sym = symbol.upper()
    for cand in (f"{sym}USD", f"{_KRAKEN_ALIAS.get(sym, sym)}USD",
                 f"{sym}USDT"):
        if cand in idx:
            return idx[cand]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Periksa universe AS & crypto")
    ap.add_argument("--market", choices=["us", "crypto"], default="us")
    ap.add_argument("--top", type=int, default=100, help="jumlah crypto teratas")
    ap.add_argument("--limit", type=int, default=20, help="cetak contoh sejumlah ini")
    args = ap.parse_args()

    if args.market == "us":
        rows = us_universe()
        print(f"Saham AS (saham biasa, ETF/warrant dibuang): {len(rows)} emiten")
        for r in rows[:args.limit]:
            print(f"  {r['symbol']:<6} {r['exchange']:<6} {r['name'][:60]}")
    else:
        rows = crypto_universe(args.top)
        idx = kraken_altname_index()
        n_pair = sum(1 for r in rows if resolve_kraken(r["symbol"], idx))
        pair_note = (f"{n_pair} punya pair Kraken" if idx else
                     "pair Kraken TIDAK tersedia (puller akan pakai yfinance)")
        print(f"Crypto top-{args.top}: {len(rows)} koin · {pair_note}")
        for r in rows[:args.limit]:
            kr = resolve_kraken(r["symbol"], idx) or "-"
            print(f"  #{r['rank']:<3} {r['symbol']:<6} {r['name'][:22]:<22} "
                  f"yahoo={r['yahoo']:<10} kraken={kr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
