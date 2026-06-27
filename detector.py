"""
STA/LTA P-wave Detector with Graded Alert Levels.

Uses recursive STA/LTA algorithm from ObsPy to detect P-wave arrivals.
Emits graded alerts based on CFT thresholds:

  CFT >= 2.0  → ELEVATED   (yellow)  — unusual activity
  CFT >= 3.0  → WARNING    (orange)  — probable P-wave
  CFT >= 5.0  → CRITICAL   (red)     — strong P-wave, likely significant earthquake

Multi-station coincidence triggers full event declaration.
Each alert is pushed immediately to the event queue.
"""

import logging
import time
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Detection parameters
STA_SECONDS = 1.0
LTA_SECONDS = 30.0
BANDPASS_LOW = 0.5   # Hz
BANDPASS_HIGH = 20.0  # Hz

# CFT alert thresholds (graded)
CFT_ELEVATED = 2.5    # Unusual activity (raised from 2.0 to reduce noise)
CFT_WARNING = 3.5     # Probable P-wave arrival
CFT_CRITICAL = 5.0    # Strong P-wave, significant earthquake

# Multi-station coincidence
COINCIDENCE_WINDOW = 30.0  # seconds
MIN_STATIONS = 1

# Cooldown per alert level per station
COOLDOWN_ELEVATED = 60.0    # seconds (raised from 30)
COOLDOWN_WARNING = 120.0    # (raised from 60)
COOLDOWN_CRITICAL = 180.0   # (raised from 120)

# Primary stations: Venezuela + immediate neighbors — single-station trigger OK
# All others: require coincidence with primary OR 2+ secondary triggers
PRIMARY_STATION_PREFIXES = (
    'VE.',       # All FUNVISIS stations in Venezuela
    'IU.SDV',    # GSN Santo Domingo, Merida
    'CM.BAR2',   # Barranquilla, Colombia (near border)
    'CM.OCA',    # Ocana, Colombia (near border)
    'PR.ACPR',   # ABC islands (Curacao)
)


class StationBuffer:
    """Ring buffer for a single station's waveform data."""

    def __init__(self, station_id: str, max_seconds: float = 120.0):
        self.station_id = station_id
        self.max_seconds = max_seconds
        self.sps: Optional[float] = None
        self.data: list = []
        self.cft_value: float = 0.0
        # Per-level cooldown timestamps
        self.last_elevated: float = 0.0
        self.last_warning: float = 0.0
        self.last_critical: float = 0.0

    def append(self, trace_data: np.ndarray, sampling_rate: float):
        if self.sps is None or self.sps != sampling_rate:
            self.sps = sampling_rate
            self.data = []
        self.data.extend(trace_data.tolist())
        max_samples = int(self.max_seconds * self.sps)
        if len(self.data) > max_samples:
            self.data = self.data[-max_samples:]

    def get_array(self) -> Optional[np.ndarray]:
        if self.sps is None or len(self.data) < int(self.sps * (LTA_SECONDS + 5)):
            return None
        return np.array(self.data, dtype=np.float64)

    @property
    def seconds_buffered(self) -> float:
        if self.sps is None or self.sps == 0:
            return 0.0
        return len(self.data) / self.sps


class Detector:
    """
    STA/LTA earthquake detector with graded alert levels.
    Each alert is emitted immediately via event_callback.
    """

    def __init__(self, event_callback=None, log_callback=None):
        self.buffers: dict[str, StationBuffer] = {}
        self.event_callback = event_callback
        self.log_callback = log_callback  # (station, timestamp, cft, amplitude, sps)
        self.recent_triggers: list = []
        self._lock = threading.Lock()

    def feed_data(self, station_id: str, data: np.ndarray, sampling_rate: float):
        with self._lock:
            if station_id not in self.buffers:
                self.buffers[station_id] = StationBuffer(station_id)
                logger.info("Detector: new station buffer for %s", station_id)

            buf = self.buffers[station_id]
            buf.append(data, sampling_rate)
            self._check_trigger(buf)

    def _check_trigger(self, buf: StationBuffer):
        arr = buf.get_array()
        if arr is None:
            return

        now = time.time()

        try:
            from obspy.signal.trigger import recursive_sta_lta
            from obspy.signal.filter import bandpass

            nyquist = buf.sps / 2.0
            bp_high = min(BANDPASS_HIGH, nyquist * 0.9)
            if bp_high <= BANDPASS_LOW:
                bp_high = nyquist * 0.8
            filtered = bandpass(arr, BANDPASS_LOW, bp_high, buf.sps, corners=2)

            nsta = int(STA_SECONDS * buf.sps)
            nlta = int(LTA_SECONDS * buf.sps)
            cft = recursive_sta_lta(filtered, nsta, nlta)

            if len(cft) == 0:
                return

            current_cft = float(cft[-1])
            buf.cft_value = current_cft

            # Amplitude from last 5 seconds
            amplitude = float(np.max(np.abs(arr[-int(5 * buf.sps):])))

            # Per-second station log for database persistence
            if self.log_callback and current_cft >= 0:
                try:
                    self.log_callback(buf.station_id, now, current_cft, amplitude, buf.sps)
                except Exception:
                    pass

            # Graded alerts — check from highest to lowest
            if current_cft >= CFT_CRITICAL and now - buf.last_critical > COOLDOWN_CRITICAL:
                buf.last_critical = now
                buf.last_warning = now
                buf.last_elevated = now
                self._emit_alert(buf, 'critical', current_cft, amplitude, now)

            elif current_cft >= CFT_WARNING and now - buf.last_warning > COOLDOWN_WARNING:
                buf.last_warning = now
                buf.last_elevated = now
                self._emit_alert(buf, 'warning', current_cft, amplitude, now)

            elif current_cft >= CFT_ELEVATED and now - buf.last_elevated > COOLDOWN_ELEVATED:
                buf.last_elevated = now
                self._emit_alert(buf, 'elevated', current_cft, amplitude, now)

        except ImportError:
            logger.error("ObsPy signal module not available for STA/LTA")
        except Exception as e:
            logger.error("STA/LTA error on %s: %s", buf.station_id, e)

    def _is_primary(self, station_id: str) -> bool:
        """Check if station is primary (close to Venezuela)."""
        return any(station_id.startswith(p) for p in PRIMARY_STATION_PREFIXES)

    def _emit_alert(self, buf: StationBuffer, level: str, cft: float,
                    amplitude: float, now: float):
        """Emit a graded alert event — suppress distant single-station triggers."""
        rough_mag = self._estimate_magnitude(amplitude)

        # Track for multi-station coincidence
        self.recent_triggers.append((buf.station_id, now, amplitude, level))
        self.recent_triggers = [
            t for t in self.recent_triggers
            if now - t[1] < COINCIDENCE_WINDOW
        ]

        # Count unique stations with WARNING+ in coincidence window
        warning_stations = set(
            s for s, t, a, l in self.recent_triggers
            if l in ('warning', 'critical') and now - t < COINCIDENCE_WINDOW
        )
        all_stations = set(
            s for s, t, a, l in self.recent_triggers
            if now - t < COINCIDENCE_WINDOW
        )

        is_primary = self._is_primary(buf.station_id)
        primary_in_coincidence = any(
            self._is_primary(s) for s in all_stations
        )

        # FALSE POSITIVE SUPPRESSION:
        # Distant (non-primary) stations must have corroboration:
        #   - a primary station also triggered, OR
        #   - 2+ different stations triggered in coincidence window
        if not is_primary and not primary_in_coincidence and len(all_stations) < 2:
            logger.info(
                "SUPPRESSED [%s] on %s — CFT=%.1f (distant, no corroboration)",
                level.upper(), buf.station_id, cft
            )
            return  # Don't emit — likely teleseismic or noise

        logger.warning(
            "ALERT [%s] on %s — CFT=%.1f, amp=%.0f, est M%.1f, stations=%d",
            level.upper(), buf.station_id, cft, amplitude, rough_mag, len(all_stations)
        )

        # Escalate alert level based on multi-station coincidence
        effective_level = level
        if len(warning_stations) >= 3:
            effective_level = 'critical'
        elif len(warning_stations) >= 2 and level != 'critical':
            effective_level = 'warning'

        event = {
            'id': f"sl_{buf.station_id}_{int(now)}",
            'source': 'seedlink',
            'type': 'waveform_alert',
            'alert_level': effective_level,
            'mag': rough_mag,
            'mag_type': 'ml_est',
            'lat': 0.0,
            'lon': 0.0,
            'depth': 10.0,
            'time': now,
            'place': f"[{effective_level.upper()}] STA/LTA trigger on {buf.station_id}",
            'received_at': now,
            'trigger_station': buf.station_id,
            'cft': cft,
            'amplitude': amplitude,
            'coincidence_stations': sorted(all_stations),
            'warning_stations': sorted(warning_stations),
        }

        if self.event_callback:
            self.event_callback(event)

    def _estimate_magnitude(self, amplitude: float) -> float:
        if amplitude <= 0:
            return 0.0
        ml_est = np.log10(amplitude) - 1.0
        return round(max(1.0, min(9.0, ml_est)), 1)

    def get_waveform_snippet(self, station_id: str, seconds: float = 60.0) -> Optional[dict]:
        with self._lock:
            buf = self.buffers.get(station_id)
            if buf is None or buf.sps is None:
                return None

            n_samples = int(seconds * buf.sps)
            data = buf.data[-n_samples:] if len(buf.data) >= n_samples else buf.data

            decimate_factor = max(1, int(buf.sps / 50))
            decimated = data[::decimate_factor]

            return {
                'station': station_id,
                'sps': buf.sps / decimate_factor,
                'data': [round(v, 1) for v in decimated],
                'cft': buf.cft_value,
                'seconds': len(data) / buf.sps if buf.sps > 0 else 0,
            }

    def get_status(self) -> dict:
        with self._lock:
            stations = {}
            for sid, buf in self.buffers.items():
                stations[sid] = {
                    'buffered_s': round(buf.seconds_buffered, 1),
                    'sps': buf.sps,
                    'cft': round(buf.cft_value, 2),
                }
            return {
                'stations': stations,
                'recent_triggers': len(self.recent_triggers),
            }
