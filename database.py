"""
SQLite persistence for seismic events and waveform alerts.

Stores all events, waveform alerts, and optionally per-second STA/LTA logs.
Database file: /data/seismic_monitor.db (Docker volume) or ./seismic_monitor.db
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
import threading
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get('SEISMIC_DB_PATH', '/data/seismic_monitor.db')


class Database:
    """Thread-safe SQLite database for seismic event persistence."""

    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        os.makedirs(os.path.dirname(self.path) if os.path.dirname(self.path) else '.', exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')

        self._conn.executescript('''
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                mag REAL,
                mag_type TEXT,
                lat REAL,
                lon REAL,
                depth REAL,
                event_time REAL,
                place TEXT,
                url TEXT,
                received_at REAL,
                sources TEXT,
                caracas_dist_km REAL,
                caracas_pga_rock REAL,
                caracas_pga_soil REAL,
                caracas_pga_soil84 REAL,
                caracas_mmi REAL,
                caracas_mmi_desc TEXT,
                caracas_swave_s REAL,
                alarm_level TEXT,
                raw_json TEXT,
                created_at REAL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS waveform_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                alert_level TEXT NOT NULL,
                station TEXT NOT NULL,
                cft REAL,
                amplitude REAL,
                est_mag REAL,
                coincidence_stations TEXT,
                warning_stations TEXT,
                alert_time REAL NOT NULL,
                created_at REAL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS station_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station TEXT NOT NULL,
                timestamp REAL NOT NULL,
                cft REAL,
                amplitude REAL,
                sps REAL
            );

            CREATE INDEX IF NOT EXISTS idx_events_time ON events(event_time);
            CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
            CREATE INDEX IF NOT EXISTS idx_alerts_time ON waveform_alerts(alert_time);
            CREATE INDEX IF NOT EXISTS idx_alerts_station ON waveform_alerts(station);
            CREATE INDEX IF NOT EXISTS idx_station_logs_ts ON station_logs(timestamp);
            CREATE INDEX IF NOT EXISTS idx_station_logs_station ON station_logs(station);
        ''')
        self._conn.commit()
        logger.info("Database initialized at %s", self.path)

    def save_event(self, event: dict):
        """Save or update an earthquake event."""
        with self._lock:
            caracas = event.get('caracas', {})
            try:
                self._conn.execute('''
                    INSERT OR REPLACE INTO events
                    (id, source, mag, mag_type, lat, lon, depth, event_time, place, url,
                     received_at, sources, caracas_dist_km, caracas_pga_rock, caracas_pga_soil,
                     caracas_pga_soil84, caracas_mmi, caracas_mmi_desc, caracas_swave_s,
                     alarm_level, raw_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (
                    event.get('id', ''),
                    event.get('source', ''),
                    event.get('mag'),
                    event.get('mag_type'),
                    event.get('lat'),
                    event.get('lon'),
                    event.get('depth'),
                    event.get('time'),
                    event.get('place', ''),
                    event.get('url', ''),
                    event.get('received_at'),
                    json.dumps(event.get('sources', [])),
                    caracas.get('dist_km'),
                    caracas.get('pga_rock'),
                    caracas.get('pga_soil'),
                    caracas.get('pga_soil84'),
                    caracas.get('mmi'),
                    caracas.get('mmi_desc'),
                    caracas.get('swave_s'),
                    caracas.get('alarm_level', ''),
                    json.dumps(event, default=str),
                ))
                self._conn.commit()
            except Exception as e:
                logger.error("DB save_event error: %s", e)

    def save_waveform_alert(self, event: dict):
        """Save a waveform alert (STA/LTA trigger)."""
        with self._lock:
            try:
                self._conn.execute('''
                    INSERT INTO waveform_alerts
                    (event_id, alert_level, station, cft, amplitude, est_mag,
                     coincidence_stations, warning_stations, alert_time)
                    VALUES (?,?,?,?,?,?,?,?,?)
                ''', (
                    event.get('id', ''),
                    event.get('alert_level', ''),
                    event.get('trigger_station', ''),
                    event.get('cft'),
                    event.get('amplitude'),
                    event.get('mag'),
                    json.dumps(event.get('coincidence_stations', [])),
                    json.dumps(event.get('warning_stations', [])),
                    event.get('time', time.time()),
                ))
                self._conn.commit()
            except Exception as e:
                logger.error("DB save_waveform_alert error: %s", e)

    def save_station_log(self, station: str, timestamp: float,
                         cft: float, amplitude: float = 0, sps: float = 0):
        """Save per-second station STA/LTA log."""
        with self._lock:
            try:
                self._conn.execute('''
                    INSERT INTO station_logs (station, timestamp, cft, amplitude, sps)
                    VALUES (?,?,?,?,?)
                ''', (station, timestamp, cft, amplitude, sps))
                # Batch commit every ~10 inserts (WAL mode handles this efficiently)
                self._conn.commit()
            except Exception as e:
                logger.error("DB save_station_log error: %s", e)

    def get_events(self, hours: int = 24, limit: int = 500) -> list:
        """Get recent events from database."""
        with self._lock:
            cutoff = time.time() - hours * 3600
            cursor = self._conn.execute('''
                SELECT raw_json FROM events
                WHERE event_time > ? OR received_at > ?
                ORDER BY event_time DESC
                LIMIT ?
            ''', (cutoff, cutoff, limit))
            results = []
            for row in cursor:
                try:
                    results.append(json.loads(row['raw_json']))
                except Exception:
                    pass
            return results

    def get_waveform_alerts(self, hours: int = 24, limit: int = 200) -> list:
        """Get recent waveform alerts."""
        with self._lock:
            cutoff = time.time() - hours * 3600
            cursor = self._conn.execute('''
                SELECT * FROM waveform_alerts
                WHERE alert_time > ?
                ORDER BY alert_time DESC
                LIMIT ?
            ''', (cutoff, limit))
            return [dict(row) for row in cursor]

    def get_station_logs(self, station: str, seconds: int = 300) -> list:
        """Get per-second station logs for charting."""
        with self._lock:
            cutoff = time.time() - seconds
            cursor = self._conn.execute('''
                SELECT timestamp, cft, amplitude FROM station_logs
                WHERE station = ? AND timestamp > ?
                ORDER BY timestamp ASC
            ''', (station, cutoff))
            return [dict(row) for row in cursor]

    def prune_old_data(self, days: int = 7):
        """Remove data older than N days."""
        with self._lock:
            cutoff = time.time() - days * 86400
            self._conn.execute('DELETE FROM station_logs WHERE timestamp < ?', (cutoff,))
            self._conn.execute('DELETE FROM waveform_alerts WHERE alert_time < ?', (cutoff,))
            self._conn.commit()
            logger.info("Pruned data older than %d days", days)

    def close(self):
        if self._conn:
            self._conn.close()
