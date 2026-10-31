// Uji UART2 tanpa PZEM dan tanpa listrik AC.
// Sambungkan kabel jumper GPIO17 (TX2) ke GPIO16 (RX2) langsung di ESP32, tanpa pembagi tegangan.
// Buka serial monitor 115200. Semua baris harus "OK".
#define PIN_RX 16
#define PIN_TX 17

void setup() {
  Serial.begin(115200);
  Serial2.begin(9600, SERIAL_8N1, PIN_RX, PIN_TX);
  delay(500);
}

void loop() {
  static uint8_t n = 0;
  while (Serial2.available()) Serial2.read();
  Serial2.write(n);
  Serial2.flush();
  delay(50);
  bool ok = Serial2.available() && Serial2.read() == n;
  Serial.printf("kirim 0x%02X -> %s\n", n, ok ? "OK" : "GAGAL (cek jumper GPIO17 ke GPIO16)");
  n++;
  delay(1000);
}
