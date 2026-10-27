"""Logika saldo dan integrasi energi. Fungsi murni: tanpa web, tanpa database."""
from dataclasses import dataclass
from typing import Optional, Tuple

MAX_GAP_S = 15.0


@dataclass(frozen=True)
class Sample:
    ts: float                  # detik (epoch) dari perangkat
    power_w: Optional[float]   # None bila sensor gagal (bukan 0 W)


@dataclass(frozen=True)
class Step:
    delta_wh: float
    gap: bool       # True bila ada celah data yang dicatat, bukan diintegrasikan
    reason: str     # first | ok | gap | sensor | out_of_order


def integrate_step(prev: Optional[Sample], cur: Sample, max_gap_s: float = MAX_GAP_S) -> Step:
    """Integrasi trapesium antara dua sampel berurutan dari device dan boot yang sama."""
    if prev is None:
        return Step(0.0, False, "first")
    dt = cur.ts - prev.ts
    if dt <= 0:
        return Step(0.0, True, "out_of_order")
    if prev.power_w is None or cur.power_w is None:
        return Step(0.0, True, "sensor")
    if dt > max_gap_s:
        return Step(0.0, True, "gap")
    delta = ((prev.power_w + cur.power_w) / 2.0) * (dt / 3600.0)
    return Step(delta, False, "ok")


def apply_energy(balance_wh: float, delta_wh: float) -> Tuple[float, float]:
    """Kurangi saldo. Saldo tampilan minimum nol, kelebihan dicatat sebagai overshoot."""
    new_balance = balance_wh - delta_wh
    if new_balance < 0:
        return 0.0, -new_balance
    return new_balance, 0.0


def apply_topup(balance_wh: float, amount_wh: float) -> float:
    if amount_wh <= 0:
        raise ValueError("amount_wh harus positif")
    return balance_wh + amount_wh


def is_online(age_s: Optional[float], stale_after_s: float = 15.0) -> bool:
    return age_s is not None and age_s <= stale_after_s


def is_low(balance_wh: float, reference_wh: float, ratio: float = 0.2) -> bool:
    return reference_wh > 0 and 0 < balance_wh <= reference_wh * ratio