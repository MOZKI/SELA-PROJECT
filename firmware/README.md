# Firmware SELA

Satu basis kode untuk dua node. Pilih `NODE_A` atau `NODE_B` di `sela_node/config.h` sebelum flash.

## Persiapan Arduino IDE

1. Board: ESP32 Dev Module (ESP32 DevKit 30 pin).
2. Library Manager: `SinricPro`, `ArduinoJson` (minimal 7.0.3), `WebSockets` oleh Markus Sattler (minimal 2.4.0).
3. Salin `sela_node/secrets.example.h` jadi `sela_node/secrets.h`, isi Wi-Fi, App Key, App Secret, ID perangkat, `API_URL`, `API_KEY`.
   App Key dan App Secret ada di portal Sinric menu Credentials. Itu berbeda dari API Key REST yang dipakai `sinric_probe.py`.
4. Buka `sela_node/sela_node.ino`, upload, serial monitor 115200.

Yang dikerjakan firmware sekarang: relay OFF saat boot, satu perangkat Sinric, telemetri tiap 5 detik dengan
`sensor_ok:false` dan `power:null` (tidak ada angka dummy, jadi ledger mencatat celah dan saldo tidak turun),
penolakan ON saat lockout atau saat balasan backend lebih tua dari 15 detik. Belum ada: PZEM asli dan buffer saat gagal kirim.

## Uji relay OFF saat boot dan reset (tanpa 220 V)

Rangkaian uji: relay disambung ke catu 5 V dari ESP32 (VIN atau pin 5V), dan kontaknya memutus LED dengan resistor 330 ohm
ke 5 V: `5V -> kontak COM -> kontak NO -> resistor 330 ohm -> LED -> GND`. Terminal AC modul relay dibiarkan kosong.

| No  | Uji                                       | Hasil yang diharapkan                                | Hasil aktual |
| --- | ----------------------------------------- | ---------------------------------------------------- | ------------ |
| 1   | Colok USB (power on)                      | LED tidak pernah menyala, tidak ada bunyi klik relay |              |
| 2   | Tekan tombol EN (reset) 10 kali           | LED tidak pernah menyala                             |              |
| 3   | Nyalakan lewat Sinric (ON), lalu tekan EN | LED mati setelah reset dan tetap mati                |              |
| 4   | Nyalakan ON, cabut USB, colok lagi        | LED mati saat boot                                   |              |
| 5   | Upload firmware ulang saat LED menyala    | LED mati selama dan sesudah upload                   |              |

Kalau ada kedip atau klik saat boot, cek `RELAY_ACTIVE_LOW`. Bila modul relay menyala saat pin mengambang,
solusinya di hardware (resistor pull-up ke 5 V pada IN untuk modul aktif LOW, atau transistor driver), bukan di kode.
Ini harus beres sebelum Minggu 2 karena tegangan AC baru boleh dipasang setelah relay terbukti aman saat boot.

## Sambungan UART PZEM (GPIO16 dan GPIO17)

```
PZEM TX ---[ R1 1k ]---+--- GPIO16 (RX2 ESP32)
                       |
                    [ R2 2k ]
                       |
                      GND
GPIO17 (TX2 ESP32) -------------- PZEM RX   (langsung, 3,3 V)
GND ESP32 ------------------------ GND TTL PZEM
```

Pembagi 1k dan 2k menurunkan 5 V jadi sekitar 3,3 V (5 x 2/3). Dua nilai lain dengan rasio 1:2 juga bisa (10k dan 20k).
Asumsi yang harus dicek: logika TX PZEM adalah 5 V, dan RX PZEM menerima 3,3 V. Keduanya bergantung pada modul V4.0
yang kalian punya, jadi cocokkan dengan dokumentasi modul dan ukur dengan multimeter di Minggu 2.

Yang bisa dibuktikan sekarang tanpa AC:

1. Pembagi: beri 5 V dari pin 5V ESP32 ke ujung R1, ukur titik tengah. Harus 3,2 sampai 3,4 V. Jangan sambungkan ke GPIO16 sebelum terukur.
2. Pin UART2: upload `uart_loopback/uart_loopback.ino`, jumper GPIO17 ke GPIO16 langsung (tanpa pembagi), semua baris harus `OK`.
3. Kedua node: pembagi dan jumper sama persis, GPIO sama.

PZEM sendiri tidak akan menjawab tanpa tegangan AC, jadi uji baca baru bisa Minggu 2 setelah rangkaian AC diperiksa.
