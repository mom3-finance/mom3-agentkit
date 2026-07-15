from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from app.modules.market_intelligence import MarketCatalog, get_market_catalog
from app.services.position_reader import PositionReader

from .engine import DUST_USD, PortfolioIntelligenceEngine


class PortfolioIntelligenceService:
    """Combines live protocol positions with wallet assets before scoring."""

    def __init__(
        self,
        catalog: MarketCatalog | None = None,
        position_reader: PositionReader | None = None,
        engine: PortfolioIntelligenceEngine | None = None,
    ) -> None:
        self.catalog = catalog or get_market_catalog()
        self.position_reader = position_reader or PositionReader()
        self.engine = engine or PortfolioIntelligenceEngine()

    def analyze(self, user_address: str, wallet_assets: list[dict]) -> dict:
        markets = self.catalog.list_markets(execution_only=True)
        positions: list[dict] = []
        failures: list[dict] = []

        if markets:
            worker_count = min(6, len(markets))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(self.position_reader.read, market, user_address): market
                    for market in markets
                }
                for future in as_completed(futures):
                    market = futures[future]
                    try:
                        position = future.result()
                        supplied = float(position.get("supplied_balance") or 0)
                        if supplied < DUST_USD:
                            continue
                        positions.append({
                            **position,
                            "asset": position.get("asset", {}).get("symbol") or market["asset"],
                            "amount_in_usd": supplied,
                            "apy": market.get("apy", 0),
                            "tvl": market.get("tvl", 0),
                            "risk_score": market.get("risk_score", 5),
                        })
                    except Exception as exc:  # partial RPC failure must not hide the portfolio
                        failures.append({
                            "market_id": market.get("market_id"),
                            "chain_id": market.get("chain_id"),
                            "reason": type(exc).__name__,
                        })

        positions.sort(key=lambda item: float(item.get("amount_in_usd") or 0), reverse=True)
        analysis = self.engine.analyze(
            wallet_assets,
            positions,
            scanned_market_count=len(markets),
            failed_market_count=len(failures),
        )
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "account": user_address,
            "source": "particle-wallet + protocol-onchain + defillama-live",
            **analysis,
            "coverage": {
                **analysis["coverage"],
                "failed_markets": failures,
            },
        }
