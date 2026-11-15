# Sinyal v3: v2 long-only (spesifikasi pra-uji)

Didaftarkan 15 Nov 2026, sebelum uji ke depan dimulai. Uji v3 adalah paper trading ke depan;
tidak ada data historis yang dipakai untuk menilainya. Aturan, cakupan, syarat lulus, dan
aturan berhenti di bawah tidak boleh diubah selama uji berjalan.

## Dasar

- Perbandingan 16 teknik di data 2017–2026: ke-13 teknik ikut-tren positif di kedua
  periode, ketiga teknik balik-arah rugi. v2 ada di tengah, bukan juara.
- SELL rugi atau nol di semua teknik pada 2023–2026, bukan cuma di v2. Short kripto memang
  tidak menghasilkan di periode ini.
- Karena itu v3 hanya membuang posisi SELL. Teknik "juara tabel" (EMA cross) sengaja tidak
  dipilih: memilih pemenang dari 16 percobaan di data yang sama adalah overfitting, dan
  selisihnya dengan v2 masih dalam rentang kebetulan.

## Aturan

Sama persis dengan v2 ([signal-v2.md](signal-v2.md)) kecuali tidak ada posisi SELL:
`run_strategy(candles, allow_short=False)`. Breakout 20 candle di atas EMA200, stop 2 × ATR
terkunci, keluar trailing 10 candle, tanpa target. Tidak ada parameter lain yang berubah.

## Cakupan uji

- Pasar: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT (Binance spot), 4h dan 1D.
- Eksekusi: `tools/binance_paper.py`, fill simulasi di bid/ask live + fee 0,1% per sisi.
- Trade yang dihitung: dibuka setelah waktu mulai v3 yang tertulis di jurnal
  (`~/.market-radar/binance_paper.jsonl`, baris `start` dengan `version: 3`).
- Gold tidak termasuk v3. Di Gold, keunggulan long sebagian besar hanya ikut tren naik
  (lihat [mt5.md](mt5.md)). Runner MT5 tetap menjalankan v2 sebagai uji eksekusi.

## Penilaian

Dinilai saat **150 trade 4h selesai** (gabungan 5 koin) atau pada **15 Mar 2028**, mana yang
lebih dulu. Backtest long-only 2023–2026 menghasilkan ±127 trade 4h per tahun untuk kelima
koin (±2–3 per minggu), jadi 150 trade butuh kira-kira 14 bulan.

Lulus kalau, untuk trade 4h:
1. ≥ 150 trade (kalau dinilai karena tanggal dan kurang dari 150: hasilnya "belum cukup
   data", bukan lulus).
2. Expectancy bersih ≥ +0,15R.
3. Batas bawah bootstrap 95% (per bulan, karena koin saling berkorelasi) > 0.
4. Mengalahkan BUY di candle acak (stop dan keluar trailing sama, periode sama, jumlah trade
   sama) di ≥ 95% dari 1.000 simulasi. Ini memisahkan keunggulan sinyal dari sekadar harga
   yang naik.

Juga dilaporkan: win rate dengan rentang 95%, max drawdown, rentetan kalah terpanjang, jumlah
`missed`, hasil 1D (tanpa syarat lulus), dan sumbangan 10 trade terbesar.

## Aturan berhenti (ditulis sebelum mulai)

- **Drawdown:** kalau kurva gabungan semua trade v3 (4h dan 1D, 5 koin) turun lebih dari
  **50R** dari puncaknya, uji dihentikan: tidak ada entry baru, posisi terbuka dibiarkan
  selesai sesuai aturan. 50R ≈ 1,2 × max drawdown backtest long-only 4h (42,6R). Runner
  menegakkan ini sendiri dan mencatat `stop_rule` di jurnal.
- **Tidak ada perubahan aturan di tengah jalan.** Ide perbaikan dicatat untuk v4 dan diuji
  setelah v3 dinilai, di data yang belum dipakai.
- **Missed tinggi:** kalau lebih dari 20% sinyal tercatat `missed`, hasilnya dilaporkan
  dengan peringatan itu; aturan tidak diubah, cara menjalankannya yang diperbaiki
  (misalnya pindah ke komputer yang menyala 24 jam).

Kalau lulus: baru pertimbangkan uang kecil (risiko 0,25–0,5% modal per trade), dengan aturan
berhenti yang sama. Kalau gagal: v3 ditulis apa adanya, dan sinyal tetap berlabel eksperimental.
