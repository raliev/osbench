"""Discrete-event simulation engine (happy-path, v1)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from ..config.policy import ReplenishmentPolicyConfig, SourcingPolicyConfig
from ..journal.schema import JType
from ..journal.writer import JournalWriter
from ..registry import get_replenishment, get_sourcing
from ..rng.counter_based import make_generator
from ..types import Assignment, CancelPoint, LineAlloc, Order, OrderLine, PolicyContext
from .events import Ev, EventQueue
from .network import Network
from .promise import accept_probability, compute_promise_min
from .state import init_inventory

_MIN_PER_DAY = 1440


class SimEngine:
    """A single run: world + policies → event journal."""

    def __init__(
        self,
        world,
        orders: pd.DataFrame,
        seed: int,
        sourcing_name: str,
        replenishment_name: str = "base_stock",
        sourcing_cfg: SourcingPolicyConfig | None = None,
        replenishment_cfg: ReplenishmentPolicyConfig | None = None,
        live_hub=None,
        snapshot_every_hours: int = 6,
        guardrail=None,
        inventory_detail: bool = False,
    ):
        self.world = world
        self.cfg = world.config
        self.seed = int(seed)
        self.net = Network(world)

        self.scfg = sourcing_cfg or SourcingPolicyConfig(name=sourcing_name)
        self.rcfg = replenishment_cfg or ReplenishmentPolicyConfig(name=replenishment_name)
        self.sourcing = get_sourcing(sourcing_name)(self.scfg, self.net)
        self.replenishment = get_replenishment(replenishment_name)(self.rcfg, self.net)

        self.avg_lead = float(np.mean([s.lead_time_days for s in self.cfg.suppliers]))
        self.inv = init_inventory(
            world, self.avg_lead, self.rcfg.review_days,
            self.rcfg.safety_days, self.rcfg.S_overshoot,
        )
        # Realized demand per (sku, wh) since start, for forecast/network replenishment.
        self._demand_cum = np.zeros((self.net.n_sku, self.net.n_wh), np.float64)

        self._build_order_arrays(orders)

        self.queue = EventQueue()
        self.wh_lines_used = np.zeros(self.net.n_wh, np.int64)
        self.wh_parcels_used = np.zeros(self.net.n_wh, np.int64)
        self._po: list[tuple[int, int, int, float]] = []  # (sku, wh, qty, unit_price)
        self._parcels: dict[int, list[tuple[int, list[int]]]] = {}  # oi -> [(wh, [global line idx])]
        self._inv_daily: list[dict] = []
        # Per-SKU x warehouse daily stock snapshots for the inspector (small scenarios only).
        self.inventory_detail = bool(inventory_detail)
        self._inv_sku_daily: list[tuple[int, np.ndarray, np.ndarray]] = []
        # reservation.enabled=False: sourcing holds nothing, stock is decremented only at pick, so
        # several orders can claim the same unit and the conflict surfaces at pick (point 3).
        self._reserve = bool(self.cfg.reservation.enabled)

        # Cheapest supplier (v1: a single one for all, by minimum price).
        sup = min(self.cfg.suppliers, key=lambda s: s.unit_price)
        self._supplier_price = sup.unit_price
        self._supplier_moq = sup.moq
        self._supplier_lead = sup.lead_time_days
        self._late_rate = float(self.cfg.shortage.penalty_late_per_day)

        # Warehouse outage shock: the warehouse is masked out of ATP within the window.
        sh = self.cfg.shocks
        self._outage_wh = -1
        self._outage_start = -1
        self._outage_end = -1
        if sh.enabled and sh.warehouse_outage_id is not None and sh.warehouse_outage_days > 0:
            start_day = sh.warehouse_outage_start_day or 0
            self._outage_wh = int(sh.warehouse_outage_id)
            self._outage_start = start_day * _MIN_PER_DAY
            self._outage_end = (start_day + sh.warehouse_outage_days) * _MIN_PER_DAY

        # Offline planning hook: a policy allowed to see the full demand (e.g. hindsight_lp)
        # precomputes its plan from the realized order stream before the run starts.
        if hasattr(self.sourcing, "plan"):
            self.sourcing.plan(self)

        # Periodic policy callback (e.g. LP bid-price recompute).
        self._period_days = int(getattr(self.sourcing, "period_days", 0) or 0)

        # Promise method (atp_based availability / percentile quantile / acceptance).
        pc = self.cfg.promise
        from .promise import noise_quantile_days
        self._promise_noise_q = (
            noise_quantile_days(self.net.shipping.noise_mean, pc.percentile)
            if pc.method == "percentile" else 0
        )
        self._atp_method = self.cfg.atp.method
        self._atp_horizon_min = int(self.cfg.atp.horizon_days * _MIN_PER_DAY)

        # Deferred sourcing (RQ5): orders park without a reservation, waiting for inbound stock
        # until a deadline derived from their promise slack. Keyed registry + per-SKU inbound ETAs.
        dc = self.cfg.deferral
        self._defer_mode = dc.mode
        self._defer_arb = dc.arbitration
        self._defer_safety_min = int(dc.safety_margin_days * _MIN_PER_DAY)
        self._defer_miss = float(dc.penalty_deferral_miss)
        self._defer_max = int(dc.max_waiting_per_sku)
        self._inv_accuracy = float(self.cfg.inventory_accuracy.accuracy)
        self._inv_err_mean = float(self.cfg.inventory_accuracy.error_mean)
        self._deferred: dict[int, dict] = {}          # oi -> {deadline, needed:set[sku], wake}
        self._was_deferred: set[int] = set()
        self._defer_waiters: dict[int, set[int]] = {}  # sku -> set of deferred order ids
        # Outstanding inbound receipts: sku -> list of [eta_min, qty, wh] (removed on receipt).
        # Feeds deferral ETAs, cumulative ATP and the precise atp_based promise.
        self._inbound_sched: dict[int, list[list[int]]] = {}
        self._track_inbound = (
            self._defer_mode != "none" or self._atp_method == "cumulative"
            or pc.method == "atp_based" or self.cfg.shortage.backorder
        )
        # Same-category SKU buckets for checkout substitution (point 1, p_substitute).
        self._cat_skus: dict[int, np.ndarray] = {}
        if self.cfg.shortage.p_substitute > 0:
            cat = self.net.sku_category
            for c in np.unique(cat):
                self._cat_skus[int(c)] = np.nonzero(cat == c)[0]

        # Live monitoring.
        self.live = live_hub
        self.guardrail = guardrail
        self.snapshot_every_min = max(1, snapshot_every_hours) * 60
        self.status = "done"
        self._init_live_stats()

    def _init_live_stats(self) -> None:
        n_wh = self.net.n_wh
        self.st_orders_done = 0
        self.st_shipping = 0.0
        self.st_holding = 0.0
        self.st_purchase = 0.0
        self.st_penalty = 0.0
        self.st_lateness = 0.0
        self.st_delivered = 0
        self.st_on_time = 0
        self.st_lost = 0
        self.st_parcels = 0
        self.st_deferred = 0          # unique orders that were deferred at least once
        self.st_promise_rejected = 0  # orders refused at checkout due to a long promise
        self.st_short_checkout = 0    # orders with an ATP shortage at checkout (point 1)
        self.st_short_post = 0        # orders a line could not be sourced (point 2)
        self.st_short_pick = 0        # lines short on physical stock at pick (point 3)
        self.st_substituted = 0       # checkout lines swapped for an in-stock SKU
        self.wh_wip = np.zeros(n_wh, np.int64)  # parcels in progress (queue proxy)
        self.wh_inbound_tick = np.zeros(n_wh, np.int64)
        self.wh_outbound_tick = np.zeros(n_wh, np.int64)
        self._wall_start = 0.0
        self._live_seq = 0

    # ------------------------------------------------------------------ setup
    def _build_order_arrays(self, orders: pd.DataFrame) -> None:
        if len(orders) == 0:
            self.n_orders = 0
            return
        orders = orders.sort_values(["order_id", "line_id"], kind="stable").reset_index(drop=True)
        self.l_sku = orders["sku_id"].to_numpy(np.int64)
        self.l_qty = orders["qty"].to_numpy(np.int64)
        self.l_lineid = orders["line_id"].to_numpy(np.int32)
        self.l_wh = np.full(len(orders), -1, np.int16)
        self.l_term = np.zeros(len(orders), np.uint8)  # 0 none, 1 delivered, 2 cancelled

        oid = orders["order_id"].to_numpy(np.int64)
        self.n_orders = int(oid.max()) + 1
        self.o_start = np.searchsorted(oid, np.arange(self.n_orders), side="left")
        self.o_end = np.searchsorted(oid, np.arange(self.n_orders), side="right")
        first = self.o_start
        self.o_created = orders["created_min"].to_numpy(np.int64)[first]
        self.o_region = orders["region_id"].to_numpy(np.int32)[first]
        self.o_dest_x = orders["dest_x"].to_numpy(np.float64)[first]
        self.o_dest_y = orders["dest_y"].to_numpy(np.float64)[first]
        self.o_promise = np.full(self.n_orders, -1, np.int64)

    # ------------------------------------------------------------------ run
    def run(self, run_dir: Path) -> dict:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        self.journal = JournalWriter(run_dir / "events.parquet")

        horizon = self.cfg.sim_days * _MIN_PER_DAY
        # Safety limit: deliveries play out past the horizon, but not indefinitely.
        hard_cap = horizon + 60 * _MIN_PER_DAY
        # Initial events.
        for day in range(0, self.cfg.sim_days + 1):
            self.queue.push(day * _MIN_PER_DAY, Ev.DAY_ROLL, day)
        for day in range(0, self.cfg.sim_days + 1, self.rcfg.review_days):
            self.queue.push(day * _MIN_PER_DAY, Ev.REPLENISH_REVIEW, day)
        for oi in range(self.n_orders):
            self.queue.push(int(self.o_created[oi]), Ev.ORDER_CREATED, oi)
        self._guard_stop = False
        if self.live is not None or self.guardrail is not None:
            import time as _time

            self._wall_start = _time.perf_counter()
            for tick in range(0, horizon + 1, self.snapshot_every_min):
                self.queue.push(tick, Ev.LIVE_TICK)

        while len(self.queue):
            # Stop from the UI: save partial results and exit.
            if self.live is not None and self.live.control.stop_requested:
                self.status = "stopped"
                self._emit_snapshot(getattr(self, "_last_t", 0), final=True)
                break
            if self._guard_stop:
                break
            t, kind, a, b = self.queue.pop()
            self._last_t = t
            if t > hard_cap:
                break
            if kind == Ev.LIVE_TICK:
                self._emit_snapshot(t)
                continue
            # New orders/replenishments only within the horizon.
            if t > horizon and kind in (Ev.ORDER_CREATED, Ev.REPLENISH_REVIEW, Ev.DAY_ROLL):
                continue
            if kind == Ev.DAY_ROLL:
                self._on_day_roll(t, a)
            elif kind == Ev.REPLENISH_REVIEW:
                self._on_replenish(t, a)
            elif kind == Ev.RECEIPT:
                self._on_receipt(t, b)
            elif kind == Ev.ORDER_CREATED:
                self._on_order_created(t, a)
            elif kind == Ev.ENTER_SOURCING:
                self.st_orders_done += 1  # count each order once (re-sources don't re-count)
                self.queue.push(t, Ev.SOURCING_DECISION, a)
            elif kind == Ev.DEFER_WAKE:
                self._on_defer_wake(t, a)
            elif kind == Ev.SOURCING_DECISION:
                self._on_sourcing(t, a)
            elif kind == Ev.RESERVE:
                self._on_reserve(t, a)
            elif kind == Ev.WH_QUEUE_ADMIT:
                self._on_admit(t, a, b)
            elif kind == Ev.PICK_DONE:
                self._on_pick(t, a, b)
            elif kind == Ev.PACK_DONE:
                self.queue.push(t, Ev.SHIP, a, b)
            elif kind == Ev.SHIP:
                self._on_ship(t, a, b)
            elif kind == Ev.DELIVER:
                self._on_deliver(t, a, b)

        self._finalize_unfulfilled(min(self._last_t if hasattr(self, "_last_t") else horizon, hard_cap))
        self.journal.close()
        self._write_inventory_daily(run_dir)
        if self.live is not None:
            self.live.control.state = self.status
            self.live.finished = True
            self._emit_snapshot(getattr(self, "_last_t", horizon), final=True)
        return {"n_orders": self.n_orders, "status": self.status}

    def _finalize_unfulfilled(self, t: int) -> None:
        """Lines still in the pipeline when the run ends (an overloaded network never shipped
        them) are closed as unfulfilled-at-horizon, so every line reaches a terminal state."""
        pen = float(self.cfg.shortage.penalty_lost_sale)
        for gi in range(self.l_term.shape[0]):
            if self.l_term[gi] != 0:
                continue
            oi = int(np.searchsorted(self.o_end, gi, side="right"))
            self.l_term[gi] = 2
            self.st_lost += 1
            self.st_penalty += pen
            self.journal.log(
                t, JType.LOST_SALE, order_id=oi, line_id=int(self.l_lineid[gi]),
                sku=int(self.l_sku[gi]), parcel=int(CancelPoint.HORIZON),
                qty=int(self.l_qty[gi]), cost=pen,
            )

    def _emit_snapshot(self, t: int, final: bool = False) -> None:
        import time as _time

        if self.live is None and self.guardrail is None:
            return
        wall = _time.perf_counter() - self._wall_start
        done = self.st_orders_done
        total = self.n_orders
        rate = done / wall if wall > 0 else 0.0
        eta = (total - done) / rate if rate > 0 and done < total else 0.0
        cost = self.st_shipping + self.st_holding + self.st_purchase + self.st_penalty + self.st_lateness
        cap = np.clip(self.net.capacity_lines, 1, None)
        assort_cols = self.world.assortment.sum(axis=0)
        stockout_share = float(
            ((self.inv.book <= 0) & (self.world.assortment > 0)).sum() / max(1, assort_cols.sum())
        )
        snap = {
            "seq": self._live_seq,
            "sim_time_min": int(t),
            "sim_day": int(t // _MIN_PER_DAY),
            "wall_s": round(wall, 2),
            "status": self.status if not final else self.live.control.state,
            "cost_per_order": round(cost / done, 4) if done else 0.0,
            "components": {
                "shipping": round(self.st_shipping / done, 4) if done else 0.0,
                "holding": round(self.st_holding / done, 4) if done else 0.0,
                "purchase": round(self.st_purchase / done, 4) if done else 0.0,
                "penalty": round(self.st_penalty / done, 4) if done else 0.0,
                "lateness": round(self.st_lateness / done, 4) if done else 0.0,
            },
            "on_time_rate": round(self.st_on_time / self.st_delivered, 4) if self.st_delivered else None,
            "lost_sales": self.st_lost,
            "lost_sales_rate": round(self.st_lost / done, 4) if done else 0.0,
            "split_rate": round(self.st_parcels / done - 1, 4) if done else 0.0,
            "stockout_share": round(stockout_share, 4),
            "compute": {
                "orders_done": done,
                "orders_total": total,
                "eta_s": round(eta, 1),
                "rate": round(rate, 1),
            },
            "wh": {
                "stock": self.inv.phys.sum(axis=0).astype(int).tolist(),
                "util": (self.wh_lines_used / cap).round(3).tolist(),
                "queue": self.wh_wip.astype(int).tolist(),
                "inbound": self.wh_inbound_tick.astype(int).tolist(),
                "outbound": self.wh_outbound_tick.astype(int).tolist(),
            },
        }
        if self.live is not None:
            self.live.emit(snap)
            # Yield the GIL so the background HTTP/WebSocket server can serve the page and
            # push updates during this CPU-bound run (otherwise the dashboard stays blank).
            _time.sleep(0.002)
        self._live_seq += 1
        self.wh_inbound_tick[:] = 0
        self.wh_outbound_tick[:] = 0

        # Guardrail: automatic stop.
        if self.guardrail is not None and not final:
            action, reason = self.guardrail.check(snap)
            if action == "aborted":
                self.status = "aborted"
                self._guard_stop = True
                if self.live is not None:
                    self.live.control.state = "aborted"

    # ------------------------------------------------------------------ handlers
    def _on_order_created(self, t: int, oi: int) -> None:
        dist = self.net.distance_to(float(self.o_dest_x[oi]), float(self.o_dest_y[oi]))
        zone = self.net.shipping.zone(dist)
        best_transit = int(self.net.shipping.days[zone].min())
        pc = self.cfg.promise
        avail_delay = self._availability_delay_days(t, oi) if pc.method == "atp_based" else 0
        promise = compute_promise_min(t, best_transit, pc, avail_delay, self._promise_noise_q)
        self.o_promise[oi] = promise
        self.journal.log(
            t, JType.ORDER_CREATED, order_id=oi, promised_at=promise,
            qty=int(self.o_end[oi] - self.o_start[oi]),
        )
        # Promise acceptance: a long promise may be rejected at checkout (customer walks away).
        if pc.p_accept_promise < 1.0:
            promise_days = (promise // _MIN_PER_DAY) - (t // _MIN_PER_DAY)
            baseline = pc.min_processing_days + best_transit
            p = accept_probability(promise_days, baseline, pc)
            if p < 1.0:
                gc = make_generator(self.seed, "customer_choice", (oi, 0))
                if float(gc.random()) > p:
                    self._reject_promise(t, oi)
                    return
        # Point 1 (checkout shortage): ATP does not cover a line at order creation.
        if self.cfg.shortage.checkout_atp_check and self._checkout_shortage(t, oi):
            return  # whole order refused at checkout
        g = make_generator(self.seed, "process_times", (oi,))
        delay = int(g.integers(self.cfg.sourcing_wait_min, self.cfg.sourcing_wait_max + 1))
        self.queue.push(t + delay, Ev.ENTER_SOURCING, oi)

    def _checkout_shortage(self, t: int, oi: int) -> bool:
        """Point 1: drop (or refuse) lines whose network ATP can't cover them at checkout.

        Returns True if the whole order is refused (no lines remain to source)."""
        s, e = int(self.o_start[oi]), int(self.o_end[oi])
        atp = self._coverage_atp(t)
        uncovered = [gi for gi in range(s, e) if int(atp[int(self.l_sku[gi])].sum()) < int(self.l_qty[gi])]
        if not uncovered:
            return False
        self.st_short_checkout += 1
        sh = self.cfg.shortage
        gc = make_generator(self.seed, "customer_choice", (oi, 2))
        # Try a same-category in-stock substitute for each uncovered line (the customer accepts
        # with probability p_substitute); substituted lines then proceed normally.
        if sh.p_substitute > 0:
            remaining = []
            for gi in uncovered:
                if float(gc.random()) < sh.p_substitute:
                    sub = self._find_substitute(int(self.l_sku[gi]), int(self.l_qty[gi]), atp)
                    if sub is not None:
                        self.journal.log(
                            t, JType.SUBSTITUTED, order_id=oi, line_id=int(self.l_lineid[gi]),
                            sku=sub, qty=int(self.l_qty[gi]),
                        )
                        self.l_sku[gi] = sub
                        w = int(np.argmax(atp[sub]))
                        atp[sub, w] = max(0, int(atp[sub, w]) - int(self.l_qty[gi]))
                        continue
                remaining.append(gi)
            uncovered = remaining
            if not uncovered:
                return False  # all shortages resolved by substitution
        keep_partial = float(gc.random()) < sh.p_partial
        drop = uncovered if (keep_partial and len(uncovered) < (e - s)) else list(range(s, e))
        for gi in drop:
            self.l_term[gi] = 2
            self.st_lost += 1
            self.st_penalty += float(sh.penalty_lost_sale)
            self.journal.log(
                t, JType.LOST_SALE, order_id=oi, line_id=int(self.l_lineid[gi]),
                sku=int(self.l_sku[gi]), parcel=int(CancelPoint.CHECKOUT),
                qty=int(self.l_qty[gi]), cost=float(sh.penalty_lost_sale),
            )
        return len(drop) == (e - s)  # refused outright if nothing remains

    def _find_substitute(self, sku: int, qty: int, atp: np.ndarray) -> int | None:
        """A different SKU in the same category whose network ATP covers qty (richest first)."""
        cands = self._cat_skus.get(int(self.net.sku_category[sku]))
        if cands is None:
            return None
        best, best_av = None, qty - 1
        for s2 in cands:
            s2 = int(s2)
            if s2 == sku:
                continue
            av = int(atp[s2].sum())
            if av > best_av:
                best, best_av = s2, av
        return best

    def _atp(self) -> np.ndarray:
        """ATP per atp.method: on_hand_only = book - reserved; basic / cumulative also keep the
        safety stock back (cumulative adds inbound receipts on top, see _coverage_atp)."""
        if self._atp_method == "on_hand_only":
            return np.clip(self.inv.book - self.inv.reserved, 0, None)
        return self.inv.atp()

    def _coverage_atp(self, t: int) -> np.ndarray:
        """ATP used for promise/coverage decisions. method=cumulative adds inbound receipts
        arriving within the horizon (and reserved already nets out committed promises)."""
        atp = self._atp()
        if self._atp_method == "cumulative" and self._inbound_sched:
            atp = atp.copy()
            horizon = t + self._atp_horizon_min
            for sku, entries in self._inbound_sched.items():
                for eta, qty, wh in entries:
                    if eta <= horizon:
                        atp[sku, wh] += qty
        return atp

    def _availability_delay_days(self, t: int, oi: int) -> int:
        """Days until every line is available: now (network ATP covers), or the day an inbound
        schedule accumulates enough. Precise — walks the receipt schedule in ETA order."""
        s, e = int(self.o_start[oi]), int(self.o_end[oi])
        atp = self._atp()
        delay = 0
        for gi in range(s, e):
            sku = int(self.l_sku[gi])
            qty = int(self.l_qty[gi])
            have = int(atp[sku].sum())
            if have >= qty:
                continue
            # Accumulate scheduled receipts (soonest first) until the shortfall is covered.
            reached = None
            for eta, q, _wh in sorted(self._inbound_sched.get(sku, ()), key=lambda x: x[0]):
                have += int(q)
                if have >= qty:
                    reached = eta
                    break
            d = (max(0, math.ceil((reached - t) / _MIN_PER_DAY)) if reached is not None
                 else self._supplier_lead)
            delay = max(delay, d)
        return delay

    def _reject_promise(self, t: int, oi: int) -> None:
        """Order refused at checkout because the promised date was too far out."""
        self.st_promise_rejected += 1
        s, e = int(self.o_start[oi]), int(self.o_end[oi])
        self.journal.log(t, JType.PROMISE_REJECTED, order_id=oi,
                         qty=int(e - s), promised_at=int(self.o_promise[oi]))
        for gi in range(s, e):
            self.l_term[gi] = 2
            penalty = float(self.cfg.shortage.penalty_lost_sale)
            self.st_lost += 1
            self.st_penalty += penalty
            self.journal.log(
                t, JType.LOST_SALE, order_id=oi, line_id=int(self.l_lineid[gi]),
                sku=int(self.l_sku[gi]), parcel=int(CancelPoint.CHECKOUT),
                qty=int(self.l_qty[gi]), cost=penalty,
            )

    def _make_order(self, oi: int) -> Order:
        s, e = int(self.o_start[oi]), int(self.o_end[oi])
        # Skip lines already terminated at an earlier stage (e.g. checkout shortage).
        lines = tuple(
            OrderLine(line_id=int(self.l_lineid[i]), sku_id=int(self.l_sku[i]), qty=int(self.l_qty[i]))
            for i in range(s, e) if self.l_term[i] == 0
        )
        return Order(
            order_id=oi,
            region_id=int(self.o_region[oi]),
            zone_hint=0,
            dest_x=float(self.o_dest_x[oi]),
            dest_y=float(self.o_dest_y[oi]),
            created_min=int(self.o_created[oi]),
            promise_min=int(self.o_promise[oi]),
            lines=lines,
        )

    def _on_sourcing(self, t: int, oi: int) -> None:
        import time

        order = self._make_order(oi)
        atp = self._atp()
        # Warehouse outage: hide the downed warehouse from policies within the window.
        if self._outage_wh >= 0 and self._outage_start <= t < self._outage_end:
            atp = atp.copy()
            atp[:, self._outage_wh] = 0
        ctx = PolicyContext(
            now_min=t,
            now_day=t // _MIN_PER_DAY,
            book_onhand=self.inv.book,
            reserved=self.inv.reserved,
            atp=atp,
            in_transit=self.inv.in_transit,
            safety_stock=self.inv.safety,
            wh_load=(self.wh_lines_used / np.clip(self.net.capacity_lines, 1, None)).astype(np.float32),
            wh_rank=self.net.wh_rank.astype(np.float32),
            rng=make_generator(self.seed, "policy", (oi,)),
        )
        t0 = time.perf_counter()
        decision = self.sourcing.decide(order, ctx)
        policy_ms = (time.perf_counter() - t0) * 1000.0

        if not isinstance(decision, Assignment):
            # Defer is not supported by policies in v1 — treat as no assignment.
            decision = Assignment(order_id=oi, lines=(), unassigned_lines=tuple(
                ln.line_id for ln in order.lines
            ))

        # Deferred sourcing: rather than act now, park the whole order (no reservation) and wait
        # for an expected receipt, as long as the promise slack allows. wait_for_receipt waits on
        # unplaceable lines; wait_to_consolidate waits to avoid a split.
        if self._defer_mode == "wait_for_receipt":
            defer_cond = bool(decision.unassigned_lines)
        elif self._defer_mode == "wait_to_consolidate":
            defer_cond = decision.n_parcels > 1 and not decision.unassigned_lines
        else:
            defer_cond = False
        if defer_cond and oi not in self._deferred and self._try_defer(t, oi, order, decision):
            return

        s = int(self.o_start[oi])
        sh = self.cfg.shortage
        # p_accept_slow: rather than lose an unplaced line, the customer accepts an out-of-tolerance
        # (slow/expensive) warehouse. Place it at the cheapest warehouse with ATP, ignoring tolerance.
        if decision.unassigned_lines and sh.p_accept_slow > 0:
            atp = self._atp()
            zone = self.net.shipping.zone(self.net.distance_to(order.dest_x, order.dest_y))
            shipcost = self.net.shipping.cost[zone]
            gc3 = make_generator(self.seed, "customer_choice", (oi, 3))
            rescued = set()
            extra: list[LineAlloc] = []
            for lid in decision.unassigned_lines:
                gi = s + lid
                sku = int(self.l_sku[gi])
                qty = int(self.l_qty[gi])
                feasible = np.nonzero((self.net.assortment[sku] > 0) & (atp[sku] >= qty))[0]
                if feasible.size and float(gc3.random()) < sh.p_accept_slow:
                    wh = int(feasible[int(np.argmin(shipcost[feasible]))])
                    self.l_wh[gi] = wh
                    atp[sku, wh] -= qty
                    rescued.add(lid)
                    extra.append(LineAlloc(line_id=lid, sku_id=sku, warehouse_id=wh, qty=qty))
            if rescued:
                decision = Assignment(
                    order_id=oi, lines=decision.lines + tuple(extra),
                    unassigned_lines=tuple(l for l in decision.unassigned_lines if l not in rescued),
                    est_ship_cost=decision.est_ship_cost, est_handling_cost=decision.est_handling_cost,
                )

        # Point 2 (post-order shortage): a line could not be sourced. With p_partial_ship the
        # order ships what's placed and cancels the rest; otherwise the whole order is cancelled.
        if decision.unassigned_lines:
            self.st_short_post += 1
            gc = make_generator(self.seed, "customer_choice", (oi, 1))
            partial = sh.p_partial_ship >= 1.0 or float(gc.random()) < sh.p_partial_ship
            if partial:
                cancel = list(decision.unassigned_lines)
            else:
                cancel = [int(self.l_lineid[gi]) for gi in range(s, int(self.o_end[oi]))
                          if self.l_term[gi] == 0]
                decision = Assignment(order_id=oi, lines=(), unassigned_lines=tuple(cancel))
            for lid in cancel:
                gi = s + lid
                if self.l_term[gi] != 0:
                    continue
                self.l_term[gi] = 2
                self.st_lost += 1
                self.st_penalty += float(sh.penalty_post_order)
                self.journal.log(
                    t, JType.LOST_SALE, order_id=oi, line_id=int(lid), sku=int(self.l_sku[gi]),
                    parcel=int(CancelPoint.POST_ORDER),
                    qty=int(self.l_qty[gi]), cost=float(sh.penalty_post_order),
                )
        self.journal.log(
            t, JType.ASSIGNED, order_id=oi, policy_time_ms=policy_ms,
            cost=float(decision.est_ship_cost + decision.est_handling_cost),
            promised_at=int(self.o_promise[oi]),
        )
        if not decision.lines:
            return
        # Assign warehouses to lines.
        for al in decision.lines:
            gi = s + al.line_id
            self.l_wh[gi] = al.warehouse_id
        self.queue.push(t, Ev.RESERVE, oi)

    # ------------------------------------------------------------------ deferred sourcing
    def _try_defer(self, t: int, oi: int, order, decision) -> bool:
        """Park an order that cannot be fully placed now, if the promise slack and expected
        receipts allow waiting. Holds no reservation (invariant). Returns True if deferred."""
        dist = self.net.distance_to(order.dest_x, order.dest_y)
        zone = self.net.shipping.zone(dist)
        best_transit = int(self.net.shipping.days[zone].min())
        fulfil_min = (best_transit + self.cfg.promise.min_processing_days) * _MIN_PER_DAY
        deadline = int(self.o_promise[oi]) - fulfil_min - self._defer_safety_min
        if t >= deadline:
            return False  # no slack left to wait
        s, e = int(self.o_start[oi]), int(self.o_end[oi])
        if decision.unassigned_lines:  # wait_for_receipt: every missing SKU needs a receipt in time
            needed = {int(self.l_sku[s + lid]) for lid in decision.unassigned_lines}
            require_all = True
        else:  # wait_to_consolidate: wait on any inbound that could let one warehouse cover all
            needed = {int(self.l_sku[gi]) for gi in range(s, e)}
            require_all = False
        # wake = when the order can plausibly proceed. wait_for_receipt: the LAST missing SKU's
        # receipt (all must be in). wait_to_consolidate: the FIRST inbound that might consolidate.
        sku_etas = []
        for sku in needed:
            etas = [e[0] for e in self._inbound_sched.get(sku, ()) if e[0] <= deadline]
            if etas and len(self._defer_waiters.get(sku, ())) < self._defer_max:
                sku_etas.append(min(etas))
            elif require_all:
                return False  # a missing SKU with no in-time receipt -> cannot wait
        if not sku_etas:
            return False  # nothing to wait for
        wake = max(sku_etas) if require_all else min(sku_etas)
        self._deferred[oi] = {"deadline": deadline, "needed": needed}
        for sku in needed:
            self._defer_waiters.setdefault(sku, set()).add(oi)
        if oi not in self._was_deferred:
            self._was_deferred.add(oi)
            self.st_deferred += 1
        self.journal.log(t, JType.DEFERRED, order_id=oi,
                         qty=len(decision.unassigned_lines), promised_at=int(self.o_promise[oi]))
        self.queue.push(min(wake, deadline), Ev.DEFER_WAKE, oi)
        return True

    def _find_phys_wh(self, sku: int, qty: int, exclude: int) -> int | None:
        """Another in-assortment warehouse that physically holds >= qty (richest first)."""
        phys = self.inv.phys[sku]
        best, best_av = None, qty - 1
        for wh2 in range(self.net.n_wh):
            if wh2 == exclude or self.net.assortment[sku, wh2] <= 0:
                continue
            av = int(phys[wh2])
            if av > best_av:
                best, best_av = wh2, av
        return best

    def _next_inbound_at(self, sku: int, wh: int, t: int, promise: int) -> int | None:
        """Earliest inbound receipt ETA for (sku, wh) after t and within the promise/horizon."""
        horizon = self.cfg.sim_days * _MIN_PER_DAY
        limit = min(promise, horizon)
        best = None
        for eta, _q, w in self._inbound_sched.get(sku, ()):
            if w == wh and t < eta <= limit:
                best = eta if best is None else min(best, eta)
        return best

    def _unpark(self, oi: int) -> None:
        info = self._deferred.pop(oi, None)
        if info is not None:
            for sku in info["needed"]:
                w = self._defer_waiters.get(sku)
                if w:
                    w.discard(oi)

    def _on_defer_wake(self, t: int, oi: int) -> None:
        """A deferred order's wait expired (deadline, or a scheduled wake) -> re-source now.
        If still short and past the deadline, _try_defer declines and the order is finalized."""
        if oi not in self._deferred:
            return  # already woken by a receipt
        self._unpark(oi)
        self.queue.push(t, Ev.SOURCING_DECISION, oi)

    def _arb_key(self, oi: int):
        """Arbitration order for deferred waiters claiming a fresh receipt."""
        if self._defer_arb == "fifo":
            return int(self.o_created[oi])
        if self._defer_arb == "value":
            s, e = int(self.o_start[oi]), int(self.o_end[oi])
            return -int(self.l_qty[s:e].sum())  # higher-value (bigger) orders first
        return self._deferred[oi]["deadline"]  # edf: earliest deadline (least slack) first

    def _wake_waiters_for_sku(self, t: int, sku: int) -> None:
        """On a receipt, re-source deferred orders needing this SKU in arbitration order, so an
        earlier-deadline order reserves the arriving stock before a later one."""
        waiters = self._defer_waiters.get(sku)
        if not waiters:
            return
        ready = sorted((oi for oi in list(waiters) if oi in self._deferred), key=self._arb_key)
        for oi in ready:
            self._unpark(oi)
            self.queue.push(t, Ev.SOURCING_DECISION, oi)

    def _on_reserve(self, t: int, oi: int) -> None:
        s, e = int(self.o_start[oi]), int(self.o_end[oi])
        by_wh: dict[int, list[int]] = {}
        for gi in range(s, e):
            wh = int(self.l_wh[gi])
            if wh < 0:
                continue
            by_wh.setdefault(wh, []).append(gi)
        if not by_wh:
            return
        # Atomic reservation (single-threaded: ATP already accounted for at decision time).
        for wh, lines in by_wh.items():
            for gi in lines:
                if self._reserve:
                    self.inv.reserved[self.l_sku[gi], wh] += self.l_qty[gi]
                self._demand_cum[self.l_sku[gi], wh] += self.l_qty[gi]  # realized demand per cell
                # Record the line -> warehouse assignment for the inspector.
                self.journal.log(
                    t, JType.ASSIGNED_LINE, order_id=oi, line_id=int(self.l_lineid[gi]),
                    sku=int(self.l_sku[gi]), warehouse=wh, qty=int(self.l_qty[gi]),
                )
            self.wh_wip[wh] += 1
        parcels = [(wh, lines) for wh, lines in sorted(by_wh.items())]
        self._parcels[oi] = parcels
        for pidx in range(len(parcels)):
            self.queue.push(t, Ev.WH_QUEUE_ADMIT, oi, pidx)

    def _on_admit(self, t: int, oi: int, pidx: int) -> None:
        wh, lines = self._parcels[oi][pidx]
        n_lines = len(lines)
        day = t // _MIN_PER_DAY
        tod = t % _MIN_PER_DAY
        cutoff = int(self.net.cutoff_min[wh])
        over_cap = self.wh_lines_used[wh] + n_lines > self.net.capacity_lines[wh]
        if tod >= cutoff or over_cap:
            self.queue.push((day + 1) * _MIN_PER_DAY, Ev.WH_QUEUE_ADMIT, oi, pidx)
            return
        self.wh_lines_used[wh] += n_lines
        pt = self.cfg.process_times
        base = pt.pick_median_min + pt.pick_per_line_min * n_lines
        g = make_generator(self.seed, "process_times", (oi, pidx, 1))
        pick_min = max(1, int(round(g.lognormal(math.log(base), pt.sigma))))
        self.queue.push(t + pick_min, Ev.PICK_DONE, oi, pidx)

    def _on_pick(self, t: int, oi: int, pidx: int) -> None:
        wh, lines = self._parcels[oi][pidx]
        sh = self.cfg.shortage
        kept: list[int] = []
        for gi in lines:
            sku = int(self.l_sku[gi])
            qty = int(self.l_qty[gi])
            avail = int(self.inv.phys[sku, wh])
            if avail >= qty:
                self.inv.phys[sku, wh] -= qty
                self.inv.book[sku, wh] -= qty
                if self._reserve:
                    self.inv.reserved[sku, wh] -= qty
                kept.append(gi)
            else:
                # Point 3 (pick shortage): physical < book (phantom inventory, or a conflict when
                # reservation is off). Try re-sourcing / backorder, else ship what's there or cancel.
                self.st_short_pick += 1
                if self._reserve:
                    self.inv.reserved[sku, wh] -= qty  # release our reservation
                self.inv.book[sku, wh] = int(self.inv.phys[sku, wh])  # reconcile the phantom here
                gc = make_generator(self.seed, "customer_choice", (oi, pidx, int(self.l_lineid[gi])))
                # (a) re-source the line from another warehouse that physically has it.
                if sh.p_resource > 0 and float(gc.random()) < sh.p_resource:
                    wh2 = self._find_phys_wh(sku, qty, wh)
                    if wh2 is not None:
                        if self._reserve:
                            self.inv.reserved[sku, wh2] += qty
                        self.wh_wip[wh2] += 1
                        self._parcels[oi].append((wh2, [gi]))
                        self.queue.push(t, Ev.WH_QUEUE_ADMIT, oi, len(self._parcels[oi]) - 1)
                        continue
                # (b) backorder: hold the line and wait for an inbound receipt at this warehouse.
                if sh.backorder:
                    eta = self._next_inbound_at(sku, wh, t, int(self.o_promise[oi]))
                    if eta is not None:
                        if self._reserve:
                            self.inv.reserved[sku, wh] += qty
                        self.wh_wip[wh] += 1
                        self._parcels[oi].append((wh, [gi]))
                        self.queue.push(eta + 1, Ev.WH_QUEUE_ADMIT, oi, len(self._parcels[oi]) - 1)
                        continue
                partial = avail > 0 and (sh.p_partial_ship >= 1.0 or float(gc.random()) < sh.p_partial_ship)
                ship_units = avail if partial else 0
                if ship_units > 0:
                    self.inv.phys[sku, wh] -= ship_units
                    self.l_qty[gi] = ship_units  # ship fewer units; the rest is a lost sale
                    kept.append(gi)
                else:
                    self.l_term[gi] = 2
                self.inv.book[sku, wh] = int(self.inv.phys[sku, wh])  # correct the phantom
                self.st_lost += 1
                self.st_penalty += float(sh.penalty_pick)
                self.journal.log(
                    t, JType.LOST_SALE, order_id=oi, line_id=int(self.l_lineid[gi]), sku=sku,
                    warehouse=wh, parcel=int(CancelPoint.PICK),
                    qty=int(qty - ship_units), cost=float(sh.penalty_pick),
                )
            if self.inv.book[sku, wh] <= 0:
                self.journal.log(t, JType.STOCKOUT, sku=sku, warehouse=wh)
        if not kept:
            self.wh_wip[wh] = max(0, self.wh_wip[wh] - 1)  # parcel dissolved before shipping
            return
        self._parcels[oi][pidx] = (wh, kept)
        pt = self.cfg.process_times
        g = make_generator(self.seed, "process_times", (oi, pidx, 2))
        pack_min = max(1, int(round(g.lognormal(math.log(pt.pack_median_min), pt.sigma))))
        self.queue.push(t + pack_min, Ev.PACK_DONE, oi, pidx)

    def _on_ship(self, t: int, oi: int, pidx: int) -> None:
        wh, lines = self._parcels[oi][pidx]
        day = t // _MIN_PER_DAY
        if self.wh_parcels_used[wh] + 1 > self.net.carrier_parcels[wh]:
            self.queue.push((day + 1) * _MIN_PER_DAY, Ev.SHIP, oi, pidx)
            return
        self.wh_parcels_used[wh] += 1
        self.wh_wip[wh] = max(0, self.wh_wip[wh] - 1)
        self.wh_outbound_tick[wh] += 1
        self.st_parcels += 1
        dist = float(self.net.distance_to(float(self.o_dest_x[oi]), float(self.o_dest_y[oi]))[wh])
        zone = int(self.net.shipping.zone(np.array([dist]))[0])
        weight = float(self.net.sku_weight[[self.l_sku[gi] for gi in lines]].sum())
        ship_cost = float(self.net.shipping.parcel_cost(np.array([zone]), np.array([weight]))[0])
        handling = float(self.net.handling[wh] * len(lines))
        cutoff = int(self.net.cutoff_min[wh])
        pack_after_cutoff = 1 if (t % _MIN_PER_DAY) >= cutoff else 0
        transit_days = int(self.net.shipping.days[zone]) + pack_after_cutoff
        self.st_shipping += ship_cost + handling
        # Delivery noise (v1: 0 by default).
        noise = 0
        if self.net.shipping.noise_mean > 0:
            g = make_generator(self.seed, "delivery_noise", (oi, pidx))
            noise = int(g.geometric(1.0 / (1.0 + self.net.shipping.noise_mean))) - 1
        deliver_min = t + (transit_days + noise) * _MIN_PER_DAY
        self.journal.log(
            t, JType.SHIPPED, order_id=oi, warehouse=wh, parcel=pidx, qty=len(lines),
            cost=ship_cost + handling, promised_at=int(self.o_promise[oi]),
            planned_at=deliver_min,
        )
        self.queue.push(deliver_min, Ev.DELIVER, oi, pidx)

    def _on_deliver(self, t: int, oi: int, pidx: int) -> None:
        wh, lines = self._parcels[oi][pidx]
        for gi in lines:
            self.l_term[gi] = 1
        self.st_delivered += 1
        promise = int(self.o_promise[oi])
        if t <= promise:
            self.st_on_time += 1
        elif self._late_rate > 0:
            late_days = math.ceil((t - promise) / _MIN_PER_DAY)
            # A deferred order that still misses its promise is penalized harder: the network
            # chose to wait on an expected receipt and the bet did not pay off (2.10).
            mult = self._defer_miss if oi in self._was_deferred else 1.0
            cost = late_days * self._late_rate * mult
            self.st_lateness += cost
            self.journal.log(
                t, JType.LATE_PENALTY, order_id=oi, warehouse=wh, parcel=pidx, cost=float(cost)
            )
        self.journal.log(
            t, JType.DELIVERED, order_id=oi, warehouse=wh, parcel=pidx, qty=len(lines),
            promised_at=int(self.o_promise[oi]), delivered_at=t,
        )

    def _policy_ctx(self, t: int) -> PolicyContext:
        """Order-less policy context (for periodic callbacks like LP recompute)."""
        return PolicyContext(
            now_min=t,
            now_day=t // _MIN_PER_DAY,
            book_onhand=self.inv.book,
            reserved=self.inv.reserved,
            atp=self._atp(),
            in_transit=self.inv.in_transit,
            safety_stock=self.inv.safety,
            wh_load=(self.wh_lines_used / np.clip(self.net.capacity_lines, 1, None)).astype(np.float32),
            wh_rank=self.net.wh_rank.astype(np.float32),
            rng=make_generator(self.seed, "policy", (-1, t)),
        )

    def _on_day_roll(self, t: int, day: int) -> None:
        self.wh_lines_used[:] = 0
        self.wh_parcels_used[:] = 0
        # Periodic policy callback (e.g. LP bid-price recompute).
        if self._period_days and day % self._period_days == 0:
            self.sourcing.on_period_start(self._policy_ctx(t))
        # Holding: charge per warehouse.
        per_wh_units = self.inv.phys.sum(axis=0)
        for wh in range(self.net.n_wh):
            cost = float(per_wh_units[wh] * self.net.storage_cost[wh])
            if cost > 0:
                self.st_holding += cost
                self.journal.log(t, JType.HOLDING, warehouse=wh, cost=cost)
            stockout = int(((self.inv.book[:, wh] <= 0) & (self.world.assortment[:, wh] > 0)).sum())
            self._inv_daily.append(
                {"day": day, "warehouse": wh, "units": int(per_wh_units[wh]), "stockout_skus": stockout}
            )
        if self.inventory_detail:
            self._inv_sku_daily.append((day, self.inv.book.copy(), self.inv.phys.copy()))

    def _on_replenish(self, t: int, day: int) -> None:
        position = self.inv.position()
        # Realized demand rate per (sku, wh): units/day observed so far (0 during warmup day 0).
        elapsed = max(1, day)
        demand_rate = self._demand_cum / elapsed
        qty = self.replenishment.order_quantities(
            position, self.inv.s_level, self.inv.S_level, demand_rate=demand_rate
        )
        qty = qty * (self.world.assortment > 0)  # only within assortment
        sku_idx, wh_idx = np.nonzero(qty > 0)
        for sku, wh in zip(sku_idx.tolist(), wh_idx.tolist()):
            q = int(qty[sku, wh])
            if q < self._supplier_moq:
                q = self._supplier_moq
            po_index = len(self._po)
            self._po.append((sku, wh, q, self._supplier_price))
            self.inv.in_transit[sku, wh] += q
            eta = t + self._supplier_lead * _MIN_PER_DAY
            if self._track_inbound:
                self._inbound_sched.setdefault(sku, []).append([eta, q, wh])
            self.journal.log(
                t, JType.REPLENISH_ORDERED, sku=sku, warehouse=wh, qty=q,
            )
            self.queue.push(eta, Ev.RECEIPT, -1, po_index)

    def _on_receipt(self, t: int, po_index: int) -> None:
        sku, wh, qty, price = self._po[po_index]
        # Inventory accuracy: with prob (1-accuracy) the record is overstated — physical arrives
        # short of book by a geometric error (mean error_mean). The gap is hidden until pick.
        phys_add = qty
        if self._inv_accuracy < 1.0:
            ge = make_generator(self.seed, "inventory_error", (int(sku), int(wh), po_index))
            if float(ge.random()) > self._inv_accuracy:
                err = int(ge.geometric(1.0 / (1.0 + self._inv_err_mean)))
                phys_add = max(0, qty - err)
        self.inv.book[sku, wh] += qty
        self.inv.phys[sku, wh] += phys_add
        self.inv.in_transit[sku, wh] -= qty
        self.st_purchase += float(qty * price)
        self.wh_inbound_tick[wh] += qty
        self.journal.log(
            t, JType.RECEIVED, sku=sku, warehouse=wh, qty=qty, cost=float(qty * price)
        )
        # Drop this receipt from the inbound schedule.
        if self._track_inbound:
            sched = self._inbound_sched.get(sku)
            if sched:
                for i, (eta, q, w) in enumerate(sched):
                    if eta == t and w == wh and q == qty:
                        sched.pop(i)
                        break
        # Deferred sourcing: re-source parked orders waiting on this SKU.
        if self._defer_mode != "none":
            self._wake_waiters_for_sku(t, sku)

    def _write_inventory_daily(self, run_dir: Path) -> None:
        if self._inv_daily:
            pd.DataFrame(self._inv_daily).to_parquet(run_dir / "inventory_daily.parquet", index=False)
        if self._inv_sku_daily:
            self._write_inventory_sku_daily(run_dir)

    def _write_inventory_sku_daily(self, run_dir: Path) -> None:
        """Long table (day, sku, warehouse, book, phys) over assortment cells only."""
        assort = self.world.assortment > 0
        sku_idx, wh_idx = np.nonzero(assort)  # fixed cell order across days
        days, skus, whs, books, physes = [], [], [], [], []
        for day, book, phys in self._inv_sku_daily:
            n = sku_idx.shape[0]
            days.append(np.full(n, day, np.int32))
            skus.append(sku_idx.astype(np.int64))
            whs.append(wh_idx.astype(np.int16))
            books.append(book[sku_idx, wh_idx].astype(np.int32))
            physes.append(phys[sku_idx, wh_idx].astype(np.int32))
        pd.DataFrame(
            {
                "day": np.concatenate(days),
                "sku": np.concatenate(skus),
                "warehouse": np.concatenate(whs),
                "book": np.concatenate(books),
                "phys": np.concatenate(physes),
            }
        ).to_parquet(run_dir / "inventory_sku_daily.parquet", index=False)
