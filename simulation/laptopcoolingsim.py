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
battery_capacity_wh = 60  # typical laptop battery
peltier_efficiency_base = 0.6  # max efficiency ratio

# Fan parameters
fan_power_draw = 0.25  # watts
fan_efficiency_multiplier_base = 1.3  # minimum efficiency boost
fan_efficiency_multiplier_max = 2.5  # maximum with ideal conditions
fan_ramp_time = 1.0  # seconds to reach full speed

# Simulation duration
total_time_s = 3600  # 60 minutes
time_step_s = 5
n_steps = total_time_s // time_step_s

# Initialize tracking variables
canisters = [cooling_capacity_joules, cooling_capacity_joules]
current_canister = 0
purge_count = 0
canister_swaps = 0
last_purge_time = -9999
temperature_c = initial_temp_c
events = []
temperature_log = []

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
    
    # Add some variation - periodic loads every 5 minutes
    variation = np.sin(time_s / 300 * np.pi) * 0.15 * cpu_power_watts
    
    # Add two intense workloads during the simulation
    if 900 < time_s < 1100 or 2400 < time_s < 2700:
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
        decay_factor = max(0, min(1, purge_timer / conduction_duration))
        purge_boost = 1.0 + 0.5 * decay_factor
    
    return base_mult * speed_factor * purge_boost

def manage_peltier(cpu_temp, battery_level, co2_available, time_since_purge):
    """Determine if Peltier should be active based on conditions"""
    global peltier_active, peltier_runtime_s

    # Conditions to activate
    should_activate = (
        cpu_temp > 70 and  # Only when needed
        battery_level > 5 and  # Preserve battery
        peltier_runtime_s < peltier_max_runtime and  # Prevent overheating
        hot_side_temp_c < 90  # Prevent TEC damage
    )
    
    # Conditions for deactivation
    should_deactivate = (
        cpu_temp < 65 or  # Cool enough
        battery_level < 3 or  # Critical battery
        hot_side_temp_c > 95 or  # Overheating risk
        peltier_runtime_s >= peltier_max_runtime  # Runtime limit
    )
    
    # Special case - activate after purge for bonus cooling
    post_purge_boost = time_since_purge > 0 and time_since_purge < 60
    
    if should_activate or post_purge_boost:
        peltier_active = True
    elif should_deactivate:
        peltier_active = False
        peltier_runtime_s = 0

def manage_fan(cpu_temp, is_post_purge, seconds_since_purge):
    """Control fan behavior based on thermal conditions"""
    global fan_active, fan_duty_cycle, fan_mode
    
    # Determine operating mode
    if cpu_temp < 50 and not is_post_purge:
        fan_mode = "PASSIVE"
        target_duty = 0
    elif cpu_temp < 65:
        fan_mode = "SLOW_HISS"
        # Pulse the fan occasionally
        if seconds % 15 == 0:  # Every 15 seconds
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
    
    # Smooth ramping for fan speed
    if target_duty > fan_duty_cycle:
        fan_duty_cycle = min(target_duty, fan_duty_cycle + 10)
    elif target_duty < fan_duty_cycle:
        fan_duty_cycle = max(target_duty, fan_duty_cycle - 5)
    
    fan_active = fan_duty_cycle > 0

# Begin simulation
for t in range(n_steps):
    seconds = t * time_step_s
    
    # Get dynamic CPU power based on workload
    current_cpu_power = get_cpu_workload(seconds)
    
    # Track time since last purge
    time_since_last_purge = seconds - last_purge_time
    is_post_purge = 0 <= time_since_last_purge <= conduction_duration
    
    # Update post-purge timer for fan control
    if is_post_purge:
        post_purge_timer = conduction_duration - time_since_last_purge
    else:
        post_purge_timer = 0
    
    # Determine cooling contributions
    
    # 1. Passive shell cooling
    passive_cooling = passive_dissipation_watts
    cooling_contribution["passive"] += passive_cooling * time_step_s
    
    # 2. Canister conduction cooling (after purge)
    conduction_cooling = conduction_watts if is_post_purge else 0
    cooling_contribution["canister_conduction"] += conduction_cooling * time_step_s
    
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
    else:
        burst_duration = 1.0
        cycle_time = 3.0
    
    # Apply CO2 microburst if timing aligns and we have CO2
    burst_now = (seconds % int(cycle_time) == 0)
    hiss_energy = burst_duration * 3.0 if burst_now and canisters[current_canister] > 0 else 0
    hiss_cooling = hiss_energy / time_step_s
    cooling_contribution["co2_hiss"] += hiss_energy
    
    # 4. Manage Peltier device
    manage_peltier(temperature_c, battery_remaining_wh, canisters[current_canister] > 50, time_since_last_purge)
    
    # Apply Peltier cooling if active
    peltier_cooling = 0
    if peltier_active:
        # Calculate efficiency based on temperature differential
        peltier_efficiency = calculate_peltier_efficiency(temperature_c, hot_side_temp_c)
        
        # Calculate cooling power
        peltier_cooling = peltier_max_cooling_watts * peltier_efficiency
        
        # Update hot side temperature (simplified)
        hot_side_temp_c += (peltier_power_draw * (1 - peltier_efficiency) * time_step_s) / thermal_mass_j_per_c
        hot_side_temp_c -= passive_dissipation_watts * 0.5 * time_step_s / thermal_mass_j_per_c
        
        # Track power consumption
        battery_remaining_wh -= (peltier_power_draw * time_step_s) / 3600
        peltier_runtime_s += time_step_s
        
        cooling_contribution["peltier"] += peltier_cooling * time_step_s
    else:
        # Hot side cools down when Peltier is off
        hot_side_temp_c = max(temperature_c, hot_side_temp_c - 0.5)
        peltier_runtime_s = max(0, peltier_runtime_s - time_step_s)  # Recovery
    
    # 5. Manage and apply fan effects
    manage_fan(temperature_c, is_post_purge, time_since_last_purge)
    
    # Calculate fan efficiency multiplier
    fan_multiplier = calculate_fan_multiplier(fan_duty_cycle, is_post_purge, post_purge_timer)
    
    # Fan power consumption
    if fan_active:
        battery_remaining_wh -= (fan_power_draw * (fan_duty_cycle/100) * time_step_s) / 3600
    
    # Apply fan boost to all cooling mechanisms
    enhanced_passive = passive_cooling * fan_multiplier
    enhanced_conduction = conduction_cooling * fan_multiplier
    enhanced_hiss = hiss_cooling * fan_multiplier
    enhanced_peltier = peltier_cooling * fan_multiplier
    
    # Track fan contribution to cooling
    fan_boost = (enhanced_passive + enhanced_conduction + enhanced_hiss + enhanced_peltier) - \
                (passive_cooling + conduction_cooling + hiss_cooling + peltier_cooling)
    cooling_contribution["fan_boost"] += fan_boost * time_step_s
    
    # Total cooling with fan enhancement
    total_cooling = enhanced_passive + enhanced_conduction + enhanced_hiss + enhanced_peltier
    
    # Emergency purge logic
    if (canisters[current_canister] < (cooling_capacity_joules * 0.10) and temperature_c > emergency_temp_c) or \
       temperature_c > 85:
        if canisters[current_canister] >= cooling_effective_joules:
            # Perform purge
            temp_drop = cooldown_per_purge_c * fan_multiplier  # Fan enhances purge effectiveness
            temperature_c -= temp_drop
            canisters[current_canister] -= cooling_effective_joules
            purge_count += 1
            last_purge_time = seconds
            cooling_contribution["co2_purge"] += cooling_effective_joules
            
            # Log the event
            events.append(f"[{seconds:>4}s] EMERGENCY PURGE: Temp → {temperature_c:.2f}°C | " +
                          f"CO₂ Left: {canisters[current_canister]:.0f}J | Fan: {fan_duty_cycle}% | " +
                          f"Battery: {battery_remaining_wh:.1f}Wh")
    
    # Canister swap logic
    if canisters[current_canister] < 50 and current_canister == 0:
        current_canister = 1
        canister_swaps += 1
        events.append(f"[{seconds:>4}s] CANISTER SWAP: Fresh CO₂ source loaded! | " +
                      f"Temp: {temperature_c:.2f}°C | Battery: {battery_remaining_wh:.1f}Wh")
    
    # Apply hiss usage to current canister
    canisters[current_canister] = max(0, canisters[current_canister] - hiss_energy)
    
    # Calculate net thermal change
    net_power = current_cpu_power - total_cooling
    delta_temp = (net_power * time_step_s) / thermal_mass_j_per_c
    temperature_c += delta_temp
    
    # Log the temperature for plotting
    temperature_log.append(temperature_c)
    
    # Status report every 5 minutes
    if seconds % 300 == 0 and seconds > 0:
        events.append(f"[{seconds:>4}s] STATUS: Temp: {temperature_c:.2f}°C | " +
                      f"CO₂: {canisters[current_canister]:.0f}J | " +
                      f"Battery: {battery_remaining_wh:.1f}Wh | " +
                      f"Mode: {fan_mode}")

# Generate summary
events.append(f"\n=== ULTIMATE THERMAL EDEN SIMULATION SUMMARY ===")
events.append(f"Mission duration: {total_time_s//60} minutes")
events.append(f"Final temperature: {temperature_c:.2f}°C")
events.append(f"Peak temperature: {max(temperature_log):.2f}°C")
events.append(f"Total CO₂ purges: {purge_count}")
events.append(f"Canister swaps: {canister_swaps}")
events.append(f"Remaining CO₂: {sum(canisters):.0f}J")
events.append(f"Battery remaining: {battery_remaining_wh:.1f}Wh ({battery_remaining_wh/battery_capacity_wh*100:.1f}%)")

# Calculate efficiency statistics
events.append(f"\n=== COOLING CONTRIBUTION ANALYSIS ===")
total_cooling = sum(cooling_contribution.values())
for mechanism, joules in cooling_contribution.items():
    percentage = (joules / total_cooling) * 100 if total_cooling > 0 else 0
    events.append(f"{mechanism}: {joules:.0f}J ({percentage:.1f}%)")

# Create temperature chart
plt.figure(figsize=(12, 8))
plt.plot(np.arange(0, total_time_s, time_step_s) / 60, temperature_log)
plt.axhline(y=critical_temp_c, color='r', linestyle='--', label=f'Critical ({critical_temp_c}°C)')
plt.axhline(y=emergency_temp_c, color='orange', linestyle='--', label=f'Emergency ({emergency_temp_c}°C)')
plt.axhline(y=75, color='y', linestyle='--', label='High (75°C)')
plt.axhline(y=65, color='g', linestyle='--', label='Optimal (65°C)')
plt.xlabel('Time (minutes)')
plt.ylabel('Temperature (°C)')
plt.title('Ultimate Tactical Field Protocol - Thermal Performance')
plt.legend()
plt.grid(True)
plt.tight_layout()

# If we're directly running this script, display the summary
if __name__ == "__main__":
    print("\n".join(events))
    plt.savefig('thermal_eden_simulation.png')
    plt.show()

# Return events for running in other environments
"\n".join(events)