import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sim  # noqa: E402


def node(key="A"):
    return sim.SimNode(key, "b-test", random.Random(1), 0.0)


def test_relay_off_saat_boot_dan_daya_nol():
    n = node()
    p = n.sample(1000.0)
    assert p["relay_on"] is False and p["power"] in (0.0, 0.1) and p["source"] == "synthetic"


def test_nominal_daya_per_node():
    for key, watt in (("A", 10.0), ("B", 20.0)):
        n = node(key)
        n.request_on()
        powers = [n.sample(1000.0 + 5 * i)["power"] for i in range(200)]
        assert abs(sum(powers) / len(powers) - watt) < 0.2


def test_seq_naik_dan_mulai_nol():
    n = node()
    assert [n.sample(1000.0 + 5 * i)["seq"] for i in range(3)] == [0, 1, 2]


def test_on_ditolak_saat_lockout_dan_off_tetap_boleh():
    n = node()
    n.request_on()
    n.apply_ack({"lockout": True})
    assert n.relay_on is False            # cut-off lokal
    assert n.request_on() is False
    n.request_off()
    n.apply_ack({"lockout": False})       # top-up melepas lockout, tetapi relay tidak otomatis ON
    assert n.relay_on is False and n.request_on() is True


def test_dataset_memuat_4_kondisi_dan_label(tmp_path):
    out = tmp_path / "d.csv"
    assert sim.main(["dataset", "--days", "7", "--csv", str(out)]) == 0
    import csv
    rows = list(csv.DictReader(out.open()))
    assert {r["source"] for r in rows} == {"synthetic"}
    by_seq = {}
    for r in rows:
        by_seq.setdefault(r["seq"], {})[r["device_id"]] = r["relay_on"]
    assert len({(v["sela-node-a"], v["sela-node-b"]) for v in by_seq.values()}) == 4
    assert {r["day_type"] for r in rows} == {"weekday", "weekend"}
