import unittest

from app.modules.portfolio_intelligence import PortfolioIntelligenceEngine, PortfolioIntelligenceService


WALLET = "0x1111111111111111111111111111111111111111"


def wallet_asset(symbol, value, chain="Base", chain_id=8453, address="0xasset"):
    return {
        "id": f"{chain_id}-{symbol}",
        "symbol": symbol,
        "name": symbol,
        "balance": value,
        "amount_in_usd": value,
        "chain": chain,
        "chain_id": chain_id,
        "token_address": address,
    }


def position(value=100, symbol="USDC", chain="Base", chain_id=8453):
    return {
        "market_id": "market-1",
        "protocol": "Aave V3",
        "project": "aave-v3",
        "chain": chain,
        "chain_id": chain_id,
        "asset": symbol,
        "position_symbol": "aUSDC",
        "position_contract": "0xreceipt",
        "supplied_balance": value,
        "supplied_balance_atomic": str(int(value * 1_000_000)),
        "amount_in_usd": value,
        "apy": 4.5,
        "tvl": 100_000_000,
        "risk_score": 2.8,
        "source": "test",
    }


class PortfolioEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = PortfolioIntelligenceEngine()

    def test_empty_portfolio_has_explicit_empty_state(self):
        result = self.engine.analyze([], [], scanned_market_count=6, failed_market_count=0)
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["summary"]["health_score"], 0)
        self.assertEqual(result["positions"], [])

    def test_detects_concentration_and_idle_stablecoins(self):
        result = self.engine.analyze(
            [wallet_asset("USDC", 900), wallet_asset("ETH", 100, chain="Arbitrum", chain_id=42161)],
            [],
            scanned_market_count=6,
            failed_market_count=0,
        )
        self.assertEqual(result["summary"]["largest_asset"], "USDC")
        self.assertEqual(result["summary"]["largest_asset_allocation"], 90)
        ids = {item["id"] for item in result["recommendations"]}
        self.assertIn("reduce-asset-concentration", ids)
        self.assertIn("activate-idle-stables", ids)

    def test_deduplicates_protocol_receipt_token(self):
        receipt = wallet_asset("aUSDC", 100, address="0xreceipt")
        result = self.engine.analyze(
            [receipt],
            [position(100)],
            scanned_market_count=1,
            failed_market_count=0,
        )
        self.assertEqual(result["summary"]["total_value"], 100)
        self.assertEqual(result["coverage"]["receipt_value_deduplicated"], 100)

    def test_partial_rpc_failure_reduces_confidence_without_hiding_positions(self):
        result = self.engine.analyze(
            [wallet_asset("USDC", 200)],
            [position(50)],
            scanned_market_count=6,
            failed_market_count=2,
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["positions"]), 1)
        self.assertEqual(result["coverage"]["failed_market_reads"], 2)


class FakeCatalog:
    def list_markets(self, execution_only=False):
        return [
            {
                "market_id": "market-1",
                "protocol": "Aave V3",
                "project": "aave-v3",
                "chain": "Base",
                "chain_id": 8453,
                "asset": "USDC",
                "apy": 4.5,
                "tvl": 100_000_000,
                "risk_score": 2.8,
                "execution": {"enabled": True},
            },
            {
                "market_id": "market-2",
                "protocol": "Compound V3",
                "project": "compound-v3",
                "chain": "Arbitrum",
                "chain_id": 42161,
                "asset": "USDC",
                "apy": 3.8,
                "tvl": 50_000_000,
                "risk_score": 3.6,
                "execution": {"enabled": True},
            },
        ]


class FakePositionReader:
    def read(self, market, _user_address):
        if market["market_id"] == "market-2":
            raise TimeoutError("RPC timeout")
        return {
            **position(25, chain=market["chain"], chain_id=market["chain_id"]),
            "market_id": market["market_id"],
            "asset": {"symbol": "USDC"},
        }


class PortfolioServiceTests(unittest.TestCase):
    def test_service_keeps_successful_positions_when_one_rpc_fails(self):
        service = PortfolioIntelligenceService(FakeCatalog(), FakePositionReader())
        result = service.analyze(WALLET, [wallet_asset("USDC", 100)])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["positions"]), 1)
        self.assertEqual(result["coverage"]["failed_market_reads"], 1)


if __name__ == "__main__":
    unittest.main()
