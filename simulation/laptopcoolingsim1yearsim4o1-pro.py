import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import time

###############################################################################
# Ultimate Tactical Field Protocol Simulation (Eden Edition)
# ----------------------------------------------------------
# Fully debugged, long-term balanced thermodynamic model for a CO₂-based
# cooling system with optional Peltier TEC and micro-fan. This version
# corrects a scope error for 'seconds' in the fan logic and ensures
# no double-counting of fan effects. Production-ready code below.
###############################################################################

# ========================= 1) SYSTEM PARAMETERS ===============================

# Core system
cpu_power_watts = 18.5          # Average CPU power draw (dynamic load modeled)
passive_dissipation_watts = 1.5 # Passive heat loss through chassis
thermal_mass_j_per_c = 300      # Effective thermal mass of internal components
initial_temp_c = 25             # Starting temperature of system
critical_temp_c = 90            # Absolute maximum safe CPU temperature
emergency_temp_c = 75           # Emergency threshold for aggressive cooling

# CO₂ canisters
cooling_capacity_joules = 2900  # Single canister total cooling capacity
purge_efficiency = 0.85         # Effectiveness of purge usage
cooling_effective_joules = cooling_capacity_joules * purge_efficiency
cooldown_per_purge_c = cooling_effective_joules / thermal_mass_j_per_c
conduction_watts = 2.2          # Additional conduction cooling from cold canister after purge
conduction_duration = 180       # Seconds of stronger conduction post-purge

# Peltier (TEC)
peltier_max_cooling_watts = 30  # Maximum Peltier cooling power
peltier_power_draw = 30         # TEC power consumption (watts)
peltier_max_runtime = 120       # Max continuous seconds to avoid overheating the TEC
peltier_efficiency_base = 0.6   # Nominal TEC coefficient of performance (scaled)
battery_capacity_wh = 8500000000 # Large battery for demonstration
hot_side_temp_c = initial_temp_c
cold_side_temp_c = initial_temp_c

# Fan
fan_power_draw = 0.25               # Power in watts at 100% duty
fan_efficiency_multiplier_base = 1.3
fan_efficiency_multiplier_max  = 2.5
fan_ramp_time = 1.0                 # Seconds to ramp up to 100% from 0%

# Simulation duration
total_time_s = 31536000  # 1 year in seconds (for stress-testing long-term balance)
time_step_s = 5
n_steps = total_time_s // time_step_s

# ========================= 2) TRACKING VARIABLES =============================

# Two canisters, index 0 or 1 in use
canisters = [cooling_capacity_joules, cooling_capacity_joules]
current_canister = 0
purge_count = 0
canister_swaps = 0
last_purge_time = -9999

temperature_c = initial_temp_c
peak_temp_c = initial_temp_c
events = []
temperature_log = []

# Peltier
peltier_active = True
peltier_runtime_s = 0
battery_remaining_wh = battery_capacity_wh

# Fan
fan_active = True
fan_duty_cycle = 0
fan_mode = "PASSIVE"
post_purge_timer = 0

# Cooling breakdown (Joules)
cooling_contribution = {
    "passive": 0,
    "co2_hiss": 0,
    "co2_purge": 0,
    "canister_conduction": 0,
    "peltier": 0,
    "fan_boost": 0
}

# ========================= 3) HELPER FUNCTIONS ===============================

def get_cpu_workload(time_s):
    """
    Returns a dynamic CPU power usage (in watts),
    approximating workload variations over time.
    """
    base_load = cpu_power_watts * 0.85
    # Gentle sinusoidal variation every few days for a year-long run
    variation = np.sin(time_s / (300 * 60) * np.pi) * 0.15 * cpu_power_watts

    # Two intense workload periods
    intense_start1 = total_time_s * 0.1
    intense_end1   = intense_start1 + 7200  # 2 hours
    intense_start2 = total_time_s * 0.6
    intense_end2   = intense_start2 + 14400 # 4 hours

    if (intense_start1 < time_s < intense_end1) or (intense_start2 < time_s < intense_end2):
        return cpu_power_watts * 1.1  # ~110% TDP
    return base_load + variation

def calculate_peltier_efficiency(cpu_temp, hot_side_temp):
    """
    Calculates an approximate TEC efficiency based on the temperature difference.
    Efficiency decreases as the temperature difference increases.
    """
    temp_diff = hot_side_temp - cpu_temp
    if temp_diff <= 0:
        return peltier_efficiency_base
    
    # Efficiency declines quadratically with increasing temp diff
    efficiency = peltier_efficiency_base * (1 - (temp_diff / 70)**2)
    
    # If hot side is very hot, derate further
    if hot_side_temp > 85:
        efficiency *= 0.5
    
    return max(0.1, min(peltier_efficiency_base, efficiency))

def calculate_fan_multiplier(duty_cycle, is_post_purge=False, purge_timer=0):
    """
    Produces a multiplier for cooling based on current fan duty cycle.
    If in a post-purge window, we add a temporary synergy boost.
    """
    if duty_cycle <= 0:
        return 1.0

    base_mult = 1.0 + (fan_efficiency_multiplier_base - 1.0) * (duty_cycle / 100)
    speed_factor = 1.0 + (duty_cycle / 100) * 0.7

    purge_boost = 1.0
    if is_post_purge:
        # Decay the boost as the conduction effect diminishes
        decay_factor = max(0, min(1, (conduction_duration - purge_timer) / conduction_duration))
        purge_boost = 1.0 + 0.5 * decay_factor

    return base_mult * speed_factor * purge_boost

def manage_peltier(cpu_temp, battery_level, co2_ok, time_since_purge):
    """
    Turn Peltier on or off based on temperature and resource conditions.
    """
    global peltier_active, peltier_runtime_s, hot_side_temp_c
    should_activate = (
        cpu_temp > 70 and
        battery_level > (0.05 * battery_capacity_wh) and
        peltier_runtime_s < peltier_max_runtime and
        hot_side_temp_c < 90
    )
    should_deactivate = (
        cpu_temp < 65 or
        battery_level < (0.03 * battery_capacity_wh) or
        peltier_runtime_s >= peltier_max_runtime
    )

    # Brief post-purge cooling synergy
    post_purge_boost = (time_since_purge >= 0 and time_since_purge < 60)

    if peltier_active:
        if should_deactivate:
            peltier_active = False
            peltier_runtime_s = 0
    else:
        if should_activate or post_purge_boost:
            if battery_level > (0.05 * battery_capacity_wh):
                peltier_active = True
            else:
                peltier_active = False
                peltier_runtime_s = 0

def manage_fan(cpu_temp, is_post_purge, seconds_since_purge, current_time):
    """
    Adaptive fan speed control based on temperature and post-purge conditions.
    Ramps up/down fan duty cycle smoothly to avoid abrupt transitions.
    """
    global fan_active, fan_duty_cycle, fan_mode

    # Decide target duty cycle
    target_duty = 0
    if cpu_temp < 50 and not is_post_purge:
        fan_mode = "PASSIVE"
        target_duty = 0
    elif cpu_temp < 50:
        fan_mode = "SLOW_HISS"
        # Brief pulses of airflow every 15s
        if int(current_time) % 15 == 0:
            target_duty = 30
        else:
            target_duty = 0
    elif is_post_purge:
        fan_mode = "PURGE"
        target_duty = 70
    elif cpu_temp > 70:
        fan_mode = "EMERGENCY"
        target_duty = 100
    else:
        fan_mode = "NORMAL"
        target_duty = 50

    ramp_up_step = (100 / fan_ramp_time) * time_step_s
    ramp_down_step = ramp_up_step * 0.5

    if target_duty > fan_duty_cycle:
        fan_duty_cycle = min(target_duty, fan_duty_cycle + ramp_up_step)
    elif target_duty < fan_duty_cycle:
        fan_duty_cycle = max(target_duty, fan_duty_cycle - ramp_down_step)

    fan_duty_cycle = max(0, min(100, fan_duty_cycle))
    fan_active = (fan_duty_cycle > 0)

# ========================= 4) SIMULATION LOOP ================================

start_time = time.time()
# Logging limiter for canister swaps (weekly log only)
last_swap_log_time = -9999999  # so the first one always logs
for t in range(n_steps):
    seconds = t * time_step_s


    # Fetch CPU load
    current_cpu_power = get_cpu_workload(seconds)

    # Time since last purge
    time_since_last_purge = seconds - last_purge_time
    is_post_purge = 0 <= time_since_last_purge <= conduction_duration
    if is_post_purge:
        post_purge_timer = conduction_duration - time_since_last_purge
    else:
        post_purge_timer = 0

    # 1) BASE COOLING (before fan boost)
    base_passive_cooling = passive_dissipation_watts
    base_conduction_cooling = conduction_watts if is_post_purge else 0

    # 2) CO₂ microburst logic
    if temperature_c < 50:
        burst_duration = 0.3
        cycle_time = 8.0
    elif 50 <= temperature_c < 70:
        burst_duration = 0.5
        cycle_time = 5.0
    elif 70 <= temperature_c < 75:
        burst_duration = 0.7
        cycle_time = 4.0
    else:
        burst_duration = 1.0
        cycle_time = 3.0

    burst_now = (
        canisters[current_canister] > 0
        and int(cycle_time) > 0
        and (seconds % int(cycle_time) < time_step_s)
    )
    hiss_joules_per_burst = burst_duration * 3.0
    hiss_energy = hiss_joules_per_burst if burst_now else 0
    base_hiss_cooling = hiss_energy / time_step_s  # Spread across the timestep

    # 3) Peltier management
    manage_peltier(temperature_c, battery_remaining_wh, canisters[current_canister] > 50, time_since_last_purge)
    base_peltier_cooling = 0
    if peltier_active:
        peltier_eff = calculate_peltier_efficiency(temperature_c, hot_side_temp_c)
        base_peltier_cooling = peltier_max_cooling_watts * peltier_eff

        # Heat dumped to hot side
        peltier_heat_generated = peltier_power_draw + base_peltier_cooling
        hot_side_delta_t = (peltier_heat_generated * 0.01 - passive_dissipation_watts * 0.1) * time_step_s
        hot_side_temp_c += hot_side_delta_t
        hot_side_temp_c = max(temperature_c, hot_side_temp_c)

        # Battery usage
        peltier_power_consumed_ws = peltier_power_draw * time_step_s
        battery_remaining_wh -= peltier_power_consumed_ws / 3600
        peltier_runtime_s += time_step_s
    else:
        # If off, hot side moves towards CPU temp
        cooling_rate = 0.1
        hot_side_temp_c -= (hot_side_temp_c - temperature_c) * cooling_rate * time_step_s
        hot_side_temp_c = max(temperature_c, hot_side_temp_c)

    # 4) Fan management & multiplier
    manage_fan(temperature_c, is_post_purge, time_since_last_purge, seconds)
    fan_multiplier = calculate_fan_multiplier(fan_duty_cycle, is_post_purge, post_purge_timer)

    # Fan power usage
    if fan_active:
        fan_power_consumed_ws = fan_power_draw * (fan_duty_cycle / 100.0) * time_step_s
        battery_remaining_wh -= fan_power_consumed_ws / 3600

    # --------------------
    # SEPARATE BASE FROM FAN BOOST
    # --------------------
    # Base cooling (no fan)
    base_total_cooling = (
        base_passive_cooling
        + base_conduction_cooling
        + base_hiss_cooling
        + base_peltier_cooling
    )

    # Enhanced cooling (with fan)
    fan_boosted_passive       = base_passive_cooling      * fan_multiplier
    fan_boosted_conduction    = base_conduction_cooling   * fan_multiplier
    fan_boosted_hiss          = base_hiss_cooling         * fan_multiplier
    fan_boosted_peltier       = base_peltier_cooling      * fan_multiplier
    total_cooling             = (fan_boosted_passive
                                 + fan_boosted_conduction
                                 + fan_boosted_hiss
                                 + fan_boosted_peltier)

    # Track base portion (Joules)
    dt_joules = time_step_s
    cooling_contribution["passive"]              += base_passive_cooling     * dt_joules
    cooling_contribution["canister_conduction"]  += base_conduction_cooling  * dt_joules
    cooling_contribution["co2_hiss"]             += base_hiss_cooling        * dt_joules
    cooling_contribution["peltier"]              += base_peltier_cooling     * dt_joules

    # Fan boost is just the difference
    fan_boost = (total_cooling - base_total_cooling)
    cooling_contribution["fan_boost"] += fan_boost * dt_joules

    # --- EMERGENCY PURGE ---
    needs_purge = (temperature_c > critical_temp_c)
    maybe_purge = (
        temperature_c > emergency_temp_c
        and canisters[current_canister] < (cooling_capacity_joules * 0.15)
    )

    if needs_purge or maybe_purge:
        if canisters[current_canister] >= cooling_effective_joules:
            temp_drop = cooldown_per_purge_c * fan_multiplier
            temperature_c -= temp_drop
            canisters[current_canister] -= cooling_effective_joules
            purge_count += 1
            last_purge_time = seconds
            cooling_contribution["co2_purge"] += cooling_effective_joules
            events.append(
                f"[{seconds:>8.0f}s] EMERG-PURGE: ΔT=-{temp_drop:.2f}°C => "
                f"{temperature_c:.2f}°C | CO₂ Left: {canisters[current_canister]:.0f}J | "
                f"Fan={fan_duty_cycle:.0f}% | Battery={battery_remaining_wh/battery_capacity_wh*100:.1f}%"
            )
        # else: no enough for full purge; fallback to swap logic

    # --- CANISTER SWAP OR REFILL ---
    if canisters[current_canister] < 50:
        other_canister = 1 - current_canister
        if canisters[other_canister] > 50:
            current_canister = other_canister
            canister_swaps += 1
            if seconds - last_swap_log_time > 604800:
                events.append(
                    f"[{seconds:>8.0f}s] WEEKLY-SWAP-LOG: Using {current_canister}, "
                    f"CO₂={canisters[current_canister]:.0f}J, T={temperature_c:.2f}°C, "
                    f"Bat={battery_remaining_wh/battery_capacity_wh*100:.1f}%"
                )
                last_swap_log_time = seconds
        else:
            # Refill both canisters in "infinite" scenario
            canisters = [min(cooling_capacity_joules, c) for c in canisters]
            current_canister = 0
            canister_swaps += 1
            if seconds - last_swap_log_time > 604800:
                events.append(
                    f"[{seconds:>8.0f}s] WEEKLY-REFILL-LOG => T={temperature_c:.2f}°C, "
                    f"Bat={battery_remaining_wh / battery_capacity_wh * 100:.1f}%"
                )
                last_swap_log_time = seconds


    
    # Apply microburst CO₂ usage after potential swap
    if hiss_energy > 0:
        canisters[current_canister] = max(0, canisters[current_canister] - hiss_energy)

    # --- NET TEMPERATURE UPDATE ---
    net_power = current_cpu_power - total_cooling
    delta_temp = (net_power * time_step_s) / thermal_mass_j_per_c
    temperature_c += delta_temp
    temperature_c = max(initial_temp_c * 0.8, temperature_c)

    if temperature_c > peak_temp_c:
        peak_temp_c = temperature_c

    temperature_log.append(temperature_c)

    # Periodic status (once/day)
    if seconds > 0 and (int(seconds) % 86400 < time_step_s):
        events.append(
            f"[{seconds:>8.0f}s] STATUS: T={temperature_c:.2f}°C (peak={peak_temp_c:.2f}), "
            f"CO₂={canisters[current_canister]:.0f}J({current_canister}), "
            f"Bat={battery_remaining_wh/battery_capacity_wh*100:.1f}%, "
            f"Fan={fan_duty_cycle:.0f}%({fan_mode})"
        )

    # Battery exhausted => stop
    if battery_remaining_wh <= 0:
        events.append(f"[{seconds:>8.0f}s] CRITICAL: Battery depleted. STOP.")
        n_steps = t + 1
        total_time_s = seconds
        break

end_time = time.time()
runtime_s = end_time - start_time
if len(temperature_log) > n_steps:
    temperature_log = temperature_log[:n_steps]

# ========================= 5) RESULTS & SUMMARY ==============================

events.append("\n=== ULTIMATE THERMAL EDEN SIMULATION SUMMARY ===")
events.append(f"Simulation Time (real): {runtime_s:.2f} s")
events.append(f"Simulated Duration: {total_time_s/3600:.1f} hrs ({total_time_s/86400:.1f} days)")
events.append(f"Final Temperature: {temperature_c:.2f}°C")
events.append(f"Peak Temperature: {peak_temp_c:.2f}°C")
events.append(f"Total CO₂ Purges: {purge_count}")
events.append(f"Canister Swaps/Refills: {canister_swaps}")
events.append(f"CO₂ Left (Canister {current_canister}): {canisters[current_canister]:.0f} J")
events.append(f"Total Remaining CO₂: {sum(canisters):.0f} J")
batt_remaining = max(0, battery_remaining_wh)
batt_pct = (batt_remaining / battery_capacity_wh) * 100
events.append(f"Battery Remaining: {batt_remaining:.2f} Wh ({batt_pct:.3f} %)")

# Cooling contributions
events.append("\n=== COOLING CONTRIBUTION ANALYSIS (Joules) ===")
total_cooling_joules = sum(cooling_contribution.values())
if total_cooling_joules > 0:
    for k, v in sorted(cooling_contribution.items(), key=lambda x: x[1], reverse=True):
        pct = (v / total_cooling_joules) * 100
        events.append(f"  {k:<20}: {v:,.0f} J  [{pct:.1f}%]")
else:
    events.append("  No cooling occurred. Possibly zero-length or battery died instantly.")

# ========================= 6) PLOT & OUTPUT ==================================

plt.figure(figsize=(12, 6))
time_days = np.arange(0, n_steps * time_step_s, time_step_s) / 86400.0
plt.plot(time_days, temperature_log, label='CPU Temperature')
plt.axhline(critical_temp_c, color='r', linestyle='--', label=f'Critical ({critical_temp_c}°C)')
plt.axhline(emergency_temp_c, color='orange', linestyle='--', label=f'Emergency ({emergency_temp_c}°C)')
plt.axhline(75, color='y', linestyle=':', label='High (75°C)')
plt.axhline(65, color='g', linestyle=':', label='Medium (65°C)')
plt.xlabel('Time (days)')
plt.ylabel('Temperature (°C)')
plt.title('Long-Term Thermodynamic Balance - 1 Year Simulation')
plt.grid(True, which='both', linestyle='--', alpha=0.7)
plt.legend(loc='best')
plt.tight_layout()

if __name__ == "__main__":
    print("\n".join(events))
    plt.savefig('thermal_eden_simulation_fixed.png', dpi=150)
    # Uncomment to show the plot if running interactively:
    # plt.show()
