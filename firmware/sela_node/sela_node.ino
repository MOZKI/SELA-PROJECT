// SELA node (Node A atau B, pilih di config.h)
// Jumat: relay OFF saat boot, satu perangkat Sinric, telemetri tiap 5 detik dengan sensor_ok=false,
// penegakan lockout lokal (tolak ON dari Sinric saat lockout atau izin tak dapat dipastikan).
// Belum ada: PZEM asli, buffer saat gagal kirim (Minggu 2).
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

static double nowEpoch() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return tv.tv_sec + tv.tv_usec / 1e6;
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
  // Dibuat manual supaya ts tidak terpotong presisinya (epoch + milidetik).
  char body[320];
  snprintf(body, sizeof(body),
           "{\"device_id\":\"%s\",\"boot_id\":\"%s\",\"seq\":%lu,\"ts\":%.3f,\"source\":\"real\","
           "\"relay_on\":%s,\"sensor_ok\":false,\"power\":null}",
           DEVICE_ID, bootId, (unsigned long)seqNo, ts, relayOn ? "true" : "false");
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
  Serial2.begin(PZEM_BAUD, SERIAL_8N1, PIN_PZEM_RX, PIN_PZEM_TX);   // dipakai mulai Minggu 2

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
    Serial.printf("[%.3f] relay=%d lockout=%d saldo=%.4f ack=%s kirim ok/gagal=%lu/%lu wifi=%d sinric=%d\n",
                  nowEpoch(), relayOn, lockout, balanceWh, onPermitted() ? "segar" : "TIDAK segar",
                  (unsigned long)sendOk, (unsigned long)sendFail, WiFi.status() == WL_CONNECTED,
                  SinricPro.isConnected());
  }
}
