import unittest

from app.modules.agent_core.execution import ExecutionIntentError, ExecutionIntentService
from app.modules.market_intelligence.catalog import MarketCatalog


MARKET = {
    "market_id": "aave-base-usdc",
    "protocol": "Aave V3",
    "project": "aave-v3",
    "chain": "Base",
    "chain_id": 8453,
    "asset": "USDC",
    "source": "test",
    "execution": {
        "enabled": True,
        "actions": ["supply", "withdraw"],
        "type": "aave-v3",
        "asset_decimals": 6,
        "asset_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "contract": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
        "position_symbol": "aUSDC",
    },
}


class FakeCatalog:
    def __init__(self, market=MARKET):
        self.market = market

    def get_market(self, market_id):
        return self.market if market_id == self.market["market_id"] else None


class ExecutionIntentTests(unittest.TestCase):
    def setUp(self):
        self.service = ExecutionIntentService(FakeCatalog())
        self.user = "0x1111111111111111111111111111111111111111"

    def test_builds_allowlisted_supply_calls(self):
        intent = self.service.create_intent(MARKET["market_id"], "supply", "12.345678", self.user)
        self.assertEqual(intent["amount_atomic"], "12345678")
        self.assertEqual(intent["chain_id"], 8453)
        self.assertEqual([call["method"] for call in intent["calls"]], ["approve", "supply"])
        self.assertTrue(intent["policy"]["requires_eip7702"])

    def test_builds_allowlisted_withdraw_call(self):
        intent = self.service.create_intent(MARKET["market_id"], "withdraw", "1", self.user)
        self.assertEqual([call["method"] for call in intent["calls"]], ["withdraw"])
        self.assertFalse(intent["policy"]["cross_chain_funding_supported"])

    def test_builds_calls_for_every_protocol_adapter(self):
        adapters = {
            "aave-v3": "supply",
            "compound-v3": "supply",
            "morpho-vault-v1": "deposit",
        }
        for execution_type, supply_method in adapters.items():
            with self.subTest(execution_type=execution_type):
                market = {
                    **MARKET,
                    "execution": {**MARKET["execution"], "type": execution_type},
                }
                service = ExecutionIntentService(FakeCatalog(market))
                supply = service.create_intent(market["market_id"], "supply", "1", self.user)
                withdraw = service.create_intent(market["market_id"], "withdraw", "1", self.user)
                self.assertEqual([call["method"] for call in supply["calls"]], ["approve", supply_method])
                self.assertEqual([call["method"] for call in withdraw["calls"]], ["withdraw"])

    def test_rejects_unknown_market(self):
        with self.assertRaises(ExecutionIntentError):
            self.service.create_intent("unknown", "supply", "1", self.user)

    def test_rejects_invalid_address(self):
        with self.assertRaises(ExecutionIntentError):
            self.service.create_intent(MARKET["market_id"], "supply", "1", "not-an-address")


class CatalogExecutionAssetTests(unittest.TestCase):
    class Collector:
        def supported_chain_ids(self):
            return [42161]

        def chain_name(self, chain_id):
            return "Arbitrum" if chain_id == 42161 else None

        def _chain_matches(self, chain, expected):
            return chain == expected

        def fetch_all_pools(self, force=False):
            return [
                {"pool": "aave-usdc", "project": "aave-v3", "chain": "Arbitrum", "symbol": "USDC", "tvlUsd": 10_000_000, "apy": 4},
                {"pool": "aave-usdt", "project": "aave-v3", "chain": "Arbitrum", "symbol": "USDT", "tvlUsd": 10_000_000, "apy": 4},
                {"pool": "aave-weth", "project": "aave-v3", "chain": "Arbitrum", "symbol": "WETH", "tvlUsd": 10_000_000, "apy": 4},
            ]

    def test_keeps_each_verified_asset_and_rejects_unmapped_assets(self):
        markets = MarketCatalog(collector=self.Collector()).build_live_markets()
        self.assertEqual({market["asset"] for market in markets}, {"USDC", "USDT"})
        self.assertEqual({market["execution"]["position_symbol"] for market in markets}, {"aUSDC", "aUSDT"})


if __name__ == "__main__":
    unittest.main()
