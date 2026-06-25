"""
EMSC Testimonies (Felt Reports) Client.

Polls EMSC SeismicPortal Testimonies API after event detection
to get crowdsourced felt intensity reports from LastQuake app users.

Endpoint: https://www.seismicportal.eu/testimonies-ws/api/search
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

TESTIMONIES_URL = 'https://www.seismicportal.eu/testimonies-ws/api/search'

# Check for testimonies this often after a new event
POLL_INTERVAL = 60  # seconds
# Only fetch testimonies for events newer than this
MAX_AGE = 3600 * 6  # 6 hours


class EMSCTestimoniesClient:
    """
    Polls EMSC Testimonies API for crowdsourced felt intensity data.
    Triggered after event detection.
    """

    def __init__(self, consolidator=None, broadcast_callback=None):
        self.consolidator = consolidator
        self.broadcast = broadcast_callback
        self.connected = False
        self._session = None
        self._fetched_events: set = set()

    async def start(self):
        """Main loop — check for events that need felt report data."""
        import aiohttp
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )

        logger.info("EMSC Testimonies client starting")

        while True:
            try:
                await self._check_events()
                self.connected = True
            except Exception as e:
                logger.error("Testimonies error: %s", e)
                self.connected = False

            await asyncio.sleep(POLL_INTERVAL)

    async def _check_events(self):
        """Check for events that need felt report data."""
        if not self.consolidator:
            return

        events = self.consolidator.get_recent_events(20)
        now = time.time()

        for event in events:
            event_id = event.get('id', '')
            if event_id in self._fetched_events:
                continue

            event_time = event.get('time', 0)
            age = now - event_time

            # Skip too old events
            if age > MAX_AGE:
                continue

            # Only fetch for significant events (M3.0+) that are from EMSC
            if event.get('mag', 0) >= 3.0:
                # Try to get EMSC unid from the event
                emsc_unid = None
                if event_id.startswith('emsc_'):
                    emsc_unid = event_id.replace('emsc_', '')

                if emsc_unid:
                    await self._fetch_testimonies(event, emsc_unid)
                else:
                    # Try location-based search
                    await self._fetch_testimonies_by_location(event)

                self._fetched_events.add(event_id)

        # Prune old fetched set
        if len(self._fetched_events) > 1000:
            self._fetched_events = set(list(self._fetched_events)[-500:])

    async def _fetch_testimonies(self, event: dict, unid: str):
        """Fetch testimonies by EMSC event UNID."""
        params = {
            'unids': f'[{unid}]',
            'includeTestimonies': 'true',
            'format': 'json',
        }

        try:
            async with self._session.get(TESTIMONIES_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._process_testimonies(event, data)
                elif resp.status == 204:
                    logger.debug("No testimonies for event %s", unid)
        except Exception as e:
            logger.error("Testimonies fetch error for %s: %s", unid, e)

    async def _fetch_testimonies_by_location(self, event: dict):
        """Fetch testimonies by geographic location."""
        params = {
            'lat': event['lat'],
            'lon': event['lon'],
            'maxradius': '5',  # degrees
            'format': 'json',
        }

        try:
            async with self._session.get(TESTIMONIES_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._process_testimonies(event, data)
        except Exception as e:
            logger.debug("Testimonies location fetch error: %s", e)

    def _process_testimonies(self, event: dict, data):
        """Process testimonies response and enrich event."""
        if not data:
            return

        # Extract summary stats
        if isinstance(data, list) and len(data) > 0:
            entry = data[0]
            n_testimonies = entry.get('nb_testimonies', 0)
            max_intensity = entry.get('max_intensity', 0)

            event['felt_reports'] = {
                'count': n_testimonies,
                'max_intensity': max_intensity,
                'source': 'emsc_testimonies',
            }

            if n_testimonies > 0:
                logger.info(
                    "Testimonies for M%.1f %s: %d reports, max intensity %s",
                    event['mag'], event.get('place', ''),
                    n_testimonies, max_intensity
                )

                if self.broadcast:
                    asyncio.create_task(self.broadcast({
                        'type': 'testimonies',
                        'event_id': event.get('id', ''),
                        'count': n_testimonies,
                        'max_intensity': max_intensity,
                    }))

    async def stop(self):
        if self._session:
            await self._session.close()
