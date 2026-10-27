# Kontrak data SELA

Semua pihak (firmware, simulator, dashboard) mengacu ke file ini. Ubah di sini dulu, baru ubah kode.

## Telemetri: ESP32 ke backend

`POST /telemetry` dengan header `X-API-Key: <kunci>` dan `Content-Type: application/json`

```json
{
  "device_id": "sela-node-a",
  "boot_id": "b3f1a9",
  "seq": 1042,
  "ts": 1793000000.0,
  "source": "real",
  "relay_on": true,
  "sensor_ok": true,
  "voltage": 220.1,
  "current": 0.045,
  "power": 10.2,
  "pf": 0.98,
  "freq": 50.0,
  "energy_wh": 0.12
}
```

| Field       | Arti                                                                                 |
| ----------- | ------------------------------------------------------------------------------------ |
| `device_id` | `sela-node-a` (10 W) atau `sela-node-b` (20 W)                                       |
| `boot_id`   | String acak baru setiap ESP32 boot. Integrasi tidak melewati pergantian boot         |
| `seq`       | Nomor urut paket, naik 1 tiap kirim, mulai 0 setiap boot                             |
| `ts`        | Waktu perangkat (detik epoch, dari NTP). Dipakai untuk integrasi energi              |
| `source`    | `real`, `synthetic`, atau `replay`                                                   |
| `relay_on`  | Status relay sekarang                                                                |
| `sensor_ok` | `false` bila PZEM gagal dibaca. Daya diabaikan dan celah dicatat, bukan dihitung 0 W |
| `power`     | Daya aktif (W). Boleh `null` saat `sensor_ok` false                                  |
| lainnya     | Opsional, disimpan apa adanya                                                        |

Paket dengan kombinasi `device_id` + `boot_id` + `seq` yang sama dianggap duplikat dan energinya tidak dihitung lagi.

## Balasan backend

```json
{
  "ack_seq": 1042,
  "balance_wh": 4.2,
  "lockout": false,
  "duplicate": false,
  "server_ts": 1793000001.2
}
```

ESP32 wajib membaca `lockout`. Bila `true`, tolak perintah ON baru secara lokal.

## Endpoint lain

| Endpoint            | Fungsi                                                                                                              |
| ------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `GET /state`        | Saldo, lockout, daya total, status tiap node (online bila ada data dalam 15 detik), `partial` bila ada node offline |
| `POST /topup`       | `{"amount_wh": 5}`: saldo bertambah, lockout lepas, lampu tetap OFF                                                 |
| `POST /admin/reset` | `{"balance_wh": 5}`: untuk pengembangan dan demo, mengembalikan saldo dan menghapus status node                     |
| `GET /health`       | Cek hidup, tanpa API key                                                                                            |

## Tabel database (SQLite)

| Tabel         | Isi                                                                             |
| ------------- | ------------------------------------------------------------------------------- |
| `telemetry`   | Semua paket mentah, unik per `device_id, boot_id, seq`                          |
| `ledger`      | Energi per paket (`delta_wh`), penanda celah (`gap`) dan alasannya              |
| `device_last` | Sampel terakhir tiap node, dipakai untuk integrasi berikutnya dan status online |
| `account`     | Satu baris: saldo, kuota acuan, overshoot, lockout                              |
| `topups`      | Riwayat top-up                                                                  |
| `commands`    | Perintah OFF saat saldo nol (status awal `requested`, dilacak per node)         |
| `alerts`      | Peringatan saldo rendah, saldo habis, top-up                                    |
