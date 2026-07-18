from __future__ import annotations

import math
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from app.modules.agent_core import ExecutionIntentService, get_mom3_agent
from app.modules.agent_core.execution import ExecutionIntentError
from app.modules.portfolio_intelligence import PortfolioIntelligenceService
from app.core.config import settings
from app.services.aave_reader import AaveReader
from app.services.position_reader import PositionReader


router = APIRouter()
agent = get_mom3_agent()
execution_intents = ExecutionIntentService()
aave_reader = AaveReader()
position_reader = PositionReader()
portfolio_intelligence = PortfolioIntelligenceService(position_reader=position_reader)


class StrategyRequest(BaseModel):
    risk_tolerance: Literal["conservative", "moderate", "aggressive"] = "moderate"
    chain_id: int | None = None
    user_address: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2_000)
    history: list[dict] | None = None
    chain_id: int | None = None
    user_address: str | None = None


class ExecutionIntentRequest(BaseModel):
    market_id: str = Field(min_length=1, max_length=160)
    action: Literal["supply", "withdraw"] = "supply"
    amount: str = Field(min_length=1, max_length=48)
    user_address: str


class PortfolioAssetInput(BaseModel):
    id: str = Field(default="", max_length=240)
    symbol: str = Field(default="UNKNOWN", max_length=48)
    name: str = Field(default="Unknown token", max_length=160)
    balance: float = 0
    amount_in_usd: float = 0
    chain: str = Field(default="Unknown chain", max_length=80)
    chain_id: int = 0
    token_address: str = Field(default="", max_length=80)


class PortfolioAnalysisRequest(BaseModel):
    user_address: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    wallet_assets: list[PortfolioAssetInput] = Field(default_factory=list, max_length=500)


def json_safe(value):
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        return json_safe(value.item())
    return value


@router.get("/health", tags=["system"])
def health():
    return {
        "ok": True,
        "service": "mom3 Agentkit",
        "version": "3.0.0",
        "supported_chains": agent.collector.supported_chain_ids(),
        "execution_protocols": ["aave-v3", "compound-v3", "kamino-lend"],
        "execution_asset": "USDC",
        "llm_available": agent.llm.available,
        "llm_model": agent.llm.model,
    }


@router.get("/api/yield-markets", tags=["markets"])
def yield_markets(
    chain_id: int | None = None,
    execution_only: bool = Query(default=False),
    protocol: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    try:
        payload = agent.markets(chain_id, execution_only, protocol=protocol)
        rows = payload.get("markets", [])
        payload["total"] = len(rows)
        payload["offset"] = offset
        payload["limit"] = limit
        payload["has_more"] = limit is not None and offset + limit < len(rows)
        payload["markets"] = rows[offset: offset + limit if limit is not None else None]
        return JSONResponse(content=json_safe(payload))
    except Exception as exc:
        logger.error(f"Yield markets failed: {exc}")
        raise HTTPException(status_code=502, detail="Live yield markets are unavailable.") from exc


@router.get("/api/yield-markets/top", tags=["markets"])
def top_yield_markets(
    limit: int = Query(default=10, ge=1, le=10),
    chain_id: int | None = Query(default=None),
):
    try:
        return JSONResponse(content=json_safe(agent.top_yields(limit, chain_id)))
    except Exception as exc:
        logger.error(f"Top yield markets failed: {exc}")
        raise HTTPException(status_code=502, detail="Top yield analysis is unavailable.") from exc


@router.get("/api/ai/market-analysis", tags=["agent"])
def market_analysis_page(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=10),
    chain_id: int | None = Query(default=None),
    risk_tolerance: Literal["conservative", "moderate", "aggressive"] = "moderate",
):
    try:
        return JSONResponse(content=json_safe(agent.market_analysis_page(page, page_size, chain_id, risk_tolerance)))
    except Exception as exc:
        logger.error(f"Market analysis page failed: {exc}")
        raise HTTPException(status_code=502, detail="Market analysis is temporarily unavailable.") from exc


@router.get("/api/yield-markets/{market_id}", tags=["markets"])
def yield_market_detail(market_id: str):
    try:
        market = agent.catalog.get_market(market_id)
        # Backend realtime may have a local Aave fallback ID when AgentKit is
        # temporarily unreachable. Resolve it to the canonical live market
        # once AgentKit is back online.
        if not market and market_id.startswith("fallback-aave-"):
            try:
                fallback_chain = int(market_id.rsplit("-", 1)[-1])
                market = next(
                    (
                        item for item in agent.catalog.list_markets(fallback_chain)
                        if item.get("project") == "aave-v3"
                    ),
                    None,
                )
            except ValueError:
                market = None
        if not market:
            raise HTTPException(status_code=404, detail="Live yield market was not found.")
        return JSONResponse(content=json_safe({
            "timestamp": agent.now_iso(),
            "market": market,
            # Detail consumers need the chart together with the market. The
            # collector caches this per pool, so this avoids a second browser
            # request and repeated chart fetches during realtime refreshes.
            "chart": (
                agent.catalog.get_history(market["pool_id"])
                if settings.enable_chart_history and settings.market_history_url
                else agent.collector.fetch_pool_chart(market["pool_id"])
                if settings.enable_chart_history
                else []
            ),
        }))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Yield market detail failed: {exc}")
        raise HTTPException(status_code=502, detail="Live yield market is unavailable.") from exc


@router.get("/api/yield-markets/{market_id}/analysis", tags=["markets"])
def yield_market_analysis(market_id: str):
    try:
        return JSONResponse(content=json_safe(agent.market_analysis(market_id)))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Live yield market was not found.") from exc
    except Exception as exc:
        logger.error(f"Yield market analysis failed: {exc}")
        raise HTTPException(status_code=502, detail="Live market analysis is unavailable.") from exc


@router.get("/api/yield-markets/{market_id}/chart", tags=["markets"])
def yield_market_chart(market_id: str):
    try:
        market = agent.catalog.get_market(market_id)
        if not market:
            raise HTTPException(status_code=404, detail="Live yield market was not found.")
        using_postgres = bool(settings.market_history_url)
        points = agent.catalog.get_history(market["pool_id"]) if using_postgres else agent.collector.fetch_pool_chart(market["pool_id"])
        return JSONResponse(content=json_safe({
            "timestamp": agent.now_iso(),
            "market_id": market_id,
            "pool_id": market["pool_id"],
            "source": "postgresql-market-snapshots" if using_postgres else "defillama-live",
            "points": points,
        }))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Yield market chart failed: {exc}")
        raise HTTPException(status_code=502, detail="Live yield chart is unavailable.") from exc


@router.get("/api/yield-markets/{market_id}/position", tags=["markets"])
def yield_market_position(market_id: str, user_address: str):
    try:
        market = agent.catalog.get_market(market_id)
        if not market:
            raise HTTPException(status_code=404, detail="Live yield market was not found.")
        return JSONResponse(content=json_safe(position_reader.read(market, user_address)))
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Yield position read failed: {exc}")
        raise HTTPException(status_code=502, detail="The on-chain position is unavailable.") from exc


@router.get("/api/market/aave", tags=["markets"])
def aave_market(chain_id: int = 42161):
    try:
        return aave_reader.read_market(chain_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Aave market read failed: {exc}")
        raise HTTPException(status_code=502, detail="Aave market is unavailable.") from exc


@router.post("/api/ai/strategy", tags=["agent"])
def strategy(request: StrategyRequest):
    try:
        return JSONResponse(
            content=json_safe(agent.strategy(request.risk_tolerance, request.chain_id))
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Strategy build failed: {exc}")
        raise HTTPException(status_code=502, detail="Unable to build the live strategy.") from exc


@router.post("/api/portfolio/analyze", tags=["portfolio"])
def analyze_portfolio(request: PortfolioAnalysisRequest):
    try:
        return JSONResponse(
            content=json_safe(
                portfolio_intelligence.analyze(
                    request.user_address,
                    [asset.model_dump() for asset in request.wallet_assets],
                )
            )
        )
    except Exception as exc:
        logger.error(f"Portfolio analysis failed: {exc}")
        raise HTTPException(status_code=502, detail="Portfolio intelligence is temporarily unavailable.") from exc


@router.post("/api/ai/execution-intent", tags=["agent"])
def execution_intent(request: ExecutionIntentRequest):
    try:
        return execution_intents.create_intent(
            request.market_id, request.action, request.amount, request.user_address
        )
    except ExecutionIntentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Execution intent failed: {exc}")
        raise HTTPException(status_code=502, detail="Unable to prepare the execution intent.") from exc


@router.get("/api/yield-forecast", tags=["agent"])
def yield_forecast(chain_id: int | None = None):
    try:
        return JSONResponse(content=json_safe(agent.forecasts(chain_id)))
    except Exception as exc:
        logger.error(f"Yield forecast failed: {exc}")
        raise HTTPException(status_code=502, detail="Yield forecast is unavailable.") from exc


@router.get("/api/liquidity-pulse", tags=["agent"])
def liquidity_pulse(chain_id: int | None = None):
    try:
        return JSONResponse(content=json_safe(agent.liquidity_pulse(chain_id)))
    except Exception as exc:
        logger.error(f"Liquidity pulse failed: {exc}")
        raise HTTPException(status_code=502, detail="Liquidity pulse is unavailable.") from exc


@router.post("/api/chat", tags=["agent"])
async def chat(request: ChatRequest):
    try:
        return await agent.chat(request.message, request.history, request.chain_id)
    except Exception as exc:
        logger.error(f"Chat failed: {exc}")
        raise HTTPException(status_code=502, detail="Chat is unavailable.") from exc


@router.get("/api/network-info", tags=["system"])
def network_info(chain_id: int | None = None):
    supported_chains = agent.collector.supported_chain_ids()
    return {
        "chain_id": chain_id,
        "chain": agent.collector.chain_name(chain_id) if chain_id else None,
        "supported_chains": supported_chains,
        "supported": chain_id in supported_chains if chain_id else False,
        "aave_supported": chain_id in AaveReader.MARKETS if chain_id else False,
        "llm_available": agent.llm.available,
    }
