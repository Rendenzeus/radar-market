# MetaTrader 5: uji Gold dan paper trading di akun demo

Butuh Windows, terminal MT5 yang sedang login, dan paket Python resmi MetaQuotes:

```bash
pip install MetaTrader5
```

Paket ini sengaja tidak dimasukkan ke `requirements.txt` karena hanya untuk alat di
laptop, bukan untuk app/APK.

## Uji v2 di data XAUUSD broker (`tools/mt5_gold.py`), 15 Nov 2026

Server MetaQuotes-Demo menyimpan XAUUSD (gold spot) sejak Juni 2004: 34.361 candle 4 jam
dan 5.762 candle harian. Data ini belum pernah dipakai untuk merancang atau menguji
sinyal, jadi ini uji out-of-sample yang benar-benar baru. Aturan v2 dipakai apa adanya.
Uji ini tidak ada di spesifikasi pra-uji, tapi empat syarat lulusnya sama.

Biaya: 0,03% pulang-pergi (lebih besar dari spread median broker 2020–2026, ±0,002%)
ditambah swap menginap dari pengaturan simbol (long −12,6 poin, short −4,6 poin per malam,
tiga kali lipat di hari rollover).

| XAUUSD | Trade | Menang | Bersih per trade setelah swap (95%) | Mengalahkan acak |
|---|---|---|---|---|
| **4h, 2004–2026** | 818 | 33% | **+0,10R** (−0,03 s/d +0,23) | 93,8% |
| 4h, 2004–2015 | 417 | 34% | +0,09R (−0,07 s/d +0,26) | – |
| 4h, 2016–2022 | 262 | 30% | +0,04R (−0,18 s/d +0,29) | – |
| 4h, 2023–2026 | 139 | 36% | +0,22R (−0,11 s/d +0,59) | – |
| 4h, long saja | 458 | 35% | +0,23R (+0,05 s/d +0,43) | – |
| 1D, 2004–2026 | 128 | 38% | +0,20R (−0,09 s/d +0,53) | 98,9% |
| 1D, long saja | 83 | 40% | +0,40R (+0,00 s/d +0,82) | – |

Syarat lulus (4h, seluruh periode): ≥ 150 trade LULUS · expectancy ≥ +0,15R GAGAL ·
batas bawah bootstrap > 0 GAGAL · mengalahkan ≥ 95% acak GAGAL (93,8%).

**Kesimpulan:** di Gold, v2 positif tapi tipis (+0,10R per trade setelah swap) dan tidak
bisa dibedakan dari kebetulan. Tahun-tahunnya naik-turun: 2018 −0,39R, 2021 −0,24R,
2025 +0,48R. Swap memakan ±0,04R per trade di 4h dan ±0,11R di 1D.

### Catatan soal "long saja"

Di Gold dan kripto, posisi BUY selalu lebih bagus dari SELL. Sebagian besar itu karena
harganya memang naik selama periode uji. Pembanding yang adil adalah BUY di candle acak:

| | Sinyal long saja (sebelum swap) | BUY di candle acak | Mengalahkan |
|---|---|---|---|
| XAUUSD 4h, 2004–2026 | +0,29R | +0,18R | 90,4% |
| 5 kripto 4h, 2023+ | +0,54R | +0,13R | 99,8% |

Di Gold, sebagian besar keunggulan long hanyalah ikut tren naik jangka panjang. Di kripto
ada selisih nyata di atas BUY acak. Aturan v2 tidak diubah berdasarkan ini; kalau mau,
"long saja" diuji sebagai v3 di data yang belum dipakai.

## Paper trading di akun demo (`tools/mt5_paper.py`)

Skrip ini menjalankan aturan v2 di XAUUSD 4h dan 1D memakai candle broker, lalu mengirim
order ke akun demo.

- **Hanya demo, dicek terus.** Jenis akun dicek di setiap putaran dan sekali lagi tepat
  sebelum setiap order. Kalau login MT5 diganti ke akun real, skrip berhenti tanpa mengirim apa pun.
- **Hanya sinyal yang dilihat langsung.** Entry dikirim hanya kalau skrip sendiri melihat
  candle breakout close: candle itu harus berubah jadi candle close terakhir di antara dua
  putaran yang jaraknya paling lama 2 menit. Skrip yang baru dinyalakan setelah close,
  terminal yang terputus, atau laptop yang tidur membuat sinyal dicatat `missed`.
- **Satu percobaan per entry.** Order yang ditolak dicatat sekali sebagai `error` lalu `missed`,
  tidak dicoba ulang.
- **Posisi dikenali dari nomor tiket di jurnal**, bukan dari komentar order. Posisi lain di
  akun (manual atau dari skrip lain) tidak pernah disentuh.
- **Sambung ulang otomatis.** Kalau terminal MT5 tertutup atau koneksi putus, skrip mencatat
  `disconnected` sekali, terus mencoba menyambung, lalu mencatat `reconnected`.
- **Error yang sama hanya dicatat sekali**, misalnya penutupan posisi yang ditolak saat pasar
  tutup (tetap dicoba lagi tiap putaran).
- **Hanya sinyal setelah mulai.** Posisi yang menurut aturan sudah berjalan sebelum skrip
  pertama kali dijalankan tidak diikuti.
- **Stop 2 × ATR dipasang di order**, jadi broker yang menjalankannya. Keluar trailing
  berbasis candle close, jadi skrip menutup posisi di harga pasar setelah candle itu close.
- **Ukuran posisi** dibuat supaya kena stop = rugi 0,5% saldo.
- **Hasil memakai uang sungguhan di akun demo**: fill, swap, dan komisi dari riwayat akun.
  R = profit posisi ÷ uang yang dipertaruhkan.
- **Jurnal** di `~/.market-radar/mt5_paper.jsonl`: append-only, satu baris per kejadian
  (`start`, `open`, `exit_sent`, `close`, `missed`, `error`, `disconnected`, `reconnected`, `stopped`), tidak pernah ditulis ulang.

Menjalankan (MT5 harus terbuka, login ke akun demo, dan tombol **Algo Trading** menyala):

```bash
python tools/mt5_paper.py
```

```bash
python tools/mt5_paper.py --status
```

Perkiraan jumlah trade dari backtest: ±37 per tahun di 4h dan ±6 per tahun di 1D, jadi
6–8 minggu hanya menghasilkan ±5 trade. Itu cukup untuk menguji eksekusi (fill, slippage,
swap, disiplin), tapi tidak cukup untuk membuktikan sinyal.

Jam candle di MT5 mengikuti jam server broker (MetaQuotes-Demo: UTC+2, UTC+3 saat musim
panas Eropa). Skrip mempelajari selisih jam itu dari harga live, dan tidak membuka posisi
selama selisihnya belum diketahui (misalnya baru dinyalakan saat akhir pekan). Sinyal dari
candle Jumat terakhir hanya bisa dieksekusi sebelum pasar tutup; setelah itu tercatat `missed`.

## Ekspor data (`tools/mt5_export.py`)

Menyimpan candle dari terminal ke CSV supaya bisa diuji tanpa MT5:

```bash
python tools/mt5_export.py XAUUSD H1,H4,D1
```

Hasilnya di `tools/.history/XAUUSD_H4.csv` dan seterusnya (tidak masuk git). Kolom: `time`
(epoch detik, jam server broker), `open`, `high`, `low`, `close`, `volume` (tick), `spread`
(poin). Candle terakhir sesi yang belum genap periodenya (misalnya H4 Jumat yang terpotong
saat pasar tutup) baru ikut setelah sesi berikutnya dibuka.

Kripto tidak bisa diuji di sini: server MetaQuotes-Demo tidak punya CFD BTCUSD, ETHUSD, dan
sejenisnya (simbol "BTC", "ETH" di sana adalah saham/ETF).
