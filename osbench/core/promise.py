"""Delivery promise computation."""

from __future__ import annotations

from ..config.schema import PromiseConfig

_MIN_PER_DAY = 1440


def compute_promise_min(
    created_min: int,
    best_transit_days: int,
    cfg: PromiseConfig,
    avail_delay_days: int = 0,
    noise_quantile_days: int = 0,
) -> int:
    """Promised delivery date in minutes (rounded to the end of the day).

    best_transit_days — tariff transit time to the nearest candidate warehouse (optimistic estimate).
    avail_delay_days — extra days until all lines are available by cumulative ATP (``atp_based``).
    noise_quantile_days — delivery-noise quantile in days added by the ``percentile`` method.
    """
    created_day = created_min // _MIN_PER_DAY
    base = cfg.min_processing_days + best_transit_days
    if cfg.method == "zone_fixed":
        days = base
    elif cfg.method == "atp_based":
        # Earliest date all lines are available (now or after an expected receipt) + handling + transit.
        days = cfg.min_processing_days + avail_delay_days + best_transit_days
    elif cfg.method == "percentile":
        # Statistical promise: zone baseline + a quantile of realized delivery noise.
        days = base + noise_quantile_days
    else:  # transit_plus_buffer
        days = base + cfg.buffer_days
    days = min(days, cfg.cap_days)
    promise_day = created_day + days
    return (promise_day + 1) * _MIN_PER_DAY - 1  # end of the promised day


def accept_probability(promise_days: int, baseline_days: int, cfg: PromiseConfig) -> float:
    """Probability a customer accepts a promise of ``promise_days``.

    1.0 up to the zone baseline, then linearly down to ``cfg.p_accept_promise`` at ``cap_days``.
    With the default p_accept_promise=1.0 this is always 1.0 (no rejections).
    """
    if cfg.p_accept_promise >= 1.0 or promise_days <= baseline_days:
        return 1.0
    span = max(1, cfg.cap_days - baseline_days)
    frac = min(1.0, (promise_days - baseline_days) / span)
    return 1.0 - frac * (1.0 - cfg.p_accept_promise)


def noise_quantile_days(noise_mean: float, q: float) -> int:
    """Quantile (in whole days) of the geometric delivery-noise distribution used in shipping.

    Shipping draws noise = geometric(1/(1+mean)) - 1; this returns ceil of its q-quantile.
    """
    import math

    if noise_mean <= 0 or q <= 0:
        return 0
    p = 1.0 / (1.0 + noise_mean)
    if q >= 1.0:
        q = 0.999
    # Geometric(p) quantile (support 1,2,...): ceil(ln(1-q)/ln(1-p)); subtract 1 for the -1 offset.
    val = math.ceil(math.log(1.0 - q) / math.log(1.0 - p)) - 1
    return max(0, int(val))
