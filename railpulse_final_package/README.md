# SIH26028 — Intelligent Train Traffic Control (Delhi → Kanpur)

A runnable prototype: a physics-based telemetry simulator, a real trained
ML pipeline (dataset generation → feature engineering → training →
inference → live monitoring), a FastAPI + WebSocket backend, and a plain
HTML/CSS/JS control-room dashboard. No React, no npm, no database server
required — just Python and a browser.

```
sih26028_project/
├── database/
│   └── schema.sql                     # production PostgreSQL+PostGIS schema (reference only)
├── backend/
│   ├── track_graph.py                 # the route graph: stations, blocks, MPS, trains
│   ├── timetable_generator.py         # Phase 1: baseline timetable CSV
│   ├── event_simulator.py             # Phase 1: the live telemetry engine
│   ├── ml/
│   │   ├── generate_historical_dataset.py  # Phase 2: build the training set
│   │   ├── feature_engineering.py          # Phase 2: raw telemetry -> feature matrix
│   │   ├── train_model.py                  # Phase 2: train + evaluate + save the model
│   │   └── inference.py                    # Phase 2: load model, predict, show the feature vector
│   ├── api/
│   │   └── main.py                    # Phase 3: REST + WebSocket + ML orchestration + serves the frontend
│   └── data/                          # generated CSVs / model_bundle.pkl land here
└── frontend/
    ├── index.html                     # Phase 4: dashboard shell
    ├── style.css
    └── app.js                         # fetch + WebSocket client, all page rendering
```

## 1. Set up (once)

Open this folder in VS Code, then in its integrated terminal:

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Build the data + train the model (once)

Run these **in order** from the `backend/` directory. Each one prints what
it's doing as it goes — this is deliberate, so you can actually see the data
being generated, transformed, and learned from at every stage.

```bash
cd backend

# Phase 1 — generate the static route graph's timetable
python timetable_generator.py

# (optional) watch live telemetry stream to the console, like a real GPS feed
python event_simulator.py --ticks 20 --interval 0.3

# Phase 2 — build a historical dataset from thousands of simulated trips
python ml/generate_historical_dataset.py --trips 3000

# turn that raw telemetry into the ML feature matrix (prints every step)
python ml/feature_engineering.py

# train the model, print metrics + feature importance, save model_bundle.pkl
python ml/train_model.py

# (optional) sanity-check a single live prediction + see its feature vector
python ml/inference.py
```

You should end up with these files in `backend/data/`:
`timetable.csv`, `historical_telemetry.csv`, `training_features.csv`,
`feature_encoding.json`, `model_bundle.pkl`.

## 3. Run the server

From the `backend/` directory:

```bash
uvicorn api.main:app --reload
```

Then open **http://127.0.0.1:8000** in your browser. That's the whole app —
FastAPI serves the frontend directly, so there's no separate frontend server.

The terminal will keep printing live logs every tick: which trains are
active, on-time %, active faults, and — every time a train finishes a
block — the real predicted-vs-actual comparison the ML monitor is scoring
itself against. This is intentional; it's the same "show how the data is
being taken" transparency as the standalone scripts, just running live.

## How the ML pipeline actually works

1. **`generate_historical_dataset.py`** runs the same physics as the live
   simulator thousands of times, headless, across randomised conditions
   (faults, gaps, times of day) and records the *actual* time each block
   took — this is the labelled training data.
2. **`feature_engineering.py`** turns that raw telemetry into the exact
   numeric matrix the model trains on (one-hot encoding, derived features
   like `speed_headroom`), printing every transformation.
3. **`train_model.py`** trains a gradient-boosted regressor (XGBoost if
   installed, otherwise an equivalent scikit-learn model so the project
   still runs) to predict `actual_time_min`, and derives an empirical 80%
   prediction interval from held-out residuals — that's what powers the
   "arriving between X and Y" confidence window.
4. **`inference.py`** loads that trained model once and exposes
   `predict_section_time()`, which the API calls for every live train.
5. **The API (`api/main.py`)** does something most demos skip: every time a
   train actually clears a block, it compares the real outcome against what
   the model predicted when the train entered that block, and feeds that
   real error into a rolling accuracy/MAE metric shown live on the ML
   Analytics page. That's a genuine feedback loop computed from real
   prediction-vs-actual data, not a simulated animation. (Retraining the
   model itself on freshly-logged data is a periodic offline job in this
   architecture — same as a real MLOps setup — rather than per-tick weight
   updates, which wouldn't reflect how a production model actually works.)

## Notes

- **No database is required to run this.** Live state lives in memory
  (`event_simulator.TrainSimulator`); generated datasets are plain CSVs.
  `database/schema.sql` is the PostgreSQL + PostGIS schema you'd migrate to
  for a real deployment with persistent history — useful for the report/
  architecture diagram, not needed to run the demo.
- The simulation auto-loops: once every train reaches Kanpur, a fresh
  service day starts automatically. You can also force a fresh cycle at any
  time from the **Overview** page ("Start next cycle" button /
  `POST /api/next-cycle`) rather than waiting for it to happen naturally.
- **Fault Injection** is available both on its own page and directly on
  **Overview** — pick a section + fault type → watch the schematic map,
  block table, passenger countdowns, and ETAs all update within one tick
  (~1 second).
- **Automatic delay events**, distinct from controller-injected faults, fire
  on their own as the simulation runs: `held_for_crossing` (a train stopped
  or nearly stopped for another to clear the section — most reliably
  triggered by injecting a fault near a train that's following closely),
  `close_following`, `fog` (early-morning hours, reduced speed corridor-wide),
  `technical_halt` (brief unscheduled stop), and `platform_wait` (held
  approaching the terminus). All of these are baked into the historical
  training data too (see `held_for_crossing` in the feature importance
  chart), so the model's predictions and confidence intervals stay
  calibrated to the richer live physics rather than drifting out of sync
  with it.
- **Overview → Station arrivals** logs, for every station a train clears,
  the scheduled time (from the baseline timetable) against what the
  simulator actually clocked, with the signed delay — a live, per-station
  version of "did this train run late," not just an end-to-end ETA.
- If XGBoost isn't installed, `train_model.py` automatically falls back to
  scikit-learn's `GradientBoostingRegressor` — the pipeline still runs
  end-to-end either way, it'll just tell you which one it used.

## New ML speed-profile and priority features

The ML pipeline now persists historical operating-speed profiles in `backend/data/speed_profiles.json`:

- `block_day_avg_speed`: average observed speed for each block during 06:00-17:59.
- `block_night_avg_speed`: average observed speed for each block during 18:00-05:59.
- `train_block_avg_speed`: historical average observed speed for each train number on each block.
- `block_overall_avg_speed`: overall block reference speed.

The training matrix includes `day_night` (1 daytime / 0 nighttime), `historical_train_block_avg_speed`, `block_average_speed`, `priority_ratio`, and `priority_weight`.

Priority weights used by the simulator are: Rajdhani 1.00, Superfast 0.97, Mail/Express 0.90, Express 0.88, Passenger 0.78, with Freight reserved at 0.72 for future train definitions. These weights make premium services operate closer to the applicable maximum speed while still obeying block restrictions, faults, and headway safety controls.

### Rebuild the ML model

The old `model_bundle.pkl` is intentionally not distributed because pickled sklearn/XGBoost models are tied to the environment that created them. On first backend startup, `backend/ml/inference.py` detects a missing/incompatible bundle and automatically regenerates the historical dataset, speed profiles, features, and model using the current virtual environment.

Manual pipeline, if preferred:

```powershell
.\venv\Scripts\python.exe backend\ml\generate_historical_dataset.py --trips 3000 --seed 7
.\venv\Scripts\python.exe backend\ml\feature_engineering.py
.\venv\Scripts\python.exe backend\ml\train_model.py
.\venv\Scripts\python.exe -m uvicorn backend.api.main:app --reload
```

The dashboard's Network Delay Trend now uses a monotonic history index, so a service-day/cycle reset cannot overwrite the first cycle's X-axis and create a vertical graph jump. ML feature-importance bars now use their actual percentage values rather than normalizing every bar against the largest feature.


## RailPulse passenger experience and ML additions

The dashboard includes a passenger-facing live journey view with predicted arrival windows, progress, current speed, next station, platform, block condition, delay reason, passenger alerts, station-by-station schedule comparison, and a live service list.

The ML pipeline stores separate daytime/nighttime block speed profiles and train-number/block historical speeds. Live inference uses the Day/Night flag, Priority Ratio and operational priority weight. Safety headway, block restrictions and faults always take precedence over train priority.

### Live map behavior
- Train markers stay on the single rail line with a vertical leader and train number above the dot.
- Clicking a train opens an in-map live information panel with train name, time, delay, speed, next station, platform, ETA, headway, block and control action.
- The simulator enforces a hard 5 km minimum separation and automatically regulates the following train instead of allowing overlap or overtaking.
- Passenger-visible delay is reserved for actual operational disruptions; ordinary headway regulation can slow a train without marking every train delayed.
- Station Master platform occupancy is shown for every corridor station, with FREE / RESERVED / OCCUPIED platform states.
