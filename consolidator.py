"""
Event Consolidator — Deduplication + GMPE Enrichment.

Consumes events from all data sources (SeedLink, EMSC, USGS),
deduplicates across sources, enriches with BA08 GMPE calculations
for Caracas, and pushes to WebSocket broadcast.
"""

import asyncio
import logging
import time

from gmpe import caracas_impact

logger = logging.getLogger(__name__)

# Dedup window: events within this time/distance are considered the same
DEDUP_TIME_WINDOW = 120.0   # seconds
DEDUP_DIST_THRESHOLD = 100.0  # km

# Rolling event list retention
MAX_EVENTS = 500
MAX_AGE_HOURS = 24


class Consolidator:
    """
    Consumes raw events from all sources, deduplicates, enriches with
    Caracas impact calculations, and emits consolidated events.
    """

    def __init__(self, event_queue: asyncio.Queue, broadcast_callback=None, db=None):
        """
        Args:
            event_queue: Input queue (shared across all data sources)
            broadcast_callback: async callable(message_dict) for WebSocket broadcast
            db: Database instance for persistence
        """
        self.event_queue = event_queue
        self.broadcast = broadcast_callback
        self.db = db
        self.events: list = []  # Consolidated event list

    async def start(self):
        """Main consolidation loop."""
        logger.info("Consolidator starting")

        # Load persisted events from database on startup
        if self.db:
            try:
                saved = self.db.get_events(hours=24, limit=500)
                for evt in saved:
                    if not self._find_duplicate(evt):
                        self.events.append(evt)
                logger.info("Loaded %d events from database", len(self.events))
            except Exception as e:
                logger.error("DB load error: %s", e)

        while True:
            try:
                event = await self.event_queue.get()
                await self._process_event(event)
            except Exception as e:
                logger.error("Consolidator error: %s", e)

    async def _process_event(self, event: dict):
        """Process a single event from any source."""
        # SeedLink waveform alerts get special handling — immediate push
        if event.get('type') == 'waveform_alert':
            await self._process_waveform_alert(event)
            return

        # Check for duplicate
        existing = self._find_duplicate(event)

        if existing is not None:
            # Update existing event — add source tag, keep fastest
            self._merge_event(existing, event)
            logger.info(
                "Consolidated: M%.1f %s — now from %s",
                existing['mag'], existing.get('place', ''),
                ', '.join(existing.get('sources', []))
            )
            # Persist updated event
            if self.db:
                try:
                    self.db.save_event(existing)
                except Exception as e:
                    logger.error("DB save updated event error: %s", e)

            # Re-broadcast the updated event
            if self.broadcast:
                await self.broadcast({
                    'type': 'event',
                    'action': 'update',
                    'event': existing,
                })
        else:
            # New event — enrich and add
            enriched = self._enrich_event(event)
            self.events.append(enriched)

            # Persist new event
            if self.db:
                try:
                    self.db.save_event(enriched)
                except Exception as e:
                    logger.error("DB save new event error: %s", e)

            logger.info(
                "New event: M%.1f %s [%s] — Caracas: PGA=%.1f gal, MMI=%.1f (%s), alarm=%s",
                enriched['mag'],
                enriched.get('place', ''),
                enriched['source'],
                enriched.get('caracas', {}).get('pga_soil', 0),
                enriched.get('caracas', {}).get('mmi', 0),
                enriched.get('caracas', {}).get('mmi_desc', ''),
                enriched.get('caracas', {}).get('alarm_level', 'none'),
            )

            # Broadcast new event
            if self.broadcast:
                await self.broadcast({
                    'type': 'event',
                    'action': 'create',
                    'event': enriched,
                })

                # If alarm level is medium or higher, also send alert
                alarm = enriched.get('caracas', {}).get('alarm_level', 'none')
                if alarm in ('critical', 'high', 'medium'):
                    await self.broadcast({
                        'type': 'alert',
                        'level': alarm,
                        'event': enriched,
                    })

            # Prune old events
            self._prune()

    async def _process_waveform_alert(self, event: dict):
        """Process SeedLink waveform alert — immediate push to frontend."""
        alert_level = event.get('alert_level', 'elevated')

        # Map detector alert levels to frontend alarm levels
        frontend_level = {
            'elevated': 'low',
            'warning': 'medium',
            'critical': 'critical',
        }.get(alert_level, 'info')

        # Add to event list for logging
        event['sources'] = ['seedlink']
        event['caracas'] = {
            'dist_km': None,
            'pga_soil': None,
            'mmi': None,
            'mmi_desc': f'STA/LTA {alert_level.upper()} — CFT={event.get("cft", 0):.1f}',
            'alarm_level': frontend_level,
            'swave_s': None,
        }
        self.events.append(event)
        self._prune()

        # Persist waveform alert to database
        if self.db:
            try:
                self.db.save_waveform_alert(event)
            except Exception as e:
                logger.error("DB save waveform alert error: %s", e)

        logger.warning(
            "Waveform alert [%s] on %s: CFT=%.1f, %d station(s) in coincidence",
            alert_level.upper(),
            event.get('trigger_station', '?'),
            event.get('cft', 0),
            len(event.get('coincidence_stations', [])),
        )

        # Broadcast immediately as event + alert
        if self.broadcast:
            await self.broadcast({
                'type': 'event',
                'action': 'create',
                'event': event,
            })

            # Also emit explicit alert for warning/critical
            if alert_level in ('warning', 'critical'):
                await self.broadcast({
                    'type': 'alert',
                    'level': frontend_level,
                    'event': event,
                })

    def _enrich_event(self, event: dict) -> dict:
        """Add Caracas impact calculations to an event."""
        event['sources'] = [event['source']]

        # SeedLink events don't have location — skip GMPE
        if event['source'] == 'seedlink' and event['lat'] == 0 and event['lon'] == 0:
            event['caracas'] = {
                'dist_km': None,
                'pga_soil': None,
                'mmi': None,
                'mmi_desc': 'Location unknown (SeedLink detection)',
                'alarm_level': 'info',  # Trigger detected but no location
                'swave_s': None,
            }
            return event

        try:
            impact = caracas_impact(
                event['mag'],
                event['lat'],
                event['lon'],
                event.get('depth', 10.0),
            )
            event['caracas'] = impact
        except Exception as e:
            logger.error("GMPE calculation error: %s", e)
            event['caracas'] = {
                'dist_km': None,
                'pga_soil': None,
                'mmi': None,
                'mmi_desc': 'Calculation error',
                'alarm_level': 'none',
            }

        return event

    def _find_duplicate(self, event: dict) -> dict | None:
        """Find an existing event that matches this one (same earthquake, different source)."""
        from gmpe import haversine

        for existing in self.events:
            # Time check
            time_diff = abs(existing['time'] - event['time'])
            if time_diff > DEDUP_TIME_WINDOW:
                continue

            # SeedLink events have no location — match by time only
            if event['source'] == 'seedlink' or existing['source'] == 'seedlink':
                if time_diff < 60:
                    return existing
                continue

            # Location check
            try:
                dist = haversine(
                    existing['lat'], existing['lon'],
                    event['lat'], event['lon']
                )
                if dist < DEDUP_DIST_THRESHOLD:
                    return existing
            except Exception:
                continue

        return None

    def _merge_event(self, existing: dict, new_event: dict):
        """Merge a new event observation into an existing consolidated event."""
        if new_event['source'] not in existing.get('sources', []):
            existing.setdefault('sources', []).append(new_event['source'])

        # If new event has better magnitude info, update
        if new_event.get('mag_type') in ('mw', 'mww', 'mwc') and existing.get('mag_type') not in ('mw', 'mww', 'mwc'):
            existing['mag'] = new_event['mag']
            existing['mag_type'] = new_event['mag_type']
            # Recalculate Caracas impact with updated magnitude
            if new_event['lat'] != 0:
                try:
                    existing['caracas'] = caracas_impact(
                        new_event['mag'],
                        new_event['lat'],
                        new_event['lon'],
                        new_event.get('depth', 10.0),
                    )
                except Exception:
                    pass

        # If new event has location and existing doesn't, take it
        if existing.get('lat') == 0 and new_event.get('lat') != 0:
            existing['lat'] = new_event['lat']
            existing['lon'] = new_event['lon']
            existing['depth'] = new_event.get('depth', 10.0)
            existing['place'] = new_event.get('place', '')

        # Keep earliest received_at
        if new_event.get('received_at', float('inf')) < existing.get('received_at', float('inf')):
            existing['first_source'] = new_event['source']

    def _prune(self):
        """Remove old events."""
        cutoff = time.time() - MAX_AGE_HOURS * 3600
        self.events = [
            e for e in self.events
            if e.get('received_at', 0) > cutoff
        ][-MAX_EVENTS:]

    def get_recent_events(self, limit: int = 50) -> list:
        """Get recent consolidated events for new WebSocket clients."""
        return self.events[-limit:]
