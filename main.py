"""
Seismic Monitor — Main Orchestrator.

Launches all data sources, detector, consolidator, and WebSocket server
as concurrent asyncio tasks.

    docker compose up     → starts everything
    ws://localhost:8768   → frontend connection
"""

import asyncio
import logging
import signal
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)-20s] %(levelname)-7s %(message)s',
    datefmt='%H:%M:%S',
    stream=sys.stdout,
)
logger = logging.getLogger('main')


async def main():
    logger.info("=" * 60)
    logger.info("  SEISMIC MONITOR — Caracas Earthquake Early Warning")
    logger.info("  Data sources: IRIS SeedLink + EMSC WS + USGS + RS + Felt")
    logger.info("  WebSocket server: ws://0.0.0.0:8768")
    logger.info("  HTTP server:      http://0.0.0.0:8080")
    logger.info("=" * 60)

    # Shared event queue — all sources push here
    event_queue = asyncio.Queue()

    # Initialize SQLite database
    from database import Database
    db = Database()

    # Initialize detector (STA/LTA)
    from detector import Detector
    detector = Detector()

    # Initialize data sources
    from seedlink_client import SeedLinkClient
    from emsc_client import EMSCClient
    from usgs_poller import USGSPoller
    from raspishake_client import RaspiShakeClient

    seedlink = SeedLinkClient(detector)
    emsc = EMSCClient(event_queue)
    usgs = USGSPoller(event_queue)

    # Initialize consolidator with database
    from consolidator import Consolidator
    consolidator = Consolidator(event_queue, db=db)

    # Initialize supplementary sources
    raspishake = RaspiShakeClient(consolidator)

    from emsc_testimonies import EMSCTestimoniesClient
    from tsunami_poller import TsunamiPoller

    # Initialize WebSocket server
    from ws_server import WSServer
    ws_server = WSServer(
        consolidator=consolidator,
        detector=detector,
        seedlink_client=seedlink,
        emsc_client=emsc,
        usgs_poller=usgs,
    )

    # Initialize HTTP server with SSE + AlarmManager
    from http_server import HTTPServer, AlarmManager
    alarm_manager = AlarmManager()
    http_server = HTTPServer(
        consolidator=consolidator,
        detector=detector,
        alarm_manager=alarm_manager,
        seedlink_client=seedlink,
        emsc_client=emsc,
        usgs_poller=usgs,
    )

    # Broadcast fanout: send to both WS (legacy) and SSE clients
    async def broadcast_fanout(message):
        await ws_server.broadcast(message)
        await http_server.sse_broadcast(message)

    consolidator.broadcast = broadcast_fanout

    testimonies = EMSCTestimoniesClient(
        consolidator=consolidator,
        broadcast_callback=broadcast_fanout,
    )

    tsunami = TsunamiPoller(broadcast_callback=broadcast_fanout)

    # Detector callback — push detected events to queue (from thread)
    # Capture event loop reference before spawning threads
    loop = asyncio.get_running_loop()

    def on_seedlink_detection(event):
        loop.call_soon_threadsafe(event_queue.put_nowait, event)

    detector.event_callback = on_seedlink_detection

    # Per-second station log callback (from SeedLink thread → DB)
    def on_station_log(station, timestamp, cft, amplitude, sps):
        try:
            db.save_station_log(station, timestamp, cft, amplitude, sps)
        except Exception as e:
            logger.error("Station log save error: %s", e)

    detector.log_callback = on_station_log

    # Periodic database pruning
    async def prune_loop():
        while True:
            await asyncio.sleep(3600)  # every hour
            try:
                db.prune_old_data(days=7)
            except Exception as e:
                logger.error("DB prune error: %s", e)

    # Launch all tasks
    tasks = [
        asyncio.create_task(seedlink.start(), name='seedlink'),
        asyncio.create_task(emsc.start(), name='emsc'),
        asyncio.create_task(usgs.start(), name='usgs'),
        asyncio.create_task(consolidator.start(), name='consolidator'),
        asyncio.create_task(ws_server.start(), name='ws_server'),
        asyncio.create_task(http_server.start(), name='http_server'),
        asyncio.create_task(raspishake.start(), name='raspishake'),
        asyncio.create_task(testimonies.start(), name='testimonies'),
        asyncio.create_task(tsunami.start(), name='tsunami'),
        asyncio.create_task(prune_loop(), name='db_prune'),
    ]

    logger.info("All %d tasks launched", len(tasks))

    # Handle shutdown
    shutdown_event = asyncio.Event()

    def handle_signal():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_signal)

    # Wait for shutdown or task failure
    done, pending = await asyncio.wait(
        tasks + [asyncio.create_task(shutdown_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Check if any task failed
    for task in done:
        if task.get_name() != 'shutdown_event' and task.exception():
            logger.error("Task %s failed: %s", task.get_name(), task.exception())

    # Cleanup
    logger.info("Shutting down...")
    for task in pending:
        task.cancel()

    await asyncio.gather(*pending, return_exceptions=True)

    await seedlink.stop()
    await emsc.stop()
    await usgs.stop()
    await raspishake.stop()
    await testimonies.stop()
    await tsunami.stop()
    db.close()

    logger.info("Shutdown complete")


if __name__ == '__main__':
    asyncio.run(main())
