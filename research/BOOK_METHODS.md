# Aturan dari 9 buku di `buku tambahan/` — apa yang sudah dipakai, apa yang dibuang, dan mengapa

Berkas ini merangkum pembacaan sembilan PDF di folder `buku tambahan/`, aturan konkret
yang ada di dalamnya (beserta nomor halaman), dan keputusan untuk masing-masing:
**sudah ada di aplikasi**, **diukur lalu dipakai**, **diukur lalu ditolak**, atau
**belum bisa diukur**.

Alat dan alur kerjanya ada di folder ini juga:

| berkas | gunanya |
|---|---|
| `research/books_extract.py` | tarik teks PDF ke `research/.cache/books/*.txt`; halaman hasil pindai dikenali dan gambarnya disimpan |
| `research/books_ocr.py` | OCR halaman hasil pindai (bisa dilanjutkan per halaman) |
| `research/book_search.py` | cari istilah **beserta nomor halaman**, teks PDF digabung dengan hasil OCR |
| `research/book_rules_study.py` | uji aturan buku di panel harian IDX lokal (0 kuota) |

Semua keluaran ada di `research/.cache/books/` dan tidak masuk git.

## Status buku

| buku | halaman | bentuk | bisa dibaca? |
|---|---|---|---|
| Mark Minervini — *Think & Trade Like a Champion* | 218 | teks | ya, penuh |
| Turtle Trader (edisi Indonesia) | 292 | teks | ya, penuh |
| Tharp — *The New Trading for a Living* | 475 | teks | ya, penuh |
| *Trade Like Jesse Livermore* | 261 | teks | ya, penuh |
| Peter Lynch — *One Up on Wall Street* | 111 | teks | ya, penuh |
| Aaron Brown — *The Poker Face of Wall Street* | 373 | teks | ya, penuh |
| Edianto Ong — *Technical Analysis for Mega Profit* | 385 | **pindai** | via OCR (bahasa Indonesia, mesin OCR di sini hanya punya `eng`) |
| Belvin Tannadi — *Ilmu Saham Biawak VVIP* | 322 | **pindai** | via OCR (idem) |
| `products_14-08-2026…pdf` | 1 | pindai | iklan screener, tidak berisi aturan |

Catatan mutu OCR: angka, nama indikator, dan istilah teknis umumnya terbaca; ejaan
Indonesia bisa melenceng ("yarg" untuk "yang"). Karena itu teks OCR dipakai untuk
**menemukan aturan dan halamannya**, bukan untuk dikutip kata per kata.

---

## 1. Mark Minervini — Trend Template & VCP (hal 103-147)

**Aturan yang ditemukan**

* **Stage-2 saja** (hal 103): jangan beli di stage 1/3/4; >95% saham pemenang besar naik saat Stage-2.
* **Trend Template, 8 syarat** (hal 105-106): (1) harga di atas MA150 & MA200; (2) MA150 > MA200;
  (3) MA200 naik ≥1 bulan (idealnya 4-5 bulan); (4) MA50 di atas MA150 & MA200;
  (5) harga ≥25% di atas low 52 minggu; (6) harga dalam 25% dari high 52 minggu;
  (7) peringkat RS ≥70 (idealnya 90-an) dan garis RS naik ≥6 minggu; (8) harga di atas MA50
  saat keluar dari base.
* **VCP** (hal 109-118): kontraksi volatilitas 2-6 kali dengan kedalaman mengecil
  ("kira-kira setengah kontraksi sebelumnya"), volume mengering di bagian terakhir
  (di bawah rata-rata 50 hari, ada 1-2 hari nyaris tidak ada volume), lalu **pivot buy point**
  ditembus dengan volume bertambah.
* **Ukuran posisi** (hal 142-144): risiko 1,25-2,5% ekuitas per posisi; stop maksimum 10%;
  posisi tidak lebih dari 50%; 20-25% per nama; 4-8 nama (maksimum 10-12).
* **Menjual** (hal 149 dst): jual ke dalam kekuatan; "base count" menentukan apakah saham
  sudah terlambat.

**Yang diukur** (`research/book_rules_study.py`, 989 emiten, 1,6 juta saham-hari, 2020-2026):

| entri | alpha 5 hari (vs kelas likuiditas yang sama) | setelah disaring 8 syarat Stage-2 |
|---|---|---|
| momentum ≥8% | +1,60% | **+4,40%** |
| momentum + breakout 20 hari | +2,90% | **+6,51%** (blok t +12,70; 52,2% untung) |
| breakout 20 hari saja | +0,35% | **+4,13%** (blok t +37,44) |
| Stage-2 sendirian | +0,79% (59 sinyal/hari) | — lemah, jadi **penyaring**, bukan kriteria |

Positif di setiap tahun (2021-2026), dan tetap sama setelah efek ukuran dikeluarkan
(residu-ukuran +6,57% vs +6,51%). Syarat RS saja (peringkat ≥70) sudah menaikkan
momentum+breakout dari +2,90% ke +4,51%.

**Keputusan: DIPAKAI.** Penyaring `stage2=true` di `/api/screener` dan
`/api/screener/preclose`, chip `🧱 Stage-2 n/8` per baris (menyebut syarat mana yang belum
lolos), dan peringkat RS dihitung lintas emiten yang dipindai. Default mati supaya hasilnya
bisa dibandingkan.

**VCP: TIDAK DIPAKAI.** Detektornya (basis ≥10%, kontraksi mengerut ≤0,75x, volume 10 bar
di bawah 0,8x rata-rata, rentang 10 bar ≤8%, harga ≥95% dari puncak 60 bar) hanya
memunculkan 258 kejadian dari 149.108 breakout dengan alpha +0,54% — praktis sama dengan
breakout biasa, dan **nol** kejadian saat digabung momentum (yang butuh bar +8%). Pola
visualnya memang tidak bisa dibaca dari data harian tanpa gambar. Menariknya, **komponen
volume VCP-nya justru terukur kuat** — lihat bagian volume kering di bawah.

---

## 2. Turtle Trader — sistem breakout Donchian (hal 57-64, 268-282)

**Aturan yang ditemukan**

* **Entry**: breakout **20 hari** (Sistem 1) dan **55 hari** (Sistem 2) dari high/low sebelumnya;
  breakout S1 dilewati bila breakout terakhir untung; entry sebelum 55 hari sebagai penyelamat (hal 270-271).
* **Ukuran**: N = ATR; **1N = 1% ekuitas**; stop **2N** dari entry; menambah posisi tiap **½N**
  sampai maksimum 4 unit per pasar, 12 unit per arah (hal 272-275).
* **Keluar**: low **10 hari** (S1) / **20 hari** (S2); **tanpa target laba** (hal 279).
* **Drawdown**: tiap akun turun 10%, ukuran notional dipotong 20% (hal 269).
* **Ekspektasi**: rata-rata untung dibagi rata-rata risiko; sistem harus punya ekspektasi positif
  sebelum dipakai (hal 61-62).
* **Risiko kehancuran** (hal 57): ukuran taruhan menentukan apakah rangkaian rugi menghabiskan modal.

**Yang diukur** (entri momentum+breakout, hasil per perdagangan dalam satuan R, biaya 0,3% sudah dipotong):

| aturan keluar | rata-rata R | median R | %untung | terbaik |
|---|---|---|---|---|
| **rencana aplikasi sekarang** (stop 1,5N, target 2R, maks 5 hari) | **+0,151** | −0,46 | **42,6%** | +2,0 |
| stop 2N, target 2R | +0,095 | −0,30 | 41,7% | +2,0 |
| stop 2N, tanpa target, maks 20 hari | −0,159 | −1,02 | 22,1% | +33,1 |
| **Turtle: stop 2N, trail low 10 hari, maks 20 hari** | **−0,185** | −1,02 | 20,8% | +33,1 |

Kesimpulannya sama juga pada subset Stage-2 (yang paling "trending"): rencana sekarang +0,202
vs Turtle −0,195.

**Keputusan: aturan KELUAR Turtle DITOLAK, aturan UKURAN POSISI DIPAKAI.** Ekor keuntungan
aturan Turtle nyata (+33R) tetapi mediannya −1R dan persen untungnya 20% — pada IDX yang
horizon buktinya 1-5 hari, target tetap + batas waktu lebih baik. Sebaliknya aturan
**ukuran posisi** (1N = 1% ekuitas, stop 2N, potong notional saat drawdown) tidak diuji
karena ia bukan sinyal: ia aritmetika risiko, dan dipakai di `/api/position-size`
beserta tombol `💼 Lot` per baris rencana harian.

---

## 3. Tharp — *The New Trading for a Living*

**Aturan yang ditemukan**

* **Batas risiko 2% ekuitas per perdagangan**, tanpa kecuali: "jarak dari entry ke stop dikali
  ukuran posisi tidak boleh lebih dari 2% ekuitas" (hal 56 dan 110). Bila stop yang logis
  mengharuskan risiko >2%, **lewati** perdagangan itu.
* **Tren vs range** (hal 110): saat trend trading pakai posisi **kecil** dengan stop **lebar**;
  di trading range boleh posisi lebih besar dengan stop lebih rapat.
* **Ekspektasi** (hal 305-307): yang menentukan bukan hasil satu perdagangan, melainkan
  ekspektasi matematis; kerugian adalah biaya bisnis.

**Keputusan: DIPAKAI sebagai batas keras di kalkulator lot.** Aplikasi sebelumnya hanya
memberi harga stop tanpa memberi tahu berapa lot yang muat dalam batas itu — padahal seluruh
audit kriteria menyimpulkan kejadian untung setelah biaya selalu di bawah 50%.

---

## 4. *Trade Like Jesse Livermore*

**Aturan yang ditemukan**

* **Pivotal Point**: harga "call-to-action"; dua jenis — **Reversal** dan **Continuation**
  (CPP = jeda konsolidasi dalam tren naik, tempat menambah posisi) (hal 72-75).
* **Tangkap breakout high baru**, jangan mengantisipasi (hal 95-96): membayar lebih untuk
  saham yang sudah menembus high lama, karena di situ tidak ada hambatan.
* Jangan kejar saham yang menjauh; tunggu CPP berikutnya (hal 74).

**Keputusan: sudah tercakup, tidak ada yang baru.** CPP ≈ konsolidasi sebelum breakout yang
sudah ditangani kriteria `launchpad` (kontraksi base) dan `breakout` (tembus high 20 hari).
Klaim "lebih baik menunggu konfirmasi" juga sudah menjadi perilaku aplikasi (entry di tutup,
bukan mengantisipasi celah buka — lihat `MOMENTUM_ENTRY_RULE`).

---

## 5. Peter Lynch — *One Up on Wall Street*

**Aturan yang ditemukan**: enam kategori saham (slow grower, stalwart, fast grower, cyclical,
asset play, turnaround) hal 38; tenbagger; "beli yang Anda pahami" (hal 11); grafik per kategori
(hal 41).

**Keputusan: belum bisa dipakai.** Kategorisasi Lynch bertumpu pada **fundamental** (pertumbuhan
laba, PEG, nilai aset) yang tidak ada di data lokal, dan aplikasi ini tidak mengambil data
fundamental per emiten secara rutin. Memaksakannya akan menghasilkan label tanpa bukti — persis
yang dihindari di proyek ini. Dicatat sebagai arah berikutnya bila sumber fundamental tersedia.

---

## 6. Aaron Brown — *The Poker Face of Wall Street*

**Aturan yang ditemukan**: kerangka keputusan di bawah ketidakpastian (probabilitas, undian,
pasar sebagai tempat menanggung risiko) — tidak ada aturan entry/exit yang bisa diterjemahkan
langsung ke data harian.

**Keputusan: tidak ada yang diterapkan sebagai sinyal.** Satu gagasannya sudah terwakili:
ukuran taruhan/risiko kehancuran, yang di aplikasi ini menjadi kalkulator lot berbasis risiko
(bersama aturan Minervini/Tharp/Turtle).

---

## 7. Edianto Ong — *Technical Analysis for Mega Profit* (pindai, OCR)

**Yang relevan dengan aplikasi**

* **Penembusan sah = harga PENUTUPAN di luar garis**, bukan sentuhan intraday (hal 57).
* Pola chart: Head & Shoulders, Double/Triple Top-Bottom, Triangle, Pennant, Flag, Wedge,
  Rectangle, Cup & Handle (Bab 21); Gaps: common, break-away, run-away, exhaustion (Bab 20);
  Candlestick: marubozu, doji, hammer, dragonfly, engulfing (Bab 24); Fibonacci &
  percentage retracement (Bab 23); Dow Theory (Bab 18); volume (Bab 19).
* Skala arithmetic vs logarithmic (Bab 6); trend line & channel, fan principle (Bab 8-14).

**Keputusan: sebagian sudah ada, sebagian tidak diukur.** Definisi "penembusan sah" sudah
sesuai: `breakout_20_series()` membandingkan **Close** dengan high 20 bar sebelumnya, bukan
sentuhan intraday. Pola Launch Pad dan Volume S&R (versi buku Coachinvestasi) juga sudah
terpasang. Pola chart klasik lain (Cup & Handle, triangle, flag) **belum diukur** — pola visual
butuh deteksi bentuk yang tidak bisa dibaca dari ringkasan harian tanpa menggambar; itu pekerjaan
terpisah, bukan sesuatu yang boleh diklaim dari satu sesi pembacaan.

---

## 8. Belvin Tannadi — *Ilmu Saham Biawak VVIP* (pindai, OCR)

**Aturan yang ditemukan**

* **Tabel volume-harga (hal 257)**: harga naik + volume naik = "kenaikan cukup kuat";
  harga naik + volume **menurun** = "kenaikan lemah"; harga turun + volume naik = "penurunan
  cukup kuat"; sideways + volume naik = akumulasi/distribusi; sideways + volume turun = minat rendah.
* Indikator & alat lain: MA (hal 252-255), stochastic (hal 255-256), Fibonacci, rasio keuangan
  (hal 298-299) — semua sudah ada di aplikasi (SMA/EMA/RSI/MACD/ATR/volume MA) atau memang
  bukan fokus aplikasi ini.

**Yang diukur — dan HASILNYA TERBALIK dari bukunya:**

| entri | volume hari sinyal **di atas** MA20 | volume hari sinyal **di bawah** MA20 |
|---|---|---|
| momentum ≥8% | alpha5 +1,28% (net +2,26%) | **+6,73%** (net +8,49%) |
| momentum + breakout 20 hari | alpha5 +2,27% (net +3,56%) | **+12,24%** (net +14,93%, 61,5% untung) |

Positif di **tujuh dari tujuh tahun** (2020-2026) dan tetap sama setelah efek ukuran dikeluarkan
(residu-ukuran +12,30% vs kelas +12,24%). Ini juga menjelaskan mengapa detektor VCP penuh gagal
sementara komponen "volume mengering" justru kuat.

**Keputusan: DIPAKAI, dengan catatan bahwa ini kebalikan dari bukunya.** Penyaring
`dry_volume=true`, chip `🫗 vol kering` per baris, dan angka pengukurannya ditulis di catatan
kode + panel dashboard supaya tidak ada yang menyangka angka itu berasal dari buku. Hanya
~0,9 sinyal/hari se-pasar, jadi bukan satu-satunya sumber.

---

## 9. Yang ditolak setelah diukur (ringkas)

| aturan buku | halaman | hasil ukur | keputusan |
|---|---|---|---|
| Keluar Turtle: trail low 10 hari, tanpa target | Turtle hal 279 | rata-rata R −0,185 vs +0,151 rencana sekarang | ditolak |
| Detektor VCP penuh | Minervini hal 109-118 | 258 kejadian, alpha +0,54% ≈ breakout biasa; 0 saat digabung momentum | ditolak |
| "Harga naik + volume naik = kuat" | Biawak hal 257 | kebalikannya yang benar (+6,73% vs +1,28%) | dibalik, lalu dipakai sebagai `dry_volume` |
| Stage-2 sebagai kriteria mandiri | Minervini hal 105-106 | alpha hanya +0,79% (59 sinyal/hari) | dipakai sebagai **penyaring**, bukan kriteria |

## 10. Dua cacat produksi yang ketahuan SAAT menguji tambahan ini

Keduanya bukan soal buku, tetapi ketahuan justru karena aturan buku memaksa memakai jendela
data 1 tahun — dan keduanya membuat fitur baru tampak "bekerja" padahal tidak:

1. **Jendela data dipatok 200 bar.** Jalur IDX Edge selalu meminta 200 bar tanpa melihat
   `period`, sehingga `period=1y` pun hanya ~9,5 bulan. Akibatnya syarat yang butuh 252 bar
   (52 minggu & peringkat RS) **selalu** NaN → False, dan penyaring Stage-2 membuang SEMUA
   saham secara diam-diam. Diperbaiki: jumlah bar mengikuti `period`
   (`IDX_EDGE_BARS_BY_PERIOD`), kuota tetap satu permintaan. Terbukti live: `period=1y`
   sekarang 330 bar.
2. **Kunci cache riwayat tidak memuat `limit`** (`hist:{kode}`), sehingga permintaan 1 tahun
   bisa dilayani data 200 bar yang sudah tersimpan. Diperbaiki menjadi `hist:{kode}:{limit}`.

Satu cacat ketiga ketahuan dari uji unit, bukan dari produksi: pada `period=1mo/3mo` penyaring
Stage-2 juga membuang semuanya tanpa penjelasan. Sekarang bila bar < 252 dikembalikan alasan
apa adanya ("data hanya N bar") **dan** jendela data otomatis diperpanjang saat penyaring
dinyalakan.

## 11. Keterbatasan yang harus dibaca bersama hasil di atas

* **Survivorship bias**: panel hanya memuat emiten yang masih ada di cache; emiten delisting tidak
  ikut, jadi semua angka cenderung terlalu optimistis.
* **Harga buku vs harga eksekusi**: angka alpha diukur dari harga tutup hari sinyal. Untuk
  pemakaian harian, satu-satunya cara mendapatkannya adalah memindai sebelum bursa tutup
  (`/api/screener/preclose`); membeli di celah buka sesi berikutnya menghapus alpanya.
* **Batas jumlah baris**: penyaring Stage-2 dan volume kering memangkas hasil secara drastis
  (Stage-2: 9,5 → 3,2 sinyal/hari; volume kering: 9,5 → 0,9). Itu konsekuensi yang sengaja
  ditampilkan, bukan masalah yang disembunyikan.
* **Nomor halaman** merujuk berkas PDF di `buku tambahan/`, bukan nomor halaman cetakan.
* **Peringkat RS** di `/api/analyze/{ticker}` selalu "belum dinilai" karena ia butuh pembanding
  lintas saham; hanya pemindaian (yang melihat banyak emiten sekaligus) yang bisa menghitungnya.
  Catatan ini ikut dikirim di setiap balasan supaya tidak dibaca sebagai "8 syarat lolos".
