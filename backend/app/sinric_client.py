"""Kirim OFF ke Sinric Pro lewat REST (sama dengan sinric/sinric_probe.py). Hanya pustaka standar."""
import json
import os
import urllib.error
import urllib.request
from typing import Callable, Optional, Tuple

Dispatcher = Callable[[str], Tuple[bool, str]]   # device_id SELA -> (berhasil, keterangan)


def make_dispatcher_from_env() -> Optional[Dispatcher]:
    """None bila SINRIC_API_KEY kosong: backend hanya memakai jalur balasan telemetri (lockout=true)."""
    key = os.environ.get("SINRIC_API_KEY", "")
    if not key:
        return None
    base = os.environ.get("SINRIC_API_BASE", "https://api.sinric.pro").rstrip("/")
    timeout = float(os.environ.get("SINRIC_HTTP_TIMEOUT_S", "2"))
    ids = {
        "sela-node-a": os.environ.get("SINRIC_DEVICE_A_ID", ""),
        "sela-node-b": os.environ.get("SINRIC_DEVICE_B_ID", ""),
    }

    def dispatch(device_id: str) -> Tuple[bool, str]:
        sinric_id = ids.get(device_id, "")
        if not sinric_id:
            return False, f"ID Sinric untuk {device_id} belum diisi"
        body = json.dumps({
            "type": "request",
            "action": "setPowerState",
            "value": json.dumps({"state": "Off"}),
        }).encode()
        req = urllib.request.Request(
            f"{base}/api/v1/devices/{sinric_id}/action", data=body, method="POST",
            headers={"X-SINRIC-API-KEY": key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read() or b"{}")
                if resp.status == 200 and data.get("success") is True:
                    return True, "ok"
                return False, f"HTTP {resp.status}: {str(data)[:120]}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            return False, f"{type(e).__name__}"

    return dispatch