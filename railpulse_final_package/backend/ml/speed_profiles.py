"""Historical speed profiles used by the ML feature pipeline and live inference."""
import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
PROFILE_PATH = os.path.join(DATA_DIR, "speed_profiles.json")

_CACHE = None


def load_profiles():
    global _CACHE
    if _CACHE is None:
        if not os.path.exists(PROFILE_PATH):
            _CACHE = {"block_day_avg_speed": {}, "block_night_avg_speed": {},
                      "block_overall_avg_speed": {}, "train_block_avg_speed": {}}
        else:
            with open(PROFILE_PATH, encoding="utf-8") as f:
                _CACHE = json.load(f)
    return _CACHE


def is_daytime(hour_of_day: int) -> int:
    """1 = daytime (06:00-17:59), 0 = nighttime (18:00-05:59)."""
    return 1 if 6 <= int(hour_of_day) < 18 else 0


def train_priority_weight(train_type: str) -> float:
    """Operational priority multiplier used to keep premium services closer to MPS."""
    return {
        "Rajdhani": 1.00,
        "Superfast": 0.97,
        "Mail/Express": 0.90,
        "Express": 0.88,
        "Passenger": 0.78,
        "Freight": 0.72,
    }.get(train_type, 0.85)


def historical_train_speed(train_number: str, block_id: str, fallback: float) -> float:
    profiles = load_profiles()
    return float(profiles.get("train_block_avg_speed", {}).get(str(train_number), {}).get(block_id, fallback))


def block_average_speed(block_id: str, day_night: int, fallback: float) -> float:
    profiles = load_profiles()
    key = "block_day_avg_speed" if day_night else "block_night_avg_speed"
    value = profiles.get(key, {}).get(block_id)
    if value is None:
        value = profiles.get("block_overall_avg_speed", {}).get(block_id, fallback)
    return float(value)
