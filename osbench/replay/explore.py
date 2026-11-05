"""Catalog views for the inspector: products, warehouses and orders as paginated lists.

Stock "as of day D" comes from the per-SKU inventory snapshot (``inventory_sku_daily``) when the
run recorded it. Large runs skip that table, so the book is then reconstructed from the journal:
initial stock (the same formula the engine uses) + receipts - shipments. Reservations
(assigned but not yet shipped units) always come from the journal, so

    ATP(D) = book(D) - reserved(D) - safety   (clipped at 0)

matches the engine's ``InventoryState.atp`` up to rare pick-shortage reconciliations.
"""

from __future__ import annotations

import json
import threading

import numpy as np
import polars as pl

from ..journal.schema import JType
from .loader import ReplayData

_MIN_PER_DAY = 1440
STATUSES = ("delivered", "late", "partial", "pending", "lost")


def _page(df: pl.DataFrame, offset: int, limit: int) -> tuple[pl.DataFrame, int]:
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    return df.slice(offset, limit), df.height


class Explorer:
    def __init__(self, data: ReplayData):
        self.d = data
        self.n_sku = data.skus.height
        self.n_wh = data.warehouses.height
        self._lock = threading.Lock()
        self._built = False
        self._stock_cache: dict[int, dict] = {}  # day -> stock matrices (slider scrubbing)

    # ------------------------------------------------------------------ setup (lazy)
    def _build(self) -> None:
        with self._lock:
            if self._built:
                return
            ev = self.d.events
            # Assigned lines with the time their parcel shipped / was delivered.
            al = ev.filter(pl.col("type") == int(JType.ASSIGNED_LINE)).select(
                "order_id", "line_id", "sku", "warehouse", "qty", pl.col("t").alias("assign_t"))
            shipped = (ev.filter(pl.col("type") == int(JType.SHIPPED))
                       .group_by(["order_id", "warehouse"]).agg(
                           pl.col("t").min().alias("ship_t"),
                           (pl.col("cost") / pl.col("qty").clip(1)).first().alias("ship_cost_line")))
            delivered = (ev.filter(pl.col("type") == int(JType.DELIVERED))
                         .group_by(["order_id", "warehouse"]).agg(pl.col("t").min().alias("deliver_t"),
                                                                  pl.col("qty").first().alias("_n")))
            late = (ev.filter(pl.col("type") == int(JType.LATE_PENALTY))
                    .group_by(["order_id", "warehouse"]).agg(pl.col("cost").sum().alias("late_cost")))
            lines = (al.join(shipped, on=["order_id", "warehouse"], how="left")
                     .join(delivered, on=["order_id", "warehouse"], how="left")
                     .join(late, on=["order_id", "warehouse"], how="left"))
            self.lines = lines.with_columns(
                (pl.col("late_cost").fill_null(0.0) / pl.col("_n").fill_null(1).clip(1))
                .alias("late_cost_line")).drop("late_cost", "_n")
            self.recv = ev.filter(pl.col("type") == int(JType.RECEIVED)).select(
                "t", "sku", "warehouse", "qty", "cost")
            self.lost = ev.filter(pl.col("type") == int(JType.LOST_SALE)).select(
                "t", "order_id", "sku", "qty", "cost")
            self._init_book, self._safety, self._safety_days = self._initial_stock()
            self._storage_cost = self.d.warehouses["storage_cost_per_unit_day"].to_numpy() \
                if "storage_cost_per_unit_day" in self.d.warehouses.columns else np.zeros(self.n_wh)
            ass = pl.read_parquet(self.d.run_dir.parent / "assortment.parquet")
            self.assort = np.zeros((self.n_sku, self.n_wh), dtype=bool)
            self.assort[ass["sku_id"].to_numpy(), ass["warehouse_id"].to_numpy()] = True
            # Demand as generated (what customers asked for, before substitution / sourcing).
            self.demand = self.d.orders.group_by("sku_id").agg(
                pl.len().alias("ordered_lines"), pl.col("order_id").n_unique().alias("ordered_orders"),
                pl.col("qty").sum().alias("ordered_units"))
            inv_daily = self.d.run_dir / "inventory_daily.parquet"
            self.inv_daily = pl.read_parquet(inv_daily) if inv_daily.exists() else None
            self._built = True

    def _initial_stock(self):
        """Initial book and safety stock [n_sku, n_wh], recomputed the way the engine does it."""
        from ..config.policy import ReplenishmentPolicyConfig
        from ..core.state import init_inventory
        from ..generate import load_world_from_dir

        rcfg = ReplenishmentPolicyConfig()
        pol = self.d.run_dir / "policy.json"  # written by the studio runner (run-time params)
        if pol.exists():
            try:
                rp = json.loads(pol.read_text("utf-8")).get("replenishment_params") or {}
                rcfg = ReplenishmentPolicyConfig(**{k: v for k, v in rp.items()
                                                    if k in ReplenishmentPolicyConfig.model_fields})
            except Exception:  # noqa: BLE001
                pass
        try:
            world = load_world_from_dir(self.d.run_dir.parent)
            lead = float(np.mean([s.lead_time_days for s in world.config.suppliers]))
            inv = init_inventory(world, lead, rcfg.review_days, rcfg.safety_days, rcfg.S_overshoot)
            return inv.book.astype(np.int64), inv.safety.astype(np.int64), rcfg.safety_days
        except Exception:  # noqa: BLE001 -- unknown initial stock: reconstruction falls back to 0
            z = np.zeros((self.n_sku, self.n_wh), np.int64)
            return z, z.copy(), rcfg.safety_days

    # ------------------------------------------------------------------ stock math
    def _acc(self, df: pl.DataFrame, qty: str = "qty") -> np.ndarray:
        m = np.zeros((self.n_sku, self.n_wh), np.int64)
        if df.height:
            g = df.group_by(["sku", "warehouse"]).agg(pl.col(qty).sum())
            np.add.at(m, (g["sku"].to_numpy(), g["warehouse"].to_numpy()), g[qty].to_numpy())
        return m

    def stock_at(self, day: int) -> dict:
        """book / reserved / safety / atp matrices [n_sku, n_wh] at the start of `day`."""
        self._build()
        if day in self._stock_cache:
            return self._stock_cache[day]
        t0 = int(day) * _MIN_PER_DAY
        shipped = self._acc(self.lines.filter(pl.col("ship_t") < t0))
        assigned = self._acc(self.lines.filter(pl.col("assign_t") < t0))
        reserved = np.clip(assigned - shipped, 0, None)
        source = "journal"
        book = None
        if self.d.inv_sku is not None:
            snap = self.d.inv_sku.filter(pl.col("day") == day)
            if snap.height:
                book = np.zeros((self.n_sku, self.n_wh), np.int64)
                book[snap["sku"].to_numpy(), snap["warehouse"].to_numpy()] = snap["book"].to_numpy()
                source = "snapshot"
        if book is None:
            book = self._init_book + self._acc(self.recv.filter(pl.col("t") < t0)) - shipped
            book = np.clip(book, 0, None)
        atp = np.clip(book - reserved - self._safety, 0, None)
        res = {"book": book, "reserved": reserved, "atp": atp, "source": source}
        if len(self._stock_cache) >= 8:
            self._stock_cache.pop(next(iter(self._stock_cache)))
        self._stock_cache[day] = res
        return res

    def _daily_book(self, sku: int) -> tuple[list[int], dict[int, list[int]], str]:
        """Per-warehouse daily book for one SKU (snapshot if recorded, else reconstructed)."""
        d0, d1 = self.d.day_range()
        days = list(range(d0, d1 + 1))
        out: dict[int, list[int]] = {}
        if self.d.inv_sku is not None:
            s = self.d.inv_sku.filter(pl.col("sku") == sku)
            for wh in range(self.n_wh):
                if not self.assort[sku, wh]:
                    continue
                w = s.filter(pl.col("warehouse") == wh)
                m = dict(zip(w["day"].to_list(), w["book"].to_list()))
                out[wh] = [int(m.get(d, 0)) for d in days]
            return days, out, "snapshot"
        n = len(days)
        recv = self.recv.filter(pl.col("sku") == sku)
        ship = self.lines.filter((pl.col("sku") == sku) & pl.col("ship_t").is_not_null())
        for wh in range(self.n_wh):
            if not self.assort[sku, wh]:
                continue
            delta = np.zeros(n + 1, np.int64)
            r = recv.filter(pl.col("warehouse") == wh)
            np.add.at(delta, np.clip(r["t"].to_numpy() // _MIN_PER_DAY + 1, 0, n), r["qty"].to_numpy())
            s = ship.filter(pl.col("warehouse") == wh)
            np.add.at(delta, np.clip(s["ship_t"].to_numpy() // _MIN_PER_DAY + 1, 0, n), -s["qty"].to_numpy())
            book = np.clip(self._init_book[sku, wh] + np.cumsum(delta)[:n], 0, None)
            out[wh] = book.astype(int).tolist()
        return days, out, "journal"

    # ------------------------------------------------------------------ products
    def products(self, day: int, q: str = "", category: int | None = None, stocked: str = "",
                 sort: str = "sku", desc: bool = False, offset: int = 0, limit: int = 50) -> dict:
        self._build()
        st = self.stock_at(day)
        sold = self.lines.group_by("sku").agg(
            pl.col("qty").filter(pl.col("deliver_t").is_not_null()).sum().alias("delivered_units"))
        lost = self.lost.group_by("sku").agg(pl.col("qty").sum().alias("lost_units"))
        tbl = (self.d.skus.rename({"sku_id": "sku"})
               .join(self.demand.rename({"sku_id": "sku"}), on="sku", how="left")
               .join(sold, on="sku", how="left").join(lost, on="sku", how="left")
               .with_columns(
                   pl.Series("atp", st["atp"].sum(axis=1)), pl.Series("book", st["book"].sum(axis=1)),
                   pl.Series("n_wh", self.assort.sum(axis=1).astype(np.int64)))
               .fill_null(0))
        if q.strip():
            ids = [int(x) for x in q.replace(",", " ").split() if x.isdigit()]
            tbl = tbl.filter(pl.col("sku").is_in(ids)) if ids else tbl.head(0)
        if category is not None:
            tbl = tbl.filter(pl.col("category") == category)
        if stocked == "in":
            tbl = tbl.filter(pl.col("atp") > 0)
        elif stocked == "out":
            tbl = tbl.filter((pl.col("atp") <= 0) & (pl.col("n_wh") > 0))
        if sort not in tbl.columns:
            sort = "sku"
        tbl = tbl.sort(sort, descending=desc)
        page, total = _page(tbl, offset, limit)
        idx = page["sku"].to_numpy()
        rows = page.to_dicts()
        for r, s in zip(rows, idx):
            r["atp_wh"] = st["atp"][s].tolist()
            r["book_wh"] = st["book"][s].tolist()
            r["stocked_wh"] = self.assort[s].tolist()
            for k in ("price", "unit_cost", "weight_kg", "volume"):
                if k in r:
                    r[k] = round(float(r[k]), 2)
        cats = sorted(int(c) for c in self.d.skus["category"].unique().to_list())
        return {"day": day, "total": total, "offset": offset, "rows": rows, "categories": cats,
                "stock_source": st["source"], "safety_days": self._safety_days}

    def product_detail(self, sku: int) -> dict:
        self._build()
        srow = self.d.skus.filter(pl.col("sku_id") == sku)
        if srow.height == 0:
            return {"found": False, "sku": sku}
        info = srow.row(0, named=True)
        price = float(info["price"])
        d0, d1 = self.d.day_range()
        dem = self.d.orders.filter(pl.col("sku_id") == sku)
        dem_day = (dem.with_columns((pl.col("created_min") // _MIN_PER_DAY).alias("d"))
                   .group_by("d").agg(pl.col("qty").sum()).sort("d"))
        ln = self.lines.filter(pl.col("sku") == sku)
        rc = self.recv.filter(pl.col("sku") == sku)
        ls = self.lost.filter(pl.col("sku") == sku)

        def per_day(df: pl.DataFrame, tcol: str, wh: int | None = None) -> dict:
            if wh is not None:
                df = df.filter(pl.col("warehouse") == wh)
            g = (df.filter(pl.col(tcol).is_not_null())
                 .with_columns((pl.col(tcol) // _MIN_PER_DAY).alias("d"))
                 .group_by("d").agg(pl.col("qty").sum()).sort("d"))
            return {"day": g["d"].to_list(), "qty": g["qty"].to_list()}

        days, book, source = self._daily_book(sku)
        per_wh = []
        for wh in range(self.n_wh):
            if not self.assort[sku, wh] and ln.filter(pl.col("warehouse") == wh).height == 0:
                continue
            per_wh.append({
                "wh": wh, "stocked": bool(self.assort[sku, wh]),
                "book": book.get(wh), "received": per_day(rc, "t", wh),
                "shipped": per_day(ln, "ship_t", wh),
                "received_units": int(rc.filter(pl.col("warehouse") == wh)["qty"].sum() or 0),
                "shipped_units": int(ln.filter((pl.col("warehouse") == wh) & pl.col("ship_t").is_not_null())["qty"].sum() or 0),
            })

        delivered_units = int(ln.filter(pl.col("deliver_t").is_not_null())["qty"].sum() or 0)
        shipped_units = int(ln.filter(pl.col("ship_t").is_not_null())["qty"].sum() or 0)
        # Holding: storage cost per unit-day x the daily book at each warehouse.
        holding = 0.0
        for wh, series in book.items():
            holding += float(np.sum(series)) * float(self._storage_cost[wh])
        purchase = float(rc["cost"].sum() or 0.0)
        fulfil = float(ln.filter(pl.col("ship_t").is_not_null())["ship_cost_line"].sum() or 0.0)
        late = float(ln["late_cost_line"].sum() or 0.0)
        penalty = float(ls["cost"].sum() or 0.0)
        total = purchase + fulfil + holding + late + penalty
        costs = {
            "purchase": round(purchase, 2), "fulfilment": round(fulfil, 2),
            "holding": round(holding, 2), "late_penalty": round(late, 2),
            "lost_penalty": round(penalty, 2), "total": round(total, 2),
            "per_delivered_unit": round(total / delivered_units, 2) if delivered_units else None,
            "purchase_per_unit": round(purchase / int(rc["qty"].sum()), 2) if rc.height else None,
            "revenue": round(price * delivered_units, 2),
            "margin": round(price * delivered_units - total, 2),
        }
        return {
            "found": True, "sku": sku,
            "info": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in info.items()},
            "demand": {"lines": int(dem.height), "orders": int(dem["order_id"].n_unique()) if dem.height else 0,
                       "units": int(dem["qty"].sum() or 0),
                       "day": dem_day["d"].to_list(), "qty": dem_day["qty"].to_list()},
            "fulfilment": {"assigned_units": int(ln["qty"].sum() or 0), "shipped_units": shipped_units,
                           "delivered_units": delivered_units,
                           "lost_lines": int(ls.height), "lost_units": int(ls["qty"].sum() or 0),
                           "received_units": int(rc["qty"].sum() or 0)},
            "received": per_day(rc, "t"), "shipped": per_day(ln, "ship_t"),
            "days": days, "stock_source": source, "per_wh": per_wh, "costs": costs,
            "day_range": [d0, d1],
        }

    # ------------------------------------------------------------------ warehouses
    def warehouses(self, day: int, offset: int = 0, limit: int = 50) -> dict:
        self._build()
        st = self.stock_at(day)
        t0 = int(day) * _MIN_PER_DAY
        # Daily total stock per warehouse for sparklines: snapshot > inventory_daily > none.
        spark: dict[int, list[int]] = {}
        if self.d.inv_sku is not None:
            g = self.d.inv_sku.group_by(["warehouse", "day"]).agg(pl.col("book").sum()).sort("day")
            for wh in range(self.n_wh):
                spark[wh] = g.filter(pl.col("warehouse") == wh)["book"].to_list()
        elif self.inv_daily is not None:
            g = self.inv_daily.sort("day")
            for wh in range(self.n_wh):
                spark[wh] = g.filter(pl.col("warehouse") == wh)["units"].to_list()
        so = {}
        if self.inv_daily is not None and "stockout_skus" in self.inv_daily.columns:
            for r in self.inv_daily.filter(pl.col("day") == day).iter_rows(named=True):
                so[int(r["warehouse"])] = int(r["stockout_skus"])
        asg = self.lines.filter(pl.col("assign_t") < t0).group_by("warehouse").len()
        shp = self.lines.filter(pl.col("ship_t") < t0).group_by("warehouse").len()
        asg_m = dict(zip(asg["warehouse"].to_list(), asg["len"].to_list()))
        shp_m = dict(zip(shp["warehouse"].to_list(), shp["len"].to_list()))
        rows = []
        for w in self.d.warehouses.iter_rows(named=True):
            wh = int(w["warehouse_id"])
            stocked = self.assort[:, wh]
            rows.append({
                "wh": wh, "name": w["name"], "x": w["x"], "y": w["y"],
                "capacity": int(w["capacity_lines_per_day"]),
                "n_sku": int(stocked.sum()),
                "book": int(st["book"][:, wh].sum()), "atp": int(st["atp"][:, wh].sum()),
                "reserved": int(st["reserved"][:, wh].sum()),
                "zero_atp_skus": int(((st["atp"][:, wh] <= 0) & stocked).sum()),
                "stockout_skus": so.get(wh),
                "assigned_lines": int(asg_m.get(wh, 0)), "shipped_lines": int(shp_m.get(wh, 0)),
                "backlog_lines": int(asg_m.get(wh, 0) - shp_m.get(wh, 0)),
                "spark": spark.get(wh),
            })
        total = len(rows)
        off = max(0, int(offset))
        return {"day": day, "total": total, "offset": off, "rows": rows[off:off + max(1, int(limit))],
                "stock_source": st["source"]}

    def warehouse_detail(self, wh: int, day: int) -> dict:
        """The inspector's warehouse view, with fallbacks when per-SKU stock was not recorded."""
        self._build()
        w = self.d.warehouse_detail(wh, day)
        w["stock_source"] = "snapshot"
        if not w["stock"]:
            if self.inv_daily is not None:
                g = self.inv_daily.filter(pl.col("warehouse") == wh).sort("day")
                w["stock_day"], w["stock"] = g["day"].to_list(), g["units"].to_list()
                w["stock_source"] = "daily totals"
            st = self.stock_at(day)
            col = st["book"][:, wh]
            top = np.argsort(-col)[:20]
            w["top_sku"] = [{"sku": int(s), "book": int(col[s])} for s in top if col[s] > 0]
            w["top_source"] = "journal"
        return w

    # ------------------------------------------------------------------ orders
    def _orders_status_tbl(self) -> pl.DataFrame:
        if getattr(self, "_otbl", None) is None:
            t = self.d._orders_tbl
            whs = self.lines.group_by("order_id").agg(pl.col("warehouse").unique().alias("whs"))
            t = t.join(whs, on="order_id", how="left").with_columns(
                pl.when(pl.col("delivered_lines") == 0)
                .then(pl.when(pl.col("n_wh") == 0).then(pl.lit("lost")).otherwise(pl.lit("pending")))
                .when(pl.col("delivered_lines") >= pl.col("n_lines"))
                .then(pl.when(pl.col("is_late")).then(pl.lit("late")).otherwise(pl.lit("delivered")))
                .otherwise(pl.lit("partial")).alias("status"))
            self._otbl = t
        return self._otbl

    def orders(self, order_id: int | None = None, day_from: int | None = None,
               day_to: int | None = None, status: str = "", wh: int | None = None,
               region: int | None = None, split: str = "", sku: int | None = None,
               min_lines: int | None = None, sort: str = "order_id", desc: bool = False,
               offset: int = 0, limit: int = 50) -> dict:
        self._build()
        t = self._orders_status_tbl()
        if order_id is not None:
            t = t.filter(pl.col("order_id") == order_id)
        if day_from is not None:
            t = t.filter(pl.col("day") >= day_from)
        if day_to is not None:
            t = t.filter(pl.col("day") <= day_to)
        sts = [s for s in status.split(",") if s in STATUSES]
        if sts:
            t = t.filter(pl.col("status").is_in(sts))
        if wh is not None:
            t = t.filter(pl.col("whs").list.contains(wh))
        if region is not None:
            t = t.filter(pl.col("region_id") == region)
        if split == "yes":
            t = t.filter(pl.col("n_wh") > 1)
        elif split == "no":
            t = t.filter(pl.col("n_wh") <= 1)
        if sku is not None:
            ids = self.d.orders.filter(pl.col("sku_id") == sku)["order_id"].unique()
            t = t.filter(pl.col("order_id").is_in(ids))
        if min_lines is not None:
            t = t.filter(pl.col("n_lines") >= min_lines)
        counts = dict(zip(*t.group_by("status").len().to_dict(as_series=False).values()))
        if sort not in ("order_id", "day", "n_lines", "cost", "lateness_days", "n_wh"):
            sort = "order_id"
        t = t.sort(sort, descending=desc, nulls_last=True)
        page, total = _page(t, offset, limit)
        rows = []
        for r in page.iter_rows(named=True):
            rows.append({
                "order_id": int(r["order_id"]), "day": int(r["day"]), "region": int(r["region_id"]),
                "n_lines": int(r["n_lines"]), "delivered_lines": int(r["delivered_lines"]),
                "lost_lines": int(r["lost_lines"]), "whs": sorted(r["whs"] or []),
                "status": r["status"], "cost": round(float(r["cost"]), 2),
                "promise_day": r["promise_day"], "last_deliver_day": r["last_deliver_day"],
                "lateness_days": None if r["lateness_days"] is None else round(float(r["lateness_days"]), 2),
            })
        return {"total": total, "offset": int(offset), "rows": rows, "status_counts": counts}

    # ------------------------------------------------------------------ sales / money
    def _build_money(self) -> None:
        """A long money ledger: one row per cash event (day, kind, sku, wh, region, amount)."""
        self._build()
        if getattr(self, "_ledger", None) is not None:
            return
        ev = self.d.events
        price = self.d.skus.select(pl.col("sku_id").alias("sku"), "price", "category")
        region = self.d._orders_tbl.select("order_id", pl.col("region_id").alias("region"))
        cols = ["day", "kind", "sku", "wh", "region", "amount", "units"]

        def day(c: str) -> pl.Expr:
            return (pl.col(c) // _MIN_PER_DAY).cast(pl.Int64).alias("day")

        parts = []
        dl = self.lines.filter(pl.col("deliver_t").is_not_null()).join(price, on="sku", how="left") \
            .join(region, on="order_id", how="left")
        parts.append(dl.select(day("deliver_t"), pl.lit("revenue").alias("kind"), "sku",
                               pl.col("warehouse").alias("wh"), "region",
                               (pl.col("price") * pl.col("qty")).alias("amount"), pl.col("qty").alias("units")))
        parts.append(self.recv.select(day("t"), pl.lit("purchase").alias("kind"), "sku",
                                      pl.col("warehouse").alias("wh"), pl.lit(None, pl.Int64).alias("region"),
                                      pl.col("cost").cast(pl.Float64).alias("amount"), pl.col("qty").alias("units")))
        for jt, kind in ((JType.SHIPPED, "fulfilment"), (JType.LATE_PENALTY, "late_penalty"),
                         (JType.HOLDING, "holding")):
            e = ev.filter(pl.col("type") == int(jt))
            if kind != "holding":
                e = e.join(region, on="order_id", how="left")
            else:
                e = e.with_columns(pl.lit(None, pl.Int64).alias("region"))
            parts.append(e.select(day("t"), pl.lit(kind).alias("kind"), pl.lit(None, pl.Int64).alias("sku"),
                                  pl.col("warehouse").alias("wh"), "region",
                                  pl.col("cost").cast(pl.Float64).alias("amount"), pl.lit(0).alias("units")))
        lost = ev.filter(pl.col("type") == int(JType.LOST_SALE)).join(price, on="sku", how="left") \
            .join(region, on="order_id", how="left")
        wh = pl.when(pl.col("warehouse") >= 0).then(pl.col("warehouse")).otherwise(None).alias("wh")
        parts.append(lost.select(day("t"), pl.lit("lost_penalty").alias("kind"), "sku", wh, "region",
                                 pl.col("cost").cast(pl.Float64).alias("amount"), pl.col("qty").alias("units")))
        parts.append(lost.select(day("t"), pl.lit("lost_revenue").alias("kind"), "sku", wh, "region",
                                 (pl.col("price") * pl.col("qty")).alias("amount"), pl.col("qty").alias("units")))
        cast = [pl.col("day").cast(pl.Int64), pl.col("sku").cast(pl.Int64), pl.col("wh").cast(pl.Int64),
                pl.col("region").cast(pl.Int64), pl.col("amount").cast(pl.Float64).fill_null(0.0),
                pl.col("units").cast(pl.Int64)]
        self._ledger = pl.concat([p.select(cols).with_columns(cast) for p in parts]).join(
            price.select("sku", "category"), on="sku", how="left")
        hd = ev.filter(pl.col("type") == int(JType.HOLDING))["t"]
        self._last_roll_day = int(hd.max() // _MIN_PER_DAY) if hd.len() else None
        rc = self.recv
        self._unit_purchase = float(rc["cost"].sum() / rc["qty"].sum()) if rc.height and rc["qty"].sum() else None

    def _sku_holding(self, a: int, b: int) -> np.ndarray:
        """Estimated holding cost per (sku, wh) over start-of-day stock for days a..b."""
        cost = self._storage_cost[None, :]
        # Holding is charged at day roll only while the sim runs; the journal tail (deliveries
        # after the horizon) has no stock snapshots.
        if self._last_roll_day is not None:
            b = min(b, self._last_roll_day)
        if b < a:
            return np.zeros((self.n_sku, self.n_wh))
        if self.d.inv_sku is not None:
            g = self.d.inv_sku.filter(pl.col("day").is_between(a, b)).group_by(["sku", "warehouse"]) \
                .agg(pl.col("book").sum())
            m = np.zeros((self.n_sku, self.n_wh))
            np.add.at(m, (g["sku"].to_numpy(), g["warehouse"].to_numpy()), g["book"].to_numpy())
            return m * cost
        # Without snapshots: an event on day e changes the stock of every snapshot day d > e.
        def weighted(df: pl.DataFrame, tcol: str) -> np.ndarray:
            df = df.filter(pl.col(tcol).is_not_null()).with_columns(
                (pl.lit(b) - pl.max_horizontal(pl.col(tcol) // _MIN_PER_DAY + 1, pl.lit(a)) + 1)
                .clip(0).alias("_n"))
            return self._acc(df.with_columns((pl.col("qty") * pl.col("_n")).alias("_w")), "_w")
        book_days = (b - a + 1) * self._init_book + weighted(self.recv, "t") - weighted(self.lines, "ship_t")
        return np.clip(book_days, 0, None) * cost

    def sales(self, day_from: int | None = None, day_to: int | None = None) -> dict:
        self._build_money()
        d0, d1 = self.d.day_range()
        a = d0 if day_from is None else max(d0, int(day_from))
        b = d1 if day_to is None else min(d1, int(day_to))
        L = self._ledger.filter(pl.col("day").is_between(a, b))
        kinds = ["revenue", "purchase", "fulfilment", "holding", "late_penalty", "lost_penalty", "lost_revenue"]
        tot = dict(zip(*L.group_by("kind").agg(pl.col("amount").sum()).to_dict(as_series=False).values()))
        tot = {k: round(float(tot.get(k, 0.0)), 2) for k in kinds}
        units = dict(zip(*L.group_by("kind").agg(pl.col("units").sum()).to_dict(as_series=False).values()))
        cost_keys = ["purchase", "fulfilment", "holding", "late_penalty", "lost_penalty"]
        spend = sum(tot[k] for k in cost_keys)
        sold = int(units.get("revenue", 0))
        n_orders = int(self.lines.filter(pl.col("deliver_t").is_not_null()
                                         & (pl.col("deliver_t") // _MIN_PER_DAY).is_between(a, b))
                       ["order_id"].n_unique())
        up = self._unit_purchase
        cogs = up * sold if up is not None else None
        st_end = self.stock_at(min(b + 1, d1))["book"]
        kpi = {
            **tot, "spend": round(spend, 2), "cash_margin": round(tot["revenue"] - spend, 2),
            "units_sold": sold, "units_lost": int(units.get("lost_revenue", 0)), "orders_delivered": n_orders,
            "aov": round(tot["revenue"] / n_orders, 2) if n_orders else None,
            "cogs": None if cogs is None else round(cogs, 2),
            "unit_margin": None if cogs is None else round(
                tot["revenue"] - cogs - tot["fulfilment"] - tot["holding"] - tot["late_penalty"] - tot["lost_penalty"], 2),
            "inventory_value_end": None if up is None else round(float(st_end.sum()) * up, 2),
            "unit_purchase_price": None if up is None else round(up, 2),
        }
        # Per day, all kinds.
        pd_ = L.group_by(["day", "kind"]).agg(pl.col("amount").sum())
        days = list(range(a, b + 1))
        daily = {k: [0.0] * len(days) for k in kinds}
        for r in pd_.iter_rows(named=True):
            daily[r["kind"]][r["day"] - a] = round(float(r["amount"]), 2)

        def breakdown(col: str) -> list[dict]:
            g = L.filter(pl.col(col).is_not_null()).group_by([col, "kind"]).agg(pl.col("amount").sum())
            out: dict[int, dict] = {}
            for r in g.iter_rows(named=True):
                out.setdefault(int(r[col]), {k: 0.0 for k in kinds})[r["kind"]] = round(float(r["amount"]), 2)
            return [{"key": k, **v} for k, v in sorted(out.items())]

        by_wh = breakdown("wh")
        by_region = breakdown("region")
        # Category: line-level revenue / purchase / lost from the ledger, fulfilment & late from
        # per-line parcel shares, holding estimated from stock x storage cost.
        hold = self._sku_holding(a, b).sum(axis=1)
        sk = self._sku_money(a, b, hold)
        cat = sk.group_by("category").agg([pl.col(c).sum() for c in
                                           ("revenue", "purchase", "fulfilment", "holding", "late_penalty",
                                            "lost_penalty", "lost_revenue", "units_sold")]).sort("category")
        by_cat = [{"key": int(r["category"]), **{k: round(float(v), 2) for k, v in r.items() if k != "category"}}
                  for r in cat.iter_rows(named=True)]
        return {"from": a, "to": b, "day_range": [d0, d1], "kpi": kpi, "days": days, "daily": daily,
                "by_wh": by_wh, "by_region": by_region, "by_category": by_cat,
                "stock_source": "snapshot" if self.d.inv_sku is not None else "journal"}

    def _sku_money(self, a: int, b: int, hold: np.ndarray | None = None) -> pl.DataFrame:
        L = self._ledger.filter(pl.col("day").is_between(a, b) & pl.col("sku").is_not_null())
        g = L.group_by("sku").agg(
            *[pl.col("amount").filter(pl.col("kind") == k).sum().alias(k)
              for k in ("revenue", "purchase", "lost_penalty", "lost_revenue")],
            pl.col("units").filter(pl.col("kind") == "revenue").sum().alias("units_sold"),
            pl.col("units").filter(pl.col("kind") == "lost_revenue").sum().alias("units_lost"))
        ln = self.lines
        f = ln.filter((pl.col("ship_t") // _MIN_PER_DAY).is_between(a, b)).group_by("sku") \
            .agg(pl.col("ship_cost_line").sum().alias("fulfilment"))
        lt = ln.filter((pl.col("deliver_t") // _MIN_PER_DAY).is_between(a, b)).group_by("sku") \
            .agg(pl.col("late_cost_line").sum().alias("late_penalty"))
        if hold is None:
            hold = self._sku_holding(a, b).sum(axis=1)
        base = self.d.skus.select(pl.col("sku_id").alias("sku"), "category", "price") \
            .with_columns(pl.Series("holding", hold.astype(np.float64)))
        t = base.join(g, on="sku", how="left").join(f, on="sku", how="left").join(lt, on="sku", how="left") \
            .fill_null(0)
        t = t.with_columns(
            (pl.col("purchase") + pl.col("fulfilment") + pl.col("holding") + pl.col("late_penalty")
             + pl.col("lost_penalty")).alias("cost"))
        return t.with_columns((pl.col("revenue") - pl.col("cost")).alias("margin"),
                              pl.when(pl.col("revenue") > 0)
                              .then((pl.col("revenue") - pl.col("cost")) / pl.col("revenue"))
                              .otherwise(None).alias("margin_pct"))

    def sales_skus(self, day_from: int | None = None, day_to: int | None = None, q: str = "",
                   category: int | None = None, sort: str = "revenue", desc: bool = True,
                   offset: int = 0, limit: int = 50) -> dict:
        self._build_money()
        d0, d1 = self.d.day_range()
        a = d0 if day_from is None else max(d0, int(day_from))
        b = d1 if day_to is None else min(d1, int(day_to))
        t = self._sku_money(a, b)
        if q.strip():
            ids = [int(x) for x in q.replace(",", " ").split() if x.isdigit()]
            t = t.filter(pl.col("sku").is_in(ids)) if ids else t.head(0)
        if category is not None:
            t = t.filter(pl.col("category") == category)
        if sort not in t.columns:
            sort = "revenue"
        t = t.sort(sort, descending=desc, nulls_last=True)
        page, total = _page(t, offset, limit)
        rows = [{k: (round(v, 3 if k == "margin_pct" else 2) if isinstance(v, float) else v)
                 for k, v in r.items()} for r in page.to_dicts()]
        return {"from": a, "to": b, "total": total, "offset": int(offset), "rows": rows}
