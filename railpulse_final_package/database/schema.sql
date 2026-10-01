-- ============================================================================
-- schema.sql
-- Phase 1 (Data Simulation & Schema) reference schema for PRODUCTION use:
-- PostgreSQL + PostGIS, matching the SIH26028 proposal's data architecture.
--
-- NOTE: the runnable prototype in this repo does NOT require Postgres -- it
-- keeps live state in memory (backend/event_simulator.py) and writes the
-- generated datasets to CSV (backend/data/) so the whole thing runs with
-- nothing but `pip install` and `python`. This file is the schema you'd
-- migrate to for a real deployment with persistent, queryable history and
-- proper spatial indexing. Column names match the CSV/JSON fields used
-- throughout the Python code, so porting is a straight mapping.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------- stations
CREATE TABLE stations (
    id                      TEXT PRIMARY KEY,          -- e.g. 'NDLS'
    name                    TEXT NOT NULL,
    distance_km             NUMERIC NOT NULL,          -- along-route distance from origin
    historical_bias_min     NUMERIC NOT NULL DEFAULT 0,
    geom                    GEOGRAPHY(POINT, 4326)     -- real lat/lon, once available
);

-- ------------------------------------------------------------------ blocks
CREATE TABLE blocks (
    id                      TEXT PRIMARY KEY,          -- e.g. 'B0'
    from_station_id         TEXT REFERENCES stations(id),
    to_station_id           TEXT REFERENCES stations(id),
    length_km               NUMERIC NOT NULL,
    max_permissible_speed   NUMERIC NOT NULL,          -- MPS, km/h
    geom                    GEOGRAPHY(LINESTRING, 4326) -- real track centerline, once available
);

-- ------------------------------------------------------------------- trains
CREATE TABLE trains (
    id                      TEXT PRIMARY KEY,          -- e.g. 'T1'
    number                  TEXT UNIQUE NOT NULL,      -- e.g. '12417'
    name                    TEXT NOT NULL,
    type                    TEXT NOT NULL,             -- Rajdhani / Superfast / Express / ...
    max_speed_kmh           NUMERIC NOT NULL
);

-- ---------------------------------------------------------------- timetable
CREATE TABLE timetable (
    id                      BIGSERIAL PRIMARY KEY,
    train_id                TEXT REFERENCES trains(id),
    station_id              TEXT REFERENCES stations(id),
    sequence                INT NOT NULL,
    scheduled_arrival       TIME,
    scheduled_departure     TIME,
    UNIQUE (train_id, sequence)
);

-- ------------------------------------------------------------ live_positions
-- Append-only telemetry log -- one row per GPS ping (the output of
-- event_simulator.py in production would land here instead of stdout).
CREATE TABLE live_positions (
    id                      BIGSERIAL PRIMARY KEY,
    train_id                TEXT REFERENCES trains(id),
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    block_id                TEXT REFERENCES blocks(id),
    position_km             NUMERIC NOT NULL,
    speed_kmh               NUMERIC NOT NULL,
    geom                    GEOGRAPHY(POINT, 4326),
    event                   TEXT,                       -- 'none' | 'yellow_signal' | 'speed_restriction' | 'signal_failure'
    delay_min               NUMERIC
);
CREATE INDEX idx_live_positions_train_time ON live_positions (train_id, recorded_at DESC);
CREATE INDEX idx_live_positions_geom ON live_positions USING GIST (geom);

-- ------------------------------------------------------------ block_occupancy
-- Enforces "only one train logically occupies a block block at a time" for
-- realistic congestion modelling.
CREATE TABLE block_occupancy (
    id                      BIGSERIAL PRIMARY KEY,
    block_id                TEXT REFERENCES blocks(id),
    train_id                TEXT REFERENCES trains(id),
    entered_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    cleared_at              TIMESTAMPTZ
);
CREATE INDEX idx_block_occupancy_open ON block_occupancy (block_id) WHERE cleared_at IS NULL;

-- -------------------------------------------------------------------- faults
CREATE TABLE faults (
    id                      BIGSERIAL PRIMARY KEY,
    block_id                TEXT REFERENCES blocks(id),
    fault_type              TEXT NOT NULL,              -- 'signal_failure' | 'tsr' | 'maintenance'
    effect                  TEXT NOT NULL,               -- 'blocked' | 'restricted'
    started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    ends_at                 TIMESTAMPTZ NOT NULL,
    injected_by              TEXT DEFAULT 'controller'    -- who/what triggered it
);
CREATE INDEX idx_faults_active ON faults (block_id, ends_at);

-- ------------------------------------------------------- historical_telemetry
-- What ml/generate_historical_dataset.py + ml/feature_engineering.py produce,
-- for a production setup where training data comes from real, accumulated
-- live_positions history instead of a headless simulator run.
CREATE TABLE historical_block_records (
    id                          BIGSERIAL PRIMARY KEY,
    trip_id                     BIGINT NOT NULL,
    train_type                  TEXT NOT NULL,
    max_speed_kmh                NUMERIC NOT NULL,
    block_id                    TEXT REFERENCES blocks(id),
    hour_of_day                 INT NOT NULL,
    preceding_gap_km            NUMERIC,
    platform_available          BOOLEAN,
    historical_station_bias_min NUMERIC,
    block_status                TEXT,                    -- 'clear' | 'restricted' | 'blocked'
    actual_time_min             NUMERIC NOT NULL          -- training target
);

-- ------------------------------------------------------------- model_registry
-- Tracks trained model artifacts (train_model.py would insert a row here
-- alongside writing model_bundle.pkl, e.g. to S3/blob storage).
CREATE TABLE model_registry (
    id                      BIGSERIAL PRIMARY KEY,
    trained_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    model_name               TEXT NOT NULL,
    training_samples         INT,
    mae_min                   NUMERIC,
    rmse_min                  NUMERIC,
    r2                        NUMERIC,
    artifact_path             TEXT,                       -- e.g. s3://.../model_bundle.pkl
    is_active                 BOOLEAN DEFAULT false
);

-- ------------------------------------------------------- historical speed profiles
-- Derived from historical_block_records / simulated telemetry. These tables
-- keep daytime and nighttime block speeds separate and retain train-specific
-- block speeds for priority-ratio calculations.
CREATE TABLE block_speed_profiles (
    block_id                    TEXT PRIMARY KEY REFERENCES blocks(id),
    daytime_avg_speed_kmh       NUMERIC NOT NULL,
    nighttime_avg_speed_kmh     NUMERIC NOT NULL,
    overall_avg_speed_kmh       NUMERIC NOT NULL,
    sample_count                INT NOT NULL DEFAULT 0,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE train_block_speed_profiles (
    train_number                TEXT NOT NULL,
    block_id                    TEXT NOT NULL REFERENCES blocks(id),
    historical_avg_speed_kmh    NUMERIC NOT NULL,
    sample_count                INT NOT NULL DEFAULT 0,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (train_number, block_id)
);

-- ML training records also retain the historical speed features.
ALTER TABLE historical_block_records
    ADD COLUMN IF NOT EXISTS train_number TEXT,
    ADD COLUMN IF NOT EXISTS day_night INT,
    ADD COLUMN IF NOT EXISTS historical_train_block_avg_speed NUMERIC,
    ADD COLUMN IF NOT EXISTS block_average_speed NUMERIC,
    ADD COLUMN IF NOT EXISTS priority_ratio NUMERIC,
    ADD COLUMN IF NOT EXISTS priority_weight NUMERIC;
