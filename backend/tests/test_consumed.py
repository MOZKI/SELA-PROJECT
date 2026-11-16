import pytest
from fastapi.testclient import TestClient

KEY = "kunci-tes"
H = {"X-API-Key": KEY}
A = "sela-node-a"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SELA_API_KEY", KEY)
    monkeypatch.setenv("SELA_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("SELA_INITIAL_BALANCE_WH", "5")
    from app.main import app
    with TestClient(app) as c:
        yield c


def pkt(seq, ts, power=36.0):
    return {"device_id": A, "boot_id": "b1", "seq": seq, "ts": ts, "source": "synthetic",
            "relay_on": True, "sensor_ok": True, "power": power, "voltage": 220.0}


def node(state, dev=A):
    return {d["device_id"]: d for d in state["devices"]}[dev]


def test_consumed_accumulates_and_resets(client):
    client.post("/telemetry", json=pkt(0, 1000), headers=H)
    client.post("/telemetry", json=pkt(1, 1010), headers=H)      # 36 W x 10 s = 0,1 Wh
    s = client.get("/state", headers=H).json()
    assert s["consumed_wh"] == pytest.approx(0.1)
    assert node(s)["consumed_wh"] == pytest.approx(0.1)
    assert node(s, "sela-node-b")["consumed_wh"] == 0.0

    client.post("/topup", json={"amount_wh": 1}, headers=H)      # top-up tidak mengubah hitungan
    assert client.get("/state", headers=H).json()["consumed_wh"] == pytest.approx(0.1)

    r = client.post("/admin/reset", json={"balance_wh": 5}, headers=H).json()
    assert r["consumed_wh"] == 0.0                               # reset memulai hitungan dari nol