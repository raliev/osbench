# ТЗ на разработку: симулятор выбора склада `osbench` (v1)

## Context

**Зачем.** Нужен открытый воспроизводимый бенчмарк-стенд для сравнения алгоритмов выбора склада под
заказ (order sourcing) в e-commerce. Полное исследовательское ТЗ (90-дневные прогоны, 13 политик, LP/ML/RL,
RQ1–RQ5, статья в NeurIPS/EJOR) — это цель проекта в целом. **Данная v1 — это рабочий прототип**, чтобы
заказчик «пощупал, как всё работает»: базовые стратегии, детерминированный мир, живая визуализация по ходу
прогона с возможностью прервать, если что-то пошло не так.

**Решения заказчика по объёму v1** (зафиксированы в этой сессии):
- **Масштаб:** работает на пресетах `tiny` и `small`; `medium` «как получится». Скорость не критична →
  **чистый numpy/Python, без агрессивной оптимизации на Numba**. Numba закладываем как изолированный слой
  горячих функций (geometry/scoring), включаемый позже, но v1 обязан работать и без него.
- **Живой UI:** веб-дашборд на **FastAPI + WebSocket**, Plotly.js в браузере, инкрементальное обновление,
  drill-down по складу кликом, кнопка «стоп» из UI.
- **Глубина модели:** полный happy-path (создание → sourcing → резерв → очередь склада → pick → pack →
  отгрузка → доставка). **Три точки нехватки и отложенный sourcing (Defer/арбитраж) — заглушки/точки
  расширения**: нехватка → отмена или частичная отправка без тонких вероятностей; `reservation: on`,
  `atp: basic`, `promise: zone_fixed`/`transit_plus_buffer`.
- **Данные:** только синтетический генератор, без калибровки по Olist/JD.

**Среда исполнения:** conda env `rapids-13`, Python 3.12.12. Доступны: numpy 2.2.6, numba 0.65, pandas 2.3.3,
polars 1.34, pyarrow 22, scipy 1.16.3 (HiGHS), pydantic 2.12.5, typer 0.25, plotly 6.5, fastapi 0.128,
uvicorn 0.40, websockets 15.0, msgspec, orjson, rich 15, pytest 9.1.1. Нет `pulp`/`highspy`/`gymnasium`
(в v1 не нужны). Проект greenfield: `/home/raufaliev/ecomsim` пуст.

**Исход v1 (критерии «пощупать»):**
1. `osbench generate` + `osbench run --live --serve` на `small` → живой веб-дашборд с метриками, картой
   сети, drill-down по складу, кнопкой «стоп».
2. Три политики (`random`, `nearest`, `cheapest_myopic`) + `weighted_score` сравниваются через
   `osbench compare` с парными ДИ.
3. Все инварианты ядра зелёные на `tiny`/`small`; «золотой прогон» воспроизводим.
4. Добавление новой политики = один файл + регистрация.

---

## Архитектура пакета

Пакет `osbench` с ядром симуляции и слоями вокруг него. Ядро пишет журнал; визуализация/метрики читают
журнал постфактум (и live-снапшоты по ходу). Создаётся как editable-пакет (`pip install -e .`) в env
`rapids-13`.

```
osbench/
  pyproject.toml              # метаданные, entry points политик, deps, pytest/ruff конфиг
  README.md                  # «запуск за 5 минут» + схемы + как добавить политику
  osbench/
    __init__.py
    registry.py              # реестр плагинов: декоратор + entry points
    types.py                 # Assignment, Defer, LineAlloc, PolicyContext, CandidateSet, enums
    protocols.py             # SourcingPolicy, ReplenishmentPolicy (typing.Protocol)
    config/
      schema.py              # pydantic: ScenarioConfig и подсхемы
      policy.py              # SourcingPolicyConfig, ReplenishmentPolicyConfig, WeightedScoreConfig
      experiment.py          # ExperimentConfig, GuardrailConfig, PairedCompareConfig
      loader.py              # YAML → валидация → frozen config + config_hash (sha256)
      presets/{tiny,small,medium}.yaml
    rng/
      streams.py             # именованные RNG-потоки (SeedSequence.spawn, фикс. порядок)
      counter_based.py       # counter-based Philox для событийных потоков (v1: тонкий, расширяемый)
      draws.py               # uniform/expo/lognormal/geometric/zipf поверх потоков
    generate/
      world.py               # регионы, склады, SKU, поставщики, зоны/тарифы → world.parquet
      demand.py              # неоднородный пуассон → orders.parquet
      writer.py              # запись parquet (pyarrow)
    core/
      state.py               # SimState, InventoryTable, OrderTable/LineTable, TransitTable (SoA numpy)
      events.py              # коды событий + приоритеты; EventQueue (heapq v1, с tie-break seq)
      engine.py              # цикл событий: pop→dispatch→handler; чекпойнты; Ctrl+C
      handlers.py            # обработчики событий (happy-path)
      geometry.py            # haversine / плоскость, зона по расстоянию  (njit-опционально)
      shipping.py            # тариф, срок доставки, лимит перевозчика
      atp.py                 # ATP basic (учёт − резерв − страховой)
      candidates.py          # движок кандидатов: build_candidate_set + перебор топ-k + greedy
      promise.py             # промис zone_fixed / transit_plus_buffer
      invariants.py          # онлайн-проверки (debug) + постфактум из журнала
    policies/
      sourcing/{random,nearest,cheapest_myopic,weighted_score}_policy.py
      sourcing/factors.py    # факторы weighted_score + min-max нормировка
      sourcing/_extensions.py # заглушки v2: hindsight_lp, dtlp_bidprice, primal_dual, ... (NotImplementedError)
      replenishment/{base_stock,s_S}_policy.py
      replenishment/_extensions.py # заглушки: R_Q, forecast_driven, network_allocation
    journal/
      schema.py              # версионируемая Arrow-схема events.parquet (SCHEMA_VERSION=1)
      writer.py              # буферизованная запись чанками
    metrics/
      compute.py             # метрики из журнала (polars)
      compare.py             # парное сравнение (t-ДИ + бутстреп) → compare.csv
      golden.py              # канонический дамп + sha256 для золотого прогона
    live/
      snapshot.py            # msgspec-схемы live-снапшота
      collector.py           # агрегаторы в ядре → Snapshot (скользящие окна, P²-перцентили)
      ring.py                # кольцевой буфер снапшотов
      bus.py                 # SPSC-мост ядро→сервер (deque под Lock, drop-oldest)
      control.py             # ControlFlags (stop/pause) через threading.Event
      guardrails.py          # GuardrailEngine + правила ранней остановки
      server.py              # FastAPI + uvicorn в фоновом daemon-потоке, WebSocket /ws
      static/{dashboard.html,dashboard.js,dashboard.css,plotly.min.js}
    reports/
      figures.py             # фабрики plotly Figure (переиспользуются live + report)
      builder.py             # статический офлайн report.html
      compare_builder.py     # compare.html
      stats.py               # t-ДИ, бутстреп, Парето-фронт
    replay/
      index.py               # индексация events.parquet по дню (row groups)
      loader.py              # чтение кусками по дню
      order_trace.py         # хронология одного заказа + причина решения
      server.py              # replay-сервер (переиспользует live/server)
    cli/
      main.py                # typer app
      {run,generate,compare,report,replay,sweep,validate}.py
  tests/
    conftest.py, test_*.py, data/golden/small_seed0/
```

**Раскладка `run_dir`:** `world.parquet`, `orders.parquet`, `events.parquet`, `inventory_daily.parquet`,
`metrics_live.parquet`, `decisions.parquet` (сэмпл причин решений), `metrics.json`, `manifest.json`,
`checkpoints/`, `report.html`.

---

## 1. Модель мира и генератор (`generate/`, `config/`)

- **География:** v1 — синтетическая плоскость (`geo.mode: plane`, 1000×1000) для `tiny`/`small`; `haversine`
  (lat/lon) заложен в `geometry.py` и используется в `medium`.
- **Сущности** (pydantic-схемы в `config/schema.py`, все `extra="forbid"`, `frozen=True`):
  регионы (координаты, `population_weight`), склады (координаты, `rank`, `capacity_shipments_per_day`,
  `storage_capacity_units`, `handling_cost_per_line`, cut-off), тарифы/зоны (`base_cost`, `per_km`,
  `cost_by_zone`, `transit_days_by_zone`, `carrier_parcels_per_day`), SKU (`n_sku`, `zipf_a=1.1`,
  `n_categories`, `region_category_matrix`, вес/объём/себестоимость), поставщики (`unit_price`, `moq`,
  `lead_time_days`, `capacity_per_period`, `sku_ids`).
- **Спрос** (`demand.py`): неоднородный пуассоновский поток = базовая интенсивность × недельный профиль
  (деф. `[1.1,1.0,1.0,1.0,1.2,1.4,0.9]`) × вес региона; строк ~ геометрическое (среднее 1.6); SKU по Ципфу
  с поправкой на категорию региона; qty 1–3. **Предгенерация в `orders.parquet`** — ключ к CRN.
- **Шоки** (`ShockConfig`): пик ×4 на 3 дня, сдвиг спроса между регионами, выпадение склада на M дней.
  В v1 реализуются пик и выпадение склада (нужны для sanity-тестов 9.2).
- Поля глубины модели в схеме присутствуют, но в v1 на дефолтах-заглушках:
  `reservation.enabled: true`, `atp.method: basic`, `promise.method: transit_plus_buffer`,
  `deferral.mode: none`, `consolidation.enabled: false`, `inventory_accuracy: 1.0`, точки нехватки с
  нулевыми вероятностями тонких исходов (нехватка → cancel/partial).

**Пресеты:**

| | tiny | small | medium |
|---|---|---|---|
| регионы / склады / SKU | 2 / 2 / 50 | 5 / 4 / 500 | 20 / 12 / 10000 |
| geo | plane | plane | haversine |
| заказов/день | 20 | 200 | 5000 |
| sim_days | 30 | 90 | 90 |
| назначение | unit-тесты | golden + sanity + демо | «как получится» |

---

## 2. Ядро симуляции (`core/`)

**Время** — целые минуты (`int64`), сутки = 1440. **Цикл событий** — собственный, однопоточный.

**EventQueue (v1):** `heapq` на кортежах `(time_min, type_priority, seq)` — `seq` монотонный tie-break для
детерминизма. Этого достаточно для `tiny`/`small`. *Точка расширения:* njit-бинарная куча на numpy-массивах,
если понадобится `medium`-скорость (описана как план Б, не реализуется в v1).

**Типы событий и приоритеты** (меньше = раньше при равном времени):

| priority | событие | смысл |
|---|---|---|
| 0 | DAY_ROLL | сброс дневных счётчиков мощности/перевозчика, начисление хранения, пополнение, инварианты, flush журнала |
| 1 | RECEIPT | приход PO (раньше новых заказов той же минуты — закладка под будущий арбитраж) |
| 3 | SOURCING_DECISION | вызов политики (модельное время 0, реальное мс логируется) |
| 4 | RESERVE | атомарный резерв строк |
| 5 | WH_QUEUE_ADMIT | допуск в очередь склада с учётом cut-off + дневной мощности |
| 6 | PICK_DONE | завершение сборки (сверка физ. остатка) |
| 7 | PACK_DONE | завершение упаковки |
| 8 | SHIP | отгрузка (лимит перевозчика) |
| 9 | DELIVER | доставка |
| 10 | ORDER_CREATED | новый заказ (позже receipt той же минуты) |
| 11 | ENTER_SOURCING | после ожидания sourcing (равн. 5–30 мин) |
| 13 | REPLENISH_REVIEW | периодический обзор пополнения |
| 14 | CHECKPOINT | сохранение состояния |

**Состояние (SoA numpy)** в `state.py`:
- `InventoryTable`: `on_hand_book`, `on_hand_phys`, `reserved`, `safety_stock`, `reorder_s`, `reorder_S`,
  `assortment` — 2D `[n_sku, n_wh]` `int32`/`uint8`.
- `TransitTable`: приходы в пути (`sku`, `wh`, `qty`, `eta_min`, `po_index`, `status`) — append-only.
- `OrderTable` / `LineTable`: плоские массивы, строки адресуются CSR-смещениями от заказа.
- `SimState` — python-dataclass-контейнер; в функции-ядра передаются конкретные массивы, не весь объект.

**Обработчики (happy-path, `handlers.py`):** ORDER_CREATED (ATP-проверка `basic`; промис; если ATP не
покрывает — v1: частичная отправка или отмена со штрафом упущенной продажи, без тонких вероятностей) →
ENTER_SOURCING → SOURCING_DECISION (вызов политики, замер `policy_time_ms`) → RESERVE (атомарный резерв,
при гонке — пере-сорсинг или частичная ветка) → WH_QUEUE_ADMIT (cut-off + мощность) → PICK_DONE (сверка
физ.; при `inventory_accuracy<1` недостача → корректировка учёта + отмена/частичная) → PACK_DONE → SHIP
(зона, тариф, срок + шум) → DELIVER. DAY_ROLL начисляет хранение, запускает пополнение, прогоняет инварианты.

**RNG/CRN** (`rng/`): мастер-сид → `SeedSequence.spawn()` → именованные потоки в **фиксированном порядке**
(`demand_arrivals`, `demand_basket`, `supplier_leadtime`, `supplier_fill`, `shocks`, `delivery_noise`,
`process_times`, `inventory_error`, `customer_choice`, `policy`). Событийные потоки (`process_times`,
`delivery_noise`, `inventory_error`, `customer_choice`, `supplier_leadtime`, `policy`) реализуются
**counter-based** (Philox с ключом = hash(master_seed, имя потока, ключ-события)), ключи событий:
`(order_id, line_id, stage)`, `(order_id, parcel_index)`, `(sku, warehouse, receipt_index)`,
`(supplier, warehouse, po_index)`. Это даёт CRN (две политики видят один мир) и бит-в-бит resume без
сохранения состояния PRNG. Случайность политик — только из потока `policy`.

**Чекпойнты/resume:** CHECKPOINT-событие и Ctrl+C (`SIGINT`) сохраняют `now_min`, все SoA-массивы, кучу
событий (`seq`), позицию чтения `orders.parquet`, дневные счётчики, `master_seed`, `config_hash`. Событийный
RNG состояние не хранит. `osbench resume` продолжает бит-в-бит.

**Детерминизм:** `fastmath=False` во всех njit-функциях; фиксированный порядок суммирования (без параллельных
редукций); `PYTHONHASHSEED=0` и `*_NUM_THREADS=1` в golden/CI.

---

## 3. Политики (`policies/`, `core/candidates.py`)

**Интерфейс** (`protocols.py`): `decide(order, ctx) -> Assignment | Defer`; опц. `on_period_start(ctx)`,
`fit(history)`. `PolicyContext` (frozen, slots) даёт: `book_onhand`, `reserved`, `atp`, `in_transit` по
SKU×склад (**физический остаток не виден**), загрузку складов, тарифы/сроки по зонам, время, промис, slack,
`rng` (поток `policy`). В v1 `Defer` не используется политиками (`deferral.mode: none`), но тип и ветка
ядра присутствуют как точка расширения.

**Движок кандидатов** (`core/candidates.py`, переиспользуемый, векторизованный numpy):
`build_candidate_set(order, ctx)` → `CandidateSet` (склады с запасом ≥1 строки; `cover=min(atp,qty)`;
`full_cover_mask`; `ship_cost`, `ship_days`, `handling`, `distance_km`). `enumerate_assignments` —
полный перебор по топ-k складам (k=4, ≤15 подмножеств) + `greedy_fill` для дополнения; штраф за разбиение =
`split_penalty_fixed` + реальный тариф доп. посылки. Непокрытые строки → стратегия «после заказа»
(v1: `p_partial_ship` или отмена + штраф). Вынести в njit-ядро — точка расширения (v1 — numpy).

**Политики v1:**
- `random` — для каждой строки случайный склад из покрывающих, случайность **только** из `ctx.rng`.
- `nearest` — склад, покрывающий все строки, минимум по `distance_km`; иначе `greedy_min_parcels`.
- `cheapest_myopic` — `enumerate_assignments`, минимизация `ship+handling`, разбиение разрешено.
- `weighted_score` (Adobe Commerce OMS, патент US 10 839 338) — `score = Σ wᵢ·fᵢ`, `fᵢ∈[0,1]`, `Σwᵢ=1`,
  нормировка **min-max по кандидатам заказа**. Факторы и дефолтные веса: `distance` 0.20, `ship_price` 0.20,
  `promise` 0.25, `splits` 0.10, `above_safety` 0.10, `wh_load` 0.10, `wh_rank` 0.05. Фактор `promise` —
  бинарный «успеваем» (`on_time`, вес 0.7) + линейная добавка за запас дней (насыщение на
  `promise_slack_cap_days=3`). Все веса/диапазоны — в `WeightedScoreConfig` (валидатор `Σw=1`).

**Пополнение v1:** `base_stock` (довести `book+in_transit-reserved` до S, пересмотр раз в R дней; S явно
или через сервис-уровень `z(0.95)=1.645`) и `s_S`. Общие правила: дешёвый поставщик с MOQ и мощностью,
при нехватке следующий; ёмкость склада урезает; `initial_cover_days=14`.

**Точки расширения (зарегистрированы как заглушки `raise NotImplementedError("v2")`):** sourcing —
`hindsight_lp`, `dtlp_bidprice`, `primal_dual`, `spillover_limit`, `rolling_milp`, `learned_scorer`, `ppo`,
`dqn`; replenishment — `R_Q`, `forecast_driven`, `network_allocation`. Хуки `on_period_start`/`fit` и поле
`use_forecast` в контексте уже присутствуют, чтобы v2 подключался без изменения ядра.

**Реестр** (`registry.py`): декоратор `@register_sourcing("name")` + entry points в `pyproject.toml`.
Добавление политики = один файл + одна строка. Детерминизм: имена сортируются перед итерацией.

---

## 4. Живая визуализация (`live/`) — главное требование

**Транспорт:** uvicorn в **фоновом daemon-потоке того же процесса**; ядро пушит снапшоты в `SnapshotBus`
(SPSC `deque(maxlen=512)` под `Lock`, **drop-oldest** при переполнении — ядро никогда не блокируется);
asyncio-таск сервера раздаёт их в WebSocket `/ws`. GIL не мешает: снапшоты редкие (раз в N симул.часов,
деф. 6), сервер в I/O-ожидании GIL отпускает. Падение браузера/WS не роняет прогон; `metrics_live.parquet`
пишется независимо; при реконнекте сервер отдаёт весь ring одним `snapshot_batch`.

**Снапшот** (`live/snapshot.py`, msgspec, ~2–4 КБ без карты): панели `cost` (скользящее 24ч + компоненты
stacked + линия эталона), `service` (вовремя / lost sales / бэкордеры), `network` (split rate, загрузка
складов/перевозчика, очереди), `inventory` (доля SKU в дефиците, дни покрытия), `compute` (p50/p99 времени
решения через онлайн-P², ETA), `invariants` (счётчики нарушений), `guardrails`, `map` (шлётся реже,
`map_every=4`; флоу агрегируются по OD-паре склад→грид-ячейка, топ-N `map_max_flows=300`). Всё кумулятивно,
чтобы поздно подключившийся UI получил полную картину.

**Drill-down по складу:** кольца по складу (запас, загрузка, очередь, inbound/outbound, топ-SKU) агрегируются
**всегда** (12–20 складов — копейки); по клику фронт шлёт WS `{"type":"drill","wh_id":...}` → сервер отдаёт
`wh_detail`. Это прямо закрывает «при выборе warehouse рисуются графики по нему».

**Фронтенд:** один self-contained `dashboard.html` + ванильный JS + **вшитый Plotly.min.js** (офлайн, без
CDN). Инкрементальное обновление через `Plotly.extendTraces(..., maxpoints=360)`; карта/бары — адресный
`restyle`/`react`. Не Dash/Streamlit (server-side перерисовка, лишние зависимости).

**Протокол WS:** сервер→клиент `hello`/`snapshot`/`snapshot_batch`/`wh_detail`/`order_trace`/`guardrail`/
`run_state`; клиент→сервер `drill`/`order`/`control(stop|pause|resume)`/`set_map`/`ping`. Плюс HTTP:
`GET /`, `/api/snapshots?since=`, `/api/wh/{id}`, `/api/order/{id}`, `/healthz`, `POST /api/control`.
Кнопка **«стоп»** → `ControlFlags.stop_requested` → ядро на границе заказа коммитит чекпойнт + частичные
метрики, статус `stopped`.

**Guardrails** (`live/guardrails.py`, конфиг в `ExperimentConfig`): нарушение инварианта → немедленная
остановка `failed`; после прогрева стоимость хуже эталона >`cost_regression_pct`(25%) в течение
`cost_regression_days`(7) подряд → `aborted` (режим `warn` по флагу); lost sales/очередь/`policy_p99` выше
порога. Решения пишутся в `manifest.json`. (`--baseline` даёт сравнение день-в-день благодаря CRN.)

**Причина решения:** сэмплируется `decision_sample_rate=0.01` решений + все решения под guardrail → в
`decisions.parquet` (кандидаты, их оценки, `chosen_warehouse`, `reason_code`). Лента событий одного заказа
показывает оценки кандидатов, если заказ в сэмпле.

**Терминал без `--serve`:** `rich.Live` — прогресс по дням + ETA + компактная таблица метрик на месте;
флаг `--json` → NDJSON для CI.

---

## 5. Метрики, сравнение, отчёты, replay

- **`metrics/compute.py`** (polars, из журнала постфактум): `total_cost_per_order` и компоненты
  (`shipping/handling/holding/purchase`), `promise_hit_rate`, `lateness_days` (p50/p90/p99), `fill_rate_lines`,
  `lost_sales_rate`, `split_rate`, `avg_distance_km`, `fc_utilization`, `inventory_days`, `stockout_events`,
  `decision_time_ms`.
- **`metrics/compare.py`** — парное сравнение по сиду: `d_s = metric(P,s) − metric(B,s)`, среднее + 95%
  t-ДИ + бутстреп 10 000 (поток `bootstrap`). `compare.csv`: `scenario, policy, baseline, metric, n_seeds,
  mean_diff, t_ci_low/high, boot_ci_low/high, t_stat, p_value, significant_95`.
- **`reports/`** — статический офлайн `report.html` (вшитый Plotly inline + данные), `--export png/pdf/csv`
  (kaleido); `compare.html` (таблица политика×метрика с цветом значимости, разброс по сидам, Парето-фронт
  стоимость×вовремя, стоимость×время решения).
- **`replay/`** — `events.parquet` пишется отсортированным, row group ≈ 1 симул.сутки; `index.py` строит
  `day→row_groups` из метаданных pyarrow (мгновенная перемотка); карта + ползунок + хронология заказа
  (`order_trace.py`).

---

## 6. Журнал (`journal/`)

Arrow-схема `events.parquet`, `SCHEMA_VERSION=1` в метаданных файла: `t, type, event_name(dict), order_id,
line_id, sku, warehouse, parcel, qty, cost, promised_at, planned_at, delivered_at, substatus,
policy_time_ms`. Буферизованная запись чанками по 200k строк, `compression='zstd'`, `row_group_size=200000`.

---

## 7. Воспроизводимость и тесты (`tests/`)

- **Золотой прогон** (`metrics/golden.py`): НЕ побайтовый parquet. Журнал → polars → канонизация (сортировка
  по `(t, order_id, line_id, type, warehouse, sku)`; float округлить до 9 знаков) → табличный текстовый дамп
  → `sha256`. Плюс `checksums.json` (сумма стоимости, число событий, counts по типам). CI сверяет для
  `small`/сид 0.
- **Источники недетерминизма закрыть:** фиксированный порядок списков/`sorted()`; `PYTHONHASHSEED=0`;
  сортировка слагаемых перед суммированием; `fastmath=False`; `OMP/OPENBLAS/MKL/NUMBA_NUM_THREADS=1`.
- **Инварианты** (`core/invariants.py` + `osbench validate`, из журнала через polars): баланс запаса/день;
  нет отрицательных; `reserved ≤ book`; `shipped ≤ phys`; после сверки `book==phys`; каждая строка — ровно
  один терминал с точкой отмены; переходы по рёбрам; отгрузки/день ≤ мощности; посылки/день ≤ перевозчика;
  `delivered−shipped = срок` при нулевом шуме; сумма компонент = полной стоимости. Онлайн (в live) —
  подмножество: нет отрицательных, `reserved ≤ book`, лимиты мощности.
- **pytest** (маркеры `fast` — каждый коммит, tiny/small <1–2с; `nightly` — golden/plausibility):
  `test_registry`, `test_config_schemas` (4 пресета валидны), `test_candidates` (cover/full_cover/greedy,
  njit==numpy), `test_policy_{random,nearest,cheapest,weighted_score}`, `test_factors_normalization`,
  `test_rng_streams` (политика видит только `policy`; повтор = идентичный журнал), `test_invariants`
  (все инварианты + искусственное нарушение ловится), `test_compare_stats`, `test_golden_run` (nightly),
  `test_plausibility` (nightly): `nearest` дешевле `random` по доставке ≥20% (сиды 0–9); двойной спрос →
  стоимость↑/вовремя↓/загрузка>100% в пик; выпадение склада → заказы региона к соседям, опоздания↑;
  CRN off → дисперсия парной разности растёт. LP-зависимые проверки (`cheapest` vs LP) — `@skip("v2")`.
- **Покрытие ядра ≥ 85%** на tiny/small.

---

## 8. CLI (`cli/`, Typer)

```
osbench generate --config scenarios/small.yaml --seed 0 --out runs/small_s0/
osbench run --scenario runs/small_s0/ --sourcing weighted_score --replenishment base_stock \
            --out runs/small_s0/ws/ [--live --serve --port 8750 --baseline runs/small_s0/nearest/]
osbench compare runs/small_s*/{random,nearest,ws}/ --baseline nearest --out compare/
osbench report runs/small_s0/ws/ --open
osbench replay runs/small_s0/ws/ --serve --port 8751
osbench sweep --config experiments/demo.yaml      # сценарии×политики×сиды, multiprocessing
osbench validate runs/small_s0/ws/                # инварианты + золотой прогон
osbench resume runs/small_s0/ws/
```
Каждая команда: код выхода 0/1, `--json` → JSON-резюме в stdout для CI.

---

## Порядок реализации (гейты)

1. **Скелет пакета + конфиги + генератор.** `pyproject.toml` (editable install в `rapids-13`), pydantic-схемы,
   `generate/` → `world.parquet` + `orders.parquet`, пресеты tiny/small. RNG-потоки. *Гейт:* `osbench generate`
   на tiny/small, схемы валидируются, orders воспроизводимы по сиду.
2. **Ядро happy-path + журнал + инварианты.** `core/` (события, состояние, обработчики), `journal/`,
   `base_stock`, политики `random`/`nearest`/`cheapest_myopic`. *Гейт:* прогон small зелёный по всем
   инвариантам; золотой прогон small/сид 0 стабилен между двумя запусками.
3. **Метрики, сравнение, статический отчёт.** `metrics/`, `reports/`. *Гейт:* `compare.csv` по трём политикам
   на 30 сидах с парными ДИ; `report.html` открывается офлайн.
4. **`weighted_score` + sanity-тесты 9.2.** Факторы, калибруемые веса. *Гейт:* `test_plausibility` зелёный.
5. **Живой дашборд.** `live/` (FastAPI+WS, снапшоты, drill-down, кнопка «стоп», guardrails), `replay/`.
   *Гейт:* `run --live --serve` на small показывает метрики, drill-down по складу, останавливает прогон по
   кнопке и по guardrail (не по таймеру).

---

## Verification (как проверить end-to-end)

Все команды — в активированном окружении: `conda activate rapids-13`, из `/home/raufaliev/ecomsim`,
после `pip install -e .`.

1. **Генерация и прогон:**
   `osbench generate --config osbench/config/presets/small.yaml --seed 0 --out runs/small_s0/` →
   `osbench run --scenario runs/small_s0/ --sourcing nearest --replenishment base_stock --out runs/small_s0/nearest/`.
   Проверить: появились `events.parquet`, `metrics.json`, `manifest.json`.
2. **Инварианты + золотой прогон:** `osbench validate runs/small_s0/nearest/` → все инварианты зелёные;
   повторный прогон того же сида даёт идентичный `golden_log_sha256`.
3. **Сравнение:** прогнать `random`, `nearest`, `cheapest_myopic`, `weighted_score` на сидах 0–29 →
   `osbench compare ... --baseline nearest` → в `compare.csv` `nearest` дешевле `random` по доставке ≥20%
   со значимым ДИ.
4. **Живой UI (ключевое):** `osbench run --scenario runs/small_s0/ --sourcing weighted_score --live --serve
   --baseline runs/small_s0/nearest/` → открыть `http://127.0.0.1:8750`: метрики рисуются по ходу, клик по
   складу на карте показывает его графики, кнопка «стоп» останавливает прогон с сохранением чекпойнта.
   Запустить политику со сломанными (отрицательными) весами → прогон останавливается по guardrail со
   статусом `aborted`/`failed`, а не висит до конца.
5. **Отчёт и replay:** `osbench report runs/small_s0/ws/ --open` (офлайн HTML); `osbench replay
   runs/small_s0/ws/ --serve` → перемотка по дням и хронология любого заказа.
6. **Тесты:** `pytest -m fast` (быстрые, каждый коммит); `pytest -m nightly` (golden + plausibility);
   покрытие ядра ≥ 85%.
