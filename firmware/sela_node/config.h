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

// Relay. Banyak modul 5 V aktif LOW (IN LOW = relay ON). Ubah ke 0 bila modul aktif HIGH.
#define PIN_RELAY          23
#define RELAY_ACTIVE_LOW   false

// UART PZEM (Serial2). RX ESP32 = GPIO16 menerima TX PZEM LEWAT PEMBAGI TEGANGAN.
#define PIN_PZEM_RX        16
#define PIN_PZEM_TX        17
#define PZEM_BAUD          9600

#define TELEMETRY_INTERVAL_MS 5000
#define HTTP_TIMEOUT_MS       2000
// ON baru hanya diterima bila balasan backend terakhir tidak lebih tua dari ini (aturan offline 15 detik)
#define PERMISSION_TTL_MS     15000
