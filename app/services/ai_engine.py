"""mom3 AI strategy engine — multi-chain, LLM-augmented.

Builds a risk-adjusted allocation across protocols/chains:
  * on-chain Aave reserve data where a verified market exists (AaveReader),
  * DefiLlama yields/TVL for every other supported EVM chain,
then runs the SLSQP optimizer, attaches yield forecasts + liquidity pulse, and
generates the reasoning text with the LLM (heuristic fallback).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from loguru import logger

from app.services.aave_reader import AaveReader
from app.services.defi_llama import get_defillama_collector
from app.services.llm_client import get_llm_client
from app.services.pulse_analyzer import get_pulse_analyzer
from app.services.strategy_optimizer import get_strategy_optimizer
from app.services.yield_forecaster import get_yield_forecaster

RiskTolerance = Literal["conservative", "moderate", "aggressive"]

# Heuristic risk score per protocol keyword (0-10). Falls back to 5.0.
_PROTOCOL_RISK = {
    "aave": 3.2,
    "aerodrome": 6.5,
    "compound": 4.0,
    "moonwell": 4.8,
    "seamless": 4.5,
    "lido": 3.5,
    "curve": 4.2,
    "pendle": 6.0,
    "morpho": 4.5,
    "spark": 4.0,
}


def _risk_for(protocol: str) -> float:
    key = protocol.lower()
    for name, score in _PROTOCOL_RISK.items():
        if name in key:
            return score
    return 5.0


def build_strategy(risk_tolerance: RiskTolerance = "moderate", chain_id: Optional[int] = None) -> Dict:
    reader = AaveReader()
    defillama = get_defillama_collector()
    optimizer = get_strategy_optimizer()
    forecaster = get_yield_forecaster()
    pulse = get_pulse_analyzer()
    llm = get_llm_client()

    # The strategy ALWAYS scans every Particle Universal Account 7702 EVM chain —
    # the AI recommends a cross-chain allocation, not a single-chain one. The
    # `chain_id` argument (when provided) marks the user's chosen execution/home
    # chain; it does NOT filter which chains are scanned.
    all_chains = list(defillama.supported_chain_ids())
    home_chain = chain_id if chain_id in defillama.supported_chain_ids() else None

    protocols: List[str] = []
    apys: List[float] = []
    risks: List[float] = []
    chain_ids: List[int] = []
    sources: List[str] = []
    pulse_inputs: List[Dict] = []
    opportunity_details: List[Dict] = []

    # On-chain Aave markets first (authoritative APY + TVL + utilization) across
    # verified chains with an explicitly configured RPC. Chains without one use
    # the DefiLlama pass below instead of relying on rate-limited public RPCs.
    for cid in AaveReader.MARKETS:
        configured_rpc = os.getenv(f"AAVE_RPC_{cid}")
        if cid == 42161:
            configured_rpc = configured_rpc or os.getenv("ARBITRUM_RPC_URL")
        if not configured_rpc:
            logger.info(f"Aave RPC not configured for chain {cid}; using DefiLlama fallback")
            continue
        try:
            market = reader.read_market(cid)
            label = f"Aave V3 ({market['network']})"
            protocols.append(label)
            apys.append(float(market["apy"]))
            risks.append(2.8)  # lending pools are low-risk
            chain_ids.append(cid)
            sources.append(market["source"])
            pulse_inputs.append({"protocol": label, "tvl": market["tvl"], "tvl_change_24h": 0})
            opportunity_details.append({
                "protocol": label,
                "pool": f"{market['asset']} supply",
                "pool_id": None,
                "asset": market["asset"],
                "chain": market["network"],
                "chain_id": cid,
                "tvl": float(market["tvl"]),
                "utilization": float(market.get("utilization", 0)),
                "apy": float(market["apy"]),
                "apy_base": float(market["apy"]),
                "apy_reward": 0.0,
                "apy_change_1d": None,
                "source": market["source"],
            })
        except Exception as exc:
            logger.warning(f"Aave on-chain read failed for chain {cid}; using DefiLlama fallback: {exc}")

    # DefiLlama yields for ALL 7702 chains, grouped by project (top pools each).
    for cid in all_chains:
        try:
            summary = defillama.fetch_chain_protocol_summary(cid, limit=3)
        except Exception as exc:
            logger.warning(f"DefiLlama summary failed for {cid}: {exc}")
            summary = []
        for item in summary:
            label = f"{item['protocol']} ({item['chain']})"
            if label in protocols:
                continue
            protocols.append(label)
            apys.append(float(item["apy"]))
            risks.append(_risk_for(item["protocol"]))
            chain_ids.append(cid)
            sources.append("defillama")
            pulse_inputs.append({"protocol": label, "tvl": item["tvl"], "tvl_change_24h": 0})
            opportunity_details.append({
                "protocol": label,
                "pool": item.get("pool") or "Yield pool",
                "pool_id": item.get("pool_id"),
                "asset": item.get("pool") or "Multi-asset",
                "chain": item["chain"],
                "chain_id": cid,
                "tvl": float(item["tvl"]),
                "utilization": None,
                "apy": float(item["apy"]),
                "apy_base": float(item.get("apy_base", 0)),
                "apy_reward": float(item.get("apy_reward", 0)),
                "apy_change_1d": float(item.get("apy_change_1d", 0)),
                "stablecoin": item.get("stablecoin", False),
                "exposure": item.get("exposure"),
                "impermanent_loss": item.get("impermanent_loss"),
                "source": "defillama",
            })

    # Cap the protocol set so SLSQP stays tractable (keep the highest-APY options
    # but always retain the home-chain candidates if present).
    if len(protocols) > 14:
        order = sorted(range(len(protocols)), key=lambda i: apys[i], reverse=True)[:14]
        protocols = [protocols[i] for i in order]
        apys = [apys[i] for i in order]
        risks = [risks[i] for i in order]
        chain_ids = [chain_ids[i] for i in order]
        sources = [sources[i] for i in order]
        pulse_inputs = [pulse_inputs[i] for i in order]
        opportunity_details = [opportunity_details[i] for i in order]

    # LLM-augmented reasoning (heuristic fallback). The prompt is explicit that
    # this is a cross-chain allocation so the model reasons across chains.
    def reasoning_fn(allocations, protos, rsks, tolerance):
        if llm.available:
            lines = [f"- {p}: {allocations.get(p, 0)}% (APY {apys[i]:.2f}%, risk {rsks[i]:.1f}/10, chain {defillama.chain_name(chain_ids[i]) or chain_ids[i]})"
                     for i, p in enumerate(protos)]
            home_label = defillama.chain_name(home_chain) if home_chain else "auto-selected"
            user_prompt = (
                f"Risk tolerance: {tolerance}. This is a CROSS-CHAIN allocation across multiple "
                f"Particle Universal Account 7702 chains. Execution/home chain: {home_label}. "
                f"Proposed allocation:\n" + "\n".join(lines)
                + "\nIn 2-3 plain-text sentences, explain why this cross-chain allocation makes sense "
                "and the main trade-off (e.g. bridging risk, concentration)."
            )
            reply = llm.chat(
                [{"role": "system", "content": "You explain cross-chain DeFi allocation decisions concisely in plain text."},
                 {"role": "user", "content": user_prompt}],
                temperature=0.5, max_tokens=400,
            )
            if reply:
                return reply + " DYOR."
        return optimizer._explain_allocation(allocations, apys, rsks, protos, tolerance)

    strategy = optimizer.optimize_allocation(
        protocols, apys, risks, risk_tolerance,
        chain_ids=chain_ids, reasoning_fn=reasoning_fn,
    )

    # Attach forecasts + pulse for the protocols we have data on.
    try:
        forecasts = forecaster.forecast_all(
            [{"protocol": p, "apy": a, "chain_id": chain_ids[i]} for i, (p, a) in enumerate(zip(protocols, apys))]
        )
    except Exception as exc:
        logger.warning(f"Forecast build failed: {exc}")
        forecasts = []
    try:
        pulse_data = pulse.analyze_multiple_protocols(pulse_inputs)
    except Exception as exc:
        logger.warning(f"Pulse build failed: {exc}")
        pulse_data = []

    allocation_by_key = {
        (row.get("protocol"), row.get("chain_id")): row
        for row in strategy.get("chain_allocations", [])
    }
    forecast_by_key = {
        (row.get("protocol"), row.get("chain_id")): row
        for row in forecasts
    }
    pulse_by_protocol = {row.get("protocol"): row for row in pulse_data}
    opportunities = []
    for detail in opportunity_details:
        allocation = allocation_by_key.get((detail["protocol"], detail["chain_id"]), {})
        weight = float(allocation.get("allocation", 0) or 0)
        if weight <= 0:
            continue
        opportunities.append({
            **detail,
            "allocation": weight,
            "risk_score": float(allocation.get("risk_score", _risk_for(detail["protocol"]))),
            "forecast": forecast_by_key.get((detail["protocol"], detail["chain_id"])),
            "liquidity_pulse": pulse_by_protocol.get(detail["protocol"]),
        })
    opportunities.sort(key=lambda item: item["allocation"], reverse=True)

    # Distinct chains that actually contributed a protocol — proves multi-chain scan.
    scanned_chains = sorted({cid for cid in chain_ids})
    now = datetime.now(timezone.utc).isoformat()
    return {
        "strategy_id": strategy.get("strategy_id", f"mom3-{risk_tolerance}"),
        "network": "Multi-chain (Particle 7702)",
        "protocol": "mom3 AI",
        "asset": "USDC",
        "chain_id": chain_id,
        "home_chain": home_chain,
        "home_chain_name": defillama.chain_name(home_chain) if home_chain else None,
        "scanned_chains": scanned_chains,
        "scanned_chain_count": len(scanned_chains),
        "risk_tolerance": risk_tolerance,
        "allocations": strategy.get("allocations", {}),
        "chain_allocations": strategy.get("chain_allocations", []),
        "opportunities": opportunities,
        "expected_apy": strategy.get("expected_apy", 0),
        "risk_score": strategy.get("risk_score", 0),
        "health_score": round(max(0, min(100, 100 - strategy.get("risk_score", 5) * 7)), 0),
        "diversification_score": strategy.get("diversification_score", 0),
        "reasoning": strategy.get("reasoning", ""),
        "forecast": forecasts,
        "liquidity_pulse": pulse_data,
        "live_data_source": ", ".join(sorted(set(sources))) or "none",
        "last_updated": now,
    }
