import os
from datetime import datetime, timezone

from web3 import Web3

# Flat getReserveData ABI (matches the working Next.js aave market route) so the
# decoded return is a plain tuple indexed by position, not a nested `data` struct.
# See _RESERVE_ABI / _ERC20_ABI below.


class AaveReader:
    # Verified per-chain Aave V3 Pool + USDC addresses. The Pool contract is deployed
    # separately on each chain — never reuse one address across chains. Only chains with
    # a verified on-chain deployment live here; everything else falls back to DefiLlama.
    MARKETS = {
        1: {"network": "Ethereum", "rpc": "https://eth.llamarpc.com", "pool": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2", "usdc": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"},
        42161: {"network": "Arbitrum One", "rpc": "https://arb1.arbitrum.io/rpc", "pool": "0x794a61358D6845594F94dc1DB02A252b5b4814aD", "usdc": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"},
        8453: {"network": "Base", "rpc": "https://mainnet.base.org", "pool": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5", "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"},
    }

    def __init__(self):
        self.default_rpc = os.getenv("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")

    def supported_chain_ids(self):
        return sorted(self.MARKETS.keys())

    def read_market(self, chain_id: int = 42161):
        market = self.MARKETS.get(chain_id)
        if not market:
            raise ValueError(f"Aave market is not configured for chain {chain_id}")
        rpc = os.getenv(f"AAVE_RPC_{chain_id}", market["rpc"] if chain_id != 42161 else self.default_rpc)
        w3 = Web3(Web3.HTTPProvider(rpc))
        pool_address = Web3.to_checksum_address(market["pool"])
        usdc_address = Web3.to_checksum_address(market["usdc"])
        pool = w3.eth.contract(address=pool_address, abi=_RESERVE_ABI)
        usdc = w3.eth.contract(address=usdc_address, abi=_ERC20_ABI)
        reserve = pool.functions.getReserveData(usdc_address).call()
        liquidity_rate = int(reserve[2])  # currentLiquidityRate
        a_token = reserve[8]
        debt_token_address = reserve[10]  # variableDebtTokenAddress
        apy = ((1 + (liquidity_rate / 10**27) / 31_536_000) ** 31_536_000 - 1) * 100
        available = usdc.functions.balanceOf(pool_address).call()
        debt = (w3.eth.contract(address=debt_token_address, abi=_ERC20_ABI).functions.balanceOf(pool_address).call()
                if int(debt_token_address, 16) else 0)
        supplied = available + debt
        utilization = (debt / supplied * 100) if supplied else 0
        return {
            "network": market["network"], "chain_id": chain_id, "protocol": "Aave V3", "asset": "USDC",
            "apy": round(apy, 4), "tvl": supplied / 10**6, "utilization": round(utilization, 2),
            "a_token": a_token, "source": "aave-pool-onchain",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }


# Explicit dict ABIs used by read_market (kept module-level so they're built once).
_RESERVE_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
        "name": "getReserveData",
        "outputs": [
            {"name": "configuration", "type": "uint256"},
            {"name": "liquidityIndex", "type": "uint128"},
            {"name": "currentLiquidityRate", "type": "uint128"},
            {"name": "variableBorrowIndex", "type": "uint128"},
            {"name": "currentVariableBorrowRate", "type": "uint128"},
            {"name": "currentStableBorrowRate", "type": "uint128"},
            {"name": "lastUpdateTimestamp", "type": "uint40"},
            {"name": "id", "type": "uint16"},
            {"name": "aTokenAddress", "type": "address"},
            {"name": "stableDebtTokenAddress", "type": "address"},
            {"name": "variableDebtTokenAddress", "type": "address"},
        ],
        "stateMutability": "view", "type": "function",
    }
]
_ERC20_ABI = [
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"}
]
