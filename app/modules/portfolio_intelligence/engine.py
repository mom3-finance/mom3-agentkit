from __future__ import annotations

import math
from collections import defaultdict


STABLECOIN_MARKERS = (
    "USDC",
    "USDT",
    "DAI",
    "USDE",
    "USD0",
    "PYUSD",
    "FRAX",
    "LUSD",
    "GHO",
)
MAJOR_ASSETS = {"BTC", "WBTC", "ETH", "WETH", "SOL"}
DUST_USD = 0.01


def _number(value) -> float:
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float, minimum: float = 0, maximum: float = 100) -> float:
    return max(minimum, min(maximum, value))


def _is_stable(symbol: str) -> bool:
    normalized = str(symbol or "").upper().replace("-", "").replace(" ", "")
    return any(marker in normalized for marker in STABLECOIN_MARKERS)


def _risk_for_wallet_asset(symbol: str) -> float:
    normalized = str(symbol or "").upper()
    if _is_stable(normalized):
        return 1.5
    if normalized in MAJOR_ASSETS:
        return 5.0
    return 7.0


def _score_label(score: float) -> tuple[str, str]:
    if score >= 75:
        return "Strong", "positive"
    if score >= 50:
        return "Watch", "warning"
    return "Needs attention", "critical"


def _distribution_score(values: dict[str, float]) -> float:
    total = sum(values.values())
    if total <= 0:
        return 0
    shares = [value / total for value in values.values() if value > 0]
    if len(shares) == 1:
        return 35
    hhi = sum(share * share for share in shares)
    return round(_clamp((1 - hhi) * 145), 1)


def _concentration_score(largest_share: float) -> float:
    if largest_share <= 0.35:
        return 100
    if largest_share <= 0.50:
        return 85
    if largest_share <= 0.70:
        return 65
    return 40


class PortfolioIntelligenceEngine:
    """Deterministic, explainable portfolio analysis over live wallet and protocol data."""

    def analyze(
        self,
        wallet_assets: list[dict],
        positions: list[dict],
        *,
        scanned_market_count: int,
        failed_market_count: int,
    ) -> dict:
        normalized_assets = [self._normalize_wallet_asset(item) for item in wallet_assets]
        normalized_positions = [self._normalize_position(item) for item in positions]

        receipt_contracts = {
            (position["chain_id"], position["position_contract"].lower())
            for position in normalized_positions
            if position["position_contract"]
        }
        receipt_symbols = {
            (position["chain_id"], position["position_symbol"].upper())
            for position in normalized_positions
            if position["position_symbol"]
        }

        liquid_assets: list[dict] = []
        receipt_value = 0.0
        for asset in normalized_assets:
            contract_key = (asset["chain_id"], asset["token_address"].lower())
            symbol_key = (asset["chain_id"], asset["symbol"].upper())
            if contract_key in receipt_contracts or symbol_key in receipt_symbols:
                receipt_value += asset["amount_in_usd"]
                continue
            liquid_assets.append(asset)

        wallet_value = sum(asset["amount_in_usd"] for asset in normalized_assets)
        position_value = sum(position["amount_in_usd"] for position in normalized_positions)
        total_value = max(0.0, wallet_value - receipt_value + position_value)

        exposures = [
            {
                "asset": asset["symbol"],
                "chain": asset["chain"],
                "chain_id": asset["chain_id"],
                "protocol": "Wallet",
                "value": asset["amount_in_usd"],
                "risk": _risk_for_wallet_asset(asset["symbol"]),
                "stable": _is_stable(asset["symbol"]),
            }
            for asset in liquid_assets
            if asset["amount_in_usd"] >= DUST_USD
        ]
        exposures.extend(
            {
                "asset": position["asset"],
                "chain": position["chain"],
                "chain_id": position["chain_id"],
                "protocol": position["protocol"],
                "value": position["amount_in_usd"],
                "risk": position["risk_score"],
                "stable": _is_stable(position["asset"]),
            }
            for position in normalized_positions
            if position["amount_in_usd"] >= DUST_USD
        )

        if total_value < DUST_USD or not exposures:
            return self._empty_analysis(
                scanned_market_count=scanned_market_count,
                failed_market_count=failed_market_count,
            )

        by_asset: defaultdict[str, float] = defaultdict(float)
        by_chain: defaultdict[str, float] = defaultdict(float)
        by_protocol: defaultdict[str, float] = defaultdict(float)
        for exposure in exposures:
            by_asset[exposure["asset"]] += exposure["value"]
            by_chain[exposure["chain"]] += exposure["value"]
            by_protocol[exposure["protocol"]] += exposure["value"]

        stable_value = sum(item["value"] for item in exposures if item["stable"])
        stable_allocation = _clamp(stable_value / total_value * 100)
        yield_allocation = _clamp(position_value / total_value * 100)
        net_apy = (
            sum(item["apy"] * item["amount_in_usd"] for item in normalized_positions)
            / position_value
            if position_value > 0
            else 0
        )
        weighted_risk = sum(item["risk"] * item["value"] for item in exposures) / total_value

        largest_asset, largest_asset_value = max(by_asset.items(), key=lambda item: item[1])
        largest_chain, largest_chain_value = max(by_chain.items(), key=lambda item: item[1])
        largest_asset_share = largest_asset_value / total_value
        largest_chain_share = largest_chain_value / total_value

        stability_score = round(stable_allocation, 1)
        diversification_score = _distribution_score(dict(by_asset))
        chain_diversification_score = _distribution_score(dict(by_chain))
        concentration_score = _concentration_score(largest_asset_share)
        risk_quality_score = _clamp(100 - weighted_risk * 10)
        liquidity_score = self._liquidity_score(normalized_positions)
        confidence_score = self._confidence_score(
            normalized_assets,
            scanned_market_count,
            failed_market_count,
        )
        health_score = round(
            stability_score * 0.25
            + diversification_score * 0.15
            + concentration_score * 0.15
            + risk_quality_score * 0.20
            + liquidity_score * 0.10
            + confidence_score * 0.10
            + chain_diversification_score * 0.05
        )
        risk_level = "Low" if weighted_risk <= 3.0 else "Medium" if weighted_risk <= 6.0 else "High"

        indicators = self._indicators(
            stability_score=stability_score,
            stable_allocation=stable_allocation,
            diversification_score=diversification_score,
            asset_count=len(by_asset),
            chain_count=len(by_chain),
            concentration_score=concentration_score,
            largest_asset=largest_asset,
            largest_asset_share=largest_asset_share,
            position_value=position_value,
            yield_allocation=yield_allocation,
            net_apy=net_apy,
            risk_quality_score=risk_quality_score,
            liquidity_score=liquidity_score,
            confidence_score=confidence_score,
            scanned_market_count=scanned_market_count,
            failed_market_count=failed_market_count,
        )
        recommendations = self._recommendations(
            largest_asset=largest_asset,
            largest_asset_share=largest_asset_share,
            largest_chain=largest_chain,
            largest_chain_share=largest_chain_share,
            stable_allocation=stable_allocation,
            yield_allocation=yield_allocation,
            weighted_risk=weighted_risk,
            position_count=len(normalized_positions),
        )

        return {
            "status": "partial" if failed_market_count else "ready",
            "summary": {
                "total_value": round(total_value, 2),
                "wallet_value": round(wallet_value, 2),
                "position_value": round(position_value, 2),
                "health_score": health_score,
                "risk_level": risk_level,
                "risk_score": round(weighted_risk, 2),
                "net_apy": round(net_apy, 2),
                "stable_allocation": round(stable_allocation, 1),
                "yield_allocation": round(yield_allocation, 1),
                "asset_count": len(by_asset),
                "chain_count": len(by_chain),
                "protocol_count": len({key for key in by_protocol if key != "Wallet"}),
                "largest_asset": largest_asset,
                "largest_asset_allocation": round(largest_asset_share * 100, 1),
            },
            "positions": normalized_positions,
            "indicators": indicators,
            "recommendations": recommendations,
            "coverage": {
                "scanned_markets": scanned_market_count,
                "successful_market_reads": max(0, scanned_market_count - failed_market_count),
                "failed_market_reads": failed_market_count,
                "confidence_score": round(confidence_score),
                "receipt_value_deduplicated": round(receipt_value, 2),
            },
        }

    @staticmethod
    def _normalize_wallet_asset(item: dict) -> dict:
        return {
            "id": str(item.get("id") or ""),
            "symbol": str(item.get("symbol") or "UNKNOWN").upper(),
            "name": str(item.get("name") or item.get("symbol") or "Unknown token"),
            "balance": max(0, _number(item.get("balance"))),
            "amount_in_usd": max(0, _number(item.get("amount_in_usd"))),
            "chain": str(item.get("chain") or "Unknown chain"),
            "chain_id": int(_number(item.get("chain_id"))),
            "token_address": str(item.get("token_address") or ""),
        }

    @staticmethod
    def _normalize_position(item: dict) -> dict:
        supplied_balance = max(0, _number(item.get("supplied_balance")))
        amount_in_usd = max(0, _number(item.get("amount_in_usd") or supplied_balance))
        risk_score = _clamp(_number(item.get("risk_score") or 5), 0, 10)
        return {
            "market_id": str(item.get("market_id") or ""),
            "protocol": str(item.get("protocol") or "Unknown protocol"),
            "project": str(item.get("project") or ""),
            "chain": str(item.get("chain") or "Unknown chain"),
            "chain_id": int(_number(item.get("chain_id"))),
            "asset": str(item.get("asset") or "UNKNOWN").upper(),
            "position_symbol": str(item.get("position_symbol") or ""),
            "position_contract": str(item.get("position_contract") or ""),
            "supplied_balance": supplied_balance,
            "supplied_balance_atomic": str(item.get("supplied_balance_atomic") or "0"),
            "amount_in_usd": round(amount_in_usd, 2),
            "apy": round(max(0, _number(item.get("apy"))), 4),
            "tvl": round(max(0, _number(item.get("tvl"))), 2),
            "risk_score": round(risk_score, 2),
            "risk": "Low" if risk_score < 4 else "Medium" if risk_score < 6.5 else "High",
            "health": round(_clamp(100 - risk_score * 8)),
            "source": str(item.get("source") or "protocol-onchain"),
        }

    @staticmethod
    def _liquidity_score(positions: list[dict]) -> float:
        total = sum(item["amount_in_usd"] for item in positions)
        if total <= 0:
            return 80

        def market_score(tvl: float) -> float:
            if tvl >= 100_000_000:
                return 100
            if tvl >= 25_000_000:
                return 85
            if tvl >= 5_000_000:
                return 65
            if tvl > 0:
                return 40
            return 30

        return round(
            sum(market_score(item["tvl"]) * item["amount_in_usd"] for item in positions) / total,
            1,
        )

    @staticmethod
    def _confidence_score(assets: list[dict], scanned: int, failed: int) -> float:
        read_ratio = (scanned - failed) / scanned if scanned > 0 else 0.5
        valued = [item for item in assets if item["amount_in_usd"] >= DUST_USD]
        known_ratio = (
            sum(1 for item in valued if item["symbol"] != "UNKNOWN") / len(valued)
            if valued
            else 1
        )
        return round(_clamp(45 + read_ratio * 40 + known_ratio * 15), 1)

    def _indicators(self, **metrics) -> list[dict]:
        stability_status, stability_tone = _score_label(metrics["stability_score"])
        diversification_status, diversification_tone = _score_label(metrics["diversification_score"])
        concentration_status, concentration_tone = _score_label(metrics["concentration_score"])
        liquidity_status, liquidity_tone = _score_label(metrics["liquidity_score"])
        confidence_status, confidence_tone = _score_label(metrics["confidence_score"])

        if metrics["position_value"] <= 0:
            yield_status, yield_tone, yield_score = "No active yield", "neutral", 50
            yield_detail = "No allowlisted protocol position was found for this account."
        else:
            yield_score = round((metrics["risk_quality_score"] + metrics["liquidity_score"]) / 2, 1)
            yield_status, yield_tone = _score_label(yield_score)
            yield_detail = (
                f"{metrics['yield_allocation']:.1f}% of the portfolio earns a weighted "
                f"{metrics['net_apy']:.2f}% APY."
            )

        return [
            {
                "id": "stability",
                "label": "Stable buffer",
                "score": round(metrics["stability_score"]),
                "status": stability_status,
                "tone": stability_tone,
                "value": f"{metrics['stable_allocation']:.1f}% stable",
                "detail": "Measures how much value is protected from normal token volatility.",
            },
            {
                "id": "concentration",
                "label": "Concentration",
                "score": round(metrics["concentration_score"]),
                "status": concentration_status,
                "tone": concentration_tone,
                "value": f"{metrics['largest_asset_share'] * 100:.1f}% {metrics['largest_asset']}",
                "detail": "Detects when one asset can dominate the portfolio outcome.",
            },
            {
                "id": "diversification",
                "label": "Diversification",
                "score": round(metrics["diversification_score"]),
                "status": diversification_status,
                "tone": diversification_tone,
                "value": f"{metrics['asset_count']} assets / {metrics['chain_count']} chains",
                "detail": "Combines asset and network distribution without rewarding dust balances.",
            },
            {
                "id": "yield_quality",
                "label": "Yield quality",
                "score": round(yield_score),
                "status": yield_status,
                "tone": yield_tone,
                "value": f"{metrics['net_apy']:.2f}% net APY" if metrics["position_value"] > 0 else "No position",
                "detail": yield_detail,
            },
            {
                "id": "liquidity",
                "label": "Exit liquidity",
                "score": round(metrics["liquidity_score"]),
                "status": liquidity_status,
                "tone": liquidity_tone,
                "value": "Deep" if metrics["liquidity_score"] >= 75 else "Moderate" if metrics["liquidity_score"] >= 50 else "Thin",
                "detail": "Weights each active position by live protocol TVL.",
            },
            {
                "id": "confidence",
                "label": "Data confidence",
                "score": round(metrics["confidence_score"]),
                "status": confidence_status,
                "tone": confidence_tone,
                "value": f"{metrics['scanned_market_count'] - metrics['failed_market_count']}/{metrics['scanned_market_count']} markets read",
                "detail": "Falls when RPC reads fail or asset metadata is incomplete.",
            },
        ]

    @staticmethod
    def _recommendations(**metrics) -> list[dict]:
        recommendations: list[dict] = []
        if metrics["largest_asset_share"] > 0.70:
            recommendations.append({
                "id": "reduce-asset-concentration",
                "priority": "high",
                "title": f"Reduce {metrics['largest_asset']} concentration",
                "detail": f"{metrics['largest_asset_share'] * 100:.0f}% of value depends on one asset.",
            })
        if metrics["stable_allocation"] < 35:
            recommendations.append({
                "id": "build-stable-buffer",
                "priority": "high" if metrics["weighted_risk"] > 6 else "medium",
                "title": "Build a stable buffer",
                "detail": "A larger stablecoin reserve can reduce drawdown and fund future opportunities.",
            })
        if metrics["largest_chain_share"] > 0.85 and metrics["position_count"] > 1:
            recommendations.append({
                "id": "diversify-chain",
                "priority": "medium",
                "title": f"Review {metrics['largest_chain']} concentration",
                "detail": "Multiple positions still depend on the same network and infrastructure.",
            })
        if metrics["weighted_risk"] > 6:
            recommendations.append({
                "id": "review-high-risk",
                "priority": "high",
                "title": "Review high-risk exposure",
                "detail": "The weighted risk score is above the preferred portfolio range.",
            })
        if metrics["yield_allocation"] == 0 and metrics["stable_allocation"] > 0:
            recommendations.append({
                "id": "activate-idle-stables",
                "priority": "low",
                "title": "Compare yield for idle stablecoins",
                "detail": "No allowlisted earning position is active. Review markets before supplying.",
            })
        if not recommendations:
            recommendations.append({
                "id": "hold-and-monitor",
                "priority": "low",
                "title": "Portfolio is within a healthy range",
                "detail": "Keep monitoring APY, liquidity, and concentration as market conditions change.",
            })
        return recommendations[:3]

    @staticmethod
    def _empty_analysis(*, scanned_market_count: int, failed_market_count: int) -> dict:
        return {
            "status": "empty",
            "summary": {
                "total_value": 0,
                "wallet_value": 0,
                "position_value": 0,
                "health_score": 0,
                "risk_level": "Unavailable",
                "risk_score": 0,
                "net_apy": 0,
                "stable_allocation": 0,
                "yield_allocation": 0,
                "asset_count": 0,
                "chain_count": 0,
                "protocol_count": 0,
                "largest_asset": None,
                "largest_asset_allocation": 0,
            },
            "positions": [],
            "indicators": [],
            "recommendations": [{
                "id": "fund-wallet",
                "priority": "low",
                "title": "Add an asset to begin analysis",
                "detail": "Mom3 will score concentration, yield quality, liquidity, and risk automatically.",
            }],
            "coverage": {
                "scanned_markets": scanned_market_count,
                "successful_market_reads": max(0, scanned_market_count - failed_market_count),
                "failed_market_reads": failed_market_count,
                "confidence_score": 0,
                "receipt_value_deduplicated": 0,
            },
        }
