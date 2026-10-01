"""
feature_engineering.py
----------------------
Builds the ML feature matrix and persistent historical speed profiles.

Historical speed is observed speed for a block:
    block_length_km / actual_time_min * 60

Stored profiles:
- average daytime speed for every block (06:00-17:59)
- average nighttime speed for every block (18:00-05:59)
- historical average speed for every train number on every block

The ML matrix additionally contains:
- day_night: 1 daytime, 0 nighttime
- priority_ratio: train/block historical speed divided by overall block speed
- priority_weight: operational class weight
"""

import os
import json
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
RAW_PATH = os.path.join(DATA_DIR, "historical_telemetry.csv")
OUT_PATH = os.path.join(DATA_DIR, "training_features.csv")
ENCODING_PATH = os.path.join(DATA_DIR, "feature_encoding.json")
PROFILE_PATH = os.path.join(DATA_DIR, "speed_profiles.json")

FEATURE_COLUMNS = [
    "block_length_km",
    "block_mps_kmh",
    "max_speed_kmh",
    "speed_headroom",
    "hour_of_day",
    "day_night",
    "preceding_gap_km",
    "close_following",
    "held_for_crossing",
    "platform_available",
    "historical_station_bias_min",
    "block_status_clear",
    "block_status_restricted",
    "block_status_blocked",
    "historical_train_block_avg_speed",
    "block_average_speed",
    "priority_ratio",
    "priority_weight",
    "train_type_code",
]
TARGET_COLUMN = "actual_time_min"

PRIORITY_WEIGHTS = {
    "Rajdhani": 1.00,
    "Superfast": 0.97,
    "Mail/Express": 0.90,
    "Express": 0.88,
    "Passenger": 0.78,
    "Freight": 0.72,
}


def main():
    print(f"[1/5] Loading raw simulated telemetry from {RAW_PATH} ...")
    df = pd.read_csv(RAW_PATH)
    df["train_number"] = df["train_number"].astype(str)
    print(f"       -> {len(df)} rows, {len(df.columns)} raw columns")

    # Observed block speed from the actual block time. This is the historical
    # speed statistic used by both the profile tables and priority ratio.
    df["observed_avg_speed_kmh"] = (
        df["block_length_km"] / df["actual_time_min"].clip(lower=0.1) * 60
    ).clip(lower=0, upper=df["max_speed_kmh"].clip(lower=1))

    # 1 = daytime (06:00-17:59), 0 = nighttime (18:00-05:59).
    df["day_night"] = df["hour_of_day"].apply(lambda h: 1 if 6 <= int(h) < 18 else 0)
    df["priority_weight"] = df["train_type"].map(PRIORITY_WEIGHTS).fillna(0.85)

    print("\n[2/5] Calculating and storing historical speed profiles ...")
    block_overall = df.groupby("block_id")["observed_avg_speed_kmh"].mean()
    block_day = df[df["day_night"] == 1].groupby("block_id")["observed_avg_speed_kmh"].mean()
    block_night = df[df["day_night"] == 0].groupby("block_id")["observed_avg_speed_kmh"].mean()
    train_block = df.groupby(["train_number", "block_id"])["observed_avg_speed_kmh"].mean()

    profiles = {
        "definition": {
            "daytime": "06:00-17:59",
            "nighttime": "18:00-05:59",
            "speed_formula": "block_length_km / actual_time_min * 60",
        },
        "block_day_avg_speed": {str(k): round(float(v), 2) for k, v in block_day.items()},
        "block_night_avg_speed": {str(k): round(float(v), 2) for k, v in block_night.items()},
        "block_overall_avg_speed": {str(k): round(float(v), 2) for k, v in block_overall.items()},
        "train_block_avg_speed": {},
    }
    for (train_number, block_id), value in train_block.items():
        profiles["train_block_avg_speed"].setdefault(str(train_number), {})[str(block_id)] = round(float(value), 2)

    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2)
    print(f"       -> block day/night profiles: {len(block_overall)} blocks")
    print(f"       -> train-number/block profiles: {len(profiles['train_block_avg_speed'])} train numbers")

    # Merge block and train historical speed values into each training row.
    df["block_average_speed"] = df["block_id"].map(block_overall).fillna(df["block_mps_kmh"])
    df["historical_train_block_avg_speed"] = [
        train_block.get((str(train), str(block)), df.loc[idx, "block_average_speed"])
        for idx, (train, block) in enumerate(zip(df["train_number"], df["block_id"]))
    ]
    df["priority_ratio"] = (
        df["historical_train_block_avg_speed"] / df["block_average_speed"].replace(0, 1)
    ).clip(lower=0, upper=2)

    print("\n[3/5] Encoding categorical and operational features ...")
    train_types = sorted(df["train_type"].unique().tolist())
    train_type_map = {name: i for i, name in enumerate(train_types)}
    df["train_type_code"] = df["train_type"].map(train_type_map)
    for status in ["clear", "restricted", "blocked"]:
        df[f"block_status_{status}"] = (df["block_status"] == status).astype(int)
    df["speed_headroom"] = (df["block_mps_kmh"] - df["max_speed_kmh"]).clip(lower=0)
    df["close_following"] = (df["preceding_gap_km"] < 15).astype(int)
    df["held_for_crossing"] = (df["preceding_gap_km"] < 8).astype(int)

    print("\n[4/5] Assembling final feature matrix ...")
    final = df[FEATURE_COLUMNS + [TARGET_COLUMN]].copy()
    final.to_csv(OUT_PATH, index=False)
    with open(ENCODING_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "train_type_map": train_type_map,
            "feature_columns": FEATURE_COLUMNS,
            "target_column": TARGET_COLUMN,
            "day_night_definition": "1=daytime 06:00-17:59, 0=nighttime 18:00-05:59",
            "priority_weights": PRIORITY_WEIGHTS,
        }, f, indent=2)

    print(f"       -> {len(FEATURE_COLUMNS)} ML features + target")
    print(f"       -> training features: {OUT_PATH}")
    print(f"       -> encoding map: {ENCODING_PATH}")
    print(f"       -> speed profiles: {PROFILE_PATH}")
    print("\nFeature engineering complete.")


if __name__ == "__main__":
    main()
