"""
USGS GeoJSON Earthquake Feed Poller.

Polls all_hour.geojson every 15 seconds, filters by Caribbean/Venezuela region,
deduplicates by event ID, emits new events to the consolidator queue.
"""

import asyncio
import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

FEEDS = {
    'hour': 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson',
    'day25': 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson',
}

# Region filter: Wide Caribbean/Venezuela (captures everything that could affect Caracas)
LAT_MIN, LAT_MAX = 6.0, 16.0
LON_MIN, LON_MAX = -76.0, -58.0

POLL_INTERVAL = 2  # seconds

# Caracas coordinates for 300km radius aggregation
CARACAS_LAT, CARACAS_LON = 10.4806, -66.9036
CARACAS_RADIUS_KM = 300


def in_region(lat: float, lon: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


def parse_feature(feature: dict) -> Optional[dict]:
    """Parse a GeoJSON feature into a normalized event dict."""
    props = feature.get('properties', {})
    geom = feature.get('geometry', {})
    coords = geom.get('coordinates', [0, 0, 0])

    lon, lat = coords[0], coords[1]
    depth = coords[2] if len(coords) > 2 else 10.0

    if not in_region(lat, lon):
        return None

    mag = props.get('mag')
    if mag is None or mag < 1.0:
        return None

    return {
        'id': feature.get('id', props.get('code', '')),
        'source': 'usgs',
        'mag': float(mag),
        'mag_type': props.get('magType', 'ml'),
        'lat': lat,
        'lon': lon,
        'depth': depth,
        'time': props.get('time', 0) / 1000.0,  # ms → unix seconds
        'place': props.get('place', ''),
        'url': props.get('url', ''),
        'received_at': time.time(),
    }


class USGSPoller:
    def __init__(self, event_queue: asyncio.Queue):
        self.event_queue = event_queue
        self.known_ids: set = set()
        self.connected = False
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        """Main polling loop."""
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        logger.info("USGS poller starting (interval=%ds)", POLL_INTERVAL)

        while True:
            try:
                await self._poll()
                self.connected = True
            except Exception as e:
                logger.error("USGS poll error: %s", e)
                self.connected = False

            await asyncio.sleep(POLL_INTERVAL)

    async def _poll(self):
        """Fetch hourly feed and emit new events."""
        async with self._session.get(FEEDS['hour']) as resp:
            if resp.status != 200:
                logger.warning("USGS HTTP %d", resp.status)
                return

            data = await resp.json()

        features = data.get('features', [])
        new_count = 0

        for feature in features:
            event = parse_feature(feature)
            if event is None:
                continue

            if event['id'] not in self.known_ids:
                self.known_ids.add(event['id'])
                await self.event_queue.put(event)
                new_count += 1
                logger.info(
                    "USGS new event: M%.1f %s (%.2f, %.2f)",
                    event['mag'], event['place'], event['lat'], event['lon']
                )

        # Prune known IDs (keep last 500)
        if len(self.known_ids) > 500:
            self.known_ids = set(list(self.known_ids)[-200:])

        if new_count:
            logger.info("USGS: %d new events from %d total", new_count, len(features))

    async def stop(self):
        if self._session:
            await self._session.close()
