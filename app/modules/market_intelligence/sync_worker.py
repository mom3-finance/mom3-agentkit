from __future__ import annotations

import threading
import time

from loguru import logger

from app.core.config import settings
from app.modules.market_intelligence import get_market_catalog


class MarketSyncWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not settings.market_ingest_url:
            logger.warning("AgentKit market sync disabled: MARKET_INGEST_URL is empty")
            return
        self._thread = threading.Thread(target=self._run, name="market-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        catalog = get_market_catalog()
        while not self._stop.is_set():
            try:
                result = catalog.sync_live_markets()
                logger.info("AgentKit market sync completed: {}", result)
            except Exception as exc:
                logger.error("AgentKit market sync failed: {}", exc)
            self._stop.wait(settings.market_sync_interval_seconds)


_worker = MarketSyncWorker()


def get_market_sync_worker() -> MarketSyncWorker:
    return _worker
