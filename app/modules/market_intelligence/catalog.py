from __future__ import annotations

import math
from typing import Optional

from app.core.config import settings
from app.services.defi_llama import DefiLlamaCollector, get_defillama_collector

from .policy import (
    DISCOVERY_PROJECTS,
    PROTOCOL_BASE_RISK,
    PROTOCOL_LABELS,
    execution_market_for,
    is_stablecoin_symbol,
)


def _number(value) -> float:
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


class MarketCatalog:
    """Curates live DefiLlama pools into a safe, executable MVP catalog."""

    def __init__(self, collector: Optional[DefiLlamaCollector] = None) -> None:
        self.collector = collector or get_defillama_collector()

    def list_markets(
        self,
        chain_id: int | None = None,
        *,
        execution_only: bool = False,
    ) -> list[dict]:
        chains = [chain_id] if chain_id is not None else sorted({42161, 8453})
        markets: list[dict] = []
        seen: set[str] = set()

        for cid in chains:
            for pool in self.collector.fetch_chain_yields(cid):
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

        if project not in DISCOVERY_PROJECTS:
            return None
        if not bool(pool.get("stablecoin")) or not is_stablecoin_symbol(symbol):
            return None
        if str(pool.get("exposure") or "single").lower() != "single":
            return None
        if str(pool.get("ilRisk") or "no").lower() not in {"no", "false"}:
            return None
        if tvl < settings.minimum_tvl_usd or apy <= 0 or apy > settings.maximum_apy:
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
            "protocol": PROTOCOL_LABELS.get(project, project),
            "project": project,
            "symbol": symbol,
            "asset": "USDC" if "USDC" in symbol else "USDT",
            "chain": self.collector.chain_name(chain_id) or str(chain_id),
            "chain_id": chain_id,
            "apy": round(apy, 4),
            "apy_base": round(_number(pool.get("apyBase")), 4),
            "apy_reward": round(reward_apy, 4),
            "apy_change_1d": round(_number(pool.get("apyPct1D")), 4),
            "apy_change_7d": round(_number(pool.get("apyPct7D")), 4),
            "apy_change_30d": round(_number(pool.get("apyPct30D")), 4),
            "tvl": round(tvl, 2),
            "stablecoin": True,
            "exposure": "single",
            "impermanent_loss": False,
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
