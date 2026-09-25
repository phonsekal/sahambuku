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

## Hasil pertama (25 Sep 2026)

### Crypto — tren/breakout MENANG; membeli jenuh jual GAGAL

74 koin (top-100 dikurangi stablecoin/token gagal tarik), 5 tahun, 113.302 koin-hari,
biaya 0,5%, return dipotong di ±100% (lihat catatan winsorize di bawah).

| aturan | n | a20 rata-rata | m20 tahan-outlier | t20 | net20 | paruh20 | putusan |
|---|---|---|---|---|---|---|---|
| **mom5d≥10% + tembus high20** | 2.815 | **+5,27%** | **+7,18%** | +8,8 | +6,46% | +1,38/+9,15 | **SEPAKAT** |
| tembus high50 | 2.725 | +4,27% | +6,16% | +7,3 | +6,07% | −0,48/+9,01 | SEPAKAT |
| tren naik + tembus high20 | 2.871 | +4,08% | +6,01% | +8,9 | +5,10% | −0,31/+8,47 | SEPAKAT |
| puncak 52m baru | 1.836 | +3,64% | +6,05% | +7,0 | +5,82% | −0,45/+7,74 | SEPAKAT |
| tembus high20 | 4.684 | +3,40% | +5,03% | +6,9 | +5,09% | +0,28/+6,51 | SEPAKAT |
| mom5d≥10% | 10.168 | +2,52% | +2,98% | +4,8 | +2,10% | +1,47/+3,57 | SEPAKAT |
| volume 2× MA20 | 4.337 | +1,64% | +3,64% | +4,6 | +5,46% | +2,53/+0,75 | SEPAKAT |
| mom1d≥5% | 8.084 | +1,79% | +2,79% | +4,1 | +1,50% | +1,22/+2,36 | SEPAKAT |
| **RSI<30 (jenuh jual)** | 3.429 | **−2,01%** | −0,03% | −5,5 | +1,34% | −3,94/−0,07 | RAPUH |
| dasar 52m baru (kontrol) | 2.473 | −1,19% | +0,41% | −3,3 | +2,18% | −2,38/−0,00 | RAPUH |

Artinya: di crypto, **meneruskan tren yang sudah kuat** bekerja, sementara **membeli
yang jatuh** tidak. Ini kebalikan dari saham AS di bawah, dan sejalan dengan sifat
pasar 24/7 tanpa auto-reject.

### Saham AS — tren/breakout GAGAL (sampel SEBAGIAN)

1.339 saham, 1,4 juta saham-hari, 2021-09 → 2026-09. Hampir SEMUA aturan momentum,
tren, dan breakout berputusan **RAPUH dengan alpha negatif** (`tembus high20` −1,17%,
`mom5d≥10%` −1,39%, `di atas SMA200` −0,31%, `dekat puncak 52m` −0,61%). Satu-satunya
yang positif adalah **`RSI pulih >30`** — membeli setelah jenuh jual mulai pulih
(+2,84 rata-rata / +3,34 tahan-outlier), tetapi **tidak stabil**: paruh pertama
−1,45%, paruh kedua +7,13%.

**Peringatan penting**: sampel AS ini hanya 1.339 dari 5.896 emiten, dan ia terurut
abjad (A–C) karena diambil oleh puller lama sebelum perbaikan. Jadi angkanya **belum
final**. Yang sudah pasti: edge momentum IDX **tidak serta-merta berpindah** ke AS, dan
itulah alasan tahap ukur ini ada.

### Catatan alat ukur yang penting

Angka pertama crypto keluar **tidak masuk akal** (a20 ≈ −2.500%). Penyebabnya bukan
pasar, melainkan alat ukur: pembandingnya rata-rata lintas-koin dari return **mentah**,
sehingga satu koin yang naik ribuan persen membuat semua koin lain tampak −1000%.
Perbaikannya: return dipotong (winsorize) di ±100% **dan** ambangnya ditulis di kepala
laporan. Ini pelajaran yang sama yang dulu melahirkan ukuran tahan-outlier di audit IDX.

## Batasan yang harus dibaca (jujur)

* **yfinance dari IP datacenter praktis tidak bisa dipakai** (batch 250 ticker gagal
  SELURUHNYA). Jalur utama sekarang **Yahoo chart API via curl_cffi** (impersonasi
  browser) — teknik yang sama yang sudah terbukti di `api/index.py` untuk kutipan
  intraday. Terverifikasi dari mesin pengembang: 12/12 ticker AS berhasil, 5 tahun.
  yfinance tetap tersedia sebagai cadangan (`--source yfinance`).
* **Kraken tidak selalu bisa dijangkau** (di mesin ini kadang gagal verifikasi SSL
  jaringan lokal) — ia cadangan, bukan andalan.
* **Angka AS belum final**: laporan `reports/report_us.txt` yang ada berasal dari
  puller LAMA (1.339 ticker, terurut abjad A–C). Setelah perbaikan (curl_cffi +
  sampel tersebar merata), penarikan ulang perlu dijalankan lewat workflow.
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
