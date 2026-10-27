import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import service
from .db import connect, db_path, init_db, tx


def now() -> float:
    return time.time()


def initial_balance() -> float:
    return float(os.environ.get("SELA_INITIAL_BALANCE_WH", "5"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.environ.get("SELA_API_KEY"):
        raise RuntimeError("SELA_API_KEY belum diisi. Salin .env.example jadi .env")
    init_db(db_path(), initial_balance(), now())
    yield


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