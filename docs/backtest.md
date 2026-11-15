# Hasil uji historis

Data: Binance BTCUSDT, 5.000 candle per timeframe (diambil 15 Nov 2026).
Hasil dalam R (kelipatan risiko awal). Rentang ± adalah interval 95%.

## Sinyal utama BUY/SELL (`tools/backtest_signal.py`)

Aturan sama persis dengan app: hanya candle close, masuk saat |skor| ≥ 35 dan ADX ≥ 20,
keluar saat |skor| < 20, stop di balik swing terakhir + 0,25 ATR, sinyal ditolak bila
R:R TP1 < 1,5, setengah posisi ditutup di TP1 lalu stop pindah ke entry.
Baseline: entry acak (arah acak) dengan pembuat rencana dan manajemen stop/target yang sama.

| Timeframe | Trade | Menang | Expectancy | Baseline acak |
|---|---|---|---|---|
| 15m | 61 | 39% | +0,12R (±0,25) | +0,09R (±0,17) |
| 1h  | 59 | 29% | −0,16R (±0,17) | −0,01R (±0,16) |
| 4h  | 59 | 31% | +0,04R (±0,23) | −0,02R (±0,15) |

Variasi "keluar hanya di stop/target atau sinyal berlawanan" juga tidak lebih baik
(+0,10R / −0,07R / +0,02R dengan rentang ±0,5R).

**Kesimpulan:** belum ada keunggulan yang bisa dibedakan dari kebetulan. Sinyal diberi
label EKSPERIMENTAL di app. Parameter sengaja tidak diutak-atik untuk mengejar angka
bagus di data yang sama (overfitting).

## Lapisan candle detektor balik arah (`tools/backtest_reversal.py`)

Win-rate sinyal 24–67% dengan sampel kecil (9–72 kejadian) dibanding baseline sekitar 40%,
yang juga titik impas untuk target 1,5 ATR / stop 1 ATR. Tidak ada keunggulan yang konsisten.
Order flow dan sentimen berita tidak punya data historis di sini, jadi belum teruji.

## Mengulang

```bash
python tools/backtest_signal.py BTCUSDT 15m,1h,4h 5
python tools/backtest_reversal.py BTCUSDT 5m,15m,1h
```

App juga mencatat tiap sinyal yang selesai ke `journal.json` di folder data app
(entry, stop, target, hasil dalam R) sehingga kinerja live bisa dinilai belakangan.
