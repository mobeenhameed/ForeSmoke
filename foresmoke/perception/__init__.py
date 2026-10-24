"""Perception adapters. Optional computer-vision imports are lazy."""
from .config import PerceptionConfig
from .pipeline import iter_observations
__all__=["PerceptionConfig","iter_observations"]
