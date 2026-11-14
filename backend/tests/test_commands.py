import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import commands, db

KEY = "kunci-tes"
H = {"X-API-Key": KEY}
A, B = "sela-node-a", "sela-node-b"


class Clock:
    def __init__(self, t=10_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr("app.main.now", c)
    return c


@pytest.fixture
def client(tmp_path, monkeypatch, clock):
    monkeypatch.setenv("SELA_API_KEY", KEY)
    monkeypatch.setenv("SELA_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("SELA_INITIAL_BALANCE_WH", "0.001")      # habis di paket kedua
    from app.main import app
    with TestClient(app) as c:
        c.db = str(tmp_path / "t.db")
        yield c


def pkt(dev=A, seq=0, ts=1000.0, power=10.0, relay=True, boot="b1"):
    return {"device_id": dev, "boot_id": boot, "seq": seq, "ts": ts, "source": "synthetic",
            "relay_on": relay, "sensor_ok": True, "power": power, "voltage": 220.0}


def send(c, **kw):
    return c.post("/telemetry", json=pkt(**kw), headers=H).json()


def cutoff(c, dev):
    d = {x["device_id"]: x for x in c.get("/state", headers=H).json()["devices"]}
    return d[dev]["cutoff"]


def lockout_node_a(c):
    send(c, seq=0, ts=1000)
    return send(c, seq=1, ts=1010)       # 10 W x 10 s = 0,028 Wh > 0,001 Wh


def test_reply_marks_sent_then_next_packet_confirms(client, clock):
    assert lockout_node_a(client)["lockout"] is True
    assert cutoff(client, A)["status"] == "sent"
    assert cutoff(client, A)["channel"] == "reply"
    assert cutoff(client, B)["status"] == "requested"            # B belum pernah mengirim apa pun

    clock.t += 5
    send(client, seq=2, ts=1015, relay=False)
    c = cutoff(client, A)
    assert c["status"] == "confirmed" and c["latency_s"] == pytest.approx(5.0)


def test_node_still_on_is_acknowledged_not_confirmed(client, clock):
    lockout_node_a(client)
    clock.t += 5
    send(client, seq=2, ts=1015, relay=True)                     # hidup, tetapi relay belum OFF
    assert cutoff(client, A)["status"] == "acknowledged"
    clock.t += 3
    send(client, seq=3, ts=1018, relay=False)
    assert cutoff(client, A)["status"] == "confirmed"


def test_old_buffered_packet_does_not_progress(client, clock):
    send(client, seq=0, ts=1000)
    send(client, seq=2, ts=1010)                                 # memicu lockout di seq 2
    assert cutoff(client, A)["status"] == "sent"
    clock.t += 2
    send(client, seq=1, ts=1005, relay=False)                    # paket lama dari antrean buffer
    assert cutoff(client, A)["status"] == "sent"
    clock.t += 2
    send(client, seq=3, ts=1015, relay=False)
    assert cutoff(client, A)["status"] == "confirmed"


def test_timeout_when_node_never_responds(client, clock):
    lockout_node_a(client)
    clock.t += 11
    commands.run_tick(client.db, None, clock.t, clock)
    assert cutoff(client, A)["status"] == "timeout"
    assert cutoff(client, B)["status"] == "timeout"
    kinds = [a["kind"] for a in client.get("/alerts", headers=H).json()["alerts"]]
    assert kinds.count("cutoff_timeout") == 2


def test_confirm_after_deadline_is_too_late(client, clock):
    lockout_node_a(client)
    clock.t += 11
    send(client, seq=2, ts=1020, relay=False)
    assert cutoff(client, A)["status"] == "timeout"


def test_sinric_retries_then_failed(client, clock):
    calls = []

    def bad(dev):
        calls.append((dev, clock.t))
        return False, "HTTP 500"

    lockout_node_a(client)                                       # A: sent lewat balasan. B: requested
    for dt in (0, 3.4, 6.8, 7.0):
        clock.t = 10_000.0 + dt
        commands.run_tick(client.db, bad, clock.t, clock)
    b_calls = [t for d, t in calls if d == B]
    assert len(b_calls) == 3                                     # 1 awal + 2 ulangan, tidak lebih
    assert cutoff(client, B)["status"] == "failed"
    assert cutoff(client, B)["last_error"] == "HTTP 500"
    assert cutoff(client, A)["status"] == "sent"                 # jalur balasan tetap berlaku


def test_sinric_ok_marks_sent_even_if_node_silent(client, clock):
    calls = []

    def ok(dev):
        calls.append((dev, clock.t))
        return True, "ok"

    lockout_node_a(client)
    clock.t = 10_000.0
    commands.run_tick(client.db, ok, clock.t, clock)
    assert cutoff(client, B)["status"] == "sent" and cutoff(client, B)["channel"] == "sinric"
    for dt in (3.4, 6.8, 10.5):
        clock.t = 10_000.0 + dt
        commands.run_tick(client.db, ok, clock.t, clock)
    assert len([1 for d, _ in calls if d == B]) == 3
    assert cutoff(client, B)["status"] == "timeout"


def test_dispatcher_exception_does_not_stop_other_node(client, clock):
    def flaky(dev):
        if dev == B:
            raise RuntimeError("boom")
        return True, "ok"

    lockout_node_a(client)
    commands.run_tick(client.db, flaky, clock.t, clock)
    assert cutoff(client, B)["last_error"] == "RuntimeError"


def test_topup_cancels_open_commands_and_nothing_is_sent(client, clock):
    lockout_node_a(client)
    client.post("/topup", json={"amount_wh": 5}, headers=H)
    assert cutoff(client, A)["status"] == "cancelled"
    sent = []
    commands.run_tick(client.db, lambda d: sent.append(d) or (True, "ok"), clock.t, clock)
    assert sent == []


def test_migrates_old_commands_table(tmp_path):
    p = str(tmp_path / "old.db")
    c = sqlite3.connect(p)
    c.executescript(
        "CREATE TABLE commands (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, "
        "command TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);"
        "INSERT INTO commands (device_id, command, status, reason, created_at, updated_at) "
        "VALUES ('sela-node-a','OFF','requested','saldo_habis',1,1);")
    c.commit()
    c.close()
    db.init_db(p, 5.0, 100.0)
    conn = db.connect(p)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(commands)")}
    assert {"attempts", "sent_at", "confirmed_at", "ref_seq", "channel", "last_error"} <= cols
    with db.tx(conn):
        commands.plan_tick(conn, 200.0, True)                    # lockout = 0, jadi baris lama dibatalkan
    assert conn.execute("SELECT status FROM commands").fetchone()["status"] == "cancelled"
    conn.close()