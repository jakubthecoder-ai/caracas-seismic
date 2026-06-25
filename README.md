# Caracas Seismic Monitor

Real-time earthquake early warning system for Caracas, Venezuela. Built after the M7.5 earthquake on June 24, 2026.

## Architecture

```
Docker container (python:3.12-slim)
  SeedLink (IRIS) ──► STA/LTA Detector ──┐
  EMSC WebSocket ──────────────────────────┼──► Consolidator ──► WebSocket :8768
  USGS GeoJSON poll ───────────────────────┘        │
  Raspberry Shake FDSNWS (post-event) ──────────────┘
  EMSC Testimonies (felt reports) ──────────────────┘
  PTWC Tsunami feed ────────────────────────────────► WebSocket :8768

Frontend (single HTML, ES/EN i18n)
  └── connects to ws://localhost:8768
  └── fallback: direct USGS + EMSC polling
```

## Data Sources

| Source | Protocol | Latency | Purpose |
|--------|----------|---------|---------|
| IRIS SeedLink | TCP | 2-5s | Raw waveforms, STA/LTA detection |
| EMSC SeismicPortal | WebSocket | 30-120s | Event push |
| USGS GeoJSON | HTTP poll 15s | 1-5 min | Event catalog |
| Raspberry Shake | HTTP REST | ~30 min | Post-event waveforms |
| EMSC Testimonies | HTTP REST | minutes | Crowdsourced felt reports |
| PTWC/NTWC | HTTP poll 60s | minutes | Tsunami warnings |

## Deploy

### Requirements

- Docker + Docker Compose
- Port 8768 available

### Quick Start

```bash
git clone git@github.com:jakubthecoder-ai/caracas-seismic.git
cd caracas-seismic
docker compose up -d
```

Open `index.html` in a browser. The frontend connects to `ws://localhost:8768` for backend data.

### Docker Compose

```bash
# Start
docker compose up -d

# Logs
docker compose logs -f

# Rebuild after code changes
docker compose down && docker compose build --no-cache && docker compose up -d

# Stop
docker compose down
```

### Data Persistence

SQLite database is stored in a Docker volume (`seismic_data`). Events, waveform alerts, and station logs are retained for 7 days (auto-pruned hourly).

### SeedLink Stations

| Station | Network | Location | Dist to Caracas |
|---------|---------|----------|-----------------|
| SDV | IU | Santo Domingo, VE | ~440 km |
| SJG | IU | San Juan, PR | ~900 km |
| BAR2 | CM | Barranquilla, CO | ~1100 km |
| GRTK | CU | Grand Turk | ~1100 km |
| BCIP | CU | Barro Colorado, PA | ~1700 km |

## Alarm Levels

Alarm severity is based on **predicted MMI at Caracas** using the Boore-Atkinson 2008 GMPE with basin amplification (Vs30=270, 1.5x basin factor):

| Level | MMI | Effect |
|-------|-----|--------|
| Critical | >= 6.0 | Siren + screen flash + notification |
| High | >= 5.0 | Siren + beeps + notification |
| Medium | >= 4.0 | Beeps + yellow banner |
| Low | >= 3.0 | Single beep |
| Info | >= 2.0 | Log only |

## Frontend

Single `index.html` file with built-in EN/ES language switcher. No build step required.

Features:
- Live seismogram display (all SeedLink stations)
- Isoseismal felt radius circles (MMI II-VIII)
- Tsunami coastal threat zone visualization (12 Caribbean zones)
- Event log with catalog events + waveform alerts tabs
- BA08 GMPE impact analysis for Caracas
- Audio alerts (siren + beeps)
- Browser notifications
- Mobile-responsive with slidable panels

## License

Private project. Not for redistribution.
