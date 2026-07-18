from __future__ import annotations

import math
import time
import requests
from typing import Optional

from app.core.config import settings
from app.services.defi_llama import DefiLlamaCollector, get_defillama_collector
from app.services.pool_snapshot_store import PoolSnapshotStore

from .policy import (
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


def _boolean(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() not in {"", "no", "false", "0", "none", "null"}


class MarketCatalog:
    MAX_PERSISTED_MARKETS = 100
    FOCUS_CHAIN_IDS = {101, 8453, 42161}

    """Builds a live catalog from all DefiLlama pools on supported chains."""

    def __init__(self, collector: Optional[DefiLlamaCollector] = None, store: Optional[PoolSnapshotStore] = None) -> None:
        self.collector = collector or get_defillama_collector()
        # Strategy data is intentionally live, like Nuvia. The store argument
        # remains injectable for compatibility with older callers, but is not
        # used as a source of market data.
        self.store = store
        self._backend_cache: dict[tuple, tuple[float, list[dict]]] = {}

    def list_markets(
        self,
        chain_id: int | None = None,
        *,
        execution_only: bool = False,
        protocol: str | None = None,
    ) -> list[dict]:
        supported_chain_ids = getattr(self.collector, "supported_chain_ids", lambda: [101, 8453, 42161])
        chains = [chain_id] if chain_id is not None else [cid for cid in supported_chain_ids() if cid in self.FOCUS_CHAIN_IDS]
        chains = [cid for cid in chains if cid in self.FOCUS_CHAIN_IDS]
        markets: list[dict] = []
        seen: set[str] = set()
        using_backend_catalog = False

        if settings.market_data_url:
            try:
                pools = self._fetch_backend_markets(chain_id, protocol, execution_only)
                using_backend_catalog = True
            except Exception as exc:
                if settings.market_data_required:
                    raise RuntimeError(f"PostgreSQL market API is unavailable: {exc}") from exc
                pools = self.collector.fetch_all_pools()
        elif settings.market_data_required:
            raise RuntimeError("Backend market catalog is required. Configure MARKET_DATA_URL.")
        elif hasattr(self.collector, "fetch_all_pools"):
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
                market = self._backend_row_to_market(pool) if using_backend_catalog else self._normalize(pool, cid)
                if not market or market["market_id"] in seen:
                    continue
                if execution_only and not market["execution"]["enabled"]:
                    continue
                seen.add(market["market_id"])
                markets.append(market)

        return self._rank_and_dedupe(markets)

    def _fetch_backend_markets(self, chain_id: int | None, protocol: str | None, execution_only: bool) -> list[dict]:
        """Read the canonical persisted catalog without re-running discovery normalization."""
        cache_key = (chain_id, protocol, execution_only)
        cached = self._backend_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 20:
            return cached[1]
        if settings.market_catalog_url:
            response = requests.get(
                settings.market_catalog_url,
                headers={"Authorization": f"Bearer {settings.market_ingest_token}"},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("markets", []) if isinstance(payload, dict) else []
            self._backend_cache[cache_key] = (time.monotonic(), rows)
            return rows
        rows: list[dict] = []
        page = 1
        while True:
            params = {"limit": "50", "page": str(page)}
            if chain_id is not None:
                params["chain_id"] = str(chain_id)
            if protocol:
                params["protocol"] = protocol
            if execution_only:
                params["execution_only"] = "true"
            response = requests.get(settings.market_data_url, params=params, timeout=10)
            response.raise_for_status()
            payload = response.json()
            page_rows = payload.get("markets", []) if isinstance(payload, dict) else []
            rows.extend(page_rows)
            pagination = payload.get("pagination", {}) if isinstance(payload, dict) else {}
            if not page_rows or not pagination.get("has_next"):
                break
            page += 1
            if page > 500:
                break
        self._backend_cache[cache_key] = (time.monotonic(), rows)
        return rows

    def build_live_markets(self) -> list[dict]:
        """Fetch, filter, normalize, and score DefiLlama data in AgentKit."""
        pools = self.collector.fetch_all_pools(force=True)
        markets: list[dict] = []
        seen: set[str] = set()
        for chain_id in self.collector.supported_chain_ids():
            chain_name = self.collector.chain_name(chain_id) or ""
            for pool in pools:
                if not self.collector._chain_matches(str(pool.get("chain") or ""), chain_name):
                    continue
                market = self._normalize(pool, chain_id)
                if market and market["market_id"] not in seen:
                    market["source"] = "agentkit-defillama"
                    market["source_url"] = "https://yields.llama.fi/pools"
                    seen.add(market["market_id"])
                    markets.append(market)
        return self._rank_and_dedupe(markets)

    @classmethod
    def _rank_and_dedupe(cls, markets: list[dict]) -> list[dict]:
        """Keep only executable markets with unique pools/contracts."""
        ranked = sorted(
            markets,
            key=lambda item: (
                not bool((item.get("execution") or {}).get("enabled")),
                -float(item.get("opportunity_score") or 0),
                -float(item.get("tvl") or 0),
            ),
        )
        seen_pools: set[str] = set()
        seen_contracts: set[str] = set()
        result: list[dict] = []
        for market in ranked:
            if not bool((market.get("execution") or {}).get("enabled")):
                continue
            pool_id = str(market.get("pool_id") or market.get("market_id") or "").strip().lower()
            if not pool_id or pool_id in seen_pools:
                continue
            execution = market.get("execution") or {}
            contract = str(execution.get("contract") or "").strip().lower()
            contract_key = f"{market.get('chain_id')}:{contract}" if contract else ""
            if contract_key and contract_key in seen_contracts:
                continue
            seen_pools.add(pool_id)
            if contract_key:
                seen_contracts.add(contract_key)
            result.append(market)
            if len(result) >= cls.MAX_PERSISTED_MARKETS:
                break
        return result

    def sync_live_markets(self) -> dict:
        if not settings.market_ingest_url or not settings.market_ingest_token:
            raise RuntimeError("MARKET_INGEST_URL and MARKET_INGEST_TOKEN are required for market sync.")
        markets = self.build_live_markets()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = requests.post(
                    settings.market_ingest_url,
                    json={"markets": markets},
                    headers={"Authorization": f"Bearer {settings.market_ingest_token}", "Content-Type": "application/json"},
                    timeout=60,
                )
                response.raise_for_status()
                self._backend_cache.clear()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"Market ingest failed after 3 attempts: {last_error}") from last_error

    @staticmethod
    def _backend_row_to_pool(row: dict) -> dict:
        return {
            **row,
            "pool": row.get("pool_id") or row.get("market_id"),
            "tvlUsd": row.get("tvl_usd", row.get("tvl")),
            "apyBase": row.get("apy_base"),
            "apyReward": row.get("apy_reward"),
            "apyPct1D": row.get("apy_change_1d"),
            "apyPct7D": row.get("apy_change_7d"),
            "apyPct30D": row.get("apy_change_30d"),
            "ilRisk": "yes" if row.get("impermanent_loss") else "no",
        }

    @staticmethod
    def _backend_row_to_market(row: dict) -> dict:
        execution = row.get("execution") or {}
        execution_enabled = execution.get("enabled") if "enabled" in execution else row.get("execution_enabled", row.get("executionEnabled"))
        return {
            "market_id": row.get("market_id") or row.get("pool_id") or row.get("poolId"),
            "pool_id": row.get("pool_id") or row.get("market_id") or row.get("poolId"),
            "protocol": row.get("protocol") or row.get("project"),
            "project": row.get("project") or str(row.get("protocol") or "").lower().replace(" ", "-"),
            "symbol": row.get("symbol") or row.get("asset"),
            "asset": row.get("asset") or row.get("symbol"),
            "chain": row.get("chain"),
            "chain_id": int(row.get("chain_id") or row.get("chainId") or 0),
            "ua_supported": bool(row.get("ua_supported")),
            "apy": _number(row.get("apy")),
            "apy_base": _number(row.get("apy_base", row.get("apyBase"))),
            "apy_reward": _number(row.get("apy_reward", row.get("apyReward"))),
            "apy_change_1d": _number(row.get("apy_change_1d", row.get("apyChange1d"))),
            "apy_change_7d": _number(row.get("apy_change_7d", row.get("apyChange7d"))),
            "apy_change_30d": _number(row.get("apy_change_30d", row.get("apyChange30d"))),
            "tvl": _number(row.get("tvl_usd", row.get("tvlUsd", row.get("tvl")))),
            "stablecoin": bool(row.get("stablecoin")),
            "exposure": row.get("exposure"),
            "impermanent_loss": row.get("impermanent_loss"),
            "risk_score": _number(row.get("risk_score", row.get("riskScore"))),
            "opportunity_score": _number(row.get("opportunity_score", row.get("opportunityScore"))),
            "execution": {
                "enabled": bool(execution_enabled),
                "actions": execution.get("actions") or [],
                "type": execution.get("type") or row.get("execution_type") or row.get("executionType"),
                "requires_user_confirmation": execution.get("requires_user_confirmation", True),
                "uses_eip7702": execution.get("uses_eip7702", True),
                "contract": execution.get("contract") or row.get("contract_address") or row.get("contractAddress"),
                "asset_address": execution.get("asset_address") or row.get("asset_address") or row.get("assetAddress"),
                "asset_decimals": execution.get("asset_decimals") or row.get("asset_decimals") or row.get("assetDecimals"),
                "position_symbol": execution.get("position_symbol") or row.get("position_symbol") or row.get("positionSymbol"),
            },
            "source": row.get("source") or "postgresql",
            "source_url": row.get("source_url"),
        }

    def get_market(self, market_id: str) -> dict | None:
        # A paginated catalog is not a reliable lookup source. The selected
        # pool can be outside the first page even though it is still live.
        # Resolve the exact ID from the backend catalog first, then fall back
        # to the live collector for deployments without a backend catalog.
        if settings.market_data_url:
            try:
                response = requests.get(
                    f"{settings.market_data_url.rstrip('/')}/{market_id}",
                    timeout=10,
                )
                if response.ok:
                    payload = response.json()
                    row = payload.get("market") if isinstance(payload, dict) else None
                    if isinstance(row, dict):
                        market = self._backend_row_to_market(row)
                        if market and market["market_id"] == market_id:
                            return market
            except Exception:
                # The list/live path below remains the compatibility fallback.
                pass
        if settings.market_data_required:
            return None
        return next((market for market in self.list_markets() if market["market_id"] == market_id), None)

    def get_history(self, market_id: str, range_name: str = "30d") -> list[dict]:
        """Read chart history from the backend snapshot API when configured."""
        if not settings.market_history_url:
            if settings.market_data_required:
                raise RuntimeError("Backend market history URL is required.")
            return []
        url = f"{settings.market_history_url.rstrip('/')}/{market_id}/history"
        response = requests.get(url, params={"range": range_name}, timeout=10)
        response.raise_for_status()
        payload = response.json()
        return payload.get("points", []) if isinstance(payload, dict) else []

    def _normalize(self, pool: dict, chain_id: int) -> dict | None:
        project = str(pool.get("project") or "").lower()
        symbol = str(pool.get("symbol") or "").upper()
        tvl = _number(pool.get("tvlUsd"))
        apy = _number(pool.get("apy"))

        if not project or not symbol or not pool.get("pool"):
            return None
        if chain_id not in self.FOCUS_CHAIN_IDS or not is_stablecoin_symbol(symbol):
            return None
        if tvl < settings.minimum_tvl_usd or apy < 0 or apy > settings.maximum_apy:
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
            "impermanent_loss": _boolean(pool.get("ilRisk", False)),
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
            "source": "agentkit-defillama" if not settings.market_data_url else "postgresql",
            "source_url": settings.market_data_url or "https://yields.llama.fi/pools",
        }


_catalog: MarketCatalog | None = None


def get_market_catalog() -> MarketCatalog:
    global _catalog
    if _catalog is None:
        _catalog = MarketCatalog()
    return _catalog
