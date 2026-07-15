"""Pendle yield adapter using the public DefiLlama pools feed."""
from app.services.yield_adapter import DefiLlamaAdapter


class PendleAdapter(DefiLlamaAdapter):
    adapter_name = "pendle"
    projects = ("pendle",)
