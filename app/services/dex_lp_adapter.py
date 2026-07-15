"""DEX LP adapter for free discovery of liquidity-pool yield markets."""
from app.services.yield_adapter import DefiLlamaAdapter


class DexLpAdapter(DefiLlamaAdapter):
    adapter_name = "dex-lp"
    projects = (
        "uniswap", "curve", "aerodrome", "velodrome", "balancer",
        "pancakeswap", "camelot", "sushiswap", "syncswap", "maverick",
    )
