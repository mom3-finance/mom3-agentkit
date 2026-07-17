from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any

from loguru import logger
from pymongo import ASCENDING, DESCENDING, MongoClient


class PoolSnapshotStore:
    """Mongo-backed current and historical DefiLlama pool snapshots."""

    def __init__(self) -> None:
        uri = os.getenv("MONGODB_URI", "").strip()
        database = os.getenv("MONGODB_DATABASE", "mom3")
        current_name = os.getenv("MONGODB_POOL_COLLECTION", "defillama_pool_current")
        history_name = os.getenv("MONGODB_POOL_HISTORY_COLLECTION", "defillama_pool_snapshots")
        self.enabled = bool(uri)
        self.client = MongoClient(uri, serverSelectionTimeoutMS=3000) if self.enabled else None
        self.current = self.client[database][current_name] if self.client else None
        self.history = self.client[database][history_name] if self.client else None
        if self.enabled:
            self._migrate_history_indexes()
            self.current.create_index([("pool_id", ASCENDING)], unique=True)
            self.current.create_index([("captured_at", DESCENDING)])
            self.history.create_index([("pool_id", ASCENDING), ("captured_at", DESCENDING)])

    def _migrate_history_indexes(self) -> None:
        """Remove the old unique pool_id index from the history collection."""
        if self.history is None:
            return
        for index in self.history.list_indexes():
            key = list(index.get("key", {}).keys())
            if index.get("name") == "pool_id_1" or (key == ["pool_id"] and index.get("unique")):
                try:
                    self.history.drop_index(index["name"])
                    logger.info("MongoDB: removed obsolete unique pool_id history index")
                except Exception as exc:
                    logger.warning(f"MongoDB history index migration skipped: {exc}")

    def _check(self) -> None:
        if not self.enabled or self.client is None:
            raise RuntimeError("MONGODB_URI is not configured for AgentKit.")
        self.client.admin.command("ping")

    def replace_current(self, pools: list[dict]) -> int:
        self._check()
        now = datetime.now(timezone.utc)
        count = 0
        for pool in pools:
            pool_id = str(pool.get("pool") or "")
            if not pool_id:
                continue
            document = {**pool, "pool_id": pool_id, "captured_at": now}
            self.current.replace_one({"pool_id": pool_id}, document, upsert=True)
            self.history.insert_one(document.copy())
            count += 1
        return count

    def latest_pools(self, max_age_seconds: int = 600) -> list[dict]:
        self._check()
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        return list(self.current.find({"captured_at": {"$gte": cutoff}}, {"_id": 0}))

    def history_for(self, pool_id: str, limit: int = 30) -> list[dict]:
        if not self.enabled:
            return []
        return list(self.history.find({"pool_id": pool_id}, {"_id": 0}).sort("captured_at", DESCENDING).limit(limit))[::-1]


_store: PoolSnapshotStore | None = None


def get_pool_snapshot_store() -> PoolSnapshotStore:
    global _store
    if _store is None:
        _store = PoolSnapshotStore()
    return _store
