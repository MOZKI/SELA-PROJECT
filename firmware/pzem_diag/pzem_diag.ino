// Diagnostik PZEM: coba beberapa baud, dua alamat Modbus, dan protokol lama PZEM-004. Tanpa Wi-Fi.
// Serial Monitor 115200. Perintah: 1 = relay ON, 0 = relay OFF (untuk mengecek jalur AC lewat bohlam).
// Satu putaran penuh sekitar 10 detik, lalu diulang. Kirim hasil satu putaran penuh.
//
// Cara baca hasil:
//   "terima 0 byte"           = PZEM diam (belum hidup, wiring/arah TX-RX, atau baud tidak cocok)
//   "terima N byte ... CRC OK" = komunikasi BERHASIL, catat baud dan alamatnya
//   ada byte tapi CRC salah    = ada sinyal, kemungkinan baud atau level sinyal

#include <Arduino.h>

#define PIN_RELAY        23
#define RELAY_ACTIVE_LOW false
#define PIN_RX           16   // dari PZEM TX lewat pembagi
#define PIN_TX           17   // ke PZEM RX
#define WAIT_MS          300

static const uint32_t BAUDS[] = {9600, 4800, 19200, 2400, 38400, 115200, 1200};
static const uint8_t  ADDRS[] = {0xF8, 0x01};

static uint16_t crc16(const uint8_t *buf, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t k = 0; k < len; k++) {
    crc ^= buf[k];
    for (int b = 0; b < 8; b++) crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : (crc >> 1);
  }
  return crc;
}

static void setRelay(bool on) {
  digitalWrite(PIN_RELAY, (on ^ RELAY_ACTIVE_LOW) ? HIGH : LOW);
  Serial.printf("# relay %s\n", on ? "ON" : "OFF");
}

// Kirim frame, tunggu balasan, kembalikan jumlah byte (maks 32).
static size_t transact(const uint8_t *req, size_t reqLen, uint8_t *buf) {
  while (Serial2.available()) Serial2.read();
  Serial2.write(req, reqLen);
  Serial2.flush();
  size_t n = 0;
  uint32_t t0 = millis(), lastByte = t0;
  while (millis() - t0 < WAIT_MS && n < 32) {
    if (Serial2.available()) { buf[n++] = Serial2.read(); lastByte = millis(); }
    else if (n > 0 && millis() - lastByte > 20) break;
    else delay(1);
  }
  return n;
}

static void printHex(const uint8_t *b, size_t n) {
  for (size_t k = 0; k < n; k++) Serial.printf(" %02X", b[k]);
}

static void tryModbus(uint32_t baud, uint8_t addr) {
  uint8_t req[8] = {addr, 0x04, 0x00, 0x00, 0x00, 0x0A, 0, 0};
  uint16_t c = crc16(req, 6);
  req[6] = c & 0xFF; req[7] = c >> 8;
  uint8_t buf[32];
  size_t n = transact(req, 8, buf);
  Serial.printf("baud %-6lu modbus addr %02X: terima %u byte", (unsigned long)baud, addr, (unsigned)n);
  if (n > 0) {
    Serial.print(":"); printHex(buf, n);
    if (n >= 5) {
      uint16_t cc = crc16(buf, n - 2);
      bool ok = buf[n - 2] == (cc & 0xFF) && buf[n - 1] == (cc >> 8);
      Serial.print(ok ? "  -> CRC OK" : "  -> CRC SALAH");
      if (ok && n == 25) {
        float v = (((uint16_t)buf[3] << 8) | buf[4]) / 10.0f;
        Serial.printf("  (V=%.1f)  <<< BERHASIL", v);
      }
    }
  } else Serial.print(" (diam)");
  Serial.println();
}

// Protokol lama PZEM-004 (bukan V3): set alamat B4 C0 A8 01 01 00 1E -> balasan A4; baca V: B0 ... 1A -> A0.
static void tryLegacy() {
  uint8_t buf[32];
  const uint8_t setAddr[7] = {0xB4, 0xC0, 0xA8, 0x01, 0x01, 0x00, 0x1E};
  const uint8_t readV[7]   = {0xB0, 0xC0, 0xA8, 0x01, 0x01, 0x00, 0x1A};
  size_t n = transact(setAddr, 7, buf);
  Serial.printf("baud 9600   protokol lama set-alamat: terima %u byte", (unsigned)n);
  if (n) { Serial.print(":"); printHex(buf, n); } else Serial.print(" (diam)");
  Serial.println();
  n = transact(readV, 7, buf);
  Serial.printf("baud 9600   protokol lama baca-V   : terima %u byte", (unsigned)n);
  if (n) { Serial.print(":"); printHex(buf, n); } else Serial.print(" (diam)");
  Serial.println();
}

void setup() {
  digitalWrite(PIN_RELAY, RELAY_ACTIVE_LOW ? HIGH : LOW);
  pinMode(PIN_RELAY, OUTPUT);
  Serial.begin(115200);
  delay(500);
  Serial.println("\n# PZEM diagnostik. Perintah: 1=relay ON, 0=relay OFF");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '1') setRelay(true);
    else if (c == '0') setRelay(false);
  }

  // Level idle jalur RX sebelum UART dipasang: harus HIGH bila TX PZEM hidup dan terhubung.
  pinMode(PIN_RX, INPUT);
  delay(5);
  Serial.printf("\n=== putaran baru | level idle GPIO%d = %s ===\n", PIN_RX, digitalRead(PIN_RX) ? "HIGH" : "LOW");

  for (uint32_t baud : BAUDS) {
    Serial2.begin(baud, SERIAL_8N1, PIN_RX, PIN_TX);
    delay(50);
    for (uint8_t a : ADDRS) tryModbus(baud, a);
    if (baud == 9600) tryLegacy();
    Serial2.end();
  }
  delay(2000);
}
