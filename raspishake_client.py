"""
Raspberry Shake FDSNWS Client — Supplementary Post-Event Waveforms.

Fetches waveform data from Raspberry Shake citizen scientist network
via FDSNWS (30-minute data delay). Triggered after event detection
to provide additional waveform data for validation.

Network code: AM
Endpoint: https://data.raspberryshake.org/fdsnws/
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

FDSNWS_BASE = 'https://data.raspberryshake.org/fdsnws'
STATION_URL = f'{FDSNWS_BASE}/station/1/query'
DATA_URL = f'{FDSNWS_BASE}/dataselect/1/query'

# Region around Caracas/Venezuela
LAT_MIN, LAT_MAX = 8.0, 14.0
LON_MIN, LON_MAX = -74.0, -60.0

FETCH_DELAY = 1800  # 30 minutes — data is delayed
CHECK_INTERVAL = 300  # Check every 5 minutes for new data


class RaspiShakeClient:
    """
    Fetches post-event waveform data from Raspberry Shake FDSNWS.
    Not real-time — data has ~30 minute delay.
    Used for event validation and additional waveform context.
    """

    def __init__(self, consolidator=None):
        self.consolidator = consolidator
        self.connected = False
        self.nearby_stations: list = []
        self._session = None

    async def start(self):
        """Main loop — discover stations, then fetch waveforms after events."""
        import aiohttp
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60)
        )

        logger.info("RaspiShake FDSNWS client starting")

        # Discover stations on first run
        await self._discover_stations()

        # Periodic check for event data
        while True:
            try:
                if self.consolidator:
                    await self._check_events()
                self.connected = True
            except Exception as e:
                logger.error("RaspiShake error: %s", e)
                self.connected = False

            await asyncio.sleep(CHECK_INTERVAL)

    async def _discover_stations(self):
        """Find Raspberry Shake stations in the Venezuela/Caribbean region."""
        params = {
            'network': 'AM',
            'minlatitude': LAT_MIN,
            'maxlatitude': LAT_MAX,
            'minlongitude': LON_MIN,
            'maxlongitude': LON_MAX,
            'level': 'station',
            'format': 'text',
        }

        try:
            async with self._session.get(STATION_URL, params=params) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    lines = text.strip().split('\n')
                    # Skip header
                    stations = []
                    for line in lines[1:]:
                        parts = line.split('|')
                        if len(parts) >= 5:
                            stations.append({
                                'network': parts[0],
                                'station': parts[1],
                                'lat': float(parts[2]),
                                'lon': float(parts[3]),
                            })
                    self.nearby_stations = stations
                    logger.info(
                        "RaspiShake: found %d stations in region",
                        len(stations)
                    )
                elif resp.status == 204:
                    logger.info("RaspiShake: no stations found in region")
                else:
                    logger.warning("RaspiShake station query: HTTP %d", resp.status)
        except Exception as e:
            logger.error("RaspiShake station discovery error: %s", e)

    async def _check_events(self):
        """Check for recent events and fetch waveforms if data is available."""
        if not self.nearby_stations:
            return

        events = self.consolidator.get_recent_events(10)
        now = time.time()

        for event in events:
            event_time = event.get('time', 0)
            age = now - event_time

            # Only fetch if event is old enough for data to be available
            if age < FETCH_DELAY:
                continue

            # Don't re-fetch
            if event.get('_raspishake_fetched'):
                continue

            if event.get('mag', 0) >= 3.0:
                await self._fetch_waveforms(event)
                event['_raspishake_fetched'] = True

    async def _fetch_waveforms(self, event: dict):
        """Fetch waveform data from nearby RS stations for an event."""
        from datetime import datetime, timezone, timedelta

        event_time = datetime.fromtimestamp(event['time'], tz=timezone.utc)
        start = event_time - timedelta(seconds=30)
        end = event_time + timedelta(seconds=120)

        for station in self.nearby_stations[:5]:  # Limit to 5 closest
            params = {
                'network': station['network'],
                'station': station['station'],
                'channel': 'EHZ',
                'starttime': start.strftime('%Y-%m-%dT%H:%M:%S'),
                'endtime': end.strftime('%Y-%m-%dT%H:%M:%S'),
                'format': 'miniseed',
            }

            try:
                async with self._session.get(DATA_URL, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        logger.info(
                            "RaspiShake: got %d bytes from %s for M%.1f event",
                            len(data), station['station'], event['mag']
                        )
                        # TODO: Parse miniSEED and add to event validation
                    elif resp.status == 204:
                        pass  # No data for this station/time
            except Exception as e:
                logger.debug("RaspiShake fetch error for %s: %s", station['station'], e)

    async def stop(self):
        if self._session:
            await self._session.close()
