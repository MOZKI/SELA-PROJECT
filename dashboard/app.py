"""Dashboard SELA versi dasar: login satu password, kartu per node, daya total, saldo.

Semua data dari GET /state di backend. Konfigurasi lewat environment variable:
  SELA_API_URL, SELA_API_KEY, DASHBOARD_PASSWORD
"""
import hmac
import os
from typing import Optional, Tuple

import requests
import streamlit as st

API_URL = os.environ.get("SELA_API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("SELA_API_KEY", "")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
REFRESH_S = 5
REQUEST_TIMEOUT_S = 5
LOW_BALANCE_RATIO = 0.2  # peringatan saldo rendah di 20% kuota acuan

NODE_LABELS = {
    "sela-node-a": "Node A (10 W)",
    "sela-node-b": "Node B (20 W)",
}

st.set_page_config(page_title="SELA Dashboard", page_icon="💡", layout="wide")


# ---------- Login ----------

def check_login() -> bool:
    """Login satu password. Bila DASHBOARD_PASSWORD kosong, akses ditolak (fail closed)."""
    if st.session_state.get("authed"):
        return True
    if not PASSWORD:
        st.error("DASHBOARD_PASSWORD belum diisi di environment. Dashboard tidak bisa dibuka.")
        return False

    st.title("SELA Dashboard")
    with st.form("login"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Masuk")
    if submitted:
        if hmac.compare_digest(pw.encode(), PASSWORD.encode()):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Password salah")
    return False


# ---------- Data ----------

def fetch_state() -> Tuple[Optional[dict], Optional[str]]:
    """Return (state, error). Salah satunya selalu None."""
    if not API_KEY:
        return None, "SELA_API_KEY belum diisi di environment dashboard."
    try:
        r = requests.get(f"{API_URL}/state", headers={"X-API-Key": API_KEY}, timeout=REQUEST_TIMEOUT_S)
    except requests.RequestException as e:
        return None, f"Backend tidak terjangkau ({API_URL}): {type(e).__name__}"
    if r.status_code == 401:
        return None, "API key ditolak backend. Samakan SELA_API_KEY dashboard dengan backend/.env."
    if r.status_code != 200:
        return None, f"Backend membalas HTTP {r.status_code}"
    try:
        return r.json(), None
    except ValueError:
        return None, "Balasan backend bukan JSON"


# ---------- Format ----------

def fmt(value: Optional[float], unit: str, digits: int = 1) -> str:
    """Angka dengan satuan, atau '—' bila kosong."""
    return "—" if value is None else f"{value:.{digits}f} {unit}".rstrip()


def fmt_w(value: Optional[float]) -> str:
    return fmt(value, "W")


def measurements_caption(m: dict) -> str:
    """Ringkasan V, A, PF, Hz, register energi. Field yang kosong ditampilkan '—'."""
    parts = [
        fmt(m.get("voltage"), "V"),
        fmt(m.get("current"), "A", 3),
        "PF " + fmt(m.get("pf"), "", 2),
        fmt(m.get("freq"), "Hz"),
        "register energi " + fmt(m.get("energy_wh"), "Wh", 0),
    ]
    return " · ".join(parts)


# ---------- Tampilan ----------

def connection_status(d: dict) -> str:
    if d["age_s"] is None:
        return "🔴 **Belum pernah mengirim data**"
    if d["online"]:
        return f"🟢 **Online** · data {d['age_s']:.0f} detik lalu"
    return f"🔴 **Offline** · data terakhir {d['age_s']:.0f} detik lalu"


def lamp_status(relay_on: Optional[bool]) -> str:
    if relay_on is None:
        return "tidak diketahui"
    return "💡 ON" if relay_on else "⚫ OFF"


def node_card(d: dict) -> None:
    label = NODE_LABELS.get(d["device_id"], d["device_id"])
    with st.container(border=True):
        st.subheader(label)
        st.markdown(connection_status(d))

        c1, c2 = st.columns(2)
        c1.metric("Lampu", lamp_status(d["relay_on"]))
        c2.metric("Daya", fmt_w(d["power_w"]) if d["online"] else "—")

        m = d.get("measurements")
        if d["online"] and d["sensor_ok"] and m:
            st.caption(measurements_caption(m))
        if d["sensor_ok"] is False:
            st.caption("⚠️ Sensor gagal dibaca, daya tidak dihitung")


def balance_section(state: dict) -> None:
    ref = state["reference_wh"] or 0
    bal = state["balance_wh"]
    ratio = max(0.0, min(1.0, bal / ref)) if ref > 0 else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric("Saldo", f"{bal:.3f} Wh")
    c2.metric("Daya total", fmt_w(state["total_power_w"]))
    c3.metric("Kuota acuan", f"{ref:.2f} Wh")
    st.progress(ratio, text=f"Sisa {ratio * 100:.0f}% dari kuota acuan")

    if not state["lockout"] and ref > 0 and ratio <= LOW_BALANCE_RATIO:
        st.warning("Saldo rendah (≤ 20% kuota acuan).")


@st.fragment(run_every=REFRESH_S)
def live_view() -> None:
    state, err = fetch_state()
    if err:
        st.error(err)
        return

    if state["lockout"]:
        st.error("🔒 LOCKOUT aktif: saldo habis, ON ditolak sampai top-up.")
    if state["partial"]:
        st.warning("Data parsial: ada node offline atau belum mengirim data. Daya total hanya dari node yang online.")

    balance_section(state)

    devices = state["devices"]
    for col, d in zip(st.columns(max(1, len(devices))), devices):
        with col:
            node_card(d)

    if state["overshoot_wh"]:
        st.caption(f"Overshoot tercatat: {state['overshoot_wh']:.4f} Wh")
    st.caption(f"Diperbarui tiap {REFRESH_S} detik · backend {API_URL}")


def main() -> None:
    if not check_login():
        return
    st.title("💡 SELA — Smart Energy Light Automation")
    live_view()


main()