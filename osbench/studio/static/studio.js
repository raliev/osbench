"use strict";
// osbench studio: visual config editor + map preview + lifecycle diagram + launcher.

let cfg = null, cfgName = null, cfgSource = "preset", dirty = false;
let baseCfg = null;  // pristine config as loaded, to diff edited params for run-name suggestions
const WH_COLORS = ["#2f81f7", "#3fb950", "#d29922", "#f85149", "#a371f7", "#8957e5",
  "#db61a2", "#1f6feb", "#2da44e", "#bf8700"];

// ----------------------------------------------------------------- path helpers
function getPath(o, path) {
  return path.split(".").reduce((a, k) => (a == null ? a : a[/^\d+$/.test(k) ? +k : k]), o);
}
function setPath(o, path, v) {
  const ks = path.split(".");
  let a = o;
  for (let i = 0; i < ks.length - 1; i++) a = a[/^\d+$/.test(ks[i]) ? +ks[i] : ks[i]];
  const last = ks[ks.length - 1];
  a[/^\d+$/.test(last) ? +last : last] = v;
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

// ----------------------------------------------------------------- field schema
const SECTIONS = [
  { title: "General", fields: [
    { path: "sim_days", label: "sim days", type: "range", min: 7, max: 365, step: 1, int: true },
    { path: "warmup_days", label: "warmup days", type: "range", min: 0, max: 60, step: 1, int: true },
    { path: "region_radius_km", label: "customer radius (km)", type: "range", min: 0, max: 400, step: 5 },
    { path: "initial_cover_days", label: "initial cover (days)", type: "range", min: 0, max: 60, step: 1, int: true },
    { path: "geo.mode", label: "geo mode", type: "select", options: ["plane", "haversine"] },
    { path: "sourcing_wait_min", label: "sourcing wait min (min)", type: "range", min: 0, max: 120, step: 1, int: true },
    { path: "sourcing_wait_max", label: "sourcing wait max (min)", type: "range", min: 0, max: 240, step: 1, int: true },
  ]},
  { title: "Demand", fields: [
    { path: "demand.base_orders_per_day", label: "orders / day", type: "range", min: 10, max: 10000, step: 10 },
    { path: "demand.lines_per_order_mean", label: "avg lines / order", type: "range", min: 1.05, max: 6, step: 0.05 },
    { path: "demand.qty_max", label: "max qty / line", type: "range", min: 1, max: 10, step: 1, int: true },
    { path: "demand.seasonality_amplitude", label: "seasonality", type: "range", min: 0, max: 1, step: 0.05 },
  ]},
  { title: "Catalog (SKU)", fields: [
    { path: "sku.n_sku", label: "number of SKUs", type: "range", min: 10, max: 5000, step: 10, int: true },
    { path: "sku.zipf_a", label: "popularity (zipf a)", type: "range", min: 1.01, max: 2.0, step: 0.01 },
    { path: "sku.n_categories", label: "categories", type: "range", min: 1, max: 12, step: 1, int: true },
  ]},
  { title: "Tariffs (per zone: 0-50 / 50-300 / 300-1000 / 1000+ km)", fields: [
    { path: "tariffs.cost_by_zone.0", label: "cost z0", type: "number" },
    { path: "tariffs.cost_by_zone.1", label: "cost z1", type: "number" },
    { path: "tariffs.cost_by_zone.2", label: "cost z2", type: "number" },
    { path: "tariffs.cost_by_zone.3", label: "cost z3", type: "number" },
    { path: "tariffs.transit_days_by_zone.0", label: "days z0", type: "number", int: true },
    { path: "tariffs.transit_days_by_zone.1", label: "days z1", type: "number", int: true },
    { path: "tariffs.transit_days_by_zone.2", label: "days z2", type: "number", int: true },
    { path: "tariffs.transit_days_by_zone.3", label: "days z3", type: "number", int: true },
    { path: "tariffs.delivery_noise_days_mean", label: "delivery noise (days)", type: "range", min: 0, max: 3, step: 0.1 },
  ]},
  { title: "Promise & ATP", fields: [
    { path: "promise.method", label: "promise method", type: "select",
      options: ["zone_fixed", "transit_plus_buffer", "atp_based", "percentile"] },
    { path: "promise.buffer_days", label: "buffer days", type: "range", min: 0, max: 7, step: 1, int: true },
    { path: "promise.cap_days", label: "cap days", type: "range", min: 1, max: 20, step: 1, int: true },
    { path: "promise.percentile", label: "percentile (p)", type: "range", min: 0.5, max: 0.99, step: 0.01 },
    { path: "promise.p_accept_promise", label: "promise accept floor", type: "range", min: 0, max: 1, step: 0.05 },
    { path: "atp.method", label: "ATP method", type: "select",
      options: ["on_hand_only", "basic", "cumulative"] },
    { path: "atp.horizon_days", label: "cumulative ATP horizon (days)", type: "range", min: 0, max: 30, step: 1, int: true },
    { path: "reservation.enabled", label: "reservation", type: "bool" },
    { path: "inventory_accuracy.accuracy", label: "inventory accuracy", type: "range", min: 0.9, max: 1.0, step: 0.005 },
  ]},
  { title: "Deferred sourcing (RQ5)", fields: [
    { path: "deferral.mode", label: "deferral mode", type: "select",
      options: ["none", "wait_for_receipt", "wait_to_consolidate"] },
    { path: "deferral.arbitration", label: "arbitration", type: "select",
      options: ["edf", "fifo", "value"] },
    { path: "deferral.safety_margin_days", label: "safety margin (days)", type: "range", min: 0, max: 3, step: 0.5 },
    { path: "deferral.penalty_deferral_miss", label: "miss penalty ×", type: "range", min: 1, max: 5, step: 0.5 },
  ]},
  { title: "Shortage points (2.9)", fields: [
    { path: "shortage.checkout_atp_check", label: "checkout ATP check", type: "bool" },
    { path: "shortage.p_partial", label: "checkout: keep partial", type: "range", min: 0, max: 1, step: 0.05 },
    { path: "shortage.p_substitute", label: "checkout: substitute SKU", type: "range", min: 0, max: 1, step: 0.05 },
    { path: "shortage.p_accept_slow", label: "post: accept out-of-tolerance", type: "range", min: 0, max: 1, step: 0.05 },
    { path: "shortage.p_partial_ship", label: "ship available (post/pick)", type: "range", min: 0, max: 1, step: 0.05 },
    { path: "shortage.p_resource", label: "pick: re-source elsewhere", type: "range", min: 0, max: 1, step: 0.05 },
    { path: "shortage.backorder", label: "backorder at pick", type: "bool" },
    { path: "shortage.penalty_lost_sale", label: "penalty: lost sale", type: "number" },
    { path: "shortage.penalty_post_order", label: "penalty: post-order", type: "number" },
    { path: "shortage.penalty_pick", label: "penalty: pick", type: "number" },
    { path: "shortage.penalty_late_per_day", label: "penalty: late / day", type: "number" },
  ]},
  { title: "Process times (minutes)", fields: [
    { path: "process_times.pick_median_min", label: "pick median", type: "range", min: 1, max: 120, step: 1 },
    { path: "process_times.pick_per_line_min", label: "pick per line", type: "range", min: 0, max: 30, step: 0.5 },
    { path: "process_times.pack_median_min", label: "pack median", type: "range", min: 1, max: 60, step: 1 },
    { path: "process_times.sigma", label: "lognormal spread", type: "range", min: 0, max: 1, step: 0.05 },
  ]},
  { title: "Shocks", fields: [
    { path: "shocks.enabled", label: "shocks enabled", type: "bool" },
    { path: "shocks.peak_multiplier", label: "peak ×", type: "range", min: 1, max: 8, step: 0.5 },
    { path: "shocks.peak_duration_days", label: "peak days", type: "range", min: 1, max: 14, step: 1, int: true },
  ]},
];

// Order lifecycle steps -> relevant config paths (shown in the step drawer).
const STEPS = [
  { name: "Order created / ATP check", note: "checks ATP, issues a promise, may refuse a long one (point 1)",
    paths: ["atp.method", "atp.horizon_days", "reservation.enabled", "promise.method", "promise.percentile",
            "promise.p_accept_promise", "shortage.checkout_atp_check", "shortage.p_partial", "shortage.p_substitute"] },
  { name: "Awaiting sourcing", note: "uniform wait: batching / anti-fraud / payment",
    paths: ["sourcing_wait_min", "sourcing_wait_max"] },
  { name: "Deferred (awaiting SLA)", note: "optional: wait for inbound stock within the promise slack (RQ5)",
    paths: ["deferral.mode", "deferral.arbitration", "deferral.safety_margin_days",
            "deferral.penalty_deferral_miss"] },
  { name: "Sourcing decision", note: "policy chosen at run time; unsourced lines → post-order shortage (point 2)",
    paths: ["shortage.p_accept_slow", "shortage.p_partial_ship", "shortage.penalty_post_order"] },
  { name: "Reserve", note: "atomic reservation of assigned lines", paths: ["reservation.enabled"] },
  { name: "Warehouse queue", note: "cut-off + daily capacity (edit per warehouse in the table)", paths: [] },
  { name: "Pick", note: "physical check surfaces phantom stock → re-source / backorder / partial (point 3)",
    paths: ["process_times.pick_median_min", "process_times.pick_per_line_min", "inventory_accuracy.accuracy",
            "shortage.p_resource", "shortage.backorder", "shortage.p_partial_ship", "shortage.penalty_pick"] },
  { name: "Pack", note: "lognormal pack time", paths: ["process_times.pack_median_min", "process_times.sigma"] },
  { name: "Shipped", note: "zone tariff + transit days + noise",
    paths: ["tariffs.delivery_noise_days_mean"] },
  { name: "Delivered", note: "compared against the promise; late penalty applies",
    paths: ["promise.method", "promise.buffer_days", "promise.cap_days", "shortage.penalty_late_per_day"] },
];

// Per-parameter help text shown behind the (i) marker next to each field.
const PARAM_HELP = {
  "sim_days": "Length of the simulated horizon in days. Metrics are computed over this window (minus warmup).",
  "warmup_days": "Leading days excluded from metrics so the pipeline/inventory reaches steady state before measuring.",
  "region_radius_km": "Customers are scattered within this radius around each region centroid; shipping distance is warehouse→customer.",
  "initial_cover_days": "Starting stock is sized to cover this many days of demand, avoiding a cold start.",
  "geo.mode": "plane = synthetic 1000×1000 km grid (presets/tests). haversine = real lat/lon great-circle distance.",
  "sourcing_wait_min": "Lower bound of the 'awaiting sourcing' wait (batching / anti-fraud / payment) before the policy runs, in minutes.",
  "sourcing_wait_max": "Upper bound of the 'awaiting sourcing' wait before the policy runs, in minutes.",
  "demand.base_orders_per_day": "Base Poisson arrival rate of orders per day, before weekly/seasonal profiles and region weights.",
  "demand.lines_per_order_mean": "Mean number of lines (distinct SKUs) per order (geometric). Higher → bigger baskets → more splits.",
  "demand.qty_max": "Maximum units per line; quantity is drawn uniformly in 1..this.",
  "demand.seasonality_amplitude": "Strength of the yearly seasonal demand swing (0 = flat, 1 = strong).",
  "sku.n_sku": "Number of distinct SKUs in the catalog.",
  "sku.zipf_a": "Zipf popularity exponent: higher → heavier long tail (a few hot SKUs, many slow movers) → more stockouts/splits.",
  "sku.n_categories": "Number of product categories (used for region×category demand correlation).",
  "tariffs.cost_by_zone.0": "Flat shipping cost for zone 0 (nearest band). Zone = distance bucket of warehouse→customer.",
  "tariffs.cost_by_zone.1": "Flat shipping cost for zone 1 (the second distance band).",
  "tariffs.cost_by_zone.2": "Flat shipping cost for zone 2 (the third distance band).",
  "tariffs.cost_by_zone.3": "Flat shipping cost for zone 3 (farthest band).",
  "tariffs.transit_days_by_zone.0": "Carrier transit days for zone 0 (nearest). Delivery = this + 1 day if packed after cut-off.",
  "tariffs.transit_days_by_zone.1": "Carrier transit days for zone 1.",
  "tariffs.transit_days_by_zone.2": "Carrier transit days for zone 2.",
  "tariffs.transit_days_by_zone.3": "Carrier transit days for zone 3 (farthest).",
  "tariffs.delivery_noise_days_mean": "Mean extra delay beyond the tariff transit time (geometric). 0 = deterministic delivery.",
  "promise.method": "How the delivery promise is set: zone_fixed, transit_plus_buffer, atp_based (by stock availability + expected receipts), or percentile (a delivery-time quantile).",
  "promise.buffer_days": "Extra days added to the promise, giving sourcing slack to wait for inbound stock.",
  "promise.cap_days": "Maximum promise length; beyond this a line is 'out of time tolerance' and may be refused.",
  "promise.percentile": "For method=percentile: the delivery-time quantile (e.g. 0.9 = p90) used to pad the promise against noise.",
  "promise.p_accept_promise": "Customer acceptance floor for a long promise: 1.0 = always accept; lower = a long promise (toward cap_days) is refused at checkout and lost.",
  "deferral.mode": "Deferred sourcing: none (decide now); wait_for_receipt (park a short order until stock arrives, within promise slack); wait_to_consolidate (wait to avoid a split).",
  "deferral.arbitration": "When a receipt arrives, who claims it first among waiting orders: edf (earliest deadline), fifo (oldest order), value (biggest order).",
  "deferral.safety_margin_days": "Slack kept in reserve before the deadline when deciding whether an order may wait for a receipt.",
  "deferral.penalty_deferral_miss": "Multiplier on the per-day late penalty if a deferred order still misses its promise (it chose to wait and lost the bet).",
  "atp.method": "Available-to-promise formula: on_hand_only = book−reserved; basic = −safety; cumulative = +inbound receipts within the horizon (reserved nets out committed promises).",
  "atp.horizon_days": "For cumulative ATP: inbound receipts arriving within this many days count toward availability.",
  "shortage.p_substitute": "Checkout: probability the customer accepts a same-category in-stock SKU when theirs is unavailable.",
  "shortage.p_accept_slow": "Post-order: probability the customer accepts an out-of-tolerance (far/expensive) warehouse rather than lose the line (needs ATP tolerance on).",
  "shortage.p_resource": "Pick: probability a short line is re-sourced from another warehouse that physically has it, as a separate parcel.",
  "shortage.backorder": "Pick: hold a short line and wait for an inbound receipt at that warehouse (within the promise) instead of cancelling.",
  "process_times.pick_median_min": "Median pick time per parcel (lognormal), in minutes.",
  "process_times.pick_per_line_min": "Extra pick minutes added per line in the parcel.",
  "process_times.pack_median_min": "Median pack time per parcel (lognormal), in minutes.",
  "process_times.sigma": "Lognormal spread (shape) of the pick/pack times; 0 = near-deterministic.",
  "reservation.enabled": "On: sourcing reserves units so other orders can't claim them. Off: conflicts surface at pick (OMS without online reserves).",
  "inventory_accuracy.accuracy": "Fraction of receipts booked correctly; below 1.0 injects phantom inventory discovered at pick.",
  "shortage.checkout_atp_check": "Point 1: at order creation, check network ATP per line. Uncovered lines are dropped (p partial) or the order is refused — a lost sale at checkout.",
  "shortage.p_partial": "Checkout: probability the customer keeps a partial order when some lines are uncovered (else the whole order is refused).",
  "shortage.p_partial_ship": "Points 2 & 3: probability of shipping what's available and cancelling the rest (else cancel the whole order / line).",
  "shortage.penalty_lost_sale": "Penalty per line lost at checkout (point 1) or refused.",
  "shortage.penalty_post_order": "Penalty per line cancelled after the order was taken but could not be sourced (point 2).",
  "shortage.penalty_pick": "Penalty per line short at pick — phantom inventory or, without reservation, an order conflict (point 3).",
  "shortage.penalty_late_per_day": "Cost charged per day a parcel is delivered after its promise (lateness penalty).",
  "shocks.enabled": "Enable scenario shocks (demand peak and/or a warehouse outage) defined below.",
  "shocks.peak_multiplier": "Demand multiplier during the peak window (e.g. 4× for 3 days).",
  "shocks.peak_duration_days": "How many days the demand peak lasts.",
};

// Descriptions of the sourcing / replenishment policies, shown by the (i) next to Run.
const POLICY_HELP = {
  sourcing: {
    random: "Lower-bound baseline: picks a random warehouse among those with stock for each line.",
    nearest: "Rule: the nearest warehouse that covers all lines; otherwise the split with the fewest parcels.",
    cheapest_myopic: "Rule: minimizes the current shipping + handling cost, splitting across warehouses if cheaper. No look-ahead.",
    weighted_score: "Industrial OMS baseline (Adobe Commerce / US 10,839,338): score = Σ wᵢ·fᵢ over min-max-normalized factors (distance, ship price, promise, splits, above-safety, warehouse load, rank).",
    weighted_score_tuned: "weighted_score with factor weights tuned offline on training seeds (`osbench tune`) — RQ1's ML-light line.",
    dtlp_bidprice: "Dual transportation LP (Acimović & Graves 2015): every period a per-SKU LP on forecast demand yields dual 'bid' prices (opportunity cost of stock); each line goes where ship_cost + bid·qty is lowest.",
    primal_dual: "Online primal-dual (Andrews et al. 2019): no forecast; a warehouse's bid rises as its stock depletes, steering orders away from scarce sites.",
    hindsight_lp: "Upper-bound-style planner: sees the whole realized demand and solves a capacity-aware region→warehouse transportation LP offline, then follows those flows online. A planning bound, not an exact optimum.",
    spillover_limit: "Myopic cheapest with a daily cap on 'foreign' orders per warehouse (DeValve et al. 2023), so a warehouse is kept for its home region's demand.",
    rolling_milp: "Periodic re-optimization (Xu et al. 2009): every `period_days` an LP re-plans region→warehouse flows for the forecast demand under capacity limits; orders follow the fresh plan.",
    broken_wh0: "Demo-only broken policy (negative weights) used to show the guardrail aborting a bad run.",
  },
  replenishment: {
    base_stock: "Order up to level S every R days; inventory position = book on-hand + in-transit − reserved. The simplest baseline.",
    s_S: "(s, S): when the position drops below s, order up to S — classic, produces batches.",
    R_Q: "Review every R days; when below the reorder point, order whole fixed lots of Q (lumpy orders; Wang & Minner 2024).",
    forecast_driven: "Order-up-to S = forecast demand over lead+review + z·σ safety stock, from the observed demand rate. A realistic industrial scheme.",
    network_allocation: "Decide a network order per SKU, then allocate it to warehouses in proportion to their observed demand share (Acimović & Graves 2017) — couples replenishment to sourcing.",
  },
};

const POLICY_META = {
  sourcing: { title: "Sourcing policies", sub: "Choose a warehouse (or split) for each order." },
  replenishment: { title: "Replenishment policies", sub: "Decide what stock to buy into each warehouse." },
};

function showPolicyHelp(kind) {
  const meta = POLICY_META[kind];
  const rows = Object.entries(POLICY_HELP[kind]).map(([n, d]) =>
    `<div class="prow"><div class="pname">${n}</div><div class="pdesc">${d}</div></div>`).join("");
  const ov = document.createElement("div");
  ov.className = "modal-overlay";
  ov.innerHTML = `<div class="modal"><h2>${meta.title}</h2>`
    + `<div class="muted" style="font-size:12px">${meta.sub} Pick one in the Run bar; some take parameters (shown in the form / step drawer).</div>`
    + `<div style="margin-top:8px">${rows}</div>`
    + `<div class="closebar"><button id="modal-close">Close</button></div></div>`;
  ov.onclick = (e) => { if (e.target === ov || e.target.id === "modal-close") ov.remove(); };
  document.addEventListener("keydown", function esc(e) {
    if (e.key === "Escape") { ov.remove(); document.removeEventListener("keydown", esc); }
  });
  document.body.appendChild(ov);
}

// ----------------------------------------------------------------- field render
function makeHelp(txt) {
  if (!txt) return null;
  const h = document.createElement("span"); h.className = "help"; h.textContent = "i";
  const tip = document.createElement("span"); tip.className = "tip"; tip.textContent = txt;
  h.appendChild(tip);
  h.onclick = (e) => { e.stopPropagation(); closeHelp(h); h.classList.toggle("open"); };
  return h;
}
function helpIcon(path) { return makeHelp(PARAM_HELP[path]); }

// Replenishment policy settings (passed at run time, not stored in the scenario config).
const replParams = { review_days: 7, safety_days: 0.5, S_overshoot: 1.5, q_mult: 1.0, service_z: 1.645 };
const REPL_FIELDS = [
  { key: "review_days", label: "review period R (days)", step: 1, int: true,
    help: "R — how often replenishment is reviewed and ordered. The order-up-to level covers lead + R days of demand." },
  { key: "safety_days", label: "safety stock (days)", step: 0.1,
    help: "Safety stock in days of demand, added to ATP as a buffer (was hardcoded at 0.5)." },
  { key: "S_overshoot", label: "S overshoot ×", step: 0.1,
    help: "Order-up-to level S = (lead + R) days of demand × this factor (was hardcoded at 1.5)." },
  { key: "q_mult", label: "R_Q lot size × (Q)", step: 0.1,
    help: "R_Q only: the fixed lot Q = (S − s) × this. Orders come in whole multiples of Q." },
  { key: "service_z", label: "service z (forecast/network)", step: 0.005,
    help: "forecast_driven / network_allocation only: safety factor z·σ (1.645 ≈ 95% service level)." },
];
function renderReplParams() {
  const root = document.getElementById("repl-params"); if (!root) return;
  root.innerHTML = "";
  for (const f of REPL_FIELDS) {
    const d = document.createElement("div"); d.className = "field";
    const lab = document.createElement("label"); lab.textContent = f.label;
    const ic = makeHelp(f.help); if (ic) lab.appendChild(ic);
    d.appendChild(lab);
    const inp = document.createElement("input"); inp.type = "number"; inp.step = f.step;
    inp.value = replParams[f.key]; inp.style.width = "80px";
    inp.onchange = () => { replParams[f.key] = f.int ? Math.round(+inp.value) : +inp.value; };
    d.appendChild(inp); root.appendChild(d);
  }
}
function replParamsForRun() {
  return {
    review_days: replParams.review_days, safety_days: replParams.safety_days,
    S_overshoot: replParams.S_overshoot,
    params: { q_mult: replParams.q_mult, service_z: replParams.service_z },
  };
}
function closeHelp(except) {
  document.querySelectorAll(".help.open").forEach(x => { if (x !== except) x.classList.remove("open"); });
}
document.addEventListener("click", () => closeHelp(null));

function fieldControl(f, onChange) {
  const v = getPath(cfg, f.path);
  if (f.type === "bool") {
    const c = document.createElement("input"); c.type = "checkbox"; c.checked = !!v;
    c.onchange = () => onChange(f, c.checked); return [c, null];
  }
  if (f.type === "select") {
    const s = document.createElement("select");
    for (const o of f.options) { const op = document.createElement("option"); op.value = o; op.textContent = o; s.appendChild(op); }
    s.value = v; s.onchange = () => onChange(f, s.value); return [s, null];
  }
  if (f.type === "range") {
    const r = document.createElement("input"); r.type = "range";
    r.min = f.min; r.max = f.max; r.step = f.step; r.value = v;
    const val = document.createElement("input"); val.type = "number"; val.className = "val"; val.value = v;
    val.step = f.step;
    const sync = (nv) => { nv = f.int ? Math.round(+nv) : +nv; r.value = nv; val.value = nv; onChange(f, nv); };
    r.oninput = () => sync(r.value); val.onchange = () => sync(val.value);
    const row = document.createElement("div"); row.className = "row"; row.append(r, val); return [row, null];
  }
  const n = document.createElement("input"); n.type = "number"; n.value = v; n.step = f.step || "any";
  n.style.width = "70px";
  n.onchange = () => onChange(f, f.int ? Math.round(+n.value) : +n.value); return [n, null];
}

function makeField(f, onChange) {
  const d = document.createElement("div"); d.className = "field";
  const lab = document.createElement("label"); lab.textContent = f.label;
  const ic = helpIcon(f.path); if (ic) lab.appendChild(ic);
  d.appendChild(lab);
  const [ctrl] = fieldControl(f, onChange); d.appendChild(ctrl); return d;
}

const onFieldChange = (f, v) => { setPath(cfg, f.path, v); markDirty(); schedule(); };

function renderSections() {
  const root = document.getElementById("sections"); root.innerHTML = "";
  for (const sec of SECTIONS) {
    const det = document.createElement("details"); det.className = "sect"; det.open = true;
    const sum = document.createElement("summary"); sum.textContent = sec.title; det.appendChild(sum);
    for (const f of sec.fields) if (getPath(cfg, f.path) !== undefined) det.appendChild(makeField(f, onFieldChange));
    root.appendChild(det);
  }
  root.appendChild(tableSection("Warehouses", "warehouses",
    [["name", "name", "text"], ["x", "x", "num"], ["y", "y", "num"],
     ["capacity_lines_per_day", "cap", "num"], ["rank", "rank", "num"],
     ["assortment_fraction", "assort", "num"]],
    () => ({ warehouse_id: cfg.warehouses.length, name: "W" + cfg.warehouses.length,
             x: 500, y: 500, capacity_lines_per_day: 400, rank: 0.5, assortment_fraction: 1.0 })));
  root.appendChild(tableSection("Regions", "regions",
    [["name", "name", "text"], ["x", "x", "num"], ["y", "y", "num"],
     ["population_weight", "weight", "num"]],
    () => ({ region_id: cfg.regions.length, name: "R" + cfg.regions.length,
             x: 500, y: 500, population_weight: 1.0 })));
  root.appendChild(tableSection("Suppliers", "suppliers",
    [["name", "name", "text"], ["unit_price", "price", "num"], ["moq", "moq", "num"],
     ["lead_time_days", "lead", "num"], ["capacity_per_period", "cap/period", "num"]],
    () => ({ supplier_id: cfg.suppliers.length, name: "S" + cfg.suppliers.length,
             unit_price: 10, moq: 10, lead_time_days: 7, capacity_per_period: 200000 })));
}

function tableSection(title, key, cols, mkRow) {
  const det = document.createElement("details"); det.className = "sect"; det.open = false;
  det.innerHTML = `<summary>${title} (${cfg[key].length})</summary>`;
  const tbl = document.createElement("table");
  const head = "<tr>" + cols.map(c => `<th>${c[1]}</th>`).join("") + "<th></th></tr>";
  let rows = "";
  cfg[key].forEach((item, i) => {
    rows += "<tr>" + cols.map(c =>
      `<td><input data-k="${key}" data-i="${i}" data-f="${c[0]}" data-t="${c[2]}" class="${c[0] === 'name' ? 'name' : ''}" value="${item[c[0]]}"></td>`
    ).join("") + `<td><button class="rowbtn" data-del="${key}" data-i="${i}">×</button></td></tr>`;
  });
  tbl.innerHTML = head + rows;
  const wrap = document.createElement("div"); wrap.className = "tblwrap"; wrap.appendChild(tbl);
  det.appendChild(wrap);
  const add = document.createElement("button"); add.className = "rowbtn"; add.textContent = "+ add";
  add.onclick = () => { cfg[key].push(mkRow()); renderSections(); markDirty(); schedule(); };
  det.appendChild(add);
  tbl.querySelectorAll("input").forEach(inp => {
    inp.onchange = () => {
      const { k, i, f, t } = inp.dataset;
      cfg[k][+i][f] = t === "num" ? +inp.value : inp.value;
      markDirty(); schedule();
    };
  });
  tbl.querySelectorAll("[data-del]").forEach(b => {
    b.onclick = () => { cfg[b.dataset.del].splice(+b.dataset.i, 1); renderSections(); markDirty(); schedule(); };
  });
  return det;
}

// ----------------------------------------------------------------- interactive map editor
const WH_R = 22;               // display radius of a warehouse circle (data units)
const ZONE_COLORS = ["#3fb950", "#d29922", "#f0883e", "#f85149", "#a371f7"];  // per tariff zone
let showZones = false;         // overlay tariff-zone rings around each warehouse
let selected = null;           // {kind:'wh'|'region', idx}
let view = null;               // captured [xrange, yrange] so zoom/pan survives redraws
let lastCustomers = { x: [], y: [], nearest: [] };
let shapeMeta = [];            // shapeMeta[i] = {kind, idx} aligned with layout.shapes
let mapWired = false;
let suppressRelayout = false;  // ignore relayout events during a programmatic redraw

const schedulePreview = debounce(() => resampleCustomers(), 200);

async function refreshPreview(resetView = false) {
  await resampleCustomers(resetView);
}

async function resampleCustomers(resetView = false) {
  const r = await fetch("/api/preview", { method: "POST", body: JSON.stringify({ config: cfg, n: 500 }) });
  const p = await r.json();
  if (p.error) { document.getElementById("preview-note").textContent = "preview error: " + p.error; return; }
  lastCustomers = p.customers;
  document.getElementById("preview-note").textContent =
    `${cfg.warehouses.length} warehouses · ${cfg.regions.length} regions · radius ${cfg.region_radius_km} km`;
  drawMap(resetView);
  renderEntityList();
}

function buildShapes() {
  const shapes = []; shapeMeta = [];
  // Tariff zones: concentric rings at each zone's outer distance bound around every warehouse,
  // coloured per zone (same colour = same tariff band). Drawn first so markers stay on top.
  if (showZones) {
    const bounds = (cfg.tariffs && cfg.tariffs.zone_bounds_km) || [];
    const plane = cfg.geo.plane_size || 1000;
    cfg.warehouses.forEach((w) => {
      bounds.forEach((b, z) => {
        if (!isFinite(b) || b <= 0 || b > plane * 1.6) return;  // skip the unbounded last zone
        shapes.push({ type: "circle", xref: "x", yref: "y", layer: "below",
          x0: w.x - b, x1: w.x + b, y0: w.y - b, y1: w.y + b,
          line: { color: ZONE_COLORS[z % ZONE_COLORS.length], width: 1, dash: "dot" },
          fillcolor: "rgba(0,0,0,0)" });
        shapeMeta.push({ kind: "zone", idx: -1 });  // not interactive / not synced
      });
    });
  }
  // Customer circles (regions): draggable move + resizable radius.
  cfg.regions.forEach((r, i) => {
    const R = cfg.region_radius_km;
    const sel = selected && selected.kind === "region" && selected.idx === i;
    shapes.push({ type: "circle", xref: "x", yref: "y", layer: "below",
      x0: r.x - R, x1: r.x + R, y0: r.y - R, y1: r.y + R,
      line: { color: sel ? "#2f81f7" : "#8b949e", width: sel ? 2.5 : 1 },
      fillcolor: "rgba(139,148,158,0.06)" });
    shapeMeta.push({ kind: "region", idx: i });
  });
  // Warehouse circles: draggable move.
  cfg.warehouses.forEach((w, i) => {
    const sel = selected && selected.kind === "wh" && selected.idx === i;
    const c = WH_COLORS[i % WH_COLORS.length];
    shapes.push({ type: "circle", xref: "x", yref: "y", layer: "below",
      x0: w.x - WH_R, x1: w.x + WH_R, y0: w.y - WH_R, y1: w.y + WH_R,
      line: { color: sel ? "#fff" : c, width: sel ? 3 : 1.5 }, fillcolor: c });
    shapeMeta.push({ kind: "wh", idx: i });
  });
  return shapes;
}

function drawMap(resetView = false) {
  const plane = cfg.geo.plane_size || 1000;
  const traces = [];
  // Customers colored by nearest warehouse.
  const byWh = {};
  lastCustomers.x.forEach((x, i) => {
    const w = lastCustomers.nearest[i];
    (byWh[w] = byWh[w] || { x: [], y: [] }); byWh[w].x.push(x); byWh[w].y.push(lastCustomers.y[i]);
  });
  for (const w in byWh) traces.push({ x: byWh[w].x, y: byWh[w].y, mode: "markers", type: "scattergl",
    marker: { size: 5, color: WH_COLORS[w % WH_COLORS.length], opacity: 0.45 }, hoverinfo: "skip" });
  // Region selection markers.
  traces.push({ x: cfg.regions.map(r => r.x), y: cfg.regions.map(r => r.y), mode: "markers",
    type: "scatter", marker: { size: 10, color: "#8b949e", symbol: "square" },
    text: cfg.regions.map(r => r.name), hoverinfo: "text",
    customdata: cfg.regions.map((_, i) => ["region", i]), name: "regions" });
  // Warehouse selection markers (labels).
  traces.push({ x: cfg.warehouses.map(w => w.x), y: cfg.warehouses.map(w => w.y),
    mode: "markers+text", type: "scatter", text: cfg.warehouses.map(w => w.name), textposition: "top center",
    marker: { size: 12, color: "#fff", opacity: 0.9 }, hoverinfo: "text",
    customdata: cfg.warehouses.map((_, i) => ["wh", i]), name: "wh" });

  const rng = resetView || !view
    ? { x: [0, plane], y: [0, plane] }
    : { x: view.x, y: view.y };
  if (resetView) view = null;

  suppressRelayout = true;
  Plotly.react("map", traces, {
    paper_bgcolor: "#161b22", plot_bgcolor: "#161b22", font: { color: "#8b949e", size: 11 },
    showlegend: false, margin: { l: 34, r: 10, t: 6, b: 28 }, dragmode: "pan",
    shapes: buildShapes(),
    xaxis: { gridcolor: "#30363d", range: rng.x, zeroline: false },
    yaxis: { gridcolor: "#30363d", range: rng.y, zeroline: false, scaleanchor: "x", scaleratio: 1 },
  }, {
    displayModeBar: true, scrollZoom: false, responsive: true,
    modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"],
    edits: { shapePosition: true },
  }).then(() => { suppressRelayout = false; });
  if (!mapWired) wireMap();
}

function wireMap() {
  mapWired = true;
  const gd = document.getElementById("map");
  // Zoom only with Ctrl+wheel (plain wheel scrolls the page). Cursor-centered.
  gd.addEventListener("wheel", (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    const xa = gd.layout.xaxis.range, ya = gd.layout.yaxis.range;
    const factor = e.deltaY < 0 ? 0.85 : 1.18;
    const drag = gd.querySelector(".nsewdrag");
    let dx = (xa[0] + xa[1]) / 2, dy = (ya[0] + ya[1]) / 2;
    if (drag) {
      const bb = drag.getBoundingClientRect();
      const fx = (e.clientX - bb.left) / bb.width, fy = (e.clientY - bb.top) / bb.height;
      dx = xa[0] + fx * (xa[1] - xa[0]); dy = ya[1] - fy * (ya[1] - ya[0]);
    }
    const nx = [dx - (dx - xa[0]) * factor, dx + (xa[1] - dx) * factor];
    const ny = [dy - (dy - ya[0]) * factor, dy + (ya[1] - dy) * factor];
    view = { x: nx, y: ny };
    Plotly.relayout(gd, { "xaxis.range": nx, "yaxis.range": ny });
  }, { passive: false });
  gd.on("plotly_click", ev => {
    const p = ev.points.find(pt => pt.data.customdata);
    if (p) { const [kind, idx] = p.customdata[p.pointIndex]; selectEntity(kind, idx); }
  });
  gd.on("plotly_relayout", ev => {
    if (suppressRelayout) return;   // ignore our own programmatic redraws
    // Capture zoom/pan so later redraws keep the view.
    if (ev["xaxis.range[0]"] !== undefined)
      view = { x: [ev["xaxis.range[0]"], ev["xaxis.range[1]"]], y: [ev["yaxis.range[0]"], ev["yaxis.range[1]"]] };
    // Sync dragged/resized shapes back into the config.
    if (Object.keys(ev).some(k => k.startsWith("shapes["))) syncShapesToConfig();
  });
}

function syncShapesToConfig() {
  const gd = document.getElementById("map");
  const shapes = (gd.layout && gd.layout.shapes) || [];
  shapes.forEach((s, i) => {
    const m = shapeMeta[i]; if (!m || m.kind === "zone") return;  // zone rings are decoration only
    const cx = (s.x0 + s.x1) / 2, cy = (s.y0 + s.y1) / 2, rad = Math.abs(s.x1 - s.x0) / 2;
    if (m.kind === "region") {
      cfg.regions[m.idx].x = Math.round(cx); cfg.regions[m.idx].y = Math.round(cy);
      cfg.region_radius_km = Math.max(1, Math.round(rad));   // global customer radius
    } else {
      cfg.warehouses[m.idx].x = Math.round(cx); cfg.warehouses[m.idx].y = Math.round(cy);
    }
  });
  markDirty();
  if (selected) renderEntityEditor();
  renderSections();           // keep the left tables in sync
  schedulePreview();          // re-sample customers after a move/resize
}

// ----------------------------------------------------------------- entity selection / editor
function renderEntityList() {
  const root = document.getElementById("entity-list"); root.innerHTML = "";
  cfg.warehouses.forEach((w, i) => root.appendChild(entityChip("wh", i, w.name)));
  cfg.regions.forEach((r, i) => root.appendChild(entityChip("region", i, r.name)));
}
function entityChip(kind, idx, name) {
  const c = document.createElement("span");
  const sel = selected && selected.kind === kind && selected.idx === idx;
  c.className = "echip " + kind + (sel ? " sel" : "");
  c.textContent = name;
  c.onclick = () => selectEntity(kind, idx);
  return c;
}

function selectEntity(kind, idx) {
  selected = { kind, idx };
  renderEntityList();
  renderEntityEditor();
  drawMap(false);             // redraw to highlight the selected shape (keep view)
  const ed = document.getElementById("entity-editor");
  ed.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderEntityEditor() {
  const ed = document.getElementById("entity-editor");
  if (!selected) { ed.style.display = "none"; return; }
  ed.style.display = "block";
  const { kind, idx } = selected;
  const title = document.getElementById("entity-title");
  const body = document.getElementById("entity-body"); body.innerHTML = "";
  let fields;
  if (kind === "wh") {
    const w = cfg.warehouses[idx]; title.textContent = "Warehouse " + w.name;
    fields = [
      { path: `warehouses.${idx}.x`, label: "x", type: "number" },
      { path: `warehouses.${idx}.y`, label: "y", type: "number" },
      { path: `warehouses.${idx}.capacity_lines_per_day`, label: "capacity/day", type: "range", min: 50, max: 24000, step: 50, int: true },
      { path: `warehouses.${idx}.rank`, label: "rank", type: "range", min: 0, max: 1, step: 0.05 },
      { path: `warehouses.${idx}.assortment_fraction`, label: "assortment", type: "range", min: 0.1, max: 1, step: 0.05 },
    ];
  } else {
    const r = cfg.regions[idx]; title.textContent = "Region " + r.name;
    fields = [
      { path: `regions.${idx}.x`, label: "x", type: "number" },
      { path: `regions.${idx}.y`, label: "y", type: "number" },
      { path: `regions.${idx}.population_weight`, label: "demand weight", type: "range", min: 0.1, max: 4, step: 0.1 },
      { path: "region_radius_km", label: "customer radius (km, global)", type: "range", min: 5, max: 400, step: 5 },
    ];
  }
  // On change, redraw the map (so the shape/circle follows the slider) and resample.
  const onChange = (f, v) => { setPath(cfg, f.path, v); markDirty(); drawMap(false); schedulePreview(); };
  for (const f of fields) body.appendChild(makeField(f, onChange));
  // Delete button.
  const del = document.createElement("button"); del.className = "rowbtn"; del.textContent = "Delete";
  del.style.marginTop = "6px";
  del.onclick = () => {
    (kind === "wh" ? cfg.warehouses : cfg.regions).splice(idx, 1);
    selected = null; renderSections(); markDirty(); refreshPreview(false);
    document.getElementById("entity-editor").style.display = "none";
  };
  body.appendChild(del);
}

function addWarehouse() {
  const plane = cfg.geo.plane_size || 1000;
  cfg.warehouses.push({ warehouse_id: cfg.warehouses.length, name: "W" + cfg.warehouses.length,
    x: Math.round(plane / 2), y: Math.round(plane / 2), capacity_lines_per_day: 400,
    rank: 0.5, assortment_fraction: 1.0 });
  renderSections(); markDirty();
  resampleCustomers(false).then(() => selectEntity("wh", cfg.warehouses.length - 1));
}
function addRegion() {
  const plane = cfg.geo.plane_size || 1000;
  cfg.regions.push({ region_id: cfg.regions.length, name: "R" + cfg.regions.length,
    x: Math.round(plane / 2), y: Math.round(plane / 2 + 60), population_weight: 1.0 });
  renderSections(); markDirty();
  resampleCustomers(false).then(() => selectEntity("region", cfg.regions.length - 1));
}

// ----------------------------------------------------------------- state machine
function renderStateMachine() {
  const root = document.getElementById("state-machine"); root.innerHTML = "";
  STEPS.forEach((st, i) => {
    const d = document.createElement("div"); d.className = "sm-step"; d.dataset.i = i;
    const n = st.paths.length ? ` <span class="s-count">(${st.paths.length})</span>` : "";
    const head = document.createElement("div");
    head.innerHTML = `<div class="s-name">${st.name}${n}</div><div class="s-note">${st.note}</div>`;
    head.onclick = () => toggleStep(i);
    const detail = document.createElement("div"); detail.className = "sm-detail";
    d.append(head, detail);
    root.appendChild(d);
    if (i < STEPS.length - 1) { const a = document.createElement("div"); a.className = "arrow"; a.textContent = "↓"; root.appendChild(a); }
  });
}
// Expand/collapse a step's settings inline (right under the clicked box).
function toggleStep(i) {
  const box = document.querySelector(`.sm-step[data-i="${i}"]`);
  const open = box.classList.contains("open");
  document.querySelectorAll(".sm-step").forEach(e => e.classList.remove("open"));
  if (open) return;
  box.classList.add("open");
  const st = STEPS[i];
  const body = box.querySelector(".sm-detail"); body.innerHTML = "";
  if (!st.paths.length) {
    body.innerHTML = `<div class="muted">No editable settings here — fixed in v1.</div>`;
    return;
  }
  for (const path of st.paths) {
    const f = SECTIONS.flatMap(s => s.fields).find(ff => ff.path === path)
      || { path, label: path, type: "number" };
    body.appendChild(makeField(f, onFieldChange));
  }
}

// ----------------------------------------------------------------- validate / dirty
const schedule = debounce(() => { refreshPreview(); validate(); }, 350);
function markDirty() { dirty = true; document.getElementById("btn-save").disabled = (cfgSource !== "user"); }

async function validate() {
  const r = await fetch("/api/validate", { method: "POST", body: JSON.stringify(cfg) });
  const res = await r.json();
  const el = document.getElementById("validation");
  if (res.ok) { el.className = "ok"; el.textContent = "✓ valid"; }
  else { el.className = "errs"; el.textContent = res.errors.map(e => `${e.loc}: ${e.msg}`).join("\n"); }
}

// ----------------------------------------------------------------- load / actions
// Walk two config objects and collect changed scalar leaves as "key=value" tokens.
// Arrays (warehouses/regions/suppliers/profiles) are summarized as "<key>=edited" to keep
// the suffix short and filesystem-friendly. Used only to suggest a run name.
// Human-readable list of changed scalar fields ("path: old → new"), excluding description.
function changeLogLines(base, cur) {
  const out = [];
  const walk = (b, c, path) => {
    if (path === "description") return;
    if (Array.isArray(b) || Array.isArray(c)) {
      if (JSON.stringify(b) !== JSON.stringify(c)) out.push(`${path}: edited`);
      return;
    }
    if (b && c && typeof b === "object" && typeof c === "object") {
      for (const k of new Set([...Object.keys(b), ...Object.keys(c)]))
        walk(b[k], c[k], path ? `${path}.${k}` : k);
      return;
    }
    if (b !== c) out.push(`${path}: ${b} → ${c}`);
  };
  walk(base, cur, "");
  return out;
}

function changedParamsSuffix(base, cur) {
  if (!base || !cur) return "";
  const tokens = [];
  const walk = (b, c, key) => {
    if (Array.isArray(b) || Array.isArray(c)) {
      if (JSON.stringify(b) !== JSON.stringify(c)) tokens.push(`${key}=edited`);
      return;
    }
    if (b && c && typeof b === "object" && typeof c === "object") {
      for (const k of new Set([...Object.keys(b), ...Object.keys(c)])) {
        if (k === "name") continue;
        walk(b[k], c[k], k);
      }
      return;
    }
    if (b !== c) tokens.push(`${key}=${c}`);
  };
  walk(base, cur, "");
  return tokens.slice(0, 4).join("_").replace(/[^\w.=-]+/g, "_").replace(/^_+|_+$/g, "");
}

// Scenario description: a 3-line clamped preview that expands into an editable textarea on
// click. Text lives in cfg.description (stored in the scenario file); edits mark the config
// dirty, and Save appends a change-log of what parameters changed.
function renderDescription() {
  const view = document.getElementById("desc-view");
  const edit = document.getElementById("desc-edit");
  if (!view) return;
  const text = (cfg && cfg.description) || "";
  view.textContent = text || "No description yet — click to add one.";
  view.classList.toggle("empty", !text);
  view.style.display = "block"; edit.style.display = "none";
  view.onclick = () => {
    edit.value = cfg.description || "";
    view.style.display = "none"; edit.style.display = "block";
    edit.style.height = Math.max(120, edit.scrollHeight + 8) + "px";
    edit.focus();
  };
  edit.onblur = () => {
    if ((cfg.description || "") !== edit.value) { cfg.description = edit.value; markDirty(); }
    renderDescription();
  };
}

async function loadConfig(name) {
  const data = await (await fetch("/api/config/" + name)).json();
  if (data.error) { alert(data.error); return; }
  cfg = data.config; cfgName = data.name; cfgSource = data.source; dirty = false;
  baseCfg = JSON.parse(JSON.stringify(data.config));
  renderDescription();
  selected = null; view = null;
  document.getElementById("entity-editor").style.display = "none";
  document.getElementById("source-badge").textContent = cfgSource;
  document.getElementById("source-badge").className = "badge " + cfgSource;
  document.getElementById("btn-save").disabled = cfgSource !== "user";
  renderSections(); renderStateMachine(); refreshPreview(true); validate();
}

async function refreshConfigList(select) {
  const c = await (await fetch("/api/configs")).json();
  const sel = document.getElementById("config-select"); sel.innerHTML = "";
  const g1 = document.createElement("optgroup"); g1.label = "user";
  for (const n of c.user) { const o = document.createElement("option"); o.value = n; o.textContent = n; g1.appendChild(o); }
  const g2 = document.createElement("optgroup"); g2.label = "presets";
  for (const n of c.presets) { const o = document.createElement("option"); o.value = n; o.textContent = n; g2.appendChild(o); }
  if (c.user.length) sel.appendChild(g1);
  sel.appendChild(g2);
  if (select) sel.value = select;
}

// ----------------------------------------------------------------- run monitor (live)
let rmHist = null, rmSeq = -1, rmId = null;
const RM_LAYOUT = (extra) => Object.assign({
  paper_bgcolor: "#161b22", plot_bgcolor: "#161b22", font: { color: "#8b949e", size: 10 },
  showlegend: false, margin: { l: 36, r: 8, t: 4, b: 22 },
  // Force autorange: the empty pre-init plot leaves a [-1,1] range that would otherwise
  // stick and hide line traces (bar charts fit categories, so they were unaffected).
  xaxis: { gridcolor: "#30363d", autorange: true },
  yaxis: { gridcolor: "#30363d", autorange: true },
}, extra || {});

function startMonitor(runId, config, sourcing, seed) {
  rmId = runId; rmSeq = -1;
  rmHist = { day: [], cost: [], onTime: [], lost: [], stockout: [] };
  const mon = document.getElementById("run-monitor"); mon.style.display = "block";
  document.getElementById("rm-title").textContent = `${config} · ${sourcing} · seed ${seed}`;
  document.getElementById("rm-final").textContent = "";
  document.getElementById("rm-stop").disabled = false;
  mon.scrollIntoView({ behavior: "smooth", block: "nearest" });
  // Draw on the next frame so the just-shown panel has a real size, then let pushSnapshot
  // create the charts with Plotly.react (a pre-init empty plot would leave a stale axis
  // range that react does not re-autorange, which looked like empty charts).
  requestAnimationFrame(() => pollMonitor());
}

async function pollMonitor() {
  if (!rmId) return;
  const s = await (await fetch(`/api/run/${rmId}/snapshots?since=${rmSeq}`)).json();
  const stEl = document.getElementById("rm-status");
  stEl.textContent = s.status; stEl.className = "badge " + (s.status || "");
  for (const snap of (s.snapshots || [])) { pushSnapshot(snap); rmSeq = snap.seq; }
  const live = (s.snapshots || []).slice(-1)[0];
  if (live) {
    const c = live.compute || {};
    document.getElementById("rm-progress").textContent =
      `day ${live.sim_day} · orders ${c.orders_done}/${c.orders_total} · ${c.rate ?? 0}/s · ETA ${c.eta_s ?? 0}s`;
  }
  const finished = ["done", "failed", "stopped", "aborted"].includes(s.status);
  if (finished) {
    document.getElementById("rm-stop").disabled = true;
    if (s.status === "failed") {
      document.getElementById("rm-final").innerHTML = `<span class="errs">${s.error}</span>`;
    } else if (s.metrics) {
      const m = s.metrics;
      let txt = `cost/order ${m.total_cost_per_order?.toFixed(2)} · on-time ${(m.promise_hit_rate ?? 0).toFixed(3)} · split ${(m.split_rate ?? 0).toFixed(3)}`;
      if (m.gap_to_hindsight != null) txt += ` · gap ${(m.gap_to_hindsight * 100).toFixed(1)}%`;
      txt += `<br>Open it in the <b>Runs</b> tab for the full analysis and inspector.`;
      document.getElementById("rm-final").innerHTML = txt;
    }
    loadRuns();   // the finished run now appears in the Runs tab
    rmId = null;
    return;
  }
  setTimeout(pollMonitor, 600);
}

const RM_CFG = { responsive: true, displayModeBar: false };
function pushSnapshot(s) {
  rmHist.day.push(s.sim_day);
  rmHist.cost.push(s.cost_per_order);
  rmHist.onTime.push(s.on_time_rate);
  rmHist.lost.push(s.lost_sales_rate);
  rmHist.stockout.push(s.stockout_share);
  // Pass fresh array copies so Plotly.react detects the change and re-autoranges
  // (it ignores in-place array mutation, which otherwise freezes the axis range).
  const X = rmHist.day.slice();
  Plotly.react("rm-cost", [{ x: X, y: rmHist.cost.slice(), mode: "lines", line: { color: "#2f81f7" } }], RM_LAYOUT(), RM_CFG);
  Plotly.react("rm-service", [
    { x: X, y: rmHist.onTime.slice(), mode: "lines", line: { color: "#3fb950" } },
    { x: X, y: rmHist.lost.slice(), mode: "lines", yaxis: "y2", line: { color: "#f85149" } },
  ], RM_LAYOUT({ yaxis: { gridcolor: "#30363d", range: [0, 1] },
    yaxis2: { overlaying: "y", side: "right", gridcolor: "transparent" } }), RM_CFG);
  Plotly.react("rm-stockout", [{ x: X, y: rmHist.stockout.slice(), mode: "lines", line: { color: "#d29922" } }],
    RM_LAYOUT({ yaxis: { gridcolor: "#30363d", range: [0, 1] } }), RM_CFG);
  if (s.wh && s.wh.util) {
    Plotly.react("rm-util", [{ type: "bar", x: s.wh.util.map((_, i) => "W" + i), y: s.wh.util.slice(),
      marker: { color: s.wh.util.map(u => u > 1 ? "#f85149" : "#2f81f7") } }], RM_LAYOUT(), RM_CFG);
  }
}

async function stopRun() {
  if (rmId) await fetch(`/api/run/${rmId}/stop`, { method: "POST" });
  document.getElementById("rm-stop").disabled = true;
}

// ----------------------------------------------------------------- tabs
function showTab(name) {
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".tabpane").forEach(p => p.classList.toggle("active", p.id === "tab-" + name));
  localStorage.setItem("osb_tab", name);
  setTimeout(() => window.dispatchEvent(new Event("resize")), 60);
  if (name === "runs") loadRuns();
  if (name === "comparisons") loadComparisons();
}

// ----------------------------------------------------------------- per-day analysis (reusable)
function sumSeries(...arrs) {
  const n = arrs[0].length; const out = new Array(n).fill(0);
  for (const a of arrs) for (let i = 0; i < n; i++) out[i] += (a[i] || 0);
  return out;
}

const LEG = { orientation: "h", y: 1.15, font: { size: 9 } };
async function renderAnalysis(runDir, ids) {
  const a = await (await fetch("/api/analysis?run=" + encodeURIComponent(runDir))).json();
  if (a.error || !a.days || !a.days.length) return;
  const d = a.days, s = a.series, cfgp = { responsive: true, displayModeBar: false };
  Plotly.react(ids.orders, [
    { x: d, y: s.created, mode: "lines", name: "created", line: { color: "#8b949e" } },
    { x: d, y: s.delivered, mode: "lines", name: "delivered", line: { color: "#2f81f7" } },
  ], RM_LAYOUT({ showlegend: true, legend: LEG }), cfgp);
  Plotly.react(ids.ontime, [
    { x: d, y: s.on_time_rate, mode: "lines", name: "on-time", line: { color: "#3fb950" } },
    { x: d, y: s.planfact_days, mode: "lines", name: "plan-fact", yaxis: "y2", line: { color: "#d29922" } },
  ], RM_LAYOUT({ showlegend: true, legend: LEG, yaxis: { gridcolor: "#30363d", range: [0, 1] },
    yaxis2: { overlaying: "y", side: "right", gridcolor: "transparent", zeroline: true } }), cfgp);
  Plotly.react(ids.cost, [
    { x: d, y: s.shipping, stackgroup: "c", name: "shipping", line: { color: "#2f81f7" } },
    { x: d, y: s.holding, stackgroup: "c", name: "holding", line: { color: "#d29922" } },
    { x: d, y: s.purchase, stackgroup: "c", name: "purchase", line: { color: "#3fb950" } },
    { x: d, y: s.penalty, stackgroup: "c", name: "penalty", line: { color: "#f85149" } },
  ], RM_LAYOUT({ showlegend: true, legend: LEG }), cfgp);
  Plotly.react(ids.split, [
    { x: d, y: s.parcels_per_order, mode: "lines", name: "parcels/order", line: { color: "#8957e5" } },
    { x: d, y: s.lost, mode: "lines", name: "lost sales", yaxis: "y2", line: { color: "#f85149" } },
  ], RM_LAYOUT({ showlegend: true, legend: LEG,
    yaxis2: { overlaying: "y", side: "right", gridcolor: "transparent" } }), cfgp);
  // Lateness distribution: not just late/not, but how late (bucketed days).
  if (ids.late && a.lateness) {
    const L = a.lateness;
    const colors = L.labels.map((_, i) => i === 0 ? "#3fb950" : `rgba(248,81,73,${0.35 + 0.08 * i})`);
    Plotly.react(ids.late, [
      { x: L.labels, y: L.counts, type: "bar", marker: { color: colors },
        hovertemplate: "%{x}: %{y} orders<extra></extra>" },
    ], RM_LAYOUT({ showlegend: false, yaxis: { gridcolor: "#30363d", title: "orders" } }), cfgp);
    const t = document.getElementById(ids.lateTitle);
    if (t && L.late_orders != null) {
      t.textContent = `lateness distribution — ${L.late_orders}/${L.n} late`
        + (L.mean != null ? ` · mean ${L.mean}d · p90 ${L.p90}d · max ${L.max}d` : "");
    }
  }
}

// ----------------------------------------------------------------- Runs tab
let selectedRuns = new Set();
async function loadRuns() {
  const runs = await (await fetch("/api/results")).json();
  const root = document.getElementById("runs-list"); root.innerHTML = "";
  if (!runs.length) { root.innerHTML = '<div class="muted">No runs yet — launch one from the Editor tab.</div>'; return; }
  runs.forEach(r => {
    const row = document.createElement("div"); row.className = "listrow";
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = selectedRuns.has(r.run_dir);
    cb.onchange = () => cb.checked ? selectedRuns.add(r.run_dir) : selectedRuns.delete(r.run_dir);
    const label = document.createElement("div"); label.className = "grow";
    let txt = `<b>${r.config}</b> · ${r.policy}`;
    if (r.cost_per_order != null) txt += ` — cost/ord ${r.cost_per_order.toFixed(2)}`;
    if (r.on_time != null) txt += ` · on-time ${r.on_time.toFixed(3)}`;
    if (r.gap != null) txt += ` · gap ${(r.gap * 100).toFixed(0)}%`;
    if (r.mtime) txt += ` <span class="muted">· ${new Date(r.mtime * 1000).toLocaleString()}</span>`;
    label.innerHTML = txt;
    label.onclick = () => openRun(r.run_dir, row);
    row.append(cb, label); root.appendChild(row);
  });
}

async function openRun(runDir, row) {
  document.querySelectorAll("#runs-list .listrow").forEach(e => e.classList.remove("sel"));
  if (row) row.classList.add("sel");
  document.getElementById("run-detail").style.display = "block";
  document.getElementById("rd-title").textContent = runDir;
  document.getElementById("rd-frame").style.display = "none";
  document.getElementById("rd-inspect").dataset.run = runDir;
  const info = await (await fetch("/api/runinfo?run=" + encodeURIComponent(runDir))).json();
  const m = info.metrics || {};
  let txt = `cost/order ${(m.total_cost_per_order ?? 0).toFixed(2)} · on-time ${(m.promise_hit_rate ?? 0).toFixed(3)} · split ${(m.split_rate ?? 0).toFixed(3)} · lost ${(m.lost_sales_rate ?? 0).toFixed(3)}`;
  if (m.gap_to_hindsight != null) txt += ` · gap ${(m.gap_to_hindsight * 100).toFixed(1)}%`;
  document.getElementById("rd-metrics").textContent = txt;
  renderAnalysis(runDir, { orders: "ra-orders", ontime: "ra-ontime", cost: "ra-cost", split: "ra-split",
    late: "ra-late", lateTitle: "ra-late-title" });
}

async function openRunInspector() {
  const runDir = document.getElementById("rd-inspect").dataset.run;
  if (!runDir) return;
  const r = await (await fetch("/api/inspect?run=" + encodeURIComponent(runDir))).json();
  if (r.error) { alert(r.error); return; }
  const f = document.getElementById("rd-frame"); f.src = r.url; f.style.display = "block";
  const link = document.getElementById("rd-inspect-link"); link.href = r.url; link.style.display = "inline";
}

async function compareSelected() {
  const runs = [...selectedRuns];
  if (!runs.length) { alert("tick at least one run to compare"); return; }
  const name = document.getElementById("cmp-name").value.trim() || `comparison (${runs.length} runs)`;
  const r = await (await fetch("/api/comparisons", { method: "POST",
    body: JSON.stringify({ name, runs }) })).json();
  selectedRuns = new Set();
  document.getElementById("cmp-name").value = "";
  await loadComparisons();
  showTab("comparisons");
  openComparison(r.id);
}

// ----------------------------------------------------------------- Comparisons tab
async function loadComparisons() {
  const items = await (await fetch("/api/comparisons")).json();
  const root = document.getElementById("comparisons-list"); root.innerHTML = "";
  document.getElementById("comparisons-empty").style.display = items.length ? "none" : "block";
  items.slice().reverse().forEach(it => {
    const row = document.createElement("div"); row.className = "listrow";
    const label = document.createElement("div"); label.className = "grow";
    label.innerHTML = `<b>${it.name}</b> · ${it.runs.length} runs`;
    label.onclick = () => openComparison(it.id, row);
    const del = document.createElement("button"); del.className = "rowbtn"; del.textContent = "×";
    del.onclick = async (e) => { e.stopPropagation(); await fetch("/api/comparisons/" + it.id, { method: "DELETE" }); loadComparisons(); };
    row.append(label, del); root.appendChild(row);
  });
}

async function openComparison(id, row) {
  if (row) { document.querySelectorAll("#comparisons-list .listrow").forEach(e => e.classList.remove("sel")); row.classList.add("sel"); }
  const data = await (await fetch("/api/comparisons/" + id)).json();
  if (data.error) { alert(data.error); return; }
  renderComparison(data);
}

function renderComparison(data) {
  document.getElementById("comparison-detail").style.display = "block";
  document.getElementById("cd-title").textContent = data.name;
  const runs = data.runs, color = (i) => WH_COLORS[i % WH_COLORS.length];
  let t = "<table><tr><th>run</th><th>cost/order</th><th>on-time</th><th>split</th><th>lost</th><th>gap</th></tr>";
  runs.forEach(r => {
    const m = r.metrics;
    t += `<tr><td>${r.label}</td><td>${(m.total_cost_per_order ?? 0).toFixed(2)}</td>`
      + `<td>${(m.promise_hit_rate ?? 0).toFixed(3)}</td><td>${(m.split_rate ?? 0).toFixed(3)}</td>`
      + `<td>${(m.lost_sales_rate ?? 0).toFixed(3)}</td>`
      + `<td>${m.gap_to_hindsight != null ? (m.gap_to_hindsight * 100).toFixed(1) + "%" : "—"}</td></tr>`;
  });
  document.getElementById("cd-table").innerHTML = t + "</table>";
  const cfgp = { responsive: true, displayModeBar: false };
  const line = (id, pick, extra) => {
    const traces = runs.map((r, i) => ({ x: r.series.days, y: pick(r.series.series),
      mode: "lines", name: r.label, line: { color: color(i) } }));
    Plotly.react(id, traces, RM_LAYOUT(Object.assign({ showlegend: true, legend: LEG }, extra || {})), cfgp);
  };
  line("cm-ontime", s => s.on_time_rate, { yaxis: { gridcolor: "#30363d", range: [0, 1] } });
  line("cm-cost", s => sumSeries(s.shipping, s.holding, s.purchase, s.penalty));
  line("cm-gap", s => s.planfact_days);
  Plotly.react("cm-pareto", [{
    x: runs.map(r => r.metrics.total_cost_per_order), y: runs.map(r => r.metrics.promise_hit_rate),
    mode: "markers+text", type: "scatter", text: runs.map(r => r.label), textposition: "top center",
    marker: { size: 11, color: runs.map((_, i) => color(i)) },
  }], RM_LAYOUT({ xaxis: { gridcolor: "#30363d", title: { text: "cost/order", font: { size: 9 } } },
    yaxis: { gridcolor: "#30363d", title: { text: "on-time", font: { size: 9 } } } }), cfgp);
}

// ----------------------------------------------------------------- collapsible panels
function initCollapsibles() {
  const KEY = "osb_collapsed";
  const saved = new Set(JSON.parse(localStorage.getItem(KEY) || "[]"));
  document.querySelectorAll(".panel[data-collapse]").forEach(p => {
    p.classList.add("collapsible");
    const key = p.dataset.collapse;
    if (saved.has(key)) p.classList.add("collapsed");
    const h = p.querySelector("h3");
    if (!h) return;
    h.addEventListener("click", () => {
      p.classList.toggle("collapsed");
      const set = new Set(JSON.parse(localStorage.getItem(KEY) || "[]"));
      p.classList.contains("collapsed") ? set.add(key) : set.delete(key);
      localStorage.setItem(KEY, JSON.stringify([...set]));
      // Re-fit Plotly charts revealed by expanding.
      if (!p.classList.contains("collapsed")) setTimeout(() => window.dispatchEvent(new Event("resize")), 60);
    });
  });
}

async function init() {
  const pol = await (await fetch("/api/policies")).json();
  const ss = document.getElementById("sourcing-select");
  for (const p of pol.sourcing) { const o = document.createElement("option"); o.value = p; o.textContent = p; ss.appendChild(o); }
  ss.value = "nearest";
  const rs = document.getElementById("replenishment-select");
  for (const p of (pol.replenishment || [])) { const o = document.createElement("option"); o.value = p; o.textContent = p; rs.appendChild(o); }
  rs.value = "base_stock";
  renderReplParams();

  await refreshConfigList();
  document.getElementById("config-select").onchange = (e) => loadConfig(e.target.value);
  document.getElementById("add-wh").onclick = addWarehouse;
  document.getElementById("add-region").onclick = addRegion;
  document.getElementById("reset-view").onclick = () => { view = null; drawMap(true); };
  document.getElementById("show-zones").onchange = (e) => { showZones = e.target.checked; drawMap(); };
  document.getElementById("btn-validate").onclick = validate;
  document.getElementById("btn-save").onclick = async () => {
    // Append an edit log of what changed (vs the last loaded/saved state) to the description.
    const changes = baseCfg ? changeLogLines(baseCfg, cfg) : [];
    if (changes.length) {
      const stamp = new Date().toISOString().slice(0, 16).replace("T", " ");
      const entry = `[${stamp}] changed: ${changes.join(", ")}`;
      cfg.description = (cfg.description ? cfg.description.trimEnd() + "\n" : "") + entry;
    }
    const r = await (await fetch("/api/save", { method: "POST", body: JSON.stringify({ name: cfgName, config: cfg }) })).json();
    if (r.ok) {
      dirty = false; baseCfg = JSON.parse(JSON.stringify(cfg)); renderDescription();
      document.getElementById("validation").textContent = "✓ saved " + r.path;
    } else document.getElementById("validation").textContent = "save failed: " + JSON.stringify(r.errors);
  };
  document.getElementById("btn-clone").onclick = async () => {
    const dst = prompt("New config name:", (cfgName || "config") + "_copy");
    if (!dst) return;
    const r = await (await fetch("/api/clone", { method: "POST", body: JSON.stringify({ src: cfgName, dst }) })).json();
    if (!r.ok) { alert("clone failed: " + JSON.stringify(r.errors)); return; }
    await refreshConfigList(dst); loadConfig(dst);
  };
  document.getElementById("btn-run").onclick = async () => {
    if (cfgSource === "user" && dirty) {
      await fetch("/api/save", { method: "POST", body: JSON.stringify({ name: cfgName, config: cfg }) });
      dirty = false;
    }
    const sourcing = document.getElementById("sourcing-select").value;
    const replenishment = document.getElementById("replenishment-select").value;
    const seed = +document.getElementById("seed-input").value;
    // Suggest a run name: <config>_s<seed> plus any changed params, so a run with tweaked
    // parameters lands in its own folder and never overwrites the baseline run. The policy
    // is a subfolder of this, so different policies on the same params still share one world.
    const suffix = changedParamsSuffix(baseCfg, cfg);
    let suggested = `${cfgName}_s${seed}`;
    if (suffix) suggested += `__${suffix}`;
    const runName = prompt("Run name (its own entry; different name = no overwrite):", suggested);
    if (runName === null) return;  // cancelled
    // Always send the edited config so tweaked params apply even for an unsaved preset.
    const body = { config: cfgName, sourcing, replenishment, seed, run_name: runName,
                   config_json: cfg, replenishment_params: replParamsForRun() };
    const r = await (await fetch("/api/run", { method: "POST", body: JSON.stringify(body) })).json();
    if (r.error) { alert("Run failed: " + r.error); return; }
    startMonitor(r.run_id, runName, sourcing, seed);
  };
  document.getElementById("btn-sourcing-help").onclick = () => showPolicyHelp("sourcing");
  document.getElementById("btn-replenishment-help").onclick = () => showPolicyHelp("replenishment");
  document.getElementById("rm-stop").onclick = stopRun;
  // Tabs.
  document.querySelectorAll(".tab").forEach(t => t.onclick = () => showTab(t.dataset.tab));
  // Runs tab.
  document.getElementById("refresh-runs").onclick = loadRuns;
  document.getElementById("compare-sel").onclick = compareSelected;
  document.getElementById("rd-inspect").onclick = openRunInspector;
  initCollapsibles();

  const first = document.getElementById("config-select").value;
  if (first) loadConfig(first);
  // Restore the last active tab.
  const tab = localStorage.getItem("osb_tab");
  if (tab && tab !== "editor") showTab(tab);
}
init();
