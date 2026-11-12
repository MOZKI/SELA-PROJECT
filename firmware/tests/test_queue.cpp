// Uji antrean dan format JSON di komputer: g++ -std=c++17 -I../sela_node test_queue.cpp -o /tmp/tq && /tmp/tq
#include <cassert>
#include <cstdio>
#include <cstring>
#include "telemetry_queue.h"

static Sample mk(uint32_t seq, bool ok = true) {
  Sample s = {};
  s.seq = seq; s.ts = 1793000000.0 + seq * 5; s.relay_on = true; s.sensor_ok = ok;
  if (ok) { s.v = 215.3f; s.i = 0.139f; s.p = 19.4f; s.pf = 0.66f; s.f = 50.0f; s.e_wh = 7; }
  return s;
}

int main() {
  // 1. FIFO + front tidak berubah selama kirim ulang (seq sama)
  SampleQueue<4> q;
  for (uint32_t i = 0; i < 3; i++) q.push(mk(i));
  assert(q.size() == 3 && q.front().seq == 0);
  assert(q.front().seq == 0);                  // gagal kirim: tidak di-pop, seq tetap 0
  q.pop(); assert(q.front().seq == 1);

  // 2. Penuh: sampel terlama dibuang, urutan tetap, dropped terhitung
  SampleQueue<4> r;
  for (uint32_t i = 0; i < 4; i++) assert(!r.push(mk(i)));
  assert(r.push(mk(4)));                       // membuang seq 0
  assert(r.push(mk(5)));                       // membuang seq 1
  assert(r.size() == 4 && r.dropped() == 2);
  for (uint32_t want = 2; want <= 5; want++) { assert(r.front().seq == want); r.pop(); }
  assert(r.empty());
  r.pop();                                     // pop pada antrean kosong aman
  assert(r.empty());

  // 3. Wrap-around ring beberapa kali tanpa meluap: isi 2, lalu tiap putaran push 1 dan pop 1
  SampleQueue<3> w;
  uint32_t next = 0, expect = 0;
  w.push(mk(next++)); w.push(mk(next++));
  for (int round = 0; round < 50; round++) {
    w.push(mk(next++));
    assert(w.front().seq == expect); w.pop(); expect++;
    assert(w.size() == 2);
  }
  assert(w.dropped() == 0);

  // 4. JSON: sensor ok dan sensor gagal (power null, bukan 0)
  char buf[448];
  int n = formatTelemetryJson(mk(42), "sela-node-a", "deadbeef", buf, sizeof(buf));
  assert(n > 0); puts(buf);
  n = formatTelemetryJson(mk(43, false), "sela-node-a", "deadbeef", buf, sizeof(buf));
  assert(n > 0 && strstr(buf, "\"power\":null") && strstr(buf, "\"sensor_ok\":false")); puts(buf);
  char tiny[20];
  assert(formatTelemetryJson(mk(1), "x", "y", tiny, sizeof(tiny)) == -1);
  puts("OK");
  return 0;
}