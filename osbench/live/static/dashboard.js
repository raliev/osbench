"use strict";
// osbench live dashboard: WebSocket + incremental Plotly charts (offline).

const L = { responsive: true, displayModeBar: false,
  margin: { l: 44, r: 12, t: 8, b: 30 },
  paper_bgcolor: "#161b22", plot_bgcolor: "#161b22",
  font: { color: "#8b949e", size: 11 },
  xaxis: { gridcolor: "#30363d" }, yaxis: { gridcolor: "#30363d" },
  legend: { orientation: "h", y: 1.15, font: { size: 10 } } };

let meta = null;
const hist = { day: [], cost: [], onTime: [], lost: [], stockout: [],
  rate: [], done: [], total: [] };
const dec = new TextDecoder();

function layout(extra) { return Object.assign({}, L, extra); }

function initStaticCharts() {
  Plotly.newPlot("c-cost", [{ x: [], y: [], mode: "lines", name: "cost/order",
    line: { color: "#2f81f7" } }], layout({}));
  Plotly.newPlot("c-service", [
    { x: [], y: [], mode: "lines", name: "on time", line: { color: "#3fb950" } },
    { x: [], y: [], mode: "lines", name: "lost sales", yaxis: "y2", line: { color: "#f85149" } },
  ], layout({ yaxis: { gridcolor: "#30363d", range: [0, 1] },
    yaxis2: { overlaying: "y", side: "right", gridcolor: "transparent" } }));
  Plotly.newPlot("c-stockout", [{ x: [], y: [], mode: "lines", name: "SKU stockout",
    line: { color: "#d29922" } }], layout({ yaxis: { gridcolor: "#30363d", range: [0, 1] } }));
  Plotly.newPlot("c-compute", [{ x: [], y: [], mode: "lines", name: "orders/s",
    line: { color: "#a371f7" } }], layout({}));
}

function updateCharts(s) {
  hist.day.push(s.sim_day); hist.cost.push(s.cost_per_order);
  hist.onTime.push(s.on_time_rate); hist.lost.push(s.lost_sales);
  hist.stockout.push(s.stockout_share); hist.rate.push(s.compute.rate);

  Plotly.react("c-cost", [{ x: hist.day, y: hist.cost, mode: "lines",
    name: "cost/order", line: { color: "#2f81f7" } }], layout({}));
  Plotly.react("c-service", [
    { x: hist.day, y: hist.onTime, mode: "lines", name: "on time", line: { color: "#3fb950" } },
    { x: hist.day, y: hist.lost, mode: "lines", name: "lost sales", yaxis: "y2",
      line: { color: "#f85149" } },
  ], layout({ yaxis: { gridcolor: "#30363d", range: [0, 1] },
    yaxis2: { overlaying: "y", side: "right", gridcolor: "transparent" } }));
  Plotly.react("c-stockout", [{ x: hist.day, y: hist.stockout, mode: "lines",
    line: { color: "#d29922" } }], layout({ yaxis: { gridcolor: "#30363d", range: [0, 1] } }));
  Plotly.react("c-compute", [{ x: hist.day, y: hist.rate, mode: "lines",
    line: { color: "#a371f7" } }], layout({}));

  // Components (latest snapshot).
  const c = s.components;
  Plotly.react("c-comp", [{ type: "bar", orientation: "h",
    x: [c.shipping, c.holding, c.purchase, c.penalty],
    y: ["shipping", "holding", "purchase", "penalties"],
    marker: { color: ["#2f81f7", "#d29922", "#3fb950", "#f85149"] } }],
    layout({ margin: { l: 70, r: 12, t: 8, b: 30 } }));

  // Per-warehouse utilization and queue.
  const names = meta.wh_names;
  Plotly.react("c-util", [{ type: "bar", x: names, y: s.wh.util,
    marker: { color: s.wh.util.map(u => u > 1 ? "#f85149" : "#2f81f7") } }],
    layout({ yaxis: { gridcolor: "#30363d" } }));
  Plotly.react("c-queue", [{ type: "bar", x: names, y: s.wh.queue,
    marker: { color: "#8957e5" } }], layout({ yaxis: { gridcolor: "#30363d" } }));

  // Map.
  const maxStock = Math.max(1, ...s.wh.stock);
  const whTrace = { x: meta.wh_x, y: meta.wh_y, mode: "markers+text", type: "scatter",
    text: names, textposition: "top center", name: "warehouses",
    marker: { size: s.wh.stock.map(v => 10 + 30 * v / maxStock),
      color: s.wh.util, colorscale: "YlOrRd", cmin: 0, cmax: 1, showscale: false,
      line: { color: "#fff", width: 1 } }, customdata: names.map((n, i) => i) };
  const regTrace = { x: meta.region_x, y: meta.region_y, mode: "markers", type: "scatter",
    name: "regions", marker: { size: 8, color: "#30363d", symbol: "square" } };
  Plotly.react("c-map", [regTrace, whTrace],
    layout({ margin: { l: 30, r: 10, t: 8, b: 30 }, showlegend: false }));
  document.getElementById("c-map").on("plotly_click", ev => {
    const p = ev.points[0];
    if (p.data.name === "warehouses") sock.send(JSON.stringify({ type: "drill", wh: p.pointIndex }));
  });

  // Header.
  document.getElementById("h-day").textContent = s.sim_day;
  document.getElementById("h-done").textContent = s.compute.orders_done;
  document.getElementById("h-total").textContent = s.compute.orders_total;
  document.getElementById("h-eta").textContent = s.compute.eta_s > 0
    ? s.compute.eta_s + " s" : "—";
  setStatus(s.status);
}

function renderDrill(d) {
  document.getElementById("drill").classList.add("show");
  document.getElementById("drill-title").textContent = "Warehouse " + d.name;
  Plotly.react("c-drill", [
    { x: d.day, y: d.stock, mode: "lines", name: "stock", line: { color: "#2f81f7" } },
    { x: d.day, y: d.queue, mode: "lines", name: "queue", yaxis: "y2",
      line: { color: "#8957e5" } },
    { x: d.day, y: d.inbound, mode: "lines", name: "inbound", yaxis: "y2",
      line: { color: "#3fb950" } },
    { x: d.day, y: d.outbound, mode: "lines", name: "outbound", yaxis: "y2",
      line: { color: "#d29922" } },
  ], layout({ yaxis: { gridcolor: "#30363d", title: "stock" },
    yaxis2: { overlaying: "y", side: "right", gridcolor: "transparent" } }));
}

function setStatus(st) {
  const el = document.getElementById("status");
  el.textContent = st; el.className = st;
  if (st !== "running") document.getElementById("stop").disabled = true;
}

let sock;
function connect() {
  sock = new WebSocket("ws://" + location.host + "/ws");
  sock.binaryType = "arraybuffer";
  sock.onmessage = ev => {
    const msg = JSON.parse(typeof ev.data === "string" ? ev.data : dec.decode(ev.data));
    if (msg.type === "hello") {
      meta = msg;
      document.getElementById("h-preset").textContent = msg.preset || "—";
      document.getElementById("h-policy").textContent = msg.policy || "—";
      initStaticCharts();
    } else if (msg.type === "snapshot") {
      if (meta) updateCharts(msg.data);
    } else if (msg.type === "wh_detail") {
      renderDrill(msg);
    } else if (msg.type === "run_state") {
      setStatus(msg.state);
    }
  };
  sock.onclose = () => setTimeout(connect, 1000);
}

document.getElementById("stop").onclick = () => {
  sock.send(JSON.stringify({ type: "control", cmd: "stop" }));
  document.getElementById("stop").disabled = true;
};

connect();
