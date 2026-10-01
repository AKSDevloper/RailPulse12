"""
inference.py
------------
Loads the trained model bundle and builds the exact live feature vector used
by training, including day/night and historical priority features.
"""

import os
import joblib
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_PATH = os.path.join(DATA_DIR, "model_bundle.pkl")
_bundle = None


def _rebuild_model():
    """Disabled in serverless deployment; model artifacts are bundled at build time."""
    raise RuntimeError("Model artifact is missing; redeploy with the bundled model_bundle.pkl")

def _load():
    global _bundle
    if _bundle is None:
        if not os.path.exists(MODEL_PATH):
            _rebuild_model()
        try:
            _bundle = joblib.load(MODEL_PATH)
        except Exception as exc:
            raise RuntimeError(f"Unable to load bundled ML model: {exc}") from exc
        print(f"[inference] Loaded model bundle: {_bundle['model_name']}  "
              f"(MAE {_bundle['metrics']['mae']:.2f} min, R2 {_bundle['metrics']['r2']:.3f})")
    return _bundle


def predict_section_time(raw_features: dict, verbose: bool = True) -> dict:
    bundle = _load()
    feature_columns = bundle["feature_columns"]
    train_type_map = bundle["train_type_map"]

    from ml.speed_profiles import (
        is_daytime, historical_train_speed, block_average_speed, train_priority_weight,
    )

    hour = int(raw_features["hour_of_day"])
    day_night = is_daytime(hour)
    fallback_speed = max(1.0, min(raw_features["max_speed_kmh"], raw_features["block_mps_kmh"]))
    block_avg = block_average_speed(raw_features["block_id"], day_night, fallback_speed)
    train_hist_speed = historical_train_speed(
        raw_features["train_number"], raw_features["block_id"], block_avg
    )
    priority_ratio = train_hist_speed / max(block_avg, 1.0)

    engineered = {
        "block_length_km": raw_features["block_length_km"],
        "block_mps_kmh": raw_features["block_mps_kmh"],
        "max_speed_kmh": raw_features["max_speed_kmh"],
        "speed_headroom": max(0.0, raw_features["block_mps_kmh"] - raw_features["max_speed_kmh"]),
        "hour_of_day": hour,
        "day_night": day_night,
        "preceding_gap_km": raw_features["preceding_gap_km"],
        "close_following": 1 if raw_features["preceding_gap_km"] < 15 else 0,
        "held_for_crossing": 1 if raw_features["preceding_gap_km"] < 8 else 0,
        "platform_available": raw_features["platform_available"],
        "historical_station_bias_min": raw_features["historical_station_bias_min"],
        "block_status_clear": 1 if raw_features["block_status"] == "clear" else 0,
        "block_status_restricted": 1 if raw_features["block_status"] == "restricted" else 0,
        "block_status_blocked": 1 if raw_features["block_status"] == "blocked" else 0,
        "historical_train_block_avg_speed": train_hist_speed,
        "block_average_speed": block_avg,
        "priority_ratio": priority_ratio,
        "priority_weight": raw_features.get("priority_weight", train_priority_weight(raw_features["train_type"])),
        "train_type_code": train_type_map.get(raw_features["train_type"], 0),
    }

    if verbose:
        print(f"[inference] Live feature vector for {raw_features.get('train_number', '?')} "
              f"on block {raw_features.get('block_id', '?')}:")
        for k in feature_columns:
            print(f"             {k:36s} = {engineered[k]}")

    row = np.array([[engineered[c] for c in feature_columns]])
    predicted = max(0.1, float(bundle["model"].predict(row)[0]))
    p10 = max(0.1, predicted + bundle["p10_offset"])
    p90 = max(p10, predicted + bundle["p90_offset"])
    result = {"predicted_min": round(predicted, 2), "p10_min": round(p10, 2), "p90_min": round(p90, 2)}
    if verbose:
        print(f"[inference] -> predicted {result['predicted_min']} min "
              f"(80% interval {result['p10_min']}-{result['p90_min']} min)\n")
    return result


if __name__ == "__main__":
    demo_features = {
        "train_number": "12417", "block_id": "B2", "train_type": "Mail/Express",
        "block_length_km": 96, "block_mps_kmh": 130, "max_speed_kmh": 110,
        "hour_of_day": 9, "preceding_gap_km": 5.4, "platform_available": 1,
        "historical_station_bias_min": 1.4, "block_status": "clear", "priority_weight": 0.90,
    }
    predict_section_time(demo_features)
