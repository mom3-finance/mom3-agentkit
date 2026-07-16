from __future__ import annotations

import os
from dataclasses import dataclass


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _boolean(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    service_name: str
    service_version: str
    cors_origins: tuple[str, ...]
    minimum_tvl_usd: float
    maximum_apy: float
    maximum_intent_amount_usd: float
    use_llm_strategy_reasoning: bool
    enable_chart_history: bool
    mongo_required: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            service_name="mom3 Agentkit",
            service_version="3.0.0",
            cors_origins=_csv(os.getenv("CORS_ORIGINS", "http://localhost:3000")),
            minimum_tvl_usd=float(os.getenv("MVP_MIN_TVL_USD", "1000000")),
            maximum_apy=float(os.getenv("MVP_MAX_APY", "20")),
            maximum_intent_amount_usd=float(os.getenv("MVP_MAX_INTENT_AMOUNT_USD", "10000")),
            use_llm_strategy_reasoning=_boolean(os.getenv("AGENT_LLM_STRATEGY_REASONING", "false")),
            enable_chart_history=_boolean(os.getenv("AGENTKIT_ENABLE_CHART_HISTORY", "false")),
            mongo_required=_boolean(os.getenv("AGENTKIT_MONGO_REQUIRED", "true")),
        )


settings = Settings.from_env()
