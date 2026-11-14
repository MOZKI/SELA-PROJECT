import pytest


@pytest.fixture(autouse=True)
def isolasi_env(monkeypatch):
    """Tes tidak boleh menyentuh Sinric asli dan tidak menjalankan loop latar belakang."""
    for k in ("SINRIC_API_KEY", "SINRIC_API_BASE", "SINRIC_DEVICE_A_ID", "SINRIC_DEVICE_B_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SELA_CMD_TICK_S", "0")