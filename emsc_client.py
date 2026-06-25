"""
EMSC SeismicPortal WebSocket Client.

Connects to wss://www.seismicportal.eu/standing_order/websocket for
real-time earthquake event push notifications. Auto-reconnects on disconnect.
"""

import asyncio
import json
import logging
import time
from typing import Optional

import websockets

logger = logging.getLogger(__name__)

EMSC_WS_URL = 'wss://www.seismicportal.eu/standing_order/websocket'

# Region filter: Wide Caribbean/Venezuela
LAT_MIN, LAT_MAX = 6.0, 16.0
LON_MIN, LON_MAX = -76.0, -58.0

RECONNECT_DELAY = 5  # seconds


def in_region(lat: float, lon: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


def parse_emsc_message(raw: str) -> Optional[dict]:
    """Parse EMSC WebSocket message into normalized event dict."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return None

    action = msg.get('action')
    if action not in ('create', 'update'):
        return None

    data = msg.get('data', {})
    props = data.get('properties', {})

    lat = props.get('lat')
    lon = props.get('lon')
    if lat is None or lon is None:
        return None

    lat, lon = float(lat), float(lon)
    if not in_region(lat, lon):
        return None

    mag = props.get('mag')
    if mag is None:
        return None

    # Parse EMSC time (ISO format)
    time_str = props.get('time', '')
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        event_time = dt.timestamp()
    except (ValueError, AttributeError):
        event_time = time.time()

    return {
        'id': f"emsc_{props.get('unid', props.get('source_id', ''))}",
        'source': 'emsc',
        'mag': float(mag),
        'mag_type': props.get('magtype', 'ml'),
        'lat': lat,
        'lon': lon,
        'depth': float(props.get('depth', 10.0)),
        'time': event_time,
        'place': props.get('flynn_region', ''),
        'url': props.get('source_catalog', ''),
        'received_at': time.time(),
        'emsc_action': action,
    }


class EMSCClient:
    def __init__(self, event_queue: asyncio.Queue):
        self.event_queue = event_queue
        self.connected = False
        self.known_ids: set = set()

    async def start(self):
        """Main connection loop with auto-reconnect."""
        logger.info("EMSC WebSocket client starting")

        while True:
            try:
                await self._connect()
            except Exception as e:
                logger.error("EMSC connection error: %s", e)
                self.connected = False

            logger.info("EMSC: reconnecting in %ds...", RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)

    async def _connect(self):
        """Connect and listen for messages."""
        async with websockets.connect(
            EMSC_WS_URL,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self.connected = True
            logger.info("EMSC WebSocket connected")

            async for raw in ws:
                event = parse_emsc_message(raw)
                if event is None:
                    continue

                if event['id'] not in self.known_ids:
                    self.known_ids.add(event['id'])
                    await self.event_queue.put(event)
                    logger.info(
                        "EMSC event: M%.1f %s (%.2f, %.2f) [%s]",
                        event['mag'], event['place'], event['lat'], event['lon'],
                        event['emsc_action']
                    )

            # If we exit the async for, connection was closed
            self.connected = False
            logger.warning("EMSC WebSocket closed")

    async def stop(self):
        self.connected = False
