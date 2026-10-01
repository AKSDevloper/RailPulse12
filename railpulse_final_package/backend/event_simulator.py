"""
event_simulator.py
---------------------
Phase 1: the Event Simulator. This is the single physics engine used by
EVERYTHING else in the project:

  - Run directly, it prints live telemetry to the console every tick,
    exactly like a real GPS/telemetry feed would arrive (mock GPS
    coordinates, current speed, track events) -- this is "how the data
    is taken" at the source.
  - ml/generate_historical_dataset.py drives this same engine thousands
    of times, headless, to build the historical dataset used to train
    the model.
  - backend/api/main.py drives this engine live, in real time, and
    streams its output over the WebSocket.

Run directly:  python event_simulator.py
"""

import json
import random
import time
import argparse
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from track_graph import (
    STATIONS, BLOCKS, TRAIN_DEFS, ROUTE_KM, FAULT_TYPES, SIM_START_CLOCK_MIN,
    block_index_for_km, latlon_for_km,
)

TICK_MIN = 3.0
MIN_SAFE_HEADWAY_KM = 10.0  # hard minimum spacing: trains must never visually/physically touch  # simulated minutes advanced per tick (a "tick" ~= one 30s-90s telemetry ping)

# Automatic, non-injected operational events -- these fire on their own as
# the simulation runs (unlike faults, which a controller injects). Each is
# (probability per tick, speed multiplier, label). Multiple can stack in the
# same tick; the one with the lowest multiplier (most severe) is reported as
# the train's headline "event" for that tick.
FOG_HOURS = {5, 6, 7}
FOG_PROBABILITY = 0.025
FOG_FACTOR = 0.75
TECHNICAL_HALT_PROBABILITY = 0.0015
TECHNICAL_HALT_FACTOR = 0.15
PLATFORM_WAIT_PROBABILITY = 0.008
PLATFORM_WAIT_ZONE_KM = 15.0   # within this distance of the terminus
PLATFORM_WAIT_FACTOR = 0.65

# Small, deterministic demo delays on only a few services. These represent
# pre-existing operational lateness at departure; they are not applied to every train.
CUSTOM_START_DELAYS = {
    "T3": (4.0, "Late departure"),
    "T6": (7.0, "Late departure"),
    "T9": (10.0, "Late departure"),
}


@dataclass
class LiveTrain:
    id: str
    number: str
    name: str
    type: str
    max_speed_kmh: float
    depart_min: float
    pos_km: float = 0.0
    status: str = "scheduled"  # scheduled | running | completed
    platform: int = field(default=0)
    delay_min: float = 0.0
    speed_kmh: float = 0.0
    delay_reason: str = "On time"
    safety_gap_km: float = 999.0
    priority_weight: float = 0.85
    completed_min: float = 0.0

    def __post_init__(self):
        h = 0
        for c in self.id:
            h = (h * 31 + ord(c)) % 97
        self.platform = (h % 6) + 1


@dataclass
class ActiveFault:
    id: str
    block_id: str
    fault_type: str
    label: str
    effect: str
    speed_factor: float
    start_min: float
    end_min: float


class TrainSimulator:
    """One instance = one running corridor simulation. Call `.tick()`
    repeatedly to advance simulated time and get the fresh telemetry
    for that tick."""

    def _random_departures(self):
        # Each service day gets a fresh, realistic dispatch pattern.  Keep the
        # published train order but jitter departures and enforce a minimum
        # dispatch gap so the live map does not spawn trains on top of each other.
        cursor = None
        result = {}
        for t in TRAIN_DEFS:
            proposed = max(0.0, t.depart_min + random.uniform(-7.0, 7.0))
            if cursor is not None:
                proposed = max(cursor + 14.0, proposed)
            result[t.id] = round(proposed, 1)
            cursor = result[t.id]
        return result

    def __init__(self, seed: Optional[int] = None):
        if seed is not None:
            random.seed(seed)
        self.sim_min = 0.0
        self.cycle = 1
        self._runtime_departures = self._random_departures()
        self.trains: List[LiveTrain] = [
            LiveTrain(
                t.id, t.number, t.name, t.type, t.max_speed_kmh, self._runtime_departures[t.id],
                delay_min=CUSTOM_START_DELAYS.get(t.id, (0.0, "On time"))[0],
                delay_reason=CUSTOM_START_DELAYS.get(t.id, (0.0, "On time"))[1],
                priority_weight=t.priority_weight,
            )
            for t in TRAIN_DEFS
        ]
        self.faults: List[ActiveFault] = []
        self._fault_seq = 0

    # ---------------------------------------------------------------- faults
    def inject_fault(self, block_id: str, fault_type: str) -> ActiveFault:
        spec = FAULT_TYPES[fault_type]
        self._fault_seq += 1
        f = ActiveFault(
            id=f"F{self._fault_seq}",
            block_id=block_id,
            fault_type=fault_type,
            label=spec["label"],
            effect=spec["effect"],
            speed_factor=spec["speed_factor"],
            start_min=self.sim_min,
            end_min=self.sim_min + spec["duration_min"],
        )
        self.faults.append(f)
        return f

    def clear_fault(self, fault_id: str):
        for f in self.faults:
            if f.id == fault_id:
                f.end_min = self.sim_min

    def _block_status(self, block_id: str):
        active = [f for f in self.faults if f.block_id == block_id and self.sim_min < f.end_min]
        if any(f.effect == "blocked" for f in active):
            return "blocked", next(f for f in active if f.effect == "blocked").speed_factor
        restricted = [f for f in active if f.effect == "restricted"]
        if restricted:
            return "restricted", restricted[0].speed_factor
        return "clear", 1.0

    def block_status(self, block_id: str):
        """Public accessor -- (status_label, speed_factor) for a block right now."""
        return self._block_status(block_id)

    def nearest_preceding_gap(self, train_id: str) -> float:
        """Distance (km) to the nearest running train ahead of this one. 999 if clear."""
        me = next((t for t in self.trains if t.id == train_id), None)
        if me is None:
            return 999.0
        gaps = [t.pos_km - me.pos_km for t in self.trains
                if t.id != train_id and t.status == "running" and t.pos_km > me.pos_km]
        return min(gaps) if gaps else 999.0

    def force_new_cycle(self):
        """Manually start a fresh service day right now, instead of waiting
        for every train to reach Kanpur on its own."""
        self._reset_cycle()

    def hour_of_day(self) -> int:
        return int((SIM_START_CLOCK_MIN + self.sim_min) // 60) % 24

    # ------------------------------------------------------------------ tick
    def tick(self) -> List[Dict]:
        """Advance the world by TICK_MIN and return this tick's telemetry
        events (one dict per running train) -- this is the raw data a real
        GPS/telemetry ingestion pipeline would receive."""
        self.sim_min += TICK_MIN
        self.faults = [f for f in self.faults if self.sim_min < f.end_min + 40]
        hour = self.hour_of_day()

        events = []
        # Snapshot positions at the start of the tick.  Safety decisions are
        # made from this snapshot so a train cannot "move away" from a
        # following train during the same tick and incorrectly remove its
        # restriction.
        # Activate every train that is due before taking the movement snapshot.
        # This keeps the ordering complete and prevents a newly-started train
        # from being ignored by a train that was already moving.
        for t in self.trains:
            if t.status == "scheduled" and self.sim_min >= t.depart_min:
                t.status = "running"

        running_positions = {t.id: t.pos_km for t in self.trains if t.status == "running"}
        running_sorted = sorted(
            [t for t in self.trains if t.status == "running"],
            key=lambda x: x.pos_km
        )

        # First calculate each train's desired movement from the same snapshot.
        # We then apply a second safety pass to the proposed positions. This is
        # important: a following train must never be allowed to "jump" into
        # the leader's space just because both trains moved during this tick.
        proposals = {}
        for t in self.trains:
            if t.status == "completed" or self.sim_min < t.depart_min:
                continue
            block_idx = block_index_for_km(t.pos_km)
            block = BLOCKS[block_idx]
            status, status_factor = self._block_status(block.id)

            ahead = [x for x in running_sorted if x.id != t.id and x.pos_km > t.pos_km]
            leader = min(ahead, key=lambda x: x.pos_km, default=None)
            gap_km = (leader.pos_km - t.pos_km) if leader else 999.0

            causes = []
            if status == "blocked":
                causes.append((0.08, "signal_failure", f"{block.id} blocked — signal hold"))
            elif status == "restricted":
                causes.append((status_factor, "speed_restriction", f"{block.id} speed restriction"))

            if block_idx + 1 < len(BLOCKS):
                next_block = BLOCKS[block_idx + 1]
                next_status, next_factor = self._block_status(next_block.id)
                dist_to_next = next_block.from_km - t.pos_km
                if next_status == "blocked" and 0 <= dist_to_next <= 12:
                    causes.append((0.10, "signal_failure", f"Approaching blocked {next_block.id}"))
                elif next_status == "restricted" and 0 <= dist_to_next <= 12:
                    causes.append((max(0.35, next_factor), "speed_restriction", f"Approaching restricted {next_block.id}"))

            # Soft headway control starts before the hard boundary. The hard
            # boundary below is the final authority and prevents contact.
            safe_gap = max(MIN_SAFE_HEADWAY_KM, min(14.0, t.max_speed_kmh * 0.10))
            t.safety_gap_km = gap_km
            if leader is not None:
                closing_speed = max(0.0, t.max_speed_kmh - max(20.0, leader.speed_kmh or leader.max_speed_kmh))
                dynamic_safe = max(safe_gap, closing_speed * 0.03)
                if gap_km < dynamic_safe:
                    ratio = max(0.08, gap_km / max(dynamic_safe, 0.1))
                    causes.append((ratio, "safe_headway", f"Safety spacing — {gap_km:.1f} km behind {leader.number}"))
                elif gap_km < dynamic_safe * 1.55:
                    ratio = 0.55 + 0.45 * (gap_km / (dynamic_safe * 1.55))
                    causes.append((ratio, "close_following", f"Traffic spacing — {gap_km:.1f} km behind {leader.number}"))

            if hour in FOG_HOURS and random.random() < FOG_PROBABILITY:
                causes.append((FOG_FACTOR, "fog", "Reduced visibility"))
            if random.random() < TECHNICAL_HALT_PROBABILITY:
                causes.append((TECHNICAL_HALT_FACTOR, "technical_halt", "Technical inspection"))
            if t.type == "Passenger" and (ROUTE_KM - t.pos_km) < PLATFORM_WAIT_ZONE_KM and random.random() < PLATFORM_WAIT_PROBABILITY:
                causes.append((PLATFORM_WAIT_FACTOR, "platform_wait", "Platform occupied"))
            if random.random() < 0.012:
                causes.append((0.7, "yellow_signal", "Caution signal"))

            random_factor = 0.96 + random.random() * 0.08
            combined_factor = random_factor
            for factor, _, _ in causes:
                combined_factor *= factor

            labelled = [c for c in causes if c[1] is not None]
            event_type = min(labelled, key=lambda c: c[0])[1] if labelled else "none"
            reason = min(labelled, key=lambda c: c[0])[2] if labelled else "On time"

            target_speed = min(t.max_speed_kmh, block.mps_kmh)
            priority_target_speed = target_speed * t.priority_weight
            desired_speed = max(0.0 if status == "blocked" else 4.0, priority_target_speed * combined_factor)
            desired_pos = min(ROUTE_KM, t.pos_km + desired_speed * TICK_MIN / 60)

            # If a newly departed train would start inside the hard safety
            # envelope, keep it at its current position until the leader has
            # opened enough distance. This prevents an initial overlap that
            # cannot be repaired by a forward-only movement engine.
            if leader is not None and gap_km < MIN_SAFE_HEADWAY_KM:
                desired_pos = t.pos_km
                desired_speed = 0.0
                if "safe_headway" not in [c[1] for c in labelled]:
                    labelled.append((0.01, "safe_headway",
                                     f"Safety spacing — holding behind {leader.number}"))
                event_type = "safe_headway"
                reason = f"Safety spacing — holding behind {leader.number}"

            proposals[t.id] = {
                "train": t,
                "block": block,
                "status": status,
                "gap_km": gap_km,
                "safe_gap": safe_gap,
                "leader": leader,
                "desired_speed": desired_speed,
                "desired_pos": desired_pos,
                "priority_target_speed": priority_target_speed,
                "event_type": event_type,
                "reason": reason,
                "labelled": labelled,
            }

        # Hard safety pass. Work from the FRONT of the route backwards using
        # the position order from the start of the tick. A following train can
        # never move ahead of its leader, and it must retain at least 5 km.
        ordered = sorted(
            proposals.values(),
            key=lambda p: p["train"].pos_km,
            reverse=True
        )
        safe_positions = {}
        for p in ordered:
            t = p["train"]
            leader = p["leader"]
            final_pos = max(t.pos_km, p["desired_pos"])

            if leader is not None and leader.id in safe_positions:
                leader_pos = safe_positions[leader.id]
                maximum_pos = leader_pos - MIN_SAFE_HEADWAY_KM
                if final_pos > maximum_pos:
                    final_pos = max(t.pos_km, maximum_pos)
                    p["event_type"] = "safe_headway"
                    p["reason"] = (
                        f"Safety spacing — held {MIN_SAFE_HEADWAY_KM:.1f} km "
                        f"behind {leader.number}"
                    )
                    if "safe_headway" not in [c[1] for c in p["labelled"]]:
                        p["labelled"].append((0.01, "safe_headway", p["reason"]))

            safe_positions[t.id] = final_pos

        for p in proposals.values():
            t = p["train"]
            old_pos = t.pos_km
            final_pos = safe_positions[t.id]
            actual_speed = max(0.0, (final_pos - old_pos) * 60 / TICK_MIN)
            target_speed = p["priority_target_speed"]
            # Headway control is a safety action, not automatically a service
            # delay. Only selected operational disruptions (or an explicit
            # fault) accumulate passenger-visible delay. This keeps the live
            # board realistic: a few trains can be delayed while others are
            # simply regulated for safe spacing.
            delay_causes = {"signal_failure", "speed_restriction", "fog", "technical_halt", "platform_wait", "yellow_signal"}
            if p["event_type"] in delay_causes and actual_speed < target_speed * 0.90:
                t.delay_min += TICK_MIN * max(0.0, 1.0 - actual_speed / max(target_speed, 0.1))
            t.speed_kmh = actual_speed
            t.delay_reason = p["reason"]
            t.pos_km = final_pos
            t.safety_gap_km = (safe_positions[p["leader"].id] - final_pos) if p["leader"] is not None and p["leader"].id in safe_positions else 999.0
            if t.pos_km >= ROUTE_KM:
                t.status = "completed"
                t.completed_min = self.sim_min

            lat, lon = latlon_for_km(t.pos_km)
            events.append({
                "sim_min": round(self.sim_min, 2),
                "train_id": t.id,
                "train_number": t.number,
                "gps_lat": lat,
                "gps_lon": lon,
                "position_km": round(t.pos_km, 2),
                "speed_kmh": round(actual_speed, 1),
                "block_id": p["block"].id,
                "block_status": p["status"],
                "preceding_gap_km": None if t.safety_gap_km >= 999 else round(t.safety_gap_km, 1),
                "safe_gap_km": round(MIN_SAFE_HEADWAY_KM, 1),
                "delay_min": round(t.delay_min, 1),
                "delay_reason": t.delay_reason,
                "event": p["event_type"],
            })

        all_done = all(t.status == "completed" for t in self.trains)
        if all_done and self.sim_min > max(t.depart_min for t in self.trains) + 30:
            self._reset_cycle()

        return events

    def _reset_cycle(self):
        self.sim_min = 0.0
        self.cycle += 1
        self._runtime_departures = self._random_departures()
        self.trains = [
            LiveTrain(
                t.id, t.number, t.name, t.type, t.max_speed_kmh, self._runtime_departures[t.id],
                delay_min=CUSTOM_START_DELAYS.get(t.id, (0.0, "On time"))[0],
                delay_reason=CUSTOM_START_DELAYS.get(t.id, (0.0, "On time"))[1],
                priority_weight=t.priority_weight,
            )
            for t in TRAIN_DEFS
        ]
        self.faults = []


def main():
    parser = argparse.ArgumentParser(description="Run the event simulator standalone and print live telemetry.")
    parser.add_argument("--ticks", type=int, default=40, help="how many ticks to simulate")
    parser.add_argument("--interval", type=float, default=0.3, help="seconds to sleep between ticks (0 = as fast as possible)")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    sim = TrainSimulator(seed=args.seed)
    print(f"Starting event simulator: {len(TRAIN_DEFS)} trains, {len(BLOCKS)} blocks, tick = {TICK_MIN} sim-min\n")

    for i in range(args.ticks):
        events = sim.tick()
        if events:
            print(f"--- tick {i+1}  (sim clock +{sim.sim_min:.0f} min) ---")
            for e in events:
                print("  " + json.dumps(e))
        if args.interval:
            time.sleep(args.interval)

    print(f"\nDone. {args.ticks} ticks simulated, cycle counter now at {sim.cycle}.")


if __name__ == "__main__":
    main()
