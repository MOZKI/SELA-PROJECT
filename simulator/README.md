# Simulator SELA

Meniru dua ESP32 (Node A 10 W, Node B 20 W). Semua paket berlabel `source: synthetic`.
Hanya memakai pustaka standar Python 3.9+.

## Jalankan

Backend harus hidup dulu (lokal atau server). Dari folder `simulator/`:

```bash
export SELA_API_KEY=isi_dari_backend/.env
export SELA_URL=http://localhost:8000      # atau http://IP_SERVER:8000

python3 sim.py drain                        # 30 W dari 5 Wh sampai lockout (cek sekitar 10 menit simulasi)
python3 sim.py demo                         # alur demo penuh, 8 pengecekan lulus atau gagal
python3 sim.py dataset --days 14            # CSV untuk ML, tanpa backend
```

Exit code 0 bila semua pengecekan lulus, 1 bila ada yang gagal.

## Percepatan

`--factor 10` (default drain dan demo) berarti jeda nyata antar paket 0,5 detik, tetapi `ts` di paket
tetap maju 5 detik. Ledger tetap benar karena celah data tidak pernah melewati 15 detik.
Demo penuh sekitar 14 detik nyata di factor 60 dan sekitar 80 detik di factor 10. Pakai `--factor 1`
untuk waktu nyata. Ke server AWS, jaga `5 / factor` lebih besar dari dua kali RTT (factor 10 aman).

## Yang dipakai ulang oleh skenario

- `drain` dan `demo` memanggil `POST /admin/reset` di awal, jadi saldo kembali ke `--balance` (default 5).
- Backend memberi `lockout` di balasan telemetri. Node tiruan langsung OFF dan menolak ON selama lockout,
  seperti yang nanti dilakukan firmware. Jalur Sinric belum dicakup (itu tugas Jumat).
- Top-up melepas lockout, lampu tetap OFF sampai ada ON baru.

## CSV untuk ML

Default ke `ml/data/synthetic_<skenario>_<waktu>.csv` (`--csv` untuk path lain, `--no-csv` untuk mematikan).
Format panjang: satu baris per paket, dua baris per tick (Node A dan Node B).

`sim_day` dan `sim_hour` dihitung dari hari dipercepat (`--day-minutes`, default 30 menit simulasi = 1 hari),
bukan dari jam dinding. `day_type` weekend bila `sim_day % 7` adalah 5 atau 6. `balance_wh` dan `lockout`
kosong pada skenario dataset karena tidak ada backend. Pakai `--seed` untuk hasil yang bisa diulang.

Skenario `dataset` memilih kondisi beban (A OFF/ON, B OFF/ON) per jam dipercepat dengan peluang yang
berbeda untuk hari kerja dan akhir pekan, sehingga keempat kondisi 0, 10, 20, 30 W muncul.
Ini data sintetis, jangan dicampur dengan data `real` saat evaluasi ML tanpa menyaring kolom `source`.

## Tes

```bash
python3 -m pytest -q tests
```
