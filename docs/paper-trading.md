# Paper trading sinyal v2

Ada tiga jalur paper trading. Ketiganya memakai aturan yang sama (`signals.run_strategy`)
dan menulis jurnal append-only: satu baris JSON per kejadian, tidak pernah ditulis ulang.

| Jalur | Pasar | Eksekusi | Jurnal |
|---|---|---|---|
| App (dashboard) | 5 kripto 4h/1D, Gold 1D | Harga close candle sinyal, tanpa fill | `<data app>/journal.jsonl` |
| `tools/binance_paper.py` | BTC, ETH, BNB, SOL, XRP 4h/1D | Simulasi fill di bid/ask live Binance + fee 0,1%/sisi | `~/.market-radar/binance_paper.jsonl` |
| `tools/mt5_paper.py` | XAUUSD 4h/1D | Order asli di akun demo MT5 (lihat [mt5.md](mt5.md)) | `~/.market-radar/mt5_paper.jsonl` |

## `tools/binance_paper.py`

Tidak mengirim order ke mana pun.

- **Entry:** hanya kalau skrip sendiri melihat candle breakout close (candle itu berubah jadi
  candle close terakhir di antara dua putaran yang jaraknya paling lama 2 menit). Fill
  disimulasikan di ask (BUY) atau bid (SELL) live saat itu. Sinyal yang ketahuan setelah
  skrip baru jalan, koneksi putus, atau laptop tidur dicatat `missed`.
- **Error jaringan** yang sama hanya dicatat sekali.
- **Stop:** 2 × ATR, terkunci, dicek ke bid/ask live tiap 15 detik. Kalau skrip sempat mati
  dan ada candle yang menembus stop, trade ditutup di harga stop (`from_candle`).
- **Keluar trailing:** saat aturan keluar di candle close, fill di bid/ask live setelah close.
- **Hasil:** R dihitung dari fill entry yang sebenarnya, dikurangi fee kedua sisi.
- **SELL:** disimulasikan seolah bisa short (futures/margin). Di spot hanya BUY yang bisa
  dijalankan, jadi `--status` juga menampilkan total BUY saja.

```bash
python tools/binance_paper.py
```

```bash
python tools/binance_paper.py --status
```

Mulai 15 Nov 2026 (BTC 22:25, empat koin lain 22:42). Posisi yang sudah dipegang aturan
sebelum itu tidak diikuti, jadi trade pertama menunggu breakout baru.

## Kapan harus menyala

Kedua skrip hanya mengikuti sinyal kalau sedang jalan saat candle close. Candle 4 jam close
setiap 4 jam (07.00, 11.00, 15.00, 19.00, 23.00, 03.00 WIB untuk Binance), jadi laptop yang
tidur atau mati membuat sinyal tercatat `missed`. Untuk hasil yang bersih, jalankan di
komputer yang memang menyala 24 jam. Jumlah `missed` di `--status` menunjukkan seberapa
banyak yang terlewat.
