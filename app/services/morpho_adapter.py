"""Morpho yield adapter using DefiLlama's free pool catalog."""
from app.services.yield_adapter import DefiLlamaAdapter


class MorphoAdapter(DefiLlamaAdapter):
    adapter_name = "morpho"
    projects = ("morpho",)
