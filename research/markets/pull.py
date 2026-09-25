#!/usr/bin/env python3
"""Tarik OHLCV harian saham AS & crypto ke cache lokal (dijalankan di GitHub Actions).

Kenapa ada
----------
Vercel TIDAK bisa memindai ratusan/ribuan ticker saat request: setiap ticker = satu
panggilan jaringan, dan Yahoo membatasi IP datacenter (di mesin pengembang saja
yfinance sudah `YFRateLimitError`). Karena itu penarikan dilakukan di LUAR runtime
request — persis pola yang sudah dipakai `fundamentals_pull.py` untuk IDX.

Sumber data:
  * yfinance (utama, AS & crypto) — satu jalur kode, batch per potongan.
  * Kraken OHLC (cadangan crypto, tanpa kunci) — dipakai hanya bila yfinance gagal
    untuk koin itu. Terbukti pernah tidak terjangkau dari jaringan tertentu, jadi
    ia cadangan, bukan andalan.

KETAHANAN: hasil per potongan disimpan sebagai checkpoint di
`research/.cache/markets/parts/{market}/part_NNN.pkl`. Kalau penarikan terputus
(mis. kena rate-limit di ticker ke-1200), jalankan ulang — potongan yang sudah ada
dilewati. `--merge` menyatukan semua potongan menjadi satu panel.

Jalankan:
    .venv/bin/python research/markets/pull.py --market us --limit 300
    .venv/bin/python research/markets/pull.py --market us            # seluruh pasar
    .venv/bin/python research/markets/pull.py --market crypto --top 100
    .venv/bin/python research/markets/pull.py --market us --merge    # gabung checkpoint
"""

from __future__ import annotations

import argparse
import os
import pickle
import sys
import time
from typing import Dict, List, Optional

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import universe as U  # noqa: E402

CACHE_DIR = os.path.join(HERE, "..", ".cache", "markets")
PARTS_DIR = os.path.join(CACHE_DIR, "parts")
OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _parts_dir(market: str) -> str:
    d = os.path.join(PARTS_DIR, market)
    os.makedirs(d, exist_ok=True)
    return d


def _panel_path(market: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{market}_panel.pkl")


def _chunks(seq: List[str], n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _clean_frame(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Rapikan satu blok ticker dari yfinance menjadi DataFrame OHLCV standar."""
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(-1)
    df = df.rename(columns={c: str(c).title() for c in df.columns})
    keep = [c for c in OHLCV if c in df.columns]
    if "Close" not in keep:
        return None
    df = df[keep].dropna(subset=[c for c in ("Open", "High", "Low", "Close") if c in keep])
    df.index = pd.to_datetime(df.index)
    if "Volume" in df.columns:
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0.0)
    return df if len(df) >= 60 else None


def pull_yfinance(tickers: List[str], period: str, chunk: int = 80,
                  retries: int = 3, threads: bool = True) -> Dict[str, pd.DataFrame]:
    """Unduh banyak ticker sekaligus, potongan demi potongan, dengan backoff.

    Mengembalikan {ticker: DataFrame}. Ticker yang gagal tidak menggagalkan
    seluruh proses — ia dilaporkan dan bisa dicoba lagi di jalankan berikutnya.
    """
    import yfinance as yf

    out: Dict[str, pd.DataFrame] = {}
    total = len(tickers)
    for i, group in enumerate(_chunks(tickers, chunk)):
        got = None
        for attempt in range(retries):
            try:
                raw = yf.download(group, period=period, interval="1d",
                                  auto_adjust=True, group_by="ticker",
                                  progress=False, threads=threads)
                got = raw
                break
            except Exception as exc:
                wait = 5 * (2 ** attempt)
                msg = str(exc)[:120]
                print(f"    yfinance percobaan {attempt + 1} gagal ({msg}) — tunggu {wait}s")
                time.sleep(wait)
        if got is None or got.empty:
            print(f"    yfinance tidak mengembalikan data untuk {len(group)} ticker")
            continue
        if isinstance(got.columns, pd.MultiIndex):
            for tk in group:
                try:
                    sub = got[tk]
                except Exception:
                    continue
                c = _clean_frame(sub)
                if c is not None:
                    out[tk] = c
        else:
            c = _clean_frame(got)
            if c is not None and len(group) == 1:
                out[group[0]] = c
        print(f"    progres {min((i + 1) * chunk, total)}/{total} — total dapat {len(out)} ticker")
        time.sleep(1.0)          # jeda sopan antar potongan
    return out


def kraken_ohlc(pair: str, days: int = 730) -> Optional[pd.DataFrame]:
    """OHLCV harian dari Kraken (cadangan crypto, tanpa kunci API).

    Kraken membatasi ~720 candle per permintaan, jadi permintaan diulang sambil
    memakai nilai `since` hingga riwayat `days` hari terpenuhi (maks beberapa
    halaman). Dikembalikan None bila Kraken tidak bisa dijangkau.
    """
    import json
    import urllib.request

    start = int(time.time()) - days * 86400
    frames: List[pd.DataFrame] = []
    since = start
    for _ in range(4):
        url = (f"https://api.kraken.com/0/public/OHLC?pair={pair}"
               f"&interval=1440&since={since}")
        try:
            req = urllib.request.Request(url, headers=U._UA)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:
            print(f"  Kraken gagal untuk {pair}: {str(exc)[:100]}")
            return None
        res = (data or {}).get("result") or {}
        rows = None
        for k, v in res.items():
            if k != "last":
                rows = v
                break
        if not rows:
            break
        df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close",
                                         "vwap", "volume", "count"])
        df.index = pd.to_datetime(df["t"], unit="s")
        for c in OHLCV:
            df[c] = pd.to_numeric(df[c.lower()], errors="coerce")
        frames.append(df[OHLCV])
        last = int(res.get("last") or 0)
        if last <= since or len(rows) < 700:
            break
        since = last
        time.sleep(1.2)
    if not frames:
        return None
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    # candle terakhir biasanya belum selesai -> buang supaya tidak jadi sinyal palsu
    return out.iloc[:-1] if len(out) > 61 else None


def pull_market(market: str, limit: Optional[int], period: str, days: int,
                top: int, chunk: int, force: bool = False) -> None:
    """Tarik satu pasar, simpan per potongan, lalu gabungkan menjadi panel panjang."""
    pdir = _parts_dir(market)
    # code = nama yang DIPAKAI di panel (BTC, AAPL); yahoo = simbol yang diunduh
    # (BTC-USD, AAPL). Untuk crypto keduanya berbeda, dan itu pernah bikin pull
    # diam-diam gagal karena Yahoo tidak mengenal "BTC" polos.
    if market == "us":
        rows = U.us_universe()
        tickers = [r["symbol"] for r in rows]
        code_to_yahoo = {r["symbol"]: r["symbol"] for r in rows}
    else:
        rows = U.crypto_universe(top)
        tickers = [r["symbol"] for r in rows]
        code_to_yahoo = {r["symbol"]: r["yahoo"] for r in rows}
    yahoo_to_code = {v: k for k, v in code_to_yahoo.items()}
    if limit:
        tickers = tickers[:limit]
    if not tickers:
        raise SystemExit(f"Universe {market} kosong — periksa koneksi ke sumber.")

    print(f"[{market}] {len(tickers)} ticker · period={period} · chunk={chunk}")
    krk_idx = U.kraken_altname_index() if market == "crypto" else {}

    parts = list(_chunks(tickers, chunk))
    for i, group in enumerate(parts):
        path = os.path.join(pdir, f"part_{i:03d}.pkl")
        if os.path.exists(path) and not force:
            print(f"  [{i}] checkpoint ada — dilewati")
            continue
        print(f"  [{i}] menarik {len(group)} ticker ...")
        dl = [code_to_yahoo.get(t, t) for t in group]
        frames = pull_yfinance(dl, period, chunk=len(dl))
        # kembalikan kunci frame ke nama panel (BTC-USD -> BTC)
        frames = {yahoo_to_code.get(k, k): v for k, v in frames.items()}
        # Crypto: isi yang gagal dengan Kraken (bila pair-nya ada dan terjangkau).
        if market == "crypto" and krk_idx:
            missing = [t for t in group if t not in frames]
            for sym in missing[:10]:
                pair = U.resolve_kraken(sym, krk_idx)
                if not pair:
                    continue
                df = kraken_ohlc(pair, days=days)
                if df is not None:
                    frames[sym] = df
                    print(f"    {sym}: diisi dari Kraken ({pair})")
        if not frames:
            # Jangan simpan checkpoint kosong: kalau disimpan, jalankan berikutnya
            # akan menganggap potongan ini "sudah" padahal isinya nol.
            print(f"  [{i}] TIDAK ada data — checkpoint tidak dibuat (akan dicoba lagi)")
            continue
        with open(path, "wb") as fh:
            pickle.dump({"frames": frames, "period": period}, fh)
        print(f"  [{i}] tersimpan {len(frames)} ticker")

    merge_parts(market)


def merge_parts(market: str) -> pd.DataFrame:
    """Gabungkan semua checkpoint potongan jadi satu panel panjang + simpan .pkl."""
    pdir = _parts_dir(market)
    files = sorted(f for f in os.listdir(pdir) if f.endswith(".pkl"))
    if not files:
        raise SystemExit(f"Tidak ada checkpoint di {pdir}.")
    long_rows: List[pd.DataFrame] = []
    for f in files:
        with open(os.path.join(pdir, f), "rb") as fh:
            blob = pickle.load(fh)
        for tk, df in (blob.get("frames") or {}).items():
            d = df.reset_index().rename(columns={"index": "date", "Date": "date"})
            d.columns = [str(c).lower() for c in d.columns]
            d["code"] = tk
            long_rows.append(d[[c for c in ("code", "date", "open", "high", "low",
                                            "close", "volume") if c in d.columns]])
    if not long_rows:
        raise SystemExit("Checkpoint ada tetapi tidak berisi data.")
    panel = pd.concat(long_rows, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    panel = panel.dropna(subset=["close"]).sort_values(["code", "date"]).reset_index(drop=True)
    out = _panel_path(market)
    with open(out, "wb") as fh:
        pickle.dump(panel, fh)
    print(f"[{market}] panel: {len(panel):,} saham-hari · {panel['code'].nunique()} ticker "
          f"· {panel['date'].min().date()} -> {panel['date'].max().date()}")
    print(f"[{market}] tersimpan: {out}")
    return panel


def main() -> int:
    ap = argparse.ArgumentParser(description="Tarik OHLCV AS/crypto ke cache lokal")
    ap.add_argument("--market", choices=["us", "crypto"], required=True)
    ap.add_argument("--limit", type=int, default=None, help="batasi jumlah ticker (uji)")
    ap.add_argument("--period", default="5y", help="jendela yfinance (mis. 5y, max)")
    ap.add_argument("--days", type=int, default=730, help="riwayat Kraken (crypto)")
    ap.add_argument("--top", type=int, default=100, help="jumlah crypto teratas")
    ap.add_argument("--chunk", type=int, default=80, help="ticker per potongan")
    ap.add_argument("--force", action="store_true", help="timpa checkpoint yang ada")
    ap.add_argument("--merge", action="store_true", help="hanya gabungkan checkpoint")
    args = ap.parse_args()
    if args.merge:
        merge_parts(args.market)
    else:
        pull_market(args.market, args.limit, args.period, args.days,
                    args.top, args.chunk, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
