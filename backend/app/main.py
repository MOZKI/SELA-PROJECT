import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager, suppress
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from . import commands, service
from .db import connect, db_path, init_db, tx
from .sinric_client import make_dispatcher_from_env


def now() -> float:
    return time.time()


def initial_balance() -> float:
    return float(os.environ.get("SELA_INITIAL_BALANCE_WH", "5"))


async def _command_loop(dispatcher, tick_s: float) -> None:
    """Latar belakang: kirim ulang OFF dan tandai timeout/failed, walau node tidak mengirim telemetri."""
    while True:
        try:
            await asyncio.to_thread(commands.run_tick, db_path(), dispatcher, now(), now)
        except Exception as e:  # noqa: BLE001  loop tidak boleh mati
            print(f"[commands] tick gagal: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(tick_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.environ.get("SELA_API_KEY"):
        raise RuntimeError("SELA_API_KEY belum diisi. Salin .env.example jadi .env")
    init_db(db_path(), initial_balance(), now())
    task = None
    tick_s = float(os.environ.get("SELA_CMD_TICK_S", "1"))
    if tick_s > 0:
        dispatcher = make_dispatcher_from_env()
        print(f"[commands] pelacakan cut-off aktif, REST Sinric {'AKTIF' if dispatcher else 'nonaktif'}", flush=True)
        task = asyncio.create_task(_command_loop(dispatcher, tick_s))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="SELA Backend", lifespan=lifespan)


def require_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = os.environ.get("SELA_API_KEY", "")
    if not expected or not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="API key salah atau tidak ada")


class Telemetry(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    boot_id: str = Field(min_length=1, max_length=64)
    seq: int = Field(ge=0)
    ts: float
    source: Literal["real", "synthetic", "replay"]
    relay_on: bool
    sensor_ok: bool
    voltage: Optional[float] = None
    current: Optional[float] = None
    power: Optional[float] = Field(default=None, ge=0)
    pf: Optional[float] = None
    freq: Optional[float] = None
    energy_wh: Optional[float] = None


class TopUp(BaseModel):
    amount_wh: float = Field(gt=0)
    note: Optional[str] = None


class Reset(BaseModel):
    balance_wh: float = Field(default=5.0, ge=0)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/telemetry")
def post_telemetry(t: Telemetry, _: None = Depends(require_key)):
    conn = connect(db_path())
    try:
        with tx(conn):
            return service.process_telemetry(conn, t, now())
    finally:
        conn.close()


@app.get("/state")
def get_state(_: None = Depends(require_key)):
    conn = connect(db_path())
    try:
        return service.get_state(conn, now())
    finally:
        conn.close()


@app.post("/topup")
def post_topup(body: TopUp, _: None = Depends(require_key)):
    conn = connect(db_path())
    try:
        with tx(conn):
            return service.do_topup(conn, body.amount_wh, body.note, now())
    finally:
        conn.close()


@app.post("/admin/reset")
def post_reset(body: Reset, _: None = Depends(require_key)):
    conn = connect(db_path())
    try:
        with tx(conn):
            return service.do_reset(conn, body.balance_wh, now())
    finally:
        conn.close()


@app.get("/alerts")
def get_alerts(since: float = 0.0, limit: int = Query(default=100, ge=1, le=1000),
               _: None = Depends(require_key)):
    conn = connect(db_path())
    try:
        return service.get_alerts(conn, since, limit)
    finally:
        conn.close()