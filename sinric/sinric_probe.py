#!/usr/bin/env python3
"""Alat uji kontrol Sinric Pro lewat curl: kirim ON/OFF, pantau SSE, ukur latensi.

Memakai `curl` (sama seperti dokumentasi Sinric) dan pustaka standar Python.
Env: SINRIC_API_KEY, SINRIC_DEVICE_A_ID, SINRIC_DEVICE_B_ID
Opsional: SINRIC_API_BASE (default https://api.sinric.pro), SINRIC_SSE_URL (default https://sse.sinric.pro/sse/stream)

  sinric_probe.py devices
  sinric_probe.py watch
  sinric_probe.py set A on
  sinric_probe.py latency A -n 10 --gap 6
  sinric_probe.py burst A on,off,on,off,on --gap 0.2
  sinric_probe.py offline A
"""
import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

API_BASE = os.environ.get("SINRIC_API_BASE", "https://api.sinric.pro").rstrip("/")
SSE_URL = os.environ.get("SINRIC_SSE_URL", "https://sse.sinric.pro/sse/stream")
RESULTS = Path(__file__).resolve().parent / "results" / "latency.csv"


def api_key() -> str:
    k = os.environ.get("SINRIC_API_KEY", "")
    if not k:
        sys.exit("SINRIC_API_KEY belum diisi. Jalankan: set -a; source ../backend/.env; set +a")
    return k


def device_id(name: str) -> str:
    if name.upper() in ("A", "B"):
        v = os.environ.get(f"SINRIC_DEVICE_{name.upper()}_ID", "")
        if not v:
            sys.exit(f"SINRIC_DEVICE_{name.upper()}_ID belum diisi")
        return v
    return name


def curl_flags(url: str) -> list:
    return ["--http2"] if url.startswith("https://") else []


class Sse:
    """Pembaca SSE: curl -N di proses terpisah, tiap event dicatat dengan waktu monotonic."""

    def __init__(self):
        self.events = []          # (t_monotonic, t_wall, dict)
        self.lock = threading.Lock()
        cmd = ["curl", "-sN", *curl_flags(SSE_URL), "-H", "Accept:text/event-stream",
               "-H", f"X-SINRIC-API-KEY: {api_key()}", SSE_URL]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()
        time.sleep(1.5)
        if self.proc.poll() is not None:
            sys.exit(f"curl SSE berhenti: {self.proc.stderr.read().strip() or 'tanpa pesan'}")

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            with self.lock:
                self.events.append((time.monotonic(), time.time(), ev))

    def mark(self) -> int:
        with self.lock:
            return len(self.events)

    def since(self, idx: int) -> list:
        with self.lock:
            return list(self.events[idx:])

    def wait_for(self, idx: int, pred, timeout: float):
        end = time.monotonic() + timeout
        seen = idx
        while time.monotonic() < end:
            new = self.since(seen)
            for t, w, ev in new:
                if pred(ev):
                    return t, ev
            seen += len(new)
            time.sleep(0.01)
        return None, None

    def close(self):
        self.proc.terminate()


def post_action(dev: str, on: bool) -> dict:
    body = json.dumps({"type": "request", "action": "setPowerState",
                       "value": json.dumps({"state": "On" if on else "Off"})})
    cmd = ["curl", "-s", "-m", "15", "-w", "\n%{http_code} %{time_total}", "-X", "POST",
           f"{API_BASE}/api/v1/devices/{dev}/action", "-H", f"X-SINRIC-API-KEY: {api_key()}",
           "-H", "Content-Type: application/json", "--data", body]
    t0 = time.monotonic()
    out = subprocess.run(cmd, capture_output=True, text=True).stdout.rsplit("\n", 1)
    code, total = (out[1].split() + ["0", "0"])[:2] if len(out) == 2 else ("0", "0")
    return {"t0": t0, "http": int(code), "post_s": float(total), "body": out[0][:200]}


def is_state(dev: str, on: bool):
    want = "On" if on else "Off"
    return lambda ev: (ev.get("event") == "deviceMessageArrived" and ev.get("device", {}).get("id") == dev
                       and ev.get("device", {}).get("powerState") == want)


def send_and_time(sse: Sse, dev: str, on: bool, timeout: float = 15.0) -> dict:
    idx = sse.mark()
    r = post_action(dev, on)
    t, ev = sse.wait_for(idx, is_state(dev, on), timeout)
    r["latency_s"] = None if t is None else t - r["t0"]
    r["events"] = [(round(t2 - r["t0"], 3), e.get("event"), e.get("device", {}).get("powerState")) for t2, _, e in sse.since(idx)]
    return r


def save_row(dev: str, target: str, r: dict) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    new = not RESULTS.exists()
    with RESULTS.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["waktu", "device_id", "target", "http", "post_s", "latency_s"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), dev, target, r["http"],
                    r["post_s"], "" if r["latency_s"] is None else round(r["latency_s"], 3)])


# ---------------------------------------------------------------- perintah

def cmd_devices(a):
    out = subprocess.run(["curl", "-s", "-H", f"X-SINRIC-API-KEY: {api_key()}", f"{API_BASE}/api/v1/devices"],
                         capture_output=True, text=True).stdout
    try:
        for d in json.loads(out).get("devices", []):
            print(f"{d.get('id')}  {d.get('name')}")
    except ValueError:
        print(out[:300])


def cmd_watch(a):
    sse = Sse()
    t0 = time.monotonic()
    print("Memantau SSE (Ctrl+C untuk berhenti)")
    shown = 0
    try:
        while True:
            for t, w, ev in sse.since(shown):
                print(f"{datetime.fromtimestamp(w).strftime('%H:%M:%S.%f')[:-3]} (+{t - t0:7.2f}s) {json.dumps(ev)}")
                shown += 1
            time.sleep(0.05)
    except KeyboardInterrupt:
        sse.close()


def cmd_set(a):
    dev, sse = device_id(a.device), Sse()
    r = send_and_time(sse, dev, a.state == "on")
    print(f"HTTP {r['http']} dalam {r['post_s']:.3f}s, isi: {r['body']}")
    print("Latensi sampai event SSE cocok: " + ("TIDAK ADA (timeout)" if r["latency_s"] is None else f"{r['latency_s']:.3f}s"))
    print("Event:", r["events"])
    save_row(dev, a.state, r)
    sse.close()


def cmd_latency(a):
    dev, sse = device_id(a.device), Sse()
    lat, fails, on = [], 0, True
    for i in range(a.n):
        r = send_and_time(sse, dev, on)
        save_row(dev, "On" if on else "Off", r)
        if r["latency_s"] is None:
            fails += 1
            print(f"{i + 1:2d} {'ON ' if on else 'OFF'} timeout (HTTP {r['http']})")
        else:
            lat.append(r["latency_s"])
            print(f"{i + 1:2d} {'ON ' if on else 'OFF'} {r['latency_s']:.3f}s (HTTP {r['http']}, post {r['post_s']:.3f}s)")
        on = not on
        time.sleep(a.gap)
    if lat:
        s = sorted(lat)
        print(f"\nn={len(lat)} gagal={fails} min={s[0]:.3f} median={statistics.median(s):.3f} "
              f"p95={s[min(len(s) - 1, int(0.95 * len(s)))]:.3f} maks={s[-1]:.3f} detik")
        print("Batas cut-off 10 detik di brief: " + ("aman" if s[-1] < 10 else "MELEWATI, perlu disesuaikan"))
    sse.close()


def cmd_burst(a):
    dev, sse = device_id(a.device), Sse()
    seq = [x.strip().lower() == "on" for x in a.sequence.split(",")]
    idx, t_start = sse.mark(), time.monotonic()
    for on in seq:
        r = post_action(dev, on)
        print(f"+{time.monotonic() - t_start:6.2f}s kirim {'ON ' if on else 'OFF'} -> HTTP {r['http']}")
        time.sleep(a.gap)
    time.sleep(a.settle)
    evs = [(round(t - t_start, 2), e.get("event"), e.get("device", {}).get("powerState"))
           for t, _, e in sse.since(idx) if e.get("device", {}).get("id") == dev]
    for e in evs:
        print("event", e)
    states = [e[2] for e in evs if e[1] == "deviceMessageArrived" and e[2]]
    want = ["On" if x else "Off" for x in seq]
    print(f"\nDikirim {len(seq)}, event state diterima {len(states)}, urutan sama: {states == want}, "
          f"state akhir {states[-1] if states else '?'} (harapan {want[-1]})")
    print("Cocokkan juga dengan log serial ESP32 dan kondisi LED.")
    sse.close()


def cmd_offline(a):
    dev, sse = device_id(a.device), Sse()
    input("Matikan ESP32 (cabut USB), lalu tekan Enter... ")
    idx = sse.mark()
    t, ev = sse.wait_for(idx, lambda e: e.get("event") == "deviceDisconnected" and e.get("device", {}).get("id") == dev, 40)
    print("deviceDisconnected terlihat" if t else "deviceDisconnected TIDAK terlihat dalam 40 detik (catat ini)")
    r = post_action(dev, True)
    print(f"Kirim ON saat offline: HTTP {r['http']}, isi: {r['body']}")
    print(f"Menunggu {a.wait} detik tanpa ESP32...")
    time.sleep(a.wait)
    print("Event selama offline:", [(round(t2 - r['t0'], 1), e.get('event'), e.get('device', {}).get('powerState')) for t2, _, e in sse.since(idx)])
    input("Nyalakan ESP32 (colok USB, buka serial monitor), lalu tekan Enter... ")
    t1, idx2 = time.monotonic(), sse.mark()
    time.sleep(a.reconnect_wait)
    for t2, _, e in sse.since(idx2):
        print(f"+{t2 - t1:6.1f}s setelah dinyalakan: {e.get('event')} {e.get('device', {}).get('powerState', '')}")
    print("\nCatat: apakah perintah ON lama dieksekusi saat ESP32 tersambung kembali? (lihat LED dan serial)")
    sse.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices").set_defaults(fn=cmd_devices)
    sub.add_parser("watch").set_defaults(fn=cmd_watch)
    p = sub.add_parser("set"); p.add_argument("device"); p.add_argument("state", choices=["on", "off"]); p.set_defaults(fn=cmd_set)
    p = sub.add_parser("latency"); p.add_argument("device"); p.add_argument("-n", type=int, default=10)
    p.add_argument("--gap", type=float, default=6.0); p.set_defaults(fn=cmd_latency)
    p = sub.add_parser("burst"); p.add_argument("device"); p.add_argument("sequence", help="contoh: on,off,on,off")
    p.add_argument("--gap", type=float, default=0.2); p.add_argument("--settle", type=float, default=8.0); p.set_defaults(fn=cmd_burst)
    p = sub.add_parser("offline"); p.add_argument("device"); p.add_argument("--wait", type=float, default=20.0)
    p.add_argument("--reconnect-wait", type=float, default=25.0); p.set_defaults(fn=cmd_offline)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
