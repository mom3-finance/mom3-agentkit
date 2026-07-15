"""Yield Prophet — 7-day APY forecasting with weather metaphors.

Ported from resource/nuvia-agentkit/ai-engine/modules/yield_prophet/forecaster.py,
with the Base-specific protocol-volatility table replaced by a generic default so
the forecast works for any chain's protocols.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
from loguru import logger

_DEFAULT_VOLATILITY = 0.5
_FORECAST_DAYS = 7


class YieldForecaster:
    """Forecasts APY trends and maps them to weather emojis (☀️🌤️🌧️🌪️)."""

    def __init__(self) -> None:
        self.historical_data: Dict[str, "object"] = {}

    def forecast_7day(self, protocol: str, current_apy: float) -> Dict:
        try:
            df = self.historical_data.get(protocol)
            if df is not None and len(df) >= 2:
                return self._forecast_with_history(df, protocol, current_apy)
            return self._forecast_simple(protocol, current_apy)
        except Exception as exc:
            logger.error(f"Forecast error for {protocol}: {exc}")
            return self._forecast_simple(protocol, current_apy)

    def _forecast_with_history(self, df, protocol: str, current_apy: float) -> Dict:
        recent = df.tail(30) if hasattr(df, "tail") else df
        if hasattr(recent, "columns") and "apy" in recent.columns:
            values = recent["apy"].values
        elif isinstance(recent, list):
            values = [item.get("apy", 0) if isinstance(item, dict) else item for item in recent]
        else:
            values = recent
        y = np.array(values, dtype=float)
        x = np.arange(len(y))
        slope = float(np.polyfit(x, y, 1)[0]) if len(x) > 1 else 0.0

        forecast = []
        for i in range(_FORECAST_DAYS):
            predicted = max(0.1, current_apy + slope * i)
            forecast.append(round(predicted, 2))

        trend = self._determine_trend(current_apy, forecast[-1])
        return {
            "protocol": protocol,
            "current_apy": float(current_apy),
            "forecast_7d": forecast,
            "trend": trend,
            "weather": self._map_to_weather(trend, slope),
            "confidence": round(min(0.95, 0.6 + len(recent) / 100), 2),
            "slope": round(slope, 4),
        }

    def _forecast_simple(self, protocol: str, current_apy: float) -> Dict:
        # Without historical points we must not invent a random APY forecast.
        # Keep the display conservative and mark it low-confidence instead.
        forecast = [round(float(current_apy), 2) for _ in range(_FORECAST_DAYS)]
        slope = (forecast[-1] - current_apy) / _FORECAST_DAYS
        trend = self._determine_trend(current_apy, forecast[-1])
        return {
            "protocol": protocol,
            "current_apy": float(current_apy),
            "forecast_7d": forecast,
            "trend": trend,
            "weather": self._map_to_weather(trend, slope),
            "confidence": 0.25,
            "slope": round(slope, 4),
        }

    @staticmethod
    def _determine_trend(current: float, future: float) -> str:
        diff_pct = ((future - current) / current) * 100 if current else 0
        if diff_pct > 5:
            return "rising"
        if diff_pct < -5:
            return "declining"
        return "stable"

    @staticmethod
    def _map_to_weather(trend: str, slope: float) -> str:
        if abs(slope) > 0.5:
            return "🌪️"
        if trend == "rising":
            return "☀️"
        if trend == "declining":
            return "🌧️"
        return "🌤️"

    def forecast_all(self, protocol_data: List[Dict]) -> List[Dict]:
        forecasts = []
        for data in protocol_data:
            protocol = data.get("protocol") or data.get("project") or "unknown"
            current_apy = float(data.get("apy", 10.0) or 0)
            forecast = self.forecast_7day(protocol, current_apy)
            # carry chain context when provided
            if "chain" in data:
                forecast["chain"] = data["chain"]
            if "chain_id" in data:
                forecast["chain_id"] = data["chain_id"]
            forecasts.append(forecast)
        return forecasts


# Singleton ---------------------------------------------------------------------
_forecaster: YieldForecaster | None = None


def get_yield_forecaster() -> YieldForecaster:
    global _forecaster
    if _forecaster is None:
        _forecaster = YieldForecaster()
    return _forecaster
