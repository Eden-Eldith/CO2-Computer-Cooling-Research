import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import time

# Ultimate Tactical Field Protocol Simulation (Eden Edition)
# Combines CO2 canisters, Peltier TEC, and micro fan in one epic cooling system

# Base system parameters
cpu_power_watts = 18.5  # undervolted CPU
passive_dissipation_watts = 1.5  # degraded by sand/grit
thermal_mass_j_per_c = 300  # thermal mass of internal components
initial_temp_c = 25  # starting temperature
critical_temp_c = 90  # absolute max temperature
emergency_temp_c = 78  # threshold for emergency measures

# CO2 canister parameters
cooling_capacity_joules = 2900  # per canister
purge_efficiency = 0.85
cooling_effective_joules = cooling_capacity_joules * purge_efficiency
cooldown_per_purge_c = cooling_effective_joules / thermal_mass_j_per_c
conduction_watts = 2.2  # passive cooling from cold canister
conduction_duration = 180  # seconds of passive cooling after purge

# Peltier (TEC) parameters
peltier_max_cooling_watts = 15  # cooling capacity
peltier_power_draw = 30  # electrical consumption
peltier_max_runtime = 120  # max continuous seconds
battery_capacity_wh = 8500000000  # monolithic base battery Edwards & Sanborn solar-plus-storage project in California, USA, currently holds the title of the world's largest battery storage system-3,287 MWh
peltier_efficiency_base = 0.6  # max efficiency ratio

# Fan parameters
fan_power_draw = 0.25  # watts
fan_efficiency_multiplier_base = 1.3  # minimum efficiency boost
fan_efficiency_multiplier_max = 2.5  # maximum with ideal conditions
fan_ramp_time = 1.0  # seconds to reach full speed

# Simulation duration
total_time_s = 31536000 # 60 minutes -> Changed to 1 year for testing
time_step_s = 5
n_steps = total_time_s // time_step_s

# Initialize tracking variables
canisters = [cooling_capacity_joules, cooling_capacity_joules]
current_canister = 0
purge_count = 0
canister_swaps = 0
last_purge_time = -9999
temperature_c = initial_temp_c
peak_temp_c = initial_temp_c # <<< OPTIMIZATION: Track peak temp during simulation
events = []
temperature_log = [] # Keep log for plotting

# Peltier tracking
peltier_active = False
peltier_runtime_s = 0
battery_remaining_wh = battery_capacity_wh
hot_side_temp_c = initial_temp_c
cold_side_temp_c = initial_temp_c

# Fan tracking
fan_active = False
fan_duty_cycle = 0
fan_mode = "PASSIVE"
post_purge_timer = 0

# For detailed analysis
cooling_contribution = {
    "passive": 0,
    "co2_hiss": 0,
    "co2_purge": 0,
    "canister_conduction": 0,
    "peltier": 0,
    "fan_boost": 0
}

# Simulate CPU workload variations (more realistic)
def get_cpu_workload(time_s):
    """Simulate varying CPU load to mimic real usage patterns"""
    base_load = cpu_power_watts * 0.85  # 85% of max is baseline

    # Add some variation - periodic loads every 5 minutes (scaled for longer sim)
    variation = np.sin(time_s / (300 * 60) * np.pi) * 0.15 * cpu_power_watts # Adjust period for year

    # Add two intense workloads during the simulation (adjust timing for year)
    intense_start1 = total_time_s * 0.1
    intense_end1 = intense_start1 + 3600 * 2 # 2 hours intense work
    intense_start2 = total_time_s * 0.6
    intense_end2 = intense_start2 + 3600 * 4 # 4 hours intense work

    if intense_start1 < time_s < intense_end1 or intense_start2 < time_s < intense_end2:
        return cpu_power_watts * 1.1  # 110% of rated TDP during intense work

    return base_load + variation

def calculate_peltier_efficiency(cpu_temp, hot_side_temp):
    """Calculate Peltier efficiency based on temperature differential"""
    temp_diff = hot_side_temp - cpu_temp
    if temp_diff <= 0:  # No differential or inverted (unlikely)
        return peltier_efficiency_base

    # Efficiency drops as temperature differential increases
    efficiency = peltier_efficiency_base * (1 - (temp_diff / 70)**2)

    # Efficiency drops dramatically if hot side gets too hot
    if hot_side_temp > 85:
        efficiency *= 0.5

    return max(0.1, min(peltier_efficiency_base, efficiency))  # Bounds

def calculate_fan_multiplier(duty_cycle, is_post_purge=False, purge_timer=0):
    """Calculate cooling efficiency boost from fan operation"""
    if duty_cycle <= 0:
        return 1.0  # No enhancement

    # Base multiplier from breaking boundary layers
    base_mult = 1.0 + (fan_efficiency_multiplier_base - 1.0) * (duty_cycle / 100)

    # Speed effect
    speed_factor = 1.0 + (duty_cycle / 100) * 0.7

    # Post-purge effectiveness boost (Cryo-Assist Chamber effect)
    purge_boost = 1.0
    if is_post_purge:
        # Effect decays over time after purge
        decay_factor = max(0, min(1, (conduction_duration - purge_timer) / conduction_duration)) # Correct decay calculation
        purge_boost = 1.0 + 0.5 * decay_factor

    return base_mult * speed_factor * purge_boost

def manage_peltier(cpu_temp, battery_level, co2_available, time_since_purge):
    """Determine if Peltier should be active based on conditions"""
    global peltier_active, peltier_runtime_s

    # Conditions to activate
    should_activate = (
        cpu_temp > 70 and  # Only when needed
        battery_level > (0.05 * battery_capacity_wh) and  # Preserve battery (use percentage)
        peltier_runtime_s < peltier_max_runtime and  # Prevent overheating
        hot_side_temp_c < 90  # Prevent TEC damage
    )

    # Conditions for deactivation
    should_deactivate = (
        cpu_temp < 65 or  # Cool enough
        battery_level < (0.03 * battery_capacity_wh) or  # Critical battery (use percentage)
        hot_side_temp_c > 95 or  # Overheating risk
        peltier_runtime_s >= peltier_max_runtime  # Runtime limit
    )

    # Special case - activate after purge for bonus cooling
    post_purge_boost = time_since_purge >= 0 and time_since_purge < 60 # Correct condition >= 0

    # Logic combining activation/deactivation
    if peltier_active:
        if should_deactivate:
            peltier_active = False
            peltier_runtime_s = 0 # Reset runtime on deactivation
    else: # If currently inactive
        if should_activate or post_purge_boost:
             # Check battery before activating
            if battery_level > (0.05 * battery_capacity_wh):
                peltier_active = True
            else:
                peltier_active = False # Not enough battery even if conditions met
                peltier_runtime_s = 0 # Ensure runtime is 0 if not activated

def manage_fan(cpu_temp, is_post_purge, seconds_since_purge):
    """Control fan behavior based on thermal conditions"""
    global fan_active, fan_duty_cycle, fan_mode

    # Determine operating mode based on current temperature and state
    target_duty = 0 # Default target duty cycle
    current_seconds = seconds # Use the global 'seconds' from the loop

    if cpu_temp < 50 and not is_post_purge:
        fan_mode = "PASSIVE"
        target_duty = 0
    elif cpu_temp < 65:
        fan_mode = "SLOW_HISS"
        # Pulse the fan occasionally
        if int(current_seconds) % 15 == 0:  # Every 15 seconds
            target_duty = 30
        else:
            target_duty = 0 # Ensure it stays off between pulses
    elif is_post_purge:
        fan_mode = "PURGE"
        target_duty = 80
    elif cpu_temp > 75: # Use emergency temp threshold? No, 75 is fine as aggressive threshold
        fan_mode = "EMERGENCY"
        target_duty = 100
    else: # Between 65 and 75, not post-purge
        fan_mode = "NORMAL"
        target_duty = 50

    # Smooth ramping for fan speed adjustment
    ramp_up_step = (100 / fan_ramp_time) * time_step_s # Calculate ramp step based on time_step
    ramp_down_step = ramp_up_step * 0.5 # Slower ramp down

    if target_duty > fan_duty_cycle:
        fan_duty_cycle = min(target_duty, fan_duty_cycle + ramp_up_step)
    elif target_duty < fan_duty_cycle:
        fan_duty_cycle = max(target_duty, fan_duty_cycle - ramp_down_step)

    fan_duty_cycle = max(0, min(100, fan_duty_cycle)) # Ensure duty cycle stays within [0, 100]
    fan_active = fan_duty_cycle > 0


# --- Simulation Start ---
start_time = time.time() # Record start time for performance measurement

# Begin simulation
for t in range(n_steps):
    seconds = t * time_step_s

    # Get dynamic CPU power based on workload
    current_cpu_power = get_cpu_workload(seconds)

    # Track time since last purge
    time_since_last_purge = seconds - last_purge_time
    is_post_purge = 0 <= time_since_last_purge <= conduction_duration

    # Update post-purge timer for fan control (counts down remaining duration)
    if is_post_purge:
        post_purge_timer = conduction_duration - time_since_last_purge
    else:
        post_purge_timer = 0 # Reset if not in post-purge phase

    # --- Cooling Contributions ---

    # 1. Passive shell cooling
    passive_cooling = passive_dissipation_watts
    # Contribution tracked later after fan boost is applied

    # 2. Canister conduction cooling (after purge)
    conduction_cooling = conduction_watts if is_post_purge else 0
    # Contribution tracked later after fan boost is applied

    # 3. Determine CO2 microburst parameters based on temperature
    if temperature_c < 60:
        burst_duration = 0.3
        cycle_time = 8.0
    elif 60 <= temperature_c < 70:
        burst_duration = 0.5
        cycle_time = 5.0
    elif 70 <= temperature_c < 75:
        burst_duration = 0.7
        cycle_time = 4.0
    else: # temperature_c >= 75
        burst_duration = 1.0
        cycle_time = 3.0

    # Apply CO2 microburst if timing aligns and we have CO2
    # Use a small tolerance for modulo on float cycle times if needed, but int() works here
    burst_now = (canisters[current_canister] > 0 and int(cycle_time) > 0 and seconds % int(cycle_time) < time_step_s) # Check if within the first time step of the cycle
    hiss_joules_per_burst = burst_duration * 3.0 # Joules per burst event
    hiss_energy = hiss_joules_per_burst if burst_now else 0
    hiss_cooling = hiss_energy / time_step_s # Convert burst energy to power (Watts) over the time step
    # Contribution tracked later after fan boost is applied

    # 4. Manage Peltier device
    manage_peltier(temperature_c, battery_remaining_wh, canisters[current_canister] > 50, time_since_last_purge)

    # Apply Peltier cooling if active
    peltier_cooling = 0
    if peltier_active:
        peltier_efficiency = calculate_peltier_efficiency(temperature_c, hot_side_temp_c)
        peltier_cooling = peltier_max_cooling_watts * peltier_efficiency

        # Update hot side temperature (simplified thermal model)
        # Heat generated = Electrical Power * (1 - efficiency) + Heat absorbed from cold side (peltier_cooling)
        peltier_heat_generated = peltier_power_draw + peltier_cooling # Total heat dumped to hot side
        # Simplified hot side delta T: (Heat generated - passive dissipation) * time / thermal_mass
        # Using a simpler arbitrary factor for hot side temp rise/fall for stability
        hot_side_delta_t = (peltier_heat_generated * 0.01 - passive_dissipation_watts * 0.1) * time_step_s
        hot_side_temp_c += hot_side_delta_t
        hot_side_temp_c = max(temperature_c, hot_side_temp_c) # Hot side can't be colder than CPU temp

        # Track power consumption
        peltier_power_consumed_ws = peltier_power_draw * time_step_s
        battery_remaining_wh -= peltier_power_consumed_ws / 3600
        peltier_runtime_s += time_step_s
        # Contribution tracked later after fan boost
    else:
        # Hot side cools down towards CPU temp when Peltier is off
        cooling_rate = 0.1 # Arbitrary cooling rate towards equilibrium
        hot_side_temp_c -= (hot_side_temp_c - temperature_c) * cooling_rate * time_step_s
        hot_side_temp_c = max(temperature_c, hot_side_temp_c) # Ensure it doesn't drop below CPU temp
        # peltier_runtime_s is reset in manage_peltier when deactivated

    # 5. Manage and apply fan effects
    manage_fan(temperature_c, is_post_purge, time_since_last_purge)

    # Calculate fan efficiency multiplier
    fan_multiplier = calculate_fan_multiplier(fan_duty_cycle, is_post_purge, post_purge_timer)

    # Fan power consumption
    if fan_active:
        fan_power_consumed_ws = fan_power_draw * (fan_duty_cycle/100.0) * time_step_s # Use float division
        battery_remaining_wh -= fan_power_consumed_ws / 3600

    # --- Apply Fan Boost and Calculate Total Cooling ---
    enhanced_passive = passive_cooling * fan_multiplier
    enhanced_conduction = conduction_cooling * fan_multiplier
    enhanced_hiss = hiss_cooling * fan_multiplier
    enhanced_peltier = peltier_cooling * fan_multiplier

    total_cooling = enhanced_passive + enhanced_conduction + enhanced_hiss + enhanced_peltier

    # --- Track Cooling Contributions (Joules over the time step) ---
    cooling_contribution["passive"] += enhanced_passive * time_step_s
    cooling_contribution["canister_conduction"] += enhanced_conduction * time_step_s
    cooling_contribution["co2_hiss"] += enhanced_hiss * time_step_s # Hiss contribution includes fan boost
    cooling_contribution["peltier"] += enhanced_peltier * time_step_s # Peltier contribution includes fan boost

    # Calculate fan boost contribution separately for analysis
    # Fan boost = (Total with fan) - (Total without fan)
    base_total_cooling = passive_cooling + conduction_cooling + hiss_cooling + peltier_cooling
    fan_boost_watts = total_cooling - base_total_cooling
    cooling_contribution["fan_boost"] += fan_boost_watts * time_step_s

    # --- Emergency Purge Logic ---
    # Condition: Temp above emergency OR (Temp high AND current canister low)
    needs_purge = temperature_c > critical_temp_c # Definitely purge if above critical
    maybe_purge = temperature_c > emergency_temp_c and canisters[current_canister] < (cooling_capacity_joules * 0.15) # Purge if hot and low fuel

    if needs_purge or maybe_purge:
        # Check if current canister has enough for a full purge
        if canisters[current_canister] >= cooling_effective_joules:
            # Perform purge
            temp_drop = cooldown_per_purge_c * fan_multiplier # Fan enhances purge effectiveness
            temperature_c -= temp_drop
            canisters[current_canister] -= cooling_effective_joules
            purge_count += 1
            last_purge_time = seconds
            cooling_contribution["co2_purge"] += cooling_effective_joules # Purge is instantaneous, not affected by fan over time step

            events.append(f"[{seconds:>8.0f}s] EMERGENCY PURGE: Temp → {temperature_c:.2f}°C ({temp_drop:.2f}°C drop)| " +
                          f"CO₂ Left: {canisters[current_canister]:.0f}J | Fan: {fan_duty_cycle:.0f}% | " +
                          f"Battery: {battery_remaining_wh/battery_capacity_wh*100:.1f}%")
        else:
            # Not enough in current canister for full purge, try swapping first
             pass # Swap logic below will handle this if possible

    # --- Adaptive Canister Swap Logic ---
    # Swap if current canister is low (<50J as threshold)
    if canisters[current_canister] < 50:
        other_canister = 1 - current_canister
        # Check if the *other* canister has sufficient charge (>50J threshold)
        if canisters[other_canister] > 50:
            current_canister = other_canister
            canister_swaps += 1
            events.append(f"[{seconds:>8.0f}s] CANISTER SWAP: Switched to canister {current_canister}. | " +
                         f"CO₂: {canisters[current_canister]:.0f}J | " +
                         f"Temp: {temperature_c:.2f}°C | Batt: {battery_remaining_wh/battery_capacity_wh*100:.1f}%")
        else:
            # Both canisters are depleted, attempt refill (infinite mode)
            canisters = [cooling_capacity_joules, cooling_capacity_joules] # Refill both
            current_canister = 0 # Reset to canister 0
            canister_swaps += 1 # Count refill as a swap action
            events.append(f"[{seconds:>8.0f}s] CANISTER REFILL: Both canisters low, REFILLED. | " +
                         f"Temp: {temperature_c:.2f}°C | Batt: {battery_remaining_wh/battery_capacity_wh*100:.1f}%")

    # Apply hiss energy usage *after* potential swap/refill
    canisters[current_canister] = max(0, canisters[current_canister] - hiss_energy) # Use hiss_energy (Joules)


    # --- Calculate Net Thermal Change ---
    net_power = current_cpu_power - total_cooling # Net power (Watts)
    delta_temp = (net_power * time_step_s) / thermal_mass_j_per_c
    temperature_c += delta_temp

    # Prevent temperature from dropping below ambient (simplified)
    temperature_c = max(initial_temp_c * 0.8, temperature_c) # Allow slightly below initial ambient

    # <<< OPTIMIZATION: Update peak temperature within the loop >>>
    if temperature_c > peak_temp_c:
        peak_temp_c = temperature_c

    # Log the temperature for plotting
    temperature_log.append(temperature_c)

    # Status report (e.g., every day for the yearly simulation)
    status_interval = 86400 # seconds in a day
    if seconds > 0 and int(seconds) % status_interval < time_step_s :
         events.append(f"[{seconds:>8.0f}s] STATUS: Temp: {temperature_c:.2f}°C | " +
                      f"Peak: {peak_temp_c:.2f}°C | CO₂: {canisters[current_canister]:.0f}J ({current_canister})| " +
                      f"Batt: {battery_remaining_wh/battery_capacity_wh*100:.1f}% | Fan: {fan_duty_cycle:.0f}% ({fan_mode})")

    # Safety break if battery depleted (avoid infinite loops in weird states)
    if battery_remaining_wh <= 0:
        events.append(f"[{seconds:>8.0f}s] CRITICAL: Battery depleted. Simulation HALTED.")
        # Adjust n_steps to stop plotting further points if desired
        n_steps = t + 1
        total_time_s = seconds
        break

# --- Simulation End ---
end_time = time.time()
simulation_runtime = end_time - start_time

# Adjust temperature log length if simulation halted early
if len(temperature_log) > n_steps:
    temperature_log = temperature_log[:n_steps]

# Generate summary
events.append(f"\n=== ULTIMATE THERMAL EDEN SIMULATION SUMMARY ===")
events.append(f"Simulation Runtime: {simulation_runtime:.2f} seconds")
events.append(f"Simulated duration: {total_time_s / 3600:.1f} hours ({total_time_s / 86400:.1f} days)")
events.append(f"Final temperature: {temperature_c:.2f}°C")
# <<< OPTIMIZATION: Use the tracked peak temperature >>>
events.append(f"Peak temperature reached: {peak_temp_c:.2f}°C")
events.append(f"Total CO₂ purges: {purge_count}")
events.append(f"Canister swaps/refills: {canister_swaps}")
events.append(f"Remaining CO₂ (current canister {current_canister}): {canisters[current_canister]:.0f}J")
events.append(f"Total Remaining CO₂: {sum(canisters):.0f}J")
final_battery_percent = max(0, battery_remaining_wh / battery_capacity_wh * 100)
events.append(f"Battery remaining: {max(0, battery_remaining_wh):.1f}Wh ({final_battery_percent:.1f}%)")


# Calculate efficiency statistics
events.append(f"\n=== COOLING CONTRIBUTION ANALYSIS (Joules) ===")
total_cooling_joules = sum(cooling_contribution.values())
if total_cooling_joules > 0:
    # Sort contributions for better readability
    sorted_contributions = sorted(cooling_contribution.items(), key=lambda item: item[1], reverse=True)
    for mechanism, joules in sorted_contributions:
        percentage = (joules / total_cooling_joules) * 100
        events.append(f"- {mechanism:<20}: {joules:,.0f} J ({percentage:.1f}%)")
else:
    events.append("No cooling occurred.")


# Create temperature chart
plt.figure(figsize=(14, 8)) # Wider plot
# Plot time in days for longer simulations
time_axis = np.arange(0, n_steps * time_step_s, time_step_s) / 86400 # Time in days
plt.plot(time_axis, temperature_log, label='CPU Temperature')
plt.axhline(y=critical_temp_c, color='r', linestyle='--', label=f'Critical ({critical_temp_c}°C)')
plt.axhline(y=emergency_temp_c, color='orange', linestyle='--', label=f'Emergency ({emergency_temp_c}°C)')
plt.axhline(y=75, color='y', linestyle=':', label='High (75°C)')
plt.axhline(y=65, color='g', linestyle=':', label='Optimal (65°C)')
plt.xlabel('Time (days)') # Updated label
plt.ylabel('Temperature (°C)')
plt.title('Ultimate Tactical Field Protocol - Thermal Performance (1 Year Simulation)') # Updated title
plt.legend(loc='best')
plt.grid(True, which='both', linestyle='--', linewidth=0.5)
plt.ylim(bottom=initial_temp_c * 0.7) # Adjust y-axis floor
plt.tight_layout()

# If we're directly running this script, display the summary
if __name__ == "__main__":
    print("\n".join(events))
    plt.savefig('thermal_eden_simulation_optimized.png', dpi=150) # Save with higher DPI
    # plt.show() # Optionally disable showing plot if only saving

# Return events for running in other environments
# print("\n".join(events)) # Keep this if needed, or remove if only used for __main__