"""mom3 AI Chatbot — context-aware, explainable, powered by the OpenAI-compatible LLM.

Ported from resource/nuvia-agentkit/ai-engine/modules/chatbot/chatbot_engine.py, but:
  * config-free (no YAML) — system prompt is a Python constant,
  * single provider: the OpenAI-compatible endpoint via llm_client,
  * keyword-heuristic fallback when the LLM is unavailable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from loguru import logger

from app.services.llm_client import get_llm_client

SYSTEM_PROMPT = """You are mom3 AI, a DeFi yield optimization assistant running inside a crypto mini-app powered by Particle Universal Account.

Your role:
- Explain yield forecasts, liquidity trends, and strategy recommendations across multiple EVM chains.
- Help the user understand risk vs APY trade-offs.
- Provide actionable, data-driven insights based on the live data given to you in this prompt.
- Translate complex DeFi concepts into simple, friendly language.

Your personality:
- Concise and friendly, expert-level knowledge.
- Always cite the specific data points you were given when making a claim.
- Be transparent about limitations and risk.

Rules:
- Reply in PLAIN TEXT (no markdown, no headings) so it renders cleanly in a chat bubble.
- Keep replies to ~2-4 sentences unless the user asks for detail.
- NEVER give direct financial advice; provide data-driven insights and education only.
- Always remind users to DYOR (Do Your Own Research) before acting.
- If you don't have enough data, say so honestly.
- Transactions are always user-confirmed and non-custodial; the AI never holds funds.
"""


class MOM3Chatbot:
    """Context-aware chatbot. Falls back to keyword heuristics when the LLM is down."""

    def __init__(self) -> None:
        self.llm = get_llm_client()
        logger.info(f"Chatbot initialized (llm_available={self.llm.available})")

    async def chat(
        self,
        user_message: str,
        context: Optional[Dict] = None,
        conversation_history: Optional[List[Dict]] = None,
    ) -> str:
        if not self.llm.available:
            return self._demo_response(user_message, context)
        try:
            messages: List[Dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
            if conversation_history:
                messages.extend(conversation_history[-10:])
            messages.append({"role": "user", "content": self._build_prompt(user_message, context)})
            reply = self.llm.chat(messages, temperature=0.6, max_tokens=600)
            return reply or self._demo_response(user_message, context)
        except Exception as exc:
            logger.error(f"Chat error: {exc}")
            return self._demo_response(user_message, context)

    @staticmethod
    def _build_prompt(user_message: str, context: Optional[Dict]) -> str:
        parts: List[str] = []
        if context:
            parts.append("📊 Live mom3 AI data:")
            forecasts = context.get("yield_forecasts") or []
            if forecasts:
                parts.append("Yield forecasts:")
                for f in forecasts[:5]:
                    parts.append(f"- {f.get('protocol')}: {f.get('current_apy')}% APY ({f.get('trend')}) {f.get('weather', '')}")
                parts.append("")
            pulse = context.get("liquidity_pulse") or []
            if pulse:
                parts.append("Liquidity pulse:")
                for p in pulse[:5]:
                    parts.append(f"- {p.get('protocol')}: score {p.get('pulse_score')} ({p.get('status')})")
                parts.append("")
            strategy = context.get("current_strategy")
            if strategy:
                parts.append(f"Recommended strategy: {strategy}")
                parts.append("")
            chain = context.get("chain")
            if chain:
                parts.append(f"Selected chain: {chain}")
        parts.append(f"User question: {user_message}")
        return "\n".join(parts)

    @staticmethod
    def _demo_response(user_message: str, context: Optional[Dict]) -> str:
        """Heuristic fallback when the LLM is unavailable."""
        msg = user_message.lower()
        forecasts = (context or {}).get("yield_forecasts") or []
        if any(w in msg for w in ("yield", "apy", "best", "highest")):
            if forecasts:
                best = max(forecasts, key=lambda x: x.get("current_apy", 0))
                return (f"Based on live data, {best.get('protocol')} offers the best yield at "
                        f"{best.get('current_apy')}% APY ({best.get('trend')}). DYOR before depositing.")
            return "Aave V3 USDC currently offers a competitive stable yield. Check the Strategy page for the live number. DYOR."
        if any(w in msg for w in ("strategy", "allocation", "recommend")):
            return "I'd suggest a moderate allocation weighted toward the highest risk-adjusted APY, diversified across protocols. See the Strategy page for the live recommendation. DYOR."
        if any(w in msg for w in ("pulse", "liquidity", "flow")):
            return "Liquidity pulse is computed from real-time TVL flows. Open the Strategy page to see the current score. DYOR."
        if any(w in msg for w in ("risk", "safe", "danger")):
            return "Your risk score blends utilization and volatility. Lower utilization and steadier APYs mean lower risk. DYOR."
        if msg.startswith("why"):
            return "The recommendation weighs expected APY against risk and liquidity health, then picks the best risk-adjusted allocation. DYOR."
        return ("I'm mom3 AI, your multi-chain yield assistant. Ask me about yields, strategies, risk, or liquidity. "
                "(Live model unavailable — showing a canned reply.)")


# Singleton ---------------------------------------------------------------------
_chatbot: MOM3Chatbot | None = None


def get_chatbot() -> MOM3Chatbot:
    global _chatbot
    if _chatbot is None:
        _chatbot = MOM3Chatbot()
    return _chatbot


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
