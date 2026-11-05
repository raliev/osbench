"use strict";
// osbench inspector: order points on a map over time, with drill into order / warehouse / SKU.

let meta = null;
let curDay = 0;
let selectedOrder = null;        // {warehouses:[...], x, y} for drawing links on the map
const cursorCharts = new Set();  // chart div ids that carry a time cursor line
const WH_COLORS = ["#2f81f7", "#3fb950", "#d29922", "#f85149", "#a371f7", "#8957e5",
  "#db61a2", "#1f6feb", "#2da44e", "#bf8700"];
const STATUS_COLOR = {
  delivered: "#3fb950", late: "#f0883e", partial: "#a371f7", pending: "#d29922", lost: "#f85149",
  // legacy keys kept so older snapshots still render.
  ok: "#3fb950", split: "#d29922",
};
const STATUS_ORDER = ["delivered", "late", "partial", "pending", "lost"];
const STATUS_LABEL = {
  delivered: "delivered on time", late: "delivered late", partial: "incomplete delivery",
  pending: "in pipeline", lost: "lost",
};

function baseLayout(extra) {
  return Object.assign({
    paper_bgcolor: "#161b22", plot_bgcolor: "#161b22",
    font: { color: "#8b949e", size: 11 }, showlegend: false,
    margin: { l: 40, r: 10, t: 6, b: 26 },
    xaxis: { gridcolor: "#30363d" }, yaxis: { gridcolor: "#30363d" },
  }, extra || {});
}

function cursorShape() {
  return { type: "line", xref: "x", yref: "paper", x0: curDay, x1: curDay, y0: 0, y1: 1,
    line: { color: "#e6edf3", width: 1, dash: "dot" } };
}

async function getJSON(url) { return (await fetch(url)).json(); }

// ----------------------------------------------------------------- map / day
async function loadDay(day) {
  curDay = day;
  const d = await getJSON("/api/day/" + day);
  document.getElementById("day-label").textContent = day;
  document.getElementById("n-orders").textContent = d.n_orders;
  drawMap(d);
  // Move the time cursor on every open time-series chart.
  for (const id of cursorCharts) {
    try { Plotly.relayout(id, { "shapes[0].x0": day, "shapes[0].x1": day }); } catch (e) {}
  }
}

function drawMap(d) {
  const traces = [];
  const mode = document.getElementById("color-mode").value;
  const legend = document.getElementById("map-legend");
  const hov = "order %{customdata[0]}<br>%{customdata[1]} lines ("
    + "%{customdata[4]} delivered) · %{customdata[2]}<br>cost %{customdata[3]}"
    + " · %{customdata[5]}<extra></extra>";
  const cd = o => [o.order_id, o.n_lines, o.status, o.cost, o.delivered_lines,
    o.lateness_days === null ? "lateness n/a" : o.lateness_days + "d late"];

  if (mode === "cost" || mode === "lateness") {
    // Continuous colouring. cost: company cost per order. lateness: days late (only fully
    // delivered orders carry a value; others are drawn grey). The slider shifts the day
    // cohort, so both light up as the backlog grows.
    const isLate = mode === "lateness";
    const [vmin, vmax] = (isLate ? d.lateness_range : d.cost_range) || [0, 1];
    const val = o => isLate ? o.lateness_days : o.cost;
    const have = d.orders.filter(o => val(o) !== null);
    const none = d.orders.filter(o => val(o) === null);
    if (none.length) traces.push({  // orders without a value (pending/partial) in grey
      x: none.map(o => o.x), y: none.map(o => o.y), mode: "markers", type: "scattergl",
      marker: { size: 6, color: "#484f58", opacity: 0.5 },
      customdata: none.map(cd), hovertemplate: hov, name: "n/a",
    });
    traces.push({
      x: have.map(o => o.x), y: have.map(o => o.y), mode: "markers", type: "scattergl",
      marker: {
        size: 7, opacity: 0.85, color: have.map(val),
        colorscale: isLate ? "YlOrRd" : "Viridis", cmin: vmin, cmax: vmax, showscale: true,
        colorbar: { thickness: 10, len: 0.5, x: 1.02,
          title: { text: isLate ? "days late" : "cost", side: "right" } },
      },
      customdata: have.map(cd), hovertemplate: hov, name: mode,
    });
    const unit = isLate ? "d" : "";
    legend.textContent = `${isLate ? "lateness" : "cost"} ${vmin.toFixed(1)}–${vmax.toFixed(1)}${unit}`
      + (none.length ? `  ·  ${none.length} n/a` : "");
  } else {
    // Group by outcome or by chosen warehouse.
    const groups = {};
    for (const o of d.orders) {
      const key = mode === "warehouse" ? (o.status === "lost" ? "lost" : "wh" + o.wh) : o.status;
      (groups[key] = groups[key] || []).push(o);
    }
    for (const key in groups) {
      const g = groups[key];
      const color = key.startsWith("wh")
        ? WH_COLORS[parseInt(key.slice(2)) % WH_COLORS.length]
        : (STATUS_COLOR[key] || "#8b949e");
      traces.push({
        x: g.map(o => o.x), y: g.map(o => o.y), mode: "markers", type: "scattergl",
        marker: { size: 7, color: color, opacity: 0.75 },
        customdata: g.map(cd), hovertemplate: hov, name: key,
      });
    }
    // Outcome legend: coloured swatches with counts for the current day cohort.
    if (mode === "status") {
      legend.innerHTML = STATUS_ORDER.filter(s => groups[s]).map(s =>
        `<span style="color:${STATUS_COLOR[s]}">■</span> ${STATUS_LABEL[s]} ${groups[s].length}`
      ).join("  ");
    } else { legend.textContent = ""; }
  }
  // Link lines from the selected order's customer to its warehouse(s).
  if (selectedOrder && selectedOrder.warehouses.length) {
    const xs = [], ys = [];
    for (const w of selectedOrder.warehouses) {
      xs.push(selectedOrder.x, meta.wh_x[w], null);
      ys.push(selectedOrder.y, meta.wh_y[w], null);
    }
    traces.push({ x: xs, y: ys, mode: "lines", type: "scatter",
      line: { color: "#e6edf3", width: 1.5 }, hoverinfo: "skip" });
  }
  // Warehouses sized by current stock.
  const maxStock = Math.max(1, ...d.wh_stock);
  traces.push({
    x: meta.wh_x, y: meta.wh_y, mode: "markers+text", type: "scatter",
    text: meta.wh_names, textposition: "top center",
    marker: { size: d.wh_stock.map(v => 14 + 26 * v / maxStock),
      color: meta.wh_x.map((_, i) => WH_COLORS[i % WH_COLORS.length]),
      line: { color: "#fff", width: 1 } },
    customdata: meta.wh_names.map((n, i) => i),
    hovertemplate: "%{text}<br>stock %{marker.size:.0f}<extra></extra>", name: "wh",
  });

  Plotly.react("c-map", traces, baseLayout({ margin: { l: 30, r: 10, t: 6, b: 26 } }));
  const div = document.getElementById("c-map");
  div.removeAllListeners && div.removeAllListeners("plotly_click");
  div.on("plotly_click", ev => {
    const p = ev.points[0];
    if (p.data.name === "wh") openWarehouse(p.pointIndex);
    else if (p.customdata) openOrder(p.customdata[0]);
  });
}

// ----------------------------------------------------------------- order
async function openOrder(id) {
  const o = await getJSON("/api/order/" + id);
  const el = document.getElementById("p-order");
  if (!o.found) { el.innerHTML = "<h3>Order " + id + "</h3><div class='hint'>not found</div>"; return; }
  selectedOrder = { warehouses: o.warehouses, x: o.x, y: o.y };
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  loadDay(curDay);  // redraw map links

  const chipCls = { delivered: "ok", in_transit: "transit", pending: "pending", lost: "lost" };

  // Summary cards: ordered vs delivered/in-transit/pending/lost (lines and units), so a
  // discrepancy like "ordered 6, delivered 2" is answered at a glance.
  const s = o.summary || {};
  const card = (k, v) => `<div class="sumcard"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  const fmt = a => a ? `${a.lines}L · ${a.units}u` : "—";
  let summary = "<div class='sumgrid'>"
    + card("ordered", fmt(s.ordered))
    + card("delivered", fmt(s.delivered))
    + card("in transit", fmt(s.in_transit))
    + card("pending", fmt(s.pending))
    + card("lost", fmt(s.lost))
    + "</div>";

  // Parcels: one per warehouse, with ship/deliver day, on-time vs promise, and cost.
  let parcels = "<table><tr><th>wh</th><th>lines</th><th>units</th><th>shipped</th>"
    + "<th>delivered</th><th>cost</th><th>status</th></tr>";
  for (const p of (o.parcels || [])) {
    let st = p.status, cls = chipCls[st] || "pending";
    if (p.status === "delivered" && p.on_time === false) { st = "late"; cls = "late"; }
    parcels += `<tr><td><span class="clickable" onclick="openWarehouse(${p.wh})">${meta.wh_names[p.wh]}</span></td>`
      + `<td>${p.n_lines}</td><td>${p.units}</td>`
      + `<td>${p.shipped_day ?? "—"}</td><td>${p.delivered_day ?? "—"}</td>`
      + `<td>${p.ship_cost ?? "—"}</td>`
      + `<td><span class="chip ${cls}">${st}</span></td></tr>`;
  }
  parcels += "</table>";

  let lines = "<table><tr><th>line</th><th>SKU</th><th>qty</th><th>wh</th><th>status</th></tr>";
  for (const l of o.lines) {
    const whtxt = l.wh === null ? "—" : meta.wh_names[l.wh];
    lines += `<tr><td>${l.line_id}</td>`
      + `<td><span class="clickable" onclick="openSku(${l.sku})">#${l.sku}</span></td>`
      + `<td>${l.qty}</td><td>${whtxt}</td>`
      + `<td><span class="chip ${chipCls[l.status] || 'pending'}">${l.status}</span></td></tr>`;
  }
  lines += "</table>";

  let chron = "<table><tr><th>day</th><th>t</th><th>event</th><th>wh</th><th>lines</th><th>cost</th></tr>";
  for (const st of o.stages) {
    const whtxt = (st.wh === null || st.wh === undefined) ? "" : meta.wh_names[st.wh];
    chron += `<tr><td>${st.day}</td><td>${st.t}</td><td>${st.event}</td>`
      + `<td>${whtxt}</td><td>${st.qty ?? ""}</td><td>${st.cost ?? ""}</td></tr>`;
  }
  chron += "</table>";

  const whs = o.warehouses.map(w =>
    `<span class="clickable" onclick="openWarehouse(${w})">${meta.wh_names[w]}</span>`).join(", ") || "—";
  const prom = o.promise_day == null ? "" : ` · promised by day ${o.promise_day}`;
  el.innerHTML = `<h3>Order ${o.order_id}</h3>`
    + `<div class="muted">day ${o.day} · region ${meta.region_names[o.region] ?? o.region}`
    + ` · customer (${o.x.toFixed(0)}, ${o.y.toFixed(0)})${prom} · warehouses: ${whs}</div>`
    + summary
    + `<h4 style="margin:8px 6px 2px">Parcels</h4>${parcels}`
    + `<h4 style="margin:10px 6px 2px">Lines</h4>${lines}`
    + `<h4 style="margin:10px 6px 2px">Chronology <span class="muted" style="font-weight:400">(qty = lines in parcel)</span></h4>${chron}`;
}

// ----------------------------------------------------------------- warehouse
async function openWarehouse(wh) {
  const w = await getJSON("/api/warehouse/" + wh + "?day=" + curDay);
  document.getElementById("p-wh").style.display = "block";
  document.getElementById("p-wh").scrollIntoView({ behavior: "smooth", block: "nearest" });
  const body = document.getElementById("wh-body");
  // Summary cards: capacity + assigned/shipped/delivered/backlog, so a pick-capacity
  // bottleneck (assigned >> shipped -> growing backlog) is visible immediately.
  const su = w.summary || {};
  const card = (k, v) => `<div class="sumcard"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  const backlogCls = (su.backlog_lines > 0) ? "pending" : "ok";
  const summary = "<div class='sumgrid'>"
    + card("capacity/day", su.capacity_lines_per_day ?? "—")
    + card("assigned", su.assigned_lines ?? "—")
    + card("shipped", su.shipped_lines ?? "—")
    + card("delivered", su.delivered_lines ?? "—")
    + `<div class="sumcard"><div class="v"><span class="chip ${backlogCls}">${su.backlog_lines ?? "—"}</span></div><div class="k">backlog</div></div>`
    + "</div>";
  body.innerHTML = `<div class="muted">${w.name} · stock @day ${curDay}</div>`
    + summary
    + `<div id="wh-stock" class="chart"></div>`
    + `<h4 style="margin:8px 6px 2px">Workload: assigned vs shipped lines/day</h4><div id="wh-work" class="chart"></div>`
    + `<h4 style="margin:8px 6px 2px">Parcels shipped / units received per day</h4><div id="wh-flow" class="chart"></div>`
    + `<h4 style="margin:8px 6px 2px">Top SKUs @day ${curDay}</h4><div id="wh-top"></div>`;
  document.getElementById("p-wh").querySelector("h3").textContent = "Warehouse " + w.name;

  Plotly.newPlot("wh-stock", [{ x: w.stock_day, y: w.stock, mode: "lines",
    line: { color: WH_COLORS[wh % WH_COLORS.length] }, name: "stock" }],
    baseLayout({ shapes: [cursorShape()], yaxis: { gridcolor: "#30363d", title: "total stock" } }));
  // Assigned vs shipped lines/day, with the daily pick capacity as a reference line.
  const workTraces = [
    { x: w.assigned_day, y: w.assigned, mode: "lines", name: "assigned lines/day", line: { color: "#58a6ff" } },
    { x: w.shipped_lines_day, y: w.shipped_lines, mode: "lines", name: "shipped lines/day", line: { color: "#d29922" } },
  ];
  const workShapes = [cursorShape()];
  if (w.capacity) workShapes.push({ type: "line", xref: "paper", x0: 0, x1: 1,
    y0: w.capacity, y1: w.capacity, line: { color: "#f85149", width: 1, dash: "dot" } });
  Plotly.newPlot("wh-work", workTraces, baseLayout({ shapes: workShapes, showlegend: true,
    legend: { orientation: "h", y: 1.2, font: { size: 10 } } }));
  Plotly.newPlot("wh-flow", [
    { x: w.ship_day, y: w.ship, mode: "lines", name: "parcels shipped/day", line: { color: "#d29922" } },
    { x: w.recv_day, y: w.recv, mode: "lines", name: "units received/day", line: { color: "#3fb950" } },
  ], baseLayout({ shapes: [cursorShape()], showlegend: true,
    legend: { orientation: "h", y: 1.2, font: { size: 10 } } }));
  cursorCharts.add("wh-stock"); cursorCharts.add("wh-work"); cursorCharts.add("wh-flow");

  let t = "<table><tr><th>SKU</th><th>book</th></tr>";
  for (const s of w.top_sku) {
    t += `<tr><td><span class="clickable" onclick="openSku(${s.sku})">#${s.sku}</span></td>`
      + `<td>${s.book}</td></tr>`;
  }
  document.getElementById("wh-top").innerHTML = t + "</table>";
}

// ----------------------------------------------------------------- sku
async function openSku(sku) {
  const s = await getJSON("/api/sku/" + sku);
  document.getElementById("p-sku").style.display = "block";
  document.getElementById("p-sku").scrollIntoView({ behavior: "smooth", block: "nearest" });
  document.getElementById("p-sku").querySelector("h3").textContent = "SKU #" + s.sku;
  const body = document.getElementById("sku-body");
  body.innerHTML = `<div class="muted">category ${s.category} · price ${s.price}`
    + ` · assigned total ${s.assigned_total} · lost lines ${s.lost_lines}</div>`
    + `<div id="sku-stock" class="chart"></div><div id="sku-assigned" class="chart"></div>`;

  const traces = [];
  for (const wh in s.stock_series) {
    const ser = s.stock_series[wh];
    traces.push({ x: ser.day, y: ser.book, mode: "lines", name: meta.wh_names[wh],
      line: { color: WH_COLORS[wh % WH_COLORS.length] } });
  }
  Plotly.newPlot("sku-stock", traces, baseLayout({ shapes: [cursorShape()], showlegend: true,
    legend: { orientation: "h", y: 1.2, font: { size: 10 } }, yaxis: { gridcolor: "#30363d", title: "stock by wh" } }));
  Plotly.newPlot("sku-assigned", [{ x: s.assigned_day, y: s.assigned, type: "bar",
    marker: { color: "#2f81f7" }, name: "assigned/day" }],
    baseLayout({ shapes: [cursorShape()], yaxis: { gridcolor: "#30363d", title: "assigned/day" } }));
  cursorCharts.add("sku-stock"); cursorCharts.add("sku-assigned");
}
window.openSku = openSku;
window.openWarehouse = openWarehouse;

// ----------------------------------------------------------------- init
async function init() {
  meta = await getJSON("/api/meta");
  const slider = document.getElementById("day-slider");
  slider.min = meta.day_min; slider.max = meta.day_max; slider.value = meta.day_min;
  document.getElementById("day-max").textContent = meta.day_max;
  if (!meta.has_inventory_detail) {
    document.getElementById("detail-note").textContent =
      "(no per-SKU detail — run with --inventory-detail for warehouse/SKU charts)";
  }
  slider.addEventListener("input", () => loadDay(parseInt(slider.value)));
  document.getElementById("color-mode").addEventListener("change", () => loadDay(curDay));
  document.getElementById("order-btn").onclick = () => {
    const v = document.getElementById("order-input").value;
    if (v !== "") openOrder(parseInt(v));
  };
  await loadDay(meta.day_min);
}
init();
