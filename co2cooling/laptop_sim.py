"""Laptop cooling simulation module."""

from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt

# Base system parameters
CPU_POWER_WATTS = 18.5
PASSIVE_DISSIPATION_WATTS = 1.5
THERMAL_MASS_J_PER_C = 300
INITIAL_TEMP_C = 25
CRITICAL_TEMP_C = 90
EMERGENCY_TEMP_C = 78

# CO2 canister parameters
COOLING_CAPACITY_JOULES = 2900
PURGE_EFFICIENCY = 0.85
COOLING_EFFECTIVE_JOULES = COOLING_CAPACITY_JOULES * PURGE_EFFICIENCY
COOLDOWN_PER_PURGE_C = COOLING_EFFECTIVE_JOULES / THERMAL_MASS_J_PER_C
CONDUCTION_WATTS = 2.2
CONDUCTION_DURATION = 180

# Peltier (TEC) parameters
PELTIER_MAX_COOLING_WATTS = 15
PELTIER_POWER_DRAW = 30
PELTIER_MAX_RUNTIME = 120
BATTERY_CAPACITY_WH = 60
PELTIER_EFFICIENCY_BASE = 0.6

# Fan parameters
FAN_POWER_DRAW = 0.25
FAN_EFFICIENCY_MULTIPLIER_BASE = 1.3
FAN_EFFICIENCY_MULTIPLIER_MAX = 2.5

# Simulation defaults
TOTAL_TIME_S = 3600
TIME_STEP_S = 5


def get_cpu_workload(time_s: float) -> float:
    """Return a simulated CPU workload in watts."""
    base_load = CPU_POWER_WATTS * 0.85
    variation = np.sin(time_s / 300 * np.pi) * 0.15 * CPU_POWER_WATTS
    if 900 < time_s < 1100 or 2400 < time_s < 2700:
        return CPU_POWER_WATTS * 1.1
    return base_load + variation


def calculate_peltier_efficiency(cpu_temp: float, hot_side_temp: float) -> float:
    """Calculate Peltier efficiency based on temperature differential."""
    temp_diff = hot_side_temp - cpu_temp
    if temp_diff <= 0:
        return PELTIER_EFFICIENCY_BASE
    efficiency = PELTIER_EFFICIENCY_BASE * (1 - (temp_diff / 70) ** 2)
    if hot_side_temp > 85:
        efficiency *= 0.5
    return max(0.1, min(PELTIER_EFFICIENCY_BASE, efficiency))


def calculate_fan_multiplier(duty_cycle: float, is_post_purge: bool = False, purge_timer: float = 0) -> float:
    """Return efficiency multiplier provided by fan operation."""
    if duty_cycle <= 0:
        return 1.0
    base_mult = 1.0 + (FAN_EFFICIENCY_MULTIPLIER_BASE - 1.0) * (duty_cycle / 100)
    speed_factor = 1.0 + (duty_cycle / 100) * 0.7
    purge_boost = 1.0
    if is_post_purge:
        decay_factor = max(0, min(1, purge_timer / CONDUCTION_DURATION))
        purge_boost = 1.0 + 0.5 * decay_factor
    return base_mult * speed_factor * purge_boost


def run_simulation(total_time_s: int = TOTAL_TIME_S, time_step_s: int = TIME_STEP_S):
    """Run the cooling simulation and return a log of events and temperatures."""
    n_steps = total_time_s // time_step_s
    canisters = [COOLING_CAPACITY_JOULES, COOLING_CAPACITY_JOULES]
    current_canister = 0
    purge_count = 0
    canister_swaps = 0
    last_purge_time = -9999
    temperature_c = INITIAL_TEMP_C
    events = []
    temperature_log = []
    peltier_active = False
    peltier_runtime_s = 0
    battery_remaining_wh = BATTERY_CAPACITY_WH
    hot_side_temp_c = INITIAL_TEMP_C
    fan_active = False
    fan_duty_cycle = 0
    fan_mode = "PASSIVE"
    post_purge_timer = 0
    cooling_contribution = {
        "passive": 0,
        "co2_hiss": 0,
        "co2_purge": 0,
        "canister_conduction": 0,
        "peltier": 0,
        "fan_boost": 0,
    }

    for t in range(n_steps):
        seconds = t * time_step_s
        current_cpu_power = get_cpu_workload(seconds)
        time_since_last_purge = seconds - last_purge_time
        is_post_purge = 0 <= time_since_last_purge <= CONDUCTION_DURATION
        post_purge_timer = CONDUCTION_DURATION - time_since_last_purge if is_post_purge else 0
        passive_cooling = PASSIVE_DISSIPATION_WATTS
        cooling_contribution["passive"] += passive_cooling * time_step_s
        conduction_cooling = CONDUCTION_WATTS if is_post_purge else 0
        cooling_contribution["canister_conduction"] += conduction_cooling * time_step_s

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
        burst_now = seconds % int(cycle_time) == 0
        hiss_energy = burst_duration * 3.0 if burst_now and canisters[current_canister] > 0 else 0
        hiss_cooling = hiss_energy / time_step_s
        cooling_contribution["co2_hiss"] += hiss_energy

        should_activate = (
            temperature_c > 70
            and battery_remaining_wh > 5
            and peltier_runtime_s < PELTIER_MAX_RUNTIME
            and hot_side_temp_c < 90
        )
        should_deactivate = (
            temperature_c < 65
            or battery_remaining_wh < 3
            or hot_side_temp_c > 95
            or peltier_runtime_s >= PELTIER_MAX_RUNTIME
        )
        post_purge_boost = 0 < time_since_last_purge < 60
        if should_activate or post_purge_boost:
            peltier_active = True
        elif should_deactivate:
            peltier_active = False
            peltier_runtime_s = 0

        peltier_cooling = 0
        if peltier_active:
            peltier_efficiency = calculate_peltier_efficiency(temperature_c, hot_side_temp_c)
            peltier_cooling = PELTIER_MAX_COOLING_WATTS * peltier_efficiency
            hot_side_temp_c += (
                PELTIER_POWER_DRAW * (1 - peltier_efficiency) * time_step_s
            ) / THERMAL_MASS_J_PER_C
            hot_side_temp_c -= PASSIVE_DISSIPATION_WATTS * 0.5 * time_step_s / THERMAL_MASS_J_PER_C
            battery_remaining_wh -= (PELTIER_POWER_DRAW * time_step_s) / 3600
            peltier_runtime_s += time_step_s
            cooling_contribution["peltier"] += peltier_cooling * time_step_s
        else:
            hot_side_temp_c = max(temperature_c, hot_side_temp_c - 0.5)
            peltier_runtime_s = max(0, peltier_runtime_s - time_step_s)

        if temperature_c < 50 and not is_post_purge:
            fan_mode = "PASSIVE"
            target_duty = 0
        elif temperature_c < 65:
            fan_mode = "SLOW_HISS"
            target_duty = 30 if seconds % 15 == 0 else 0
        elif is_post_purge:
            fan_mode = "PURGE"
            target_duty = 80
        elif temperature_c > 75:
            fan_mode = "EMERGENCY"
            target_duty = 100
        else:
            fan_mode = "NORMAL"
            target_duty = 50
        if target_duty > fan_duty_cycle:
            fan_duty_cycle = min(target_duty, fan_duty_cycle + 10)
        elif target_duty < fan_duty_cycle:
            fan_duty_cycle = max(target_duty, fan_duty_cycle - 5)
        fan_active = fan_duty_cycle > 0

        fan_multiplier = calculate_fan_multiplier(fan_duty_cycle, is_post_purge, post_purge_timer)
        if fan_active:
            battery_remaining_wh -= (FAN_POWER_DRAW * (fan_duty_cycle / 100) * time_step_s) / 3600
        enhanced_passive = passive_cooling * fan_multiplier
        enhanced_conduction = conduction_cooling * fan_multiplier
        enhanced_hiss = hiss_cooling * fan_multiplier
        enhanced_peltier = peltier_cooling * fan_multiplier
        fan_boost = (
            enhanced_passive
            + enhanced_conduction
            + enhanced_hiss
            + enhanced_peltier
            - (passive_cooling + conduction_cooling + hiss_cooling + peltier_cooling)
        )
        cooling_contribution["fan_boost"] += fan_boost * time_step_s
        total_cooling = enhanced_passive + enhanced_conduction + enhanced_hiss + enhanced_peltier

        if (
            canisters[current_canister] < (COOLING_CAPACITY_JOULES * 0.10)
            and temperature_c > EMERGENCY_TEMP_C
        ) or temperature_c > 85:
            if canisters[current_canister] >= COOLING_EFFECTIVE_JOULES:
                temperature_c -= COOLDOWN_PER_PURGE_C * fan_multiplier
                canisters[current_canister] -= COOLING_EFFECTIVE_JOULES
                purge_count += 1
                last_purge_time = seconds
                cooling_contribution["co2_purge"] += COOLING_EFFECTIVE_JOULES
                events.append(
                    f"[{seconds:>4}s] EMERGENCY PURGE: Temp → {temperature_c:.2f}°C | CO₂ Left: {canisters[current_canister]:.0f}J | Fan:{fan_duty_cycle}% | Battery: {battery_remaining_wh:.1f}Wh"
                )

        if canisters[current_canister] < 50 and current_canister == 0:
            current_canister = 1
            canister_swaps += 1
            events.append(
                f"[{seconds:>4}s] CANISTER SWAP: Fresh CO₂ source loaded! | Temp: {temperature_c:.2f}°C | Battery: {battery_remaining_wh:.1f}Wh"
            )
        canisters[current_canister] = max(0, canisters[current_canister] - hiss_energy)

        net_power = current_cpu_power - total_cooling
        delta_temp = (net_power * time_step_s) / THERMAL_MASS_J_PER_C
        temperature_c += delta_temp
        temperature_log.append(temperature_c)
        if seconds % 300 == 0 and seconds > 0:
            events.append(
                f"[{seconds:>4}s] STATUS: Temp: {temperature_c:.2f}°C | CO₂: {canisters[current_canister]:.0f}J | Battery: {battery_remaining_wh:.1f}Wh | Mode: {fan_mode}"
            )

    events.append("\n=== ULTIMATE THERMAL EDEN SIMULATION SUMMARY ===")
    events.append(f"Mission duration: {total_time_s // 60} minutes")
    events.append(f"Final temperature: {temperature_c:.2f}°C")
    events.append(f"Peak temperature: {max(temperature_log):.2f}°C")
    events.append(f"Total CO₂ purges: {purge_count}")
    events.append(f"Canister swaps: {canister_swaps}")
    events.append(f"Remaining CO₂: {sum(canisters):.0f}J")
    events.append(
        f"Battery remaining: {battery_remaining_wh:.1f}Wh ({battery_remaining_wh / BATTERY_CAPACITY_WH * 100:.1f}%)"
    )
    events.append("\n=== COOLING CONTRIBUTION ANALYSIS ===")
    total_cool = sum(cooling_contribution.values())
    for mech, joules in cooling_contribution.items():
        percentage = (joules / total_cool) * 100 if total_cool > 0 else 0
        events.append(f"{mech}: {joules:.0f}J ({percentage:.1f}%)")

    times = np.arange(0, total_time_s, time_step_s) / 60
    plt.figure(figsize=(12, 8))
    plt.plot(times, temperature_log)
    plt.axhline(y=CRITICAL_TEMP_C, color="r", linestyle="--", label=f"Critical ({CRITICAL_TEMP_C}°C)")
    plt.axhline(y=EMERGENCY_TEMP_C, color="orange", linestyle="--", label=f"Emergency ({EMERGENCY_TEMP_C}°C)")
    plt.xlabel("Time (minutes)")
    plt.ylabel("Temperature (°C)")
    plt.title("Ultimate Tactical Field Protocol - Thermal Performance")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    return events, temperature_log


if __name__ == "__main__":
    log, temps = run_simulation()
    print("\n".join(log))
    plt.savefig("thermal_eden_simulation.png")
    plt.show()
