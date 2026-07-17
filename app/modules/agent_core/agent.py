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

    def markets(self, chain_id: int | None = None, execution_only: bool = False, protocol: str | None = None) -> dict:
        rows = self.catalog.list_markets(chain_id, execution_only=execution_only, protocol=protocol)
        supported_chains = self.collector.supported_chain_ids()
        chain_tvl = self.collector.fetch_supported_chain_tvl()
        execution_protocols = sorted({
            str(item["project"])
            for item in rows
            if item.get("execution", {}).get("enabled")
        })
        return {
            "timestamp": self.now_iso(),
            "chain_id": chain_id,
            "mvp_scope": {
                "chains": supported_chains,
                "execution_protocols": execution_protocols,
                "execution_assets": sorted({
                    str(item["asset"])
                    for item in rows
                    if item.get("execution", {}).get("enabled")
                }),
            },
            "chain_liquidity": list(chain_tvl.values()),
            "markets": rows,
        }

    def strategy(self, risk_tolerance: RiskTolerance, home_chain: int | None = None) -> dict:
        # Strategy only needs actionable candidates. Asking the PostgreSQL
        # catalog for executable rows avoids loading the first discovery page
        # and then accidentally filtering out all UA-compatible pools locally.
        markets = self.catalog.list_markets(execution_only=True)
        if not markets:
            raise RuntimeError("No live UA-compatible market passed the current policy.")

        opportunities_seed = self._select_opportunities(markets, risk_tolerance, home_chain)
        if not opportunities_seed:
            raise RuntimeError("No live strategy candidate matched the current market policy.")

        allocations = self._allocations(opportunities_seed, risk_tolerance, home_chain)
        opportunities = []
        forecasts = self._forecasts(opportunities_seed)
        pulses = self._pulses(opportunities_seed)
        forecast_by_id = {item["market_id"]: item for item in forecasts}
        pulse_by_id = {item["market_id"]: item for item in pulses}

        for market in opportunities_seed:
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
            "asset": self._strategy_asset_label(opportunities),
            "risk_tolerance": risk_tolerance,
            "strategy_mode": self._mode_label(risk_tolerance),
            "risk_guardrails": {
                "max_risk_score": {"conservative": 4.5, "moderate": 7.0, "aggressive": 10.0}[risk_tolerance],
                "impermanent_loss_allowed": risk_tolerance != "conservative",
            },
            "home_chain": home_chain,
            "home_chain_name": self.collector.chain_name(home_chain) if home_chain else None,
            "scanned_chains": sorted({item["chain_id"] for item in markets}),
            "scanned_chain_count": len({item["chain_id"] for item in markets}),
            "chain_liquidity": list(self.collector.fetch_supported_chain_tvl().values()),
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
            "primary_execution": next(
                (item["execution"] for item in opportunities if item["execution"]["enabled"]),
                opportunities[0]["execution"],
            ),
            "live_data_source": (
                "PostgreSQL market snapshots + mom3 smart strategy scoring + Particle UA execution policy"
                if settings.market_data_url
                else "DefiLlama live pools + mom3 smart strategy scoring + Particle UA execution policy"
            ),
            "last_updated": self.now_iso(),
        }

    def forecasts(self, chain_id: int | None = None) -> dict:
        markets = self.catalog.list_markets(chain_id)
        return {"timestamp": self.now_iso(), "chain_id": chain_id, "forecasts": self._forecasts(markets)}

    def liquidity_pulse(self, chain_id: int | None = None) -> dict:
        markets = self.catalog.list_markets(chain_id)
        return {"timestamp": self.now_iso(), "chain_id": chain_id, "protocols": self._pulses(markets)}

    async def chat(
        self,
        message: str,
        history: list[dict] | None,
        chain_id: int | None,
    ) -> dict:
        strategy = self.strategy(self._risk_tolerance_from_message(message), chain_id)
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
                "strategy_mode": strategy["strategy_mode"],
            },
            "model": self.llm.model if self.llm.available else "heuristic-fallback",
        }

    def _select_opportunities(
        self,
        markets: list[dict],
        tolerance: RiskTolerance,
        home_chain: int | None,
    ) -> list[dict]:
        # Strategy recommendations must be actionable by the user. Discovery
        # markets remain available to Explore, but non-allowlisted pools must
        # never be recommended as executable strategy opportunities.
        eligible = [
            market for market in markets
            if market.get("execution", {}).get("enabled") is True
            and self._risk_eligible(market, tolerance)
        ]
        # A risk profile is a hard guardrail. Scoring only ranks candidates
        # inside the selected risk band; it must not promote a high-risk pool
        # just because its APY is larger.
        scored = sorted(
            eligible,
            key=lambda market: self._market_profile_score(market, tolerance, home_chain),
            reverse=True,
        )
        if not scored:
            return []

        target_count = {"conservative": 3, "moderate": 4, "aggressive": 5}[tolerance]
        selected: list[dict] = []
        protocol_counts: dict[str, int] = {}
        chain_counts: dict[int, int] = {}

        def can_add(market: dict) -> bool:
            protocol = str(market["protocol"])
            chain_id = int(market["chain_id"])
            if tolerance == "conservative":
                return protocol_counts.get(protocol, 0) < 1 and chain_counts.get(chain_id, 0) < 2
            if tolerance == "moderate":
                return protocol_counts.get(protocol, 0) < 2 and chain_counts.get(chain_id, 0) < 2
            return protocol_counts.get(protocol, 0) < 2 and chain_counts.get(chain_id, 0) < 3

        for market in scored:
            if len(selected) >= target_count:
                break
            if not can_add(market):
                continue
            selected.append(market)
            protocol = str(market["protocol"])
            chain_id = int(market["chain_id"])
            protocol_counts[protocol] = protocol_counts.get(protocol, 0) + 1
            chain_counts[chain_id] = chain_counts.get(chain_id, 0) + 1

        if not selected:
            selected = scored[:target_count]

        if not any(item["execution"]["enabled"] for item in selected):
            executable = next((item for item in scored if item["execution"]["enabled"]), None)
            if executable:
                selected = [executable, *selected[:-1]] if selected else [executable]

        return selected

    @staticmethod
    def _risk_eligible(market: dict, tolerance: RiskTolerance) -> bool:
        risk = float(market.get("risk_score") or 10)
        has_il = bool(market.get("impermanent_loss"))
        ceilings = {"conservative": 4.5, "moderate": 7.0, "aggressive": 10.0}
        if risk > ceilings[tolerance]:
            return False
        if tolerance == "conservative" and has_il:
            return False
        return True

    def _market_profile_score(self, market: dict, tolerance: RiskTolerance, home_chain: int | None) -> float:
        profile = {
            "conservative": {
                "apy": 0.65,
                "trend": 0.2,
                "tvl": 1.7,
                "execution": 3.0,
                "risk": 2.2,
                "reward_penalty": 1.2,
                "volatility": 0.45,
                "stability": 1.4,
                "home_bonus": 0.8,
            },
            "moderate": {
                "apy": 1.0,
                "trend": 0.5,
                "tvl": 1.2,
                "execution": 2.4,
                "risk": 1.35,
                "reward_penalty": 0.8,
                "volatility": 0.3,
                "stability": 0.8,
                "home_bonus": 0.9,
            },
            "aggressive": {
                "apy": 1.5,
                "trend": 0.9,
                "tvl": 0.8,
                "execution": 1.6,
                "risk": 0.85,
                "reward_penalty": 0.35,
                "volatility": 0.18,
                "stability": 0.25,
                "home_bonus": 1.1,
            },
        }[tolerance]

        apy = float(market.get("apy") or 0)
        tvl = float(market.get("tvl") or 0)
        risk = float(market.get("risk_score") or 5)
        trend = float(market.get("apy_change_7d") or 0)
        reward_apy = float(market.get("apy_reward") or 0)
        reward_ratio = reward_apy / max(apy, 0.01)
        volatility_penalty = abs(float(market.get("apy_change_1d") or 0)) + abs(trend) * 0.35
        stability_bonus = 1.0 if abs(trend) <= 2 else 0.0
        execution_bonus = 1.0 if market.get("execution", {}).get("enabled") else 0.0
        home_bonus = 1.0 if home_chain and int(market["chain_id"]) == home_chain else 0.0
        depth_score = max(0.0, math.log10(max(tvl, 1)) - 5)

        score = (
            (apy * profile["apy"])
            + (trend * profile["trend"])
            + (depth_score * profile["tvl"])
            + (execution_bonus * profile["execution"])
            + (stability_bonus * profile["stability"])
            + (home_bonus * profile["home_bonus"])
            - (risk * profile["risk"])
            - (reward_ratio * profile["reward_penalty"])
            - (volatility_penalty * profile["volatility"])
        )
        return round(score, 6)

    def _allocations(self, markets: list[dict], tolerance: RiskTolerance, home_chain: int | None) -> dict[str, float]:
        scores = [max(0.01, self._market_profile_score(market, tolerance, home_chain) + 12) for market in markets]
        total = sum(scores) or 1
        weights = [score / total for score in scores]
        if len(weights) > 1:
            largest = max(range(len(weights)), key=weights.__getitem__)
            max_weight = {"conservative": 0.46, "moderate": 0.42, "aggressive": 0.55}[tolerance]
            if weights[largest] > max_weight:
                overflow = weights[largest] - max_weight
                weights[largest] = max_weight
                other_total = sum(weights) - weights[largest]
                if other_total > 0:
                    for index in range(len(weights)):
                        if index != largest:
                            weights[index] += overflow * (weights[index] / other_total)
        rounded = [round(weight * 100, 1) for weight in weights]
        rounded[-1] = round(rounded[-1] + (100 - sum(rounded)), 1)
        return {market["market_id"]: rounded[index] for index, market in enumerate(markets)}

    def _forecasts(self, markets: list[dict]) -> list[dict]:
        output = []
        # Chart history is expensive and rate-limited. Enrich only the best
        # candidates; the remaining rows use the cheap /pools snapshot fields.
        history_limit = min(4, len(markets))
        for index, market in enumerate(markets):
            history = self._pool_history(market) if index < history_limit else []
            apy_values = [point["apy"] for point in history if point.get("apy") is not None]
            # Use actual DefiLlama chart history when available. The snapshot
            # change fields remain a fast fallback if the chart is unavailable.
            if len(apy_values) >= 2:
                recent = apy_values[-30:]
                slope = (recent[-1] - recent[0]) / max(len(recent) - 1, 1)
                values = [round(max(0.01, recent[-1] + slope * (day + 1)), 2) for day in range(7)]
                confidence = min(0.95, 0.55 + len(recent) / 100)
            else:
                weekly_change = float(market.get("apy_change_7d") or 0)
                slope = weekly_change / 7
                values = [round(max(0.01, market["apy"] + slope * (day + 1)), 2) for day in range(7)]
                confidence = 0.35
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
                "confidence": round(max(confidence, probability / 100) if probability else confidence, 2),
                "slope": round(slope, 4),
                "data_source": "defillama-chart" if len(apy_values) >= 2 else "defillama-pools-snapshot",
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
        history_limit = min(4, len(markets))
        for index, market in enumerate(markets):
            history = self._pool_history(market) if index < history_limit else []
            tvl_change_24h = self._tvl_change_24h(history)
            depth_score = min(30.0, max(0.0, math.log10(max(market["tvl"], 1)) - 5) * 15)
            trend_score = max(-10.0, min(10.0, tvl_change_24h * 2))
            score = round(max(0.0, min(100.0, 48 + depth_score + trend_score - market["risk_score"] * 2)), 1)
            status = "Strong" if score >= 70 else "Healthy" if score >= 50 else "Watch"
            output.append({
                "protocol": market["protocol"],
                "pulse_score": score,
                "status": status,
                "tvl": market["tvl"],
                "tvl_change_24h": tvl_change_24h,
                "net_flow": round(market["tvl"] * tvl_change_24h / 100, 2),
                "is_anomaly": False,
                "alert": None,
                "signal_basis": "DefiLlama TVL history and market depth",
                "timestamp": self.now_iso(),
                "market_id": market["market_id"],
                "chain": market["chain"],
                "chain_id": market["chain_id"],
            })
        return output

    def _pool_history(self, market: dict) -> list[dict]:
        """Fetch one cached DefiLlama chart only for a selected market."""
        if not settings.enable_chart_history:
            return []
        pool_id = str(market.get("pool_id") or "")
        if not pool_id:
            return []
        points = self.collector.fetch_pool_chart(pool_id)
        normalized = []
        for point in points:
            try:
                apy = float(point.get("apy")) if point.get("apy") is not None else None
                tvl = float(point.get("tvlUsd")) if point.get("tvlUsd") is not None else None
                if apy is not None or tvl is not None:
                    normalized.append({"apy": apy, "tvl": tvl, "timestamp": point.get("timestamp")})
            except (TypeError, ValueError):
                continue
        return normalized

    @staticmethod
    def _tvl_change_24h(history: list[dict]) -> float:
        values = [point["tvl"] for point in history if point.get("tvl") is not None and point["tvl"] > 0]
        if len(values) < 2 or values[-2] <= 0:
            return 0.0
        return round((values[-1] - values[-2]) / values[-2] * 100, 4)

    def _reasoning(self, opportunities: list[dict], tolerance: str, home_chain: int | None) -> str:
        top = opportunities[0]
        allocation_summary = ", ".join(
            f"{item['protocol']} on {item['chain']} {item['allocation']:.0f}%"
            for item in opportunities
            if item["allocation"] > 0
        )
        mode = self._mode_label(tolerance)
        fallback = (
            f"The {mode} strategy ranks live UA-compatible stablecoin markets in real time across chains, then "
            f"balances APY, TVL depth, risk, and recent trend. {top['protocol']} on {top['chain']} leads at "
            f"{top['apy']:.2f}% APY with ${top['tvl'] / 1_000_000:.1f}M TVL; the allocation is {allocation_summary}. "
            f"Rates are variable and every transaction still requires user confirmation."
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
    def _strategy_asset_label(opportunities: list[dict]) -> str:
        assets = sorted({str(item.get("asset") or "") for item in opportunities if item.get("asset")})
        if len(assets) == 1:
            return assets[0]
        if not assets:
            return "Stablecoin"
        return " / ".join(assets[:3])

    @staticmethod
    def _mode_label(tolerance: str) -> str:
        return {
            "conservative": "safe",
            "moderate": "balanced",
            "aggressive": "degen",
        }.get(tolerance, "balanced")

    @staticmethod
    def _risk_tolerance_from_message(message: str) -> RiskTolerance:
        normalized = message.lower()
        if any(keyword in normalized for keyword in ("degen", "aggressive", "high risk", "max apy")):
            return "aggressive"
        if any(keyword in normalized for keyword in ("safe", "conservative", "low risk", "protect")):
            return "conservative"
        return "moderate"

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
