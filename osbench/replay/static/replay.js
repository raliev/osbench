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

// A failed API call shows a banner instead of silently leaving empty panels. A 404 on a known
// endpoint usually means the server process predates this page (restart `osbench studio`).
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) {
    const path = url.split("?")[0];
    const msg = r.status === 404
      ? `The inspector server does not know ${path} — it was started before this version of the UI. Restart \`osbench studio\` (or the inspector) and reopen it.`
      : `${path} failed: HTTP ${r.status} ${(await r.text()).slice(0, 200)}`;
    showError(msg);
    throw new Error(msg);
  }
  return r.json();
}
function showError(msg) {
  let b = document.getElementById("api-error");
  if (!b) {
    b = document.createElement("div"); b.id = "api-error";
    b.style.cssText = "background:#3d1b1b;color:#f85149;border-bottom:1px solid #f85149;padding:6px 16px;font-size:12px;cursor:pointer";
    b.title = "click to dismiss"; b.onclick = () => b.remove();
    document.querySelector("header").after(b);
  }
  b.textContent = msg;
}

// ----------------------------------------------------------------- map / day
async function loadDay(day) {
  curDay = day;
  const d = await getJSON("/api/day/" + day);
  document.getElementById("day-label").textContent = day;
  document.getElementById("n-orders").textContent = d.n_orders;
  drawMap(d);
  document.querySelectorAll(".cur-day").forEach(e => e.textContent = day);
  onDayChanged();
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
// panel: where to render. onMap: the map tab (draw customer->warehouse links, inline SKU
// panel); otherwise (Orders tab) SKU / warehouse links jump to the Products / Warehouses tabs.
async function openOrder(id, panel = "p-order") {
  const onMap = panel === "p-order";
  const o = await getJSON("/api/order/" + id);
  const el = document.getElementById(panel);
  if (!o.found) { el.innerHTML = "<h3>Order " + id + "</h3><div class='hint'>not found</div>"; return; }
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  if (onMap) {
    selectedOrder = { warehouses: o.warehouses, x: o.x, y: o.y };
    loadDay(curDay);  // redraw map links
  }
  const skuFn = onMap ? "openSku" : "gotoProduct";
  const whFn = onMap ? "openWarehouse" : "gotoWarehouse";

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
      + `<td><span class="clickable" onclick="${skuFn}(${l.sku})">#${l.sku}</span></td>`
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
    `<span class="clickable" onclick="${whFn}(${w})">${meta.wh_names[w]}</span>`).join(", ") || "—";
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
// t: element ids to render into (map tab: p-wh/wh-body; Warehouses tab: p-wh2/wh2-body); chart
// ids are prefixed so both copies can live in the DOM at once.
async function openWarehouse(wh, t = { panel: "p-wh", body: "wh-body", pre: "wh" }) {
  const w = await getJSON("/api/warehouse/" + wh + "?day=" + curDay);
  const P = t.pre, onMap = t.panel === "p-wh";
  document.getElementById(t.panel).style.display = "block";
  document.getElementById(t.panel).scrollIntoView({ behavior: "smooth", block: "nearest" });
  const body = document.getElementById(t.body);
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
  const srcNote = w.stock_source && w.stock_source !== "snapshot"
    ? `<div class="note">no per-SKU stock history recorded — stock = ${w.stock_source}${w.top_source ? ", top SKUs reconstructed from the journal" : ""}</div>` : "";
  body.innerHTML = `<div class="muted">${w.name} · stock @day ${curDay}</div>`
    + summary + srcNote
    + (w.stock.length ? `<div id="${P}-stock" class="chart"></div>` : `<div class="hint">no stock history for this run</div>`)
    + `<h4 style="margin:8px 6px 2px">Workload: assigned vs shipped lines/day</h4><div id="${P}-work" class="chart"></div>`
    + `<h4 style="margin:8px 6px 2px">Parcels shipped / units received per day</h4><div id="${P}-flow" class="chart"></div>`
    + `<h4 style="margin:8px 6px 2px">Top SKUs @day ${curDay}</h4><div id="${P}-top"></div>`;
  document.getElementById(t.panel).querySelector("h3").textContent = "Warehouse " + w.name;

  if (w.stock.length) Plotly.newPlot(`${P}-stock`, [{ x: w.stock_day, y: w.stock, mode: "lines",
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
  Plotly.newPlot(`${P}-work`, workTraces, baseLayout({ shapes: workShapes, showlegend: true,
    legend: { orientation: "h", y: 1.2, font: { size: 10 } } }));
  Plotly.newPlot(`${P}-flow`, [
    { x: w.ship_day, y: w.ship, mode: "lines", name: "parcels shipped/day", line: { color: "#d29922" } },
    { x: w.recv_day, y: w.recv, mode: "lines", name: "units received/day", line: { color: "#3fb950" } },
  ], baseLayout({ shapes: [cursorShape()], showlegend: true,
    legend: { orientation: "h", y: 1.2, font: { size: 10 } } }));
  if (w.stock.length) cursorCharts.add(`${P}-stock`);
  cursorCharts.add(`${P}-work`); cursorCharts.add(`${P}-flow`);

  let tb = "<table><tr><th>SKU</th><th>book</th></tr>";
  for (const s of w.top_sku) {
    tb += `<tr><td><span class="clickable" onclick="${onMap ? "openSku" : "gotoProduct"}(${s.sku})">#${s.sku}</span></td>`
      + `<td>${s.book}</td></tr>`;
  }
  document.getElementById(`${P}-top`).innerHTML = tb + "</table>";
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

// ----------------------------------------------------------------- tabs
let curTab = "map";
function showTab(name) {
  curTab = name;
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".pane").forEach(p => p.classList.toggle("active", p.id === "pane-" + name));
  try { localStorage.setItem("osb_insp_tab", name); } catch (e) {}
  if (name === "products") loadProducts();
  if (name === "warehouses") loadWarehouses();
  if (name === "orders" && !ordersLoaded) loadOrders();
  if (name === "sales") loadSales();
  setTimeout(() => window.dispatchEvent(new Event("resize")), 60);
}
// Lists that depend on the day reload (debounced) while the slider is scrubbed.
let dayTimer = null;
function onDayChanged() {
  clearTimeout(dayTimer);
  dayTimer = setTimeout(() => {
    if (curTab === "products") loadProducts();
    if (curTab === "sales" && ["through", "day"].includes(document.getElementById("sl-scope").value)) loadSales();
    if (curTab === "warehouses") {
      loadWarehouses();
      if (wlState.sel !== null) openWarehouse(wlState.sel, { panel: "p-wh2", body: "wh2-body", pre: "wh2" });
    }
  }, 250);
}

const esc = v => String(v ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function pager(id, total, offset, limit, go) {
  const el = document.getElementById(id);
  const pages = Math.max(1, Math.ceil(total / limit)), page = Math.floor(offset / limit) + 1;
  el.innerHTML = "";
  const btn = (txt, p, dis) => {
    const b = document.createElement("button"); b.className = "ghost sm"; b.textContent = txt;
    b.disabled = dis; b.onclick = () => go((p - 1) * limit); el.appendChild(b);
  };
  btn("«", 1, page <= 1); btn("‹", page - 1, page <= 1);
  const span = document.createElement("span");
  span.textContent = total ? `page ${page} / ${pages} · ${offset + 1}–${Math.min(total, offset + limit)} of ${total}` : "nothing matches";
  el.appendChild(span);
  btn("›", page + 1, page >= pages); btn("»", pages, page >= pages);
}
function sortHeader(cols, st, reload) {
  // cols: [[key|null, label, cls]]; clicking a sortable header toggles direction.
  return "<tr>" + cols.map(([k, l, cls]) => {
    if (!k) return `<th class="${cls || ""}">${l}</th>`;
    const arrow = st.sort === k ? (st.desc ? " ▾" : " ▴") : "";
    return `<th class="sortable ${cls || ""}" data-sort="${k}">${l}${arrow}</th>`;
  }).join("") + "</tr>";
}
function bindSort(rootId, st, reload) {
  document.querySelectorAll(`#${rootId} th.sortable`).forEach(th => th.onclick = () => {
    const k = th.dataset.sort;
    if (st.sort === k) st.desc = !st.desc; else { st.sort = k; st.desc = k !== "sku" && k !== "order_id"; }
    st.offset = 0; reload();
  });
}
const srcNote = src => src === "journal"
  ? "per-SKU stock history was not recorded for this run — stock is reconstructed from the journal (initial stock + receipts − shipments)" : "";

// ----------------------------------------------------------------- products
const prState = { offset: 0, sort: "sku", desc: false, sel: null };
let prSeq = 0;
async function loadProducts() {
  const lim = +document.getElementById("pr-limit").value;
  const qs = new URLSearchParams({ day: curDay, offset: prState.offset, limit: lim, sort: prState.sort,
    desc: prState.desc, q: document.getElementById("pr-q").value,
    category: document.getElementById("pr-cat").value, stocked: document.getElementById("pr-stocked").value });
  const seq = ++prSeq;
  const r = await getJSON("/api/products?" + qs);
  if (seq !== prSeq) return;  // a newer request (slider scrub) superseded this one
  const cat = document.getElementById("pr-cat");
  if (cat.options.length === 1) for (const c of r.categories) cat.add(new Option("category " + c, c));
  document.getElementById("pr-note").textContent = srcNote(r.stock_source);
  const whCols = meta.wh_names.length <= 8;  // per-warehouse ATP columns when they fit
  const cols = [["sku", "SKU"], ["category", "cat"], ["price", "price", "num"], ["unit_cost", "unit cost", "num"],
    ["ordered_lines", "ordered (lines)", "num"], ["ordered_units", "units", "num"],
    ["delivered_units", "delivered", "num"], ["atp", `ATP @${curDay}`, "num"]];
  let h = "<table>" + sortHeader(cols, prState)
    .replace("</tr>", (whCols ? meta.wh_names.map(n => `<th class="num">${esc(n)}</th>`).join("") : "<th>stocked at</th>") + "</tr>");
  for (const p of r.rows) {
    const whCells = whCols
      ? p.atp_wh.map((v, i) => p.stocked_wh[i]
          ? `<td class="num ${v <= 0 ? "zero" : ""}" title="book ${p.book_wh[i]}">${v}</td>` : `<td class="num muted">·</td>`).join("")
      : `<td>${p.n_wh} wh</td>`;
    h += `<tr class="row ${prState.sel === p.sku ? "sel" : ""}" data-sku="${p.sku}"><td>#${p.sku}</td><td>${p.category}</td>`
      + `<td class="num">${p.price}</td><td class="num">${p.unit_cost}</td>`
      + `<td class="num">${p.ordered_lines}</td><td class="num">${p.ordered_units}</td><td class="num">${p.delivered_units}</td>`
      + `<td class="num ${p.atp <= 0 && p.n_wh ? "zero" : ""}" title="book ${p.book}"><b>${p.atp}</b></td>${whCells}</tr>`;
  }
  const tbl = document.getElementById("pr-table");
  tbl.innerHTML = h + "</table>";
  tbl.querySelectorAll("tr.row").forEach(tr => tr.onclick = () => openProduct(+tr.dataset.sku));
  bindSort("pr-table", prState, loadProducts);
  pager("pr-pager", r.total, r.offset, lim, off => { prState.offset = off; loadProducts(); });
}

async function openProduct(sku) {
  prState.sel = sku;
  document.querySelectorAll("#pr-table tr.row").forEach(tr => tr.classList.toggle("sel", +tr.dataset.sku === sku));
  const p = await getJSON("/api/product/" + sku);
  const el = document.getElementById("p-product");
  if (!p.found) { el.innerHTML = `<h3>SKU #${sku}</h3><div class="hint">not found</div>`; return; }
  const i = p.info, f = p.fulfilment, c = p.costs, dm = p.demand;
  const card = (k, v) => `<div class="sumcard"><div class="v">${v ?? "—"}</div><div class="k">${k}</div></div>`;
  let html = `<h3>SKU #${p.sku}</h3><div class="muted">category ${i.category} · popularity rank ${i.popularity_rank ?? "—"}`
    + ` · ${i.weight_kg} kg · vol ${i.volume}</div>`
    + `<h4 style="margin:8px 6px 2px">Demand &amp; fulfilment</h4><div class="sumgrid">`
    + card("orders", dm.orders) + card("ordered lines", dm.lines) + card("ordered units", dm.units)
    + card("shipped units", f.shipped_units) + card("delivered units", f.delivered_units)
    + card("lost", `${f.lost_lines}L · ${f.lost_units}u`) + card("received units", f.received_units) + "</div>"
    + `<h4 style="margin:8px 6px 2px">Price &amp; cost to the company</h4><div class="sumgrid">`
    + card("price", i.price) + card("catalog unit cost", i.unit_cost) + card("purchase / unit", c.purchase_per_unit)
    + card("cost / delivered unit", c.per_delivered_unit) + "</div>"
    + `<table style="margin:6px 0"><tr><th>purchase</th><th>fulfilment (ship+handling)</th><th>holding</th>`
    + `<th>late penalty</th><th>lost-sale penalty</th><th>total</th><th>revenue</th><th>margin</th></tr>`
    + `<tr><td>${c.purchase}</td><td>${c.fulfilment}</td><td>${c.holding}</td><td>${c.late_penalty}</td>`
    + `<td>${c.lost_penalty}</td><td><b>${c.total}</b></td><td>${c.revenue}</td>`
    + `<td class="${c.margin < 0 ? "zero" : ""}">${c.margin}</td></tr></table>`
    + `<div class="muted" style="font-size:11px;margin:0 6px">fulfilment / late penalty are the parcel's cost split per line; holding = storage cost × daily stock.</div>`
    + `<h4 style="margin:10px 6px 2px">All warehouses: ordered / received / shipped units per day</h4><div id="pp-flow" class="chart"></div>`
    + `<h4 style="margin:10px 6px 2px">Per warehouse: stock (line) and inbound / outbound (bars)</h4>`
    + (p.stock_source === "journal" ? `<div class="note">${srcNote("journal")}</div>` : "")
    + `<div class="whgrid">` + p.per_wh.map(w => `<div class="whcard"><div class="muted">${esc(meta.wh_names[w.wh])}`
      + `${w.stocked ? "" : " (not in assortment)"} · in ${w.received_units} · out ${w.shipped_units}</div>`
      + `<div id="pp-wh-${w.wh}" class="chart"></div></div>`).join("") + `</div>`;
  if (!p.per_wh.length) html += `<div class="hint">not stocked at any warehouse</div>`;
  el.innerHTML = html;
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  const leg = { orientation: "h", y: 1.25, font: { size: 10 } };
  Plotly.newPlot("pp-flow", [
    { x: dm.day, y: dm.qty, type: "bar", name: "ordered", marker: { color: "#8b949e" } },
    { x: p.received.day, y: p.received.qty, type: "bar", name: "received", marker: { color: "#3fb950" } },
    { x: p.shipped.day, y: p.shipped.qty, type: "bar", name: "shipped", marker: { color: "#d29922" } },
  ], baseLayout({ barmode: "group", shapes: [cursorShape()], showlegend: true, legend: leg,
    xaxis: { gridcolor: "#30363d", range: [p.day_range[0] - 0.5, p.day_range[1] + 0.5] } }), { displayModeBar: false });
  cursorCharts.add("pp-flow");
  for (const w of p.per_wh) {
    const color = WH_COLORS[w.wh % WH_COLORS.length];
    const tr = [
      { x: w.received.day, y: w.received.qty, type: "bar", name: "received", marker: { color: "#3fb950" }, yaxis: "y2" },
      { x: w.shipped.day, y: w.shipped.qty, type: "bar", name: "shipped", marker: { color: "#d29922" }, yaxis: "y2" },
    ];
    if (w.book) tr.push({ x: p.days, y: w.book, mode: "lines", name: "stock", line: { color, width: 2, shape: "hv" } });
    Plotly.newPlot(`pp-wh-${w.wh}`, tr, baseLayout({ barmode: "group", shapes: [cursorShape()], showlegend: false,
      margin: { l: 34, r: 30, t: 4, b: 22 },
      xaxis: { gridcolor: "#30363d", range: [p.day_range[0] - 0.5, p.day_range[1] + 0.5] },
      yaxis: { gridcolor: "#30363d", title: { text: "stock", font: { size: 9 } }, rangemode: "tozero" },
      yaxis2: { overlaying: "y", side: "right", showgrid: false, rangemode: "tozero",
        title: { text: "in/out", font: { size: 9 } } } }), { displayModeBar: false });
    cursorCharts.add(`pp-wh-${w.wh}`);
  }
}
function gotoProduct(sku) { showTab("products"); openProduct(sku); }

// ----------------------------------------------------------------- warehouses
const wlState = { offset: 0, sel: null };
let wlSeq = 0;
function sparkSvg(vals, color) {
  if (!vals || vals.length < 2) return '<span class="muted">—</span>';
  const W = 120, H = 26, mx = Math.max(1, ...vals);
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * W).toFixed(1)},${(H - 2 - v / mx * (H - 4)).toFixed(1)}`).join(" ");
  const cx = Math.min(vals.length - 1, curDay) / (vals.length - 1) * W;
  return `<svg width="${W}" height="${H}"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5"/>`
    + `<line x1="${cx}" x2="${cx}" y1="0" y2="${H}" stroke="#e6edf3" stroke-dasharray="2,2" stroke-width="1"/></svg>`;
}
async function loadWarehouses() {
  const lim = +document.getElementById("wl-limit").value;
  const seq = ++wlSeq;
  const r = await getJSON(`/api/warehouses?day=${curDay}&offset=${wlState.offset}&limit=${lim}`);
  if (seq !== wlSeq) return;
  document.getElementById("wl-note").textContent = srcNote(r.stock_source);
  let h = `<table><tr><th>warehouse</th><th>stock over run</th><th class="num">book @${curDay}</th>`
    + `<th class="num">ATP</th><th class="num">reserved</th><th class="num">SKUs</th><th class="num">zero-ATP SKUs</th>`
    + `<th class="num">cap/day</th><th class="num" title="lines assigned but not yet shipped at the start of this day">open lines</th></tr>`;
  for (const w of r.rows) {
    h += `<tr class="row ${wlState.sel === w.wh ? "sel" : ""}" data-wh="${w.wh}"><td><b>${esc(w.name)}</b></td>`
      + `<td>${sparkSvg(w.spark, WH_COLORS[w.wh % WH_COLORS.length])}</td>`
      + `<td class="num">${w.book}</td><td class="num">${w.atp}</td><td class="num">${w.reserved}</td>`
      + `<td class="num">${w.n_sku}</td><td class="num ${w.zero_atp_skus ? "zero" : ""}">${w.zero_atp_skus}</td>`
      + `<td class="num">${w.capacity}</td><td class="num ${w.backlog_lines > w.capacity ? "zero" : ""}">${w.backlog_lines}</td></tr>`;
  }
  const tbl = document.getElementById("wl-table");
  tbl.innerHTML = h + "</table>";
  tbl.querySelectorAll("tr.row").forEach(tr => tr.onclick = () => openWh2(+tr.dataset.wh));
  pager("wl-pager", r.total, r.offset, lim, off => { wlState.offset = off; loadWarehouses(); });
}
function openWh2(wh) {
  wlState.sel = wh;
  document.querySelectorAll("#wl-table tr.row").forEach(tr => tr.classList.toggle("sel", +tr.dataset.wh === wh));
  return openWarehouse(wh, { panel: "p-wh2", body: "wh2-body", pre: "wh2" });
}
function gotoWarehouse(wh) { showTab("warehouses"); openWh2(wh); }

// ----------------------------------------------------------------- orders
const olState = { offset: 0, sort: "order_id", desc: false, sel: null };
let ordersLoaded = false, olSeq = 0;
const OL_FIELDS = { "ol-id": "order_id", "ol-from": "day_from", "ol-to": "day_to", "ol-status": "status",
  "ol-wh": "wh", "ol-region": "region", "ol-split": "split", "ol-sku": "sku", "ol-minl": "min_lines" };
const CHIP = { delivered: "ok", late: "late", partial: "partial", pending: "pending", lost: "lost" };
async function loadOrders() {
  ordersLoaded = true;
  const lim = +document.getElementById("ol-limit").value;
  const qs = new URLSearchParams({ offset: olState.offset, limit: lim, sort: olState.sort, desc: olState.desc });
  for (const [id, k] of Object.entries(OL_FIELDS)) qs.set(k, document.getElementById(id).value);
  const seq = ++olSeq;
  const r = await getJSON("/api/orders?" + qs);
  if (seq !== olSeq) return;
  document.getElementById("ol-counts").innerHTML = STATUS_ORDER.filter(s => r.status_counts[s])
    .map(s => `<span style="color:${STATUS_COLOR[s]}">■</span> ${STATUS_LABEL[s]} ${r.status_counts[s]}`).join("  ");
  const cols = [["order_id", "order"], ["day", "day", "num"], [null, "region"], ["n_lines", "lines", "num"],
    [null, "delivered", "num"], ["n_wh", "warehouses"], [null, "status"], [null, "promise", "num"],
    ["lateness_days", "late (d)", "num"], ["cost", "cost", "num"]];
  let h = "<table>" + sortHeader(cols, olState);
  for (const o of r.rows) {
    h += `<tr class="row ${olState.sel === o.order_id ? "sel" : ""}" data-id="${o.order_id}"><td>${o.order_id}</td>`
      + `<td class="num">${o.day}</td><td>${esc(meta.region_names[o.region] ?? o.region)}</td>`
      + `<td class="num">${o.n_lines}</td><td class="num">${o.delivered_lines}</td>`
      + `<td>${o.whs.map(w => esc(meta.wh_names[w])).join(", ") || "—"}</td>`
      + `<td><span class="chip ${CHIP[o.status]}">${o.status}</span></td>`
      + `<td class="num">${o.promise_day ?? "—"}</td><td class="num">${o.lateness_days ?? "—"}</td>`
      + `<td class="num">${o.cost}</td></tr>`;
  }
  const tbl = document.getElementById("ol-table");
  tbl.innerHTML = h + "</table>";
  tbl.querySelectorAll("tr.row").forEach(tr => tr.onclick = () => {
    olState.sel = +tr.dataset.id;
    tbl.querySelectorAll("tr.row").forEach(x => x.classList.toggle("sel", x === tr));
    openOrder(olState.sel, "p-order2");
  });
  bindSort("ol-table", olState, loadOrders);
  pager("ol-pager", r.total, r.offset, lim, off => { olState.offset = off; loadOrders(); });
}
window.gotoProduct = gotoProduct;
window.gotoWarehouse = gotoWarehouse;

// ----------------------------------------------------------------- sales
const MONEY = { revenue: "#3fb950", purchase: "#2f81f7", fulfilment: "#d29922", holding: "#a371f7",
  late_penalty: "#f0883e", lost_penalty: "#f85149", lost_revenue: "#8b949e" };
const MONEY_LABEL = { revenue: "revenue", purchase: "purchase", fulfilment: "fulfilment", holding: "holding",
  late_penalty: "late penalty", lost_penalty: "lost-sale penalty", lost_revenue: "lost revenue" };
const COSTS = ["purchase", "fulfilment", "holding", "late_penalty", "lost_penalty"];
const fmtMoney = v => v == null ? "—" : Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(2) + "M"
  : Math.abs(v) >= 1e4 ? (v / 1e3).toFixed(1) + "k" : (+v).toFixed(2);
const ssState = { offset: 0, sort: "revenue", desc: true };
let slSeq = 0;
function salesRange() {
  const sc = document.getElementById("sl-scope").value;
  document.getElementById("sl-custom").style.display = sc === "custom" ? "inline" : "none";
  if (sc === "through") return { from: "", to: curDay, label: `days 0–${curDay}` };
  if (sc === "day") return { from: curDay, to: curDay, label: `day ${curDay}` };
  if (sc === "custom") {
    const f = document.getElementById("sl-from").value, t = document.getElementById("sl-to").value;
    return { from: f, to: t, label: `days ${f || "start"}–${t || "end"}` };
  }
  return { from: "", to: "", label: "whole run" };
}
function moneyTable(rows, keyLabel, nameOf, onClick) {
  const cols = ["revenue", ...COSTS, "lost_revenue"];
  let h = `<table><tr><th>${keyLabel}</th>` + cols.map(k => `<th class="num">${MONEY_LABEL[k]}</th>`).join("")
    + `<th class="num">margin</th><th class="num">margin %</th></tr>`;
  const sum = Object.fromEntries(cols.map(k => [k, 0]));
  for (const r of rows) {
    const cost = COSTS.reduce((a, k) => a + (r[k] || 0), 0), m = r.revenue - cost;
    cols.forEach(k => sum[k] += r[k] || 0);
    h += `<tr class="${onClick ? "row" : ""}" data-key="${r.key}"><td>${esc(nameOf(r.key))}</td>`
      + cols.map(k => `<td class="num">${fmtMoney(r[k])}</td>`).join("")
      + `<td class="num ${m < 0 ? "zero" : ""}"><b>${fmtMoney(m)}</b></td>`
      + `<td class="num">${r.revenue ? (100 * m / r.revenue).toFixed(1) + "%" : "—"}</td></tr>`;
  }
  const scost = COSTS.reduce((a, k) => a + sum[k], 0);
  h += `<tr><td class="muted">total (attributed)</td>` + cols.map(k => `<td class="num muted">${fmtMoney(sum[k])}</td>`).join("")
    + `<td class="num muted">${fmtMoney(sum.revenue - scost)}</td><td></td></tr>`;
  return h + "</table>";
}
async function loadSales() {
  const R = salesRange();
  const seq = ++slSeq;
  const s = await getJSON(`/api/sales?day_from=${R.from}&day_to=${R.to}`);
  if (seq !== slSeq) return;
  document.getElementById("sl-title").textContent = R.label;
  document.getElementById("sl-range").textContent = `→ days ${s.from}–${s.to} of ${s.day_range[0]}–${s.day_range[1]}`;
  const k = s.kpi;
  const card = (label, v, sub, cls) => `<div class="sumcard ${cls || ""}"><div class="v">${v}</div><div class="k">${label}</div>${sub ? `<div class="s">${sub}</div>` : ""}</div>`;
  const pct = (v, base) => base ? (100 * v / base).toFixed(1) + "% of revenue" : "";
  document.getElementById("sl-kpi").innerHTML = "<div class='sumgrid'>"
    + card("revenue", fmtMoney(k.revenue), `${k.units_sold} units · ${k.orders_delivered} orders`)
    + card("avg order value", fmtMoney(k.aov))
    + card("total spend", fmtMoney(k.spend), pct(k.spend, k.revenue))
    + card("cash margin", fmtMoney(k.cash_margin), pct(k.cash_margin, k.revenue), k.cash_margin >= 0 ? "pos" : "neg")
    + card("unit margin", fmtMoney(k.unit_margin), k.cogs != null ? `COGS ${fmtMoney(k.cogs)} @ ${k.unit_purchase_price}/u` : "", k.unit_margin >= 0 ? "pos" : "neg")
    + card("lost revenue", fmtMoney(k.lost_revenue), `${k.units_lost} units not sold`, k.lost_revenue > 0 ? "neg" : "")
    + card("inventory value (end)", fmtMoney(k.inventory_value_end), "stock × purchase price")
    + "</div><div class='sumgrid'>"
    + COSTS.map(c => card(MONEY_LABEL[c], fmtMoney(k[c]), pct(k[c], k.revenue))).join("") + "</div>";

  const cfg = { displayModeBar: false, responsive: true };
  const leg = { orientation: "h", y: 1.18, font: { size: 10 } };
  const D = s.days, dd = s.daily;
  const shapes = curDay >= s.from && curDay <= s.to ? [cursorShape()] : [];
  Plotly.newPlot("sl-daily", [
    ...COSTS.map(c => ({ x: D, y: dd[c], type: "bar", name: MONEY_LABEL[c], marker: { color: MONEY[c] } })),
    { x: D, y: dd.revenue, mode: "lines", name: "revenue", line: { color: MONEY.revenue, width: 2 } },
  ], baseLayout({ barmode: "stack", showlegend: true, legend: leg, shapes }), cfg);
  const cum = a => { let t = 0; return a.map(v => t += v); };
  const spendDay = D.map((_, i) => COSTS.reduce((a, c) => a + dd[c][i], 0));
  const cr = cum(dd.revenue), cs = cum(spendDay);
  Plotly.newPlot("sl-cum", [
    { x: D, y: cr, mode: "lines", name: "revenue", line: { color: MONEY.revenue } },
    { x: D, y: cs, mode: "lines", name: "spend", line: { color: MONEY.lost_penalty } },
    { x: D, y: cr.map((v, i) => v - cs[i]), mode: "lines", name: "cash margin", fill: "tozeroy",
      line: { color: "#58a6ff" }, fillcolor: "rgba(88,166,255,.12)" },
  ], baseLayout({ showlegend: true, legend: leg, shapes }), cfg);
  const sc = COSTS.filter(c => k[c] > 0);
  Plotly.newPlot("sl-struct", [{ type: "bar", orientation: "h", y: sc.map(c => MONEY_LABEL[c]), x: sc.map(c => k[c]),
    marker: { color: sc.map(c => MONEY[c]) }, text: sc.map(c => `${fmtMoney(k[c])} · ${(100 * k[c] / (k.spend || 1)).toFixed(1)}%`),
    textposition: "auto", hovertemplate: "%{y}: %{x:.2f}<extra></extra>" }],
    baseLayout({ margin: { l: 110, r: 10, t: 6, b: 26 }, yaxis: { autorange: "reversed" } }), cfg);
  Plotly.newPlot("sl-lost", [
    { x: D, y: dd.lost_revenue, type: "bar", name: "lost revenue", marker: { color: MONEY.lost_revenue } },
    { x: D, y: dd.lost_penalty, type: "bar", name: "lost-sale penalty", marker: { color: MONEY.lost_penalty } },
    { x: D, y: dd.late_penalty, type: "bar", name: "late penalty", marker: { color: MONEY.late_penalty } },
  ], baseLayout({ barmode: "stack", showlegend: true, legend: leg, shapes }), cfg);
  for (const id of ["sl-daily", "sl-cum", "sl-lost"]) if (shapes.length) cursorCharts.add(id);

  document.getElementById("sl-wh").innerHTML = moneyTable(s.by_wh, "warehouse", w => meta.wh_names[w] ?? w, true);
  document.querySelectorAll("#sl-wh tr.row").forEach(tr => tr.onclick = () => gotoWarehouse(+tr.dataset.key));
  document.getElementById("sl-region").innerHTML = moneyTable(s.by_region, "region", r => meta.region_names[r] ?? r)
    + `<div class="muted" style="font-size:11px;margin:4px 6px">purchase and holding are per warehouse, not per region.</div>`;
  document.getElementById("sl-cat").innerHTML = moneyTable(s.by_category, "category", c => "category " + c, true);
  document.querySelectorAll("#sl-cat tr.row").forEach(tr => tr.onclick = () => {
    document.getElementById("ss-cat").value = tr.dataset.key; ssState.offset = 0; loadSalesSkus();
    document.getElementById("ss-table").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  const cat = document.getElementById("ss-cat");
  if (cat.options.length === 1) for (const c of s.by_category) cat.add(new Option("category " + c.key, c.key));
  if (s.stock_source === "journal") document.getElementById("sl-cat").insertAdjacentHTML("beforeend",
    `<div class="note">${srcNote("journal")}</div>`);
  loadSalesSkus();
}
let ssSeq = 0;
async function loadSalesSkus() {
  const R = salesRange(), lim = +document.getElementById("ss-limit").value;
  const qs = new URLSearchParams({ day_from: R.from, day_to: R.to, q: document.getElementById("ss-q").value,
    category: document.getElementById("ss-cat").value, sort: ssState.sort, desc: ssState.desc,
    offset: ssState.offset, limit: lim });
  const seq = ++ssSeq;
  const r = await getJSON("/api/sales/skus?" + qs);
  if (seq !== ssSeq) return;
  const cols = [["sku", "SKU"], ["category", "cat"], ["price", "price", "num"], ["units_sold", "sold", "num"],
    ["revenue", "revenue", "num"], ["purchase", "purchase", "num"], ["fulfilment", "fulfilment", "num"],
    ["holding", "holding", "num"], ["late_penalty", "late pen.", "num"], ["lost_penalty", "lost pen.", "num"],
    ["cost", "total cost", "num"], ["margin", "margin", "num"], ["margin_pct", "margin %", "num"],
    ["units_lost", "lost units", "num"], ["lost_revenue", "lost revenue", "num"]];
  let h = "<table>" + sortHeader(cols, ssState);
  for (const x of r.rows) {
    h += `<tr class="row" data-sku="${x.sku}"><td>#${x.sku}</td><td>${x.category}</td><td class="num">${x.price}</td>`
      + `<td class="num">${x.units_sold}</td><td class="num">${fmtMoney(x.revenue)}</td>`
      + ["purchase", "fulfilment", "holding", "late_penalty", "lost_penalty", "cost"].map(k => `<td class="num">${fmtMoney(x[k])}</td>`).join("")
      + `<td class="num ${x.margin < 0 ? "zero" : ""}"><b>${fmtMoney(x.margin)}</b></td>`
      + `<td class="num">${x.margin_pct == null ? "—" : (100 * x.margin_pct).toFixed(1) + "%"}</td>`
      + `<td class="num">${x.units_lost}</td><td class="num">${fmtMoney(x.lost_revenue)}</td></tr>`;
  }
  const tbl = document.getElementById("ss-table");
  tbl.innerHTML = h + "</table>";
  tbl.querySelectorAll("tr.row").forEach(tr => tr.onclick = () => gotoProduct(+tr.dataset.sku));
  bindSort("ss-table", ssState, loadSalesSkus);
  pager("ss-pager", r.total, r.offset, lim, off => { ssState.offset = off; loadSalesSkus(); });
}

function initCatalogs() {
  document.querySelectorAll(".tab").forEach(t => t.onclick = () => showTab(t.dataset.tab));
  const debounce = (fn, ms) => { let t; return () => { clearTimeout(t); t = setTimeout(fn, ms); }; };
  const prReload = () => { prState.offset = 0; loadProducts(); };
  document.getElementById("pr-q").oninput = debounce(prReload, 300);
  for (const id of ["pr-cat", "pr-stocked", "pr-limit"]) document.getElementById(id).onchange = prReload;
  document.getElementById("wl-limit").onchange = () => { wlState.offset = 0; loadWarehouses(); };
  const olReload = () => { olState.offset = 0; loadOrders(); };
  for (const id of [...Object.keys(OL_FIELDS), "ol-limit"]) {
    const el = document.getElementById(id);
    el.oninput = el.tagName === "SELECT" ? null : debounce(olReload, 350);
    el.onchange = olReload;
  }
  document.getElementById("sl-scope").onchange = loadSales;
  for (const id of ["sl-from", "sl-to"]) document.getElementById(id).onchange = loadSales;
  document.getElementById("ss-q").oninput = debounce(() => { ssState.offset = 0; loadSalesSkus(); }, 300);
  for (const id of ["ss-cat", "ss-limit"]) document.getElementById(id).onchange = () => { ssState.offset = 0; loadSalesSkus(); };
  const whSel = document.getElementById("ol-wh");
  meta.wh_names.forEach((n, i) => whSel.add(new Option(n, i)));
  const rgSel = document.getElementById("ol-region");
  meta.region_names.forEach((n, i) => rgSel.add(new Option(n, i)));
  document.getElementById("ol-today").onclick = () => {
    document.getElementById("ol-from").value = curDay; document.getElementById("ol-to").value = curDay; olReload();
  };
  document.getElementById("ol-reset").onclick = () => {
    for (const id of Object.keys(OL_FIELDS)) document.getElementById(id).value = "";
    olReload();
  };
}

// ----------------------------------------------------------------- init
async function init() {
  meta = await getJSON("/api/meta");
  initCatalogs();
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
  let tab = null;
  try { tab = localStorage.getItem("osb_insp_tab"); } catch (e) {}
  if (tab && tab !== "map" && document.getElementById("pane-" + tab)) showTab(tab);
}
init();
