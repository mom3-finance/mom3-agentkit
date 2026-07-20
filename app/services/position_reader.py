from __future__ import annotations

import os
import re

import requests

from web3 import Web3


ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")

RPC_URLS = {
    42161: ("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc"),
    8453: ("AAVE_RPC_8453", "https://mainnet.base.org"),
}

SOLANA_ADDRESS_PATTERN = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
KAMINO_API_URL = "https://api.kamino.finance/kamino-market/{market}/users/{user}/obligations"

ERC20_BALANCE_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]

AAVE_RESERVE_ABI = [
    {
        "inputs": [{"name": "asset", "type": "address"}],
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
        "stateMutability": "view",
        "type": "function",
    }
]

ERC4626_POSITION_ABI = [
    {
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "maxWithdraw",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


class PositionReader:
    """Reads wallet and supplied balances for an allowlisted yield market."""

    def read(self, market: dict, user_address: str) -> dict:
        chain_id = int(market["chain_id"])
        execution = market.get("execution") or {}
        if chain_id == 101:
            return self._read_kamino(market, user_address, execution)

        if not ADDRESS_PATTERN.fullmatch(user_address or ""):
            raise ValueError("A valid EVM Universal Account address is required.")
        if not execution.get("enabled"):
            raise ValueError("This market does not have an execution adapter.")

        rpc_setting = RPC_URLS.get(chain_id)
        if not rpc_setting:
            raise ValueError(f"Position reads are not configured for chain {chain_id}.")
        rpc_url = os.getenv(rpc_setting[0], rpc_setting[1])
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))

        account = Web3.to_checksum_address(user_address)
        asset_address = Web3.to_checksum_address(execution["asset_address"])
        contract_address = Web3.to_checksum_address(execution["contract"])
        decimals = int(execution["asset_decimals"])
        asset = w3.eth.contract(address=asset_address, abi=ERC20_BALANCE_ABI)
        wallet_atomic = int(asset.functions.balanceOf(account).call())
        execution_type = str(execution["type"])

        if execution_type == "aave-v3":
            pool = w3.eth.contract(address=contract_address, abi=AAVE_RESERVE_ABI)
            reserve = pool.functions.getReserveData(asset_address).call()
            position_contract = Web3.to_checksum_address(reserve[8])
            supplied_atomic = int(
                w3.eth.contract(address=position_contract, abi=ERC20_BALANCE_ABI)
                .functions.balanceOf(account)
                .call()
            )
        elif execution_type == "compound-v3":
            position_contract = contract_address
            supplied_atomic = int(
                w3.eth.contract(address=contract_address, abi=ERC20_BALANCE_ABI)
                .functions.balanceOf(account)
                .call()
            )
        elif execution_type == "morpho-vault-v1":
            position_contract = contract_address
            supplied_atomic = int(
                w3.eth.contract(address=contract_address, abi=ERC4626_POSITION_ABI)
                .functions.maxWithdraw(account)
                .call()
            )
        else:
            raise ValueError("Unsupported position adapter.")

        scale = 10 ** decimals
        return {
            "market_id": market["market_id"],
            "protocol": market["protocol"],
            "project": market["project"],
            "chain": market["chain"],
            "chain_id": chain_id,
            "asset": {
                "symbol": market["asset"],
                "address": execution["asset_address"],
                "decimals": decimals,
            },
            "position_symbol": execution.get("position_symbol") or market["asset"],
            "position_contract": position_contract,
            "wallet_balance": wallet_atomic / scale,
            "wallet_balance_atomic": str(wallet_atomic),
            "supplied_balance": supplied_atomic / scale,
            "supplied_balance_atomic": str(supplied_atomic),
            "source": "protocol-onchain",
        }

    def _read_kamino(self, market: dict, user_address: str, execution: dict) -> dict:
        if not SOLANA_ADDRESS_PATTERN.fullmatch(user_address or ""):
            raise ValueError("A valid Solana wallet address is required.")
        if not execution.get("enabled"):
            raise ValueError("This market does not have an execution adapter.")

        market_address = execution.get("contract") or market.get("contract")
        if not market_address:
            raise ValueError("Kamino market address is not configured.")
        url = KAMINO_API_URL.format(market=market_address, user=user_address)
        try:
            response = requests.get(
                url,
                params={"env": "mainnet-beta"},
                timeout=float(os.getenv("KAMINO_POSITION_TIMEOUT_SECONDS", "5")),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise ValueError("Kamino position service is temporarily unavailable.") from exc

        obligations = payload if isinstance(payload, list) else payload.get("obligations", [])
        if isinstance(obligations, dict):
            obligations = [obligations]
        supplied = 0.0
        for obligation in obligations:
            loan_info = obligation.get("loanInfo") or obligation.get("loan_info") or obligation
            collateral = loan_info.get("collateral") or {}
            for deposit in collateral.get("deposits", []) or []:
                if deposit.get("tokenMint") == execution.get("asset_address"):
                    supplied += float(deposit.get("tokenAmount") or 0)

        decimals = int(execution.get("asset_decimals") or 6)
        return {
            "market_id": market["market_id"],
            "protocol": market["protocol"],
            "project": market["project"],
            "chain": market["chain"],
            "chain_id": 101,
            "asset": {
                "symbol": market["asset"],
                "address": execution.get("asset_address"),
                "decimals": decimals,
            },
            "position_symbol": execution.get("position_symbol") or market["asset"],
            "position_contract": market_address,
            "wallet_balance": 0,
            "wallet_balance_atomic": "0",
            "supplied_balance": supplied,
            "supplied_balance_atomic": str(int(round(supplied * (10 ** decimals)))),
            "source": "kamino-api",
        }
