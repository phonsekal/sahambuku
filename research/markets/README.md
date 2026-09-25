# Pasar AS & crypto — tahap UKUR (belum ada menu)

Folder ini adalah **tahap pengukuran** untuk rencana menambahkan screener saham AS
dan crypto. Sesuai keputusan proyek ini — *"ukur dulu, baru bangun menu"* — yang
dibangun di sini adalah **pipeline data + mesin ukur**, bukan tab/menu aplikasi.
Menu hanya dipasang untuk aturan yang LOLOS pengukuran (dua ukuran sekaligus), dan
sampai sekarang belum ada satu pun yang dipasang.

## Kenapa tidak langsung bikin menu

1. **Vercel tidak bisa memindai ratusan/ribuan ticker saat request.** Screener IDX
   dilayani dari daftar emiten + kuota IDX Edge. Untuk AS/crypto setiap ticker =
   satu panggilan jaringan, dan function Vercel berbatas 10-60 detik. Selain itu
   Yahoo membatasi IP datacenter — di mesin pengembang saja `yfinance` langsung
   melempar `YFRateLimitError`.
2. **Kriteria IDX tidak bisa dipakai apa adanya.** `run_screener` di
   `api/index.py` terikat IDX: regime memakai IHSG, kelas likuiditas memakai Rp,
   bandarmology dari Broker Summary IDX. Untuk AS/crypto perlu kriteria sendiri.
3. **Aturan yang menang di IDX belum tentu menang di pasar lain.** Saham AS punya
   celah *overnight* dan jam tutup; crypto diperdagangkan 24/7 dengan volatilitas
   jauh lebih tinggi. Karena itu strateginya diukur, bukan diwarisi.

## Isi

| berkas | gunanya |
|---|---|
| `universe.py` | daftar universe: saham AS + crypto top-N, plus penolakan pair Kraken |
| `pull.py` | tarik OHLCV harian ke cache lokal, per potongan, dengan checkpoint |
| `study.py` | ukur strategi kandidat dengan DUA ukuran (rata-rata + tahan-outlier) |
| `.github/workflows/markets-data.yml` | penarik terjadwal + penulis laporan ke `reports/` |

## Sumber data (dipilih karena gratis & tanpa kunci API)

* **Saham AS** — berkas direktori simbul resmi NASDAQ Trader
  (`nasdaqlisted.txt` + `otherlisted.txt`): memuat **bendera ETF dan "Test Issue"**,
  jadi ETF/warrant/units/rights dibuang lewat bendera & nama, bukan ditebak.
  Hasil: **~5.900 saham biasa** (setelah ADR tetap dipertahankan).
  Harga: **yfinance** (jalur utama).
* **Crypto** — **CoinGecko** `/coins/markets` untuk peringkat kapitalisasi pasar
  (top-100). Stablecoin & token *wrapped/staked* dibuat eksplisit (daftar
  `_CRYPTO_SKIP`) karena harganya menempel ~1 atau menyalin koin lain.
  Harga: **yfinance** (`BTC-USD`, …); **Kraken OHLC** sebagai cadangan tanpa kunci.

## Cara menjalankan

```bash
# periksa universe (tanpa menarik harga)
.venv/bin/python research/markets/universe.py --market us --limit 20
.venv/bin/python research/markets/universe.py --market crypto --top 100 --limit 20

# tarik harga (di CI; dari mesin lokal sering kena rate-limit Yahoo)
.venv/bin/python research/markets/pull.py --market crypto --top 100 --chunk 25
.venv/bin/python research/markets/pull.py --market us --chunk 60
.venv/bin/python research/markets/pull.py --market us --merge      # gabung checkpoint

# ukur
.venv/bin/python research/markets/study.py --market crypto
.venv/bin/python research/markets/study.py --selftest              # tanpa jaringan
```

## Yang SUDAH terverifikasi (25 Sep 2026)

* `universe.py` — universe AS dan crypto bisa ditarik dari sumber aslinya
  (5.896 saham AS; 100 koin crypto setelah stablecoin dibuang). **Terverifikasi.**
* `study.py --selftest` **LULUS**: mesin ukur mendeteksi *edge* yang sengaja
  ditanam di data sintetis (`a20=+0,64`, `m20=+0,51`, `t20=+32,2`). **Terverifikasi.**
* Pipeline crypto **end-to-end** pernah berhasil (3 ticker: BTC/ETH/USDT, 5.478
  saham-hari, 2021-09 → 2026-09) dan studinya menghasilkan tabel. **Terverifikasi
  pada sampel kecil.**
* Ketahanan gagal: saat penarikan terputus, checkpoint kosong **tidak** disimpan
  dan jalankan ulang melanjutkan dari potongan yang sudah ada. **Terverifikasi.**

## Batasan yang harus dibaca (jujur)

* **Penarikan massal dari mesin pengembang tidak bisa diandalkan.** Diuji:
  3 ticker crypto berhasil, 25 ticker langsung kena `YFRateLimitError`. Karena itu
  penarikan besar **wajib** lewat GitHub Actions. Artinya: **angka pengukuran AS &
  crypto yang sebenarnya BELUM ada** — laporan pertama baru terbit setelah workflow
  dijalankan.
* **Kraken tidak selalu bisa dijangkau** (di mesin ini kadang gagal verifikasi SSL
  jaringan lokal) — ia cadangan, bukan andalan.
* **Riwayat**: yfinance 5 tahun; Kraken ~2 tahun (cadangan). P/E & P/B pasar AS
  belum diintegrasikan (kunci FMP yang ada **tidak** memberi akses riwayat harga;
  endpoint-nya membalas kosong), jadi pengukuran saat ini berbasis harga+volume.
* **Survivorship bias** tetap ada: yang diukur hanya emiten yang masih tercatat.
* **Pembanding** di sini adalah rata-rata SELURUH panel pada tanggal yang sama
  (satu kelas), bukan kelas likuiditas seperti audit IDX — metabolit ukurnya sama
  (`criteria_audit.block_t`, `median_excess`, `holdout`), sehingga angkanya tidak
  bisa dibandingkan langsung dengan tabel IDX.

## Kriteria kandidat yang diukur (`study.py`)

Momentum (1d ≥3%/≥5%, 5d ≥10%), tembus high 20/50, di atas SMA20/50/200, tren naik
SMA20>SMA50, golden cross 50/200, RSI <30 & pemulihan >30, dekat/puncak 52 minggu,
volume 2× MA20, pullback di uptrend, gabungan momentum+breakout, *mean-reversion*
di atas SMA200, dan dua **kontrol** (dasar 52 minggu baru, dasar 52 minggu baru).
Dua kontrol itu penting: kalau kontrol ikut "menang", yang bekerja bukan aturannya
melainkan arah pasar.

## Tahap berikutnya (belum dikerjakan)

1. **Baca laporan** dari workflow, lalu putuskan aturan mana yang lolos.
2. **Endpoint** `/api/screener` yang sadar-pasar (`market=us|crypto`) tanpa
   komponen IDX, membaca snapshot hasil CI (bukan menarik saat request).
3. **Snapan produksi**: workflow menulis hanya *sinyal hari ini* (kecil) ke repo,
   pola yang sama dengan `api/fundamentals.json` — bukan panel penuh.
4. **Tab dashboard** untuk AS & crypto.
