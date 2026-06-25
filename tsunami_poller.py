"""
Tsunami Warning Poller.

Polls PTWC (Pacific Tsunami Warning Center) Atom/CAP feeds and USGS tsunami flags.
Broadcasts active tsunami alerts to the WebSocket server.
"""

import asyncio
import logging
import time
from typing import Optional
from xml.etree import ElementTree as ET

import aiohttp

logger = logging.getLogger(__name__)

# PTWC feeds (covers Caribbean)
PTWC_ATOM = 'https://www.tsunami.gov/events/xml/PHEBAtom.xml'
PTWC_CAP = 'https://www.tsunami.gov/events/xml/PHEBCAP.xml'
# NTWC feed (covers US coasts)
NTWC_ATOM = 'https://www.tsunami.gov/events/xml/PAAQAtom.xml'

POLL_INTERVAL = 60  # seconds

# Atom/CAP namespaces
NS_ATOM = {'a': 'http://www.w3.org/2005/Atom'}
NS_CAP = {'cap': 'urn:oasis:names:tc:emergency:cap:1.2'}

# Max age of a tsunami message to consider it active
MAX_AGE_HOURS = 24


class TsunamiPoller:
    def __init__(self, broadcast_callback=None):
        self.broadcast = broadcast_callback
        self.connected = False
        self._session: Optional[aiohttp.ClientSession] = None
        self.current_alert: Optional[dict] = None
        self.last_check: float = 0

    async def start(self):
        """Main polling loop."""
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        logger.info("Tsunami poller starting (interval=%ds)", POLL_INTERVAL)

        while True:
            try:
                await self._poll()
                self.connected = True
            except Exception as e:
                logger.error("Tsunami poll error: %s", e)
                self.connected = False

            await asyncio.sleep(POLL_INTERVAL)

    async def _poll(self):
        """Fetch PTWC feeds and check for active tsunami alerts."""
        self.last_check = time.time()

        alert = await self._check_ptwc_atom()

        if alert and alert != self.current_alert:
            self.current_alert = alert
            logger.warning(
                "TSUNAMI ALERT: %s (sent: %s)",
                alert.get('title', '?'), alert.get('sent', '?')
            )
            if self.broadcast:
                await self.broadcast({
                    'type': 'tsunami',
                    'active': True,
                    'alert': alert,
                })
        elif not alert and self.current_alert:
            self.current_alert = None
            logger.info("Tsunami alert cleared")
            if self.broadcast:
                await self.broadcast({
                    'type': 'tsunami',
                    'active': False,
                    'alert': None,
                })

    async def _check_ptwc_atom(self) -> Optional[dict]:
        """Check PTWC Atom feed for active tsunami messages."""
        try:
            async with self._session.get(PTWC_ATOM) as resp:
                if resp.status != 200:
                    logger.warning("PTWC Atom HTTP %d", resp.status)
                    return None
                text = await resp.text()

            root = ET.fromstring(text)
            title_el = root.find('a:title', NS_ATOM)
            updated_el = root.find('a:updated', NS_ATOM)

            if title_el is None:
                return None

            title = (title_el.text or '').strip()

            # Check if title contains tsunami-related keywords
            title_lower = title.lower()
            if 'tsunami' not in title_lower:
                return None

            # Parse update time
            updated = (updated_el.text or '').strip() if updated_el is not None else ''

            # Check age
            try:
                from datetime import datetime, timezone
                if updated:
                    # Parse ISO format
                    dt = datetime.fromisoformat(updated.replace('Z', '+00:00'))
                    age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                    if age_hours > MAX_AGE_HOURS:
                        return None
            except Exception:
                pass  # If we can't parse the date, still report it

            # Determine severity from title
            severity = 'information'
            if 'warning' in title_lower:
                severity = 'warning'
            elif 'watch' in title_lower:
                severity = 'watch'
            elif 'advisory' in title_lower:
                severity = 'advisory'

            # Extract entry details if available
            description = ''
            region = ''
            for entry in root.findall('a:entry', NS_ATOM):
                entry_title = entry.find('a:title', NS_ATOM)
                entry_summary = entry.find('a:summary', NS_ATOM)
                if entry_title is not None:
                    region = (entry_title.text or '').strip()
                if entry_summary is not None:
                    # Extract text from XHTML summary
                    summary_text = ET.tostring(entry_summary, encoding='unicode', method='text')
                    description = summary_text.strip()[:500]

            # Check for energy map link
            energy_map = ''
            for link in root.findall('a:link', NS_ATOM):
                if link.get('title', '') == 'Energy Map':
                    energy_map = link.get('href', '').strip()

            return {
                'title': title,
                'severity': severity,
                'sent': updated,
                'region': region,
                'description': description,
                'energy_map': energy_map,
                'source': 'PTWC',
            }

        except ET.ParseError as e:
            logger.warning("PTWC Atom XML parse error: %s", e)
            return None
        except Exception as e:
            logger.error("PTWC Atom check error: %s", e)
            return None

    def get_status(self) -> dict:
        return {
            'connected': self.connected,
            'last_check': self.last_check,
            'active_alert': self.current_alert is not None,
            'alert': self.current_alert,
        }

    async def stop(self):
        if self._session:
            await self._session.close()
