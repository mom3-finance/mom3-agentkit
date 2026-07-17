"""Multi-chain DefiLlama collector for the mom3 agentkit.

Ports the Nuvia `data/collectors/defi_llama.py` idea but is:
  * config-free (no YAML) — the chain map is a Python dict,
  * multi-chain — covers every Particle-supported EVM chain,
  * lightly cached — pools are fetched at most once per CACHE_TTL_SECONDS.

DefiLlama's free APIs:
  * https://yields.llama.fi/pools          — every yield pool, with `chain`, `project`, `apy`, `tvlUsd`.
  * https://api.llama.fi/protocol/{slug}   — historical TVL per chain.
"""
from __future__ import annotations

import time
from threading import Lock
from typing import Dict, List, Optional

import requests
from loguru import logger

# Particle Universal Account v2-supported chains -> DefiLlama chain label.
# This is the market-discovery boundary: pools outside this set are never
# returned to AgentKit and therefore cannot be recommended by the product.
SUPPORTED_CHAINS: Dict[int, str] = {
    101: "Solana",
    1: "Ethereum",
    56: "BSC",
    196: "XLayer",
    42161: "Arbitrum",    # ARBITRUM_MAINNET_ONE
    8453: "Base",
}

# Chain labels that DefiLlama sometimes emits as alternates.
_CHAIN_ALIASES = {
    "arbitrum one": "arbitrum",
    "polygon poe": "polygon",
    "polygon poe (matic)": "polygon",
    "bnb smart chain (bep20)": "bsc",
    "bnb chain": "bsc",
    "binance": "bsc",
    "avax": "avalanche",
    "c-chain": "avalanche",
    "avalanche c": "avalanche",
    "ethereum mainnet": "ethereum",
    "x layer": "xlayer",
    "x-layer": "xlayer",
    "okxchain": "xlayer",
    "conflux espace": "conflux",
    "merlin chain": "merlin",
    "berachain berna": "berachain",
}

YIELDS_URL = "https://yields.llama.fi/pools"
PROTOCOL_URL = "https://api.llama.fi/protocol/{slug}"
CHAINS_URL = "https://api.llama.fi/v2/chains"
POOL_CHART_URL = "https://yields.llama.fi/chart/{pool}"
CACHE_TTL_SECONDS = 300  # refresh at most every 5 minutes to balance freshness and rate limits
CHART_CACHE_TTL_SECONDS = 900


class DefiLlamaCollector:
    """Collects yield + TVL data across all supported EVM chains."""

    def __init__(self) -> None:
        self._pools_cache: Optional[List[dict]] = None
        self._pools_fetched_at: float = 0.0
        self._pools_by_chain: Dict[str, List[dict]] = {}
        self._chains_cache: Optional[List[dict]] = None
        self._chains_fetched_at: float = 0.0
        self._chart_cache: Dict[str, tuple[float, List[dict]]] = {}
        self._chart_backoff_until: Dict[str, float] = {}
        self._pools_lock = Lock()
        self._chart_lock = Lock()
        self._http = requests.Session()

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _normalize_chain(name: str) -> str:
        key = name.strip().lower()
        return _CHAIN_ALIASES.get(key, key)

    def _chain_matches(self, pool_chain: str, target: str) -> bool:
        """Loose chain comparison tolerant of DefiLlama's label variants."""
        p = self._normalize_chain(pool_chain)
        t = target.strip().lower()
        return p == t or p.startswith(t) or t.startswith(p)

    def fetch_all_pools(self, *, force: bool = False) -> List[dict]:
        """Return every yield pool DefiLlama knows about (cached for CACHE_TTL_SECONDS)."""
        now = time.monotonic()
        if not force and self._pools_cache is not None and (now - self._pools_fetched_at) < CACHE_TTL_SECONDS:
            return self._pools_cache

        # Single-flight: concurrent API requests share one upstream request.
        with self._pools_lock:
            now = time.monotonic()
            if not force and self._pools_cache is not None and (now - self._pools_fetched_at) < CACHE_TTL_SECONDS:
                return self._pools_cache
            try:
                response = self._http.get(YIELDS_URL, timeout=20)
                response.raise_for_status()
                pools = response.json().get("data", []) or []
                by_chain: Dict[str, List[dict]] = {}
                for pool in pools:
                    key = self._normalize_chain(str(pool.get("chain") or ""))
                    by_chain.setdefault(key, []).append(pool)
                for values in by_chain.values():
                    values.sort(key=lambda p: p.get("tvlUsd", 0) or 0, reverse=True)
                self._pools_cache = pools
                self._pools_by_chain = by_chain
                self._pools_fetched_at = now
                logger.info(f"DefiLlama: fetched {len(pools)} pools and indexed {len(by_chain)} chains")
                return pools
            except Exception as exc:
                logger.error(f"DefiLlama yields fetch failed: {exc}")
                return self._pools_cache or []

    def supported_chain_ids(self) -> List[int]:
        return sorted(SUPPORTED_CHAINS.keys())

    def fetch_chain_tvl(self, *, force: bool = False) -> List[dict]:
        """Return current TVL for all chains from DefiLlama's public TVL API."""
        now = time.monotonic()
        if not force and self._chains_cache is not None and (now - self._chains_fetched_at) < CACHE_TTL_SECONDS:
            return self._chains_cache

        with self._pools_lock:
            now = time.monotonic()
            if not force and self._chains_cache is not None and (now - self._chains_fetched_at) < CACHE_TTL_SECONDS:
                return self._chains_cache
            try:
                response = self._http.get(CHAINS_URL, timeout=15)
                response.raise_for_status()
                payload = response.json()
                chains = payload if isinstance(payload, list) else []
                self._chains_cache = chains
                self._chains_fetched_at = now
                logger.info(f"DefiLlama: fetched TVL for {len(chains)} chains")
                return chains
            except Exception as exc:
                logger.warning(f"DefiLlama chain TVL fetch failed: {exc}")
                return self._chains_cache or []

    def fetch_supported_chain_tvl(self) -> Dict[int, dict]:
        """Map supported Particle chain IDs to DefiLlama's current chain TVL."""
        result: Dict[int, dict] = {}
        for item in self.fetch_chain_tvl():
            name = str(item.get("name") or "")
            for chain_id, chain_name in SUPPORTED_CHAINS.items():
                if self._chain_matches(name, chain_name):
                    result[chain_id] = {
                        "chain_id": chain_id,
                        "chain": chain_name,
                        "tvl": float(item.get("tvl") or 0),
                        "token_symbol": item.get("tokenSymbol"),
                        "source": "defillama-chain-tvl",
                    }
                    break
        return result

    def chain_name(self, chain_id: int) -> Optional[str]:
        return SUPPORTED_CHAINS.get(chain_id)

    def fetch_chain_yields(self, chain_id: int) -> List[dict]:
        """All DefiLlama yield pools on the given Particle chain id (TVL desc)."""
        name = SUPPORTED_CHAINS.get(chain_id)
        if not name:
            return []
        self.fetch_all_pools()
        target = self._normalize_chain(name)
        pools = self._pools_by_chain.get(target)
        if pools is not None:
            return pools
        # Keep tolerant matching for aliases not known by the index.
        return [p for p in self._pools_cache or [] if self._chain_matches(p.get("chain", ""), name)]

    def fetch_pool_chart(self, pool_id: str) -> List[dict]:
        """Return historical APY/TVL points for one pool from the free API."""
        if not pool_id:
            return []
        now = time.monotonic()
        if now < self._chart_backoff_until.get(pool_id, 0):
            cached = self._chart_cache.get(pool_id)
            return cached[1] if cached else []
        cached = self._chart_cache.get(pool_id)
        if cached and now - cached[0] < CHART_CACHE_TTL_SECONDS:
            return cached[1]
        with self._chart_lock:
            cached = self._chart_cache.get(pool_id)
            if cached and now - cached[0] < CHART_CACHE_TTL_SECONDS:
                return cached[1]
            try:
                response = self._http.get(POOL_CHART_URL.format(pool=pool_id), timeout=15)
                response.raise_for_status()
                payload = response.json()
                points = payload if isinstance(payload, list) else payload.get("data", []) or []
                self._chart_cache[pool_id] = (time.monotonic(), points)
                return points
            except Exception as exc:
                if getattr(exc, "response", None) is not None and exc.response.status_code == 429:
                    retry_after = exc.response.headers.get("Retry-After")
                    try:
                        delay = max(300, min(1800, int(retry_after)))
                    except (TypeError, ValueError):
                        delay = 900
                    self._chart_backoff_until[pool_id] = time.monotonic() + delay
                    logger.warning(f"DefiLlama chart rate-limited for {pool_id}; backing off {delay}s")
                    return cached[1] if cached else []
                logger.warning(f"DefiLlama pool chart failed for {pool_id}: {exc}")
                return cached[1] if cached else []

    def fetch_chain_protocol_summary(self, chain_id: int, limit: int = 12) -> List[dict]:
        """Highest-TVL pool per protocol on a chain -> compact summary list."""
        pools = self.fetch_chain_yields(chain_id)
        by_project: Dict[str, dict] = {}
        for pool in pools:
            project = pool.get("project") or pool.get("symbol") or "unknown"
            existing = by_project.get(project)
            if existing is None or (pool.get("tvlUsd", 0) or 0) > (existing.get("tvlUsd", 0) or 0):
                by_project[project] = pool

        summary: List[dict] = []
        for project, pool in by_project.items():
            tvl = pool.get("tvlUsd", 0) or 0
            apy = pool.get("apy", 0) or 0
            # Coerce every value to a native JSON-safe type. DefiLlama occasionally
            # returns numpy-typed scalars (e.g. numpy.bool / numpy.float) which
            # FastAPI's jsonable_encoder cannot serialize and would 500 the response.
            exposure = pool.get("exposure")
            il_risk = pool.get("ilRisk")
            summary.append({
                "protocol": str(project),
                "pool": str(pool.get("symbol", "") or ""),
                "pool_id": str(pool.get("pool", "") or ""),
                "apy": float(apy) or 0.0,
                "apy_base": float(pool.get("apyBase", 0) or 0) or 0.0,
                "apy_reward": float(pool.get("apyReward", 0) or 0) or 0.0,
                "apy_change_1d": float(pool.get("apyPct1D", 0) or 0) or 0.0,
                "apy_change_7d": float(pool.get("apyPct7D", 0) or 0) or 0.0,
                "apy_change_30d": float(pool.get("apyPct30D", 0) or 0) or 0.0,
                "tvl": float(tvl) or 0.0,
                "stablecoin": bool(pool.get("stablecoin", False)),
                "exposure": str(exposure) if exposure is not None else None,
                "impermanent_loss": bool(il_risk) if il_risk is not None else None,
                "chain": SUPPORTED_CHAINS[chain_id],
                "chain_id": int(chain_id),
                "source": "defillama",
            })
        summary.sort(key=lambda s: s["tvl"], reverse=True)
        return summary[:limit]

    def get_all_supported_chains_summary(self, limit_per_chain: int = 6) -> Dict[int, List[dict]]:
        """Compact per-chain protocol summaries for every supported EVM chain."""
        return {cid: self.fetch_chain_protocol_summary(cid, limit=limit_per_chain)
                for cid in SUPPORTED_CHAINS}

    def fetch_protocol_historical_tvl(self, slug: str, chain: Optional[str] = None) -> List[dict]:
        """Historical TVL points for a protocol slug and optional chain."""
        try:
            response = requests.get(PROTOCOL_URL.format(slug=slug), timeout=15)
            response.raise_for_status()
            data = response.json()
            tvl_data = data.get("chainTvls", {})
            target = self._normalize_chain(chain) if chain else None
            for chain_key, chain_tvls in tvl_data.items():
                normalized_key = self._normalize_chain(chain_key.split("-borrowed")[0])
                if target and normalized_key != target:
                    continue
                points = chain_tvls.get("tvl", [])
                if points:
                    return [{"date": p.get("date"), "tvl": p.get("totalLiquidityUSD")} for p in points]
            return []
        except Exception as exc:
            logger.error(f"DefiLlama historical TVL for {slug} failed: {exc}")
            return []

    def fetch_protocol_tvl_change(self, slug: str, chain: str) -> float:
        """Return the latest day-over-day TVL change percentage."""
        points = self.fetch_protocol_historical_tvl(slug, chain)
        if len(points) < 2:
            return 0.0
        previous = float(points[-2].get("tvl") or 0)
        current = float(points[-1].get("tvl") or 0)
        if previous <= 0:
            return 0.0
        return round(((current - previous) / previous) * 100, 4)


# Singleton ---------------------------------------------------------------------
_collector: Optional[DefiLlamaCollector] = None


def get_defillama_collector() -> DefiLlamaCollector:
    global _collector
    if _collector is None:
        _collector = DefiLlamaCollector()
    return _collector
