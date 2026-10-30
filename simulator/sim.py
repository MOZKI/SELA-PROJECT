#!/usr/bin/env python3
"""Simulator dua node SELA (Node A 10 W, Node B 20 W).

Meniru ESP32 sungguhan: kirim telemetri tiap 5 detik waktu simulasi, baca `lockout` dari
balasan backend, dan tolak ON saat lockout. Semua paket berlabel `synthetic`.

Percepatan: waktu simulasi (`ts` di paket) tetap maju 5 detik per paket, jadi aturan celah
15 detik di ledger tidak terganggu. Yang dipercepat hanya jeda dunia nyata antar paket
(5 / factor detik). Dengan --factor 10, saldo 5 Wh pada 30 W habis dalam sekitar 60 detik nyata.

Skenario:
  drain    30 W dari saldo 5 Wh sampai lockout, cek waktunya sekitar 10 menit simulasi
  demo     alur demo penuh: nyala bertahap, peringatan rendah, lockout, ON ditolak, top-up
  dataset  hari dipercepat berpola (4 kondisi beban), tanpa backend, hanya tulis CSV untuk ML

Hanya memakai pustaka standar Python.
"""
import argparse
import csv
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TICK_S = 5.0                      # selang antar paket dalam waktu simulasi
NODES = {"A": ("sela-node-a", 10.0), "B": ("sela-node-b", 20.0)}
B_OFFSET_S = 1.7                  # Node B tidak serentak dengan Node A, seperti perangkat asli
CSV_COLUMNS = [
    "session_id", "scenario", "source", "device_id", "boot_id", "seq", "ts", "iso_utc",
    "sim_day", "sim_hour", "day_type", "relay_on", "sensor_ok",
    "voltage", "current", "power", "pf", "freq", "energy_wh", "balance_wh", "lockout",
]


# ---------------------------------------------------------------- klien HTTP

class Api:
    def __init__(self, base_url: str, api_key: str):
        self.base = base_url.rstrip("/")
        self.key = api_key

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-API-Key": self.key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def telemetry(self, payload: dict) -> dict:
        return self._call("POST", "/telemetry", payload)

    def state(self) -> dict:
        return self._call("GET", "/state")

    def topup(self, amount_wh: float, note: str) -> dict:
        return self._call("POST", "/topup", {"amount_wh": amount_wh, "note": note})

    def reset(self, balance_wh: float) -> dict:
        return self._call("POST", "/admin/reset", {"balance_wh": balance_wh})

    def alerts(self, since: float) -> List[dict]:
        return self._call("GET", f"/alerts?since={since}&limit=200")["alerts"]


# ---------------------------------------------------------------- node tiruan

class SimNode:
    """Satu ESP32 + PZEM + relay. Relay OFF saat boot, lockout dipegang dari balasan backend."""

    def __init__(self, key: str, boot_id: str, rng: random.Random, offset_s: float):
        self.device_id, self.nominal_w = NODES[key]
        self.boot_id = boot_id
        self.rng = rng
        self.offset_s = offset_s
        self.relay_on = False
        self.lockout = False
        self.seq = 0
        self.energy_wh = 0.0
        self._last_ts: Optional[float] = None
        self._last_p = 0.0

    def request_on(self) -> bool:
        """Penegakan lokal: ON ditolak saat lockout aktif."""
        if self.lockout:
            return False
        self.relay_on = True
        return True

    def request_off(self) -> None:
        self.relay_on = False

    def sample(self, ts: float, source: str = "synthetic") -> Dict[str, Any]:
        r = self.rng
        v = round(220.0 + r.gauss(0, 1.2), 1)
        freq = round(50.0 + r.gauss(0, 0.03), 2)
        if self.relay_on:
            p = max(0.0, round(self.nominal_w * (1 + r.gauss(0, 0.012)), 1))
            pf = round(min(1.0, max(0.9, 0.98 + r.gauss(0, 0.01))), 2)
            i = round(p / (v * pf), 3)
        else:
            p = 0.1 if r.random() < 0.02 else 0.0     # sesekali derau kecil saat OFF (uji deadband)
            pf = 0.0
            i = 0.0
        if self._last_ts is not None:                  # register energi seperti PZEM
            self.energy_wh += (self._last_p + p) / 2 * (ts - self._last_ts) / 3600
        self._last_ts, self._last_p = ts, p
        pkt = {"device_id": self.device_id, "boot_id": self.boot_id, "seq": self.seq, "ts": round(ts, 3),
               "source": source, "relay_on": self.relay_on, "sensor_ok": True, "voltage": v,
               "current": i, "power": p, "pf": pf, "freq": freq, "energy_wh": round(self.energy_wh, 5)}
        self.seq += 1
        return pkt

    def apply_ack(self, ack: dict) -> None:
        self.lockout = bool(ack["lockout"])
        if self.lockout:
            self.relay_on = False                      # cut-off lokal; jalur Sinric menyusul Jumat


# ---------------------------------------------------------------- skenario

def scenario_drain(initial_wh: float) -> List[tuple]:
    return [
        ("reset", initial_wh), ("wait", 15), ("mark", "start30"),
        ("on", "A"), ("on", "B"),
        ("until_lockout", 1200), ("wait", 10),
        ("check_drain", "start30", initial_wh),
    ]


def scenario_demo(initial_wh: float, topup_wh: float) -> List[tuple]:
    return [
        ("reset", initial_wh), ("wait", 15),
        ("check_balance", initial_wh, "saldo awal sesuai kuota"),
        ("on", "A"), ("wait", 60),                      # 10 W
        ("on", "B"), ("wait", 120),                     # 30 W
        ("off", "A"), ("wait", 60),                     # 20 W
        ("on", "A"),                                    # 30 W lagi sampai habis
        ("until_lockout", 1200), ("wait", 10),
        ("check_cutoff",), ("check_alerts_order",),
        ("wait", 20),
        ("try_on", "A"), ("try_on", "B"),               # harus ditolak
        ("wait", 20),
        ("topup", topup_wh),
        ("check_topup", topup_wh),
        ("wait", 20), ("check_lamps_off",),             # lampu tetap OFF setelah top-up
        ("on", "A"), ("wait", 60), ("check_consuming", topup_wh),
        ("off", "A"),
    ]


# Peluang node ON per jam dalam hari dipercepat: (jam mulai, jam akhir, pA, pB) hari kerja / akhir pekan
PROFILE = {
    False: [(0, 6, 0.03, 0.02), (6, 9, 0.70, 0.20), (9, 17, 0.15, 0.10), (17, 23, 0.80, 0.65), (23, 24, 0.20, 0.10)],
    True:  [(0, 7, 0.03, 0.02), (7, 10, 0.50, 0.30), (10, 17, 0.45, 0.30), (17, 23, 0.85, 0.75), (23, 24, 0.25, 0.15)],
}


def profile_probs(hour: int, weekend: bool) -> Tuple[float, float]:
    for lo, hi, pa, pb in PROFILE[weekend]:
        if lo <= hour < hi:
            return pa, pb
    return 0.0, 0.0


# ---------------------------------------------------------------- pelaksana

class Runner:
    def __init__(self, args, api: Optional[Api], steps: List[tuple], rng: random.Random):
        self.args, self.api, self.steps, self.rng = args, api, steps, rng
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        boot = f"sim-{self.session_id}"
        self.nodes = {k: SimNode(k, boot, rng, 0.0 if k == "A" else B_OFFSET_S) for k in NODES}
        self.sim_t = 0.0
        self.start_ts = time.time()
        self.idx = 0
        self.wait_left: Optional[float] = None
        self.marks: Dict[str, float] = {}
        self.t_lockout: Optional[float] = None
        self.t_server0 = 0.0
        self.rows: List[dict] = []
        self.results: List[Tuple[str, bool, str]] = []
        self.post_failures = 0

    # --- util
    def log(self, msg: str) -> None:
        print(f"[sim {self.sim_t:7.0f}s] {msg}", flush=True)

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.results.append((name, ok, detail))
        self.log(f"{'LULUS' if ok else 'GAGAL'}  {name}: {detail}")

    def any_lockout(self) -> bool:
        return any(n.lockout for n in self.nodes.values())

    # --- langkah skenario
    def advance(self) -> bool:
        """Jalankan langkah yang sudah waktunya. Return False bila skenario selesai."""
        while self.idx < len(self.steps):
            kind, *a = self.steps[self.idx]
            if kind == "wait":
                if self.wait_left is None:
                    self.wait_left = a[0]
                if self.wait_left > 0:
                    return True
                self.wait_left = None
            elif kind == "until_lockout":
                if self.wait_left is None:
                    self.wait_left = a[0]
                if self.any_lockout():
                    self.t_lockout = self.sim_t
                    self.log("lockout aktif")
                elif self.wait_left > 0:
                    return True
                else:
                    self.check("lockout tercapai", False, f"tidak ada lockout dalam {a[0]} detik simulasi")
                    return False
                self.wait_left = None
            else:
                self.run_instant(kind, a)
            self.idx += 1
        return False

    def run_instant(self, kind: str, a: list) -> None:
        api = self.api
        if kind == "reset":
            st = api.reset(a[0])
            self.t_server0 = st["server_ts"]
            self.log(f"reset saldo {a[0]} Wh")
        elif kind == "mark":
            self.marks[a[0]] = self.sim_t
        elif kind in ("on", "try_on"):
            node = self.nodes[a[0]]
            allowed = node.request_on()
            if kind == "on":
                self.log(f"Node {a[0]} ON {'diizinkan' if allowed else 'DITOLAK (lockout)'}")
            else:
                self.check(f"ON ditolak saat lockout (Node {a[0]})", not allowed,
                           "ditolak" if not allowed else "relay malah menyala")
        elif kind == "off":
            self.nodes[a[0]].request_off()
            self.log(f"Node {a[0]} OFF")
        elif kind == "check_balance":
            st = api.state()
            self.check(a[1], abs(st["balance_wh"] - a[0]) < 0.01, f"saldo {st['balance_wh']} Wh")
        elif kind == "check_drain":
            took = self.t_lockout - self.marks[a[0]]
            expected = a[1] / 30 * 3600
            self.check("30 W habis sekitar 10 menit", abs(took - expected) <= 0.05 * expected,
                       f"{took:.0f} detik simulasi (acuan {expected:.0f}, toleransi 5 persen)")
            st = api.state()
            both_off = all(not d["relay_on"] for d in st["devices"] if d["relay_on"] is not None)
            self.check("saldo nol dan lockout di backend", st["lockout"] and st["balance_wh"] == 0,
                       f"saldo {st['balance_wh']}, lockout {st['lockout']}")
            self.check("kedua node OFF setelah lockout", both_off, str([(d["device_id"], d["relay_on"]) for d in st["devices"]]))
        elif kind == "check_cutoff":
            st = api.state()
            off = {d["device_id"]: d["relay_on"] for d in st["devices"]}
            self.check("lockout dan kedua node OFF", st["lockout"] and not any(off.values()),
                       f"saldo {st['balance_wh']}, relay {off}")
        elif kind == "check_alerts_order":
            al = sorted(api.alerts(self.t_server0), key=lambda x: x["id"])
            kinds = [x["kind"] for x in al]
            ok = "saldo_rendah" in kinds and "saldo_habis" in kinds and kinds.index("saldo_rendah") < kinds.index("saldo_habis")
            self.check("peringatan saldo rendah sebelum saldo habis", ok, f"urutan {kinds}")
        elif kind == "topup":
            self.before_topup = api.state()["balance_wh"]
            st = api.topup(a[0], "simulator")
            self.log(f"top-up {a[0]} Wh, saldo jadi {st['balance_wh']}")
        elif kind == "check_topup":
            st = api.state()
            ok = abs(st["balance_wh"] - (self.before_topup + a[0])) < 0.01 and not st["lockout"]
            self.check("top-up menaikkan saldo dan melepas lockout", ok,
                       f"{self.before_topup} naik ke {st['balance_wh']} Wh, lockout {st['lockout']}")
        elif kind == "check_lamps_off":
            st = api.state()
            sim_off = not any(n.relay_on for n in self.nodes.values())
            be_off = not any(d["relay_on"] for d in st["devices"])
            self.check("kedua lampu tetap OFF setelah top-up", sim_off and be_off,
                       f"simulator {sim_off}, backend {be_off}, lockout node {[n.lockout for n in self.nodes.values()]}")
        elif kind == "check_consuming":
            st = api.state()
            self.check("saldo turun lagi setelah ON baru", 0 < st["balance_wh"] < a[0] - 0.05,
                       f"saldo {st['balance_wh']} Wh")
        else:
            raise ValueError(f"langkah tidak dikenal: {kind}")

    # --- satu tick
    def post(self, node: SimNode, pkt: dict) -> None:
        if self.api is None:
            return
        try:
            ack = self.api.telemetry(pkt)
            node.apply_ack(ack)
            pkt["_ack"] = ack
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self.post_failures += 1
            self.log(f"kirim gagal {pkt['device_id']} seq {pkt['seq']}: {e}")

    def record(self, pkt: dict, scenario: str) -> None:
        day_s = self.args.day_minutes * 60.0
        day_idx = int(self.sim_t // day_s)
        hour = (self.sim_t % day_s) / day_s * 24.0
        ack = pkt.pop("_ack", None)
        self.rows.append({
            "session_id": self.session_id, "scenario": scenario, "source": pkt["source"],
            "device_id": pkt["device_id"], "boot_id": pkt["boot_id"], "seq": pkt["seq"], "ts": pkt["ts"],
            "iso_utc": datetime.fromtimestamp(pkt["ts"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sim_day": day_idx, "sim_hour": round(hour, 3),
            "day_type": "weekend" if day_idx % 7 >= 5 else "weekday",
            "relay_on": int(pkt["relay_on"]), "sensor_ok": int(pkt["sensor_ok"]),
            "voltage": pkt["voltage"], "current": pkt["current"], "power": pkt["power"], "pf": pkt["pf"],
            "freq": pkt["freq"], "energy_wh": pkt["energy_wh"],
            "balance_wh": "" if ack is None else ack["balance_wh"], "lockout": "" if ack is None else int(ack["lockout"]),
        })

    def tick(self, scenario: str) -> None:
        for key in ("A", "B"):
            node = self.nodes[key]
            pkt = node.sample(self.start_ts + self.sim_t + node.offset_s)
            self.post(node, pkt)
            self.record(pkt, scenario)

    # --- skenario dataset (tanpa backend)
    def dataset_apply_profile(self, state: Dict[str, bool]) -> None:
        day_s = self.args.day_minutes * 60.0
        day_idx = int(self.sim_t // day_s)
        hour = int((self.sim_t % day_s) / day_s * 24.0)
        pa, pb = profile_probs(hour, day_idx % 7 >= 5)
        for key, p in (("A", pa), ("B", pb)):
            if self.rng.random() < 0.5:                    # kondisi bertahan sebagian, tidak berkedip tiap jam
                continue
            state[key] = self.rng.random() < p
            if state[key]:
                self.nodes[key].request_on()
            else:
                self.nodes[key].request_off()

    def run_dataset(self) -> None:
        total = self.args.days * self.args.day_minutes * 60.0
        hour_s = self.args.day_minutes * 60.0 / 24.0
        state = {"A": False, "B": False}
        next_block = 0.0
        while self.sim_t < total:
            if self.sim_t >= next_block:
                self.dataset_apply_profile(state)
                next_block += hour_s
            self.tick("dataset")
            self.sim_t += TICK_S

    def run_scripted(self, scenario: str) -> None:
        wall0, n = time.monotonic(), 0
        factor = self.args.factor
        while self.advance():
            self.tick(scenario)
            if self.wait_left is not None:
                self.wait_left -= TICK_S
            self.sim_t += TICK_S
            n += 1
            if factor > 0:
                delay = wall0 + n * TICK_S / factor - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
        if self.post_failures:
            self.check("semua paket terkirim", False, f"{self.post_failures} paket gagal")


# ---------------------------------------------------------------- main

def write_csv(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Simulator dua node SELA")
    ap.add_argument("scenario", choices=["drain", "demo", "dataset"])
    ap.add_argument("--url", default=os.environ.get("SELA_URL", "http://localhost:8000"))
    ap.add_argument("--api-key", default=os.environ.get("SELA_API_KEY", ""), help="default: env SELA_API_KEY")
    ap.add_argument("--factor", type=float, default=None,
                    help="percepatan waktu dunia nyata (default 10 untuk drain/demo, tanpa jeda untuk dataset)")
    ap.add_argument("--balance", type=float, default=5.0, help="saldo awal Wh (drain/demo)")
    ap.add_argument("--topup", type=float, default=3.0, help="jumlah top-up Wh (demo)")
    ap.add_argument("--days", type=int, default=14, help="jumlah hari dipercepat (dataset)")
    ap.add_argument("--day-minutes", type=float, default=30.0, help="panjang satu hari dipercepat, menit simulasi")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--csv", default=None, help="path CSV keluaran (default ml/data/synthetic_<skenario>_<waktu>.csv)")
    ap.add_argument("--no-csv", action="store_true")
    args = ap.parse_args(argv)

    if args.factor is None:
        args.factor = 0.0 if args.scenario == "dataset" else 10.0
    rng = random.Random(args.seed)

    api = None
    if args.scenario != "dataset":
        if not args.api_key:
            print("SELA_API_KEY belum diisi (export atau --api-key).", file=sys.stderr)
            return 2
        api = Api(args.url, args.api_key)
        try:
            urllib.request.urlopen(args.url.rstrip("/") + "/health", timeout=5).read()
        except OSError as e:
            print(f"Backend tidak terjangkau di {args.url}: {e}", file=sys.stderr)
            return 2

    steps = {"drain": lambda: scenario_drain(args.balance),
             "demo": lambda: scenario_demo(args.balance, args.topup),
             "dataset": lambda: []}[args.scenario]()
    run = Runner(args, api, steps, rng)

    t0 = time.monotonic()
    if args.scenario == "dataset":
        run.run_dataset()
    else:
        run.run_scripted(args.scenario)
    wall = time.monotonic() - t0

    if not args.no_csv:
        path = Path(args.csv) if args.csv else (
            Path(__file__).resolve().parent.parent / "ml" / "data" / f"synthetic_{args.scenario}_{run.session_id}.csv")
        write_csv(path, run.rows)
        print(f"CSV: {path} ({len(run.rows)} baris)")

    print(f"\nSelesai: {run.sim_t:.0f} detik simulasi dalam {wall:.1f} detik nyata, {len(run.rows)} paket, source=synthetic")
    if run.results:
        failed = [r for r in run.results if not r[1]]
        print(f"Hasil: {len(run.results) - len(failed)} lulus, {len(failed)} gagal")
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
