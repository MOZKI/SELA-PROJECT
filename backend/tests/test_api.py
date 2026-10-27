import pytest
from fastapi.testclient import TestClient

KEY = "kunci-tes"
H = {"X-API-Key": KEY}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SELA_API_KEY", KEY)
    monkeypatch.setenv("SELA_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("SELA_INITIAL_BALANCE_WH", "5")
    return tmp_path


@pytest.fixture
def client(env):
    from app.main import app
    with TestClient(app) as c:
        yield c


def pkt(device="sela-node-a", boot="b1", seq=0, ts=1000.0, power=10.0, relay=True, ok=True):
    return {"device_id": device, "boot_id": boot, "seq": seq, "ts": ts, "source": "synthetic",
            "relay_on": relay, "sensor_ok": ok, "power": power, "voltage": 220.0}


def state(c):
    return c.get("/state", headers=H).json()


def test_auth_required(client):
    assert client.get("/state").status_code == 401
    assert client.get("/state", headers={"X-API-Key": "salah"}).status_code == 401
    assert client.post("/telemetry", json=pkt()).status_code == 401
    assert client.get("/health").status_code == 200


def test_basic_energy_accounting(client):
    client.post("/telemetry", json=pkt(seq=0, ts=1000), headers=H)
    r = client.post("/telemetry", json=pkt(seq=1, ts=1005), headers=H).json()
    assert r["ack_seq"] == 1 and not r["duplicate"] and not r["lockout"]
    assert r["balance_wh"] == pytest.approx(5 - 10 * 5 / 3600, abs=1e-6)


def test_duplicate_packet_not_counted_twice(client):
    client.post("/telemetry", json=pkt(seq=0, ts=1000), headers=H)
    client.post("/telemetry", json=pkt(seq=1, ts=1005), headers=H)
    before = state(client)["balance_wh"]
    r = client.post("/telemetry", json=pkt(seq=1, ts=1005), headers=H).json()
    assert r["duplicate"] is True
    assert state(client)["balance_wh"] == before


def test_gap_not_integrated(client):
    client.post("/telemetry", json=pkt(seq=0, ts=1000), headers=H)
    client.post("/telemetry", json=pkt(seq=1, ts=1100), headers=H)
    assert state(client)["balance_wh"] == pytest.approx(5.0)


def test_new_boot_does_not_integrate_across_boots(client):
    client.post("/telemetry", json=pkt(boot="b1", seq=0, ts=1000), headers=H)
    client.post("/telemetry", json=pkt(boot="b2", seq=0, ts=1005), headers=H)
    assert state(client)["balance_wh"] == pytest.approx(5.0)


def test_two_nodes_combined_balance_and_total(client):
    for i in range(2):
        client.post("/telemetry", json=pkt("sela-node-a", seq=i, ts=1000 + 5 * i, power=10), headers=H)
        client.post("/telemetry", json=pkt("sela-node-b", seq=i, ts=1000 + 5 * i, power=20), headers=H)
    s = state(client)
    assert s["total_power_w"] == pytest.approx(30)
    assert s["partial"] is False
    assert s["balance_wh"] == pytest.approx(5 - 30 * 5 / 3600, abs=1e-6)


def test_partial_when_one_node_missing(client):
    client.post("/telemetry", json=pkt("sela-node-a", seq=0, ts=1000), headers=H)
    s = state(client)
    assert s["partial"] is True
    assert [d["online"] for d in s["devices"]] == [True, False]


def test_node_goes_offline_after_15s(client, monkeypatch):
    import app.main as m
    t0 = 5000.0
    monkeypatch.setattr(m, "now", lambda: t0)
    client.post("/telemetry", json=pkt(seq=0, ts=1000), headers=H)
    monkeypatch.setattr(m, "now", lambda: t0 + 16)
    s = state(client)
    assert s["devices"][0]["online"] is False and s["partial"] is True


def test_sensor_failure_not_counted_as_zero(client):
    client.post("/telemetry", json=pkt(seq=0, ts=1000, power=10), headers=H)
    client.post("/telemetry", json=pkt(seq=1, ts=1005, power=None, ok=False), headers=H)
    assert state(client)["balance_wh"] == pytest.approx(5.0)


def deplete(client):
    client.post("/admin/reset", json={"balance_wh": 0.01}, headers=H)
    client.post("/telemetry", json=pkt(seq=0, ts=1000, power=30), headers=H)
    return client.post("/telemetry", json=pkt(seq=1, ts=1005, power=30), headers=H).json()


def test_zero_balance_triggers_lockout_and_off_commands(client, env):
    r = deplete(client)
    assert r["balance_wh"] == 0 and r["lockout"] is True
    s = state(client)
    assert s["lockout"] and s["overshoot_wh"] > 0
    import sqlite3
    con = sqlite3.connect(env / "t.db")
    cmds = con.execute("SELECT device_id, command, status FROM commands").fetchall()
    assert set(d for d, _, _ in cmds) == {"sela-node-a", "sela-node-b"}
    assert all(c == "OFF" and s == "requested" for _, c, s in cmds)


def test_lockout_command_created_only_once(client, env):
    deplete(client)
    client.post("/telemetry", json=pkt(seq=2, ts=1010, power=30), headers=H)
    import sqlite3
    n = sqlite3.connect(env / "t.db").execute("SELECT COUNT(*) FROM commands").fetchone()[0]
    assert n == 2


def test_lockout_survives_restart(env):
    from app.main import app
    with TestClient(app) as c1:
        deplete(c1)
        assert state(c1)["lockout"] is True
    with TestClient(app) as c2:   # simulasi container restart, database sama
        s = state(c2)
        assert s["lockout"] is True and s["balance_wh"] == 0


def test_topup_releases_lockout_and_lamps_stay_off(client, env):
    deplete(client)
    s = client.post("/topup", json={"amount_wh": 5}, headers=H).json()
    assert s["lockout"] is False and s["balance_wh"] == pytest.approx(5)
    import sqlite3
    con = sqlite3.connect(env / "t.db")
    on_cmds = con.execute("SELECT COUNT(*) FROM commands WHERE command = 'ON'").fetchone()[0]
    assert on_cmds == 0


def test_low_balance_alert_once(client, env):
    client.post("/admin/reset", json={"balance_wh": 5}, headers=H)
    ts = 1000
    for i in range(0, 130):
        client.post("/telemetry", json=pkt(seq=i, ts=ts + 5 * i, power=30), headers=H)
    import sqlite3
    kinds = [r[0] for r in sqlite3.connect(env / "t.db").execute("SELECT kind FROM alerts")]
    assert kinds.count("saldo_rendah") == 1
    assert kinds.count("saldo_habis") == 1
