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

## SeedLink Station Network

52 SeedLink subscriptions covering the Caracas fault system (San Sebastian, Bocono, El Pilar) within ~1000 km of Caracas. Stations are grouped by priority:

### Required (GSN — always available on IRIS)

| Station | Network | Location | Dist |
|---------|---------|----------|------|
| SDV | IU | Santo Domingo, Merida | 440 km |
| SJG | IU | San Juan, Puerto Rico | 850 km |
| BCIP | CU | Barro Colorado, Panama | 1700 km |
| GRTK | CU | Grand Turk | 1100 km |

### Venezuelan FUNVISIS Stations (30+ channels)

Stations sorted by distance from Caracas, covering all three major fault segments:

- **Near Caracas (<100 km):** FUNV (10 km), TACV (43 km), BIRV (69 km), BENV (97 km)
- **100-300 km:** TURV, CUPV, TINV, MERV, ORCV, BAUV, JACV, PCRV, TERV, IBAV
- **300-700 km:** MACV, SANV, SIQV, CUNV, CURV, XCAR, QARV, CRUV, DABV, ORIV, ITEV, GUNV, XYAG, GUIV, VIGV, SOCV, MCQV, CAPV

Both BHZ and HHZ channels subscribed (many VE stations only have high-gain HHZ).

### Regional Stations

| Station | Network | Location | Dist |
|---------|---------|----------|------|
| BAR2 | CM | Barranquilla, Colombia | 868 km |
| OCA | CM | Ocana, Colombia | 748 km |
| ACPR | PR | Aruba/Curacao | 405 km |
| GRGR | CU | Grenada | 600 km |
| BBGH | CU | Barbados | 851 km |

### False Positive Suppression

Primary stations (VE.\*, IU.SDV, CM.BAR2, CM.OCA, PR.ACPR) can trigger single-station alerts. Distant stations require corroboration: a primary station also triggered, or 2+ stations in coincidence window (30s).

## Alarm Levels

Alarm severity is based on **predicted MMI at Caracas** using the Boore-Atkinson 2008 GMPE with basin amplification (Vs30=270, 1.5x basin factor):

| Level | MMI | Effect |
|-------|-----|--------|
| Critical | >= 6.0 | Siren + screen flash + notification |
| High | >= 5.0 | Siren + beeps + notification |
| Medium | >= 4.0 | Beeps + banner |
| Low | >= 3.0 | Single beep |
| Info | >= 2.0 | Log only |

### Confirmed vs Unconfirmed Alerts

The threat banner uses color to indicate confirmation status:

- **Red (CONFIRMED)** — catalog event from EMSC/USGS, or multi-station SeedLink coincidence (2+ stations)
- **Yellow (UNCONFIRMED)** — single-station SeedLink trigger without corroboration

Sound intensity is still based on severity level regardless of confirmation status.

## Frontend

Single `index.html` file with built-in EN/ES language switcher (no page reload). No build step required.

### Features

- **2-column layout** — always-visible map + sidebar with impact panel, stats, controls
- **Live seismogram display** — all active SeedLink stations (BHZ waveforms)
- **Isoseismal felt radius circles** — BA08 GMPE, MMI II-VIII with binary search for radius, persistent on click
- **Tectonic fault lines** — San Sebastian, Bocono, El Pilar, Oca-Ancon faults + Caribbean plate boundary (toggleable)
- **Station Manager** — collapsible panel, sortable (distance/code/city/lat/lon), per-station alert enable/disable with localStorage persistence
- **Tsunami coastal threat zone** — 12 Caribbean zones from PTWC feed
- **Event list** — catalog events + waveform alerts tabs, click to pan map to epicenter
- **Caracas impact panel** — PGA, MMI, S-wave arrival estimate
- **Progressive event ghosting** — 24h opacity decay on map markers
- **Audio alerts** — siren + beeps, with test alarm button
- **Browser notifications** — desktop push notifications for significant events
- **Screen wake-lock** — prevents device sleep during monitoring
- **Backend status indicators** — SSE, SeedLink, EMSC, USGS connection dots
- **Mobile-responsive** — collapsible sidebar, touch-friendly controls

## License

Private project. Not for redistribution.
