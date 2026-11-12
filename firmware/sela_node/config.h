#pragma once
// Satu basis kode untuk dua node. Pilih SATU sebelum flash.
#define NODE_A
// #define NODE_B

#ifdef NODE_A
  #define DEVICE_ID        "sela-node-a"
  #define SINRIC_DEVICE_ID SINRIC_DEVICE_A_ID
#endif
#ifdef NODE_B
  #define DEVICE_ID        "sela-node-b"
  #define SINRIC_DEVICE_ID SINRIC_DEVICE_B_ID
#endif

// Relay. Banyak modul 5 V aktif LOW (IN LOW = relay ON). Modul yang dipakai: aktif HIGH.
#define PIN_RELAY          23
#define RELAY_ACTIVE_LOW   false

// UART PZEM (Serial2). RX ESP32 = GPIO16 menerima TX PZEM LEWAT PEMBAGI TEGANGAN (3 x 220 ohm seri, tap ke GPIO16).
#define PIN_PZEM_RX        16
#define PIN_PZEM_TX        17
#define PZEM_BAUD          9600
#define PZEM_ADDR          0xF8      // alamat umum Modbus PZEM
#define PZEM_TIMEOUT_MS    300
// Batas kewajaran tegangan. Di luar rentang ini bacaan dianggap gagal (sensor_ok=false), bukan dikirim sebagai data.
#define PZEM_V_MIN         80.0f
#define PZEM_V_MAX         270.0f

#define TELEMETRY_INTERVAL_MS 5000
#define HTTP_TIMEOUT_MS       2000
// Buffer saat gagal kirim: sampel yang belum terkirim ditampung di RAM (hilang bila ESP32 restart).
// 120 sampel x 5 detik = 10 menit. Penuh: sampel terlama dibuang dan dihitung.
#define QUEUE_SIZE            120
// Batas kerja per siklus 5 detik supaya SinricPro.handle() tidak tertahan lama saat mengosongkan antrean.
#define MAX_FLUSH_PER_CYCLE   6
#define FLUSH_BUDGET_MS       1500
// ON baru hanya diterima bila balasan backend terakhir tidak lebih tua dari ini (aturan offline 15 detik)
#define PERMISSION_TTL_MS     15000