from __future__ import annotations

import math
from typing import Optional

from app.services.defi_llama import DefiLlamaCollector, get_defillama_collector
from app.services.pool_snapshot_store import PoolSnapshotStore

from .policy import (
    PROTOCOL_BASE_RISK,
    PROTOCOL_LABELS,
    execution_market_for,
)


def _number(value) -> float:
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


class MarketCatalog:
    """Builds a live catalog from all DefiLlama pools on supported chains."""

    def __init__(self, collector: Optional[DefiLlamaCollector] = None, store: Optional[PoolSnapshotStore] = None) -> None:
        self.collector = collector or get_defillama_collector()
        # Strategy data is intentionally live, like Nuvia. The store argument
        # remains injectable for compatibility with older callers, but is not
        # used as a source of market data.
        self.store = store

    def list_markets(
        self,
        chain_id: int | None = None,
        *,
        execution_only: bool = False,
        protocol: str | None = None,
    ) -> list[dict]:
        supported_chain_ids = getattr(self.collector, "supported_chain_ids", lambda: [42161, 8453])
        chains = [chain_id] if chain_id is not None else supported_chain_ids()
        markets: list[dict] = []
        seen: set[str] = set()

        # DefiLlamaCollector provides the five-minute in-memory cache and
        # single-flight request protection. MongoDB is not part of strategy.
        if hasattr(self.collector, "fetch_all_pools"):
            pools = self.collector.fetch_all_pools()
        else:
            # Compatibility for lightweight collectors used by integrations/tests.
            pools = [pool for cid in chains for pool in self.collector.fetch_chain_yields(cid)]

        for cid in chains:
            chain_name = self.collector.chain_name(cid)
            if hasattr(self.collector, "_chain_matches"):
                chain_pools = [p for p in pools if self.collector._chain_matches(str(p.get("chain") or ""), chain_name or "")]
            else:
                chain_pools = self.collector.fetch_chain_yields(cid)
            for pool in chain_pools:
                if protocol and str(pool.get("project") or "").lower() != protocol.lower():
                    continue
                market = self._normalize(pool, cid)
                if not market or market["market_id"] in seen:
                    continue
                if execution_only and not market["execution"]["enabled"]:
                    continue
                seen.add(market["market_id"])
                markets.append(market)

        markets.sort(
            key=lambda item: (
                not item["execution"]["enabled"],
                -item["opportunity_score"],
                -item["tvl"],
            )
        )
        return markets

    def get_market(self, market_id: str) -> dict | None:
        return next((market for market in self.list_markets() if market["market_id"] == market_id), None)

    def _normalize(self, pool: dict, chain_id: int) -> dict | None:
        project = str(pool.get("project") or "").lower()
        symbol = str(pool.get("symbol") or "").upper()
        tvl = _number(pool.get("tvlUsd"))
        apy = _number(pool.get("apy"))

        if not project or not symbol or not pool.get("pool"):
            return None

        market_id = str(pool.get("pool") or f"{project}:{chain_id}:{symbol}")
        execution = execution_market_for(market_id, project, symbol, chain_id)
        reward_apy = _number(pool.get("apyReward"))
        base_risk = PROTOCOL_BASE_RISK.get(project, 5.0)
        liquidity_penalty = 0.0 if tvl >= 25_000_000 else 0.6 if tvl >= 5_000_000 else 1.2
        reward_penalty = min(1.2, reward_apy / max(apy, 0.01))
        risk_score = round(min(10.0, base_risk + liquidity_penalty + reward_penalty), 2)
        tvl_score = min(3.0, math.log10(max(tvl, 1)) - 5)
        opportunity_score = round((apy * 1.4) + tvl_score - (risk_score * 0.8), 4)
        prediction = pool.get("predictions") if isinstance(pool.get("predictions"), dict) else {}

        return {
            "market_id": market_id,
            "pool_id": str(pool.get("pool") or ""),
            "protocol": PROTOCOL_LABELS.get(project, project.replace("-", " ").title()),
            "project": project,
            "symbol": symbol,
            "asset": symbol.split("-")[0].split("/")[0].strip() or symbol,
            "chain": self.collector.chain_name(chain_id) or str(chain_id),
            "chain_id": chain_id,
            "ua_supported": chain_id in self.collector.supported_chain_ids(),
            "apy": round(apy, 4),
            "apy_base": round(_number(pool.get("apyBase")), 4),
            "apy_reward": round(reward_apy, 4),
            "apy_change_1d": round(_number(pool.get("apyPct1D")), 4),
            "apy_change_7d": round(_number(pool.get("apyPct7D")), 4),
            "apy_change_30d": round(_number(pool.get("apyPct30D")), 4),
            "tvl": round(tvl, 2),
            "stablecoin": bool(pool.get("stablecoin", False)),
            "exposure": str(pool.get("exposure") or "unknown"),
            "impermanent_loss": bool(pool.get("ilRisk", False)),
            "risk_score": risk_score,
            "opportunity_score": opportunity_score,
            "prediction": {
                "class": prediction.get("predictedClass"),
                "probability": _number(prediction.get("predictedProbability")),
            },
            "execution": {
                "enabled": execution is not None,
                "actions": ["supply", "withdraw"] if execution else [],
                "type": execution.execution_type if execution else None,
                "requires_user_confirmation": True,
                "uses_eip7702": True,
                "contract": execution.contract if execution else None,
                "asset_address": execution.asset_address if execution else None,
                "asset_decimals": execution.asset_decimals if execution else None,
                "position_symbol": execution.position_symbol if execution else None,
            },
            "source": "defillama-live",
            "source_url": "https://yields.llama.fi/pools",
        }


_catalog: MarketCatalog | None = None


def get_market_catalog() -> MarketCatalog:
    global _catalog
    if _catalog is None:
        _catalog = MarketCatalog()
    return _catalog
