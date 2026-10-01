"""
track_graph.py
----------------
Phase 1 (Data Simulation & Schema): the track graph for the Delhi -> Kanpur
corridor, modelled as nodes (stations/junctions) and edges (track blocks).

This is the single source of truth every other script imports from --
the simulator, the ML pipeline, and the API all read STATIONS / BLOCKS
from here so the whole project stays consistent.
"""

from dataclasses import dataclass, field
from typing import List, Optional

# Real approximate coordinates for the two terminals. Intermediate stations
# are linearly interpolated by distance-along-route -- good enough to plot
# a live-looking map without needing a full GIS shapefile for a prototype.
NDLS_LATLON = (28.6139, 77.2090)   # New Delhi
CNB_LATLON = (26.4499, 80.3319)    # Kanpur Central


@dataclass
class Station:
    id: str
    name: str
    km: float                 # distance from New Delhi, along the route
    historical_bias_min: float  # avg extra minutes trains lose approaching/leaving this station historically


@dataclass
class Block:
    id: str
    from_id: str
    to_id: str
    from_name: str
    to_name: str
    from_km: float
    to_km: float
    length_km: float
    mps_kmh: float             # Maximum Permissible Speed for this block


STATIONS: List[Station] = [
    Station("NDLS", "New Delhi", 0, 3.2),
    Station("GZB", "Ghaziabad Jn", 19, 2.1),
    Station("ALJN", "Aligarh Jn", 128, 1.0),
    Station("TDL", "Tundla Jn", 224, 1.4),
    Station("ETW", "Etawah", 297, 0.6),
    Station("CNB", "Kanpur Central", 440, 2.6),
]

_BLOCK_MPS = [110, 130, 130, 120, 110]

BLOCKS: List[Block] = []
for i in range(len(STATIONS) - 1):
    a, b = STATIONS[i], STATIONS[i + 1]
    BLOCKS.append(Block(
        id=f"B{i}",
        from_id=a.id, to_id=b.id,
        from_name=a.name, to_name=b.name,
        from_km=a.km, to_km=b.km,
        length_km=b.km - a.km,
        mps_kmh=_BLOCK_MPS[i],
    ))

ROUTE_KM = STATIONS[-1].km
SIM_START_CLOCK_MIN = 6 * 60  # 06:00 -- single source of truth for the simulated clock's origin


@dataclass
class TrainDef:
    id: str
    number: str
    name: str
    type: str
    max_speed_kmh: float
    priority_weight: float
    depart_min: float   # minutes after simulation start (06:00 baseline)


TRAIN_DEFS: List[TrainDef] = [
    TrainDef("T1", "12417", "Prayagraj Express", "Mail/Express", 110, 0.90, 0),
    TrainDef("T2", "12002", "Shatabdi Express", "Superfast", 130, 0.97, 22),
    TrainDef("T3", "12554", "Vaishali Express", "Express", 110, 0.88, 46),
    TrainDef("T4", "12303", "Poorva Express", "Mail/Express", 105, 0.90, 70),
    TrainDef("T5", "12034", "Kanpur Shatabdi", "Superfast", 130, 0.97, 96),
    TrainDef("T6", "14217", "Kashi Express", "Express", 110, 0.88, 122),
    TrainDef("T7", "12444", "Dibrugarh Rajdhani", "Rajdhani", 130, 1.00, 146),
    TrainDef("T8", "11015", "Kushinagar Express", "Express", 105, 0.88, 172),
    TrainDef("T9", "19040", "Firozpur Janata Exp", "Passenger", 90, 0.78, 198),
]

FAULT_TYPES = {
    "signal_failure": {"label": "Signal Failure", "effect": "blocked", "duration_min": 10, "speed_factor": 0.06},
    "tsr": {"label": "Speed Restriction (TSR)", "effect": "restricted", "duration_min": 18, "speed_factor": 0.42},
    "maintenance": {"label": "Track Maintenance Block", "effect": "restricted", "duration_min": 26, "speed_factor": 0.30},
}


def station_by_id(station_id: str) -> Optional[Station]:
    return next((s for s in STATIONS if s.id == station_id), None)


def block_index_for_km(km: float) -> int:
    """Which block a given distance-along-route falls into."""
    if km >= ROUTE_KM:
        return len(BLOCKS) - 1
    for i, b in enumerate(BLOCKS):
        if b.from_km <= km < b.to_km:
            return i
    return len(BLOCKS) - 1


def block_by_id(block_id: str) -> Optional[Block]:
    return next((b for b in BLOCKS if b.id == block_id), None)


def latlon_for_km(km: float):
    """Linear interpolation between the two terminal coordinates by
    fraction of route covered. A simplification vs. real track geometry,
    fine for a schematic live map."""
    frac = max(0.0, min(1.0, km / ROUTE_KM))
    lat = NDLS_LATLON[0] + (CNB_LATLON[0] - NDLS_LATLON[0]) * frac
    lon = NDLS_LATLON[1] + (CNB_LATLON[1] - NDLS_LATLON[1]) * frac
    return round(lat, 5), round(lon, 5)


if __name__ == "__main__":
    print(f"Route: {STATIONS[0].name} -> {STATIONS[-1].name}  ({ROUTE_KM} km)")
    print(f"{len(STATIONS)} stations, {len(BLOCKS)} blocks, {len(TRAIN_DEFS)} scheduled trains\n")
    for b in BLOCKS:
        print(f"  {b.id}: {b.from_name:15s} -> {b.to_name:15s}  {b.length_km:5.0f} km   MPS {b.mps_kmh} km/h")
