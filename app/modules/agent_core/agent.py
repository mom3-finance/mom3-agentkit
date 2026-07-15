from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Literal

from app.core.config import settings
from app.modules.market_intelligence import MarketCatalog, get_market_catalog
from app.services.chatbot import get_chatbot
from app.services.defi_llama import get_defillama_collector
from app.services.llm_client import get_llm_client
from app.services.pulse_analyzer import get_pulse_analyzer
from app.services.yield_forecaster import get_yield_forecaster


RiskTolerance = Literal["conservative", "moderate", "aggressive"]


class Mom3Agent:
    """Orchestrates live market data, strategy reasoning, and safe execution metadata."""

    def __init__(self, catalog: MarketCatalog | None = None) -> None:
        self.catalog = catalog or get_market_catalog()
        self.collector = get_defillama_collector()
        self.forecaster = get_yield_forecaster()
        self.pulse = get_pulse_analyzer()
        self.chatbot = get_chatbot()
        self.llm = get_llm_client()

    def markets(self, chain_id: int | None = None, execution_only: bool = False) -> dict:
        rows = self.catalog.list_markets(chain_id, execution_only=execution_only)
        return {
            "timestamp": self.now_iso(),
            "chain_id": chain_id,
            "mvp_scope": {
                "chains": [42161, 8453],
                "execution_protocols": ["aave-v3", "compound-v3", "morpho-blue"],
                "execution_asset": "USDC",
            },
            "markets": rows,
        }

    def strategy(self, risk_tolerance: RiskTolerance, home_chain: int | None = None) -> dict:
        markets = self.catalog.list_markets(execution_only=True)
        if not markets:
            raise RuntimeError("No live execution-ready market passed the MVP policy.")

        allocations = self._allocations(markets, risk_tolerance, home_chain)
        opportunities = []
        forecasts = self._forecasts(markets)
        pulses = self._pulses(markets)
        forecast_by_id = {item["market_id"]: item for item in forecasts}
        pulse_by_id = {item["market_id"]: item for item in pulses}

        for market in markets:
            allocation = allocations[market["market_id"]]
            opportunities.append({
                **market,
                "pool": market["symbol"],
                "allocation": allocation,
                "forecast": forecast_by_id.get(market["market_id"]),
                "liquidity_pulse": pulse_by_id.get(market["market_id"]),
            })

        expected_apy = sum(item["apy"] * item["allocation"] / 100 for item in opportunities)
        risk_score = sum(item["risk_score"] * item["allocation"] / 100 for item in opportunities)
        reasoning = self._reasoning(opportunities, risk_tolerance, home_chain)
        allocation_labels = {
            f"{item['protocol']} {item['asset']} ({item['chain']})": item["allocation"]
            for item in opportunities
        }
        chain_allocations = [
            {
                "market_id": item["market_id"],
                "protocol": item["protocol"],
                "chain_id": item["chain_id"],
                "allocation": item["allocation"],
                "expected_apy": item["apy"],
                "risk_score": item["risk_score"],
                "execution_ready": item["execution"]["enabled"],
            }
            for item in opportunities
        ]
        fingerprint = ":".join(sorted(item["market_id"] for item in opportunities))

        return {
            "strategy_id": "m3s_" + hashlib.sha256(
                f"{risk_tolerance}:{fingerprint}".encode()
            ).hexdigest()[:20],
            "network": "Particle Universal Account",
            "protocol": "mom3 AI",
            "asset": "USDC",
            "risk_tolerance": risk_tolerance,
            "home_chain": home_chain,
            "home_chain_name": self.collector.chain_name(home_chain) if home_chain else None,
            "scanned_chains": sorted({item["chain_id"] for item in markets}),
            "scanned_chain_count": len({item["chain_id"] for item in markets}),
            "allocations": allocation_labels,
            "chain_allocations": chain_allocations,
            "opportunities": opportunities,
            "expected_apy": round(expected_apy, 2),
            "risk_score": round(risk_score, 2),
            "health_score": round(max(0, min(100, 100 - risk_score * 7))),
            "diversification_score": self._diversification(opportunities),
            "reasoning": reasoning,
            "forecast": forecasts,
            "liquidity_pulse": pulses,
            "primary_execution": opportunities[0]["execution"],
            "live_data_source": "DefiLlama live pools + Particle UA execution policy",
            "last_updated": self.now_iso(),
        }

    def forecasts(self, chain_id: int | None = None) -> dict:
        markets = self.catalog.list_markets(chain_id, execution_only=True)
        return {"timestamp": self.now_iso(), "chain_id": chain_id, "forecasts": self._forecasts(markets)}

    def liquidity_pulse(self, chain_id: int | None = None) -> dict:
        markets = self.catalog.list_markets(chain_id, execution_only=True)
        return {"timestamp": self.now_iso(), "chain_id": chain_id, "protocols": self._pulses(markets)}

    async def chat(
        self,
        message: str,
        history: list[dict] | None,
        chain_id: int | None,
    ) -> dict:
        strategy = self.strategy("moderate", chain_id)
        context = {
            "chain": self.collector.chain_name(chain_id) if chain_id else "cross-chain",
            "yield_forecasts": strategy["forecast"],
            "liquidity_pulse": strategy["liquidity_pulse"],
            "current_strategy": strategy["allocations"],
        }
        reply = await self.chatbot.chat(message, context, history)
        return {
            "reply": reply,
            "timestamp": self.now_iso(),
            "context_used": {
                "yield_data": True,
                "liquidity_data": True,
                "strategy_id": strategy["strategy_id"],
                "chain": context["chain"],
            },
            "model": self.llm.model if self.llm.available else "heuristic-fallback",
        }

    def _allocations(self, markets: list[dict], tolerance: RiskTolerance, home_chain: int | None) -> dict[str, float]:
        risk_weight = {"conservative": 1.5, "moderate": 1.0, "aggressive": 0.65}[tolerance]
        scores: list[float] = []
        for market in markets:
            home_bonus = 1.08 if home_chain and market["chain_id"] == home_chain else 1.0
            score = max(0.01, market["opportunity_score"] + 5) * home_bonus
            score /= max(1.0, market["risk_score"] * risk_weight)
            scores.append(score)
        total = sum(scores) or 1
        weights = [score / total for score in scores]
        if len(weights) > 1:
            largest = max(range(len(weights)), key=weights.__getitem__)
            if weights[largest] > 0.7:
                overflow = weights[largest] - 0.7
                weights[largest] = 0.7
                other_total = sum(weights) - weights[largest]
                for index in range(len(weights)):
                    if index != largest:
                        weights[index] += overflow * (weights[index] / other_total)
        rounded = [round(weight * 100, 1) for weight in weights]
        rounded[-1] = round(rounded[-1] + (100 - sum(rounded)), 1)
        return {market["market_id"]: rounded[index] for index, market in enumerate(markets)}

    def _forecasts(self, markets: list[dict]) -> list[dict]:
        output = []
        for market in markets:
            # DefiLlama already publishes 1d/7d/30d APY changes and a model
            # prediction in the global snapshot. Use those fields on the hot
            # strategy path instead of making one chart request per market.
            weekly_change = float(market.get("apy_change_7d") or 0)
            daily_slope = weekly_change / 7
            values = [
                round(max(0.01, market["apy"] + daily_slope * (day + 1)), 2)
                for day in range(7)
            ]
            future = values[-1]
            delta_pct = ((future - market["apy"]) / market["apy"] * 100) if market["apy"] else 0
            trend = "rising" if delta_pct > 5 else "declining" if delta_pct < -5 else "stable"
            probability = float((market.get("prediction") or {}).get("probability") or 0)
            forecast = {
                "protocol": market["protocol"],
                "current_apy": market["apy"],
                "forecast_7d": values,
                "trend": trend,
                "weather": "sunny" if trend == "rising" else "rainy" if trend == "declining" else "stable",
                "confidence": round(probability / 100, 2) if probability else 0.45,
                "slope": round(daily_slope, 4),
            }
            output.append({
                **forecast,
                "market_id": market["market_id"],
                "chain": market["chain"],
                "chain_id": market["chain_id"],
            })
        return output

    def _pulses(self, markets: list[dict]) -> list[dict]:
        output = []
        for market in markets:
            depth_score = min(30.0, max(0.0, math.log10(max(market["tvl"], 1)) - 5) * 15)
            trend_score = max(-10.0, min(10.0, float(market.get("apy_change_7d") or 0) * 5))
            score = round(max(0.0, min(100.0, 48 + depth_score + trend_score - market["risk_score"] * 2)), 1)
            status = "Strong" if score >= 70 else "Healthy" if score >= 50 else "Watch"
            output.append({
                "protocol": market["protocol"],
                "pulse_score": score,
                "status": status,
                "tvl": market["tvl"],
                "tvl_change_24h": None,
                "net_flow": None,
                "is_anomaly": False,
                "alert": None,
                "signal_basis": "market depth and APY trend",
                "timestamp": self.now_iso(),
                "market_id": market["market_id"],
                "chain": market["chain"],
                "chain_id": market["chain_id"],
            })
        return output

    def _reasoning(self, opportunities: list[dict], tolerance: str, home_chain: int | None) -> str:
        top = opportunities[0]
        allocation_summary = ", ".join(
            f"{item['protocol']} on {item['chain']} {item['allocation']:.0f}%"
            for item in opportunities
            if item["allocation"] > 0
        )
        fallback = (
            f"The strategy prioritizes executable USDC lending markets. {top['protocol']} on {top['chain']} "
            f"leads at {top['apy']:.2f}% APY with ${top['tvl'] / 1_000_000:.1f}M TVL; the allocation "
            f"is {allocation_summary}. Rates are variable and every transaction still requires user confirmation."
        )
        if not self.llm.available or not settings.use_llm_strategy_reasoning:
            return fallback
        rows = "\n".join(
            f"- {item['protocol']} {item['asset']} on {item['chain']}: {item['apy']:.2f}% APY, "
            f"TVL ${item['tvl']:.0f}, risk {item['risk_score']}/10, allocation {item['allocation']}%"
            for item in opportunities
        )
        reply = self.llm.chat(
            [
                {"role": "system", "content": "Write a concise 2-3 sentence summary of this non-custodial cross-chain USDC lending strategy. Follow the supplied allocations exactly and mention the leading market, APY, TVL, and why the allocation fits the risk profile. Do not claim guaranteed returns. Do not use the words MVP, production ready, or production-ready."},
                {"role": "user", "content": f"Risk profile: {tolerance}; home chain: {home_chain}; allocation summary: {allocation_summary}.\n{rows}"},
            ],
            temperature=0.4,
            max_tokens=350,
        )
        if not reply:
            return fallback
        # Keep user-facing strategy copy consistent even if the model ignores a wording constraint.
        cleaned = reply.replace("production-ready", "deployment-ready").replace("production ready", "deployment-ready")
        cleaned = cleaned.replace("MVP", "strategy").replace("mvp", "strategy")
        return cleaned

    @staticmethod
    def _diversification(opportunities: list[dict]) -> float:
        weights = [item["allocation"] / 100 for item in opportunities]
        return round(1 - sum(weight * weight for weight in weights), 2)

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


_agent: Mom3Agent | None = None


def get_mom3_agent() -> Mom3Agent:
    global _agent
    if _agent is None:
        _agent = Mom3Agent()
    return _agent
