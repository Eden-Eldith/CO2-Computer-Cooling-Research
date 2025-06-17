import numpy as np
import matplotlib.pyplot as plt
# from matplotlib.colors import LinearSegmentedColormap # Not used, removed
import time
import sys # For checking recursion depth

# Increase recursion depth limit if needed for very long simulations / complex plots (use with caution)
# try:
#     sys.setrecursionlimit(2000)
# except Exception as e:
#     print(f"Warning: Could not set recursion depth limit - {e}")

# --- Parameters ---

# Base system parameters
cpu_power_watts = 18.5  # Base TDP of the undervolted CPU
passive_dissipation_watts_at_10_delta = 1.5 # Passive dissipation rate at 10°C above ambient
thermal_mass_j_per_c = 300  # Thermal mass of internal components
initial_temp_c = 25  # Starting temperature / Ambient temperature
critical_temp_c = 90  # Absolute max temperature -> triggers immediate purge if possible
emergency_temp_c = 78  # Threshold for aggressive cooling / preemptive purge checks

# CO2 canister parameters
cooling_capacity_joules = 2900  # Energy per canister
purge_efficiency = 0.85 # Fraction of capacity usable in a purge
cooling_effective_joules = cooling_capacity_joules * purge_efficiency # Energy removed by one purge
conduction_watts = 2.2  # Passive cooling power from cold canister surface after purge
conduction_duration = 180  # seconds of passive conduction cooling after purge

# Peltier (TEC) parameters
peltier_max_cooling_watts = 15  # Max heat absorption rate from cold side (CPU)
peltier_power_draw = 30  # Electrical power consumption (Watts)
peltier_max_runtime = 120  # Max continuous seconds allowed (prevent self-overheating)
battery_capacity_wh = 3287 * 1000 * 1000 # 3,287 MWh -> Wh (Large battery for long test)
peltier_efficiency_base = 0.6  # Base max efficiency (COP/max_COP approximation)
thermal_mass_hot_side_j_per_c = 50 # Estimated thermal mass of TEC hot side + heatsink
k_hot_dissipation_w_per_c = 1.0 # Heat transfer coefficient (W/°C) from hot side to ambient

# Fan parameters
fan_power_draw = 0.25  # watts
fan_efficiency_multiplier_base = 1.3  # Max boost factor to passive/conduction cooling at 100% duty
fan_efficiency_multiplier_max = 2.5  # Absolute cap on the fan multiplier effect
fan_ramp_time = 1.0  # seconds to ramp from 0% to 100% or vice-versa

# Simulation duration
total_time_s = 31536000 # 1 year (365 * 24 * 3600)
# total_time_s = 3600 * 24 # 1 day (for quicker testing)
time_step_s = 5 # Simulation time step in seconds
n_steps = total_time_s // time_step_s

# --- Initialization ---
canisters = [cooling_capacity_joules, cooling_capacity_joules]
current_canister = 0
purge_count = 0
canister_swaps = 0
last_purge_time = -conduction_duration - 1 # Initialize safely outside post-purge window
temperature_c = initial_temp_c
peak_temp_c = initial_temp_c
events = []
temperature_log = np.zeros(n_steps) # Pre-allocate numpy array

# Peltier tracking
peltier_active = False
peltier_runtime_s = 0
battery_remaining_wh = battery_capacity_wh
hot_side_temp_c = initial_temp_c

# Fan tracking
fan_active = False
fan_duty_cycle = 0
fan_mode = "PASSIVE"
post_purge_timer = 0 # Counts *down* remaining boosted time

# Analysis / Summary Tracking
cooling_contribution = { m: 0.0 for m in ["passive", "co2_hiss", "co2_purge", "canister_conduction", "peltier", "fan_boost"] }
total_cpu_heat_joules = 0.0 # Correctly accumulate heat generated

# --- Helper Functions ---

def get_cpu_workload(time_s):
    """
    Simulate CPU load for a 24/7 continuously operated machine.
    Includes a base load and periodic intense phases.
    Removed daily cycles as requested.
    """
    # Higher base load for 24/7 operation compared to intermittent use
    base_load = cpu_power_watts * 0.75

    # Add some long-term variability (e.g., weekly pattern)
    week_seconds = time_s % (7 * 86400)
    # Slightly higher load during "business hours" equivalent of the week?
    if week_seconds < 5 * 86400: # Monday-Friday equivalent
        week_multiplier = 1.05
    else: # Weekend equivalent
        week_multiplier = 0.95

    # Periodic intense workloads (represent batch jobs, peak demands etc.)
    intense_load = 0
    # Intense period 1: Month 2-3 (approx)
    intense_start1 = total_time_s * 0.12
    intense_end1 = intense_start1 + 3600 * 24 * 10 # 10 days intense work
    # Intense period 2: Month 8-9 (approx)
    intense_start2 = total_time_s * 0.65
    intense_end2 = intense_start2 + 3600 * 24 * 15 # 15 days intense work

    if intense_start1 <= time_s < intense_end1 or intense_start2 <= time_s < intense_end2:
       # Additive intense load - represents extra tasks on top of base
       intense_load = cpu_power_watts * 0.40

    # Combine factors
    dynamic_load = base_load * week_multiplier + intense_load

    # Add small random noise for minor fluctuations
    noise = np.random.uniform(-0.05, 0.05) * cpu_power_watts
    dynamic_load += noise

    # Ensure load doesn't exceed absolute max or go below a minimum idle
    return max(cpu_power_watts * 0.2, min(cpu_power_watts * 1.25, dynamic_load))


def calculate_peltier_efficiency(cpu_temp, hot_side_temp):
    """Calculate Peltier efficiency (approx COP) based on temperature differential"""
    delta_T = hot_side_temp - cpu_temp
    if delta_T <= 0:
        return peltier_efficiency_base # Favorable conditions, max efficiency

    max_delta_t_realistic = 70.0 # Assumed max delta T for reasonable COP
    # Use a power > 1 for steeper drop-off with increasing delta_T
    efficiency_factor = max(0.0, 1.0 - (delta_T / max_delta_t_realistic)**1.5)
    efficiency = peltier_efficiency_base * efficiency_factor

    # Further penalize if hot side is excessively hot
    if hot_side_temp > 85: efficiency *= 0.5
    if hot_side_temp > 95: efficiency = 0.0 # Essentially stops working

    return max(0.0, min(peltier_efficiency_base, efficiency))


def calculate_fan_multiplier(duty_cycle, is_post_purge=False, current_post_purge_timer=0):
    """Calculate cooling efficiency boost factor from fan operation."""
    if duty_cycle <= 0: return 1.0

    # Base multiplier scales linearly with duty cycle up to max base boost
    base_mult = 1.0 + (fan_efficiency_multiplier_base - 1.0) * (duty_cycle / 100.0)

    # Additional boost from higher speed (less linear, maybe diminishing returns)
    # Let's use a sqrt relationship for the extra boost beyond base
    speed_factor = 1.0 + (np.sqrt(duty_cycle / 100.0)) * 0.3 # Additional 30% boost at 100%

    # Post-purge boost, decays linearly over remaining time
    purge_boost = 1.0
    if is_post_purge and conduction_duration > 0:
        decay_factor = max(0.0, min(1.0, current_post_purge_timer / conduction_duration))
        purge_boost = 1.0 + 0.7 * decay_factor # Up to 70% boost right after purge

    calculated_multiplier = base_mult * speed_factor * purge_boost
    return min(calculated_multiplier, fan_efficiency_multiplier_max) # Cap at absolute max


def manage_peltier(cpu_temp, battery_level, hot_side_temp, time_since_purge):
    """Determine if Peltier should be active"""
    global peltier_active, peltier_runtime_s

    can_activate = (
        battery_level > (0.05 * battery_capacity_wh) and # Min 5% battery
        hot_side_temp < 85 # Safety threshold
    )
    should_activate_temp = cpu_temp > 70
    should_activate_post_purge = 0 <= time_since_purge < 60 # Short boost after purge

    activate_now = can_activate and (should_activate_temp or should_activate_post_purge)

    should_deactivate_temp = cpu_temp < 65
    should_deactivate_battery = battery_level < (0.03 * battery_capacity_wh) # Min 3% battery
    should_deactivate_hot_side = hot_side_temp > 95
    should_deactivate_runtime = peltier_runtime_s >= peltier_max_runtime

    deactivate_now = should_deactivate_temp or should_deactivate_battery or should_deactivate_hot_side or should_deactivate_runtime

    if peltier_active:
        if deactivate_now:
            peltier_active = False
            peltier_runtime_s = 0 # Reset runtime only when turning off
    else:
        if activate_now:
            peltier_active = True
            # Runtime starts accumulating from 0 (already 0 or reset previously)

def manage_fan(cpu_temp, is_post_purge):
    """Control fan duty cycle based on thermal conditions"""
    global fan_active, fan_duty_cycle, fan_mode

    target_duty = 0
    if cpu_temp < 50 and not is_post_purge:
        fan_mode = "PASSIVE"
        target_duty = 0
    elif cpu_temp < 65 and not is_post_purge:
        fan_mode = "LOW"
        target_duty = 25 # Minimum active airflow
    elif is_post_purge:
        fan_mode = "PURGE_ASSIST"
        target_duty = 85 # High speed to leverage cold surfaces
    elif cpu_temp > emergency_temp_c:
        fan_mode = "EMERGENCY"
        target_duty = 100 # Max speed
    elif cpu_temp >= 65: # Temp between 65 and emergency_temp_c
        fan_mode = "NORMAL"
        temp_range = max(1, emergency_temp_c - 65) # Avoid division by zero
        temp_fraction = (cpu_temp - 65) / temp_range
        target_duty = 40 + temp_fraction * 60 # Scale duty cycle 40% -> 100%
        target_duty = min(100, target_duty)

    # Smooth ramp
    ramp_step = (100.0 / max(0.1, fan_ramp_time)) * time_step_s # Duty change per step
    if target_duty > fan_duty_cycle:
        fan_duty_cycle = min(target_duty, fan_duty_cycle + ramp_step)
    elif target_duty < fan_duty_cycle:
        fan_duty_cycle = max(target_duty, fan_duty_cycle - ramp_step)

    fan_duty_cycle = max(0.0, min(100.0, fan_duty_cycle))
    fan_active = fan_duty_cycle > 0

# --- Simulation Start ---
start_time = time.time()
events.append("Simulation Started... (1 Year, 24/7 Operation)")

# --- Main Simulation Loop ---
for t in range(n_steps):
    seconds = t * time_step_s

    # 1. Get CPU Power & Update Total Heat Generated
    current_cpu_power = get_cpu_workload(seconds)
    total_cpu_heat_joules += current_cpu_power * time_step_s

    # 2. Update Timers & States
    time_since_last_purge = seconds - last_purge_time
    is_post_purge = 0 <= time_since_last_purge <= conduction_duration
    post_purge_timer = max(0, conduction_duration - time_since_last_purge) if is_post_purge else 0

    # 3. Manage Fan (Set duty cycle) & Calculate Multiplier
    manage_fan(temperature_c, is_post_purge)
    fan_multiplier = calculate_fan_multiplier(fan_duty_cycle, is_post_purge, post_purge_timer)

    # 4. Manage Peltier (Set active state)
    manage_peltier(temperature_c, battery_remaining_wh, hot_side_temp_c, time_since_last_purge)

    # --- Calculate Cooling Power Components (Watts) ---

    # 4a. Peltier Cooling & Hot Side Physics
    peltier_cooling_watts = 0.0
    peltier_heat_generated_watts = 0.0
    hot_side_dissipation_watts = 0.0

    # Calculate potential hot side dissipation (even if Peltier is off)
    # Cools towards ambient, enhanced by fan
    hot_side_delta_T_ambient = hot_side_temp_c - initial_temp_c
    if hot_side_delta_T_ambient > 0:
         # Dissipation depends on temp diff and fan multiplier
        hot_side_dissipation_watts = k_hot_dissipation_w_per_c * hot_side_delta_T_ambient * fan_multiplier

    if peltier_active:
        peltier_efficiency = calculate_peltier_efficiency(temperature_c, hot_side_temp_c)
        peltier_cooling_watts = peltier_max_cooling_watts * peltier_efficiency
        peltier_heat_generated_watts = peltier_power_draw + peltier_cooling_watts # Heat dumped = Power in + Heat moved

        # Update Peltier runtime and battery
        peltier_runtime_s += time_step_s
        battery_remaining_wh -= (peltier_power_draw * time_step_s) / 3600.0
    # else: runtime reset in manage_peltier

    # Net power affecting hot side: Heat generated - Heat dissipated
    net_power_hot_side = peltier_heat_generated_watts - hot_side_dissipation_watts
    delta_temp_hot = (net_power_hot_side * time_step_s) / thermal_mass_hot_side_j_per_c
    hot_side_temp_c += delta_temp_hot
    hot_side_temp_c = max(initial_temp_c, hot_side_temp_c) # Cannot cool below ambient passively

    # 4b. Passive System Cooling (Enhanced by Fan)
    # Base passive cooling depends on temp difference to ambient
    k_passive_w_per_c = passive_dissipation_watts_at_10_delta / 10.0 # Calculate conductance
    passive_cooling_watts = k_passive_w_per_c * max(0, temperature_c - initial_temp_c)
    enhanced_passive_cooling = passive_cooling_watts * fan_multiplier

    # 4c. CO2 Canister Conduction Cooling (Post-Purge, Enhanced by Fan)
    conduction_cooling_watts = conduction_watts if is_post_purge else 0
    enhanced_conduction_cooling = conduction_cooling_watts * fan_multiplier

    # 4d. CO2 Microburst Hiss Cooling (Scheduled, Enhanced by Fan)
    hiss_cooling_watts = 0.0
    hiss_energy_joules = 0.0 # Energy consumed this step

    # Determine burst schedule based on temperature
    if temperature_c < 60: cycle_time = 8.0; burst_duration = 0.3
    elif 60 <= temperature_c < 70: cycle_time = 5.0; burst_duration = 0.5
    elif 70 <= temperature_c < 75: cycle_time = 4.0; burst_duration = 0.7
    else: cycle_time = 3.0; burst_duration = 1.0 # >= 75

    burst_now = (canisters[current_canister] > 0 and cycle_time > 0 and (seconds % cycle_time < time_step_s))

    if burst_now:
        joules_per_burst = burst_duration * 3.0 # Assume 3W effective rate during burst
        hiss_energy_joules = min(joules_per_burst, canisters[current_canister])
        hiss_cooling_watts = hiss_energy_joules / time_step_s # Average power over time step

    enhanced_hiss_cooling = hiss_cooling_watts * fan_multiplier

    # --- Calculate Total Cooling and Net Power on Main System ---
    total_continuous_cooling_watts = (
        enhanced_passive_cooling
        + enhanced_conduction_cooling
        + enhanced_hiss_cooling
        + peltier_cooling_watts # Direct cooling effect on CPU side
    )
    net_power_system = current_cpu_power - total_continuous_cooling_watts

    # --- Update System Temperature (Main Thermal Mass) ---
    delta_temp = (net_power_system * time_step_s) / thermal_mass_j_per_c
    temperature_c += delta_temp

    # --- Emergency CO2 Purge Logic ---
    purge_temp_drop = 0.0
    needs_critical_purge = temperature_c > critical_temp_c
    # Preemptive purge only if really hot AND low on current AND other is empty (swap preferred otherwise)
    needs_preemptive_purge = (temperature_c > emergency_temp_c + 5 and # Higher threshold for preemptive
                              canisters[current_canister] < (cooling_effective_joules * 0.2) and
                              canisters[1-current_canister] < 50 )

    if needs_critical_purge or needs_preemptive_purge:
        if canisters[current_canister] >= cooling_effective_joules:
            purge_joules_used = cooling_effective_joules
            # Fan boost on purge effectiveness (clearing cold air)
            effective_purge_joules = purge_joules_used * (1 + 0.1 * (fan_duty_cycle / 100.0))
            purge_temp_drop = effective_purge_joules / thermal_mass_j_per_c

            temperature_c -= purge_temp_drop # Instantaneous drop
            canisters[current_canister] -= purge_joules_used
            purge_count += 1
            last_purge_time = seconds
            cooling_contribution["co2_purge"] += effective_purge_joules # Track effective energy removed

            events.append(f"[{seconds/86400:>4.1f}d] PURGE: T {temperature_c + purge_temp_drop:.1f}->{temperature_c:.1f}°C | CO2={canisters[current_canister]:.0f}J | Fan={fan_duty_cycle:.0f}%")
        # else: Not enough for purge, swap/refill might happen below

    # --- Canister Swap / Refill Logic ---
    if canisters[current_canister] < 50: # Swap threshold
        other_canister = 1 - current_canister
        if canisters[other_canister] > 50: # Swap if other is usable
            current_canister = other_canister
            canister_swaps += 1
            events.append(f"[{seconds/86400:>4.1f}d] SWAP -> Can {current_canister} | CO2={canisters[current_canister]:.0f}J | T={temperature_c:.1f}°C")
        else: # Both low -> Refill
            canisters = [cooling_capacity_joules, cooling_capacity_joules]
            current_canister = 0
            canister_swaps += 1 # Count refill as a swap action
            events.append(f"[{seconds/86400:>4.1f}d] REFILL Both | T={temperature_c:.1f}°C")

    # --- Apply Energy Consumptions ---
    # CO2 Hiss
    canisters[current_canister] = max(0, canisters[current_canister] - hiss_energy_joules)
    # Fan Power
    if fan_active:
        battery_remaining_wh -= (fan_power_draw * (fan_duty_cycle / 100.0) * time_step_s) / 3600.0

    # --- Track Cooling Contributions (Joules) & Fan Boost ---
    base_passive_joules = passive_cooling_watts * time_step_s
    base_conduction_joules = conduction_cooling_watts * time_step_s
    base_hiss_joules = hiss_cooling_watts * time_step_s
    peltier_joules = peltier_cooling_watts * time_step_s

    enhanced_passive_joules = enhanced_passive_cooling * time_step_s
    enhanced_conduction_joules = enhanced_conduction_cooling * time_step_s
    enhanced_hiss_joules = enhanced_hiss_cooling * time_step_s

    # Fan boost is the difference between enhanced and base cooling for fan-affected terms
    fan_boost_joules = (enhanced_passive_joules - base_passive_joules) + \
                       (enhanced_conduction_joules - base_conduction_joules) + \
                       (enhanced_hiss_joules - base_hiss_joules)
    # Also add boost to hot side dissipation (indirect effect)? For simplicity, focus on direct cooling boost.

    cooling_contribution["passive"] += enhanced_passive_joules
    cooling_contribution["canister_conduction"] += enhanced_conduction_joules
    cooling_contribution["co2_hiss"] += enhanced_hiss_joules
    cooling_contribution["peltier"] += peltier_joules
    cooling_contribution["fan_boost"] += fan_boost_joules

    # --- Final Temp Constraints & Logging ---
    temperature_c = max(initial_temp_c * 0.9, temperature_c) # Limit unrealistic cooling unless actively driven hard
    temperature_log[t] = temperature_c
    if temperature_c > peak_temp_c: peak_temp_c = temperature_c

    # --- Status Reporting & Safety Break ---
    status_interval_days = 7 # Report weekly
    status_interval_s = status_interval_days * 86400
    if seconds > 0 and (seconds % status_interval_s < time_step_s):
         events.append(f"[{seconds/86400:>4.0f}d] Stat: T={temperature_c:.1f}°C (Pk:{peak_temp_c:.1f}°C)|CO2={canisters[current_canister]:.0f}({current_canister})|Bat={battery_remaining_wh/battery_capacity_wh*100:.1f}%|Fan={fan_duty_cycle:.0f}%({fan_mode})|Pel:{'ON' if peltier_active else 'OFF'}(Hot:{hot_side_temp_c:.1f}°C)")

    if battery_remaining_wh <= 0:
        events.append(f"[{seconds/86400:>4.1f}d] CRITICAL: Battery depleted. Simulation HALTED.")
        n_steps = t + 1 # Correct step count
        total_time_s = seconds
        temperature_log = temperature_log[:n_steps] # Trim log
        break

# --- Simulation End ---
end_time = time.time()
simulation_runtime = end_time - start_time

# Adjust log if stopped early
if len(temperature_log) > n_steps: temperature_log = temperature_log[:n_steps]
elif len(temperature_log) < n_steps: n_steps = len(temperature_log)

# --- Generate Summary Report ---
events.append(f"\n=== SIMULATION SUMMARY ===")
events.append(f"Compute Time: {simulation_runtime:.2f} seconds")
events.append(f"Simulated Duration: {total_time_s / 86400.0:.2f} days ({n_steps} steps)")
events.append(f"Final Temp: {temperature_c:.2f}°C")
events.append(f"Peak Temp: {peak_temp_c:.2f}°C")
events.append(f"CO₂ Purges: {purge_count}")
events.append(f"Canister Swaps/Refills: {canister_swaps}")
events.append(f"Final CO₂ (Can {current_canister}): {canisters[current_canister]:.0f}J")
final_bat_pct = max(0, battery_remaining_wh / battery_capacity_wh * 100)
events.append(f"Final Battery: {max(0, battery_remaining_wh):.1f}Wh ({final_bat_pct:.1f}%)")

events.append(f"\n=== COOLING CONTRIBUTION (Total Joules) ===")
# Purge contribution already added, sum others
total_energy_removed = sum(cooling_contribution.values())

if total_energy_removed > 0:
    sorted_contributions = sorted(cooling_contribution.items(), key=lambda item: item[1], reverse=True)
    for mechanism, joules in sorted_contributions:
        if joules != 0: # Only show contributing factors
            percentage = (joules / total_energy_removed) * 100 if total_energy_removed > 0 else 0
            unit = "<< Instantaneous" if mechanism == "co2_purge" else ""
            events.append(f"- {mechanism:<20}: {joules:,.0f} J ({percentage:.1f}%) {unit}")
    events.append(f"- {'TOTAL ENERGY REMOVED':<20}: {total_energy_removed:,.0f} J")
else:
    events.append("No significant cooling occurred.")

events.append(f"\nTotal CPU Heat Generated (Est): {total_cpu_heat_joules:,.0f} J")
energy_balance = total_cpu_heat_joules - total_energy_removed
final_thermal_energy_change = (temperature_c - initial_temp_c) * thermal_mass_j_per_c
events.append(f"Energy Balance Check (Heat In - Heat Out): {energy_balance:,.0f} J")
events.append(f"Expected Stored Energy Change (Final T - Initial T): {final_thermal_energy_change:,.0f} J")


# --- Create Temperature Chart ---
try:
    plt.style.use('seaborn-v0_8-darkgrid')
    plt.figure(figsize=(15, 8))
    time_axis_days = np.arange(n_steps) * time_step_s / 86400.0

    if n_steps > 0 : # Ensure there is data to plot
        plt.plot(time_axis_days, temperature_log[:n_steps], label='CPU Temperature', linewidth=1.0)

        # Threshold lines
        plt.axhline(y=critical_temp_c, color='red', linestyle='--', linewidth=1, label=f'Critical ({critical_temp_c}°C)')
        plt.axhline(y=emergency_temp_c, color='orange', linestyle='--', linewidth=1, label=f'Emergency ({emergency_temp_c}°C)')
        plt.axhline(y=70, color='gold', linestyle=':', linewidth=0.8, label='High Threshold (70°C)')
        plt.axhline(y=initial_temp_c, color='blue', linestyle=':', linewidth=0.8, label=f'Ambient ({initial_temp_c}°C)')

        # Formatting
        plt.xlabel('Time (days)')
        plt.ylabel('Temperature (°C)')
        plt.title(f'Thermal Simulation - 24/7 Operation ({total_time_s / 86400.0:.1f} Days)')
        plt.legend(loc='upper left', bbox_to_anchor=(1, 1)) # Legend outside
        plt.grid(True, which='both', linestyle='--', linewidth=0.5)

        min_temp_plot = max(0, initial_temp_c - 15)
        max_temp_plot = min(110, peak_temp_c + 15)
        plt.ylim(min_temp_plot, max_temp_plot)
        plt.xlim(0, time_axis_days[-1] if n_steps > 0 else 1)
        plt.tight_layout(rect=[0, 0, 0.85, 1]) # Adjust for legend

        plt.savefig('thermal_eden_simulation_1year_247_fixed.png', dpi=200)
        print("\nPlot saved to 'thermal_eden_simulation_1year_247_fixed.png'")
        # plt.show() # Uncomment to display interactively
    else:
        events.append("\nWarning: No simulation steps completed, cannot generate plot.")

except Exception as e:
    events.append(f"\nERROR generating plot: {e}")
    # This might catch issues if n_steps is 0 or very small, or plotting errors

# Print the simulation events and summary
print("\n".join(events))