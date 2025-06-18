"""Monolithic script containing all simulations with a Tkinter GUI"""
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
import threading
import io
from contextlib import redirect_stdout
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
def show_non_blocking(*args, **kwargs):
    kwargs.setdefault("block", False)
    plt.show_original(*args, **kwargs)
plt.show_original = plt.show
plt.show = show_non_blocking
SCRIPTS = {}
SCRIPTS['simulation/laptopcoolingsim.py'] = '''
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
'''
SCRIPTS['simulation/laptopcoolingsim1yearsim.py'] = '''
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
total_time_s = 31536000  # 60 minutes
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
    
    # Adaptive canister swap logic
# Infinite canister refill logic
    if canisters[current_canister] < 50:
        # First check if other canister has enough cooling capacity
        other_canister = 1 - current_canister
        if canisters[other_canister] > 50:
            # Switch to the other canister if it has capacity
            current_canister = other_canister
            canister_swaps += 1
            events.append(f"[{seconds:>4}s] CANISTER SWAP: Switching to canister {current_canister}! | " +
                         f"CO₂ remaining: {canisters[current_canister]:.0f}J | " +
                         f"Temp: {temperature_c:.2f}°C | Battery: {battery_remaining_wh:.1f}Wh")
        else:
            # Both canisters depleted - refill them both for infinite simulation!
            canisters = [cooling_capacity_joules, cooling_capacity_joules]
            canister_swaps += 1
            events.append(f"[{seconds:>4}s] CANISTER REFILL: Both canisters replenished to full capacity! | " +
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
    if seconds % 1440 == 0 and seconds > 0:
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
'''
SCRIPTS['simulation/laptopcoolingsim1yearsim2.py'] = '''
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
'''
SCRIPTS['simulation/laptopcoolingsim1yearsim3.py'] = '''
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
'''
SCRIPTS['simulation/laptopcoolingsim1yearsim4DS.py'] = '''
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
battery_capacity_wh = 8500000000  # massive solar-storage battery
peltier_efficiency_base = 0.6  # max efficiency ratio

# Fan parameters
fan_power_draw = 0.25  # watts
fan_efficiency_multiplier_base = 1.3  # minimum efficiency boost
fan_efficiency_multiplier_max = 2.5  # maximum with ideal conditions
fan_ramp_time = 1.0  # seconds to reach full speed

# Simulation duration
total_time_s = 31536000  # 1 year simulation
time_step_s = 5
n_steps = total_time_s // time_step_s

# Initialize tracking variables
canisters = [cooling_capacity_joules, cooling_capacity_joules]
current_canister = 0
purge_count = 0
canister_swaps = 0
last_purge_time = -9999
temperature_c = initial_temp_c
peak_temp_c = initial_temp_c
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

# Simulate CPU workload variations
def get_cpu_workload(time_s):
    """Simulate varying CPU load patterns"""
    base_load = cpu_power_watts * 0.85

    # Periodic loads every 5 minutes (scaled for year)
    variation = np.sin(time_s / (300 * 60) * np.pi) * 0.15 * cpu_power_watts

    # Intense workloads
    intense_start1 = total_time_s * 0.1
    intense_end1 = intense_start1 + 3600 * 2
    intense_start2 = total_time_s * 0.6
    intense_end2 = intense_start2 + 3600 * 4

    if intense_start1 < time_s < intense_end1 or intense_start2 < time_s < intense_end2:
        return cpu_power_watts * 1.1

    return base_load + variation

def calculate_peltier_efficiency(cpu_temp, hot_side_temp):
    """Calculate Peltier efficiency dynamically"""
    temp_diff = hot_side_temp - cpu_temp
    if temp_diff <= 0:
        return peltier_efficiency_base

    efficiency = peltier_efficiency_base * (1 - (temp_diff / 70)**2)
    if hot_side_temp > 85:
        efficiency *= 0.5

    return max(0.1, min(peltier_efficiency_base, efficiency))

def calculate_fan_multiplier(duty_cycle, is_post_purge=False, purge_timer=0):
    """Calculate cooling efficiency boost from fan"""
    if duty_cycle <= 0:
        return 1.0

    base_mult = 1.0 + (fan_efficiency_multiplier_base - 1.0) * (duty_cycle / 100)
    speed_factor = 1.0 + (duty_cycle / 100) * 0.7
    purge_boost = 1.0

    if is_post_purge:
        decay_factor = max(0, min(1, (conduction_duration - purge_timer) / conduction_duration))
        purge_boost = 1.0 + 0.5 * decay_factor

    return base_mult * speed_factor * purge_boost

def manage_peltier(cpu_temp, battery_level, co2_available, time_since_purge):
    """Control Peltier activation"""
    global peltier_active, peltier_runtime_s

    should_activate = (
        cpu_temp > 70 and
        battery_level > (0.05 * battery_capacity_wh) and
        peltier_runtime_s < peltier_max_runtime and
        hot_side_temp_c < 90
    )

    should_deactivate = (
        cpu_temp < 65 or
        battery_level < (0.03 * battery_capacity_wh) or
        hot_side_temp_c > 95 or
        peltier_runtime_s >= peltier_max_runtime
    )

    post_purge_boost = time_since_purge >= 0 and time_since_purge < 60

    if peltier_active:
        if should_deactivate:
            peltier_active = False
            peltier_runtime_s = 0
    else:
        if (should_activate or post_purge_boost) and battery_level > (0.05 * battery_capacity_wh):
            peltier_active = True
        else:
            peltier_active = False
            peltier_runtime_s = 0

def manage_fan(cpu_temp, is_post_purge, seconds_since_purge):
    """Control fan behavior"""
    global fan_active, fan_duty_cycle, fan_mode

    target_duty = 0
    current_seconds = seconds

    if cpu_temp < 50 and not is_post_purge:
        fan_mode = "PASSIVE"
    elif cpu_temp < 65:
        fan_mode = "SLOW_HISS"
        target_duty = 30 if int(current_seconds) % 15 == 0 else 0
    elif is_post_purge:
        fan_mode = "PURGE"
        target_duty = 80
    elif cpu_temp > 75:
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
    fan_active = fan_duty_cycle > 0

# --- Simulation Start ---
start_time = time.time()

for t in range(n_steps):
    seconds = t * time_step_s
    current_cpu_power = get_cpu_workload(seconds)
    time_since_last_purge = seconds - last_purge_time
    is_post_purge = 0 <= time_since_last_purge <= conduction_duration

    # Update post-purge timer
    post_purge_timer = conduction_duration - time_since_last_purge if is_post_purge else 0

    # --- Cooling Contributions ---
    passive_cooling = passive_dissipation_watts
    conduction_cooling = conduction_watts if is_post_purge else 0

    # Critical Fix 1: Subtract conduction energy from canister
    if is_post_purge:
        conduction_energy = conduction_watts * time_step_s
        canisters[current_canister] = max(0, canisters[current_canister] - conduction_energy)

    # Determine CO2 microburst parameters
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

    burst_now = canisters[current_canister] > 0 and int(cycle_time) > 0 and seconds % int(cycle_time) < time_step_s
    hiss_joules_per_burst = burst_duration * 3.0
    hiss_energy = hiss_joules_per_burst if burst_now else 0
    hiss_cooling = hiss_energy / time_step_s

    manage_peltier(temperature_c, battery_remaining_wh, canisters[current_canister] > 50, time_since_last_purge)

    peltier_cooling = 0
    if peltier_active:
        peltier_efficiency = calculate_peltier_efficiency(temperature_c, hot_side_temp_c)
        peltier_cooling = peltier_max_cooling_watts * peltier_efficiency
        peltier_heat_generated = peltier_power_draw + peltier_cooling
        hot_side_delta_t = (peltier_heat_generated * 0.01 - passive_dissipation_watts * 0.1) * time_step_s
        hot_side_temp_c += hot_side_delta_t
        hot_side_temp_c = max(temperature_c, hot_side_temp_c)
        battery_remaining_wh -= (peltier_power_draw * time_step_s) / 3600
        peltier_runtime_s += time_step_s
    else:
        hot_side_temp_c -= (hot_side_temp_c - temperature_c) * 0.1 * time_step_s
        hot_side_temp_c = max(temperature_c, hot_side_temp_c)

    manage_fan(temperature_c, is_post_purge, time_since_last_purge)
    fan_multiplier = calculate_fan_multiplier(fan_duty_cycle, is_post_purge, post_purge_timer)

    if fan_active:
        battery_remaining_wh -= (fan_power_draw * (fan_duty_cycle/100.0) * time_step_s) / 3600

    # Critical Fix 2: Correct cooling contribution calculation
    base_passive = passive_cooling
    base_conduction = conduction_cooling
    base_hiss = hiss_cooling
    base_peltier = peltier_cooling

    total_base_cooling = base_passive + base_conduction + base_hiss + base_peltier
    total_cooling = total_base_cooling * fan_multiplier

    cooling_contribution["passive"] += base_passive * time_step_s
    cooling_contribution["canister_conduction"] += base_conduction * time_step_s
    cooling_contribution["co2_hiss"] += base_hiss * time_step_s
    cooling_contribution["peltier"] += base_peltier * time_step_s

    fan_boost_joules = (total_cooling - total_base_cooling) * time_step_s
    cooling_contribution["fan_boost"] += fan_boost_joules

    # --- Emergency Purge Logic ---
    needs_purge = temperature_c > critical_temp_c
    maybe_purge = temperature_c > emergency_temp_c and canisters[current_canister] < (cooling_capacity_joules * 0.15)

    if needs_purge or maybe_purge:
        if canisters[current_canister] >= cooling_effective_joules:
            temp_drop = cooldown_per_purge_c * fan_multiplier
            temperature_c -= temp_drop
            canisters[current_canister] -= cooling_effective_joules
            purge_count += 1
            last_purge_time = seconds
            cooling_contribution["co2_purge"] += cooling_effective_joules

    # --- Adaptive Canister Swap Logic ---
    if canisters[current_canister] < 50:
        other_canister = 1 - current_canister
        if canisters[other_canister] > 50:
            current_canister = other_canister
            canister_swaps += 1
        else:
            canisters = [cooling_capacity_joules, cooling_capacity_joules]
            current_canister = 0
            canister_swaps += 1

    canisters[current_canister] = max(0, canisters[current_canister] - hiss_energy)

    # --- Thermal Calculation ---
    net_power = current_cpu_power - total_cooling
    delta_temp = (net_power * time_step_s) / thermal_mass_j_per_c
    temperature_c += delta_temp
    temperature_c = max(initial_temp_c * 0.8, temperature_c)

    if temperature_c > peak_temp_c:
        peak_temp_c = temperature_c

    temperature_log.append(temperature_c)

    if battery_remaining_wh <= 0:
        events.append(f"[{seconds:>8.0f}s] CRITICAL: Battery depleted.")
        n_steps = t + 1
        total_time_s = seconds
        break

# --- Simulation End ---
end_time = time.time()
simulation_runtime = end_time - start_time

# Generate outputs
events.append(f"\n=== SIMULATION SUMMARY ===")
events.append(f"Final temperature: {temperature_c:.2f}°C")
events.append(f"Peak temperature: {peak_temp_c:.2f}°C")
events.append(f"Total CO₂ purges: {purge_count}")
events.append(f"Canister swaps: {canister_swaps}")
events.append(f"Battery remaining: {max(0, battery_remaining_wh):.1f}Wh")

# Calculate cooling contributions
total_cooling_joules = sum(cooling_contribution.values())
if total_cooling_joules > 0:
    sorted_contributions = sorted(cooling_contribution.items(), key=lambda item: item[1], reverse=True)
    for mechanism, joules in sorted_contributions:
        percentage = (joules / total_cooling_joules) * 100
        events.append(f"- {mechanism:<20}: {joules:,.0f} J ({percentage:.1f}%)")

# Plot results
plt.figure(figsize=(14, 8))
time_axis = np.arange(0, n_steps * time_step_s, time_step_s) / 86400
plt.plot(time_axis, temperature_log, label='CPU Temperature')
plt.axhline(y=critical_temp_c, color='r', linestyle='--', label=f'Critical ({critical_temp_c}°C)')
plt.xlabel('Time (days)')
plt.ylabel('Temperature (°C)')
plt.title('Thermal Performance (1 Year Simulation)')
plt.legend()
plt.grid(True)
plt.tight_layout()

if __name__ == "__main__":
    print("\n".join(events))
    plt.savefig('thermal_simulation_corrected.png', dpi=150)
'''
SCRIPTS['simulation/laptopcoolingsim1yearsim4o1-pro.py'] = '''
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

'''
SCRIPTS['simulation/tactical-pi-cooling.py'] = '''
#!/usr/bin/env python3
"""
🧊 ULTIMATE TACTICAL PI COOLING SYSTEM 🧊
-----------------------------------------
Full-spectrum thermal management solution with advanced CO2 dynamics
Implements both microbursts (hiss) and emergency purges with dynamic timing
"""

import time
import os
import signal
import sys
import datetime
import RPi.GPIO as GPIO
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
import json

# ===== HARDWARE CONFIG =====
FAN_PIN = 18
VALVE_PIN = 23
LED_PIN = 24  # Optional status indicator

# ===== COOLING PARAMETERS =====
# Temperature thresholds
TEMP_NOMINAL = 55    # °C - Everything's fine
TEMP_WARNING = 65    # °C - Start getting concerned
TEMP_HIGH = 70       # °C - Definitely hot
TEMP_CRITICAL = 75   # °C - Take serious action
TEMP_EMERGENCY = 80  # °C - Emergency measures

# CO2 parameters (inspired by original simulation)
COOLING_CAPACITY_JOULES = 2900  # per canister
PURGE_EFFICIENCY = 0.85
COOLING_EFFECTIVE_JOULES = COOLING_CAPACITY_JOULES * PURGE_EFFICIENCY
THERMAL_MASS_J_PER_C = 300  # Thermal mass of Pi + case
COOLDOWN_PER_PURGE_C = COOLING_EFFECTIVE_JOULES / THERMAL_MASS_J_PER_C
CONDUCTION_DURATION = 180  # seconds of passive cooling after purge

# Canister tracking
CANISTER_VOLUME_ML = 16  # Typical small CO2 cartridge size
REMAINING_CO2_ML = CANISTER_VOLUME_ML  # Track usage

# Fan parameters
FAN_MIN_EFFECTIVE_TEMP = 45  # Below this temp, fan has little effect
FAN_AFTERRUN = 30  # seconds to run fan after CO2 burst
FAN_EFFICIENCY_BASE = 1.3  # Minimum cooling multiplier
FAN_EFFICIENCY_MAX = 2.5  # Maximum with ideal conditions
FAN_SPEEDS = {
    "OFF": 0,
    "LOW": 25,
    "MEDIUM": 50, 
    "HIGH": 75,
    "MAX": 100
}

# Test parameters
TEST_DURATION = 1800  # 30 minutes total test time
SAMPLE_INTERVAL = 2  # seconds between temperature readings

# ===== TEST PHASES =====
PHASES = {
    "BASELINE": {"duration": 300, "description": "No cooling, establishing baseline"},
    "FAN_ONLY": {"duration": 300, "description": "Using only the fan for cooling"},
    "CO2_FAN": {"duration": 600, "description": "Using CO2 bursts with fan"},
    "ADAPTIVE": {"duration": 300, "description": "Using full adaptive algorithm"},
    "COOLDOWN": {"duration": 300, "description": "System cooldown, passive only"}
}

# ===== DATA STORAGE =====
data = {
    "timestamp": [],
    "temperature": [],
    "cooling_state": [],  # "NONE", "FAN", "HISS", "PURGE"
    "fan_speed": [],     # Percentage value
    "fan_mode": [],      # String description of mode 
    "phase": [],
    "co2_events": [],    # Track when CO2 events happen (hiss/purge)
    "co2_usage_ml": [],  # Track CO2 consumption
    "efficiency": []     # Cooling efficiency
}

# File paths
LOG_DIR = Path("cooling_test_logs")
LOG_DIR.mkdir(exist_ok=True)
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = LOG_DIR / f"cooling_test_{timestamp}.csv"
plot_file = LOG_DIR / f"cooling_test_{timestamp}.png"
json_file = LOG_DIR / f"cooling_test_{timestamp}.json"

# ===== GLOBAL STATE TRACKING =====
last_hiss_time = 0
last_purge_time = 0
fan_duty_cycle = 0
fan_mode = "PASSIVE"
post_purge_timer = 0
co2_total_usage_ml = 0

# ===== SYSTEM FUNCTIONS =====
def setup_gpio():
    """Initialize GPIO pins for controlling cooling hardware"""
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(FAN_PIN, GPIO.OUT)
    GPIO.setup(VALVE_PIN, GPIO.OUT)
    if LED_PIN:
        GPIO.setup(LED_PIN, GPIO.OUT)
    
    # Initialize PWM for fan speed control
    fan_pwm = GPIO.PWM(FAN_PIN, 100)  # 100 Hz PWM frequency
    fan_pwm.start(0)  # Start with 0% duty cycle
    
    # Initialize all pins to OFF
    GPIO.output(VALVE_PIN, False)
    if LED_PIN:
        GPIO.output(LED_PIN, False)
    
    print("🔌 GPIO pins initialized")
    return fan_pwm

def generate_load(intensity=80):
    """Heat this Pi up with CPU load"""
    print(f"🔥 Generating {intensity}% CPU load...")
    
    try:
        cores = max(1, int(os.cpu_count() * intensity / 100))
        os.system(f"stress-ng --cpu {cores} --timeout 3600s &")
        return True
    except Exception as e:
        print(f"Error generating load: {e}")
        return False

def stop_load():
    """Stop the CPU load generation"""
    try:
        os.system("pkill stress-ng")
        print("CPU load generation stopped")
    except:
        print("Note: stress-ng might not have been running")

def cleanup(fan_pwm):
    """Clean up GPIO and system resources"""
    print("\nCleaning up...")
    stop_load()
    fan_pwm.stop()
    GPIO.output(VALVE_PIN, False)
    if LED_PIN:
        GPIO.output(LED_PIN, False)
    GPIO.cleanup()
    print("Cleanup complete")

def signal_handler(sig, frame):
    """Handle exit signals gracefully"""
    print("\nTest interrupted! Cleaning up...")
    global fan_pwm
    cleanup(fan_pwm)
    save_data()
    generate_plot()
    sys.exit(0)

# ===== SENSOR & ACTUATOR FUNCTIONS =====
def get_pi_temp():
    """Get Raspberry Pi CPU temperature in Celsius"""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as temp_file:
            temp = float(temp_file.read()) / 1000.0
        return temp
    except:
        # Fallback to vcgencmd if file access fails
        try:
            output = os.popen("vcgencmd measure_temp").readline()
            return float(output.replace("temp=","").replace("'C\n",""))
        except:
            print("Unable to read temperature!")
            return 0

def calculate_fan_multiplier(duty_cycle, is_post_purge=False, purge_timer=0):
    """Calculate cooling efficiency boost from fan operation
    Args:
        duty_cycle: Fan speed percentage (0-100)
        is_post_purge: Whether we're in post-purge cooling phase
        purge_timer: Seconds since last purge
    Returns:
        Multiplier value for cooling efficiency
    """
    if duty_cycle <= 0:
        return 1.0  # No enhancement
    
    # Base multiplier from breaking boundary layers
    base_mult = 1.0 + (FAN_EFFICIENCY_BASE - 1.0) * (duty_cycle / 100)
    
    # Speed effect
    speed_factor = 1.0 + (duty_cycle / 100) * 0.7
    
    # Post-purge effectiveness boost (Cryo-Assist Chamber effect)
    purge_boost = 1.0
    if is_post_purge:
        # Effect decays over time after purge
        decay_factor = max(0, min(1, purge_timer / CONDUCTION_DURATION))
        purge_boost = 1.0 + 0.5 * decay_factor
    
    return base_mult * speed_factor * purge_boost

def manage_fan(temp, is_post_purge, seconds_since_purge):
    """Control fan behavior based on thermal conditions
    
    Implements the sophisticated fan management algorithm from the 
    original simulation, with multiple operating modes.
    """
    global fan_duty_cycle, fan_mode
    
    # Determine operating mode
    if temp < FAN_MIN_EFFECTIVE_TEMP and not is_post_purge:
        fan_mode = "PASSIVE"
        target_duty = 0
    elif temp < TEMP_WARNING:
        fan_mode = "SLOW_HISS"
        # Pulse the fan occasionally
        if int(time.time()) % 15 == 0:  # Every 15 seconds
            target_duty = 30
        else:
            target_duty = 15
    elif is_post_purge:
        fan_mode = "PURGE_ASSIST"
        target_duty = 80
    elif temp > TEMP_HIGH:
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
    
    return fan_duty_cycle, fan_mode

def set_fan_speed(fan_pwm, speed_pct):
    """Set the fan speed using PWM"""
    fan_pwm.ChangeDutyCycle(speed_pct)
    return speed_pct

def trigger_co2(duration, event_type="HISS"):
    """Trigger CO2 valve for specified duration
    
    Args:
        duration: Time in seconds to open valve
        event_type: "HISS" for microburst or "PURGE" for emergency
    
    Returns:
        Estimated cooling effect in °C
        CO2 usage in ml
    """
    global co2_total_usage_ml, REMAINING_CO2_ML
    
    # Calculate CO2 usage based on duration
    # A full purge (longer duration) uses more CO2 than a short hiss
    if event_type == "PURGE":
        co2_usage = (duration / 1.0) * 2.0  # ml of CO2
    else:
        co2_usage = (duration / 1.0) * 0.5  # ml of CO2
    
    # Check if we have enough CO2 left
    if co2_usage > REMAINING_CO2_ML:
        print(f"⚠️ CO2 canister depleted! Only {REMAINING_CO2_ML:.1f}ml left but need {co2_usage:.1f}ml")
        co2_usage = REMAINING_CO2_ML
        duration = duration * (REMAINING_CO2_ML / co2_usage)
    
    # Track usage
    REMAINING_CO2_ML -= co2_usage
    co2_total_usage_ml += co2_usage
    
    # Calculate cooling effect (simplified)
    if event_type == "PURGE":
        cooling_effect = COOLDOWN_PER_PURGE_C * (co2_usage / 2.0)
    else:
        cooling_effect = COOLDOWN_PER_PURGE_C * (co2_usage / 8.0)
    
    # Indicate with LED if available
    if LED_PIN:
        GPIO.output(LED_PIN, True)
    
    # Trigger valve
    print(f"❄️ {event_type}: {duration:.2f}s burst | CO2 used: {co2_usage:.2f}ml | Effect: {cooling_effect:.2f}°C")
    GPIO.output(VALVE_PIN, True)
    time.sleep(duration)
    GPIO.output(VALVE_PIN, False)
    
    # Turn off LED
    if LED_PIN:
        GPIO.output(LED_PIN, False)
    
    return cooling_effect, co2_usage

def calculate_co2_hiss_parameters(temp):
    """Calculate CO2 microburst parameters based on temperature
    
    Returns:
        burst_duration: Duration of microburst in seconds
        cycle_time: Time between microbursts in seconds
    """
    if temp < 60:
        burst_duration = 0.3
        cycle_time = 8.0
    elif 60 <= temp < 70:
        burst_duration = 0.5
        cycle_time = 5.0
    elif 70 <= temp < 75:
        burst_duration = 0.7
        cycle_time = 4.0
    else:
        burst_duration = 1.0
        cycle_time = 3.0
    
    return burst_duration, cycle_time

def manage_co2_cooling(temp, elapsed_time, fan_multiplier):
    """Advanced CO2 cooling management with hiss and purge capabilities
    
    Implements the full tactical cooling logic from the original simulation,
    with microbursts (hiss) and emergency purges.
    
    Args:
        temp: Current temperature in °C
        elapsed_time: Current test time in seconds
        fan_multiplier: Current fan efficiency multiplier
    
    Returns:
        Dict with cooling info: type, effect, usage
    """
    global last_hiss_time, last_purge_time, post_purge_timer
    
    # Track time since last events
    time_since_last_hiss = elapsed_time - last_hiss_time
    time_since_last_purge = elapsed_time - last_purge_time
    is_post_purge = 0 <= time_since_last_purge <= CONDUCTION_DURATION
    
    # Update post-purge timer for fan control
    if is_post_purge:
        post_purge_timer = CONDUCTION_DURATION - time_since_last_purge
    else:
        post_purge_timer = 0
    
    # Initialize result
    cooling_result = {
        "type": "NONE",
        "effect": 0,
        "usage": 0
    }
    
    # Emergency purge logic - highest priority
    if temp > TEMP_EMERGENCY or temp > TEMP_CRITICAL and time_since_last_purge > 120:
        # Perform emergency purge
        effect, usage = trigger_co2(1.5, "PURGE")
        last_purge_time = elapsed_time
        cooling_result = {
            "type": "PURGE",
            "effect": effect * fan_multiplier,  # Fan enhances cooling
            "usage": usage
        }
        return cooling_result
    
    # CO2 microburst (hiss) logic
    burst_duration, cycle_time = calculate_co2_hiss_parameters(temp)
    
    # Only do microburst if temperature is above warning threshold and
    # it's been long enough since last hiss
    if temp > TEMP_WARNING and time_since_last_hiss >= cycle_time:
        effect, usage = trigger_co2(burst_duration, "HISS")
        last_hiss_time = elapsed_time
        cooling_result = {
            "type": "HISS",
            "effect": effect * fan_multiplier,  # Fan enhances cooling
            "usage": usage
        }
    
    return cooling_result

# ===== DATA HANDLING =====
def save_data():
    """Save collected data to CSV and JSON"""
    print(f"💾 Saving data to {log_file}...")
    
    # Save to CSV
    with open(log_file, "w") as f:
        f.write("timestamp,temperature,cooling_state,fan_speed,fan_mode,phase,co2_usage_ml,efficiency\n")
        for i in range(len(data["timestamp"])):
            f.write(f"{data['timestamp'][i]},{data['temperature'][i]:.2f}," +
                   f"{data['cooling_state'][i]},{data['fan_speed'][i]},{data['fan_mode'][i]}," +
                   f"{data['phase'][i]},{data['co2_usage_ml'][i]:.2f},{data['efficiency'][i]:.2f}\n")
    
    # Save to JSON for easier parsing/analysis
    with open(json_file, "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"Data saved to {log_file} and {json_file}")

def generate_plot():
    """Generate detailed temperature and cooling visualization plot"""
    print(f"📊 Generating temperature plot...")
    
    # Setup figure with two subplots - temp on top, cooling events below
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [3, 1]})
    
    # Plot temperature on the top subplot
    ax1.plot(data["timestamp"], data["temperature"], 'b-', linewidth=2, label='Temperature (°C)')
    
    # Highlight different test phases with background colors
    phase_changes = []
    current_phase = None
    for i, phase in enumerate(data["phase"]):
        if phase != current_phase:
            phase_changes.append((i, phase))
            current_phase = phase
    
    # Phase background colors
    colors = {
        "BASELINE": "lightgray", 
        "FAN_ONLY": "lightgreen", 
        "CO2_FAN": "lightblue", 
        "ADAPTIVE": "lavender",
        "COOLDOWN": "thistle"
    }
    
    # Add phase backgrounds
    for i in range(len(phase_changes)):
        start_idx = phase_changes[i][0]
        end_idx = len(data["timestamp"]) if i == len(phase_changes) - 1 else phase_changes[i + 1][0]
        phase_name = phase_changes[i][1]
        start_time = data["timestamp"][start_idx]
        
        # Handle edge case for last data point
        if end_idx >= len(data["timestamp"]):
            end_time = data["timestamp"][-1]
        else:
            end_time = data["timestamp"][end_idx-1]
            
        ax1.axvspan(start_time, end_time, 
                   alpha=0.3, color=colors.get(phase_name, "gray"), label=f"{phase_name}")
    
    # Add threshold lines
    ax1.axhline(y=TEMP_NOMINAL, color='green', linestyle='--', alpha=0.7, label=f'Nominal ({TEMP_NOMINAL}°C)')
    ax1.axhline(y=TEMP_WARNING, color='gold', linestyle='--', alpha=0.7, label=f'Warning ({TEMP_WARNING}°C)')
    ax1.axhline(y=TEMP_HIGH, color='orange', linestyle='--', alpha=0.7, label=f'High ({TEMP_HIGH}°C)')
    ax1.axhline(y=TEMP_CRITICAL, color='red', linestyle='--', alpha=0.7, label=f'Critical ({TEMP_CRITICAL}°C)')
    ax1.axhline(y=TEMP_EMERGENCY, color='darkred', linestyle='--', alpha=0.7, label=f'Emergency ({TEMP_EMERGENCY}°C)')
    
    # Mark CO2 events on bottom subplot
    hiss_times = []
    purge_times = []
    
    for i, state in enumerate(data["cooling_state"]):
        if state == "HISS":
            hiss_times.append(data["timestamp"][i])
        elif state == "PURGE":
            purge_times.append(data["timestamp"][i])
    
    # Plot CO2 events
    if hiss_times:
        ax2.scatter(hiss_times, [0.3] * len(hiss_times), marker='o', color='cyan', s=50, label='Hiss')
    if purge_times:
        ax2.scatter(purge_times, [0.7] * len(purge_times), marker='*', color='blue', s=150, label='Purge')
    
    # Plot fan duty cycle on bottom subplot
    ax2.plot(data["timestamp"], [x/100 for x in data["fan_speed"]], 'g-', label='Fan Speed')
    
    # Plot cooling efficiency
    ax2.plot(data["timestamp"], [min(1, x/3) for x in data["efficiency"]], 'r-', alpha=0.7, label='Cooling Efficiency')
    
    # Customize bottom subplot
    ax2.set_ylim(0, 1)
    ax2.set_yticks([0, 0.3, 0.7, 1])
    ax2.set_yticklabels(['0%', 'Hiss', 'Purge', '100%'])
    ax2.grid(True, alpha=0.3)
    ax2.set_xlabel('Time (seconds)')
    ax2.set_ylabel('Events / Fan')
    
    # Customize top subplot
    ax1.set_ylabel('Temperature (°C)')
    ax1.set_title('Raspberry Pi Ultimate Tactical Cooling System Test Results')
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='upper right')
    
    # Avoid duplicate labels in legend
    handles, labels = ax2.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax2.legend(by_label.values(), by_label.keys(), loc='upper right')
    
    # Layout
    plt.tight_layout()
    
    # Save plot
    plt.savefig(plot_file, dpi=150)
    print(f"Plot saved to {plot_file}")
    
    # Display statistics
    avg_temp = np.mean(data["temperature"])
    max_temp = np.max(data["temperature"])
    min_temp = np.min(data["temperature"])
    
    # Count cooling events
    hiss_count = sum(1 for s in data["cooling_state"] if s == "HISS")
    purge_count = sum(1 for s in data["cooling_state"] if s == "PURGE")
    
    # Calculate total CO2 used
    total_co2_used = co2_total_usage_ml
    
    print("\n===== TEST RESULTS =====")
    print(f"Average Temperature: {avg_temp:.2f}°C")
    print(f"Maximum Temperature: {max_temp:.2f}°C")
    print(f"Minimum Temperature: {min_temp:.2f}°C")
    print(f"Total CO2 Microbursts (Hiss): {hiss_count}")
    print(f"Total CO2 Emergency Purges: {purge_count}")
    print(f"Total CO2 Used: {total_co2_used:.2f}ml of {CANISTER_VOLUME_ML}ml ({(total_co2_used/CANISTER_VOLUME_ML*100):.1f}%)")
    
    # Phase-by-phase analysis
    for phase in set(data["phase"]):
        phase_indices = [i for i, p in enumerate(data["phase"]) if p == phase]
        if phase_indices:
            phase_temps = [data["temperature"][i] for i in phase_indices]
            phase_co2 = sum(data["co2_usage_ml"][i] for i in phase_indices)
            phase_hiss = sum(1 for i in phase_indices if data["cooling_state"][i] == "HISS")
            phase_purge = sum(1 for i in phase_indices if data["cooling_state"][i] == "PURGE")
            
            print(f"\n{phase} Phase:")
            print(f"  Average Temp: {np.mean(phase_temps):.2f}°C")
            print(f"  Max Temp: {np.max(phase_temps):.2f}°C")
            print(f"  Temperature Change: {phase_temps[-1] - phase_temps[0]:.2f}°C")
            print(f"  CO2 Used: {phase_co2:.2f}ml ({phase_hiss} hiss, {phase_purge} purge)")

# ===== MAIN TEST FUNCTION =====
def run_test():
    """Run the complete cooling system test with all phases"""
    print("🚀 Starting Raspberry Pi ULTIMATE TACTICAL Cooling System Test 🚀")
    print(f"Test duration: {TEST_DURATION//60} minutes")
    print(f"Sampling interval: {SAMPLE_INTERVAL} seconds")
    print(f"CO2 canister capacity: {CANISTER_VOLUME_ML}ml")
    
    # Setup GPIO
    global fan_pwm
    fan_pwm = setup_gpio()
    
    # Generate load for testing
    load_running = generate_load(intensity=80)
    if not load_running:
        print("Warning: Could not generate CPU load, temperature might not rise as expected")
    
    # Track timing
    start_time = time.time()
    elapsed_seconds = 0
    current_phase = "BASELINE"
    phase_start_time = start_time
    
    try:
        # Main test loop
        while elapsed_seconds < TEST_DURATION:
            current_time = time.time()
            elapsed_seconds = int(current_time - start_time)
            
            # Check if we need to change phases
            phase_elapsed = int(current_time - phase_start_time)
            if phase_elapsed >= PHASES[current_phase]["duration"]:
                # Move to next phase
                phase_list = list(PHASES.keys())
                current_idx = phase_list.index(current_phase)
                if current_idx < len(phase_list) - 1:
                    current_phase = phase_list[current_idx + 1]
                    phase_start_time = current_time
                    print(f"\n==== Entering {current_phase} Phase ====")
                    print(PHASES[current_phase]["description"])
                    
                    # Reset cooling states at phase change
                    if current_phase == "BASELINE" or current_phase == "COOLDOWN":
                        set_fan_speed(fan_pwm, 0)
            
            # Get current temperature
            temp = get_pi_temp()
            
            # Track time since last purge
            time_since_last_purge = elapsed_seconds - last_purge_time
            is_post_purge = 0 <= time_since_last_purge <= CONDUCTION_DURATION
            
            # Determine cooling actions based on phase
            cooling_state = "NONE"
            co2_usage = 0
            cooling_effect = 0
            
            # Update fan speed and mode based on temperature and phase
            if current_phase == "BASELINE" or current_phase == "COOLDOWN":
                # No active cooling in these phases
                fan_duty_cycle = 0
                fan_mode = "OFF"
                set_fan_speed(fan_pwm, 0)
                
            else:
                # Active fan management in other phases
                fan_duty, fan_current_mode = manage_fan(temp, is_post_purge, time_since_last_purge)
                set_fan_speed(fan_pwm, fan_duty)
            
            # Calculate fan multiplier effect on cooling
            fan_multiplier = calculate_fan_multiplier(fan_duty_cycle, is_post_purge, post_purge_timer)
            
            # CO2 cooling logic based on phase
            if current_phase == "FAN_ONLY":
                # Only use fan in this phase
                cooling_state = "FAN" if fan_duty_cycle > 0 else "NONE"
                
            elif current_phase == "CO2_FAN" or current_phase == "ADAPTIVE":
                # Use CO2 cooling in these phases
                cooling_result = manage_co2_cooling(temp, elapsed_seconds, fan_multiplier)
                cooling_state = cooling_result["type"]
                cooling_effect = cooling_result["effect"]
                co2_usage = cooling_result["usage"]
                
                # If CO2 was used, set fan to assist
                if cooling_state in ["HISS", "PURGE"]:
                    # Make sure fan is running if CO2 was used
                    if fan_duty_cycle < 50:
                        set_fan_speed(fan_pwm, 75)
                        fan_duty_cycle = 75
                        fan_mode = "CO2_ASSIST"
            
            # Record the data
            data["timestamp"].append(elapsed_seconds)
            data["temperature"].append(temp)
            data["cooling_state"].append(cooling_state)
            data["fan_speed"].append(fan_duty_cycle)
            data["fan_mode"].append(fan_mode)
            data["phase"].append(current_phase)
            data["co2_usage_ml"].append(co2_usage)
            data["efficiency"].append(fan_multiplier)
            
            # Print status
            co2_left_pct = int((REMAINING_CO2_ML / CANISTER_VOLUME_ML) * 100)
            co2_bar = "█" * (co2_left_pct // 10) + "░" * (10 - (co2_left_pct // 10))
            
            print(f"[{elapsed_seconds:4d}s] Phase: {current_phase:8s} | " +
                  f"Temp: {temp:5.2f}°C | Fan: {fan_duty_cycle:3d}% | " +
                  f"Mode: {fan_mode:10s} | CO2: {co2_bar} {co2_left_pct:3d}%", end="\r")
            
            # Wait until next sample time
            time.sleep(SAMPLE_INTERVAL)
        
        print("\n✅ Test completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Error during test: {e}")
    finally:
        cleanup(fan_pwm)
        save_data()
        generate_plot()

if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run the test
    fan_pwm = None  # initialize global variable
    run_test()

'''
SCRIPTS['simulation/tactical_cooling_sim.py'] = '''
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

'''
def run_script(name, console):
    console.delete("1.0", tk.END)
    code = SCRIPTS[name]
    buf = io.StringIO()
    with redirect_stdout(buf):
        namespace = {"__name__": "__main__"}
        try:
            exec(code, namespace)
        except Exception as e:
            print(e)
    console.insert(tk.END, buf.getvalue())
def main():
    root = tk.Tk()
    root.title("Cooling Simulations")
    options = list(SCRIPTS.keys())
    var = tk.StringVar(value=options[0])
    tk.OptionMenu(root, var, *options).pack()
    console = ScrolledText(root, width=100, height=30)
    console.pack()
    def runner():
        run_script(var.get(), console)
    tk.Button(root, text="Run", command=lambda: threading.Thread(target=runner).start()).pack()
    root.mainloop()

if __name__ == "__main__":
    main()
