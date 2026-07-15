"""Aave yield adapter using free DefiLlama discovery and optional RPC verification."""
from __future__ import annotations

from typing import Optional

from app.services.aave_reader import AaveReader
from app.services.yield_adapter import DefiLlamaAdapter, _number


class AaveAdapter(DefiLlamaAdapter):
    adapter_name = "aave"
    projects = ("aave",)

    def __init__(self, collector=None, reader: Optional[AaveReader] = None) -> None:
        super().__init__(collector)
        self.reader = reader or AaveReader()

    def fetch_markets(self, chain_id: Optional[int] = None) -> list[dict]:
        markets = super().fetch_markets(chain_id)
        # DefiLlama remains the free discovery source. For configured Aave
        # chains, attach the live USDC reserve reading when it succeeds.
        chain_ids = [chain_id] if chain_id is not None else self.reader.supported_chain_ids()
        for cid in chain_ids:
            try:
                live = self.reader.read_market(cid)
            except Exception:
                continue
            for market in markets:
                if market["chain_id"] == cid and "usdc" in market["symbol"].lower():
                    market.update({
                        "apy": _number(live.get("apy")),
                        "tvl": _number(live.get("tvl")),
                        "utilization": _number(live.get("utilization")),
                        "verified_onchain": True,
                        "source": "aave-rpc",
                    })
        return markets
