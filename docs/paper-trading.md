# Paper trading sinyal v2

Ada tiga jalur paper trading. Ketiganya memakai aturan yang sama (`signals.run_strategy`)
dan menulis jurnal append-only: satu baris JSON per kejadian, tidak pernah ditulis ulang.

| Jalur | Pasar | Eksekusi | Jurnal |
|---|---|---|---|
| App (dashboard) | 5 kripto 4h/1D, Gold 1D | Harga close candle sinyal, tanpa fill | `<data app>/journal.jsonl` |
| `tools/binance_paper.py` | BTCUSDT 4h/1D | Simulasi fill di bid/ask live Binance + fee 0,1%/sisi | `~/.market-radar/binance_paper.jsonl` |
| `tools/mt5_paper.py` | XAUUSD 4h/1D | Order asli di akun demo MT5 (lihat [mt5.md](mt5.md)) | `~/.market-radar/mt5_paper.jsonl` |

## `tools/binance_paper.py`

Tidak mengirim order ke mana pun.

- **Entry:** hanya kalau candle breakout adalah candle yang baru saja close, dan close setelah
  waktu mulai. Fill disimulasikan di ask (BUY) atau bid (SELL) live sesaat setelah close.
  Sinyal yang ketahuan terlambat dicatat `missed`.
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

Mulai 15 Nov 2026 22:25. Saat itu aturan sedang memegang SELL di 4h dan 1D (dibuka 9 Nov);
posisi itu tidak diikuti, jadi trade pertama menunggu breakout baru.
