# Pasar AS & ETF & crypto — pipeline UKUR + pemindai

Folder ini adalah **tahap pengukuran** untuk screener saham AS, ETF, dan crypto,
sekaligus rumah pipeline yang menyajikan hasilnya. Sesuai keputusan proyek — *"ukur
dulu, baru bangun menu"* — mesin ukur dibangun lebih dulu, lalu menu hanya dipasang
untuk aturan yang **LOLOS pengukuran (dua ukuran sekaligus) DAN bisa disajikan**.

Hasil akhirnya: **crypto punya 3 aturan terpasang** dan **saham AS punya 3 aturan
terpasang**. AS disajikan lewat **keadaan turunan** (satu baris per emiten), bukan
panel penuh — lihat bagian "Yang sudah dipasang".

**ETF diperlakukan sebagai pasar TERSENDIRI** (bukan sebagian dari AS). ETF = keranjang,
bukan emiten tunggal, jadi aturannya diukur sendiri; menu ETF hanya terisi bila ada
aturan yang lolos bar **di data ETF** — angka saham biasa tidak dipinjam untuk ETF.

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
| `universe.py` | daftar universe: saham AS + **ETF** + crypto top-N, plus penolakan pair Kraken |
| `pull.py` | tarik OHLCV harian ke cache lokal, per potongan, dengan checkpoint (crypto: Yahoo → Kraken → CoinGecko) |
| `study.py` | ukur strategi kandidat dengan DUA ukuran (rata-rata + tahan-outlier) untuk `us`, `etf`, `crypto` |
| `state_rules.py` | **satu sumber** daftar aturan keadaan (kunci kolom, nama aturan, label, deskripsi) |
| `backtest_screener.py` | backtest AKURASI: ukur ulang aturan yang disajikan screener, bandingkan dengan `api/market_study.json` |
| `execute_us.py` | verifikasi EKSEKUSI aturan AS/ETF (`--market us|etf`): likuiditas, slippage, masuk-di-open |
| `export.py` | ekspor snapshot ringkas crypto + **keadaan turunan** AS & ETF (satu baris/ticker) |
| `summary.py` | ringkas laporan teks → `api/market_study.json` + putusan lolos-bar |
| `.github/workflows/markets-data.yml` | penarik terjadwal + penulis laporan ke `reports/` |

## Sumber data (dipilih karena gratis & tanpa kunci API)

* **Saham AS** — berkas direktori simbul resmi NASDAQ Trader
  (`nasdaqlisted.txt` + `otherlisted.txt`): memuat **bendera ETF dan "Test Issue"**,
  jadi ETF/warrant/units/rights dibuang lewat bendera & nama, bukan ditebak.
  Hasil: **~5.896 saham biasa** (ADR tetap dipertahankan).
  Harga: **Yahoo chart API via `curl_cffi`** (jalur utama), yfinance cadangan.
* **ETF** — sumber universe yang SAMA (bendera ETF NASDAQ Trader), tetapi diambil
  baris yang **bertanda ETF saja** (`etf_universe()`). ETF diukur terpisah dari saham
  biasa; warrant/units/notes tetap dibuang lewat `_SKIP_NAME`.
* **Crypto** — **CoinGecko** `/coins/markets` untuk peringkat kapitalisasi pasar
  (top-100). Stablecoin & token *wrapped/staked* dibuat eksplisit (daftar
  `_CRYPTO_SKIP`) karena harganya menempel ~1 atau menyalin koin lain.
  Harga: **Yahoo chart API**, lalu **Kraken OHLC**, lalu **CoinGecko market_chart**
  sebagai sumber ke-3 tanpa kunci. CoinGecko dipakai untuk koin yang tidak ada di
  Yahoo maupun Kraken (mis. HYPE, WLFI, ASTER); batasannya bahwa ia hanya memberi
  harga+volume harian (tanpa OHLC) ditulis di `coingecko_daily()` dan koin itu
  ditandai `approx_close_only` di meta cakupan — bukan disembunyikan.

## Cara menjalankan

```bash
# periksa universe (tanpa menarik harga)
.venv/bin/python research/markets/universe.py --market us --limit 20
.venv/bin/python research/markets/universe.py --market etf --limit 20
.venv/bin/python research/markets/universe.py --market crypto --top 100 --limit 20

# tarik harga (di CI; dari mesin lokal sering kena rate-limit Yahoo)
.venv/bin/python research/markets/pull.py --market crypto --top 100 --chunk 25
.venv/bin/python research/markets/pull.py --market us --chunk 250 --workers 6
.venv/bin/python research/markets/pull.py --market etf --chunk 250  # universe ETF
.venv/bin/python research/markets/pull.py --market us --merge      # gabung checkpoint

# ukur
.venv/bin/python research/markets/study.py --market us
.venv/bin/python research/markets/study.py --market etf
.venv/bin/python research/markets/study.py --selftest              # tanpa jaringan

# verifikasi eksekusi aturan AS/ETF (likuiditas, slippage, masuk-di-open)
.venv/bin/python research/markets/execute_us.py
.venv/bin/python research/markets/execute_us.py --market etf

# sajikan
.venv/bin/python research/markets/export.py --market crypto --bars 220
.venv/bin/python research/markets/export.py --market us --state   # keadaan turunan AS
.venv/bin/python research/markets/export.py --market etf --state  # keadaan turunan ETF
.venv/bin/python research/markets/summary.py

# uji GERBANG aturan keadaan AS/ETF (tanpa jaringan, tanpa panel harga)
.venv/bin/python -m unittest discover -s tests -t . -v
```

## Yang SUDAH terverifikasi (25 Sep 2026)

* `universe.py` — universe AS dan crypto bisa ditarik dari sumber aslinya
  (5.896 saham AS; 100 koin crypto setelah stablecoin dibuang). **Terverifikasi.**
* `study.py --selftest` **LULUS**: mesin ukur mendeteksi *edge* yang sengaja
  ditanam di data sintetis (`a20=+0,64`, `m20=+0,51`, `t20=+32,2`). **Terverifikasi.**
* **Penarikan PENUH 5.794 saham AS** lewat workflow (run `36100556818`, sukses;
  6.325.200 baris). **Terverifikasi.**
* Ketahanan gagal: saat penarikan terputus, checkpoint kosong **tidak** disimpan
  dan jalankan ulang melanjutkan dari potongan yang sudah ada. **Terverifikasi.**
* Produksi: `/api/markets/study` 200 (crypto 91 ticker + AS 5.794 ticker),
  `/api/markets/crypto/screener` 200, `/api/markets/us/screener` 200 (dari keadaan
  turunan). Diuji lokal: kriteria tak dikenal → 422; saringan memilih emiten yang benar
  dan menyertakan bukti ukur tiap aturan.
* `export.py --market us --state` diuji lokal pada panel sintetis: menulis satu baris
  per emiten dengan SMA20/50/200, high52, ret5, dan tanda SEMUA kandidat aturan
  keadaan (dari `state_rules.py`).
* `execute_us.py` diuji lokal pada panel sintetis: mengukur likuiditas, net per tingkat
  slippage, dan net masuk-di-open tanpa error. Verifikasi yang sama dijalankan untuk
  `--market etf` (menulis `api/market_etf_exec.json`).
* `export.py --market etf --state` diuji lokal pada panel sintetis: menulis
  `api/market_etf_state.csv` dengan kolom yang SAMA seperti AS (close, SMA20/50/200,
  high52, ret5, tanda semua kandidat aturan keadaan), tetapi dari panel ETF — bukan
  dari panel AS.
* `summary.py` + `api/index.py` diuji lokal: aturan ETF yang **lolos bar di pasar ETF**
  otomatis muncul sebagai kriteria `/api/markets/etf/screener`; kriteria tak dikenal
  → 422 dan daftar pilihan yang benar ditampilkan; pasar tanpa aturan lolos → 503.
* `tests/test_markets_state.py` **20 uji LULUS**: gerbang aturan keadaan (ETF tidak
  meminjam angka AS), rute `/api/markets/etf/screener`, 503/422, kunci kolom
  `export.py` disejajarkan dengan kandidat `summary.py`, dan registry produksi
  (`api/index.py`) terbukti membaca SEMUA kandidat dari `api/market_study.json`
  dengan cadangan 3 aturan lama bila JSON belum punya kunci `state_rules`.
* `coingecko_daily()` diuji dengan balasan tiruan: 70 titik harian → 69 bar (hari
  terakhir dibuang), sumber ditandai `coingecko`.

## Hasil UKUR penuh

### Saham AS — tren/RENDAH-VOLATILITAS menang, momentum & breakout GAGAL

**5.794 saham · 6.325.200 saham-hari · 2021-09-27 → 2026-09-24 · biaya 0,3% ·
return dipotong ±100%.** Ini bukan sampel sebagian: seluruh universe ditarik.

| aturan | n | a5 | t5 | a20 | m20 | t20 | net20 | paruh20 | putusan |
|---|---|---|---|---|---|---|---|---|---|
| **pullback di uptrend** | 836.533 | +0,09 | +21,4 | **+0,54** | **+0,64** | +30,2 | **+0,42** | +0,47/+0,61 | **SEPAKAT** |
| **di atas SMA200** | 1.918.759 | +0,06 | +16,9 | **+0,47** | **+0,54** | +30,8 | **+0,28** | +0,32/+0,62 | **SEPAKAT** |
| **dekat puncak 52m** | 742.415 | −0,01 | −0,8 | +0,20 | +0,44 | +6,4 | +0,04 | +0,08/+0,32 | **SEPAKAT** |
| di atas SMA50 | 2.064.188 | −0,02 | −4,6 | +0,13 | +0,31 | +7,8 | **−0,30** | +0,10/+0,16 | SEPAKAT |
| di atas SMA20 | 2.101.047 | −0,07 | −6,6 | +0,09 | +0,29 | +3,4 | **−0,42** | +0,21/−0,02 | SEPAKAT |
| tren naik (SMA20>50) | 1.272.341 | −0,05 | −3,4 | +0,00 | +0,20 | +0,0 | −0,40 | −0,06/+0,06 | TIPIS |
| golden cross 50/200 | 11.795 | −0,13 | −1,8 | −0,08 | −0,06 | −0,3 | −0,26 | −0,46/+0,30 | RAPUH |
| tren naik + tembus high20 | 173.394 | −0,31 | −6,1 | −0,36 | +0,04 | −4,1 | −0,49 | −0,49/−0,23 | RAPUH |
| tembus high20 | 264.630 | −0,42 | −10,0 | −0,41 | +0,03 | −5,0 | −0,54 | −0,22/−0,60 | RAPUH |
| puncak 52m baru | 161.342 | −0,26 | −13,2 | −0,42 | +0,14 | −4,1 | −0,21 | −0,80/−0,05 | RAPUH |
| tembus high50 | 164.340 | −0,40 | −9,3 | −0,53 | −0,05 | −6,1 | −0,56 | −0,59/−0,47 | RAPUH |
| RSI pulih >30 | 54.716 | −0,68 | −8,1 | −1,60 | −1,14 | −6,9 | −0,26 | −1,26/−1,95 | RAPUH |
| mom1d≥3% | 479.069 | −0,66 | −24,7 | −1,66 | −1,98 | −18,3 | −1,32 | −1,65/−1,67 | RAPUH |
| volume 2× MA20 | 202.587 | −0,80 | −34,2 | −1,71 | −0,80 | −23,9 | −1,26 | −1,21/−2,21 | RAPUH |
| mom5d≥10% + tembus high20 | 83.368 | −1,09 | −19,7 | −1,89 | −1,82 | −12,0 | −1,65 | −1,97/−1,82 | RAPUH |
| RSI<30 (jenuh jual) | 203.228 | −0,54 | −19,9 | −1,97 | −1,28 | −16,8 | −0,45 | −1,22/−2,71 | RAPUH |
| mom5d≥10% | 291.040 | −0,98 | −32,9 | −2,53 | −3,05 | −28,7 | −2,34 | −2,51/−2,54 | RAPUH |
| dasar 52m baru (kontrol) | 114.392 | −1,04 | −21,4 | −3,00 | −2,17 | −20,6 | −0,53 | −2,07/−3,93 | RAPUH |
| mom1d≥5% | 222.200 | −1,14 | −28,8 | −3,11 | −3,90 | −25,0 | −2,37 | −2,96/−3,26 | RAPUH |

Bacaan: di AS yang bekerja adalah **menahan posisi yang sudah berjalan** (di atas
SMA200, pullback di dalam tren naik), bukan **mengejar yang baru meledak**. Hampir
semua aturan momentum/breakout bahkan **negatif di kedua ukuran**, dan kontrol
"dasar 52m baru" ikut negatif — artinya bukan sekadar arah pasar.

Tiga aturan lolos bar proyek (`pullback di uptrend`, `di atas SMA200`,
`dekat puncak 52m`) dan **sudah dipasang** sejak 26 Sep 2026 (lihat bagian berikutnya).
Aturan momentum/breakout yang gagal **tidak** dipasang — termasuk `tembus high20` yang di
crypto justru menang.

### Crypto — tren/breakout MENANG; membeli jenuh jual GAGAL

**91 koin · 122.370 koin-hari · 2021-09-25 → 2026-09-25 · biaya 0,5%.**

| aturan | n | a5 | t5 | a20 | m20 | t20 | net20 | paruh20 | putusan |
|---|---|---|---|---|---|---|---|---|---|
| **mom5d≥10% + tembus high20** | 2.897 | +1,50 | +5,0 | **+5,52** | **+7,52** | +8,6 | +6,41 | +1,46/+9,58 | **SEPAKAT** |
| tembus high50 | 2.777 | +1,24 | +3,6 | +4,45 | +6,41 | +8,6 | +5,96 | −0,46/+9,36 | SEPAKAT |
| tren naik + tembus high20 | 2.927 | +1,15 | +6,1 | +4,23 | +6,24 | +8,1 | +4,99 | −0,29/+8,75 | SEPAKAT |
| puncak 52m baru | 1.874 | +1,46 | +3,1 | +3,93 | +6,41 | +6,6 | +5,77 | −0,08/+7,94 | SEPAKAT |
| **tembus high20** | 4.778 | +1,07 | +3,6 | +3,67 | +5,41 | +7,6 | +5,07 | +0,36/+6,98 | **SEPAKAT** |
| **mom5d≥10%** | 10.615 | +0,88 | +7,6 | +2,72 | +3,09 | +5,3 | +1,92 | +1,42/+4,01 | **SEPAKAT** |
| volume 2× MA20 | 4.500 | +0,61 | +2,0 | +1,81 | +3,91 | +5,4 | +5,24 | +2,33/+1,30 | SEPAKAT |
| mom1d≥5% | 8.448 | +0,57 | +2,4 | +1,80 | +2,78 | +4,0 | +1,36 | +1,14/+2,45 | SEPAKAT |
| golden cross 50/200 | 252 | +0,50 | +0,5 | +1,23 | +5,18 | +0,9 | +5,14 | +0,86/+1,60 | TIPIS |
| mom1d≥3% | 15.556 | +0,25 | +1,4 | +1,12 | +1,64 | +2,8 | +0,58 | +0,38/+1,86 | SEPAKAT |
| dekat puncak 52m | 8.338 | +0,24 | +4,9 | +0,75 | +2,05 | +5,8 | +2,98 | −0,33/+1,83 | SEPAKAT |
| di atas SMA20 | 40.777 | +0,19 | +4,2 | +0,62 | +0,76 | +3,9 | +1,46 | −0,01/+1,26 | SEPAKAT |
| di atas SMA200 | 33.132 | +0,15 | +7,6 | +0,60 | +1,11 | +8,4 | +1,09 | +1,07/+0,14 | SEPAKAT |
| pullback di uptrend | 11.311 | −0,10 | −1,5 | +0,59 | +2,36 | +3,9 | −0,13 | +1,52/−0,34 | SEPAKAT |
| tren naik (SMA20>50) | 22.613 | +0,41 | +4,4 | +0,58 | +1,14 | +5,7 | +1,76 | −0,79/+1,96 | SEPAKAT |
| di atas SMA50 | 38.370 | +0,15 | +3,2 | +0,45 | +0,78 | +4,7 | +0,93 | +0,21/+0,69 | SEPAKAT |
| RSI pulih >30 | 1.120 | −0,18 | −1,2 | −0,05 | +1,49 | −0,1 | +0,16 | −0,52/+0,42 | RAPUH |
| turun 5d≥10% di atas SMA200 | 2.351 | +0,13 | +0,3 | −0,22 | +1,52 | −0,3 | −2,54 | +1,55/−1,99 | RAPUH |
| dasar 52m baru (kontrol) | 2.507 | −0,70 | −9,7 | −1,22 | +0,51 | −3,1 | +2,25 | −2,37/−0,06 | RAPUH |
| RSI<30 (jenuh jual) | 3.490 | −0,88 | −5,1 | −1,74 | +0,33 | −4,5 | +1,47 | −3,94/+0,46 | RAPUH |

Artinya: di crypto, **meneruskan tren yang sudah kuat** bekerja, sementara **membeli
yang jatuh** tidak. Ini kebalikan dari saham AS, dan sejalan dengan sifat pasar 24/7
tanpa auto-reject.

> Catatan cakupan: run CI terakhir sampai **91 dari 100** koin (naik dari 74 karena
> Kraken aktif di CI). Angka crypto di atas berasal dari run 91-koin itu; jadi ia
> lebih kuat dari tabel 74-koin versi pertama.

### Catatan alat ukur yang penting

Angka pertama crypto keluar **tidak masuk akal** (a20 ≈ −2.500%). Penyebabnya bukan
pasar, melainkan alat ukur: pembandingnya rata-rata lintas-koin dari return **mentah**,
sehingga satu koin yang naik ribuan persen membuat semua koin lain tampak −1000%.
Perbaikannya: return dipotong (winsorize) di ±100% **dan** ambangnya ditulis di kepala
laporan. Ini pelajaran yang sama yang dulu melahirkan ukuran tahan-outlier di audit IDX.

## Yang sudah dipasang di produksi

Keputusan aturan dihitung dengan bar proyek yang sama (`summary.py`), bukan diketik
manual, dan dipisahkan dari pengukuran:

* **Crypto** — 20 aturan diukur, **8 lolos bar, 3 dipasang**:
  `crypto_momentum_breakout` (ret 5 hari ≥ +10% **dan** tembus high 20 hari),
  `crypto_breakout` (tembus high 20 hari), `crypto_momentum` (ret 5 hari ≥ +10%).
  Alasan: `mom5d≥10% + tembus high20` punya alpha h20 +5,52% rata-rata, +7,52%
  tahan-outlier, blok t +8,6, net20 +6,41%, dan positif di KEDUA paruh (+1,46/+9,58).
  Aturan tren yang lolos bar tetapi **paruh pertamanya negatif** (`tembus high50`,
  `tren naik + tembus high20`, `puncak 52m baru`) sengaja TIDAK dipakai.
* **Saham AS** — 20 aturan diukur, **3 lolos bar, 3 dipasang** (sejak 26 Sep 2026):
  `us_pullback_uptrend` (+0,54/+0,64, t +30,2, net +0,42), `us_above_sma200`
  (+0,47/+0,54, t +30,8, net +0,28), `us_near_high52` (+0,20/+0,44, net **+0,04**).

  Dua penghalang yang dulu menahan pemasangan sudah dibereskan:
  1. **Jalur penyajian.** Snapshot penuh AS (~5.800 ticker) tetap **tidak** diekspor.
     Yang diekspor hanya **keadaan turunan** (`api/market_us_state.csv`): satu baris
     per emiten berisi close, SMA20/50/200, high52, ret5, nilai transaksi 20 hari, dan
     tanda aturan keadaan — ratusan KB, bukan puluhan MB. Karena aturan ini **filter
     keadaan** (bukan sinyal harian yang jarang), nilai terakhir per emiten sudah cukup
     untuk menyajikannya.
  2. **Verifikasi eksekusi** (`execute_us.py` → `api/market_us_exec.json`): likuiditas
     (nilai transaksi harian), sisa alpha setelah slippage 0,2/0,5/1,0%, dan apakah
     alpha bertahan bila masuk di **open** hari berikutnya (bukan di close hari sinyal,
     yang tidak bisa dibeli). Hasilnya ditampilkan bersama tabel ukur, bukan disembunyikan.

  Batasan yang tetap dibaca: net20 aturan terlemah cuma **+0,04%** setelah biaya 0,3%,
  dan alpha AS kecil secara absolut (+0,2% s/d +0,5%) — paling rentan terhadap
  slippage. Karena itu ia dipasang sebagai **daftar kandidat** (filter keadaan), dengan
  angka verifikasi eksekusi ikut tampil; bukan sebagai sinyal beli hari ini.

* **ETF** — 20 aturan diukur di panel ETF SENDIRI, lalu yang **lolos bar di ETF**
  dipasang **otomatis** oleh `summary.py` (tidak boleh ada aturan ETF yang tampil tanpa
  pengukuran ETF). Selama belum ada aturan yang lolos bar di data ETF, menu ETF
  **sengaja kosong** dan API menjawab 503 beserta alasannya — itu keadaan yang benar,
  bukan kekurangan data. Semua kandidat aturan kini punya kolom di keadaan turunan,
  jadi aturan mana pun yang lolos bar di ETF langsung bisa disajikan **tanpa mengubah
  kode**: `api/index.py` membaca daftarnya dari `state_rules` di `api/market_study.json`
  (ditulis `summary.py` dari `state_rules.py`), dan hanya yang punya kolom + lolos bar
  di pasar itu yang tampil.

Permukaan produksi:

| bagian | gunanya |
|---|---|
| `GET /api/markets/study` | hasil ukur tiap pasar + aturan yang DIPASANG & yang lolos bar + `install_note` + verifikasi eksekusi |
| `GET /api/markets/crypto/screener?criteria=…` | pemindai crypto dari snapshot (0 kuota) |
| `GET /api/markets/us/screener?criteria=…` | pemindai AS dari keadaan turunan (`all` atau salah satu kunci aturan yang lolos bar, mis. `pullback_uptrend`) |
| `GET /api/markets/etf/screener?criteria=…` | pemindai **ETF** dari keadaan turunan ETF; kriterianya hanya yang lolos bar di ETF |
| `GET /api/markets/analyze/{market}/{ticker}` | analisis satu emiten pasar luar IDX (`us`/`etf` = keadaan turunan, `crypto` = indikator penuh dari snapshot OHLCV); 0 kuota, tanpa jaringan |
| `api/market_crypto.csv` | snapshot 220 bar × 91 koin (di-commit pipeline CI) |
| `api/market_crypto_meta.json` | cakupan: berapa ticker dapat dari berapa + dari sumber mana |
| `api/market_us_state.csv` | keadaan turunan AS, satu baris/emiten (ratusan KB, di-commit pipeline CI) |
| `api/market_etf_state.csv` | keadaan turunan ETF, satu baris/ETF (kolom sama seperti AS) |
| `api/market_us_exec.json` / `api/market_etf_exec.json` | verifikasi eksekusi AS & ETF (likuiditas, slippage, masuk-di-open) |
| `api/market_study.json` | ringkasan hasil ukur + `installed` + `age_days`/`stale` (di-commit pipeline CI) |
| tab **🌐 AS & Crypto** di dashboard | status data, tabel hasil ukur, pemindai crypto, AS, & ETF (klik ticker → buka tab Analisis dengan pasar yang benar) |
| tab **🔎 Analisis** di dashboard | pemilih pasar IDX / AS / ETF / Crypto; IDX lewat jalur penuh, AS/ETF lewat keadaan turunan, crypto lewat snapshot OHLCV |

### Backtest akurasi screener

`backtest_screener.py` mengukur ulang aturan yang DISAJIKAN screener pada panel yang
SAMA yang dipakai `study.py` (mesin ukur dipakai ulang, bukan ditulis ulang), lalu
membandingkan `a20`/`m20`/`net20` dengan `api/market_study.json`. Hasilnya ditulis ke
`reports/backtest_<pasar>.json` dan dijalankan otomatis di pipeline CI:

```bash
.venv/bin/python research/markets/backtest_screener.py --market crypto
.venv/bin/python research/markets/backtest_screener.py --market us
.venv/bin/python research/markets/backtest_screener.py --market etf --all-rules  # uji semua aturan (menu ETF kosong)
```

Yang diukur: n sinyal, rata-rata & median return 20 hari ke depan (excess vs rata-rata
pasar pada tanggal yang sama), blok t, net setelah biaya, kedua paruh waktu, dan
**hit rate** (bagian hari-sinyal dengan excess positif). Kolom "vs study" menyatakan
COCOK/BEDA; bila panel lokal berbeda dari yang diukur CI, kecocokan **tidak diuji**
(itulah sebabnya CI yang menjalankannya, karena hanya di sana panelnya lengkap).

### Cakupan data: 91 dari 100 (disebut apa adanya)

Sepuluh koin teratas yang tidak berhasil ditarik: HYPE, RAIN, USYC, BUIDL, MORPHO,
ASTER, WLFI, U, VVV, EURSAFO, BCAP, PI — sebagian memang tidak ada di Yahoo
("chart API" membalas kosong untuk semua varian simbol) dan sebagian gagal sementara
karena pembatasan. Karena itu:

* puller memakai **Kraken** sebagai sumber kedua (aktif di CI: 91 koin terisi, naik
  dari 74 saat Kraken tidak terjangkau dari mesin lokal);
* puller juga memakai **CoinGecko `market_chart`** sebagai sumber KETIGA untuk koin
  yang tidak ada di Yahoo maupun Kraken (mis. HYPE, WLFI, ASTER). Batasannya:
  CoinGecko hanya memberi harga+volume harian (tanpa OHLC), jadi open=high=low=close
  untuk koin itu dan mereka ditandai `approx_close_only` di meta cakupan;
* jumlah yang benar-benar dapat **ditulis ke `api/market_crypto_meta.json`** dan
  ditampilkan di dashboard sebagai "cakupan 91/100" — supaya tidak pernah terbaca
  sebagai cakupan penuh.

> Catatan: berkas meta ini dulu **tertinggal dari commit otomatis** (workflow hanya
> `git add` CSV + `market_study.json`), sehingga repo menyimpan meta lama 74/260
> sementara snapshot-nya sudah 91/220. Sudah diperbaiki di workflow **dan** berkasnya
> diselaraskan — cakupan yang tampil kini sama dengan isi snapshot.

### Peringatan data basi

Umur snapshot dihitung **di server** (`MARKET_STALE_DAYS = 5`) dan dikirim sebagai
`age_days` + `stale` per pasar, lalu ditampilkan sebagai peringatan di tab. Ambang 5
hari dipilih supaya akhir pekan bursa AS (tutup 2 hari) tidak dianggap keterlambatan.
Diuji: tanggal 24 hari lalu → `stale=True`; 1 hari → `stale=False`.

## Batasan yang harus dibaca (jujur)

* **Rate limit Yahoo nyata & terukur.** `yfinance` dari IP datacenter praktis tidak
  bisa dipakai (batch 250 ticker gagal SELURUHNYA). Jalur utama sekarang **Yahoo chart
  API via `curl_cffi`** (impersonasi browser) — teknik yang sama yang sudah terbukti di
  `api/index.py` untuk kutipan intraday. Terverifikasi: penuh untuk 5.794 ticker AS.
* **Kraken tidak selalu bisa dijangkau dari mesin lokal** (verifikasi SSL jaringan
  lokal), tetapi **bekerja di CI** — karena itu cakupan crypto lebih tinggi di CI.
* **CoinGecko hanya memberi harga+volume harian**, bukan OHLC. Untuk koin yang hanya
  ada di CoinGecko, `tembus high20` sebenarnya `tembus high dari close`, bukan dari
  high intraday — dan itu ditandai `approx_close_only` di `market_crypto_meta.json`.
* **Riwayat**: 5 tahun untuk kedua pasar; Kraken ~2 tahun, CoinGecko ~2 tahun (keduanya
  cadangan). P/E & P/B pasar
  AS belum diintegrasikan (kunci FMP yang ada **tidak** memberi akses riwayat harga;
  endpoint-nya membalas kosong), jadi pengukuran saat ini berbasis harga+volume.
* **Survivorship bias** tetap ada: yang diukur hanya emiten yang masih tercatat.
* **Pembanding** di sini adalah rata-rata SELURUH panel pada tanggal yang sama
  (satu kelas), bukan kelas likuiditas seperti audit IDX — mesin ukurnya sama
  (`criteria_audit.block_t`, `median_excess`, `holdout`), sehingga angkanya tidak
  bisa dibandingkan langsung dengan tabel IDX.
* **Alpha AS sangat kecil secara absolut** (+0,2% s/d +0,5%). Ia lolos bar statistik,
  tetapi nilainya paling rentan terhadap slippage.
* **ETF belum diukur di mesin ini (lokal)** — tidak ada panel ETF di cache, jadi menunya
  masih kosong sampai workflow CI menjalankan `pull/study/execute/export --market etf`.
  Banyak ETF juga tipis likuiditasnya; saringan `MIN_VALUE_USD` membuat hasilnya jujur
  (yang tidak bisa dieksekusi tidak dihitung), tetapi cakupannya bisa lebih kecil dari
  seluruh universe ETF.
* **Analisis ETF memakai jalur `/api/analyze/{ticker}` yang sama** (mis. `SPY`, `QQQ`).
  Endpoint itu mencoba varian `.JK` lebih dulu untuk kode pendek lalu kode aslinya, jadi
  ticker ETF 3 huruf tetap terlayani — tidak ada jalur analisis terpisah untuk ETF.

## Kriteria kandidat yang diukur (`study.py`)

Momentum (1d ≥3%/≥5%, 5d ≥10%), tembus high 20/50, di atas SMA20/50/200, tren naik
SMA20>SMA50, golden cross 50/200, RSI <30 & pemulihan >30, dekat/puncak 52 minggu,
volume 2× MA20, pullback di uptrend, gabungan momentum+breakout, *mean-reversion*
di atas SMA200, dan sebuah **kontrol** (dasar 52 minggu baru). Kontrol itu penting:
kalau kontrol ikut "menang", yang bekerja bukan aturannya melainkan arah pasar.

## Tahap berikutnya

1. **Kalibrasi ulang aturan AS** dengan snapshot yang sudah ada di cache: bandingkan
   hasil pemasangan terhadap pengukuran awal, dan awasi bila net20 aturan terlemah
   turun di bawah nol.
2. **Kerapatan pemindaian AS**: keadaan turunan saat ini diproduksi mingguan. Bila
   dipakai harian, perlu jadwal tarik yang lebih rapat (dan tetap di CI, bukan Vercel).
3. **Crypto lebih luas**: ukur ulang setelah sumber ke-3 aktif supaya angka `91/100`
   dan tabel ukur crypto dinyatakan pada cakupan terbaru (bukan cakupan lama).
4. **Ukur ETF pertama**: jalankan `--market etf` di workflow, lalu baca tabelnya. Bila
   ada aturan yang lolos bar, menu ETF terisi otomatis; bila tidak, menu tetap kosong
   dan itu jawabannya (jangan dipaksa dengan meminjam angka saham biasa).
5. **Survivorship**: emiten/ETF/koin yang delisting belum diuji; ini batasan yang masih
   terbuka untuk ketiga pasar.
