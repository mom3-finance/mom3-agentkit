from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ExecutionMarket:
    market_id: str
    chain_id: int
    chain: str
    project: str
    symbol: str
    contract: str
    asset_address: str
    asset_decimals: int
    execution_type: str
    position_symbol: str

    def as_dict(self) -> dict:
        return asdict(self)


# Only exact DefiLlama pools that have been matched to official protocol
# deployments are executable. Everything else remains discovery-only.
EXECUTION_MARKETS: dict[str, ExecutionMarket] = {
    # Aave V3 native USDC reserves.
    "d9fa8e14-0447-4207-9ae8-7810199dfa1f": ExecutionMarket(
        market_id="d9fa8e14-0447-4207-9ae8-7810199dfa1f",
        chain_id=42161,
        chain="Arbitrum",
        project="aave-v3",
        symbol="USDC",
        contract="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        asset_address="0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        asset_decimals=6,
        execution_type="aave-v3",
        position_symbol="aUSDC",
    ),
    "7e0661bf-8cf3-45e6-9424-31916d4c7b84": ExecutionMarket(
        market_id="7e0661bf-8cf3-45e6-9424-31916d4c7b84",
        chain_id=8453,
        chain="Base",
        project="aave-v3",
        symbol="USDC",
        contract="0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
        asset_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        asset_decimals=6,
        execution_type="aave-v3",
        position_symbol="aUSDC",
    ),
    # Compound III native USDC Comet proxies.
    "d9c395b9-00d0-4426-a6b3-572a6dd68e54": ExecutionMarket(
        market_id="d9c395b9-00d0-4426-a6b3-572a6dd68e54",
        chain_id=42161,
        chain="Arbitrum",
        project="compound-v3",
        symbol="USDC",
        contract="0x9c4ec768c28520B50860ea7a15bd7213a9fF58bf",
        asset_address="0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        asset_decimals=6,
        execution_type="compound-v3",
        position_symbol="cUSDCv3",
    ),
    "0c8567f8-ba5b-41ad-80de-00a71895eb19": ExecutionMarket(
        market_id="0c8567f8-ba5b-41ad-80de-00a71895eb19",
        chain_id=8453,
        chain="Base",
        project="compound-v3",
        symbol="USDC",
        contract="0xb125E6687d4313864e53df431d5425969c15Eb2F",
        asset_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        asset_decimals=6,
        execution_type="compound-v3",
        position_symbol="cUSDCv3",
    ),
    # Listed Morpho Vault V1 markets matched by symbol, asset, chain, and TVL.
    "aebb9f47-d15b-4671-8fe3-debb6e913ae2": ExecutionMarket(
        market_id="aebb9f47-d15b-4671-8fe3-debb6e913ae2",
        chain_id=42161,
        chain="Arbitrum",
        project="morpho-blue",
        symbol="GTUSDCC",
        contract="0x7e97fa6893871A2751B5fE961978DCCb2c201E65",
        asset_address="0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        asset_decimals=6,
        execution_type="morpho-vault-v1",
        position_symbol="gtUSDCc",
    ),
    "e0672197-9f3e-4414-bca5-e6b4c90aa469": ExecutionMarket(
        market_id="e0672197-9f3e-4414-bca5-e6b4c90aa469",
        chain_id=8453,
        chain="Base",
        project="morpho-blue",
        symbol="GTUSDCP",
        contract="0xeE8F4eC5672F09119b96Ab6fB59C27E1b7e44b61",
        asset_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        asset_decimals=6,
        execution_type="morpho-vault-v1",
        position_symbol="gtUSDCp",
    ),
}

DISCOVERY_PROJECTS = {"aave-v3", "compound-v3", "morpho-blue"}
PROTOCOL_LABELS = {
    "aave-v3": "Aave V3",
    "compound-v3": "Compound V3",
    "morpho-blue": "Morpho",
}
PROTOCOL_BASE_RISK = {"aave-v3": 2.8, "compound-v3": 3.6, "morpho-blue": 4.4}


def is_stablecoin_symbol(symbol: str) -> bool:
    value = symbol.upper().replace(" ", "")
    return "USDC" in value or value in {"USDT", "USDT0"}


def execution_market_for(market_id: str, project: str, symbol: str, chain_id: int) -> ExecutionMarket | None:
    market = EXECUTION_MARKETS.get(market_id)
    if not market:
        return None
    if market.project != project or market.chain_id != chain_id:
        return None
    if symbol.upper() != market.symbol:
        return None
    return market
