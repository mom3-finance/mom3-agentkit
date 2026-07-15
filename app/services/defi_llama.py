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
from typing import Dict, List, Optional

import requests
from loguru import logger

# Particle Universal Account 7702 EVM chains (every entry in the SDK's CHAIN_ID
# enum except Solana 101) -> DefiLlama chain label. This is the authoritative
# scope the AI scans when building a cross-chain strategy.
SUPPORTED_CHAINS: Dict[int, str] = {
    1: "Ethereum",        # ETHEREUM_MAINNET
    10: "Optimism",       # OPTIMISM_MAINNET
    56: "BSC",            # BSC_MAINNET
    137: "Polygon",       # POLYGON_MAINNET
    146: "Sonic",         # SONIC_MAINNET
    169: "Manta",         # MANTA_MAINNET
    196: "XLayer",        # XLAYER_MAINNET (DefiLlama: "XLayer")
    500: "Mantle",        # (reserved/fallback)
    5000: "Mantle",       # MANTLE_MAINNET
    81457: "Blast",       # BLAST_MAINNET
    34443: "Mode",        # MODE_MAINNET
    42161: "Arbitrum",    # ARBITRUM_MAINNET_ONE
    43114: "Avalanche",   # AVALANCHE_MAINNET
    8453: "Base",         # BASE_MAINNET
    59144: "Linea",       # LINEA_MAINNET
    80094: "Berachain",   # BERACHAIN_MAINNET
    143: "Monad",         # MONAD_MAINNET
    999: "HyperEVM",      # HYPEREVM_MAINNET
    9745: "Plasma",       # PLASMA_MAINNET
    1030: "Conflux",      # CONFLUX_ESPACE_MAINNET
    4200: "Merlin",       # MERLIN_MAINNET
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
POOL_CHART_URL = "https://yields.llama.fi/chart/{pool}"
CACHE_TTL_SECONDS = 300  # the global pools payload is large; refresh at most every 5 minutes


class DefiLlamaCollector:
    """Collects yield + TVL data across all supported EVM chains."""

    def __init__(self) -> None:
        self._pools_cache: Optional[List[dict]] = None
        self._pools_fetched_at: float = 0.0

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

        try:
            response = requests.get(YIELDS_URL, timeout=20)
            response.raise_for_status()
            pools = response.json().get("data", []) or []
            self._pools_cache = pools
            self._pools_fetched_at = now
            logger.info(f"DefiLlama: fetched {len(pools)} pools")
            return pools
        except Exception as exc:
            logger.error(f"DefiLlama yields fetch failed: {exc}")
            return self._pools_cache or []

    def supported_chain_ids(self) -> List[int]:
        return sorted(SUPPORTED_CHAINS.keys())

    def chain_name(self, chain_id: int) -> Optional[str]:
        return SUPPORTED_CHAINS.get(chain_id)

    def fetch_chain_yields(self, chain_id: int) -> List[dict]:
        """All DefiLlama yield pools on the given Particle chain id (TVL desc)."""
        name = SUPPORTED_CHAINS.get(chain_id)
        if not name:
            return []
        pools = [p for p in self.fetch_all_pools() if self._chain_matches(p.get("chain", ""), name)]
        pools.sort(key=lambda p: p.get("tvlUsd", 0) or 0, reverse=True)
        return pools

    def fetch_pool_chart(self, pool_id: str) -> List[dict]:
        """Return historical APY/TVL points for one pool from the free API."""
        if not pool_id:
            return []
        try:
            response = requests.get(POOL_CHART_URL.format(pool=pool_id), timeout=15)
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, list) else payload.get("data", []) or []
        except Exception as exc:
            logger.warning(f"DefiLlama pool chart failed for {pool_id}: {exc}")
            return []

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
