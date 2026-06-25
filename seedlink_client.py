"""
IRIS SeedLink Client for Real-Time Waveform Ingestion.

Connects to IRIS rtserve SeedLink server and subscribes to
seismic stations near Caracas/Venezuela for P-wave detection.
"""

import asyncio
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

SEEDLINK_SERVER = 'rtserve.iris.washington.edu'
SEEDLINK_PORT = 18000

# Stations to subscribe (ordered by priority)
STATIONS = [
    ('IU', 'SDV',  '00', 'BHZ'),  # Santo Domingo, VE — GSN, guaranteed
    ('IU', 'SDV',  '00', 'BHN'),  # North component for amplitude
    ('CM', 'BAR2', '00', 'BHZ'),  # Barranquilla, Colombia — likely available
    ('CU', 'BCIP', '00', 'BHZ'),  # Barro Colorado, Panama — Caribbean
    ('CU', 'GRTK', '00', 'BHZ'),  # Grand Turk — Caribbean
    ('IU', 'SJG',  '00', 'BHZ'),  # San Juan, Puerto Rico — Caribbean GSN
]

# Optional VE/FUNVISIS stations (may not be available on IRIS SeedLink)
OPTIONAL_STATIONS = [
    ('VE', 'FUNV', '00', 'BHZ'),  # FUNVISIS HQ, Caracas
    ('VE', 'GUIV', '00', 'BHZ'),  # Guiria
    ('VE', 'CURV', '00', 'BHZ'),  # Curarigua
]


class SeedLinkClient:
    """
    SeedLink client that runs ObsPy's EasySeedLinkClient in a background thread.
    Feeds received waveform data to the Detector.
    """

    def __init__(self, detector):
        """
        Args:
            detector: Detector instance to feed waveform data to
        """
        self.detector = detector
        self.connected = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._client = None
        self.active_stations: set = set()

    async def start(self):
        """Start the SeedLink client in a background thread."""
        logger.info("SeedLink client starting (server=%s:%d)", SEEDLINK_SERVER, SEEDLINK_PORT)

        # Run ObsPy SeedLink in a thread since it's blocking
        self._thread = threading.Thread(
            target=self._run_seedlink,
            daemon=True,
            name='seedlink-thread',
        )
        self._thread.start()

        # Keep this coroutine alive and monitor the thread
        while not self._stop_event.is_set():
            if self._thread and not self._thread.is_alive():
                logger.warning("SeedLink thread died, restarting...")
                self.connected = False
                await asyncio.sleep(10)
                self._thread = threading.Thread(
                    target=self._run_seedlink,
                    daemon=True,
                    name='seedlink-thread',
                )
                self._thread.start()
            await asyncio.sleep(5)

    def _run_seedlink(self):
        """Run the SeedLink client (blocking, runs in thread)."""
        try:
            from obspy.clients.seedlink.easyseedlink import EasySeedLinkClient

            class _Client(EasySeedLinkClient):
                def __init__(inner_self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    inner_self.outer = self

                def on_data(inner_self, trace):
                    """Called when new waveform data arrives."""
                    station_id = f"{trace.stats.network}.{trace.stats.station}.{trace.stats.location}.{trace.stats.channel}"
                    inner_self.outer.active_stations.add(station_id)
                    inner_self.outer.connected = True

                    # Feed data to detector
                    try:
                        inner_self.outer.detector.feed_data(
                            station_id,
                            trace.data,
                            trace.stats.sampling_rate,
                        )
                    except Exception as e:
                        logger.error("Error feeding data from %s: %s", station_id, e)

            server_url = f'{SEEDLINK_SERVER}:{SEEDLINK_PORT}'
            client = _Client(server_url)
            self._client = client

            # Subscribe to primary stations
            for net, sta, loc, cha in STATIONS:
                try:
                    client.select_stream(net, sta, f'{cha}')
                    logger.info("Subscribed: %s.%s.%s.%s", net, sta, loc, cha)
                except Exception as e:
                    logger.warning("Failed to subscribe %s.%s: %s", net, sta, e)

            # Try optional stations (may fail)
            for net, sta, loc, cha in OPTIONAL_STATIONS:
                try:
                    client.select_stream(net, sta, f'{cha}')
                    logger.info("Subscribed (optional): %s.%s.%s.%s", net, sta, loc, cha)
                except Exception as e:
                    logger.debug("Optional station %s.%s not available: %s", net, sta, e)

            logger.info("SeedLink: starting data stream...")
            client.run()

        except ImportError:
            logger.error("ObsPy not installed — SeedLink client disabled")
            self.connected = False
        except Exception as e:
            logger.error("SeedLink error: %s", e)
            self.connected = False

    async def stop(self):
        """Stop the client."""
        self._stop_event.set()
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        self.connected = False

    def get_status(self) -> dict:
        return {
            'connected': self.connected,
            'server': f'{SEEDLINK_SERVER}:{SEEDLINK_PORT}',
            'active_stations': sorted(self.active_stations),
            'thread_alive': self._thread.is_alive() if self._thread else False,
        }
