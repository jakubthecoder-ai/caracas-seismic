"""
HTTP Server with Server-Side Rendering + SSE.

Serves a lightweight HTML frontend via Jinja2 templates and pushes
real-time updates via Server-Sent Events. Includes AlarmManager
with Schmitt trigger hysteresis for false positive suppression.

Port 8080.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from i18n import t, mmi_desc, time_ago, TRANSLATIONS

logger = logging.getLogger(__name__)

HTTP_HOST = '0.0.0.0'
HTTP_PORT = 8080

BASE_DIR = Path(__file__).parent

# --- Alarm Level Ordering ---
LEVEL_ORDER = {
    'none': 0, 'info': 0, 'low': 1, 'elevated': 2,
    'warning': 3, 'medium': 3, 'high': 4, 'critical': 5,
}


class AlarmManager:
    """
    Dual-source alarm manager with Schmitt trigger hysteresis.

    Source 1: Catalog alarm (USGS/EMSC events) — reliable, zero false positives.
    Source 2: Waveform alarm (STA/LTA CFT) — fast, needs filtering.

    Output = max(catalog_level, waveform_level).

    Anti-false-positive features:
    - Elevated level is SILENT (internal tracking only)
    - Schmitt hysteresis: different ON/OFF thresholds prevent oscillation
    - Sustained requirement: signal must hold above threshold for N seconds
    - Corroboration: warning requires 2nd station or catalog match
    - Minimum hold: once triggered, alarm stays for at least 2 minutes
    """

    # Escalation thresholds: (CFT on-threshold, sustained seconds required)
    ESCALATION = {
        'elevated': (2.5, 5),
        'warning': (3.5, 5),
        'critical': (5.0, 10),
    }

    # De-escalation thresholds: (CFT off-threshold, cooldown seconds)
    DEESCALATION = {
        'critical': (2.5, 120),
        'warning': (2.0, 60),
        'elevated': (1.5, 30),
    }

    # Sudden spike: CFT jump > this in 1 second → immediate critical
    SPIKE_THRESHOLD = 3.0
    SPIKE_MIN_CFT = 5.0

    # Minimum hold time for user-visible alarms
    MIN_HOLD_SECONDS = 120

    # Catalog alarm minimum duration
    CATALOG_MIN_DURATION = 300  # 5 minutes

    def __init__(self):
        self.waveform_level = 'none'
        self.catalog_level = 'none'
        self.output_level = 'none'
        self.reason_key = ''
        self.reason_params = {}
        self.max_cft = 0.0
        self.max_cft_station = ''

        # Hysteresis tracking
        self._above_since = {}    # level -> timestamp when CFT first exceeded ON threshold
        self._below_since = {}    # level -> timestamp when CFT first dropped below OFF threshold
        self._last_trigger_time = 0
        self._prev_max_cft = 0.0

        # Catalog
        self._catalog_until = 0
        self.last_event = None

        # Waveform snippet for mini-seismogram
        self.waveform_snippet = []

    def on_catalog_event(self, event: dict):
        """Called when consolidator produces a new event with alarm_level."""
        alarm = event.get('caracas', {}).get('alarm_level', 'none')
        if LEVEL_ORDER.get(alarm, 0) >= LEVEL_ORDER.get('medium', 0):
            self.catalog_level = alarm
            self._catalog_until = time.time() + self.CATALOG_MIN_DURATION
            self.last_event = event
            key, params = self._catalog_reason_key(event)
            self._update_output(reason_key=key, reason_params=params, event=event)

    def update_waveform(self, stations: list, detector=None):
        """
        Called every 1s with current station CFT levels.

        Args:
            stations: list of {station, cft, amplitude, lat, lon, is_primary}
            detector: Detector instance for waveform snippets
        """
        now = time.time()

        if not stations:
            return

        max_cft = max((s['cft'] for s in stations), default=0.0)
        max_station = max(stations, key=lambda s: s['cft'])
        primary_cfts = [s['cft'] for s in stations if s.get('is_primary')]

        self.max_cft = max_cft
        self.max_cft_station = max_station.get('station', '')

        # Get waveform snippet for mini-seismogram
        if detector and max_cft > 1.5:
            snippet = detector.get_waveform_snippet(self.max_cft_station, seconds=30)
            if snippet:
                self.waveform_snippet = snippet.get('data', [])
        elif max_cft <= 1.5:
            self.waveform_snippet = []

        # --- Sudden spike detection ---
        cft_jump = max_cft - self._prev_max_cft
        if cft_jump > self.SPIKE_THRESHOLD and max_cft > self.SPIKE_MIN_CFT:
            self._set_waveform_level('critical', now,
                                     reason_key='alert_spike',
                                     reason_params={'station': self.max_cft_station})
            self._prev_max_cft = max_cft
            return

        # --- Sustained threshold escalation ---
        for level in ('elevated', 'warning', 'critical'):
            on_thresh, sustain_s = self.ESCALATION[level]
            if max_cft > on_thresh:
                self._above_since.setdefault(level, now)
                if now - self._above_since[level] >= sustain_s:
                    wf_keys = {
                        'elevated': ('alert_waveform', {'station': self.max_cft_station}),
                        'warning': ('alert_waveform_warning', {}),
                        'critical': ('alert_waveform_critical', {}),
                    }
                    # Warning requires corroboration
                    if level == 'warning':
                        corroborated = sum(1 for c in primary_cfts if c > 2.0) >= 2
                        catalog_match = LEVEL_ORDER.get(self.catalog_level, 0) > 0
                        if corroborated or catalog_match:
                            key, params = wf_keys[level]
                            self._set_waveform_level(level, now, reason_key=key, reason_params=params)
                    else:
                        key, params = wf_keys[level]
                        self._set_waveform_level(level, now, reason_key=key, reason_params=params)
            else:
                self._above_since.pop(level, None)

        # --- De-escalation with hysteresis ---
        for level in ('critical', 'warning', 'elevated'):
            if LEVEL_ORDER.get(self.waveform_level, 0) < LEVEL_ORDER.get(level, 0):
                continue  # Only de-escalate from current level or above

            off_thresh, cool_s = self.DEESCALATION[level]
            if max_cft < off_thresh:
                self._below_since.setdefault(level, now)
                if (now - self._below_since[level] >= cool_s and
                        now - self._last_trigger_time >= self.MIN_HOLD_SECONDS):
                    self._deescalate_waveform(level, now)
            else:
                self._below_since.pop(level, None)

        # --- Catalog alarm expiry ---
        if self._catalog_until and now > self._catalog_until:
            # Only clear catalog if waveform is also calm
            if LEVEL_ORDER.get(self.waveform_level, 0) <= LEVEL_ORDER.get('elevated', 0):
                self.catalog_level = 'none'
                self._catalog_until = 0
                self._update_output()

        self._prev_max_cft = max_cft

    def _set_waveform_level(self, level: str, now: float,
                            reason_key: str = '', reason_params: dict = None):
        """Set waveform alarm level (only escalate, never downgrade here)."""
        if LEVEL_ORDER.get(level, 0) > LEVEL_ORDER.get(self.waveform_level, 0):
            self.waveform_level = level
            self._last_trigger_time = now
            self._update_output(reason_key=reason_key,
                                reason_params=reason_params or {})

    def _deescalate_waveform(self, from_level: str, now: float):
        """De-escalate waveform alarm by one step."""
        steps = {'critical': 'warning', 'warning': 'elevated', 'elevated': 'none'}
        new_level = steps.get(from_level, 'none')
        if LEVEL_ORDER.get(new_level, 0) < LEVEL_ORDER.get(self.waveform_level, 0):
            self.waveform_level = new_level
            self._below_since.pop(from_level, None)
            self._update_output()

    def _update_output(self, reason_key: str = '', reason_params: dict = None,
                       event: dict = None):
        """Compute output alarm level = max(catalog, waveform)."""
        wf_order = LEVEL_ORDER.get(self.waveform_level, 0)
        cat_order = LEVEL_ORDER.get(self.catalog_level, 0)

        if cat_order >= wf_order:
            new_level = self.catalog_level
        else:
            new_level = self.waveform_level

        # Map internal levels to user-visible: elevated is not shown
        if new_level == 'elevated':
            new_level = 'none'

        if reason_key:
            self.reason_key = reason_key
            self.reason_params = reason_params or {}

        self.output_level = new_level

    def _catalog_reason_key(self, event: dict) -> tuple:
        """Return (i18n_key, params) for a catalog event alarm."""
        mag = event.get('mag', 0)
        place = event.get('place', '')
        level = event.get('caracas', {}).get('alarm_level', 'none')
        params = {'mag': f'{mag:.1f}', 'place': place}
        if level == 'critical':
            return 'alert_critical', params
        if level == 'high':
            return 'alert_high', params
        return 'alert_medium', params

    def get_state(self) -> dict:
        """Get current alarm state for SSE push."""
        return {
            'level': self.output_level,
            'reason_key': self.reason_key,
            'reason_params': self.reason_params,
            'cft': self.max_cft,
            'station': self.max_cft_station,
            'waveform_level': self.waveform_level,
            'catalog_level': self.catalog_level,
            'waveform_snippet': self.waveform_snippet[-300:] if self.waveform_snippet else [],
            'event_mag': self.last_event.get('mag') if self.last_event else None,
            'event_place': self.last_event.get('place') if self.last_event else None,
        }


class HTTPServer:
    """aiohttp-based HTTP server with SSE for real-time updates."""

    def __init__(self, consolidator=None, detector=None, alarm_manager=None,
                 seedlink_client=None, emsc_client=None, usgs_poller=None):
        self.consolidator = consolidator
        self.detector = detector
        self.alarm = alarm_manager or AlarmManager()
        self.seedlink_client = seedlink_client
        self.emsc_client = emsc_client
        self.usgs_poller = usgs_poller
        self.sse_clients: set = set()
        self._app = None
        self._prev_alarm_level = 'none'

    async def start(self):
        """Start the HTTP server."""
        from aiohttp import web as _web
        import jinja2 as _jinja2
        import aiohttp_jinja2 as _aiohttp_jinja2

        # Store references for use in handlers
        self._web = _web
        self._aiohttp_jinja2 = _aiohttp_jinja2

        app = _web.Application()

        # Setup Jinja2
        _aiohttp_jinja2.setup(
            app,
            loader=_jinja2.FileSystemLoader(str(BASE_DIR / 'templates')),
        )

        # Routes
        app.router.add_get('/', self._handle_index)
        app.router.add_get('/details', self._handle_details)
        app.router.add_get('/sse', self._handle_sse)
        app.router.add_static('/static', str(BASE_DIR / 'static'))

        self._app = app

        runner = _web.AppRunner(app)
        await runner.setup()
        site = _web.TCPSite(runner, HTTP_HOST, HTTP_PORT)
        await site.start()
        logger.info("HTTP server listening on http://%s:%d", HTTP_HOST, HTTP_PORT)

        # Start background tasks
        await asyncio.gather(
            self._alarm_monitor_loop(),
            self._status_broadcast_loop(),
        )

    async def _handle_index(self, request):
        """Serve the main page (server-rendered)."""
        lang = request.query.get('lang', 'es')
        if lang not in TRANSLATIONS:
            lang = 'es'

        alarm_state = self.alarm.get_state()
        alarm_level = alarm_state['level']

        # Format alarm text in the correct language
        reason_key = alarm_state.get('reason_key', '')
        reason_params = alarm_state.get('reason_params', {})
        if alarm_level in ('critical', 'high', 'warning', 'medium') and reason_key:
            alarm_text = t(reason_key, lang, **reason_params)
        else:
            alarm_text = t('no_threat', lang)

        # Last event
        last_event = None
        last_event_time = ''
        if self.consolidator:
            events = self.consolidator.get_recent_events(1)
            if events:
                last_event = events[-1]
                elapsed = time.time() - last_event.get('received_at', time.time())
                last_event_time = time_ago(elapsed, lang)

        context = {
            'lang': lang,
            't': lambda key, **kw: t(key, lang, **kw),
            'alarm_level': alarm_level,
            'alarm_text': alarm_text,
            'last_event': last_event,
            'last_event_time': last_event_time,
        }
        return self._aiohttp_jinja2.render_template('base.html', request, context)

    async def _handle_details(self, request):
        """Serve the details HTML fragment."""
        lang = request.query.get('lang', 'es')
        if lang not in TRANSLATIONS:
            lang = 'es'

        # Events for table
        events = []
        impact = None
        if self.consolidator:
            raw_events = self.consolidator.get_recent_events(20)
            for evt in raw_events:
                elapsed = time.time() - evt.get('received_at', time.time())
                events.append({
                    **evt,
                    'time_ago': time_ago(elapsed, lang),
                })
            # Impact from most recent significant event
            if raw_events:
                last = raw_events[-1]
                caracas = last.get('caracas', {})
                if caracas.get('dist_km') is not None:
                    impact = {
                        'dist_km': caracas.get('dist_km', 0),
                        'pga_soil': caracas.get('pga_soil', 0),
                        'mmi': caracas.get('mmi', 0),
                        'mmi_desc': caracas.get('mmi_desc', ''),
                        'swave_s': caracas.get('swave_s'),
                    }

        # Station data for map
        stations = []
        if self.detector:
            stations = self.detector.get_station_levels()

        # Events as JSON for map markers
        events_json = json.dumps([
            {'lat': e.get('lat'), 'lon': e.get('lon'), 'mag': e.get('mag', 0),
             'place': e.get('place', ''), 'depth': e.get('depth', 0)}
            for e in events if e.get('lat') and e.get('lon')
        ], default=str)

        # Render template
        env = self._aiohttp_jinja2.get_env(self._app)
        template = env.get_template('detail_fragment.html')
        html = template.render(
            lang=lang,
            t=lambda key, **kw: t(key, lang, **kw),
            events=events,
            impact=impact,
            stations=stations,
            events_json=events_json,
        )
        return self._web.Response(text=html, content_type='text/html')

    async def _handle_sse(self, request):
        """SSE endpoint — long-lived connection for real-time updates."""
        resp = self._web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            },
        )
        await resp.prepare(request)

        # Send current state on connect
        await self._sse_send(resp, 'alarm', self.alarm.get_state())

        # Send event history
        if self.consolidator:
            events = self.consolidator.get_recent_events(20)
            await self._sse_send(resp, 'history', {'events': events})

        self.sse_clients.add(resp)
        logger.info("SSE client connected (%d total)", len(self.sse_clients))

        try:
            # Keep connection alive with heartbeat
            while True:
                await asyncio.sleep(25)
                await resp.write(b': keepalive\n\n')
        except (ConnectionResetError, ConnectionAbortedError, asyncio.CancelledError):
            pass
        finally:
            self.sse_clients.discard(resp)
            logger.info("SSE client disconnected (%d remaining)", len(self.sse_clients))

        return resp

    async def sse_broadcast(self, message: dict):
        """Broadcast a message to all SSE clients (called from consolidator/etc)."""
        msg_type = message.get('type', 'event')

        # Update alarm manager for catalog events
        if msg_type == 'event' and message.get('action') == 'create':
            event = message.get('event', {})
            alarm_level = event.get('caracas', {}).get('alarm_level', 'none')
            if LEVEL_ORDER.get(alarm_level, 0) >= LEVEL_ORDER.get('medium', 0):
                self.alarm.on_catalog_event(event)

        if not self.sse_clients:
            return

        dead = set()
        for client in self.sse_clients.copy():
            try:
                await self._sse_send(client, msg_type, message)
            except (ConnectionResetError, ConnectionAbortedError):
                dead.add(client)
            except Exception as e:
                logger.error("SSE broadcast error: %s", e)
                dead.add(client)

        self.sse_clients -= dead

    async def _sse_send(self, resp, event_type: str, data: dict):
        """Send a single SSE event."""
        payload = json.dumps(data, default=str)
        msg = f'event: {event_type}\ndata: {payload}\n\n'
        await resp.write(msg.encode('utf-8'))

    async def _alarm_monitor_loop(self):
        """Monitor detector CFT levels and update alarm state every 1 second."""
        while True:
            await asyncio.sleep(1.0)

            if not self.detector:
                continue

            stations = self.detector.get_station_levels()
            self.alarm.update_waveform(stations, detector=self.detector)

            # Push alarm change to SSE clients
            new_level = self.alarm.output_level
            if new_level != self._prev_alarm_level:
                self._prev_alarm_level = new_level
                if self.sse_clients:
                    alarm_state = self.alarm.get_state()
                    dead = set()
                    for client in self.sse_clients.copy():
                        try:
                            await self._sse_send(client, 'alarm', alarm_state)
                        except Exception:
                            dead.add(client)
                    self.sse_clients -= dead

    async def _status_broadcast_loop(self):
        """Broadcast system status + station levels every 5 seconds."""
        while True:
            await asyncio.sleep(5.0)

            if not self.sse_clients:
                continue

            status = {
                'type': 'status',
                'seedlink': self.seedlink_client.get_status() if self.seedlink_client else {'connected': False},
                'emsc': self.emsc_client.connected if self.emsc_client else False,
                'usgs': self.usgs_poller.connected if self.usgs_poller else False,
                'stations': self.detector.get_station_levels() if self.detector else [],
                'alarm': self.alarm.get_state(),
                'timestamp': time.time(),
            }

            dead = set()
            for client in self.sse_clients.copy():
                try:
                    await self._sse_send(client, 'status', status)
                except Exception:
                    dead.add(client)
            self.sse_clients -= dead

            # Also broadcast waveforms if details panels are open
            if self.detector:
                sent = set()
                for station_id, buf in self.detector.buffers.items():
                    if not station_id.endswith('BHZ'):
                        continue
                    base = '.'.join(station_id.split('.')[:2])
                    if base in sent:
                        continue
                    snippet = self.detector.get_waveform_snippet(station_id, seconds=60)
                    if snippet and len(snippet.get('data', [])) > 10:
                        wf_msg = {'type': 'waveform', **snippet}
                        for client in self.sse_clients.copy():
                            try:
                                await self._sse_send(client, 'waveform', wf_msg)
                            except Exception:
                                pass
                        sent.add(base)
