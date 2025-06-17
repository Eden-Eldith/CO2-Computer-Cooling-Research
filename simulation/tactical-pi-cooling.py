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
