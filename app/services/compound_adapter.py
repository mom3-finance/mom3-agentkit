"""Compound yield adapter; free DefiLlama discovery plus optional RPC-ready boundary."""
from app.services.yield_adapter import DefiLlamaAdapter


class CompoundAdapter(DefiLlamaAdapter):
    adapter_name = "compound"
    projects = ("compound",)
