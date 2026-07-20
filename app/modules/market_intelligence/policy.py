from __future__ import annotations

import re
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


# Execution adapters are selected by protocol + chain + supplied asset. Pool
# UUIDs are live data identifiers and must never be hardcoded into this policy.
# Do not add a symbol here until its protocol reserve/mint has been verified.
EXECUTION_DEPLOYMENTS: dict[tuple[str, int, str], dict] = {
    ("kamino-lend", 101, "USDC"): {"chain": "Solana", "symbol": "USDC", "contract": "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF", "asset_address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "asset_decimals": 6, "execution_type": "kamino-lend", "position_symbol": "kUSDC"},
    ("kamino-lend", 101, "SOL"): {"chain": "Solana", "symbol": "SOL", "contract": "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF", "asset_address": "So11111111111111111111111111111111111111112", "asset_decimals": 9, "execution_type": "kamino-lend", "position_symbol": "kSOL"},
    ("aave-v3", 42161, "USDC"): {"chain": "Arbitrum", "symbol": "USDC", "contract": "0x794a61358D6845594F94dc1DB02A252b5b4814aD", "asset_address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "asset_decimals": 6, "execution_type": "aave-v3", "position_symbol": "aUSDC"},
    ("aave-v3", 42161, "USDT"): {"chain": "Arbitrum", "symbol": "USDT", "contract": "0x794a61358D6845594F94dc1DB02A252b5b4814aD", "asset_address": "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9", "asset_decimals": 6, "execution_type": "aave-v3", "position_symbol": "aUSDT"},
    ("aave-v3", 8453, "USDC"): {"chain": "Base", "symbol": "USDC", "contract": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5", "asset_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "asset_decimals": 6, "execution_type": "aave-v3", "position_symbol": "aUSDC"},
    ("compound-v3", 42161, "USDC"): {"chain": "Arbitrum", "symbol": "USDC", "contract": "0x9c4ec768c28520B50860ea7a15bd7213a9fF58bf", "asset_address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "asset_decimals": 6, "execution_type": "compound-v3", "position_symbol": "cUSDCv3"},
    ("compound-v3", 8453, "USDC"): {"chain": "Base", "symbol": "USDC", "contract": "0xb125E6687d4313864e53df431d5425969c15Eb2F", "asset_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "asset_decimals": 6, "execution_type": "compound-v3", "position_symbol": "cUSDCv3"},
}

DISCOVERY_PROJECTS = {"morpho-blue", "kamino-lend", "kamino-liquidity"}
PROTOCOL_LABELS = {
    "aave-v2": "Aave V2",
    "aave-v3": "Aave V3",
    "aave-v4": "Aave V4",
    "compound-v2": "Compound V2",
    "compound-v3": "Compound V3",
    "morpho-blue": "Morpho",
    "kamino-lend": "Kamino Lend",
    "kamino-liquidity": "Kamino Liquidity",
}
PROTOCOL_BASE_RISK = {
    "aave-v2": 3.4,
    "aave-v3": 2.8,
    "aave-v4": 3.0,
    "compound-v2": 4.2,
    "compound-v3": 3.6,
    "morpho-blue": 4.4,
    "kamino-lend": 4.0,
    "kamino-liquidity": 5.8,
}


def is_discovery_project(project: str) -> bool:
    """Allow supported protocol families without pretending every pool is executable."""
    normalized = str(project or "").strip().lower()
    return (
        normalized in DISCOVERY_PROJECTS
        or bool(re.fullmatch(r"aave-v\d+", normalized))
        or bool(re.fullmatch(r"compound-v\d+", normalized))
    )


def canonical_asset_symbol(symbol: str) -> str:
    value = str(symbol or "").upper().replace("₮", "T").split("-")[0].split("/")[0].strip()
    return "USDT" if value in {"USDT", "USDT0"} else value


def execution_market_for(market_id: str, project: str, symbol: str, chain_id: int) -> ExecutionMarket | None:
    deployment = EXECUTION_DEPLOYMENTS.get((project.lower(), int(chain_id), canonical_asset_symbol(symbol)))
    if not deployment:
        return None
    return ExecutionMarket(market_id=market_id, project=project, chain_id=chain_id, **deployment)
