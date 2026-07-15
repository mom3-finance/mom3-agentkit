"""Free price routing for UI valuation and data freshness checks.

Priority:
1. Chainlink AggregatorV3 when a feed address is configured in the environment.
2. DefiLlama's open coin-price endpoint for broad, keyless coverage.

Pyth can be added for a chain/feed where its contract and feed ID are configured;
the adapter deliberately does not invent feed IDs or silently use stale prices.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import requests
from web3 import Web3

_CHAINLINK_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"type": "uint80"}, {"type": "int256"}, {"type": "uint256"},
            {"type": "uint256"}, {"type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]


class PriceOracle:
    def __init__(self, timeout: int = 10, max_age_seconds: int = 900) -> None:
        self.timeout = timeout
        self.max_age_seconds = max_age_seconds

    def _chainlink_feed(self, chain_id: int, token: str) -> Optional[str]:
        key = f"CHAINLINK_FEED_{chain_id}_{token.lower().replace('-', '_')}"
        return os.getenv(key)

    def _rpc_url(self, chain_id: int) -> Optional[str]:
        return os.getenv(f"AAVE_RPC_{chain_id}") or os.getenv(f"RPC_URL_{chain_id}")

    def _chainlink(self, chain_id: int, token: str) -> Optional[dict]:
        feed_address = self._chainlink_feed(chain_id, token)
        rpc_url = self._rpc_url(chain_id)
        if not feed_address or not rpc_url:
            return None
        try:
            web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": self.timeout}))
            feed = web3.eth.contract(address=Web3.to_checksum_address(feed_address), abi=_CHAINLINK_ABI)
            decimals = int(feed.functions.decimals().call())
            _, answer, _, updated_at, _ = feed.functions.latestRoundData().call()
            updated_at = int(updated_at)
            if answer <= 0 or time.time() - updated_at > self.max_age_seconds:
                return None
            return {
                "price_usd": float(answer) / (10 ** decimals),
                "source": "chainlink",
                "updated_at": updated_at,
                "confidence": 0.98,
            }
        except Exception:
            return None

    def prices(self, tokens_by_chain: dict[int, list[str]]) -> dict[str, dict]:
        """Fetch keyless prices for `chain_id -> token address` mappings."""
        result: dict[str, dict] = {}
        pending: list[str] = []
        for chain_id, tokens in tokens_by_chain.items():
            for token in tokens:
                key = f"{chain_id}:{token.lower()}"
                quote = self._chainlink(chain_id, token)
                if quote:
                    result[key] = quote
                else:
                    pending.append(key)

        if pending:
            try:
                response = requests.get(
                    "https://api.llama.fi/prices/current/" + ",".join(pending),
                    timeout=self.timeout,
                )
                response.raise_for_status()
                coins = response.json().get("coins", {})
                now = int(time.time())
                for key, coin in coins.items():
                    price = coin.get("price")
                    if isinstance(price, (int, float)) and price > 0:
                        result[key.lower()] = {
                            "price_usd": float(price),
                            "source": "defillama-coins",
                            "updated_at": int(coin.get("timestamp") or now),
                            "confidence": 0.85,
                            "symbol": coin.get("symbol"),
                        }
            except Exception:
                pass
        return result
