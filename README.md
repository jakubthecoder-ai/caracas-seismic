# Caracas Seismic Monitor

Real-time earthquake early warning system for Caracas, Venezuela. Built after the M7.5 earthquake on June 24, 2026.

## Architecture

```
Docker container (python:3.12-slim, asyncio)

  Data Sources:
    SeedLink (IRIS) ──► STA/LTA Detector ──┐
    EMSC WebSocket ────────────────────────┼──► Consolidator (dedup + GMPE)
    USGS GeoJSON poll ─────────────────────┘        │
    Raspberry Shake FDSNWS (post-event) ────────────┘
    EMSC Testimonies (felt reports) ────────────────┘
    PTWC Tsunami feed ─────────────────────────────────┐
                                                       │
  Consolidator ──► broadcast_fanout() ─────────────────┤
                   ├──► WebSocket :8768 (legacy/debug)  │
                   └──► HTTP :8080 (SSR + SSE) ◄────────┘
                        ├── GET /        → base.html (minimal, ~5KB)
                        ├── GET /details → detail fragment (map, events)
                        ├── GET /sse     → real-time push (SSE)
                        └── GET /static/ → JS/CSS

  AlarmManager (Schmitt trigger hysteresis)
    ├── Catalog alarm (USGS/EMSC events → MMI)
    └── Waveform alarm (STA/LTA CFT → sustained + corroborated)
```

### Frontend: Two-Tier UI

**Default view** (~5KB HTML, server-rendered):
- Threat banner (green/yellow/orange/red) with alarm text
- Last event summary (one line)
- "Show details" toggle button
- Mini-seismogram (SVG) during active alarms
- SSE connection for real-time updates

**Details view** (lazy-loaded on demand via fetch):
- Leaflet map with epicenters + station markers (color-coded by CFT)
- Live seismograms (canvas, 60s, all BHZ stations)
- Event table (server-rendered, SSE-updated)
- Caracas impact panel (PGA, MMI, S-wave arrival)

### Data Flow

1. **SeedLink** streams raw waveforms from IRIS (2-5s latency)
2. **Detector** runs recursive STA/LTA on bandpass-filtered data
3. **EMSC WebSocket** pushes catalog events (30-120s latency)
4. **USGS poller** fetches GeoJSON feeds (1-5 min latency)
5. **Consolidator** deduplicates across sources (30s/50km/1.0M window), enriches with BA08 GMPE
6. **AlarmManager** evaluates alarm level from catalog events + waveform CFT
7. **broadcast_fanout()** sends to both WebSocket and SSE clients
8. **Frontend** updates banner/map/seismogram via SSE events

## Data Sources

| Source | Protocol | Latency | Purpose |
|--------|----------|---------|---------|
| IRIS SeedLink | TCP | 2-5s | Raw waveforms, STA/LTA detection |
| EMSC SeismicPortal | WebSocket | 30-120s | Event push |
| USGS GeoJSON | HTTP poll 2s/5m | 1-5 min | Event catalog (hourly + daily feeds) |
| Raspberry Shake | HTTP REST | ~30 min | Post-event waveforms |
| EMSC Testimonies | HTTP REST | minutes | Crowdsourced felt reports |
| PTWC/NTWC | HTTP poll 60s | minutes | Tsunami warnings |

## Alarm System

### Dual-Source with Schmitt Trigger Hysteresis

Two independent alarm sources, output = max(catalog, waveform):

**Catalog alarm** (USGS/EMSC events — zero false positives):
- Based on predicted MMI at Caracas (BA08 GMPE with basin amplification)
- Minimum duration: 5 minutes

| Level | MMI | Effect |
|-------|-----|--------|
| Critical | >= 6.0 | Siren + notification |
| High | >= 5.0 | Siren + beeps + notification |
| Medium | >= 4.0 | Beeps + orange banner |

**Waveform alarm** (STA/LTA — fast, filtered for false positives):

| Level | Entry (ON) | Exit (OFF) | Sound | User-visible? |
|-------|-----------|------------|-------|---------------|
| Elevated | CFT > 2.5 for 5s | CFT < 1.5 for 30s | None | No (internal) |
| Warning | CFT > 3.5 for 5s + corroboration | CFT < 2.0 for 60s | Beeps | Yes |
| Critical | CFT > 5.0 for 10s OR spike > 3.0/s | CFT < 2.5 for 120s | Siren | Yes |

**False positive suppression**:
- **Elevated is silent** — most noise hits this range, user never sees it
- **Schmitt hysteresis** — different ON/OFF thresholds prevent oscillation
- **Sustained requirement** — noise < 1s, real P-waves sustain 5-30s
- **Corroboration** — warning requires 2nd primary station OR catalog match
- **Minimum hold** — once triggered, alarm holds for 2+ minutes

## SeedLink Stations

| Station | Network | Location | Dist to Caracas | Primary? |
|---------|---------|----------|-----------------|----------|
| SDV | IU | Santo Domingo, VE | ~440 km | Yes |
| FUNV | VE | Caracas (FUNVISIS) | ~0 km | Yes |
| GUIV | VE | Guiria, VE | ~500 km | Yes |
| CURV | VE | Curiepe, VE | ~10 km | Yes |
| BAR2 | CM | Barranquilla, CO | ~1100 km | Yes |
| SJG | IU | San Juan, PR | ~900 km | No |
| GRTK | CU | Grand Turk | ~1100 km | No |
| BCIP | CU | Barro Colorado, PA | ~1700 km | No |

## Deploy

### Requirements

- Docker + Docker Compose
- Ports 8080 (HTTP) and 8768 (WebSocket) available

### Quick Start

```bash
git clone git@github.com:jakubthecoder-ai/caracas-seismic.git
cd caracas-seismic
docker compose up -d
```

Open `http://localhost:8080` in a browser. Default language is Spanish.

### Docker Compose

```bash
# Start
docker compose up -d

# Logs
docker compose logs -f

# Restart after code changes (code mounted as volume)
docker compose restart

# Rebuild (after dependency changes)
docker compose down && docker compose build --no-cache && docker compose up -d

# Stop
docker compose down
```

### Data Persistence

SQLite database in Docker volume (`seismic_data`). Events, waveform alerts, and station logs retained for 7 days (auto-pruned hourly).

## File Structure

```
caracas-seismic/
  main.py                  # Orchestrator — launches all tasks
  http_server.py           # HTTP server (SSR + SSE, port 8080)
  ws_server.py             # WebSocket server (port 8768, legacy)
  consolidator.py          # Event dedup + GMPE enrichment
  detector.py              # STA/LTA P-wave detection + station levels
  database.py              # SQLite persistence (WAL mode)
  gmpe.py                  # Boore-Atkinson 2008 GMPE
  i18n.py                  # ES/EN translations
  seedlink_client.py       # IRIS SeedLink waveform stream
  emsc_client.py           # EMSC SeismicPortal WebSocket
  usgs_poller.py           # USGS GeoJSON feed poller
  raspishake_client.py     # Raspberry Shake FDSNWS
  emsc_testimonies.py      # EMSC felt reports
  tsunami_poller.py        # PTWC/NTWC tsunami warnings
  templates/
    base.html              # Minimal view (banner + toggle)
    detail_fragment.html   # Details panel (map, events, seismogram)
  static/
    monitor.js             # SSE client (~150 lines)
  index.html               # Legacy SPA frontend
  Dockerfile               # python:3.12-slim + scientific deps
  docker-compose.yml       # Service definition
  requirements.txt         # Python dependencies
```

## GMPE

Boore-Atkinson 2008 (Earthquake Spectra 24(1)) with Caracas basin amplification:
- Site Vs30: 270 m/s (soft soil, Caracas valley)
- Basin amplification: 1.5x extra factor
- MMI conversion: Worden 2012 (PGA → Modified Mercalli Intensity)
- S-wave travel time: distance / 3.5 km/s

## License

Private project. Not for redistribution.
