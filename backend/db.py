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

-- 积雪覆盖窗：针对阵列（string_code 前缀），覆盖下限起按前缀匹配，
-- start_hour/end_hour 为后台数据库所在时区的钟点；end_hour < start_hour 表示跨零点。
CREATE TABLE IF NOT EXISTS snow_windows (
    id serial PRIMARY KEY,
    array_code text NOT NULL,
    coverage_min double precision NOT NULL,
    start_hour integer NOT NULL CHECK (start_hour BETWEEN 0 AND 23),
    end_hour integer NOT NULL CHECK (end_hour BETWEEN 0 AND 23),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    deleted_at timestamptz
);

-- 封锁痕迹：每次因积雪窗拒收一张扫描单时同批写入。
CREATE TABLE IF NOT EXISTS snow_blocks (
    id serial PRIMARY KEY,
    window_id integer NOT NULL REFERENCES snow_windows(id),
    scan_id integer NOT NULL REFERENCES iv_scans(id),
    string_code text NOT NULL,
    blocked_by text NOT NULL,
    blocked_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snow_blocks_window ON snow_blocks(window_id);
CREATE INDEX IF NOT EXISTS idx_snow_blocks_scan ON snow_blocks(scan_id);

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
"""
