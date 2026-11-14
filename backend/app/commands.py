"""Pelacakan perintah cut-off per node.

requested -> sent -> acknowledged -> confirmed | failed | timeout     (+ cancelled bila lockout dilepas)

requested     baris dibuat saat saldo nol (service.process_telemetry)
sent          OFF sudah dikirim: REST Sinric diterima (HTTP 200, success=true) atau balasan
              telemetri node itu membawa lockout=true
acknowledged  node mengirim telemetri BARU setelah sent (node hidup dan terjangkau)
confirmed     telemetri baru itu memuat relay_on=false
failed        semua percobaan REST Sinric gagal dan tidak ada jalur lain yang berhasil
timeout       belum confirmed dalam SELA_CMD_TIMEOUT_S (default 10 detik sejak requested)

Percobaan: 1 kali awal + SELA_CMD_RETRIES (default 2) ulangan, jarak sama rata dalam jendela 10 detik.
"""
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from .db import connect, tx

OPEN = "('requested','sent','acknowledged')"
Dispatcher = Callable[[str], Tuple[bool, str]]


def timeout_s() -> float:
    return float(os.environ.get("SELA_CMD_TIMEOUT_S", "10"))


def max_retries() -> int:
    return int(os.environ.get("SELA_CMD_RETRIES", "2"))


def retry_interval_s() -> float:
    return timeout_s() / (max_retries() + 1)


def _alert(conn: sqlite3.Connection, now_s: float, kind: str, message: str) -> None:
    conn.execute("INSERT INTO alerts (ts, kind, message) VALUES (?, ?, ?)", (now_s, kind, message))


def _open_rows(conn: sqlite3.Connection, device_id: Optional[str] = None) -> List[sqlite3.Row]:
    sql = f"SELECT * FROM commands WHERE status IN {OPEN}"
    args: tuple = ()
    if device_id is not None:
        sql += " AND device_id = ?"
        args = (device_id,)
    return conn.execute(sql + " ORDER BY id", args).fetchall()


def expire_overdue(conn: sqlite3.Connection, now_s: float) -> None:
    for r in _open_rows(conn):
        if now_s - r["created_at"] > timeout_s():
            conn.execute("UPDATE commands SET status='timeout', updated_at=? WHERE id=?", (now_s, r["id"]))
            _alert(conn, now_s, "cutoff_timeout",
                   f"Cut-off {r['device_id']} belum terkonfirmasi dalam {timeout_s():g} detik")


def cancel_open(conn: sqlite3.Connection, now_s: float) -> None:
    """Dipanggil saat lockout dilepas (top-up atau reset): perintah OFF lama tidak boleh dikirim lagi."""
    conn.execute(f"UPDATE commands SET status='cancelled', updated_at=? WHERE status IN {OPEN}", (now_s,))


def _set_sent(conn: sqlite3.Connection, r: sqlite3.Row, now_s: float, channel: str) -> None:
    # Simpan (boot_id, seq) terakhir: hanya paket yang lebih baru dari ini yang boleh meng-acknowledge.
    last = conn.execute("SELECT boot_id, seq FROM device_last WHERE device_id = ?", (r["device_id"],)).fetchone()
    conn.execute(
        "UPDATE commands SET status='sent', channel=?, sent_at=?, ref_boot=?, ref_seq=?, updated_at=? WHERE id=?",
        (channel, now_s, last["boot_id"] if last else None, last["seq"] if last else None, now_s, r["id"]),
    )


def _is_newer(r: sqlite3.Row, boot_id: str, seq: int) -> bool:
    if r["ref_boot"] is None or boot_id != r["ref_boot"]:
        return True
    return seq > r["ref_seq"]


def on_telemetry(conn: sqlite3.Connection, device_id: str, boot_id: str, seq: int,
                 relay_on: bool, lockout: bool, now_s: float) -> None:
    """Dipanggil di akhir process_telemetry (di dalam transaksi), setelah saldo dan lockout diperbarui."""
    expire_overdue(conn, now_s)

    for r in _open_rows(conn, device_id):
        if r["status"] in ("sent", "acknowledged") and _is_newer(r, boot_id, seq):
            if r["status"] == "sent":
                conn.execute("UPDATE commands SET status='acknowledged', acked_at=?, updated_at=? WHERE id=?",
                             (now_s, now_s, r["id"]))
            if not relay_on:
                conn.execute("UPDATE commands SET status='confirmed', confirmed_at=?, updated_at=? WHERE id=?",
                             (now_s, now_s, r["id"]))

    if lockout:   # balasan untuk paket ini membawa lockout=true
        for r in _open_rows(conn, device_id):
            if r["status"] == "requested":
                _set_sent(conn, r, now_s, "reply")


def plan_tick(conn: sqlite3.Connection, now_s: float, dispatch_enabled: bool) -> List[Tuple[int, str]]:
    """Putuskan perintah mana yang dikirim ulang sekarang. Dipanggil di dalam transaksi, tanpa I/O jaringan."""
    acc = conn.execute("SELECT lockout FROM account WHERE id = 1").fetchone()
    if acc is None or not acc["lockout"]:
        cancel_open(conn, now_s)
        return []

    expire_overdue(conn, now_s)
    if not dispatch_enabled:
        return []

    attempts_max = max_retries() + 1
    todo: List[Tuple[int, str]] = []
    for r in _open_rows(conn):
        if r["status"] == "requested" and r["attempts"] >= attempts_max:
            conn.execute("UPDATE commands SET status='failed', updated_at=? WHERE id=?", (now_s, r["id"]))
            _alert(conn, now_s, "cutoff_gagal",
                   f"Cut-off {r['device_id']} gagal dikirim ({attempts_max} percobaan): {r['last_error'] or '-'}")
            continue
        due = r["attempts"] == 0 or (
            r["attempts"] < attempts_max and now_s - r["last_attempt_at"] >= retry_interval_s() - 1e-6)
        if due:
            conn.execute("UPDATE commands SET attempts=attempts+1, last_attempt_at=?, updated_at=? WHERE id=?",
                         (now_s, now_s, r["id"]))
            todo.append((r["id"], r["device_id"]))
    return todo


def record_dispatch(conn: sqlite3.Connection, cmd_id: int, ok: bool, detail: str, now_s: float) -> None:
    r = conn.execute(f"SELECT * FROM commands WHERE id = ? AND status IN {OPEN}", (cmd_id,)).fetchone()
    if r is None:     # sudah confirmed, timeout, atau dibatalkan selagi HTTP berjalan
        return
    if ok:
        if r["status"] == "requested":
            _set_sent(conn, r, now_s, "sinric")
        conn.execute("UPDATE commands SET last_error=NULL, updated_at=? WHERE id=?", (now_s, cmd_id))
    else:
        conn.execute("UPDATE commands SET last_error=?, updated_at=? WHERE id=?", (detail[:200], now_s, cmd_id))


def run_tick(db_file: str, dispatcher: Optional[Dispatcher], now_s: float,
             now_fn: Callable[[], float] = time.time) -> int:
    """Satu putaran: rencanakan (transaksi singkat), kirim HTTP di luar transaksi, catat hasil."""
    conn = connect(db_file)
    try:
        with tx(conn):
            todo = plan_tick(conn, now_s, dispatcher is not None)
        if not todo or dispatcher is None:
            return 0

        def call(item: Tuple[int, str]) -> Tuple[int, bool, str]:
            cmd_id, dev = item
            try:
                ok, detail = dispatcher(dev)
            except Exception as e:  # noqa: BLE001  satu node gagal tidak boleh menghentikan yang lain
                ok, detail = False, type(e).__name__
            return cmd_id, ok, detail

        with ThreadPoolExecutor(max_workers=len(todo)) as ex:
            results = list(ex.map(call, todo))
        with tx(conn):
            for cmd_id, ok, detail in results:
                record_dispatch(conn, cmd_id, ok, detail, now_fn())
        return len(todo)
    finally:
        conn.close()


def latest_per_device(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    rows = conn.execute(
        "SELECT c.* FROM commands c JOIN (SELECT device_id, MAX(id) AS mid FROM commands GROUP BY device_id) m "
        "ON c.id = m.mid"
    ).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        lat = None if r["confirmed_at"] is None else round(r["confirmed_at"] - r["created_at"], 3)
        out[r["device_id"]] = {
            "id": r["id"], "status": r["status"], "attempts": r["attempts"], "channel": r["channel"],
            "created_at": r["created_at"], "updated_at": r["updated_at"], "latency_s": lat,
            "last_error": r["last_error"],
        }
    return out