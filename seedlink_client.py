"""
IRIS SeedLink Client for Real-Time Waveform Ingestion.

Connects to IRIS rtserve SeedLink server and subscribes to
seismic stations covering the Caracas fault system (San Sebastian,
Bocono, El Pilar) within ~1000km of Caracas.
"""

import asyncio
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

SEEDLINK_SERVER = 'rtserve.iris.washington.edu'
SEEDLINK_PORT = 18000

# Core GSN/CU stations — always available on IRIS SeedLink
REQUIRED_STATIONS = [
    ('IU', 'SDV',  '00', 'BHZ'),  # Santo Domingo, Merida — GSN (440 km)
    ('IU', 'SDV',  '00', 'BHN'),  # North component for amplitude
    ('IU', 'SJG',  '00', 'BHZ'),  # San Juan, Puerto Rico — Caribbean GSN (850 km)
    ('CU', 'BCIP', '00', 'BHZ'),  # Barro Colorado, Panama (1700 km)
    ('CU', 'GRTK', '00', 'BHZ'),  # Grand Turk (1100 km)
]

# VE (FUNVISIS) stations covering the fault system, sorted by distance from Caracas.
# Channels: BHZ where available, HHZ for stations that only have high-gain.
# Many may be intermittently available on IRIS SeedLink.
VE_STATIONS = [
    # --- Near Caracas (<100 km) ---
    ('VE', 'FUNV', '', 'BHZ'),   # FUNVISIS HQ, Caracas — 10 km
    ('VE', 'FUNV', '', 'HHZ'),   # fallback HHZ
    ('VE', 'TACV', '', 'BHZ'),   # Tacata, Miranda — 43 km (Bocono)
    ('VE', 'TACV', '', 'HHZ'),
    ('VE', 'BIRV', '', 'BHZ'),   # Birongo, Miranda — 69 km (El Pilar W)
    ('VE', 'BIRV', '', 'HHZ'),
    ('VE', 'BENV', '', 'BHZ'),   # Belen, Carabobo — 97 km (Bocono)
    ('VE', 'BENV', '', 'HHZ'),
    # --- 100-200 km ---
    ('VE', 'TURV', '', 'BHZ'),   # Turiamo, Carabobo — 103 km (San Sebastian W)
    ('VE', 'TURV', '', 'HHZ'),
    ('VE', 'CUPV', '', 'HHZ'),   # Cupira, Miranda — 129 km (El Pilar)
    ('VE', 'TINV', '', 'HHZ'),   # Tinaco, Cojedes — 146 km (inland)
    ('VE', 'MERV', '', 'HHZ'),   # Mercedes, Guarico — 153 km (south baseline)
    ('VE', 'ORCV', '', 'HHZ'),   # Orchila island — 165 km (offshore)
    # --- 200-300 km ---
    ('VE', 'BAUV', '', 'BHZ'),   # Barinitas, Barinas — 214 km (Bocono foothills)
    ('VE', 'BAUV', '', 'HHZ'),
    ('VE', 'JACV', '', 'HHZ'),   # Jacuque, Falcon — 221 km (San Sebastian)
    ('VE', 'PCRV', '', 'BHZ'),   # Pto. La Cruz, Anzoategui — 255 km (El Pilar)
    ('VE', 'TERV', '', 'BHZ'),   # El Tocuyo, Lara — 258 km (Bocono)
    ('VE', 'TERV', '', 'HHZ'),
    ('VE', 'IBAV', '', 'HHZ'),   # Isla de Coche/Margarita — 291 km (offshore)
    # --- 300-500 km ---
    ('VE', 'MACV', '', 'HHZ'),   # Macanao, Margarita — 302 km (offshore)
    ('VE', 'SANV', '', 'HHZ'),   # Sanare, Lara — 309 km (Bocono)
    ('VE', 'SIQV', '', 'HHZ'),   # Siquisique, Lara — 318 km (San Sebastian ext)
    ('VE', 'CUNV', '', 'HHZ'),   # Cumana, Sucre — 319 km (El Pilar)
    ('VE', 'CURV', '', 'BHZ'),   # Curarigua, Lara — 339 km (Bocono)
    ('VE', 'CURV', '', 'HHZ'),
    ('VE', 'XCAR', '', 'HHZ'),   # Carupano, Sucre — 366 km (El Pilar)
    ('VE', 'QARV', '', 'HHZ'),   # Quibor, Lara — 398 km (Bocono)
    ('VE', 'CRUV', '', 'HHZ'),   # Carupano W, Sucre — 406 km (El Pilar)
    ('VE', 'DABV', '', 'HHZ'),   # Dabajuro, Falcon — 411 km (San Sebastian ext)
    ('VE', 'ORIV', '', 'HHZ'),   # Orinoco, Bolivar — 413 km (south baseline)
    ('VE', 'ITEV', '', 'HHZ'),   # Isla de Testigos — 422 km (offshore)
    ('VE', 'GUNV', '', 'HHZ'),   # Guiria, Sucre — 435 km (El Pilar E)
    ('VE', 'XYAG', '', 'HHZ'),   # Yaguaraparo, Sucre — 445 km (El Pilar E)
    # --- 500-700 km ---
    ('VE', 'GUIV', '', 'BHZ'),   # Guiria E, Sucre — 513 km (El Pilar terminus)
    ('VE', 'GUIV', '', 'HHZ'),
    ('VE', 'VIGV', '', 'HHZ'),   # Vigia, Merida — 523 km (Bocono deep)
    ('VE', 'SOCV', '', 'HHZ'),   # Socopo, Barinas — 499 km (Bocono)
    ('VE', 'MCQV', '', 'BHZ'),   # Machiques, Zulia — 619 km (border)
    ('VE', 'MCQV', '', 'HHZ'),
    ('VE', 'CAPV', '', 'HHZ'),   # Capacho, Tachira — 663 km (border, Bocono S)
]

# Other regional stations (non-VE)
OTHER_OPTIONAL = [
    ('CM', 'BAR2', '00', 'BHZ'),  # Barranquilla, Colombia — 868 km
    ('CM', 'OCA',  '00', 'BHZ'),  # Ocana, Colombia — 748 km
    ('PR', 'ACPR', '00', 'HHZ'),  # Aruba/Curacao — 405 km
    ('CU', 'GRGR', '00', 'BHZ'),  # Grenada — 600 km
    ('CU', 'BBGH', '00', 'BHZ'),  # Barbados — 851 km
]


class SeedLinkClient:
    """
    SeedLink client that runs ObsPy's EasySeedLinkClient in a background thread.
    Feeds received waveform data to the Detector.
    """

    def __init__(self, detector):
        self.detector = detector
        self.connected = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._client = None
        self.active_stations: set = set()
        self._subscribed: set = set()  # track (net,sta,cha) to avoid duplicates

    async def start(self):
        """Start the SeedLink client in a background thread."""
        logger.info("SeedLink client starting (server=%s:%d)", SEEDLINK_SERVER, SEEDLINK_PORT)

        self._thread = threading.Thread(
            target=self._run_seedlink,
            daemon=True,
            name='seedlink-thread',
        )
        self._thread.start()

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

    def _subscribe(self, client, net, sta, cha, optional=False):
        """Subscribe to a station/channel, skipping duplicates."""
        key = (net, sta, cha)
        if key in self._subscribed:
            return
        try:
            client.select_stream(net, sta, cha)
            self._subscribed.add(key)
            label = "Subscribed (optional)" if optional else "Subscribed"
            logger.info("%s: %s.%s.%s", label, net, sta, cha)
        except Exception as e:
            if optional:
                logger.debug("Optional station %s.%s.%s not available: %s", net, sta, cha, e)
            else:
                logger.warning("Failed to subscribe %s.%s.%s: %s", net, sta, cha, e)

    def _run_seedlink(self):
        """Run the SeedLink client (blocking, runs in thread)."""
        try:
            from obspy.clients.seedlink.easyseedlink import EasySeedLinkClient

            class _Client(EasySeedLinkClient):
                def __init__(inner_self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    inner_self.outer = self

                def on_data(inner_self, trace):
                    station_id = f"{trace.stats.network}.{trace.stats.station}.{trace.stats.location}.{trace.stats.channel}"
                    inner_self.outer.active_stations.add(station_id)
                    inner_self.outer.connected = True
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
            self._subscribed = set()

            # 1. Required (GSN) stations — must succeed
            for net, sta, loc, cha in REQUIRED_STATIONS:
                self._subscribe(client, net, sta, cha, optional=False)

            # 2. VE (FUNVISIS) stations — optional, may or may not be available
            for net, sta, loc, cha in VE_STATIONS:
                self._subscribe(client, net, sta, cha, optional=True)

            # 3. Other regional stations — optional
            for net, sta, loc, cha in OTHER_OPTIONAL:
                self._subscribe(client, net, sta, cha, optional=True)

            logger.info("SeedLink: %d unique subscriptions, starting data stream...",
                       len(self._subscribed))
            client.run()

        except ImportError:
            logger.error("ObsPy not installed — SeedLink client disabled")
            self.connected = False
        except Exception as e:
            logger.error("SeedLink error: %s", e)
            self.connected = False

    async def stop(self):
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
