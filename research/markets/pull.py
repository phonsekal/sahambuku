#!/usr/bin/env python3
"""Tarik OHLCV harian saham AS & crypto ke cache lokal (dijalankan di GitHub Actions).

Kenapa ada
----------
Vercel TIDAK bisa memindai ratusan/ribuan ticker saat request: setiap ticker = satu
panggilan jaringan, dan Yahoo membatasi IP datacenter (di mesin pengembang saja
yfinance sudah `YFRateLimitError`). Karena itu penarikan dilakukan di LUAR runtime
request — persis pola yang sudah dipakai `fundamentals_pull.py` untuk IDX.

Sumber data:
  * Yahoo chart API (utama, AS & crypto) — satu simbol per permintaan via curl_cffi.
  * yfinance (jalur alternatif, AS & crypto) — batch per potongan.    * Kraken OHLC (cadangan crypto ke-2, tanpa kunci) — dipakai hanya bila Yahoo gagal
    untuk koin itu. Terbukti pernah tidak terjangkau dari jaringan tertentu.

Pasar AS punya dua daftar: `us` (saham biasa) dan `etf` (ETF terdaftar). Keduanya
memakai tarik-menarik yang sama; yang berbeda hanya universe dan pasar ukurnya.
  * CoinGecko market_chart (cadangan crypto ke-3, tanpa kunci) — dipakai untuk sisa
    koin yang TIDAK ada di Yahoo maupun Kraken (mis. HYPE, WLFI, ASTER). Batasannya
    ditulis di `coingecko_daily()`: hanya harga+volume harian (tanpa OHLC).

CATATAN SUMBER: tiap frame diberi `df.attrs["source"]` (yahoo/kraken/coingecko),
sehingga cakupan yang ditulis ke meta bisa menyebut dari mana tiap koin berasal —
dan koin yang datanya hanya close (CoinGecko) ditandai `approx_close_only`.

KETAHANAN: hasil per potongan disimpan sebagai checkpoint di
`research/.cache/markets/parts/{market}/part_NNN.pkl`. Kalau penarikan terputus
(mis. kena rate-limit di ticker ke-1200), jalankan ulang — potongan yang sudah ada
dilewati. `--merge` menyatukan semua potongan menjadi satu panel.

Jalankan:
    .venv/bin/python research/markets/pull.py --market us --limit 300
    .venv/bin/python research/markets/pull.py --market us            # seluruh pasar
    .venv/bin/python research/markets/pull.py --market etf           # seluruh ETF AS
    .venv/bin/python research/markets/pull.py --market crypto --top 100
    .venv/bin/python research/markets/pull.py --market us --merge    # gabung checkpoint
"""

from __future__ import annotations

import argparse
import json
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
COINGECKO_CHART = "https://api.coingecko.com/api/v3/coins/{id}/market_chart"
# Sumber yang datanya HANYA harga harian (tanpa OHLC) -> open=high=low=close.
# Dipakai untuk menandai koin di meta cakupan supaya breakout pada koin itu tidak
# terbaca seolah memakai high intraday.
CLOSE_ONLY_SOURCES = {"coingecko"}


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


def _covered_codes(pdir: str) -> set:
    """Kode yang sudah punya data di checkpoint mana pun (untuk menentukan sisa)."""
    covered = set()
    for f in sorted(x for x in os.listdir(pdir) if x.endswith(".pkl")):
        try:
            with open(os.path.join(pdir, f), "rb") as fh:
                covered.update((pickle.load(fh).get("frames") or {}).keys())
        except Exception:
            continue
    return covered


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
                # Balasan KOSONG diperlakukan sebagai gagal, bukan sebagai "tidak ada
                # data": saat rate-limit, yfinance mengembalikan frame kosong tanpa
                # melempar error. Kalau itu dianggap selesai, potongan itu hilang diam-diam.
                if raw is None or raw.empty:
                    raise RuntimeError("balasan kosong (kemungkinan rate-limit)")
                got = raw
                break
            except Exception as exc:
                # Backoff sengaja pendek (5/10/20 dtk): potongan yang gagal karena
                # rate-limit jarang pulih dalam hitungan detik, dan backoff panjang
                # membuat satu run habis waktunya hanya untuk menunggu.
                wait = 5 * (2 ** attempt)       # 5s, 10s, 20s
                msg = str(exc)[:110]
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


def yahoo_daily(symbol: str, period: str = "5y", timeout: int = 25) -> Optional[pd.DataFrame]:
    """OHLCV harian dari Yahoo chart API via curl_cffi (impersonasi browser).

    KENAPA JALUR INI, BUKAN yfinance: yfinance dari IP datacenter cepat ditolak
    (terukur: batch 250 ticker gagal SELURUHNYA, dan penarikan berhenti di ~23 dari
    99 potongan). Repo ini sudah membuktikan jalur curl_cffi LOLOS blokir itu untuk
    kutipan intraday (lihat `_yahoo_chart_cffi` di api/index.py). Jalur yang sama
    dipakai di sini dengan interval harian, satu simbol per permintaan.

    Harga disesuaikan (adjclose/close) supaya split & dividen tidak jadi lompatan
    palsu di pengukuran — setara `auto_adjust=True` milik yfinance.
    """
    try:
        from curl_cffi import requests as cr
    except Exception:
        return None
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range={period}&interval=1d&events=div%2Csplit")
    try:
        r = cr.get(url, impersonate="chrome", timeout=timeout)
        if r.status_code != 200:
            return None
        res = ((r.json().get("chart") or {}).get("result") or [])
        if not res:
            return None
        res = res[0]
        ts = res.get("timestamp") or []
        ind = res.get("indicators") or {}
        q = (ind.get("quote") or [{}])[0]
        if not ts or not q.get("close"):
            return None
        idx = pd.to_datetime(ts, unit="s", utc=True).tz_convert(None).normalize()
        df = pd.DataFrame({"Open": q.get("open"), "High": q.get("high"),
                           "Low": q.get("low"), "Close": q.get("close"),
                           "Volume": q.get("volume")}, index=idx)
        adj = (ind.get("adjclose") or [{}])
        adjclose = adj[0].get("adjclose") if adj else None
        if adjclose:
            factor = pd.Series(adjclose, index=idx) / df["Close"]
            for c in ("Open", "High", "Low", "Close"):
                df[c] = df[c] * factor
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0.0)
        df.index.name = None
        return df if len(df) >= 60 else None
    except Exception:
        return None


def pull_via_yahoo(codes: List[str], period: str,
                   code_to_yahoo: Dict[str, str], sleep: float = 0.05,
                   retries: int = 2, workers: int = 6) -> Dict[str, pd.DataFrame]:
    """Tarik lewat Yahoo chart API (curl_cffi) dengan beberapa worker.

    KENAPA BERPARALEL: satu permintaan 5 tahun berisi ~1.250 bar dan makan ~1-2 detik.
    Untuk ~5.900 ticker AS, sekuensial = ~3 JAM — melewati batas waktu workflow dan
    sudah terbukti menggantung di percobaan sebelumnya. Dengan beberapa worker,
    waktunya turun ke ~20-30 menit. Jumlah worker sengaja moderat (bawaan 6): terlalu
    agresif memicu pembatasan, dan itu justru mematikan seluruh run.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out: Dict[str, pd.DataFrame] = {}
    n = len(codes)

    def one(code: str):
        sym = code_to_yahoo.get(code, code)
        for attempt in range(retries):
            df = yahoo_daily(sym, period)
            if df is not None:
                return code, df
            time.sleep(0.5 * (attempt + 1))
        return code, None

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(one, c) for c in codes]
        for fut in as_completed(futs):
            done += 1
            code, df = fut.result()
            if df is not None:
                out[code] = df
            if done % 250 == 0:
                print(f"    yahoo: {done}/{n} — dapat {len(out)}")
            time.sleep(sleep)      # pacing di sisi konsumen
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


def coingecko_daily(coin_id: str, days: int = 730) -> Optional[pd.DataFrame]:
    """OHLCV harian dari CoinGecko market_chart (SUMBER KETIGA crypto, tanpa kunci).

    KENAPA ADA: sebagian koin tidak ada di Yahoo (mis. HYPE, WLFI, ASTER) dan tidak
    punya pair Kraken, sehingga dua sumber sebelumnya berhenti di ~91/100. CoinGecko
    mengenal hampir semua koin dan endpoint-nya tanpa kunci.

    BATASAN YANG DITULIS TERBUKA: endpoint ini memberi harga + volume HARIAN, bukan
    OHLC — jadi open=high=low=close=harga harian. Akibatnya `tembus high20` untuk koin
    ini sebenarnya `tembus high dari close`, bukan dari high intraday. Itu sebabnya
    sumbernya dicatat (`approx_close_only`) di meta cakupan, bukan disembunyikan.
    """
    import json as _json
    import urllib.request
    url = (f"{COINGECKO_CHART.format(id=coin_id)}?vs_currency=usd"
           f"&days={days}&interval=daily")
    try:
        req = urllib.request.Request(url, headers=U._UA)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        print(f"  CoinGecko gagal untuk {coin_id}: {str(exc)[:100]}")
        return None
    prices = (data or {}).get("prices") or []
    vols = (data or {}).get("total_volumes") or []
    if len(prices) < 61:
        return None
    p = pd.DataFrame(prices, columns=["t", "close"])
    p.index = pd.to_datetime(p["t"], unit="ms").dt.normalize()
    p = p[~p.index.duplicated(keep="last")]
    v = pd.DataFrame(vols, columns=["t", "volume"])
    v.index = pd.to_datetime(v["t"], unit="ms").dt.normalize()
    v = v[~v.index.duplicated(keep="last")]
    df = pd.DataFrame(index=p.index)
    px = p["close"].astype(float)
    for c in ("Open", "High", "Low", "Close"):
        df[c] = px
    df["Volume"] = v["volume"].reindex(p.index).fillna(0.0).astype(float)
    df = df.dropna(subset=["Close"])
    # Hari terakhir (00:00 UTC) biasanya belum lengkap -> buang supaya tidak jadi sinyal palsu.
    df = df.iloc[:-1] if len(df) > 61 else df
    df.index.name = None
    df.attrs["source"] = "coingecko"
    return df if len(df) >= 60 else None


def pull_market(market: str, limit: Optional[int], period: str, days: int,
                top: int, chunk: int, force: bool = False,
                sweep_max: int = 60, source: str = "yahoo", workers: int = 6) -> None:
    """Tarik satu pasar, simpan per potongan, lalu gabungkan menjadi panel panjang."""
    pdir = _parts_dir(market)
    # code = nama yang DIPAKAI di panel (BTC, AAPL); yahoo = simbol yang diunduh
    # (BTC-USD, AAPL). Untuk crypto keduanya berbeda, dan itu pernah bikin pull
    # diam-diam gagal karena Yahoo tidak mengenal "BTC" polos.
    if market in ("us", "etf"):
        # ETF punya universe sendiri (hanya baris yang ditandai ETF) supaya aturannya
        # diukur terpisah dari saham biasa — campur keduanya akan menyembunyikan
        # perbedaan perilaku keranjang vs emiten tunggal.
        rows = U.us_universe() if market == "us" else U.etf_universe()
        tickers = [r["symbol"] for r in rows]
        code_to_yahoo = {r["symbol"]: r["symbol"] for r in rows}
        code_to_cg: Dict[str, Optional[str]] = {}
    else:
        rows = U.crypto_universe(top)
        tickers = [r["symbol"] for r in rows]
        code_to_yahoo = {r["symbol"]: r["yahoo"] for r in rows}
        # ID CoinGecko per simbol: sumber terakhir untuk koin yang tidak ada di Yahoo/Kraken.
        code_to_cg = {r["symbol"]: r.get("id") for r in rows}
    yahoo_to_code = {v: k for k, v in code_to_yahoo.items()}
    if limit and limit < len(tickers):
        # Ambil sampel TERSEBAR MERATA, bukan `limit` pertama. Daftar NASDAQ Trader
        # terurut abjad, jadi "800 pertama" hanya berisi emiten A-C dan kesimpulannya
        # tidak mewakili pasar. Cara yang sama dipakai `spread_pick` di api/index.py.
        step = len(tickers) / limit
        tickers = [tickers[int(i * step)] for i in range(limit)]
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
        if source == "yahoo":
            # Jalur utama: curl_cffi per simbol (terbukti lolos blokir datacenter).
            frames = pull_via_yahoo(group, period, code_to_yahoo, workers=workers)
        else:
            frames = pull_yfinance(dl, period, chunk=len(dl))
            # kembalikan kunci frame ke nama panel (BTC-USD -> BTC)
            frames = {yahoo_to_code.get(k, k): v for k, v in frames.items()}
        for f in frames.values():
            f.attrs.setdefault("source", "yahoo")
        # Crypto: isi yang gagal dengan Kraken (bila pair-nya ada dan terjangkau).
        if market == "crypto" and krk_idx:
            missing = [t for t in group if t not in frames]
            for sym in missing[:10]:
                pair = U.resolve_kraken(sym, krk_idx)
                if not pair:
                    continue
                df = kraken_ohlc(pair, days=days)
                if df is not None:
                    df.attrs["source"] = "kraken"
                    frames[sym] = df
                    print(f"    {sym}: diisi dari Kraken ({pair})")
        # Crypto: sisa yang tidak ada di Yahoo MAUPUN Kraken dicoba dari CoinGecko.
        if market == "crypto":
            for sym in [t for t in group if t not in frames]:
                cid = code_to_cg.get(sym)
                if not cid:
                    continue
                dg = coingecko_daily(cid, days=days)
                if dg is not None:
                    frames[sym] = dg
                    print(f"    {sym}: diisi dari CoinGecko ({cid}, close-only)")
                time.sleep(2)      # laju CoinGecko tetap sopan
        if not frames:
            # Jangan simpan checkpoint kosong: kalau disimpan, jalankan berikutnya
            # akan menganggap potongan ini "sudah" padahal isinya nol.
            print(f"  [{i}] TIDAK ada data — checkpoint tidak dibuat (akan dicoba lagi)")
            continue
        with open(path, "wb") as fh:
            pickle.dump({"frames": frames, "period": period}, fh)
        print(f"  [{i}] tersimpan {len(frames)} ticker")

    # SWEEP: pada penarikan besar, Yahoo sering membalas kosong di sebagian potongan
    # (terukur: hanya ~23 dari 99 potongan yang berhasil sebelum kena batas). Ticker
    # yang belum dapat dicoba ulang dalam kelompok KECIL dengan jeda lebih panjang —
    # permintaan kecil lebih jarang ditolak, dan checkpoint terpisah (sweep_*) supaya
    # tidak menimpa potongan utama.
    covered = _covered_codes(pdir)
    missing = [t for t in tickers if t not in covered]
    # Simpan ukuran universe (sebelum penyaringan limit) untuk laporan cakupan.
    try:
        with open(os.path.join(CACHE_DIR, f"{market}_universe.json"), "w",
                  encoding="utf-8") as fh:
            # code_to_yahoo dibangun dari universe PENUH (sebelum --limit dipakai),
            # jadi angkanya tidak menyesatkan saat dipakai untuk uji cepat.
            json.dump({"universe": len(code_to_yahoo)}, fh)
    except Exception:
        pass
    if missing:
        limit_groups = max(0, sweep_max)
        print(f"  SWEEP: {len(missing)} ticker belum dapat — paling banyak "
              f"{limit_groups * 15} dicoba ulang pelan-pelan (sisanya tunggu run berikutnya)")
        for i, group in enumerate(_chunks(missing, 15)):
            if i >= limit_groups:
                break
            sp = os.path.join(pdir, f"sweep_{i:03d}.pkl")
            if os.path.exists(sp) and not force:
                continue
            if source == "yahoo":
                frames = pull_via_yahoo(group, period, code_to_yahoo, sleep=0.1,
                                        retries=1, workers=4)
            else:
                dl = [code_to_yahoo.get(t, t) for t in group]
                frames = pull_yfinance(dl, period, chunk=len(dl), retries=1)
                frames = {yahoo_to_code.get(k, k): v for k, v in frames.items()}
            # Crypto: sebagian koin memang TIDAK ada di Yahoo (mis. HYPE, WLFI, ASTER)
            # atau gagal sementara. Kraken dicoba sebagai sumber kedua supaya cakupan
            # tidak berhenti di ~74/100. Kraken hanya dijalankan di SWEEP (kelompok
            # kecil) agar laju permintaannya tetap sopan.
            if market == "crypto" and krk_idx:
                for sym in [t for t in group if t not in frames]:
                    pair = U.resolve_kraken(sym, krk_idx)
                    if not pair:
                        continue
                    dk = kraken_ohlc(pair, days=days)
                    if dk is not None:
                        dk.attrs["source"] = "kraken"
                        frames[sym] = dk
                        print(f"    {sym}: diisi dari Kraken ({pair})")
            for f in frames.values():
                f.attrs.setdefault("source", "yahoo")
            # Sisa yang Yahoo & Kraken tetap tidak punya: CoinGecko (close-only).
            if market == "crypto":
                for sym in [t for t in group if t not in frames]:
                    cid = code_to_cg.get(sym)
                    if not cid:
                        continue
                    dg = coingecko_daily(cid, days=days)
                    if dg is not None:
                        frames[sym] = dg
                        print(f"    {sym}: diisi dari CoinGecko ({cid}, close-only)")
                    time.sleep(2)
            if frames:
                with open(sp, "wb") as fh:
                    pickle.dump({"frames": frames, "period": period}, fh)
            time.sleep(2)
        print(f"  SWEEP selesai — total dapat {len(_covered_codes(pdir))} ticker")

    merge_parts(market)


def merge_parts(market: str) -> pd.DataFrame:
    """Gabungkan semua checkpoint potongan jadi satu panel panjang + simpan .pkl."""
    pdir = _parts_dir(market)
    files = sorted(f for f in os.listdir(pdir) if f.endswith(".pkl"))
    if not files:
        raise SystemExit(f"Tidak ada checkpoint di {pdir}.")
    long_rows: List[pd.DataFrame] = []
    sources: Dict[str, str] = {}
    for f in files:
        with open(os.path.join(pdir, f), "rb") as fh:
            blob = pickle.load(fh)
        for tk, df in (blob.get("frames") or {}).items():
            sources[tk] = str((df.attrs or {}).get("source") or "unknown")
            # Kolom tanggal dibuat EKSPLISIT dari indeks. Sebelumnya mengandalkan
            # nama indeks ("index"/"Date") dan itu pernah gagal `KeyError: 'date'`
            # begitu ada frame yang indeksnya bernama lain — jalur crypto di CI.
            d = df.copy()
            d["date"] = pd.to_datetime(d.index)
            d = d.reset_index(drop=True)
            d.columns = [str(c).lower() for c in d.columns]
            d["code"] = tk
            long_rows.append(d[[c for c in ("code", "date", "open", "high", "low",
                                            "close", "volume") if c in d.columns]])
    if not long_rows:
        raise SystemExit("Checkpoint ada tetapi tidak berisi data.")
    panel = pd.concat(long_rows, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    panel = panel.dropna(subset=["close"]).sort_values(["code", "date"]).reset_index(drop=True)
    # Catat CAKUPAN (berapa dari berapa) supaya "74 koin" tidak pernah terbaca
    # sebagai "100 koin". Ditulis terpisah karena yang menarik hanya tickernya.
    try:
        cov_path = os.path.join(CACHE_DIR, f"{market}_universe.json")
        prev = {}
        if os.path.exists(cov_path):
            with open(cov_path, "r", encoding="utf-8") as fh:
                prev = json.load(fh) or {}
        by_source: Dict[str, int] = {}
        for tk in panel["code"].unique():
            src = sources.get(str(tk), "unknown")
            by_source[src] = by_source.get(src, 0) + 1
        approx = sorted(str(tk) for tk in panel["code"].unique()
                        if sources.get(str(tk)) in CLOSE_ONLY_SOURCES)
        prev.update({"scanned": int(panel["code"].nunique()),
                     "as_of": str(panel["date"].max().date()),
                     "rows": int(len(panel)),
                     "sources": by_source,
                     "approx_close_only": approx})
        with open(cov_path, "w", encoding="utf-8") as fh:
            json.dump(prev, fh)
    except Exception as exc:
        print(f"  (gagal menulis cakupan: {exc})")
    out = _panel_path(market)
    with open(out, "wb") as fh:
        pickle.dump(panel, fh)
    print(f"[{market}] panel: {len(panel):,} saham-hari · {panel['code'].nunique()} ticker "
          f"· {panel['date'].min().date()} -> {panel['date'].max().date()}")
    print(f"[{market}] tersimpan: {out}")
    return panel


def main() -> int:
    ap = argparse.ArgumentParser(description="Tarik OHLCV AS/crypto ke cache lokal")
    ap.add_argument("--market", choices=["us", "etf", "crypto"], required=True)
    ap.add_argument("--limit", type=int, default=None, help="batasi jumlah ticker (uji)")
    ap.add_argument("--period", default="5y", help="jendela yfinance (mis. 5y, max)")
    ap.add_argument("--days", type=int, default=730, help="riwayat Kraken (crypto)")
    ap.add_argument("--top", type=int, default=100, help="jumlah crypto teratas")
    ap.add_argument("--chunk", type=int, default=250,
                    help="ticker per potongan (lebih besar = lebih sedikit permintaan = "
                         "lebih jarang ditolak)")
    ap.add_argument("--workers", type=int, default=6,
                    help="jumlah permintaan paralel ke Yahoo chart API (bawaan 6)")
    ap.add_argument("--source", choices=["yahoo", "yfinance"], default="yahoo",
                    help="jalur penarikan: 'yahoo' (curl_cffi, tahan blokir datacenter) "
                         "atau 'yfinance' (batch)")
    ap.add_argument("--force", action="store_true", help="timpa checkpoint yang ada")
    ap.add_argument("--sweep-max", type=int, default=60,
                    help="batas kelompok (x15 ticker) yang dicoba ulang di SWEEP; "
                         "0 = tanpa sweep. Dibuat berbatas supaya satu run tidak "
                         "menghabiskan waktu hanya untuk menunggu rate-limit")
    ap.add_argument("--merge", action="store_true", help="hanya gabungkan checkpoint")
    args = ap.parse_args()
    if args.merge:
        merge_parts(args.market)
    else:
        pull_market(args.market, args.limit, args.period, args.days,
                    args.top, args.chunk, force=args.force,
                    sweep_max=args.sweep_max, source=args.source,
                    workers=args.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
