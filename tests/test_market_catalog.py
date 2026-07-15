import unittest

from app.modules.market_intelligence.catalog import MarketCatalog


class FakeCollector:
    def chain_name(self, chain_id):
        return {42161: "Arbitrum", 8453: "Base"}.get(chain_id)

    def fetch_chain_yields(self, chain_id):
        pool_id = {
            42161: "d9fa8e14-0447-4207-9ae8-7810199dfa1f",
            8453: "7e0661bf-8cf3-45e6-9424-31916d4c7b84",
        }[chain_id]
        return [
            {
                "pool": pool_id,
                "project": "aave-v3",
                "symbol": "USDC",
                "stablecoin": True,
                "exposure": "single",
                "ilRisk": "no",
                "apy": 3.2,
                "apyBase": 3.2,
                "apyReward": 0,
                "tvlUsd": 30_000_000,
                "apyPct1D": 0.02,
                "apyPct7D": 0.1,
                "apyPct30D": 0.3,
            },
            {
                "pool": f"lp-{chain_id}",
                "project": "aave-v3",
                "symbol": "USDC-ETH",
                "stablecoin": False,
                "exposure": "multi",
                "ilRisk": "yes",
                "apy": 80,
                "tvlUsd": 100_000,
            },
        ]


class MarketCatalogTests(unittest.TestCase):
    def test_catalog_keeps_only_stable_single_asset_markets(self):
        markets = MarketCatalog(FakeCollector()).list_markets()
        self.assertEqual(len(markets), 2)
        self.assertTrue(all(market["asset"] == "USDC" for market in markets))
        self.assertTrue(all(market["execution"]["enabled"] for market in markets))
        self.assertEqual({market["chain_id"] for market in markets}, {42161, 8453})


if __name__ == "__main__":
    unittest.main()
