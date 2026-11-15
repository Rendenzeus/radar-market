# Sinyal v2: breakout tren di timeframe besar (spesifikasi pra-uji)

Dokumen ini ditulis dan di-commit **sebelum** uji holdout dijalankan. Aturan, parameter,
data, dan syarat lulus di bawah tidak boleh diubah setelah hasil terlihat. Kalau ada yang
diubah, itu harus dicatat sebagai versi baru dan diuji di data yang belum dipakai.

## Kenapa diganti, bukan disetel

Uji v1 (skor gabungan, 15m/1h) rugi setelah biaya. Dua penyebab utama:
1. Di timeframe kecil stop terlalu sempit dibanding biaya (±0,45R per trade di BTC 15m).
2. ±75–80% trade ditutup oleh aturan keluar skor, sehingga trade untung dipotong cepat.

v2 memakai pola yang sudah lama dikenal (breakout saluran Donchian ala "Turtle") dengan
parameter dari konvensi literatur, bukan hasil optimasi di data kita.

## Aturan

Hanya candle close. Satu posisi per pasar/timeframe, tanpa tambah posisi.

| Bagian | Aturan |
|---|---|
| Timeframe sinyal | BTC dan kripto: 4h dan 1D. Gold: 1D saja (data intraday Yahoo terlalu pendek). Timeframe lebih kecil hanya menampilkan chart dan konteks, tanpa sinyal. |
| Filter tren | Long hanya bila close > EMA200; short hanya bila close < EMA200 (timeframe yang sama). |
| Entry long | Close > high tertinggi 20 candle sebelumnya. Masuk di harga close candle itu. |
| Entry short | Close < low terendah 20 candle sebelumnya. |
| Stop awal | 2 × ATR(14) dari entry. Terkunci, tidak pernah dipindah. |
| Stop kena | Dicek di candle berikutnya dengan high/low. Kalau candle dibuka melewati stop (gap), isi di harga open. |
| Keluar trailing | Long: close < low terendah 10 candle sebelumnya, keluar di close. Short: close > high tertinggi 10 candle sebelumnya. Level ini hanya dihitung ulang tiap candle close. |
| Target | Tidak ada. Untung dibiarkan berjalan sampai keluar trailing. |
| Entry ulang | Hanya di candle setelah posisi ditutup, dengan breakout baru. |
| Hasil | Dalam R (1R = 2 × ATR saat entry), dikurangi biaya pulang-pergi dalam R. |
| Biaya | Kripto: 0,1% per sisi + slippage 0,03% per sisi = 0,26%. Gold: 0,03% (asumsi). |

Parameter: 20, 10, 200, 2 × ATR, ATR 14. Tidak ada yang lain.

## Data uji

Binance spot 4h dan 1D sejak data tersedia; Yahoo GC=F 1D.

- **Periode pengembangan** (boleh dilihat untuk mencari bug, bukan untuk menyetel):
  BTCUSDT sebelum 1 Jan 2023.
- **Holdout utama** (penilaian lulus/gagal): gabungan BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT,
  XRPUSDT di 4h, trade yang dibuka 1 Jan 2023 – 15 Nov 2026.
- **Cek tambahan** (dilaporkan, bukan syarat): sama di 1D; Gold 1D sebelum dan sesudah
  1 Jan 2016; masing-masing simbol terpisah; hanya posisi long (yang bisa dijalankan di spot).

Catatan kejujuran: data BTC 2024–2026 sudah pernah dilihat di uji v1, dengan aturan yang
berbeda. ETH, BNB, SOL, XRP belum pernah dipakai di proyek ini.

## Syarat lulus (holdout utama)

1. ≥ 150 trade.
2. Expectancy bersih ≥ +0,15R.
3. Batas bawah bootstrap 95% expectancy bersih > 0.
4. Mengalahkan entry acak di ≥ 95% dari 1.000 simulasi. Entry acak: candle dan arah acak,
   jumlah trade sama, stop 2 × ATR dan keluar trailing 10 candle yang sama, biaya sama.

Juga dilaporkan: win rate dengan rentang 95%, max drawdown, rentetan kalah terpanjang.

Kalau gagal: sinyal tetap berlabel EKSPERIMENTAL dan hasilnya ditulis apa adanya.
Kalau lulus: langkah berikutnya tetap paper trading 6–8 minggu, bukan uang sungguhan.
