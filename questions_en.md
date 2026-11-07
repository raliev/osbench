# Questions the osbench testbed can answer

Each question is phrased the way an e-commerce manager would ask it, then reduced to
concrete scenario-parameter axes and a single primary metric. Every parameter listed
exists in `osbench/config/schema.py` and is actually read by the engine.

The answering protocol is the same throughout: 30 seeds per cell, common random numbers
(one seed → the same world and the same order stream for every variant), paired
differences by seed, 95 % confidence intervals from both the t-distribution and the
bootstrap, and a Holm correction for multiple comparisons within a question.

Status: **✅ studied** — a full experiment was run, see `papers/`.
**⚠️ probed, effect is small** — a quick probe found the effect at noise level in the
current scenarios.

---

## A. Is the IT capability worth building?

### A1. Do we need online inventory reservation? ✅

> "If the OMS reserves stock at checkout instead of at pick, how many cancellations do
> we avoid, and what does it cost us?"

| | |
|---|---|
| Axes | `reservation.enabled: [true, false]` × network load ρ (contention) |
| Primary metric | `lost_sales_rate` (not `total_cost_per_order` — it is 85 % COGS) |
| Explanatory | `shortage_rate_pick`, `promise_hit_rate`, `lateness_days_p90` |
| Result | see `papers/study2_en.md`, Study C: a fill-rate lever with an on-time cost, works only at ρ ≳ 0.6 |

### A2. Do we need ATP that counts inbound receipts?

> "Should we sell an item that is not in the warehouse yet but arrives the day after
> tomorrow?"

| | |
|---|---|
| Axes | `atp.method: [on_hand_only, basic, cumulative]` × `atp.horizon_days: [3, 7, 14]` |
| Primary metric | `total_cost_per_order` |
| Explanatory | `promise_rejection_rate`, `shortage_rate_checkout` versus `promise_hit_rate`, `lateness_days_p90` |
| In one line | the price of optimism in the promise |

### A3. What is one percentage point of inventory-record accuracy worth? ⚠️

> "Will cycle counting or a new WMS pay for itself?"

| | |
|---|---|
| Axes | `inventory_accuracy.accuracy: [1.0, 0.99, 0.98, 0.95]` × `reservation.enabled` |
| Primary metric | slope of `total_cost_per_order` against accuracy |
| Explanatory | `shortage_rate_pick`, `lines_cancelled_by_stage.pick` |

A quick probe on `demo_scarce` put the effect below the noise floor: a record error of
two units per receipt is negligible against replenishment lots (MOQ 40 and up), and the
discrepancy is reconciled at the very first pick. To make the question meaningful you
need a scenario with small frequent receipts and a long SKU tail — or a larger
`inventory_accuracy.error_mean`.

---

## B. How clever does the algorithm need to be?

### B1. When does a sourcing algorithm pay off, and when does it not? ✅

> "We ship from the nearest warehouse today. What are we losing, and under what
> conditions?"

| | |
|---|---|
| Axes | policies `[random, nearest, cheapest_myopic, weighted_score, dtlp_bidprice]` × network load ρ = demand / capacity |
| Primary metric | `total_cost_per_order` relative to `nearest` |
| Explanatory | `promise_hit_rate`, `penalty_cost`, warehouse utilisation |
| Result | see `papers/study_en.md`, Study A |

### B2. Is tuning the weights worth it, over and above adopting weighted score?

> "Do we need a data scientist, or is common sense in the settings enough?"

| | |
|---|---|
| Axes | `weighted_score` (defaults) versus `weighted_score_tuned` (`osbench tune` on seeds 0–9), tested on seeds 15–44 |
| Second cut | weights tuned at ρ = 0.8, replayed at ρ = 1.1 and under a warehouse outage |
| Primary metric | `total_cost_per_order` |
| In one line | will the tuning survive Black Friday |

### B3. Is a heavy forecast-driven LP policy worth it?

> "dtlp costs an order of magnitude more compute. What does it buy over a tuned weighted
> score?"

| | |
|---|---|
| Axes | the pair `weighted_score_tuned` × `dtlp_bidprice` across several load regimes |
| Primary metric | `total_cost_per_order` |
| Explanatory | `decision_time_ms_p99`, `gap_to_hindsight` |

### B4. How much money is on the table at all?

| | |
|---|---|
| Metric | `gap_to_hindsight` for the incumbent policy — the ceiling on any sourcing optimisation |
| Meaning | if the gap is 3 %, the RL conversation can be closed |

---

## C. The promise to the customer

### C1. What do +1 / +2 days on the promised date buy? ✅

> "If we stop promising tomorrow and start promising the day after, how much do we save
> and how many customers do we lose?"

| | |
|---|---|
| Axes | `promise.buffer_days: [0, 1, 2, 3]` × `deferral.mode: [none, wait_for_receipt]` × `promise.p_accept_promise: [1.0, 0.6]` |
| Primary metric | `lost_sales_rate` |
| Explanatory | `promise_hit_rate`, `promise_rejection_rate`, `shipping_cost_per_order` |
| Required | `p_accept_promise < 1`, otherwise a long promise wins for free |
| Result | see `papers/study_en.md`, Study B |

### C2. Should an order wait for an inbound receipt instead of being split? ✅

| | |
|---|---|
| Axes | `deferral.mode: [none, wait_for_receipt, wait_to_consolidate]` × `deferral.safety_margin_days` × supplier spread `suppliers[].lead_time_std_days` |
| Primary metric | `lost_sales_rate` and `total_cost_per_order` |
| Explanatory | `deferred_share`, `split_rate`, `promise_hit_rate` |
| Expectation | the strategy should flip as lead-time variance grows |

### C3. On a shortage, ship partial or cancel the whole order?

| | |
|---|---|
| Axes | `shortage.p_partial_ship: [0, 0.5, 1.0]` × `shortage.penalty_post_order: [4, 8, 16]` |
| Primary metric | `total_cost_per_order` |
| Meaning | the break-even point on the "cost of customer disappointment" |

---

## D. Inventory versus software

### D1. Which is cheaper — another week of stock or a smarter sourcing policy? ⚠️

| | |
|---|---|
| Axes | `initial_cover_days: [6, 10, 16, 24, 30]` × policy |
| Primary metric | the equivalence: how many days of cover the `nearest → weighted_score` switch is worth |

Limitation found while probing: `initial_cover_days` only sets the starting stock. The
`base_stock` replenishment policy drives the network to its own steady-state level
within two review cycles, so over a 45–90 day horizon the initial cover has a weak and
non-monotone effect. Answering this honestly requires varying the steady-state level
(`ReplenishmentPolicyConfig.S_overshoot` / `safety_days` / `service_level`), which is not
currently exposed as a sweep axis. That is a development task.

### D2. How much do we lose when purchasing and sourcing disagree?

| | |
|---|---|
| Axes | `replenishment: [base_stock, forecast_driven, network_allocation]` × sourcing policy |
| Primary metric | `total_cost_per_order` |
| Meaning | the interaction matrix is the point, not the main effects |

### D3. How robust is the policy to losing a warehouse? ✅

| | |
|---|---|
| Axes | `shocks.warehouse_outage_days: [0, 3, 7]` × network load ρ × policy |
| Primary metric | degradation of `promise_hit_rate` relative to the calm run |
| Explanatory | `total_cost_per_order`, the daily recovery curve |
| Result | see `papers/study2_en.md`, Study D: a resilience cliff at ρ≈0.8; headroom decides, not the policy |

---

## Gaps to close in the testbed

1. **Multiple-comparison correction.** `osbench compare` computes paired CIs but does not
   adjust them across a policy × metric × cell grid. Holm or Benjamini–Hochberg is needed.
2. **Policy parameters as sweep axes.** Today the experiment YAML takes `replenishment`
   as a bare name. Without `S_overshoot` / `service_level` on the axes, question D1
   cannot be answered correctly.
3. **Missing metrics.** There is no `splits_avoided_by_deferral` (needed for C2), and no
   warehouse-utilisation or days-of-cover series (they explain *why* a different policy
   wins in a given cell).

---

## New engine mechanisms (ready for experiments)

- **A4. Stock-sync latency (`inventory_sync`, §2.12).** The OMS reads a delayed snapshot of
  the WMS book. Axes: `inventory_sync.mode` / `lag_minutes` / `period_minutes` × reservation
  × load ρ. Metrics: `oms_wms_divergence_units`, `shortage_pick_stale_snapshot_share`.
  Hypothesis: the contention threshold above which reservation pays off shifts left in
  proportion to the lag.
- **A5. Customer cancellations (`demand.cancel_rate`).** The honest argument against early
  binding: a reservation holds a hot unit for an order the customer will cancel. Axes:
  `cancel_rate` × reservation × ρ. Metrics: `customer_cancel_rate`, `lost_sales_rate`.
- **E1. Inter-warehouse transfers (`transfers`, §2.13).** The main rival of smart sourcing:
  move stock ahead of time instead of shipping an order from afar. Policies
  `threshold_balance`, `pull_base_stock`. Metrics: `transfer_cost` (part of `total_cost`),
  `transfer_share`, `ping_pong_units`. Ties into D3: does rebalancing let a network recover
  from a warehouse outage.
- **E2. Stochastic lead time (`suppliers[].lead_time_std_days`).** Now actually read by the
  engine (lognormal, `supplier_leadtime` stream). Makes deferred sourcing (C2) riskier — a
  direct extension of Study B.
