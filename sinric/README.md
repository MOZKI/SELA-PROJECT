# Uji kontrol Sinric Pro

## 1. Setup di portal (portal.sinric.pro)

1. Devices: buka perangkat SMARTSWITCH, ganti **nama** jadi `Lampu 10 W`. ID perangkat tidak berubah, jadi firmware lama tetap cocok.
   Bila perangkat ini sudah ditautkan ke Alexa atau Google Home, nama baru di sana baru terbaca setelah sinkron ulang.
2. Devices, Add Device: tipe Switch, nama `Lampu 20 W`.
3. Salin ID kedua perangkat (daftar Devices).
4. Credentials, API Keys, buat API Key baru (nama bebas, misal `sela-rest`). Salin sekali, tidak bisa dilihat lagi.
5. Simpan di `backend/.env` (lokal) dan `.env` di server, jangan di repositori:

```
SINRIC_API_KEY=...
SINRIC_DEVICE_A_ID=...
SINRIC_DEVICE_B_ID=...
```

6. Cek: `set -a; source ../backend/.env; set +a; python3 sinric_probe.py devices` harus menampilkan dua perangkat.

## 2. Curl manual (sesuai API Guide Sinric)

```bash
set -a; source ../backend/.env; set +a

# Terminal 1: pantau SSE
curl -N --http2 -H "Accept:text/event-stream" -H "X-SINRIC-API-KEY: $SINRIC_API_KEY" https://sse.sinric.pro/sse/stream

# Terminal 2: ON, lalu OFF
curl -s -X POST "https://api.sinric.pro/api/v1/devices/$SINRIC_DEVICE_A_ID/action" \
  -H "X-SINRIC-API-KEY: $SINRIC_API_KEY" -H "Content-Type: application/json" \
  --data '{"type":"request","action":"setPowerState","value":"{\"state\":\"On\"}"}'
```

Balasan `success:true` hanya berarti pesan masuk antrean (kata dokumen Sinric: antrean diproses asinkron). Bukti relay berubah
adalah event SSE `deviceMessageArrived` dengan `powerState` yang sesuai, ditambah LED dan log serial ESP32.

## 3. Uji otomatis dengan pengukur waktu

```bash
python3 sinric_probe.py watch                          # log SSE berstempel waktu
python3 sinric_probe.py set A on                       # satu perintah dan latensinya
python3 sinric_probe.py latency A -n 20 --gap 6        # 20 kali ON/OFF bergantian, min, median, p95, maks
python3 sinric_probe.py burst A on,off,on,off,on --gap 0.2
python3 sinric_probe.py offline A                      # terpandu: ESP32 dimatikan lalu dinyalakan
```

Latensi = dari sebelum POST sampai event SSE pertama yang cocok. Hasil `latency` ditambahkan ke `results/latency.csv`.
Kalau tidak ada event SSE yang cocok, cek dulu apakah event `deviceMessageArrived` benar muncul untuk perintah dari API
(pakai `watch`). Kalau tidak, ukur dari log serial ESP32 (stempel waktu NTP di tiap baris `SINRIC ...`) dan bandingkan dengan waktu kirim.
Uji kering tanpa akun: `mock_sinric.py` (lihat komentar di file).

Untuk laporan, batas cut-off 10 detik di brief disesuaikan dari p95 dan maksimum di sini.

## 4. Uji paling penting: ESP32 menolak ON saat lockout

Prasyarat: Node A online di `GET /state`, serial monitor terbuka, LED uji terpasang.

1. Baseline: `set A on` lalu `set A off`. LED mengikuti.
2. Paksa lockout tanpa menunggu saldo habis: `curl -s -X POST $SELA_URL/admin/reset -H "X-API-Key: $SELA_API_KEY" -H "Content-Type: application/json" -d '{"balance_wh":0}'`.
   Paket telemetri berikutnya (maksimal 5 detik) memicu lockout. Cek `GET /state`, `lockout` harus `true`, dan serial menampilkan `lockout=1`.
3. Kirim ON lewat tiap jalur dan isi tabel di bawah. Yang diharapkan: LED tetap mati, serial menampilkan `SINRIC ON DITOLAK`.
4. Kirim OFF: harus tetap diterima.
5. Top-up: `curl -s -X POST $SELA_URL/topup -H "X-API-Key: $SELA_API_KEY" -H "Content-Type: application/json" -d '{"amount_wh":3}'`.
   Setelah maksimal 5 detik, ON harus diterima lagi, dan lampu tidak menyala sendiri sebelum ON.
6. Backend tidak terjangkau: di server `docker compose stop api`, tunggu 20 detik, kirim ON. Harus ditolak (izin tak dapat dipastikan).
   Nyalakan lagi `docker compose start api`, tunggu 10 detik, ON harus diterima.

Kesimpulan yang boleh ditulis di laporan:

- Semua jalur yang diuji menolak: lockout menyeluruh untuk jalur tersebut.
- Ada jalur yang lolos: jangan klaim lockout menyeluruh, tulis jalur yang lolos sebagai batasan (sesuai aturan di brief).

## 5. Tabel hasil (isi hanya dari hasil nyata)

| Uji                                 | Jalur                         | Hasil yang diharapkan                          | Hasil aktual |
| ----------------------------------- | ----------------------------- | ---------------------------------------------- | ------------ |
| ON saat lockout                     | REST API (probe)              | LED mati, serial DITOLAK                       |              |
| ON saat lockout                     | Aplikasi Sinric, Wi-Fi sama   | sama                                           |              |
| ON saat lockout                     | Aplikasi Sinric, data seluler | sama                                           |              |
| ON saat lockout                     | Voice, bila sudah tertaut     | sama                                           |              |
| Tampilan Sinric setelah ditolak     | Aplikasi atau portal          | Kembali ke Off                                 |              |
| OFF saat lockout                    | REST API                      | Diterima                                       |              |
| ON setelah top-up                   | REST API                      | Diterima, maksimal 5 detik sesudah top-up      |              |
| ON saat backend mati 20 detik       | REST API                      | Ditolak                                        |              |
| Perintah saat ESP32 mati lalu hidup | `offline`                     | Catat: dieksekusi saat tersambung atau dibuang |              |
| Perintah beruntun                   | `burst`                       | Urutan dan state akhir sesuai                  |              |
| Latensi ON/OFF                      | `latency -n 20`               | Catat min, median, p95, maks                   |              |
