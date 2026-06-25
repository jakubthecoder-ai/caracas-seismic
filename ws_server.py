"""
WebSocket Server for Seismic Monitor.

Serves real-time earthquake data to the frontend.
Port 8768.

Message types pushed to clients:
- {"type":"event", "action":"create|update", "event":{...}}
- {"type":"waveform", "station":"IU.SDV.00.BHZ", "data":[...], "sps":...}
- {"type":"status", "seedlink":{}, "emsc":bool, "usgs":bool}
- {"type":"alert", "level":"critical|high|medium|low", "event":{...}}
- {"type":"history", "events":[...]} — sent on connect
"""

import asyncio
import json
import logging
import time

import websockets

logger = logging.getLogger(__name__)

WS_HOST = '0.0.0.0'
WS_PORT = 8768

# Waveform broadcast interval (don't flood)
WAVEFORM_INTERVAL = 1.0   # seconds
STATUS_INTERVAL = 10.0      # seconds


class WSServer:
    """WebSocket server that broadcasts seismic data to connected frontends."""

    def __init__(self, consolidator=None, detector=None,
                 seedlink_client=None, emsc_client=None, usgs_poller=None):
        self.consolidator = consolidator
        self.detector = detector
        self.seedlink_client = seedlink_client
        self.emsc_client = emsc_client
        self.usgs_poller = usgs_poller
        self.clients: set = set()
        self._broadcast_queue: asyncio.Queue = asyncio.Queue()

    async def start(self):
        """Start the WebSocket server and background tasks."""
        server = await websockets.serve(
            self._handler,
            WS_HOST,
            WS_PORT,
            ping_interval=30,
            ping_timeout=10,
        )
        logger.info("WebSocket server listening on ws://%s:%d", WS_HOST, WS_PORT)

        # Start background broadcast tasks
        await asyncio.gather(
            self._broadcast_loop(),
            self._waveform_loop(),
            self._status_loop(),
            server.wait_closed(),
        )

    async def _handler(self, ws):
        """Handle a new WebSocket client connection."""
        self.clients.add(ws)
        client_addr = ws.remote_address
        logger.info("Client connected: %s (%d total)", client_addr, len(self.clients))

        try:
            # Send event history on connect
            if self.consolidator:
                history = self.consolidator.get_recent_events(50)
                await ws.send(json.dumps({
                    'type': 'history',
                    'events': history,
                }, default=str))

            # Send current status
            await ws.send(json.dumps(self._build_status(), default=str))

            # Keep connection alive, handle client messages
            async for msg in ws:
                # Client can send commands (future: filter settings, etc.)
                try:
                    data = json.loads(msg)
                    logger.debug("Client message: %s", data)
                except json.JSONDecodeError:
                    pass

        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)
            logger.info("Client disconnected: %s (%d remaining)", client_addr, len(self.clients))

    async def broadcast(self, message: dict):
        """Queue a message for broadcast to all clients."""
        await self._broadcast_queue.put(message)

    async def _broadcast_loop(self):
        """Process broadcast queue and send to all clients."""
        while True:
            message = await self._broadcast_queue.get()
            if not self.clients:
                continue

            payload = json.dumps(message, default=str)
            dead = set()

            for ws in self.clients.copy():
                try:
                    await ws.send(payload)
                except websockets.ConnectionClosed:
                    dead.add(ws)
                except Exception as e:
                    logger.error("Broadcast error: %s", e)
                    dead.add(ws)

            self.clients -= dead

    async def _waveform_loop(self):
        """Periodically broadcast live waveform data from all active stations."""
        while True:
            await asyncio.sleep(WAVEFORM_INTERVAL)

            if not self.clients or not self.detector:
                continue

            # Send waveforms from all active BHZ stations
            sent = set()
            for station_id, buf in self.detector.buffers.items():
                # Only BHZ vertical component, skip duplicates
                if not station_id.endswith('BHZ'):
                    continue
                base = station_id.split('.')[0] + '.' + station_id.split('.')[1]
                if base in sent:
                    continue

                snippet = self.detector.get_waveform_snippet(station_id, seconds=60)
                if snippet and len(snippet.get('data', [])) > 10:
                    await self.broadcast({
                        'type': 'waveform',
                        **snippet,
                    })
                    sent.add(base)

    async def _status_loop(self):
        """Periodically broadcast connection status."""
        while True:
            await asyncio.sleep(STATUS_INTERVAL)

            if not self.clients:
                continue

            status = self._build_status()
            await self.broadcast(status)

    def _build_status(self) -> dict:
        """Build current system status."""
        return {
            'type': 'status',
            'seedlink': self.seedlink_client.get_status() if self.seedlink_client else {'connected': False},
            'emsc': self.emsc_client.connected if self.emsc_client else False,
            'usgs': self.usgs_poller.connected if self.usgs_poller else False,
            'detector': self.detector.get_status() if self.detector else {},
            'clients': len(self.clients),
            'timestamp': time.time(),
        }
