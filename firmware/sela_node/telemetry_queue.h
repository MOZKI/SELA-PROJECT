#pragma once
// Antrean sampel telemetri (ring buffer FIFO) untuk buffer saat gagal kirim.
// Sengaja tanpa dependensi Arduino supaya logikanya bisa dites di komputer (lihat firmware/tests/).
//
// Aturan:
//  - Sampel diberi seq SEKALI saat diambil. Kirim ulang selalu memakai seq yang sama, backend menolak
//    duplikat lewat UNIQUE (device_id, boot_id, seq), jadi kirim ulang aman walau kiriman sebelumnya
//    sebenarnya sudah sampai (timeout).
//  - Dikirim berurutan dari yang terlama supaya integrasi trapesium di backend tetap berurutan.
//  - Bila antrean penuh, sampel TERLAMA dibuang dan dihitung di dropped().
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

struct Sample {
  uint32_t seq;
  double ts;          // epoch saat sampel DIAMBIL, bukan saat dikirim
  bool relay_on;      // status relay saat sampel diambil
  bool sensor_ok;
  float v, i, p, pf, f;
  uint32_t e_wh;
};

template <size_t N>
class SampleQueue {
 public:
  bool empty() const { return count_ == 0; }
  size_t size() const { return count_; }
  uint32_t dropped() const { return dropped_; }

  // Return true bila ada sampel lama yang terpaksa dibuang karena penuh.
  bool push(const Sample &s) {
    bool overwrote = false;
    if (count_ == N) {            // penuh: buang yang terlama
      head_ = (head_ + 1) % N;
      count_--;
      dropped_++;
      overwrote = true;
    }
    buf_[(head_ + count_) % N] = s;
    count_++;
    return overwrote;
  }

  const Sample &front() const { return buf_[head_]; }

  void pop() {
    if (count_ == 0) return;
    head_ = (head_ + 1) % N;
    count_--;
  }

 private:
  Sample buf_[N];
  size_t head_ = 0;
  size_t count_ = 0;
  uint32_t dropped_ = 0;
};

// Badan JSON telemetri. Dibuat manual supaya ts tidak terpotong presisinya (epoch + milidetik).
// Return jumlah karakter yang ditulis, atau -1 bila buffer kurang.
static inline int formatTelemetryJson(const Sample &s, const char *deviceId, const char *bootId, char *out,
                                      size_t n) {
  char meas[200];
  if (s.sensor_ok) {
    snprintf(meas, sizeof(meas),
             "\"sensor_ok\":true,\"voltage\":%.1f,\"current\":%.3f,\"power\":%.1f,\"pf\":%.2f,"
             "\"freq\":%.1f,\"energy_wh\":%lu",
             s.v, s.i, s.p, s.pf, s.f, (unsigned long)s.e_wh);
  } else {
    snprintf(meas, sizeof(meas), "\"sensor_ok\":false,\"power\":null");
  }
  int len = snprintf(out, n,
                     "{\"device_id\":\"%s\",\"boot_id\":\"%s\",\"seq\":%lu,\"ts\":%.3f,\"source\":\"real\","
                     "\"relay_on\":%s,%s}",
                     deviceId, bootId, (unsigned long)s.seq, s.ts, s.relay_on ? "true" : "false", meas);
  return (len < 0 || (size_t)len >= n) ? -1 : len;
}