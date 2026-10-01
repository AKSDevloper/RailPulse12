"""
api/main.py
--------------
Phase 3: The API & Real-Time Gateway.

Runs the event simulator in the background, calls the trained ML model
(via ml/inference.py) to turn live telemetry into ETAs with confidence
windows, tracks real prediction-vs-actual accuracy as trains complete
blocks, and streams all of it to the frontend over a WebSocket. Also
serves the plain HTML/CSS/JS frontend directly, so `uvicorn` is the only
thing you need to run.

Run from the backend/ directory:
    uvicorn api.main:app --reload

Then open:  http://127.0.0.1:8000
"""

import os
import sys
import random
import asyncio
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from track_graph import (  # noqa: E402
    STATIONS, BLOCKS, TRAIN_DEFS, FAULT_TYPES, ROUTE_KM, SIM_START_CLOCK_MIN,
    latlon_for_km, station_by_id, block_by_id,
)
from event_simulator import TrainSimulator  # noqa: E402
from timetable_generator import fmt_clock, build_timetable  # noqa: E402
from ml import inference  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BACKEND_DIR, "..", "frontend")

app = FastAPI(title="SIH26028 Intelligent Train Traffic Control")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

sim = TrainSimulator()
clients: list = []

# per-train bookkeeping so we can compare "what we predicted when the train
# entered this block" against "what actually happened when it left it"
_block_entry = {}          # train_id -> {block_id, entry_min, predicted_min}
_rolling_residuals = []    # real (actual - predicted) minutes, most recent first
_history = []              # rolling series for the dashboard charts
_station_log = []          # station-by-station arrival log: scheduled vs actual, most recent first
_last_cycle = {"n": sim.cycle}
_history_counter = 0
LATEST = {"ready": False}

# train_id, station_id -> scheduled_arrival "HH:MM" (or None for the origin station)
_scheduled_lookup = {
    (row["train_id"], row["station_id"]): row["scheduled_arrival"]
    for row in build_timetable()
}


def _runtime_station_schedule(train):
    """Build the current service day's station schedule from the train's
    randomized departure. This keeps the passenger board synchronized with
    the live simulation instead of showing yesterday's fixed timetable."""
    rows = []
    clock = SIM_START_CLOCK_MIN + train.depart_min
    for i, st in enumerate(STATIONS):
        if i == 0:
            arr = None
            dep = clock
        else:
            travel = (st.km - STATIONS[i - 1].km) / 112.0 * 60
            clock += travel
            arr = clock
            dep = None if i == len(STATIONS) - 1 else clock + 2
            if dep is not None:
                clock = dep
        rows.append({
            "station_id": st.id, "station_name": st.name,
            "scheduled_arrival": None if arr is None else fmt_clock(arr),
            "scheduled_arrival_min": None if arr is None else arr,
            "scheduled_departure": None if dep is None else fmt_clock(dep),
        })
    return rows


def _clock_str_to_min(hhmm: str):
    if not hhmm or hhmm == "--":
        return None
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _signed_clock_diff(actual_min: float, scheduled_min: float) -> float:
    """Minutes actual is after scheduled, shortest signed distance around
    the 24h clock (fine for a same-day demo window)."""
    diff = (actual_min - scheduled_min + 720) % 1440 - 720
    return diff


def _live_features(train, block, gap_km: float, hour_of_day: int) -> dict:
    status, _ = sim.block_status(block.id)
    station_bias = next(s.historical_bias_min for s in STATIONS if s.id == block.to_id)
    from ml.speed_profiles import is_daytime, block_average_speed, historical_train_speed, train_priority_weight
    day_night = is_daytime(hour_of_day)
    fallback_speed = max(1.0, min(train.max_speed_kmh, block.mps_kmh))
    block_avg = block_average_speed(block.id, day_night, fallback_speed)
    train_hist = historical_train_speed(train.number, block.id, block_avg)
    return {
        "train_number": train.number,
        "block_id": block.id,
        "train_type": train.type,
        "block_length_km": block.length_km,
        "block_mps_kmh": block.mps_kmh,
        "max_speed_kmh": train.max_speed_kmh,
        "hour_of_day": hour_of_day,
        "day_night": day_night,
        "preceding_gap_km": round(gap_km, 1),
        "platform_available": 1 if random.random() > 0.15 else 0,
        "historical_station_bias_min": station_bias,
        "block_status": status,
        "historical_train_block_avg_speed": round(train_hist, 2),
        "block_average_speed": round(block_avg, 2),
        "priority_ratio": round(train_hist / max(block_avg, 1.0), 3),
        "priority_weight": train.priority_weight if hasattr(train, "priority_weight") else train_priority_weight(train.type),
    }


def _hour_of_day() -> int:
    return int((SIM_START_CLOCK_MIN + sim.sim_min) // 60) % 24


def _update_block_monitor(train):
    """Called every tick. Detects when a train crosses into a new block,
    scores the PREVIOUS block's prediction against what actually happened,
    then issues a fresh (verbose, printed) prediction for the new block."""
    from track_graph import block_index_for_km
    idx = block_index_for_km(train.pos_km)
    block = BLOCKS[idx]
    prev = _block_entry.get(train.id)

    if prev is None or prev["block_id"] != block.id:
        if prev is not None:
            actual_elapsed = sim.sim_min - prev["entry_min"]
            residual = actual_elapsed - prev["predicted_min"]
            print(f"[ml-monitor] {train.number} cleared block {prev['block_id']}: "
                  f"predicted {prev['predicted_min']:.1f} min, actual {actual_elapsed:.1f} min, "
                  f"error {residual:+.1f} min")
            # Winsorize before it feeds the rolling MAE/accuracy KPI: a fault that
            # clears partway through a block (predicted assuming it persisted the
            # whole block) can produce a legitimate but enormous one-off residual.
            # The full, un-clipped number is still printed above for transparency --
            # this only keeps one transient event from dominating the live dashboard.
            _rolling_residuals.insert(0, max(-30.0, min(30.0, residual)))
            del _rolling_residuals[50:]

            # The train just crossed into a new block, which means it just
            # arrived at the station sitting at the end of the block it left --
            # log that arrival: scheduled time (from the baseline timetable)
            # vs. what the simulator actually clocked, and the delay between them.
            left_block = block_by_id(prev["block_id"])
            arrived_station = station_by_id(left_block.to_id)
            actual_clock_min = (SIM_START_CLOCK_MIN + sim.sim_min) % 1440
            sched_str = _scheduled_lookup.get((train.id, arrived_station.id))
            sched_min = _clock_str_to_min(sched_str)
            delay_min = _signed_clock_diff(actual_clock_min, sched_min) if sched_min is not None else None
            _station_log.insert(0, {
                "train_id": train.id, "train_number": train.number, "train_name": train.name,
                "station_id": arrived_station.id, "station_name": arrived_station.name,
                "scheduled": sched_str or "--",
                "actual": fmt_clock(actual_clock_min),
                "delay_min": round(delay_min, 1) if delay_min is not None else None,
                "cycle": sim.cycle,
            })
            del _station_log[60:]

        gap = sim.nearest_preceding_gap(train.id)
        feats = _live_features(train, block, gap, _hour_of_day())
        pred = inference.predict_section_time(feats, verbose=True)
        _block_entry[train.id] = {
            "block_id": block.id, "entry_min": sim.sim_min, "predicted_min": pred["predicted_min"],
        }


def _estimate_eta(train):
    from track_graph import block_index_for_km
    idx = block_index_for_km(train.pos_km)
    remaining_min = 0.0
    half_widths = []
    for i in range(idx, len(BLOCKS)):
        block = BLOCKS[i]
        gap = sim.nearest_preceding_gap(train.id) if i == idx else 999.0
        feats = _live_features(train, block, gap, _hour_of_day())
        pred = inference.predict_section_time(feats, verbose=False)
        block_min = pred["predicted_min"]
        if i == idx:
            frac = max(0.02, min(1.0, (block.to_km - train.pos_km) / block.length_km))
            block_min *= frac
        remaining_min += block_min
        half_widths.append(max(0.5, (pred["p90_min"] - pred["p10_min"]) / 2))

    eta_min = sim.sim_min + remaining_min
    combined_hw = sum(h ** 2 for h in half_widths) ** 0.5
    return {
        "eta_min": round(eta_min, 1),
        "p10_min": round(max(sim.sim_min + 0.5, eta_min - combined_hw), 1),
        "p90_min": round(eta_min + combined_hw, 1),
    }


def _rolling_metrics():
    if not _rolling_residuals:
        return {"rolling_mae": None, "rolling_accuracy": None, "samples": 0}
    mae = sum(abs(r) for r in _rolling_residuals) / len(_rolling_residuals)
    accuracy = max(35.0, min(99.0, 100 - mae * 9))
    return {"rolling_mae": round(mae, 2), "rolling_accuracy": round(accuracy, 1), "samples": len(_rolling_residuals)}


def _delay_min(train) -> float:
    # Delay is maintained by the simulator from actual speed loss.  It is
    # therefore caused by real operating constraints (headway, block fault,
    # restriction, etc.) rather than by comparing every train with one fixed
    # 100 km/h baseline.
    return round(max(0.0, getattr(train, "delay_min", 0.0)), 1)


def _build_payload():
    from track_graph import block_index_for_km

    if sim.cycle != _last_cycle["n"]:
        # the corridor looped (every train reached Kanpur and a fresh service
        # day started) -- drop stale per-train block-entry bookkeeping so we
        # don't score a new trip's block-1 against last cycle's block-5 entry.
        _block_entry.clear()
        _last_cycle["n"] = sim.cycle

    active_trains = [t for t in sim.trains if t.status == "running"]
    trains_payload = []
    for t in sim.trains:
        lat, lon = latlon_for_km(t.pos_km)
        entry = {
            "id": t.id, "number": t.number, "name": t.name, "type": t.type,
            "priority_weight": getattr(t, "priority_weight", 0.85),
            "status": t.status, "position_km": round(t.pos_km, 2),
            "lat": lat, "lon": lon, "platform": t.platform,
            "live_clock": fmt_clock(SIM_START_CLOCK_MIN + sim.sim_min),
            "delay_min": _delay_min(t),
            "speed_kmh": round(getattr(t, "speed_kmh", 0.0), 1),
            "delay_reason": getattr(t, "delay_reason", "On time"),
            "safety_gap_km": round(getattr(t, "safety_gap_km", 999.0), 1),
            "station_schedule": _runtime_station_schedule(t),
        }
        if t.status == "running":
            _update_block_monitor(t)
            eta = _estimate_eta(t)
            entry.update(eta)
            entry["eta_clock"] = fmt_clock(SIM_START_CLOCK_MIN + eta["eta_min"])
            entry["p10_clock"] = fmt_clock(SIM_START_CLOCK_MIN + eta["p10_min"])
            entry["p90_clock"] = fmt_clock(SIM_START_CLOCK_MIN + eta["p90_min"])

            # fresh, tick-live feature snapshot for the current block, purely
            # for display -- lets the dashboard show exactly what the model
            # is looking at right now for this train, not a stale value.
            idx = block_index_for_km(t.pos_km)
            block = BLOCKS[idx]
            gap = sim.nearest_preceding_gap(t.id)
            feats = _live_features(t, block, gap, _hour_of_day())
            pred = inference.predict_section_time(feats, verbose=False)
            entry["live_features"] = feats
            entry["block_prediction"] = pred
            entry["current_block_id"] = block.id
            entry["current_block_status"] = sim.block_status(block.id)[0]
            entry["priority_target_speed_kmh"] = round(min(t.max_speed_kmh, block.mps_kmh) * getattr(t, "priority_weight", 0.85), 1)
            # Passenger-facing next stop and traffic-spacing explanation.
            next_station = next((st for st in STATIONS if st.km > t.pos_km + 0.5), STATIONS[-1])
            entry["next_station_id"] = next_station.id
            entry["next_station_name"] = next_station.name
            leader = min((x for x in active_trains if x.id != t.id and x.pos_km > t.pos_km),
                         key=lambda x: x.pos_km, default=None)
            if leader is not None and entry["safety_gap_km"] < 15:
                entry["conflict_status"] = "caution"
                entry["conflict_train_number"] = leader.number
            else:
                entry["conflict_status"] = "clear"
                entry["conflict_train_number"] = None

        # Complete station-by-station live board data.
        logs = {(r["station_id"], r["cycle"]): r for r in _station_log if r["train_id"] == t.id}
        board = []
        for row in entry["station_schedule"]:
            st = station_by_id(row["station_id"])
            log = logs.get((row["station_id"], sim.cycle))
            sched_min = row["scheduled_arrival_min"]
            sched = row["scheduled_arrival"] or "--"
            if log:
                live = log["actual"]
                delay = log["delay_min"] or 0.0
                status = "Reached"
            elif st.km < t.pos_km - 0.5:
                live = sched
                delay = 0.0
                status = "Departed"
            elif t.status == "completed" and st.id == STATIONS[-1].id:
                actual_clock = (SIM_START_CLOCK_MIN + t.completed_min) % 1440
                live = fmt_clock(actual_clock)
                delay = _signed_clock_diff(actual_clock, sched_min) if sched_min is not None else 0.0
                status = "Reached"
            elif t.status == "running" and st.km > t.pos_km + 0.5:
                # Use the live ETA for the immediate next stop and the baseline
                # schedule plus current train delay for later stops.
                if st.id == entry.get("next_station_id"):
                    live = entry.get("eta_clock", sched)
                    delay = max(0.0, _signed_clock_diff(_clock_str_to_min(live), sched_min)) if sched_min is not None else 0.0
                    status = "Next station"
                else:
                    live = pax_est = fmt_clock((sched_min or 0) + _delay_min(t)) if sched_min is not None else "--"
                    delay = _delay_min(t)
                    status = "Upcoming"
            else:
                live = sched
                delay = 0.0
                status = "At origin" if st.id == STATIONS[0].id else "Upcoming"
            board.append({"station_id": st.id, "station_name": st.name, "scheduled": sched, "live": live, "delay_min": round(max(0.0, delay),1), "status": status, "platform": t.platform})
        entry["station_board"] = board
        trains_payload.append(entry)

    blocks_payload = []
    for i, b in enumerate(BLOCKS):
        status, _ = sim.block_status(b.id)
        occ = sum(1 for t in active_trains if block_index_for_km(t.pos_km) == i)
        blocks_payload.append({
            "id": b.id, "from_id": b.from_id, "to_id": b.to_id,
            "from_name": b.from_name, "to_name": b.to_name,
            "from_km": b.from_km, "to_km": b.to_km, "length_km": b.length_km,
            "mps_kmh": b.mps_kmh, "status": status, "occupancy": occ,
        })

    faults_payload = [
        {"id": f.id, "block_id": f.block_id, "label": f.label, "effect": f.effect,
         "start_min": f.start_min, "end_min": f.end_min,
         "clears_in_min": round(max(0, f.end_min - sim.sim_min), 1)}
        for f in sim.faults if sim.sim_min < f.end_min
    ]

    global _history_counter
    _history_counter += 1

    avg_delay = (sum(_delay_min(t) for t in sim.trains if t.status != "scheduled") /
                 max(1, len([t for t in sim.trains if t.status != "scheduled"])))
    utilization = round(len({block_index_for_km(t.pos_km) for t in active_trains}) / len(BLOCKS) * 100)

    metrics = _rolling_metrics()
    _history.insert(0, {
        "t": _history_counter,
        "avg_delay": round(avg_delay, 2),
        "rolling_mae": metrics["rolling_mae"],
        "rolling_accuracy": metrics["rolling_accuracy"],
        "active_trains": len(active_trains),
        "utilization": utilization,
    })
    del _history[70:]

    # Live platform board for every station. Each station exposes four demo
    # platforms. A train reserves its assigned platform at the next station
    # while approaching, and becomes OCCUPIED inside the final 8 km.
    station_platforms = []
    for st in STATIONS:
        approaching = []
        for t in active_trains:
            next_station = next((x for x in STATIONS if x.km > t.pos_km + 0.5), STATIONS[-1])
            if next_station.id == st.id:
                approaching.append(t)
        platform_rows = []
        for pf in range(1, 5):
            train = next((t for t in approaching if t.platform == pf), None)
            occupied = bool(train and abs(st.km - train.pos_km) <= 8)
            platform_rows.append({
                "platform": pf,
                "status": "occupied" if occupied else ("reserved" if train else "free"),
                "train_number": train.number if train else None,
                "train_name": train.name if train else None,
                "eta_clock": fmt_clock(SIM_START_CLOCK_MIN + sim.sim_min) if train else None,
            })
        station_platforms.append({"station_id": st.id, "station_name": st.name, "platforms": platform_rows})

    bundle = inference._load()
    return {
        "sim_min": round(sim.sim_min, 1),
        "clock": fmt_clock(SIM_START_CLOCK_MIN + sim.sim_min),
        "cycle": sim.cycle,
        "trains": trains_payload,
        "blocks": blocks_payload,
        "faults": faults_payload,
        "station_platforms": station_platforms,
        "kpis": {
            "active_trains": len(active_trains),
            "avg_delay_min": round(avg_delay, 2),
            "track_utilization_pct": utilization,
            "on_time_pct": round(
                100 * len([t for t in sim.trains if t.status != "scheduled" and _delay_min(t) <= 5]) /
                max(1, len([t for t in sim.trains if t.status != "scheduled"]))
            ),
        },
        "ml_monitor": {
            **metrics,
            "model_name": bundle["model_name"],
            "trained_mae": round(bundle["metrics"]["mae"], 2),
            "trained_r2": round(bundle["metrics"]["r2"], 3),
        },
        "history": list(reversed(_history[:70])),
        "station_log": _station_log[:40],
    }


# --------------------------------------------------------------------------- REST

@app.get("/api/health")
def health():
    return {"status": "ok", "sim_min": sim.sim_min, "cycle": sim.cycle}


@app.get("/api/route")
def get_route():
    return {
        "stations": [{"id": s.id, "name": s.name, "km": s.km} for s in STATIONS],
        "blocks": [{"id": b.id, "from_id": b.from_id, "to_id": b.to_id,
                    "from_name": b.from_name, "to_name": b.to_name,
                    "from_km": b.from_km, "to_km": b.to_km,
                    "length_km": b.length_km, "mps_kmh": b.mps_kmh} for b in BLOCKS],
        "route_km": ROUTE_KM,
    }


@app.get("/api/timetable")
def get_timetable():
    import csv
    path = os.path.join(BACKEND_DIR, "data", "timetable.csv")
    if not os.path.exists(path):
        raise HTTPException(404, "timetable.csv not found -- run timetable_generator.py first")
    with open(path) as f:
        return list(csv.DictReader(f))


@app.get("/api/state")
def get_state():
    """Return the next simulation snapshot.

    The original local version used a background WebSocket loop. Vercel runs
    this API as a serverless function, so a request-driven tick is used for
    deployment compatibility. The in-memory simulator is intentionally kept
    at module scope so warm Vercel instances continue the demo smoothly.
    """
    sim.tick()
    payload = _build_payload()
    payload["ready"] = True
    LATEST.clear()
    LATEST.update(payload)
    return LATEST


class FaultRequest(BaseModel):
    block_id: str
    fault_type: str


@app.post("/api/faults")
def create_fault(req: FaultRequest):
    if req.fault_type not in FAULT_TYPES:
        raise HTTPException(400, f"unknown fault_type. choose from {list(FAULT_TYPES.keys())}")
    if not any(b.id == req.block_id for b in BLOCKS):
        raise HTTPException(400, f"unknown block_id. choose from {[b.id for b in BLOCKS]}")
    f = sim.inject_fault(req.block_id, req.fault_type)
    print(f"[api] Fault injected: {f.label} on {f.block_id}, clears at sim_min {f.end_min:.0f}")
    return {"id": f.id, "block_id": f.block_id, "label": f.label}


@app.delete("/api/faults/{fault_id}")
def delete_fault(fault_id: str):
    sim.clear_fault(fault_id)
    return {"cleared": fault_id}


@app.get("/api/fault-types")
def get_fault_types():
    return FAULT_TYPES


@app.post("/api/next-cycle")
def next_cycle():
    """Manually start a fresh service day right now instead of waiting for
    every train to reach Kanpur on its own -- e.g. 'start cycle 2' on demand."""
    before = sim.cycle
    sim.force_new_cycle()
    print(f"[api] Manual cycle advance requested: cycle {before} -> {sim.cycle}")
    return {"previous_cycle": before, "new_cycle": sim.cycle}


@app.get("/api/model-info")
def get_model_info():
    bundle = inference._load()
    return {
        "model_name": bundle["model_name"],
        "training_samples": bundle.get("training_samples"),
        "metrics": bundle["metrics"],
        "feature_importances": bundle.get("feature_importances"),
        "feature_columns": bundle["feature_columns"],
    }


# --------------------------------------------------------------------------- WebSocket

@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    print(f"[ws] client connected ({len(clients)} total)")
    try:
        if LATEST.get("ready"):
            await websocket.send_json(LATEST)
        while True:
            await websocket.receive_text()  # keep the connection open; we don't need client messages
    except WebSocketDisconnect:
        clients.remove(websocket)
        print(f"[ws] client disconnected ({len(clients)} total)")


async def _simulation_loop():
    while True:
        sim.tick()
        payload = _build_payload()
        payload["ready"] = True
        LATEST.clear()
        LATEST.update(payload)
        dead = []
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in clients:
                clients.remove(ws)
        print(f"[tick] sim_min={sim.sim_min:.0f} clock={payload['clock']} "
              f"active={payload['kpis']['active_trains']} "
              f"on_time={payload['kpis']['on_time_pct']}% "
              f"faults={len(payload['faults'])} "
              f"-> broadcast to {len(clients)} client(s)")
        await asyncio.sleep(1.0)


# --------------------------------------------------------------------------- Startup
# The serverless deployment advances the simulation from /api/state requests.
# No permanent background task is started here because Vercel instances are
# ephemeral and do not provide a persistent worker process.

@app.on_event("startup")
async def on_startup():
    print("=" * 70)
    print("SIH26028 -- Intelligent Train Traffic Control backend starting")
    print(f"Model: {inference._load()['model_name']}")
    print("Mode: request-driven live simulation (Vercel compatible)")
    print("=" * 70)


# --------------------------------------------------------------------------- static frontend
# Mounted last so it acts as a catch-all and doesn't shadow the /api and /ws routes above.
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
