# Hasil uji historis

Hasil dalam R (kelipatan risiko awal). "Bersih" berarti sudah dipotong biaya transaksi.
Rentang ± dan "95%" adalah interval keyakinan 95%.

## Sinyal v2: breakout 4h/1D (`tools/backtest_trend.py`), 15 Nov 2026

Aturan, data, dan syarat lulus ditulis di [signal-v2.md](signal-v2.md) dan di-commit
(54b42f0) sebelum uji dijalankan. Tidak ada parameter yang diubah setelah hasil terlihat.
Satu perbaikan setelah uji pertama: data Gold diambil ulang karena Yahoo `range=max`
ternyata mengembalikan candle bulanan, bukan harian. Itu perbaikan data, bukan aturan.

### Holdout utama: BTC, ETH, BNB, SOL, XRP di 4h, trade dibuka 2023 – Nov 2026

| | Hasil |
|---|---|
| Trade | 932 |
| Menang | 30% (27–33%) |
| Expectancy kotor | +0,32R |
| Biaya rata-rata | 0,09R |
| **Expectancy bersih** | **+0,24R** (bootstrap 95%: +0,05 s/d +0,44) |
| Max drawdown | 49R |
| Rentetan kalah terpanjang | 21 |
| Entry acak (1.000 simulasi) | rata-rata +0,00R; sinyal mengalahkan 99,6% |

Syarat: ≥ 150 trade LULUS · expectancy bersih ≥ +0,15R LULUS · batas bawah bootstrap > 0
LULUS · mengalahkan ≥ 95% acak LULUS.

### Kenapa ini belum cukup untuk uang sungguhan

- **Koin saling berkorelasi.** Bootstrap di atas menganggap tiap trade independen, padahal
  breakout BTC, ETH, dan lainnya sering terjadi bersamaan. Kalau di-bootstrap per bulan
  (cek tambahan, bukan syarat yang didaftarkan), rentang 95%-nya −0,06 s/d +0,59R:
  batas bawahnya di bawah nol.
- **Melemah dari tahun ke tahun.** Per tahun: 2023 +0,45R, 2024 +0,31R, 2025 +0,09R,
  2026 +0,13R. Dua tahun terakhir di bawah ambang +0,15R.
- **Per koin tidak ada yang signifikan sendiri.** BTC +0,41R, ETH +0,39R, BNB +0,06R,
  SOL +0,20R, XRP +0,16R; semua rentang 95%-nya mencakup nol.
- **Posisi SELL rugi.** 444 trade short: −0,09R per trade. Long saja: +0,54R per trade
  (497 trade, rentang +0,21 s/d +0,90). Di spot memang hanya long yang bisa dijalankan.
  Ini cek tambahan; aturan tidak diubah jadi long saja karena keputusan itu diambil setelah
  melihat hasil. Kalau mau, uji sebagai v3 di data yang belum dipakai.
- **Win rate rendah.** 70% trade kalah, rentetan kalah bisa 20 kali lebih. Hasil datang dari
  sedikit trade yang untung besar.
- Data BTC 2024–2026 sudah pernah dilihat waktu uji v1 (dengan aturan berbeda).

### Cek tambahan

| Uji | Trade | Bersih per trade (95%) | Mengalahkan acak |
|---|---|---|---|
| BTC 4h, sebelum 2023 (pengembangan) | 226 | +0,59R (+0,19 s/d +1,05) | 98,5% |
| 5 kripto 1D, 2023+ | 160 | +0,43R (−0,12 s/d +1,15) | 90,0% |
| 5 kripto 1D, 2023+, long saja | 87 | +1,02R (+0,05 s/d +2,34) | – |
| Gold 1D, 2006–2015 | 56 | +0,32R (−0,15 s/d +0,86) | 88,6% |
| Gold 1D, 2016+ | 69 | +0,20R (−0,25 s/d +0,73) | 84,7% |

Arahnya konsisten positif, tapi di 1D dan Gold jumlah trade terlalu sedikit untuk yakin.

### Langkah berikutnya

Paper trading. Tanggal mulai ditulis sekali di baris pertama `journal.jsonl` dan tidak
pernah berubah. App mengikuti kelima koin holdout (BTC, ETH, BNB, SOL, XRP) di 4h dan 1D
serta Gold di 1D di latar belakang, apa pun yang sedang tampil di kartu, dan mencatat semua
trade yang dibuka sejak tanggal mulai. Trade yang terjadi saat app tertutup dicatat saat
app dibuka lagi dengan tanda `backfilled: true` (aturannya sama, tapi tidak mungkin
dieksekusi langsung). Jurnal append-only, satu baris per kejadian, ada waktu pencatatan.

Paper trading 6–8 minggu hanya menghasilkan ±30–40 trade, terlalu sedikit untuk
membuktikan atau membantah +0,24R (hasil holdout bergantung pada ±10 trade besar dari 932).
Gunanya menguji eksekusi: apakah entry bisa diikuti tepat waktu dan rentetan kalah bisa
ditahan. Bukti keunggulan harus datang dari data baru (v3 di koin lain atau waktu ke depan).

Mengulang: `python tools/backtest_trend.py` (unduh ±20.000 candle per koin, ±10 menit).

---

# Arsip: sinyal v1 (skor gabungan), sudah diganti

## Uji 2 periode dengan biaya (alat lama, sudah dihapus), 15 Nov 2026

Aturan sama persis dengan app (hanya candle close, masuk |skor| ≥ 35 dan ADX ≥ 20,
keluar |skor| < 20, stop di balik swing terakhir + 0,25 ATR, sinyal ditolak bila R:R TP1
setelah biaya < 1,5, setengah posisi di TP1 lalu stop ke entry). Parameter dibekukan:
tidak ada yang disetel di data ini. Satu-satunya perubahan logika sebelum uji adalah
perbaikan bug tanda faktor volume (lihat bawah).

Data dibagi dua: periode A (tahun lama) dan periode B (tahun baru, out-of-sample).
Baseline acak: 1.000 simulasi dengan jumlah trade yang sama, candle dan arah acak, pembuat
rencana, biaya, dan manajemen stop/target yang sama, lama posisi diambil dari sebaran
lama posisi sinyal.

Biaya: BTC 0,1% per sisi (Binance spot Regular User) + slippage 0,03% per sisi = 0,26%
pulang-pergi. Gold: asumsi 0,03% pulang-pergi.

### BTCUSDT 15m, Binance, 70.079 candle (Nov 2024 – Nov 2026)

| | Periode A | Periode B (out-of-sample) |
|---|---|---|
| Trade | 318 | 331 |
| Menang | 21% (17–26%) | 22% (18–27%) |
| Expectancy kotor | −0,06R | −0,01R |
| Biaya rata-rata | 0,42R | 0,49R |
| **Expectancy bersih** | **−0,48R** (−0,60 s/d −0,37) | **−0,50R** (−0,63 s/d −0,37) |
| Max drawdown | 154R | 175R |
| Rentetan kalah terpanjang | 26 | 19 |
| Rata-rata acak | −0,84R | −0,73R |
| Mengalahkan acak | 100% simulasi | 99,6% simulasi |

Syarat lulus periode B: ≥ 150 trade LULUS · expectancy bersih ≥ +0,15R GAGAL ·
batas bawah bootstrap > 0 GAGAL · mengalahkan ≥ 95% acak LULUS.

**Kesimpulan BTC 15m: tidak layak.** Sebelum biaya sinyal ini impas, dan memang lebih baik
dari entry acak (acak lebih buruk lagi). Tapi stop di 15m terlalu sempit dibanding biaya:
biaya pulang-pergi memakan rata-rata ±0,45R per trade, sehingga hasil bersihnya sekitar
−0,5R per trade. Mengalahkan acak tidak berarti untung; keduanya rugi.

### GC=F (gold futures) 1h, Yahoo, 13.741 candle (Jun 2024 – Nov 2026)

| | Periode A | Periode B (out-of-sample) |
|---|---|---|
| Trade | 101 | 86 |
| Menang | 39% (29–49%) | 29% (20–40%) |
| Expectancy bersih | +0,11R (−0,10 s/d +0,33) | −0,02R (−0,22 s/d +0,19) |
| Max drawdown | 8,1R | 9,2R |
| Rentetan kalah terpanjang | 6 | 12 |
| Mengalahkan acak | 99,4% simulasi | 72,2% simulasi |

Syarat lulus periode B: semua GAGAL (trade < 150, expectancy, bootstrap, baseline acak).

**Kesimpulan Gold 1h: tidak ada keunggulan yang terbukti.** Periode A sedikit positif
tapi tidak signifikan, dan tidak bertahan di periode B.

### Yang dipelajari (belum diterapkan, supaya tidak overfitting)

- Sebagian besar trade (±75–80%) ditutup oleh aturan keluar skor (|skor| < 20), bukan oleh
  stop/target. Rencana stop/target jarang sempat bekerja.
- Di BTC, biaya dalam R hanya turun kalau stop lebih lebar (timeframe lebih besar atau
  stop lebih jauh). Timeframe kecil dengan biaya taker hampir pasti rugi.
- Mengubah aturan berdasarkan hasil ini lalu menguji ulang di data yang sama bukan bukti.
  Ide perbaikan harus diuji di data baru (paper trading ke depan) atau di instrumen lain.

## Perbaikan bug: tanda faktor volume

Volume tinggi di candle hijau dulu diberi −10 saat skor condong turun (dan +10 untuk
candle merah), artinya tekanan beli dibaca sebagai bearish. Sekarang poin volume selalu
mengikuti warna candle: hijau +10, merah −10, dengan keterangan searah/melawan arah skor.

## Uji lama (sebelum biaya, 5.000 candle)

Hasilnya (tanpa biaya, sebelum perbaikan volume) juga tidak menunjukkan keunggulan.
Alat uji v1 (`backtest_signal.py`, `backtest_walkforward.py`) sudah dihapus; versi
terakhirnya ada di commit 851d31b. `tools/backtest_reversal.py` masih ada untuk detektor
balik arah.
