# Hasil uji historis

Hasil dalam R (kelipatan risiko awal). "Bersih" berarti sudah dipotong biaya transaksi.
Rentang ± dan "95%" adalah interval keyakinan 95%.

## Uji 2 periode dengan biaya (`tools/backtest_walkforward.py`), 15 Nov 2026

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

`tools/backtest_signal.py` dan `tools/backtest_reversal.py` masih ada untuk perbandingan.
Hasilnya (tanpa biaya, sebelum perbaikan volume) juga tidak menunjukkan keunggulan.

## Mengulang

```bash
python tools/backtest_walkforward.py BTCUSDT 15m 730
python tools/backtest_walkforward.py GC=F 1h
```

Data Binance disimpan di `tools/.history/` (tidak masuk git) dan hasil JSON-nya juga.

App mencatat tiap sinyal yang selesai ke `journal.json` di folder data app (entry, stop,
target, hasil kotor dan bersih dalam R). Log ini bisa dipakai untuk paper trading, tapi
belum append-only yang terkunci: file bisa diedit manual.
