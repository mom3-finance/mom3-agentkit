from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Literal

from app.core.config import settings
from app.modules.market_intelligence import MarketCatalog, get_market_catalog


ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOLANA_ADDRESS_PATTERN = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
ExecutionAction = Literal["supply", "withdraw"]


class ExecutionIntentError(ValueError):
    pass


class ExecutionIntentService:
    """Build allowlisted semantic intents; encoding and signing happen later."""

    def __init__(self, catalog: MarketCatalog | None = None) -> None:
        self.catalog = catalog or get_market_catalog()

    def create_intent(
        self,
        market_id: str,
        action: ExecutionAction,
        amount: str,
        user_address: str,
    ) -> dict:
        if action not in {"supply", "withdraw"}:
            raise ExecutionIntentError("Action must be supply or withdraw.")
        market = self.catalog.get_market(market_id)
        if not market:
            raise ExecutionIntentError("The selected market is not in the live MVP catalog.")
        execution = market.get("execution") or {}
        if not execution.get("enabled") or action not in (execution.get("actions") or []):
            raise ExecutionIntentError("This market is discovery-only and cannot be executed yet.")
        is_solana = int(market["chain_id"]) == 101
        if not (SOLANA_ADDRESS_PATTERN.fullmatch(user_address or "") if is_solana else ADDRESS_PATTERN.fullmatch(user_address or "")):
            raise ExecutionIntentError("A valid Solana wallet address is required." if is_solana else "A valid EVM Universal Account address is required.")

        value = self._amount(amount)
        decimals = int(execution["asset_decimals"])
        quantized = value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN)
        atomic = int(quantized * (10 ** decimals))
        if atomic <= 0:
            raise ExecutionIntentError("Amount is below the token precision.")

        canonical_amount = format(quantized, "f")
        fingerprint = f"{market_id}:{action}:{canonical_amount}:{user_address.lower()}"
        asset_address = str(execution["asset_address"])
        target = str(execution["contract"])
        execution_type = str(execution["type"])

        return {
            "intent_id": "m3i_" + hashlib.sha256(fingerprint.encode()).hexdigest()[:24],
            "market_id": market_id,
            "action": action,
            "protocol": market["protocol"],
            "project": market["project"],
            "execution_type": execution_type,
            "chain": market["chain"],
            "chain_id": market["chain_id"],
            "amount": canonical_amount,
            "amount_atomic": str(atomic),
            "asset": {
                "symbol": market["asset"],
                "address": asset_address,
                "decimals": decimals,
            },
            "position_symbol": execution.get("position_symbol") or market["asset"],
            "receiver": user_address,
            "calls": [] if is_solana else self._calls(
                execution_type,
                action,
                asset_address,
                target,
                str(atomic),
                user_address,
            ),
            "policy": {
                "execution_mode": "user-confirmed",
                "requires_eip7702": not is_solana,
                "cross_chain_funding_supported": action == "supply",
                "max_amount_usd": settings.maximum_intent_amount_usd,
                "slippage_bps": 100,
            },
            "source": market["source"],
        }

    @staticmethod
    def _amount(amount: str) -> Decimal:
        try:
            value = Decimal(str(amount))
        except (InvalidOperation, ValueError) as exc:
            raise ExecutionIntentError("Amount must be a valid decimal number.") from exc
        if not value.is_finite() or value <= 0:
            raise ExecutionIntentError("Amount must be greater than zero.")
        if value > Decimal(str(settings.maximum_intent_amount_usd)):
            raise ExecutionIntentError(
                f"MVP execution is limited to {settings.maximum_intent_amount_usd:g} USDC per intent."
            )
        return value

    @staticmethod
    def _calls(
        execution_type: str,
        action: ExecutionAction,
        asset: str,
        target: str,
        atomic: str,
        receiver: str,
    ) -> list[dict]:
        if action == "supply":
            protocol_call = {
                "aave-v3": {"to": target, "method": "supply", "args": [asset, atomic, receiver, 0]},
                "compound-v3": {"to": target, "method": "supply", "args": [asset, atomic]},
                "morpho-vault-v1": {"to": target, "method": "deposit", "args": [atomic, receiver]},
            }.get(execution_type)
            if not protocol_call:
                raise ExecutionIntentError("Unsupported supply adapter.")
            return [
                {"to": asset, "method": "approve", "args": [target, atomic]},
                protocol_call,
            ]

        protocol_call = {
            "aave-v3": {"to": target, "method": "withdraw", "args": [asset, atomic, receiver]},
            "compound-v3": {"to": target, "method": "withdraw", "args": [asset, atomic]},
            "morpho-vault-v1": {
                "to": target,
                "method": "withdraw",
                "args": [atomic, receiver, receiver],
            },
        }.get(execution_type)
        if not protocol_call:
            raise ExecutionIntentError("Unsupported withdraw adapter.")
        return [protocol_call]
