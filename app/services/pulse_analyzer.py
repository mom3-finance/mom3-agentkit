"""Liquidity Pulse — real-time TVL flow monitoring + anomaly detection.

Ported from resource/nuvia-agentkit/ai-engine/modules/liquidity_pulse/pulse_analyzer.py.
Chain-agnostic; aggregate pulse uses the optimizer's allocations (mom3 has no vault contract).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

import numpy as np
from loguru import logger
from sklearn.ensemble import IsolationForest


class LiquidityPulseAnalyzer:
    """Pulse scores (0–100) like a heartbeat monitor, with anomaly detection."""

    def __init__(self) -> None:
        self.historical_flows: Dict[str, List[float]] = {}
        self.anomaly_detector = IsolationForest(contamination=0.1, random_state=42)

    def calculate_pulse_score(
        self,
        protocol: str,
        current_tvl: float,
        tvl_change_24h: float,
        transaction_volume: float | None = None,
    ) -> Dict:
        try:
            if tvl_change_24h > 20:
                base_score = 90
            elif tvl_change_24h > 10:
                base_score = 75
            elif tvl_change_24h > 0:
                base_score = 60
            elif tvl_change_24h > -10:
                base_score = 40
            elif tvl_change_24h > -20:
                base_score = 25
            else:
                base_score = 10

            if transaction_volume and current_tvl:
                base_score = min(100, base_score * min(1.2, transaction_volume / current_tvl))

            pulse_score = round(base_score, 1)
            is_anomaly = self._check_anomaly(protocol, tvl_change_24h)
            return {
                "protocol": protocol,
                "pulse_score": pulse_score,
                "status": self._determine_status(pulse_score),
                "tvl": float(current_tvl or 0),
                "tvl_change_24h": float(tvl_change_24h),
                "net_flow": self._calculate_net_flow(current_tvl, tvl_change_24h),
                "is_anomaly": is_anomaly,
                "alert": self._generate_alert(pulse_score, tvl_change_24h, is_anomaly),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            logger.error(f"Pulse error for {protocol}: {exc}")
            return self._default_pulse(protocol)

    @staticmethod
    def _determine_status(pulse_score: float) -> str:
        if pulse_score >= 80:
            return "🚨 Surge"
        if pulse_score >= 65:
            return "💓 Strong"
        if pulse_score >= 45:
            return "💚 Healthy"
        if pulse_score >= 30:
            return "💔 Weak"
        return "🩸 Critical"

    @staticmethod
    def _calculate_net_flow(current_tvl: float, change_pct: float) -> str:
        flow_amount = (current_tvl or 0) * (change_pct / 100)
        if abs(flow_amount) >= 1_000_000:
            return f"${flow_amount/1_000_000:.1f}M"
        if abs(flow_amount) >= 1_000:
            return f"${flow_amount/1_000:.0f}k"
        return f"${flow_amount:.0f}"

    def _check_anomaly(self, protocol: str, tvl_change: float) -> bool:
        history = self.historical_flows.setdefault(protocol, [])
        history.append(float(tvl_change))
        if len(history) > 50:
            del history[:-50]
        if len(history) < 10:
            return False
        try:
            x = np.array(history).reshape(-1, 1)
            predictions = self.anomaly_detector.fit_predict(x)
            return predictions[-1] == -1
        except Exception as exc:
            logger.error(f"Anomaly detection error for {protocol}: {exc}")
            return False

    @staticmethod
    def _generate_alert(pulse_score: float, tvl_change: float, is_anomaly: bool) -> str | None:
        if is_anomaly and tvl_change < -15:
            return "⚠️ Unusual large outflow detected"
        if is_anomaly and tvl_change > 25:
            return "📈 Whale activity: large inflow detected"
        if pulse_score < 25:
            return "🚨 Critical: heavy outflow risk"
        if pulse_score > 85:
            return "✨ Strong momentum: capital flowing in"
        return None

    @staticmethod
    def _default_pulse(protocol: str) -> Dict:
        return {
            "protocol": protocol,
            "pulse_score": 50,
            "status": "💚 Healthy",
            "tvl": 0,
            "tvl_change_24h": 0,
            "net_flow": "$0",
            "is_anomaly": False,
            "alert": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def analyze_multiple_protocols(self, protocol_data: List[Dict]) -> List[Dict]:
        results = [
            self.calculate_pulse_score(
                protocol=d.get("protocol") or d.get("project") or "unknown",
                current_tvl=float(d.get("tvl", 0) or 0),
                tvl_change_24h=float(d.get("tvl_change_24h", d.get("change_24h", 0)) or 0),
                transaction_volume=d.get("volume_24h"),
            )
            for d in protocol_data
        ]
        results.sort(key=lambda x: x["pulse_score"], reverse=True)
        return results

    def get_vault_aggregate_pulse(self, vault_allocations: Dict[str, float], pulse_data: List[Dict]) -> Dict:
        total_score = 0.0
        total_weight = 0.0
        for protocol, weight in (vault_allocations or {}).items():
            pulse = next((p for p in pulse_data if p["protocol"] == protocol), None)
            if pulse:
                total_score += pulse["pulse_score"] * (weight / 100)
                total_weight += weight / 100
        if total_weight == 0:
            return {"vault_pulse": 50, "status": "💚 Healthy"}
        vault_pulse = round(total_score, 1)
        return {"vault_pulse": vault_pulse, "status": self._determine_status(vault_pulse)}


# Singleton ---------------------------------------------------------------------
_analyzer: LiquidityPulseAnalyzer | None = None


def get_pulse_analyzer() -> LiquidityPulseAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = LiquidityPulseAnalyzer()
    return _analyzer
