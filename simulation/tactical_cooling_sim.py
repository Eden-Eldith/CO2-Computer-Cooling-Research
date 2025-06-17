"""
Below is a self-contained Python script that demonstrates a production-ready approach to simulating a tactical CO₂-based cooling system across different planetary environments. The code includes realistic (but necessarily simplified) physics for thermal conduction, CO₂ bursts (including rudimentary canister pressure modeling), Peltier cooling, and fan-based cooling. Feel free to adjust numerical values (e.g., capacity, conduction coefficients) to reflect your actual hardware and mission design data.

---
"""
## Complete Python Code
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tactical CO2-Based Cooling System Simulation
============================================

Simulates a sealed, pressurized CO2-canister cooling system with conduction,
Peltier cooling, and fan assistance. Includes planetary environment modeling
(Mars, Earth, Moon) with sub-environments (e.g., crater base, desert, etc.).

Author: Eden_Eldith
Date: 2025-03-28

Dependencies:
    - numpy
    - matplotlib
    - dataclasses
    - time
    - random
Usage:
    python tactical_cooling_sim.py
"""

import numpy as np
import matplotlib.pyplot as plt
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Dict

###############################################################################
#                             PHYSICAL CONSTANTS                              #
###############################################################################
R_UNIVERSAL = 8.314462618  # J/(mol·K) - Universal gas constant
M_CO2 = 44.01e-3           # kg/mol for CO2
CP_CO2 = 844.0             # J/(kg·K) approximate heat capacity of CO2 (gas) near room temp

###############################################################################
#                         ENVIRONMENT & SUB-ENVIRONMENT                       #
###############################################################################
@dataclass
class SubEnvironment:
    """
    Represents a specific zone or condition on a planet, e.g., "Crater Base" on Mars.
    """
    name: str
    thermal_conductivity: float        # W/mK (inward/outward conduction w.r.t. system)
    ambient_temp_func: Callable[[float], float]  # function returning ambient temp (C) over time (seconds)
    pressure_pa: float                # atmospheric pressure in Pascals
    radiation_flux: float             # W/m^2 (unused in this example, but could feed into net heat gain/loss)

@dataclass
class PlanetaryEnvironment:
    """
    Represents the planetary-level properties, including gravity and atmosphere composition.
    """
    name: str
    gravity: float                    # m/s^2
    atmosphere: str                   # e.g. "95% CO2" for Mars
    sub_environments: Dict[str, SubEnvironment] = field(default_factory=dict)

def mars_diurnal_temp(t_s: float) -> float:
    """
    Simplified diurnal temperature variation on Mars (in °C).
    Let's assume a -60°C night minimum to +0°C midday max for a quick example.
    A full Martian day is ~24h 39min (~88,740s). We can just approximate ~24h.
    """
    period = 24 * 3600.0
    # Sine wave around average -30°C, amplitude 30°C
    avg_temp = -30.0
    amp = 30.0
    return avg_temp + amp * np.sin(2.0 * np.pi * t_s / period)

def earth_diurnal_temp(t_s: float) -> float:
    """
    Very rough Earth-like diurnal cycle (in °C).
    Let's say 15°C average, ±10°C swing.
    """
    period = 24 * 3600.0
    avg_temp = 15.0
    amp = 10.0
    return avg_temp + amp * np.sin(2.0 * np.pi * t_s / period)

def moon_diurnal_temp(t_s: float) -> float:
    """
    Extremely simplified lunar temperature cycle (in °C).
    A full lunar day is ~29.5 Earth days, but we’ll do a short partial cycle here.
    For demonstration, let's just vary from -150°C to +100°C over a 24-hour period.
    """
    period = 24 * 3600.0
    avg_temp = -25.0
    amp = 125.0
    return avg_temp + amp * np.sin(2.0 * np.pi * t_s / period)

# Example Planetary Definitions:
mars = PlanetaryEnvironment(
    name="Mars",
    gravity=3.711,
    atmosphere="95% CO2",
    sub_environments={
        "Crater Base": SubEnvironment(
            name="Crater Base",
            thermal_conductivity=0.005,      # W/mK, a very rough conduction factor to system
            ambient_temp_func=mars_diurnal_temp,
            pressure_pa=610.0,              # ~6 mbar
            radiation_flux=200.0            # W/m^2 (placeholder, not used in detail)
        )
    }
)

earth = PlanetaryEnvironment(
    name="Earth",
    gravity=9.81,
    atmosphere="N2/O2",
    sub_environments={
        "Urban": SubEnvironment(
            name="Urban",
            thermal_conductivity=0.03,
            ambient_temp_func=earth_diurnal_temp,
            pressure_pa=101325.0,
            radiation_flux=300.0
        ),
        "Desert": SubEnvironment(
            name="Desert",
            thermal_conductivity=0.025,
            ambient_temp_func=earth_diurnal_temp,
            pressure_pa=101325.0,
            radiation_flux=1000.0
        )
    }
)

moon = PlanetaryEnvironment(
    name="Moon",
    gravity=1.62,
    atmosphere="Near Vacuum",
    sub_environments={
        "Surface": SubEnvironment(
            name="Surface",
            thermal_conductivity=0.0001,
            ambient_temp_func=moon_diurnal_temp,
            pressure_pa=1e-9,
            radiation_flux=1361.0  # roughly solar constant if in direct sunlight
        )
    }
)

PLANETS = {
    "Mars": mars,
    "Earth": earth,
    "Moon": moon
}

###############################################################################
#                           COOLING SYSTEM MODEL                               #
###############################################################################
class CoolingSystem:
    """
    Represents the entire cooling system, including:
      - CO2 canisters (pressure, temperature, capacity)
      - Peltier/TEC and fan logic
      - System thermal mass
      - State machine logic for idle, active, emergency cooling
      - Battery usage
    """

    def __init__(
        self,
        initial_temp: float = 35.0,
        system_heat_capacity_jpk: float = 2000.0,
        co2_canister_joules: float = 3.0e5,
        co2_canister_pressure_pa: float = 5.0e6,  # ~50 bar as an example
        co2_canister_volume_m3: float = 0.01,     # 10 liters
        n_canisters: int = 2,
        battery_capacity_wh: float = 200.0,
        conduction_canister_k: float = 0.02  # conduction factor from canister to system
    ):
        # System (the "robot" or device we are cooling)
        self.temperature_c = initial_temp
        self.system_heat_capacity = system_heat_capacity_jpk  # J/K for system
        # Peltier + Fans
        self.tec_on = False
        self.fan_on = False
        # Battery in Wh
        self.battery_wh = battery_capacity_wh

        # CO2 canisters: store each canister's "energy capacity" and track pressure, T
        # For simplicity, each canister starts at the same pressure and temperature (ambient).
        self.canisters = []
        for _ in range(n_canisters):
            self.canisters.append({
                "energy_j": co2_canister_joules,  # total cooling potential in Joules
                "pressure_pa": co2_canister_pressure_pa,
                "volume_m3": co2_canister_volume_m3,
                "temperature_k": 293.0,          # ~20°C in Kelvin for start
            })

        self.current_canister_idx = 0  # which canister is currently in use
        self.conduction_canister_k = conduction_canister_k

        # Logging
        self.time_log = []
        self.temp_log = []
        self.battery_log = []
        self.co2_pressure_log = []
        self.state_log = []

        # State machine
        self.state = "IDLE"

        # Internal cooldown timers / counters
        self.last_burst_time = -999.0
        self.burst_interval = 5.0  # require 5s between bursts to avoid rapid depletion

    def get_current_canister(self):
        return self.canisters[self.current_canister_idx]

    def swap_canister(self):
        """Attempt to swap to a new canister if the current one is depleted."""
        for i, can in enumerate(self.canisters):
            if can["energy_j"] > 0.0:  # has capacity left
                self.current_canister_idx = i
                return
        # If all canisters are empty, do nothing (we're out of CO2).
        pass

    def canister_cooling_burst(self, dt: float):
        """
        Release a micro-burst of CO2 to cool the system.
        We'll remove some Joules from the system, limited by canister energy.
        Also consider a Joule-Thomson effect or pressure drop in the canister.
        """
        now = self.time_log[-1] if self.time_log else 0.0

        if (now - self.last_burst_time) < self.burst_interval:
            # Too soon for another burst
            return

        canister = self.get_current_canister()
        if canister["energy_j"] <= 0.0:
            # Current canister is empty; attempt swap
            self.swap_canister()
            canister = self.get_current_canister()
            if canister["energy_j"] <= 0.0:
                # No canisters left
                return

        # Define how much a single burst cools:
        # E.g., 2kJ per burst (2000 Joules). Tweak as needed.
        burst_joules = 2000.0
        used_joules = min(burst_joules, canister["energy_j"])

        # Remove from system (cooling):
        self._remove_heat(used_joules)
        # Deplete from canister
        canister["energy_j"] -= used_joules

        # Pressure drop (very simplified: ideal gas with linear mass drop assumption):
        # P*V = nRT => if we reduce "n" by ratio of used_joules to total, do the same for pressure
        fraction_used = used_joules / burst_joules
        # scale pressure by fraction of mass used. This is naive, but illustrative.
        canister["pressure_pa"] *= (1.0 - 0.01 * fraction_used)

        # Joule-Thomson cooling effect on canister (rough):
        # For CO2 near 1 bar and ~300K, the JT coefficient is ~1.3 K/bar. We are at higher pressure, so let's keep it simple:
        jt_coeff = 1.0  # K/bar for demonstration
        delta_p_bar = (burst_joules / 500.0)  # naive correlation for demonstration
        canister["temperature_k"] -= jt_coeff * delta_p_bar

        self.last_burst_time = now

    def peltier_cooling(self, dt: float, tec_power_w: float = 50.0):
        """
        If TEC is ON, remove a certain rate of heat from the system.
        This also consumes battery. For example, a 50 W Peltier draws 50 W from the battery
        and might remove ~40 W of heat from the system in a real device.
        We'll do a direct 1:1 for simplicity (50 J/s) in this example.
        """
        if self.tec_on:
            # Subtract from system
            heat_removed_j = tec_power_w * dt
            self._remove_heat(heat_removed_j)
            # Battery consumption: 50 W for dt seconds => 50*(dt/3600) Wh
            self.battery_wh -= (tec_power_w * dt / 3600.0)

    def fan_cooling(self, dt: float, fan_power_w: float = 5.0):
        """
        If fan is ON, it provides forced convection to the environment.
        We'll treat that as an additional conduction factor or a direct
        heat removal rate. Simplify to 5 W of cooling, matched by 5 W battery usage.
        """
        if self.fan_on:
            heat_removed_j = fan_power_w * dt
            self._remove_heat(heat_removed_j)
            # Battery usage
            self.battery_wh -= (fan_power_w * dt / 3600.0)

    def conduction_with_canister(self, dt: float):
        """
        Heat flows between system and canister via conduction.
        Q = conduction_canister_k * (T_canister - T_system)
        We'll do a simple lumped approach in J/s => multiply by dt for total J.
        """
        canister = self.get_current_canister()
        T_canister_c = canister["temperature_k"] - 273.15
        dT = (T_canister_c - self.temperature_c)
        q_dot = self.conduction_canister_k * dT  # J/s
        q = q_dot * dt

        # If q > 0, heat flows from canister to system (warming the system, cooling canister).
        # If q < 0, heat flows from system to canister.
        self.temperature_c += q / self.system_heat_capacity

        # Update canister temperature. For the canister, we approximate heat capacity
        # by the CO2 mass: n = (P*V)/(R*T), mass = n * M_CO2, so Cp_total = mass * CP_CO2.
        # We'll quickly recalc each step:
        canister_n_mol = (canister["pressure_pa"] * canister["volume_m3"]) / (R_UNIVERSAL * canister["temperature_k"])
        canister_mass_kg = canister_n_mol * M_CO2
        canister_cp_jpk = canister_mass_kg * CP_CO2
        # The canister changes temperature by -q/canister_cp_jpk
        # But watch signs carefully: if q is positive, it means the system gained heat from the canister,
        # so the canister lost heat and gets cooler.
        canister["temperature_k"] -= q / canister_cp_jpk

    def conduction_with_environment(self, dt: float, env_temp_c: float, env_k: float):
        """
        Simple conduction between system and environment.
        Q_dot = env_k * (T_env - T_sys)
        """
        dT = (env_temp_c - self.temperature_c)
        q_dot = env_k * dT  # W
        q = q_dot * dt
        # Apply to system
        self.temperature_c += q / self.system_heat_capacity

    def _remove_heat(self, joules: float):
        """Utility to remove a given amount of heat from the system (J)."""
        # Temperature change = Q / (m * Cp) => system_heat_capacity is total J/K
        delta_t = joules / self.system_heat_capacity
        self.temperature_c = max(self.temperature_c - delta_t, -100.0)  # clamp to -100C artificially

    def update_state_machine(self):
        """
        Basic example state machine:
         - IDLE: if T > 30, go ACTIVE
         - ACTIVE: if T > 40, go EMERGENCY; if T < 25, go IDLE
         - EMERGENCY: if T < 35, go ACTIVE
        This logic is a placeholder; adjust thresholds as needed.
        """
        if self.state == "IDLE":
            self.tec_on = False
            self.fan_on = False
            # Transition condition
            if self.temperature_c > 30.0:
                self.state = "ACTIVE"

        elif self.state == "ACTIVE":
            self.tec_on = True
            self.fan_on = True
            if self.temperature_c > 40.0:
                self.state = "EMERGENCY"
            elif self.temperature_c < 25.0:
                self.state = "IDLE"

        elif self.state == "EMERGENCY":
            # In emergency, do everything plus CO2 bursts
            self.tec_on = True
            self.fan_on = True
            # We'll trigger frequent CO2 bursts directly in the main loop
            if self.temperature_c < 35.0:
                self.state = "ACTIVE"

    def step(self, t_s: float, dt: float, env: SubEnvironment):
        """
        Single step of the simulation. 
        """
        # 1) Update the state machine
        self.update_state_machine()

        # 2) Conduction with environment
        env_temp_c = env.ambient_temp_func(t_s)
        self.conduction_with_environment(dt, env_temp_c, env.thermal_conductivity)

        # 3) Conduction with canister
        self.conduction_with_canister(dt)

        # 4) Peltier & Fan cooling (if on)
        self.peltier_cooling(dt, tec_power_w=50.0)
        self.fan_cooling(dt, fan_power_w=5.0)

        # 5) If state is EMERGENCY or ACTIVE, occasionally do CO2 bursts
        if self.state == "EMERGENCY":
            # In EMERGENCY, do more frequent bursts
            self.canister_cooling_burst(dt)
        elif self.state == "ACTIVE" and (self.temperature_c > 32.0):
            # Optional occasional bursts in ACTIVE
            self.canister_cooling_burst(dt)

        # 6) Log data
        self.time_log.append(t_s)
        self.temp_log.append(self.temperature_c)
        self.battery_log.append(self.battery_wh)
        self.co2_pressure_log.append(self.get_current_canister()["pressure_pa"])
        self.state_log.append(self.state)

###############################################################################
#                                MAIN SIMULATION                               #
###############################################################################
def main():
    # ---------------------------
    # 1. Simulation Configuration
    # ---------------------------
    PLANET_NAME = "Mars"  # Choose from "Mars", "Earth", "Moon"
    SUB_ENV_NAME = "Crater Base"  # example sub-environment (must exist in the chosen planet)
    SIM_DURATION = 86400 * 7  # 7 days in seconds
    TIME_STEP = 1.0           # 1-second resolution

    # Retrieve planet & sub-environment
    planet = PLANETS[PLANET_NAME]
    sub_env = planet.sub_environments[SUB_ENV_NAME]

    # ---------------------------
    # 2. Initialize Cooling System
    # ---------------------------
    system = CoolingSystem(
        initial_temp=35.0,
        system_heat_capacity_jpk=2000.0,    # Adjust as needed
        co2_canister_joules=3.0e5,         # 300 kJ total cooling potential
        co2_canister_pressure_pa=5.0e6,    # 50 bar
        co2_canister_volume_m3=0.01,       # 10 liters
        n_canisters=2,                    # number of canisters
        battery_capacity_wh=200.0,        # total battery capacity in Wh
        conduction_canister_k=0.02        # conduction factor
    )

    # ---------------------------
    # 3. Run the Simulation
    # ---------------------------
    print(f"Starting simulation for {planet.name} - {sub_env.name} ...")
    start_real_time = time.time()

    current_time_s = 0.0
    while current_time_s <= SIM_DURATION:
        system.step(current_time_s, TIME_STEP, sub_env)
        current_time_s += TIME_STEP

    end_real_time = time.time()
    print(f"Simulation finished in {end_real_time - start_real_time:.2f} real seconds.")

    # ---------------------------
    # 4. Results & Plotting
    # ---------------------------
    final_temp = system.temp_log[-1]
    peak_temp = max(system.temp_log)
    total_co2_used = 0.0
    for c in system.canisters:
        # original capacity was 3e5 J each => total_co2_used is (original - leftover)
        total_co2_used += (3.0e5 - c["energy_j"])

    battery_used = system.battery_log[0] - system.battery_log[-1]

    print(f"--- Simulation Results ({PLANET_NAME}, {SUB_ENV_NAME}) ---")
    print(f"Final Internal Temp: {final_temp:.2f} °C")
    print(f"Peak Internal Temp: {peak_temp:.2f} °C")
    print(f"Total CO2 Used: {total_co2_used:.2f} J")
    print(f"Battery Used: {battery_used:.2f} Wh")

    # Plot temperature vs. time
    time_array = np.array(system.time_log) / 3600.0  # convert seconds to hours
    plt.figure(figsize=(10, 5))
    plt.plot(time_array, system.temp_log, label="System Temp (°C)")
    plt.xlabel("Time (hours)")
    plt.ylabel("Temperature (°C)")
    plt.title(f"Thermal Response - {PLANET_NAME} / {sub_env.name}")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(f"{PLANET_NAME}_{sub_env.name}_thermal.png")
    plt.close()

    # Plot CO2 pressure vs. time (of the current canister)
    plt.figure(figsize=(10, 5))
    plt.plot(time_array, system.co2_pressure_log, label="Canister Pressure (Pa)")
    plt.xlabel("Time (hours)")
    plt.ylabel("Pressure (Pa)")
    plt.title(f"Canister Pressure - {PLANET_NAME} / {sub_env.name}")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(f"{PLANET_NAME}_{sub_env.name}_pressure.png")
    plt.close()

    # Plot battery usage vs. time
    plt.figure(figsize=(10, 5))
    plt.plot(time_array, system.battery_log, label="Battery (Wh)")
    plt.xlabel("Time (hours)")
    plt.ylabel("Battery (Wh)")
    plt.title(f"Battery Usage - {PLANET_NAME} / {sub_env.name}")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(f"{PLANET_NAME}_{sub_env.name}_battery.png")
    plt.close()

    print("Plots saved. Simulation complete.")

# Run if this file is executed directly
if __name__ == "__main__":
    main()

"""
---

## How This Simulation Works

1. **Planetary Environments**  
   - Each planet is described by basic gravity, atmosphere, and a dictionary of sub-environments.  
   - Each `SubEnvironment` includes a conduction coefficient, an atmospheric pressure, and a function modeling ambient temperature over time.

2. **Cooling System**  
   - **Thermal Lumped Model**: The “system” (e.g., your robot or electronics enclosure) is treated as one thermal mass with a total heat capacity in J/K (`system_heat_capacity_jpk`).  
   - **CO₂ Canisters**:
     - Each canister has a starting pressure, temperature, volume, and total “cooling energy” budget.  
     - A CO₂ burst removes heat from the system (in Joules) and depletes canister energy (and pressure).  
     - A simplified Joule-Thomson effect is applied to adjust the canister’s own temperature after a burst.  
   - **Conduction**:
     - The system conducts heat both with the environment and with whichever canister is active.  
   - **Peltier + Fans**:
     - If turned on (depending on the system’s state), they remove a fixed rate of heat from the system (in W).  
     - This also consumes battery energy.  

3. **State Machine**  
   - Simple logic switches between **IDLE**, **ACTIVE**, and **EMERGENCY** states based on thresholds.  
   - In **EMERGENCY**, CO₂ bursts are used more aggressively.  
   - You can customize these transitions and thresholds to reflect your actual control logic.

4. **Simulation Loop**  
   - Runs for `SIM_DURATION` seconds, at a `TIME_STEP` of 1 second.  
   - Each iteration updates the system’s thermal and battery states, logs the results, and checks for state transitions.

5. **Outputs**  
   - **Peak temperature**, **Final temperature**, total CO₂ used, battery consumed.  
   - Saves plots of system temperature, canister pressure, and battery usage over time.

---

## Adapting for Your Needs

- **Extend the Heat Flow Model**: If you have geometry, conduction areas, or advanced convection/radiation terms, you can replace or expand the conduction steps accordingly.
- **Planetary Details**: The code uses simplified sine-wave diurnal cycles. You can substitute real temperature data or more sophisticated climate models.
- **Power & Pressure**: Adjust canister pressure/volume, battery capacity, Peltier efficiency, etc. for real-world values.
- **Multiple Sub-Environments**: You can easily add new sub-environments (e.g., “Underground Cavern,” “Arctic Plain”) by providing new conduction coefficients and temperature functions.
"""
