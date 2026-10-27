"""Logika bisnis di atas database: telemetri, state, top-up, reset."""
import os
import sqlite3
from typing import Any, Dict, List, Optional

from . import ledger
from .ledger import Sample


def stale_after_s() -> float:
    return float(os.environ.get("SELA_STALE_AFTER_S", "15"))


def expected_devices() -> List[str]:
    raw = os.environ.get("SELA_EXPECTED_DEVICES", "sela-node-a,sela-node-b")
    return [d.strip() for d in raw.split(",") if d.strip()]


def get_account(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM account WHERE id = 1").fetchone()


def _add_alert(conn: sqlite3.Connection, now_s: float, kind: str, message: str) -> None:
    conn.execute("INSERT INTO alerts (ts, kind, message) VALUES (?, ?, ?)", (now_s, kind, message))


def _ack(seq: int, acc: sqlite3.Row, duplicate: bool, now_s: float) -> Dict[str, Any]:
    return {
        "ack_seq": seq,
        "balance_wh": round(acc["balance_wh"], 6),
        "lockout": bool(acc["lockout"]),
        "duplicate": duplicate,
        "server_ts": now_s,
    }


def process_telemetry(conn: sqlite3.Connection, t, now_s: float) -> Dict[str, Any]:
    """Satu paket telemetri. Dipanggil di dalam transaksi."""
    power = t.power if t.sensor_ok else None

    try:
        conn.execute(
            """INSERT INTO telemetry
               (device_id, boot_id, seq, ts, source, relay_on, sensor_ok,
                voltage, current, power, pf, freq, energy_wh, received_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (t.device_id, t.boot_id, t.seq, t.ts, t.source, int(t.relay_on), int(t.sensor_ok),
             t.voltage, t.current, t.power, t.pf, t.freq, t.energy_wh, now_s),
        )
    except sqlite3.IntegrityError:
        # Paket sama (device_id, boot_id, seq): energi tidak dihitung dua kali.
        return _ack(t.seq, get_account(conn), True, now_s)

    last = conn.execute("SELECT * FROM device_last WHERE device_id = ?", (t.device_id,)).fetchone()
    prev: Optional[Sample] = None
    if last is not None and last["boot_id"] == t.boot_id:
        prev = Sample(last["ts"], last["power_w"])

    step = ledger.integrate_step(prev, Sample(t.ts, power))
    conn.execute(
        """INSERT INTO ledger (device_id, boot_id, seq, ts, power_w, delta_wh, gap, reason)
           VALUES (?,?,?,?,?,?,?,?)""",
        (t.device_id, t.boot_id, t.seq, t.ts, power, step.delta_wh, int(step.gap), step.reason),
    )

    # Perbarui sampel terakhir device, kecuali paket ini lebih tua dari yang sudah ada (boot sama).
    if prev is None or t.ts > prev.ts:
        conn.execute(
            """INSERT INTO device_last (device_id, boot_id, seq, ts, power_w, relay_on, sensor_ok, received_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(device_id) DO UPDATE SET
                 boot_id=excluded.boot_id, seq=excluded.seq, ts=excluded.ts, power_w=excluded.power_w,
                 relay_on=excluded.relay_on, sensor_ok=excluded.sensor_ok, received_at=excluded.received_at""",
            (t.device_id, t.boot_id, t.seq, t.ts, power, int(t.relay_on), int(t.sensor_ok), now_s),
        )
    else:
        conn.execute("UPDATE device_last SET received_at = ? WHERE device_id = ?", (now_s, t.device_id))

    acc = get_account(conn)
    balance, overshoot = acc["balance_wh"], acc["overshoot_wh"]
    lockout, low_alerted = acc["lockout"], acc["low_alerted"]

    if step.delta_wh > 0:
        balance, extra = ledger.apply_energy(balance, step.delta_wh)
        overshoot += extra

    if balance <= 0 and not lockout:
        lockout = 1
        _add_alert(conn, now_s, "saldo_habis", "Saldo nol, lockout aktif, kedua node diminta OFF")
        devices = set(expected_devices())
        devices.update(r["device_id"] for r in conn.execute("SELECT device_id FROM device_last"))
        for dev in sorted(devices):
            conn.execute(
                "INSERT INTO commands (device_id, command, status, reason, created_at, updated_at) "
                "VALUES (?, 'OFF', 'requested', 'saldo_habis', ?, ?)",
                (dev, now_s, now_s),
            )
    elif not low_alerted and ledger.is_low(balance, acc["reference_wh"]):
        low_alerted = 1
        _add_alert(conn, now_s, "saldo_rendah", "Saldo tersisa 20 persen atau kurang dari kuota acuan")

    conn.execute(
        "UPDATE account SET balance_wh=?, overshoot_wh=?, lockout=?, low_alerted=?, updated_at=? WHERE id=1",
        (balance, overshoot, lockout, low_alerted, now_s),
    )
    return _ack(t.seq, get_account(conn), False, now_s)


def get_state(conn: sqlite3.Connection, now_s: float) -> Dict[str, Any]:
    acc = get_account(conn)
    rows = {r["device_id"]: r for r in conn.execute("SELECT * FROM device_last")}
    ids = list(expected_devices())
    ids += [d for d in rows if d not in ids]

    devices = []
    total = 0.0
    partial = False
    for dev in ids:
        r = rows.get(dev)
        if r is None:
            devices.append({"device_id": dev, "online": False, "relay_on": None, "sensor_ok": None,
                            "power_w": None, "last_ts": None, "age_s": None})
            partial = True
            continue
        age = now_s - r["received_at"]
        online = ledger.is_online(age, stale_after_s())
        devices.append({"device_id": dev, "online": online, "relay_on": bool(r["relay_on"]),
                        "sensor_ok": bool(r["sensor_ok"]), "power_w": r["power_w"],
                        "last_ts": r["ts"], "age_s": round(age, 3)})
        if online and r["power_w"] is not None:
            total += r["power_w"]
        else:
            partial = True

    return {
        "balance_wh": round(acc["balance_wh"], 6),
        "reference_wh": acc["reference_wh"],
        "overshoot_wh": round(acc["overshoot_wh"], 6),
        "lockout": bool(acc["lockout"]),
        "total_power_w": round(total, 3),
        "partial": partial,
        "devices": devices,
        "server_ts": now_s,
    }


def do_topup(conn: sqlite3.Connection, amount_wh: float, note: Optional[str], now_s: float) -> Dict[str, Any]:
    acc = get_account(conn)
    new_balance = ledger.apply_topup(acc["balance_wh"], amount_wh)
    conn.execute("INSERT INTO topups (amount_wh, ts, note) VALUES (?, ?, ?)", (amount_wh, now_s, note))
    # Lockout lepas, tetapi lampu tidak otomatis ON (tidak ada perintah ON yang dikirim).
    conn.execute(
        "UPDATE account SET balance_wh=?, reference_wh=?, lockout=0, low_alerted=0, updated_at=? WHERE id=1",
        (new_balance, new_balance, now_s),
    )
    _add_alert(conn, now_s, "topup", f"Top-up {amount_wh} Wh, lockout dilepas, lampu tetap OFF")
    return get_state(conn, now_s)


def do_reset(conn: sqlite3.Connection, balance_wh: float, now_s: float) -> Dict[str, Any]:
    """Untuk pengembangan dan demo: kembalikan saldo awal dan bersihkan data sinkron."""
    conn.execute(
        "UPDATE account SET balance_wh=?, reference_wh=?, overshoot_wh=0, lockout=0, low_alerted=0, updated_at=? WHERE id=1",
        (balance_wh, balance_wh, now_s),
    )
    conn.execute("DELETE FROM device_last")
    return get_state(conn, now_s)
