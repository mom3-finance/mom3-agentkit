"""Shared no-API-key adapter primitives for yield market discovery."""
from __future__ import annotations

from typing import Iterable, Optional

from app.services.defi_llama import DefiLlamaCollector, get_defillama_collector


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


class DefiLlamaAdapter:
    """Protocol adapter backed by DefiLlama's free, public pools endpoint."""

    adapter_name = "defillama"
    projects: tuple[str, ...] = ()

    def __init__(self, collector: Optional[DefiLlamaCollector] = None) -> None:
        self.collector = collector or get_defillama_collector()

    def matches(self, project: str) -> bool:
        normalized = project.lower().replace("_", "-")
        return any(alias in normalized for alias in self.projects)

    def _chains(self, chain_id: Optional[int]) -> Iterable[int]:
        return [chain_id] if chain_id is not None else self.collector.supported_chain_ids()

    def fetch_markets(self, chain_id: Optional[int] = None) -> list[dict]:
        markets: list[dict] = []
        for cid in self._chains(chain_id):
            for pool in self.collector.fetch_chain_yields(cid):
                project = str(pool.get("project") or "unknown")
                if not self.matches(project):
                    continue
                markets.append(self.normalize(pool, cid, project))
        return markets

    def normalize(self, pool: dict, chain_id: int, project: str) -> dict:
        return {
            "adapter": self.adapter_name,
            "pool_id": str(pool.get("pool") or ""),
            "protocol": project,
            "symbol": str(pool.get("symbol") or "Yield pool"),
            "chain": self.collector.chain_name(chain_id),
            "chain_id": chain_id,
            "apy": _number(pool.get("apy")),
            "apy_base": _number(pool.get("apyBase")),
            "apy_reward": _number(pool.get("apyReward")),
            "apy_change_1d": _number(pool.get("apyPct1D")),
            "tvl": _number(pool.get("tvlUsd")),
            "stablecoin": bool(pool.get("stablecoin", False)),
            "exposure": pool.get("exposure"),
            "impermanent_loss": pool.get("ilRisk"),
            "underlying_tokens": [str(token) for token in (pool.get("underlyingTokens") or [])],
            "source": "defillama",
            "source_url": "https://yields.llama.fi/pools",
        }
