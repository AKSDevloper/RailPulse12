"""
timetable_generator.py
------------------------
Phase 1: builds the baseline scheduled timetable for every train across
every station on the route, and writes it to data/timetable.csv.

Run directly:  python timetable_generator.py
"""

import os
import csv
from track_graph import STATIONS, TRAIN_DEFS, ROUTE_KM, SIM_START_CLOCK_MIN

NOMINAL_AVG_SPEED_KMH = 112    # used only to build a *baseline* schedule -- close to the
                                # route's length-weighted average MPS (~121 km/h), leaving a
                                # realistic ~10% recovery margin rather than a huge one
DWELL_MIN = 2                 # scheduled halt at intermediate stations

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def fmt_clock(total_min: float) -> str:
    m = int(round(total_min)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def build_timetable():
    rows = []
    for t in TRAIN_DEFS:
        clock = SIM_START_CLOCK_MIN + t.depart_min
        for i, s in enumerate(STATIONS):
            if i == 0:
                arr = None
                dep = clock
            else:
                travel_min = (s.km - STATIONS[i - 1].km) / NOMINAL_AVG_SPEED_KMH * 60
                clock += travel_min
                arr = clock
                is_last = i == len(STATIONS) - 1
                dep = None if is_last else clock + DWELL_MIN
                if not is_last:
                    clock += DWELL_MIN

            rows.append({
                "train_id": t.id,
                "train_number": t.number,
                "train_name": t.name,
                "train_type": t.type,
                "sequence": i,
                "station_id": s.id,
                "station_name": s.name,
                "distance_km": s.km,
                "scheduled_arrival": "--" if arr is None else fmt_clock(arr),
                "scheduled_departure": "--" if dep is None else fmt_clock(dep),
            })
    return rows


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    rows = build_timetable()
    out_path = os.path.join(DATA_DIR, "timetable.csv")
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} timetable rows for {len(TRAIN_DEFS)} trains -> {out_path}")
    print("\nSample (first train):")
    for r in rows[: len(STATIONS)]:
        print(f"  {r['station_name']:16s} arr {r['scheduled_arrival']:>5s}  dep {r['scheduled_departure']:>5s}")


if __name__ == "__main__":
    main()
