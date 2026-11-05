"""Data access for the inspector: orders on the map, per-order / per-warehouse / per-SKU views."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from ..journal.schema import JTYPE_NAMES, JType

_MIN_PER_DAY = 1440


class ReplayData:
    """Reads a run_dir journal plus world/orders and serves the inspector views."""

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        self.events = pl.read_parquet(self.run_dir / "events.parquet")
        scen = self.run_dir.parent
        self.orders = pl.read_parquet(scen / "orders.parquet")
        self.warehouses = pl.read_parquet(scen / "warehouses.parquet")
        self.regions = pl.read_parquet(scen / "regions.parquet")
        self.skus = pl.read_parquet(scen / "skus.parquet")
        inv = self.run_dir / "inventory_sku_daily.parquet"
        self.inv_sku = pl.read_parquet(inv) if inv.exists() else None

        # One row per order: destination, created day, chosen warehouses, lost flag.
        self._orders_tbl = self._build_orders_table()

    # ------------------------------------------------------------------ setup
    def _build_orders_table(self) -> pl.DataFrame:
        base = (
            self.orders.group_by("order_id").agg(
                pl.col("dest_x").first(),
                pl.col("dest_y").first(),
                pl.col("region_id").first(),
                pl.col("created_min").first(),
                pl.len().alias("n_lines"),
            )
        ).with_columns((pl.col("created_min") // _MIN_PER_DAY).alias("day"))

        al = self.events.filter(pl.col("type") == int(JType.ASSIGNED_LINE))
        wh_per_order = (
            al.group_by("order_id").agg(pl.col("warehouse").n_unique().alias("n_wh"),
                                        pl.col("warehouse").min().alias("first_wh"))
        )
        lost_ev = self.events.filter(pl.col("type") == int(JType.LOST_SALE))
        lost = lost_ev.group_by("order_id").agg(
            pl.len().alias("lost_lines"), pl.col("cost").sum().alias("lost_cost")
        )

        # Delivered lines per order: a line is delivered iff its (order, warehouse) parcel has a
        # DELIVERED event. Join assigned lines against delivered parcels to count them, and track
        # the last delivery day + whether any parcel landed after the promise (-> late).
        delivered_parcels = (
            self.events.filter(pl.col("type") == int(JType.DELIVERED))
            .with_columns((pl.col("t") // _MIN_PER_DAY).alias("deliver_day"))
            .group_by(["order_id", "warehouse"]).agg(pl.col("deliver_day").max())
        )
        al_deliv = al.join(delivered_parcels, on=["order_id", "warehouse"], how="left")
        deliv = al_deliv.group_by("order_id").agg(
            pl.col("deliver_day").is_not_null().sum().alias("delivered_lines"),
            pl.col("deliver_day").max().alias("last_deliver_day"),
        )
        # Last delivery time (minutes) for minute-resolution lateness magnitude.
        last_deliv_min = (
            self.events.filter(pl.col("type") == int(JType.DELIVERED))
            .group_by("order_id").agg(pl.col("t").max().alias("last_deliver_min"))
        )

        # Promise day/min + realized cost to the company (planned sourcing cost + lost-sale
        # penalty; shipping is already inside the ASSIGNED estimate, so it is not double-counted).
        promise = (
            self.events.filter(pl.col("promised_at") >= 0)
            .group_by("order_id").agg(
                (pl.col("promised_at").min() // _MIN_PER_DAY).alias("promise_day"),
                pl.col("promised_at").min().alias("promise_min"),
            )
        )
        assign_cost = (
            self.events.filter(pl.col("type") == int(JType.ASSIGNED))
            .group_by("order_id").agg(pl.col("cost").sum().alias("assign_cost"))
        )

        tbl = (
            base.join(wh_per_order, on="order_id", how="left")
            .join(lost, on="order_id", how="left")
            .join(deliv, on="order_id", how="left")
            .join(last_deliv_min, on="order_id", how="left")
            .join(promise, on="order_id", how="left")
            .join(assign_cost, on="order_id", how="left")
        ).with_columns(
            pl.col("n_wh").fill_null(0), pl.col("first_wh").fill_null(-1),
            pl.col("lost_lines").fill_null(0), pl.col("lost_cost").fill_null(0.0),
            pl.col("delivered_lines").fill_null(0), pl.col("assign_cost").fill_null(0.0),
        )
        fully_delivered = pl.col("delivered_lines") == pl.col("n_lines")
        tbl = tbl.with_columns(
            (pl.col("lost_lines") > 0).alias("has_lost"),
            (pl.col("assign_cost") + pl.col("lost_cost")).alias("cost"),
            # Lateness magnitude in days (delivered_at - promised_at), only for fully delivered
            # orders; null otherwise. Can be negative (early) / 0 (on time) / positive (late).
            pl.when(fully_delivered & pl.col("promise_min").is_not_null())
            .then((pl.col("last_deliver_min") - pl.col("promise_min")).cast(pl.Float64) / _MIN_PER_DAY)
            .otherwise(None).alias("lateness_days"),
        )
        tbl = tbl.with_columns(
            # late: fully delivered and the last parcel landed after the promise.
            (fully_delivered & (pl.col("lateness_days") > 0)).alias("is_late"),
        )
        return tbl

    # ------------------------------------------------------------------ meta
    def meta(self) -> dict:
        d0, d1 = self.day_range()
        return {
            "type": "meta",
            "wh_names": self.warehouses["name"].to_list(),
            "wh_x": self.warehouses["x"].to_list(),
            "wh_y": self.warehouses["y"].to_list(),
            "region_names": self.regions["name"].to_list(),
            "region_x": self.regions["x"].to_list(),
            "region_y": self.regions["y"].to_list(),
            "n_sku": self.skus.height,
            "day_min": d0,
            "day_max": d1,
            "has_inventory_detail": self.inv_sku is not None,
        }

    def day_range(self) -> tuple[int, int]:
        t = self.events["t"]
        if t.len() == 0:
            return 0, 0
        return 0, int(t.max() // _MIN_PER_DAY)

    # ------------------------------------------------------------------ map / day
    def orders_on_day(self, day: int, limit: int = 2000) -> dict:
        """Order points created on `day`: location, chosen warehouse, status."""
        rows = self._orders_tbl.filter(pl.col("day") == day).head(limit)
        orders = []
        for r in rows.iter_rows(named=True):
            nl, dl = int(r["n_lines"]), int(r["delivered_lines"])
            if dl == 0:
                # Nothing delivered: fully lost (never sourced) vs still stuck in the pipeline.
                status = "lost" if int(r["n_wh"]) == 0 else "pending"
            elif dl >= nl:
                status = "late" if r["is_late"] else "delivered"
            else:
                status = "partial"  # closed with incomplete delivery (some lines lost/unshipped)
            lateness = r["lateness_days"]
            orders.append(
                {
                    "order_id": int(r["order_id"]),
                    "x": float(r["dest_x"]), "y": float(r["dest_y"]),
                    "wh": int(r["first_wh"]), "n_wh": int(r["n_wh"]),
                    "n_lines": nl, "delivered_lines": dl,
                    "status": status, "split": int(r["n_wh"]) > 1,
                    "cost": round(float(r["cost"]), 2),
                    "lateness_days": None if lateness is None else round(float(lateness), 2),
                }
            )
        wh_stock = self._wh_total_stock(day)
        costs = [o["cost"] for o in orders]
        cost_range = [min(costs), max(costs)] if costs else [0.0, 0.0]
        lates = [o["lateness_days"] for o in orders if o["lateness_days"] is not None]
        lateness_range = [min(lates), max(lates)] if lates else [0.0, 0.0]
        return {"type": "day", "day": day, "orders": orders, "wh_stock": wh_stock,
                "n_orders": rows.height, "cost_range": cost_range,
                "lateness_range": lateness_range}

    def _wh_total_stock(self, day: int) -> list[int]:
        n_wh = self.warehouses.height
        stock = [0] * n_wh
        if self.inv_sku is None:
            return stock
        d = self.inv_sku.filter(pl.col("day") == day).group_by("warehouse").agg(
            pl.col("book").sum()
        )
        for row in d.iter_rows(named=True):
            w = int(row["warehouse"])
            if 0 <= w < n_wh:
                stock[w] = int(row["book"])
        return stock

    # ------------------------------------------------------------------ order
    def order_detail(self, order_id: int) -> dict:
        orow = self._orders_tbl.filter(pl.col("order_id") == order_id)
        if orow.height == 0:
            return {"type": "order", "order_id": order_id, "found": False}
        o = orow.row(0, named=True)

        ev = self.events.filter(pl.col("order_id") == order_id).sort("t")

        # Promise (minutes -> day): the first non-empty promised_at on any event for the order.
        promise_day = None
        if "promised_at" in ev.columns:
            pr = ev.filter(pl.col("promised_at") >= 0)
            if pr.height:
                promise_day = int(pr.row(0, named=True)["promised_at"] // _MIN_PER_DAY)

        # A warehouse ships an order as one parcel; reconcile each parcel's fate from the
        # SHIPPED / DELIVERED events so a line's status reflects what actually happened to it
        # (a line can be assigned but stuck in the pick queue and never shipped by horizon).
        shipped_wh: dict[int, dict] = {}
        delivered_wh: dict[int, int] = {}
        for r in ev.filter(pl.col("type") == int(JType.SHIPPED)).iter_rows(named=True):
            shipped_wh[int(r["warehouse"])] = {
                "day": int(r["t"] // _MIN_PER_DAY),
                "cost": None if r["cost"] is None else round(float(r["cost"]), 3),
            }
        for r in ev.filter(pl.col("type") == int(JType.DELIVERED)).iter_rows(named=True):
            delivered_wh[int(r["warehouse"])] = int(r["t"] // _MIN_PER_DAY)

        def _line_status(wh: int) -> str:
            if wh in delivered_wh:
                return "delivered"
            if wh in shipped_wh:
                return "in_transit"
            return "pending"  # assigned but not shipped (queued / backlog at horizon)

        al = ev.filter(pl.col("type") == int(JType.ASSIGNED_LINE))
        lost = ev.filter(pl.col("type") == int(JType.LOST_SALE))
        lines = []
        for r in al.iter_rows(named=True):
            wh = int(r["warehouse"])
            lines.append({"line_id": int(r["line_id"]), "sku": int(r["sku"]),
                          "qty": int(r["qty"]), "wh": wh, "status": _line_status(wh)})
        for r in lost.iter_rows(named=True):
            lines.append({"line_id": int(r["line_id"]), "sku": int(r["sku"]),
                          "qty": int(r["qty"]), "wh": None, "status": "lost"})
        lines.sort(key=lambda x: x["line_id"])

        # Per-warehouse parcels with their delivery outcome.
        parcels = []
        for wh in sorted({l["wh"] for l in lines if l["wh"] is not None}):
            plines = [l for l in lines if l["wh"] == wh]
            dday = delivered_wh.get(wh)
            parcels.append({
                "wh": wh, "n_lines": len(plines), "units": sum(l["qty"] for l in plines),
                "shipped_day": shipped_wh.get(wh, {}).get("day"),
                "delivered_day": dday,
                "ship_cost": shipped_wh.get(wh, {}).get("cost"),
                "status": _line_status(wh),
                "on_time": None if (dday is None or promise_day is None) else (dday <= promise_day),
            })

        # Aggregate so a glance answers "ordered N, delivered M, where's the rest?".
        def _agg(statuses):
            sel = [l for l in lines if l["status"] in statuses]
            return {"lines": len(sel), "units": sum(l["qty"] for l in sel)}
        assign_cost = None
        ac = ev.filter(pl.col("type") == int(JType.ASSIGNED))
        if ac.height and ac.row(0, named=True)["cost"] is not None:
            assign_cost = round(float(ac.row(0, named=True)["cost"]), 3)
        summary = {
            "ordered": {"lines": len(lines), "units": sum(l["qty"] for l in lines)},
            "delivered": _agg({"delivered"}), "in_transit": _agg({"in_transit"}),
            "pending": _agg({"pending"}), "lost": _agg({"lost"}),
            "assign_cost": assign_cost,
            "ship_cost": round(sum(p["ship_cost"] for p in parcels if p["ship_cost"]), 3),
            "promise_day": promise_day,
        }

        stages = [
            {
                "t": int(r["t"]), "day": int(r["t"] // _MIN_PER_DAY),
                "event": JTYPE_NAMES.get(int(r["type"]), str(r["type"])),
                "wh": int(r["warehouse"]) if r["warehouse"] is not None and r["warehouse"] >= 0 else None,
                "qty": int(r["qty"]) if r["qty"] is not None and r["qty"] >= 0 else None,
                "cost": None if r["cost"] is None else round(float(r["cost"]), 3),
            }
            for r in ev.iter_rows(named=True)
            if int(r["type"]) != int(JType.ASSIGNED_LINE)
        ]
        return {
            "type": "order", "order_id": int(order_id), "found": True,
            "x": float(o["dest_x"]), "y": float(o["dest_y"]),
            "region": int(o["region_id"]), "day": int(o["day"]),
            "promise_day": promise_day,
            "warehouses": sorted({l["wh"] for l in lines if l["wh"] is not None}),
            "lines": lines, "parcels": parcels, "summary": summary, "stages": stages,
        }

    # ------------------------------------------------------------------ warehouse
    def warehouse_detail(self, wh: int, day: int) -> dict:
        name = self.warehouses["name"][wh] if wh < self.warehouses.height else f"W{wh}"
        # Stock total over time.
        stock_days, stock_vals = [], []
        top = []
        if self.inv_sku is not None:
            tot = (
                self.inv_sku.filter(pl.col("warehouse") == wh)
                .group_by("day").agg(pl.col("book").sum()).sort("day")
            )
            stock_days = tot["day"].to_list()
            stock_vals = tot["book"].to_list()
            at_day = (
                self.inv_sku.filter((pl.col("warehouse") == wh) & (pl.col("day") == day))
                .sort("book", descending=True).head(20)
            )
            top = [{"sku": int(r["sku"]), "book": int(r["book"])} for r in at_day.iter_rows(named=True)]
        # Shipped parcels per day and received units per day.
        ship = (
            self.events.filter((pl.col("type") == int(JType.SHIPPED)) & (pl.col("warehouse") == wh))
            .with_columns((pl.col("t") // _MIN_PER_DAY).alias("d")).group_by("d").len().sort("d")
        )
        recv = (
            self.events.filter((pl.col("type") == int(JType.RECEIVED)) & (pl.col("warehouse") == wh))
            .with_columns((pl.col("t") // _MIN_PER_DAY).alias("d"))
            .group_by("d").agg(pl.col("qty").sum()).sort("d")
        )
        # Assigned lines per day (inbound workload) vs shipped lines per day (throughput): the
        # gap reveals a pick-capacity backlog -- why assigned orders sit unshipped.
        al = self.events.filter(
            (pl.col("type") == int(JType.ASSIGNED_LINE)) & (pl.col("warehouse") == wh)
        ).with_columns((pl.col("t") // _MIN_PER_DAY).alias("d"))
        asg = al.group_by("d").len().sort("d")
        shipped_lines = (
            self.events.filter((pl.col("type") == int(JType.SHIPPED)) & (pl.col("warehouse") == wh))
            .with_columns((pl.col("t") // _MIN_PER_DAY).alias("d")).group_by("d")
            .agg(pl.col("qty").sum()).sort("d")  # qty on SHIPPED = lines in parcel
        )
        wrow = self.warehouses.filter(pl.col("warehouse_id") == wh)
        cap = int(wrow["capacity_lines_per_day"][0]) if wrow.height else None
        n_assigned = int(al.height)
        n_shipped_lines = int(shipped_lines["qty"].sum() or 0)
        delivered = self.events.filter(
            (pl.col("type") == int(JType.DELIVERED)) & (pl.col("warehouse") == wh)
        )
        n_delivered_lines = int(delivered["qty"].sum() or 0)
        summary = {
            "capacity_lines_per_day": cap,
            "assigned_lines": n_assigned,
            "shipped_lines": n_shipped_lines,
            "delivered_lines": n_delivered_lines,
            "backlog_lines": n_assigned - n_shipped_lines,  # assigned but never shipped by horizon
        }
        return {
            "type": "warehouse", "wh": int(wh), "name": name, "day": day,
            "stock_day": stock_days, "stock": stock_vals,
            "ship_day": ship["d"].to_list(), "ship": ship["len"].to_list(),
            "recv_day": recv["d"].to_list(), "recv": recv["qty"].to_list(),
            "assigned_day": asg["d"].to_list(), "assigned": asg["len"].to_list(),
            "shipped_lines_day": shipped_lines["d"].to_list(),
            "shipped_lines": shipped_lines["qty"].to_list(),
            "capacity": cap, "summary": summary,
            "top_sku": top,
        }

    # ------------------------------------------------------------------ sku
    def sku_detail(self, sku: int) -> dict:
        srow = self.skus.filter(pl.col("sku_id") == sku)
        cat = int(srow["category"][0]) if srow.height else None
        price = round(float(srow["price"][0]), 2) if srow.height else None
        # Stock per warehouse over time.
        series = {}
        if self.inv_sku is not None:
            s = self.inv_sku.filter(pl.col("sku") == sku).sort("day")
            for wh in sorted(s["warehouse"].unique().to_list()):
                w = s.filter(pl.col("warehouse") == wh)
                series[str(int(wh))] = {"day": w["day"].to_list(), "book": w["book"].to_list()}
        # Assigned units per day (demand actually sourced for this SKU).
        al = (
            self.events.filter((pl.col("type") == int(JType.ASSIGNED_LINE)) & (pl.col("sku") == sku))
            .with_columns((pl.col("t") // _MIN_PER_DAY).alias("d"))
            .group_by("d").agg(pl.col("qty").sum()).sort("d")
        )
        assigned_total = int(al["qty"].sum() or 0)
        lost = self.events.filter(
            (pl.col("type") == int(JType.LOST_SALE)) & (pl.col("sku") == sku)
        ).height
        return {
            "type": "sku", "sku": int(sku), "category": cat, "price": price,
            "stock_series": series,
            "assigned_day": al["d"].to_list(), "assigned": al["qty"].to_list(),
            "assigned_total": assigned_total, "lost_lines": lost,
        }
