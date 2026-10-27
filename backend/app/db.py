import os
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    boot_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts REAL NOT NULL,
    source TEXT NOT NULL,
    relay_on INTEGER NOT NULL,
    sensor_ok INTEGER NOT NULL,
    voltage REAL, current REAL, power REAL, pf REAL, freq REAL, energy_wh REAL,
    received_at REAL NOT NULL,
    UNIQUE (device_id, boot_id, seq)
);

CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    boot_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts REAL NOT NULL,
    power_w REAL,
    delta_wh REAL NOT NULL,
    gap INTEGER NOT NULL,
    reason TEXT NOT NULL,
    UNIQUE (device_id, boot_id, seq)
);

CREATE TABLE IF NOT EXISTS device_last (
    device_id TEXT PRIMARY KEY,
    boot_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts REAL NOT NULL,
    power_w REAL,
    relay_on INTEGER NOT NULL,
    sensor_ok INTEGER NOT NULL,
    received_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    balance_wh REAL NOT NULL,
    reference_wh REAL NOT NULL,
    overshoot_wh REAL NOT NULL DEFAULT 0,
    lockout INTEGER NOT NULL DEFAULT 0,
    low_alerted INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS topups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount_wh REAL NOT NULL,
    ts REAL NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def db_path() -> str:
    return os.environ.get("SELA_DB_PATH", "./data/sela.db")


def connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def init_db(path: str, initial_balance_wh: float, now_s: float) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT 1 FROM account WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO account (id, balance_wh, reference_wh, updated_at) VALUES (1, ?, ?, ?)",
                (initial_balance_wh, initial_balance_wh, now_s),
            )
    finally:
        conn.close()
