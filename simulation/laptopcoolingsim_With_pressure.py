import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import time

# Ultimate Tactical Field Protocol Simulation (Production Edition)
# This version integrates a dynamic pressure model, CO₂ injections, and pressure-based auto-purge.
# When chamber pressure exceeds 1.5 bar, the exit valve "opens" to vent excess CO₂ and injections are held.

# -------------------------
# System Thermal Parameters
# -------------------------
cpu_power_watts            = 18.5    # undervolted CPU power (W)
passive_dissipation_watts  = 1.5     # passive cooling contribution (W)
thermal_mass_j_per_c       = 300     # thermal mass of internal components (J/°C)
initial_temp_c             = 25      # starting temperature (°C)
critical_temp_c            = 90      # absolute maximum temperature (°C)
emergency_temp_c           = 78      # threshold for emergency measures (°C)

# -------------------------
# CO2 Canister and Cooling Parameters
# -------------------------
cooling_capacity_joules    = 2900    # energy content per CO₂ canister (J)
purge_efficiency           = 0.85    # effective fraction used in a purge event
cooling_effective_joules   = cooling_capacity_joules * purge_efficiency
cooldown_per_purge_c       = cooling_effective_joules / thermal_mass_j_per_c  # °C drop per purge
conduction_watts           = 2.2     # cooling from cold canister conduction (W)
conduction_duration        = 180     # seconds of passive cooling following a purge

# -------------------------
# Peltier (TEC) Parameters
# -------------------------
peltier_max_cooling_watts  = 15      # maximum TEC cooling capacity (W)
peltier_power_draw         = 30      # electrical consumption (W)
peltier_max_runtime        = 120     # maximum continuous runtime (seconds)
battery_capacity_wh        = 60      # battery capacity (Wh)
peltier_efficiency_base    = 0.6     # base efficiency ratio

# -------------------------
# Fan and Convective Cooling Parameters
# -------------------------
fan_power_draw             = 0.25    # fan power draw (W)
fan_efficiency_multiplier_base = 1.3  # base multiplier boost from convection
fan_efficiency_multiplier_max  = 2.5  # maximum boost available
fan_ramp_time              = 1.0     # seconds to reach full speed

# -------------------------
# Pressure Model Parameters
# -------------------------
vessel_volume_m3           = 0.0025  # vessel volume (2.5 L in cubic meters)
R                          = 8.314   # Ideal gas constant, J/(mol*K)

# Valve thresholds
relief_pressure_pa         = 5e5     # relief valve set at 5 bar (500,000 Pa)
auto_purge_pressure_threshold_pa = 2.5e5  # auto-purge triggers at 2.5 bar (250,000 Pa)
pressure_cooling_threshold_pa = 2.0e5    # use pressurized CO2 for cooling at 2.0 bar
moisture_protection_pressure_pa = 1.1e5  # maintain above 1.1 bar for moisture protection

# Calculate baseline moles in the chamber (pre-purged with dry CO₂ at moisture protection level)
initial_moles = (moisture_protection_pressure_pa * vessel_volume_m3) / (R * (initial_temp_c + 273.15))
internal_co2_moles = initial_moles  # start here

# Injection details: each microburst injects a small quantity of CO₂
injection_rate_molps       = 0.005   # moles per second during a burst

# -------------------------
# Injection Hold Logic
# -------------------------
injection_hold_time = 30        # seconds to hold off new injections after a purge event
injection_hold_until = 0        # timestamp until which injections are disabled

# -------------------------
# Simulation Time Parameters
# -------------------------
total_time_s             = 3600    # total simulation time: 60 minutes
time_step_s              = 5       # simulation time step (seconds)
n_steps                  = total_time_s // time_step_s

# -------------------------
# Initialize Tracking Variables
# -------------------------
canisters      = [cooling_capacity_joules, cooling_capacity_joules]
current_canister = 0
purge_count    = 0
pressure_vent_count = 0  # Track pressure vents separately
canister_swaps = 0
last_purge_time = -9999
temperature_c   = initial_temp_c
events          = []
temperature_log = []
pressure_log    = []  # log for internal chamber pressure (Pa)

# Peltier device state
peltier_active  = False
peltier_runtime_s = 0
battery_remaining_wh = battery_capacity_wh
hot_side_temp_c = initial_temp_c
cold_side_temp_c = initial_temp_c

# Fan state
fan_active   = False
fan_duty_cycle = 0
fan_mode     = "PASSIVE"
post_purge_timer = 0

# Cooling contributions logging
cooling_contribution = {
    "passive": 0,
    "co2_hiss": 0,
    "co2_purge": 0,
    "canister_conduction": 0,
    "peltier": 0,
    "fan_boost": 0
}

# Moisture protection tracking
time_below_moisture_threshold = 0

# -------------------------
# Utility Functions
# -------------------------
def get_cpu_workload(time_s):
    """
    Simulate varying CPU load to mimic real usage patterns.
    Baseline load is 85% of rated power, with sinusoidal variations,
    and periodic intense workloads.
    """
    base_load = cpu_power_watts * 0.85
    variation = np.sin(time_s / 300 * np.pi) * 0.15 * cpu_power_watts
    if 900 < time_s < 1100 or 2400 < time_s < 2700:
        return cpu_power_watts * 1.1  # 110% of rated power during intensive periods
    return base_load + variation

def calculate_peltier_efficiency(cpu_temp, hot_side_temp):
    """
    Estimate TEC efficiency based on the temperature difference.
    Efficiency decreases quadratically with excessive differential.
    """
    temp_diff = hot_side_temp - cpu_temp
    if temp_diff <= 0:
        return peltier_efficiency_base
    efficiency = peltier_efficiency_base * (1 - (temp_diff / 70)**2)
    if hot_side_temp > 85:
        efficiency *= 0.5
    return max(0.1, min(peltier_efficiency_base, efficiency))

def calculate_fan_multiplier(duty_cycle, is_post_purge=False, purge_timer=0, chamber_pressure=1e5):
    """
    Compute the convection multiplier from the fan, modified by duty cycle,
    post-purge boost, and the chamber pressure (higher pressure improves convective heat transfer).
    """
    if duty_cycle <= 0:
        return 1.0
    base_mult = 1.0 + (fan_efficiency_multiplier_base - 1.0) * (duty_cycle / 100)
    speed_factor = 1.0 + (duty_cycle / 100) * 0.7
    purge_boost = 1.0
    if is_post_purge:
        decay_factor = max(0, min(1, purge_timer / conduction_duration))
        purge_boost = 1.0 + 0.5 * decay_factor
    pressure_factor = chamber_pressure / 1e5  # baseline at 1 bar
    pressure_factor = max(1.0, min(2.0, pressure_factor))  # clamp between 1 and 2
    return base_mult * speed_factor * purge_boost * pressure_factor

def manage_peltier(cpu_temp, battery_level, co2_available, time_since_purge):
    """
    Determine if the Peltier should be active based on CPU temperature, battery,
    cumulative runtime, and hot side conditions.
    """
    global peltier_active, peltier_runtime_s
    should_activate = (cpu_temp > 70 and battery_level > 5 and
                       peltier_runtime_s < peltier_max_runtime and
                       hot_side_temp_c < 90)
    should_deactivate = (cpu_temp < 65 or battery_level < 3 or
                         hot_side_temp_c > 95 or peltier_runtime_s >= peltier_max_runtime)
    # Allow activation for a brief post-purge boost (if within 60 seconds)
    post_purge_boost = time_since_purge > 0 and time_since_purge < 60
    if should_activate or post_purge_boost:
        peltier_active = True
    elif should_deactivate:
        peltier_active = False
        peltier_runtime_s = 0

def manage_fan(cpu_temp, is_post_purge, seconds_since_purge):
    """
    Manage fan speed and mode based on the CPU temperature and purge status.
    """
    global fan_active, fan_duty_cycle, fan_mode
    if cpu_temp < 50 and not is_post_purge:
        fan_mode = "PASSIVE"
        target_duty = 0
    elif cpu_temp < 65:
        fan_mode = "SLOW_HISS"
        # Pulse the fan every 15 seconds in this regime.
        if seconds_since_purge % 15 == 0:
            target_duty = 30
        else:
            target_duty = 0
    elif is_post_purge:
        fan_mode = "PURGE"
        target_duty = 80
    elif cpu_temp > 75:
        fan_mode = "EMERGENCY"
        target_duty = 100
    else:
        fan_mode = "NORMAL"
        target_duty = 50

    if target_duty > fan_duty_cycle:
        fan_duty_cycle = min(target_duty, fan_duty_cycle + 10)
    elif target_duty < fan_duty_cycle:
        fan_duty_cycle = max(target_duty, fan_duty_cycle - 5)
    fan_active = (fan_duty_cycle > 0)

# -------------------------
# Simulation Loop
# -------------------------
for t in range(n_steps):
    seconds = t * time_step_s
    current_cpu_power = get_cpu_workload(seconds)
    time_since_last_purge = seconds - last_purge_time
    is_post_purge = (0 <= time_since_last_purge <= conduction_duration)
    if is_post_purge:
        post_purge_timer = conduction_duration - time_since_last_purge
    else:
        post_purge_timer = 0

    # Passive cooling contributions
    passive_cooling = passive_dissipation_watts
    cooling_contribution["passive"] += passive_cooling * time_step_s
    conduction_cooling = conduction_watts if is_post_purge else 0
    cooling_contribution["canister_conduction"] += conduction_cooling * time_step_s

    # Determine CO₂ microburst parameters based on current temperature
    if temperature_c < 60:
        burst_duration = 0.3
        cycle_time = 8.0
    elif 60 <= temperature_c < 70:
        burst_duration = 0.5
        cycle_time = 5.0
    elif 70 <= temperature_c < 75:
        burst_duration = 0.7
        cycle_time = 4.0
    else:
        burst_duration = 1.0
        cycle_time = 3.0

    # Decide if a burst event occurs (based on time modulo cycle).
    burst_now = (seconds % int(cycle_time) == 0)

    # Calculate current chamber pressure BEFORE injection decision
    temperature_kelvin = temperature_c + 273.15
    pressure_pa = (internal_co2_moles * R * temperature_kelvin) / vessel_volume_m3

    # --- Injection Control Based on Pressure and Hold Time ---
    # Don't inject if pressure is too high OR we're in hold period
    if pressure_pa >= auto_purge_pressure_threshold_pa or seconds < injection_hold_until:
        n_injection = 0
        hiss_energy = 0
    else:
        # Normal operation: if a burst event occurs and CO₂ is available, compute injection.
        if burst_now and canisters[current_canister] > 0:
            n_injection = injection_rate_molps * burst_duration
            hiss_energy = burst_duration * 3.0
        else:
            n_injection = 0
            hiss_energy = 0

    cooling_contribution["co2_hiss"] += hiss_energy
    internal_co2_moles += n_injection

    # Manage the Peltier cooling device based on thermal conditions.
    manage_peltier(temperature_c, battery_remaining_wh, canisters[current_canister] > 50, time_since_last_purge)
    peltier_cooling = 0
    if peltier_active:
        peltier_efficiency = calculate_peltier_efficiency(temperature_c, hot_side_temp_c)
        peltier_cooling = peltier_max_cooling_watts * peltier_efficiency
        hot_side_temp_c += (peltier_power_draw * (1 - peltier_efficiency) * time_step_s) / thermal_mass_j_per_c
        hot_side_temp_c -= (passive_dissipation_watts * 0.5 * time_step_s) / thermal_mass_j_per_c
        battery_remaining_wh -= (peltier_power_draw * time_step_s) / 3600
        peltier_runtime_s += time_step_s
        cooling_contribution["peltier"] += peltier_cooling * time_step_s
    else:
        hot_side_temp_c = max(temperature_c, hot_side_temp_c - 0.5)
        peltier_runtime_s = max(0, peltier_runtime_s - time_step_s)

    manage_fan(temperature_c, is_post_purge, time_since_last_purge)

    # Recalculate pressure after injection
    temperature_kelvin = temperature_c + 273.15
    pressure_pa = (internal_co2_moles * R * temperature_kelvin) / vessel_volume_m3

    # Relief valve: vent if pressure exceeds 5 bar.
    n_max = (relief_pressure_pa * vessel_volume_m3) / (R * temperature_kelvin)
    if internal_co2_moles > n_max:
        internal_co2_moles = n_max
        pressure_pa = relief_pressure_pa
        events.append(f"[{seconds:>4}s] RELIEF VALVE: Pressure capped at 5.0 bar")

    # Recalculate pressure after any venting operations
    pressure_pa = (internal_co2_moles * R * temperature_kelvin) / vessel_volume_m3
    pressure_log.append(pressure_pa)

    # Track moisture protection
    if pressure_pa < moisture_protection_pressure_pa:
        time_below_moisture_threshold += time_step_s

    # Include chamber pressure in the fan multiplier calculation.
    fan_multiplier = calculate_fan_multiplier(fan_duty_cycle, is_post_purge, post_purge_timer, pressure_pa)

    if fan_active:
        battery_remaining_wh -= (fan_power_draw * (fan_duty_cycle / 100) * time_step_s) / 3600

    enhanced_passive   = passive_cooling * fan_multiplier
    enhanced_conduction = conduction_cooling * fan_multiplier
    enhanced_hiss      = (hiss_energy / time_step_s) * fan_multiplier  # averaged over time step
    enhanced_peltier   = peltier_cooling * fan_multiplier
    fan_boost = (enhanced_passive + enhanced_conduction + enhanced_hiss + enhanced_peltier) - \
                (passive_cooling + conduction_cooling + (hiss_energy / time_step_s) + peltier_cooling)
    cooling_contribution["fan_boost"] += fan_boost * time_step_s

    total_cooling = enhanced_passive + enhanced_conduction + enhanced_hiss + enhanced_peltier

    # ---------------
    # PRESSURE-BASED THERMAL PURGE LOGIC
    # ---------------
    # If pressure is high AND temperature needs cooling, do a cooling purge
    if pressure_pa > pressure_cooling_threshold_pa and temperature_c > 65:
        if canisters[current_canister] >= cooling_effective_joules:
            # This is a pressure-driven cooling purge - use the pressurized CO2 for cooling
            temp_before = temperature_c
            temp_drop = cooldown_per_purge_c * fan_multiplier * 1.2  # bonus for high pressure
            temperature_c -= temp_drop
            canisters[current_canister] -= cooling_effective_joules
            purge_count += 1
            last_purge_time = seconds
            cooling_contribution["co2_purge"] += cooling_effective_joules
            events.append(f"[{seconds:>4}s] PRESSURE COOLING PURGE: {pressure_pa/1e5:.2f} bar | "
                          f"Temp: {temp_before:.2f}°C → {temperature_c:.2f}°C | "
                          f"CO₂ Left: {canisters[current_canister]:.0f}J")
            # Vent to moisture protection level, not all the way to baseline
            target_moles = (moisture_protection_pressure_pa * vessel_volume_m3) / (R * temperature_kelvin)
            internal_co2_moles = target_moles
            injection_hold_until = seconds + injection_hold_time

    # ---------------
    # WASTE PRESSURE VENTING (only if temperature is cool)
    # ---------------
    elif pressure_pa > auto_purge_pressure_threshold_pa and temperature_c < 65:
        pressure_vent_count += 1
        events.append(f"[{seconds:>4}s] PRESSURE VENT: {pressure_pa/1e5:.2f} bar → {moisture_protection_pressure_pa/1e5:.2f} bar | "
                      f"Temp: {temperature_c:.2f}°C")
        # Vent to moisture protection level
        target_moles = (moisture_protection_pressure_pa * vessel_volume_m3) / (R * temperature_kelvin)
        internal_co2_moles = target_moles
        injection_hold_until = seconds + 10  # shorter hold for waste venting

    # ---------------
    # TEMPERATURE-BASED EMERGENCY PURGE
    # ---------------
    elif temperature_c > 85 or (temperature_c > emergency_temp_c and canisters[current_canister] < (cooling_capacity_joules * 0.10)):
        if canisters[current_canister] >= cooling_effective_joules:
            temp_drop = cooldown_per_purge_c * fan_multiplier
            temperature_c -= temp_drop
            canisters[current_canister] -= cooling_effective_joules
            purge_count += 1
            last_purge_time = seconds
            cooling_contribution["co2_purge"] += cooling_effective_joules
            events.append(f"[{seconds:>4}s] EMERGENCY TEMP PURGE: Temp → {temperature_c:.2f}°C | "
                          f"CO₂ Left: {canisters[current_canister]:.0f}J | Pressure: {pressure_pa/1e5:.2f} bar")
            # Maintain moisture protection
            target_moles = (moisture_protection_pressure_pa * vessel_volume_m3) / (R * temperature_kelvin)
            internal_co2_moles = target_moles
            injection_hold_until = seconds + injection_hold_time

    # Canister swap logic: if CO₂ energy is nearly exhausted, swap to the spare canister.
    if canisters[current_canister] < 50 and current_canister == 0:
        current_canister = 1
        canister_swaps += 1
        events.append(f"[{seconds:>4}s] CANISTER SWAP: Fresh CO₂ source loaded! | "
                      f"Temp: {temperature_c:.2f}°C | Battery: {battery_remaining_wh:.1f}Wh")

    # Apply the hiss energy cost to the current canister.
    canisters[current_canister] = max(0, canisters[current_canister] - hiss_energy)

    # Update net thermal energy:
    net_power = current_cpu_power - total_cooling
    delta_temp = (net_power * time_step_s) / thermal_mass_j_per_c
    temperature_c += delta_temp
    temperature_log.append(temperature_c)

    if seconds % 300 == 0 and seconds > 0:
        moisture_status = "PROTECTED" if pressure_pa >= moisture_protection_pressure_pa else "AT RISK"
        events.append(f"[{seconds:>4}s] STATUS: Temp: {temperature_c:.2f}°C | CO₂: {canisters[current_canister]:.0f}J | "
                      f"Battery: {battery_remaining_wh:.1f}Wh | Mode: {fan_mode} | Pressure: {pressure_pa/1e5:.2f} bar | "
                      f"Moisture: {moisture_status}")

# -------------------------
# Simulation Summary and Plots
# -------------------------
events.append(f"\n=== ULTIMATE THERMAL EDEN SIMULATION SUMMARY ===")
events.append(f"Mission duration: {total_time_s//60} minutes")
events.append(f"Final temperature: {temperature_c:.2f}°C")
events.append(f"Peak temperature: {max(temperature_log):.2f}°C")
events.append(f"Total CO₂ purges: {purge_count}")
events.append(f"Pressure vents: {pressure_vent_count}")
events.append(f"Canister swaps: {canister_swaps}")
events.append(f"Remaining CO₂: {sum(canisters):.0f}J")
events.append(f"Battery remaining: {battery_remaining_wh:.1f}Wh ({battery_remaining_wh/battery_capacity_wh*100:.1f}%)")
moisture_percentage = ((total_time_s - time_below_moisture_threshold) / total_time_s) * 100
events.append(f"Moisture protection maintained: {moisture_percentage:.1f}% of mission time")

events.append(f"\n=== COOLING CONTRIBUTION ANALYSIS ===")
total_cooling_energy = sum(cooling_contribution.values())
for mechanism, joules in cooling_contribution.items():
    percentage = (joules / total_cooling_energy) * 100 if total_cooling_energy > 0 else 0
    events.append(f"{mechanism}: {joules:.0f}J ({percentage:.1f}%)")

# Plot Temperature and Pressure profiles
fig, axs = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
time_minutes = np.arange(0, total_time_s, time_step_s) / 60

axs[0].plot(time_minutes, temperature_log, label="Temperature (°C)")
axs[0].axhline(y=critical_temp_c, color='r', linestyle='--', label=f'Critical ({critical_temp_c}°C)')
axs[0].axhline(y=emergency_temp_c, color='orange', linestyle='--', label=f'Emergency ({emergency_temp_c}°C)')
axs[0].axhline(y=75, color='y', linestyle='--', label='High (75°C)')
axs[0].axhline(y=65, color='g', linestyle='--', label='Optimal (65°C)')
axs[0].set_ylabel('Temperature (°C)')
axs[0].set_title('Ultimate Tactical Field Protocol: Thermal Profile')
axs[0].legend()
axs[0].grid(True)

axs[1].plot(time_minutes, [p/1e5 for p in pressure_log], label="Pressure (bar)", color='purple')
axs[1].axhline(y=relief_pressure_pa/1e5, color='red', linestyle='--', label='Relief Valve (5 bar)')
axs[1].axhline(y=auto_purge_pressure_threshold_pa/1e5, color='orange', linestyle='--', label='Auto-Purge (2.5 bar)')
axs[1].axhline(y=pressure_cooling_threshold_pa/1e5, color='yellow', linestyle='--', label='Cooling Threshold (2.0 bar)')
axs[1].axhline(y=moisture_protection_pressure_pa/1e5, color='green', linestyle='--', label='Moisture Protection (1.1 bar)')
axs[1].set_ylabel('Pressure (bar)')
axs[1].set_xlabel('Time (minutes)')
axs[1].set_title('Chamber Pressure Evolution')
axs[1].legend()
axs[1].grid(True)

plt.tight_layout()

if __name__ == "__main__":
    print("\n".join(events))
    plt.savefig('thermal_pressure_simulation.png')
    plt.show()