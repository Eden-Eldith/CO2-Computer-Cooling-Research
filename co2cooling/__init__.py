"""CO2 cooling simulation package."""

from .laptop_sim import run_simulation, calculate_peltier_efficiency, calculate_fan_multiplier

__all__ = [
    "run_simulation",
    "calculate_peltier_efficiency",
    "calculate_fan_multiplier",
]
