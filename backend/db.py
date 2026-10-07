import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('iv_scan_new', NEW.id::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();

CREATE TABLE IF NOT EXISTS snow_windows (
    id serial PRIMARY KEY,
    array_code text NOT NULL,
    cover_min double precision NOT NULL,
    start_time time NOT NULL,
    end_time time NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT snow_windows_time_ck CHECK (start_time <> end_time),
    CONSTRAINT snow_windows_cover_ck CHECK (cover_min >= 0 AND cover_min <= 1)
);

CREATE TABLE IF NOT EXISTS snow_blocks (
    id serial PRIMARY KEY,
    window_id integer REFERENCES snow_windows(id) ON DELETE SET NULL,
    array_code text NOT NULL,
    string_code text NOT NULL,
    voc_v double precision,
    isc_a double precision,
    fill_factor double precision,
    reason text NOT NULL,
    blocked_by text NOT NULL,
    blocked_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snow_windows_array ON snow_windows(array_code);
CREATE INDEX IF NOT EXISTS idx_snow_blocks_at ON snow_blocks(blocked_at DESC);
"""
