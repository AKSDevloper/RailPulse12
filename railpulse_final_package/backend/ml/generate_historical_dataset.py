"""
generate_historical_dataset.py
---------------------------------
Phase 2, step 1: Dataset Construction.

Live railway data is restricted, so we build our training set by running
the SAME physics engine as event_simulator.py thousands of times, headless,
across randomised conditions (different train mixes, random faults, random
preceding-train gaps, different times of day). Each completed block gives
us one labelled training row: the features known at block-entry time, and
the ACTUAL time it took to clear that block.

This is the raw data the ML model learns from. feature_engineering.py
takes this file and turns it into the final training matrix.

Run:  python ml/generate_historical_dataset.py --trips 3000
"""

import os
import sys
import csv
import random
import argparse

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from track_graph import STATIONS, BLOCKS, TRAIN_DEFS, FAULT_TYPES  # noqa: E402
try:
    from .speed_profiles import train_priority_weight  # package import
except ImportError:
    from speed_profiles import train_priority_weight  # direct script import

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# Probability a given block on a given trip is affected by some disruption
FAULT_PROBABILITY = 0.12
FAULT_KEYS = list(FAULT_TYPES.keys())

HOURS_OF_DAY = list(range(5, 23))  # trains run 05:00 - 23:00


def simulate_one_trip(trip_id: int, rng: random.Random):
    """Simulate one train's full run across every block and yield one
    labelled row per block -- this mirrors exactly what the live simulator
    does per-tick (event_simulator.TrainSimulator.tick), just collapsed
    straight to per-block outcomes so we can generate a large dataset
    quickly. Kept in sync with the live physics (gap-based holds, fog,
    technical halts, platform waits) so the trained model stays calibrated
    to what the live dashboard actually does."""
    train_def = rng.choice(TRAIN_DEFS)
    hour_of_day = rng.choice(HOURS_OF_DAY)
    day_night = 1 if 6 <= hour_of_day < 18 else 0
    platform_available = 1 if rng.random() > 0.15 else 0

    rows = []
    for block_i, block in enumerate(BLOCKS):
        # -- known-at-prediction-time features --------------------------------
        # Gap frequency is deliberately skewed toward "clear ahead" -- trains
        # are usually well spaced, and a genuinely tight gap (held-for-crossing
        # territory) is a rare event, same as it is in the live simulator.
        r = rng.random()
        if r < 0.04:
            preceding_gap_km = rng.uniform(2, 8)       # rare: tight, held-for-crossing zone
        elif r < 0.15:
            preceding_gap_km = rng.uniform(8, 15)      # occasional: close following
        elif r < 0.35:
            preceding_gap_km = rng.uniform(15, 25)     # mild caution
        else:
            preceding_gap_km = rng.uniform(25, 80)     # majority: clear ahead
        preceding_gap_km = round(preceding_gap_km, 1)
        station_bias = next(s.historical_bias_min for s in STATIONS if s.id == block.to_id)

        block_status = "clear"
        speed_factor = 1.0
        if rng.random() < FAULT_PROBABILITY:
            fkey = rng.choice(FAULT_KEYS)
            spec = FAULT_TYPES[fkey]
            block_status = spec["effect"]
            speed_factor = spec["speed_factor"]

        # -- ground truth: what actually happened -------------------------------
        # The live simulator applies these tick-by-tick, so a transient event
        # (a brief hold, a technical check) only affects the few minutes it's
        # actually active for -- not the whole block. To keep the historical
        # data physically honest, sustained conditions (a block fault, fog)
        # scale the whole block's time, but brief incidents (a hold for a
        # passing train, a technical halt, a platform wait) are modelled as a
        # bounded extra delay added on top, not a block-wide speed multiplier.
        mild_preceding_factor = 0.85 if 15 <= preceding_gap_km < 25 else 1.0
        random_factor = rng.uniform(0.90, 1.08)

        sustained_factor = speed_factor * mild_preceding_factor * random_factor
        # Simulated historical operating profile: nighttime traffic runs a
        # little below the daytime profile so the Day/Night feature carries
        # measurable information rather than being a label with no effect.
        if day_night == 0:
            sustained_factor *= 0.94
        if hour_of_day in (5, 6, 7) and rng.random() < 0.10:
            sustained_factor *= 0.80                             # fog: whole block runs a bit slower

        capped_speed = min(train_def.max_speed_kmh, block.mps_kmh)
        priority_weight = train_priority_weight(train_def.type)
        effective_speed = max(4.0, capped_speed * priority_weight * sustained_factor)
        base_time_min = block.length_km / effective_speed * 60

        extra_delay_min = 0.0
        if preceding_gap_km < 8:
            extra_delay_min += rng.uniform(4, 14)                 # held for a preceding train to clear
        elif preceding_gap_km < 15:
            extra_delay_min += rng.uniform(1, 5)                  # close following, mild caution
        if rng.random() < 0.006:
            extra_delay_min += rng.uniform(12, 30)                # technical/brake check halt
        if block_i == len(BLOCKS) - 1 and rng.random() < 0.03:
            extra_delay_min += rng.uniform(5, 15)                 # platform wait near terminus

        actual_time_min = base_time_min + extra_delay_min

        rows.append({
            "trip_id": trip_id,
            "train_number": train_def.number,
            "train_type": train_def.type,
            "max_speed_kmh": train_def.max_speed_kmh,
            "priority_weight": priority_weight,
            "day_night": day_night,
            "block_id": block.id,
            "from_station": block.from_id,
            "to_station": block.to_id,
            "block_length_km": block.length_km,
            "block_mps_kmh": block.mps_kmh,
            "hour_of_day": hour_of_day,
            "preceding_gap_km": preceding_gap_km,
            "platform_available": platform_available,
            "historical_station_bias_min": station_bias,
            "block_status": block_status,
            "actual_time_min": round(actual_time_min, 2),
        })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trips", type=int, default=3000, help="number of simulated historical trips to generate")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "historical_telemetry.csv")

    print(f"Generating {args.trips} simulated historical trips across {len(BLOCKS)} blocks each...")
    fieldnames = None
    total_rows = 0
    with open(out_path, "w", newline="") as f:
        writer = None
        for trip_id in range(1, args.trips + 1):
            rows = simulate_one_trip(trip_id, rng)
            if writer is None:
                fieldnames = list(rows[0].keys())
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
            writer.writerows(rows)
            total_rows += len(rows)
            if trip_id % 500 == 0:
                print(f"  ... {trip_id}/{args.trips} trips simulated ({total_rows} block-records so far)")

    print(f"\nDone. Wrote {total_rows} block-level records -> {out_path}")
    print("Columns:", ", ".join(fieldnames))


if __name__ == "__main__":
    main()
