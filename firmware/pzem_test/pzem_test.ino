// Uji komunikasi UART PZEM-004T V4.0 (Modbus-RTU langsung, tanpa library), tanpa Wi-Fi dan tanpa Sinric.
// Wiring: PZEM TX -> pembagi 3 x 220 ohm -> GPIO16 (RX2). GPIO17 (TX2) -> PZEM RX. Relay IN -> D23.
//
// Perintah lewat Serial Monitor (115200, newline boleh apa saja):
//   1 = relay ON    0 = relay OFF    s = ringkasan statistik daya    z = reset statistik
// Relay selalu OFF saat boot. Ringkasan otomatis dicetak tiap relay berganti, untuk bahan deadband.

#include <Arduino.h>
#include <math.h>

#define PIN_RELAY        23
#define RELAY_ACTIVE_LOW false
#define PIN_PZEM_RX      16
#define PIN_PZEM_TX      17
#define PZEM_BAUD        9600
#define PZEM_ADDR        0xF8      // alamat umum Modbus PZEM (satu slave di bus)
#define PZEM_TIMEOUT_MS  300
#define POLL_MS          1000

// ---- PZEM BEGIN (fungsi murni, diuji di host) ----
struct PzemData {
  float v, i, p, pf, f;
  uint32_t e_wh;
  bool alarm;
};

static uint16_t crc16(const uint8_t *buf, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t k = 0; k < len; k++) {
    crc ^= buf[k];
    for (int b = 0; b < 8; b++) crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : (crc >> 1);
  }
  return crc;
}

// Permintaan: baca 10 input register mulai alamat 0 (fungsi 0x04).
static void buildRequest(uint8_t addr, uint8_t out[8]) {
  out[0] = addr; out[1] = 0x04; out[2] = 0x00; out[3] = 0x00; out[4] = 0x00; out[5] = 0x0A;
  uint16_t c = crc16(out, 6);
  out[6] = c & 0xFF;          // CRC low dulu
  out[7] = c >> 8;
}

// Balasan normal 25 byte: addr, 0x04, 0x14, 20 byte data, CRC (2 byte).
static bool parseFrame(const uint8_t *b, size_t n, PzemData &d) {
  if (n != 25 || b[1] != 0x04 || b[2] != 0x14) return false;
  uint16_t crc = crc16(b, 23);
  if (b[23] != (crc & 0xFF) || b[24] != (crc >> 8)) return false;
  auto reg = [&](int k) -> uint32_t { return ((uint32_t)b[3 + 2 * k] << 8) | b[4 + 2 * k]; };
  d.v     = reg(0) / 10.0f;
  d.i     = (((reg(2) << 16) | reg(1))) / 1000.0f;
  d.p     = (((reg(4) << 16) | reg(3))) / 10.0f;
  d.e_wh  = (reg(6) << 16) | reg(5);
  d.f     = reg(7) / 10.0f;
  d.pf    = reg(8) / 100.0f;
  d.alarm = reg(9) == 0xFFFF;
  return true;
}
// ---- PZEM END ----

static bool relayOn = false;
static uint32_t okCount = 0, failCount = 0;
static uint32_t sN = 0;
static double sSum = 0, sMin = 1e9, sMax = -1e9;

static void setRelay(bool on) {
  relayOn = on;
  digitalWrite(PIN_RELAY, (on ^ RELAY_ACTIVE_LOW) ? HIGH : LOW);
}

static void statsReset() { sN = 0; sSum = 0; sMin = 1e9; sMax = -1e9; }

static void statsPrint(const char *label) {
  if (sN == 0) { Serial.printf("# stat %s: belum ada sampel\n", label); return; }
  Serial.printf("# stat %s: n=%lu daya min=%.1f maks=%.1f rata2=%.2f selisih=%.1f W\n",
                label, (unsigned long)sN, sMin, sMax, sSum / sN, sMax - sMin);
}

// Kirim permintaan, kumpulkan balasan. Mengembalikan jumlah byte yang diterima (maks 32).
static size_t pzemTransact(uint8_t *buf) {
  uint8_t req[8];
  buildRequest(PZEM_ADDR, req);
  while (Serial2.available()) Serial2.read();
  Serial2.write(req, 8);
  Serial2.flush();
  size_t n = 0;
  uint32_t t0 = millis();
  uint32_t lastByte = t0;
  while (millis() - t0 < PZEM_TIMEOUT_MS && n < 32) {
    if (Serial2.available()) { buf[n++] = Serial2.read(); lastByte = millis(); }
    else if (n >= 25 && millis() - lastByte > 5) break;
    else delay(1);
  }
  return n;
}

static void handleCommand(char c) {
  if (c == '1') {
    statsPrint("sebelum ON (relay OFF)"); statsReset(); setRelay(true);
    Serial.println("# relay ON");
  } else if (c == '0') {
    statsPrint("sebelum OFF (relay ON)"); statsReset(); setRelay(false);
    Serial.println("# relay OFF");
  } else if (c == 's') statsPrint(relayOn ? "relay ON" : "relay OFF");
  else if (c == 'z') { statsReset(); Serial.println("# statistik direset"); }
}

void setup() {
  digitalWrite(PIN_RELAY, RELAY_ACTIVE_LOW ? HIGH : LOW);   // OFF sebelum pinMode
  pinMode(PIN_RELAY, OUTPUT);
  relayOn = false;
  Serial.begin(115200);
  Serial2.begin(PZEM_BAUD, SERIAL_8N1, PIN_PZEM_RX, PIN_PZEM_TX);
  delay(500);
  Serial.println("\n# PZEM test. relay=OFF. Perintah: 1=ON 0=OFF s=statistik z=reset");
  Serial.println("ms,relay,V,A,W,PF,Hz,Wh_register,alarm");
}

void loop() {
  while (Serial.available()) handleCommand((char)Serial.read());

  static uint32_t last = 0;
  if (millis() - last < POLL_MS) return;
  last = millis();

  uint8_t buf[32];
  size_t n = pzemTransact(buf);
  PzemData d;
  if (parseFrame(buf, n, d)) {
    okCount++;
    sN++; sSum += d.p; if (d.p < sMin) sMin = d.p; if (d.p > sMax) sMax = d.p;
    Serial.printf("%lu,%d,%.1f,%.3f,%.1f,%.2f,%.1f,%lu,%d\n", (unsigned long)millis(), relayOn, d.v, d.i, d.p,
                  d.pf, d.f, (unsigned long)d.e_wh, d.alarm);
  } else {
    failCount++;
    Serial.printf("GAGAL (ok=%lu gagal=%lu) terima %u byte:", (unsigned long)okCount, (unsigned long)failCount,
                  (unsigned)n);
    for (size_t k = 0; k < n; k++) Serial.printf(" %02X", buf[k]);
    if (n == 0) Serial.print(" (tidak ada balasan sama sekali)");
    Serial.println();
  }
}
