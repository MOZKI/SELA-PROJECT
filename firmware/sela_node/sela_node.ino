// SELA node (Node A atau B, pilih di config.h)
// Relay OFF saat boot, satu perangkat Sinric, telemetri tiap 5 detik dari PZEM asli (Modbus-RTU langsung),
// penegakan lockout lokal (tolak ON dari Sinric saat lockout atau izin tak dapat dipastikan).
// Bacaan PZEM gagal dikirim sebagai sensor_ok=false (bukan 0 W). Belum ada: buffer saat gagal kirim.
//
// Library: SinricPro, ArduinoJson (minimal 7.0.3), WebSockets by Markus Sattler (minimal 2.4.0).
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <sys/time.h>
#include <SinricPro.h>
#include <SinricProSwitch.h>
#include "secrets.h"
#include "config.h"

static bool relayOn = false;
static bool lockout = false;
static bool haveAck = false;
static uint32_t lastAckMs = 0;
static double balanceWh = -1;
static bool resyncSinric = false;   // minta kirim state relay sebenarnya ke Sinric dari loop()
static uint32_t seqNo = 0;
static char bootId[9];
static uint32_t lastSendMs = 0, lastStatusMs = 0, lastWifiTryMs = 0;
static uint32_t sendOk = 0, sendFail = 0;
static uint32_t pzemOk = 0, pzemFail = 0;

static double nowEpoch() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return tv.tv_sec + tv.tv_usec / 1e6;
}


// ---- PZEM (Modbus-RTU langsung, sama dengan firmware/pzem_test) ----
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

static void buildRequest(uint8_t addr, uint8_t out[8]) {
  out[0] = addr; out[1] = 0x04; out[2] = 0x00; out[3] = 0x00; out[4] = 0x00; out[5] = 0x0A;
  uint16_t c = crc16(out, 6);
  out[6] = c & 0xFF;
  out[7] = c >> 8;
}

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

// true bila balasan valid DAN tegangan masuk akal. Selain itu sensor_ok=false.
static bool pzemRead(PzemData &d) {
  uint8_t req[8], buf[32];
  buildRequest(PZEM_ADDR, req);
  while (Serial2.available()) Serial2.read();
  Serial2.write(req, 8);
  Serial2.flush();
  size_t n = 0;
  uint32_t t0 = millis(), lastByte = t0;
  while (millis() - t0 < PZEM_TIMEOUT_MS && n < sizeof(buf)) {
    if (Serial2.available()) { buf[n++] = Serial2.read(); lastByte = millis(); }
    else if (n >= 25 && millis() - lastByte > 5) break;
    else delay(1);
  }
  if (!parseFrame(buf, n, d)) { pzemFail++; return false; }
  if (d.v < PZEM_V_MIN || d.v > PZEM_V_MAX) {
    Serial.printf("PZEM tegangan tidak wajar: %.1f V, bacaan dibuang\n", d.v);
    pzemFail++;
    return false;
  }
  pzemOk++;
  return true;
}

static void setRelay(bool on) {
  relayOn = on;
  digitalWrite(PIN_RELAY, (on ^ RELAY_ACTIVE_LOW) ? HIGH : LOW);
}

// ON baru boleh bila: balasan backend masih segar, dan lockout tidak aktif.
static bool onPermitted() {
  return haveAck && !lockout && (millis() - lastAckMs) <= PERMISSION_TTL_MS;
}

// Callback Sinric. Return false dan state=false berarti perintah ditolak.
static bool onPowerState(const String &deviceId, bool &state) {
  if (state) {
    if (!onPermitted()) {
      Serial.printf("[%.3f] SINRIC ON DITOLAK lockout=%d ack_ada=%d umur_ack=%lu ms\n", nowEpoch(), lockout,
                    haveAck, haveAck ? (unsigned long)(millis() - lastAckMs) : 0UL);
      state = false;
      resyncSinric = true;
      return false;
    }
    setRelay(true);
  } else {
    setRelay(false);
  }
  Serial.printf("[%.3f] SINRIC %s diterima, relay=%d\n", nowEpoch(), state ? "ON" : "OFF", relayOn);
  return true;
}

static void sendTelemetry() {
  double ts = nowEpoch();
  if (ts < 1.6e9) {                 // NTP belum sinkron, jangan kirim ts palsu
    Serial.println("NTP belum sinkron, telemetri ditunda");
    return;
  }
  PzemData d;
  bool ok = pzemRead(d);
  ts = nowEpoch();                  // waktu sampel diambil sesudah PZEM terbaca

  char meas[200];
  if (ok) {
    snprintf(meas, sizeof(meas),
             "\"sensor_ok\":true,\"voltage\":%.1f,\"current\":%.3f,\"power\":%.1f,\"pf\":%.2f,"
             "\"freq\":%.1f,\"energy_wh\":%lu",
             d.v, d.i, d.p, d.pf, d.f, (unsigned long)d.e_wh);
    Serial.printf("[%.3f] PZEM V=%.1f I=%.3f P=%.1f PF=%.2f f=%.1f E=%lu Wh\n", ts, d.v, d.i, d.p, d.pf, d.f,
                  (unsigned long)d.e_wh);
  } else {
    snprintf(meas, sizeof(meas), "\"sensor_ok\":false,\"power\":null");
    Serial.printf("[%.3f] PZEM GAGAL dibaca, dikirim sensor_ok=false\n", ts);
  }

  // Dibuat manual supaya ts tidak terpotong presisinya (epoch + milidetik).
  char body[448];
  snprintf(body, sizeof(body),
           "{\"device_id\":\"%s\",\"boot_id\":\"%s\",\"seq\":%lu,\"ts\":%.3f,\"source\":\"real\","
           "\"relay_on\":%s,%s}",
           DEVICE_ID, bootId, (unsigned long)seqNo, ts, relayOn ? "true" : "false", meas);
  seqNo++;                          // naik tiap percobaan; celah seq tidak masalah, duplikat yang bermasalah

  WiFiClient client;
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.begin(client, String(API_URL) + "/telemetry");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-API-Key", API_KEY);
  int code = http.POST(body);
  if (code == 200) {
    JsonDocument doc;
    if (!deserializeJson(doc, http.getString())) {
      lockout = doc["lockout"] | false;
      balanceWh = doc["balance_wh"] | -1.0;
      lastAckMs = millis();
      haveAck = true;
      sendOk++;
      if (lockout && relayOn) {     // cut-off lokal
        setRelay(false);
        resyncSinric = true;
        Serial.printf("[%.3f] LOCKOUT dari backend, relay dipaksa OFF\n", nowEpoch());
      }
    } else {
      sendFail++;
    }
  } else {
    sendFail++;
    Serial.printf("kirim gagal, kode %d\n", code);
  }
  http.end();
}

void setup() {
  // Relay ke level OFF SEBELUM pinMode supaya tidak ada kedip saat pin jadi output.
  digitalWrite(PIN_RELAY, RELAY_ACTIVE_LOW ? HIGH : LOW);
  pinMode(PIN_RELAY, OUTPUT);
  relayOn = false;

  Serial.begin(115200);
  Serial2.begin(PZEM_BAUD, SERIAL_8N1, PIN_PZEM_RX, PIN_PZEM_TX);   

  snprintf(bootId, sizeof(bootId), "%08lx", (unsigned long)esp_random());
  Serial.printf("\n%s boot_id=%s relay=OFF\n", DEVICE_ID, bootId);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(0, 0, "pool.ntp.org", "time.google.com");

  SinricProSwitch &sw = SinricPro[SINRIC_DEVICE_ID];
  sw.onPowerState(onPowerState);
  SinricPro.onConnected([]() {
    Serial.printf("[%.3f] Sinric tersambung\n", nowEpoch());
    resyncSinric = true;            // portal bisa menyimpan state On dari sesi lama, relay kita OFF
  });
  SinricPro.onDisconnected([]() { Serial.printf("[%.3f] Sinric terputus\n", nowEpoch()); });
  SinricPro.begin(SINRIC_APP_KEY, SINRIC_APP_SECRET);
}

void loop() {
  SinricPro.handle();

  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - lastWifiTryMs > 10000) {
      lastWifiTryMs = millis();
      WiFi.reconnect();
    }
  } else if (millis() - lastSendMs >= TELEMETRY_INTERVAL_MS) {
    lastSendMs = millis();
    sendTelemetry();
  }

  if (resyncSinric && SinricPro.isConnected()) {
    SinricProSwitch &sw = SinricPro[SINRIC_DEVICE_ID];
    sw.sendPowerStateEvent(relayOn);
    resyncSinric = false;
    Serial.printf("[%.3f] state relay=%d dikirim ke Sinric\n", nowEpoch(), relayOn);
  }

  if (millis() - lastStatusMs >= 10000) {
    lastStatusMs = millis();
    Serial.printf("[%.3f] relay=%d lockout=%d saldo=%.4f ack=%s kirim ok/gagal=%lu/%lu pzem ok/gagal=%lu/%lu wifi=%d sinric=%d\n",
                  nowEpoch(), relayOn, lockout, balanceWh, onPermitted() ? "segar" : "TIDAK segar",
                  (unsigned long)sendOk, (unsigned long)sendFail, (unsigned long)pzemOk, (unsigned long)pzemFail,
                  WiFi.status() == WL_CONNECTED,
                  SinricPro.isConnected());
  }
}