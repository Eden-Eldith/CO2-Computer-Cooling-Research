# Thermal Management Simulation Framework

[![Python](https://img.shields.io/badge/Python-3.8+-green)](https://github.com/pcobrien/CO2-Adaptive-Cooling/tree/main/simulation)
[![Version](https://img.shields.io/badge/Version-1.0.0-blue.svg)](https://github.com/pcobrien/CO2-Adaptive-Cooling/tree/main/simulation)
[![Dependencies](https://img.shields.io/badge/Dependencies-numpy%20%7C%20matplotlib%20%7C%20RPi.GPIO-orange)](https://github.com/pcobrien/CO2-Adaptive-Cooling/blob/main/simulation/requirements.txt)
[![Status](https://img.shields.io/badge/Status-Validated-success.svg)](https://github.com/pcobrien/CO2-Adaptive-Cooling/tree/main/simulation)

This directory contains the mathematical models, simulation code, and analysis tools for the CO2-Based Adaptive Cooling project. The simulation framework implements a comprehensive thermal model based on established thermodynamic principles, with validation against theoretical predictions demonstrating accurate performance modeling for both field-deployed and domestic outdoor computing scenarios.

## Mathematical Models

### Core Energy Balance
The system is modeled as a lumped thermal mass with the core energy balance:

```
ΔT = (PCPU - Pcool)Δt / Cth
```
Where:
- ΔT: Temperature change
- PCPU: CPU power input
- Pcool: Total cooling power
- Cth: Thermal capacitance (300 J/°C)
- Δt: Timestep (5s)

## Core System Parameters

### Thermal Power Components

The system integrates multiple cooling mechanisms whose combined effect is modeled as:
```
Pcool = Ppassive + PCO2 + Pcond + PTEC + Pfan
```

Where:
1. **Ppassive = 1.5W**: Base thermal dissipation (degraded by environment)
2. **PCO2**: Dynamic CO2 cooling
   - Adaptive microbursts (0.3-1.0s)
   - Emergency purge events
   - Joule-Thomson expansion effect
3. **Pcond = 2.2W**: Cold canister conduction
   - 180s post-purge duration
   - Metal-to-metal thermal interface
4. **PTEC**: Peltier cooling (temperature-dependent)
   - Max 15W cooling capacity
   - 60% base efficiency
   - Temperature-based derating
5. **Pfan**: Convective enhancement multiplier
   - 1.3x to 2.5x boost range
   - Dynamic duty cycle control
   - Post-purge enhancement

### Advanced Subsystem Models

#### 1. CO2 Cooling System (Primary)
- **Canister Parameters**:
  - Effective Cooling: 2900J (85% of theoretical 3852J)
  - Joule-Thomson Effect: ΔT = 1.1 K/atm × ΔP
  - Phase Change: Q = m × 321 J/g
  - Conduction: 2.2W for 180s post-purge

#### 2. Thermoelectric Model (TEC)
- **Peltier Effect Equation**:
  ```
  QTEC = α·I·Tc - 0.5·I²·R - K·ΔT
  ```
  Where:
  - α: Seebeck coefficient
  - I: Current through device
  - Tc: Cold side temperature
  - R: Electrical resistance
  - K: Thermal conductance
  - ΔT: Temperature differential

- **Operating Parameters**:
  - Max Cooling: 15W at ΔT = 0
  - Power Draw: 30W nominal
  - Base Efficiency: 60%
  - Runtime Limit: 120s continuous
  
- **Thermal Protection**:
  - Hot-side temperature monitoring
  - Efficiency derating above 85°C
  - Auto-shutdown at 95°C

#### 3. Fan Enhancement System
- **Convection Multiplier Model**:
  ```python
  multiplier = base_mult * speed_factor * purge_boost
  ```
  Where:
  - base_mult: 1.3x to 2.5x range
  - speed_factor: f(duty_cycle)
  - purge_boost: Enhanced post-purge effect

- **Operating Modes**:
  - PASSIVE: Natural convection
  - SLOW_HISS: Intermittent operation
  - NORMAL: Standard cooling
  - PURGE: Maximum airflow
  - EMERGENCY: Full power

- **Control Parameters**:
  - Power Draw: 0.25W at 100%
  - Ramp Time: 1.0s to full speed
  - Pulse Width Modulation
  - Temperature-based adaptation

## Development Requirements

### Python Environment
- Python 3.8 or higher
- Dependencies (in requirements.txt):
  ```
  numpy>=1.21.0    # Array operations, thermal calculations
  matplotlib>=3.4.0 # Performance visualization
  RPi.GPIO         # Hardware control for tactical Pi cooling
  ```

### Installation
```bash
# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # Linux/Mac
.\venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt
```

### Hardware Requirements (for physical validation)
- ESP32 development board
- DS18B20 temperature sensors
- BMP280 pressure sensor
- Dual solenoid valves
- TEC module + heatsink
- 40mm PWM fan

## Simulation Implementation

### 1. Dynamic Workload Model
```python
def get_cpu_workload(time_s):
    """
    Simulate realistic CPU workload patterns including periodic intensive tasks
    and natural variations.
    
    Parameters:
        time_s (float): Current simulation time in seconds
    
    Returns:
        float: CPU power draw in watts
    """
    # Base load (85% TDP)
    base_load = cpu_power_watts * 0.85
    
    # Natural variations (sinusoidal)
    variation = np.sin(time_s / 300 * np.pi) * 0.15 * cpu_power_watts
    
    # Intense workload periods (110% TDP)
    if 900 < time_s < 1100 or 2400 < time_s < 2700:
        return cpu_power_watts * 1.1
    
    return base_load + variation
```

Key Features:
- Baseline: 85% of TDP
- Periodic variation: ±15%
- Stress periods: 110% TDP
- 5-minute natural cycles

### 2. Adaptive Control Algorithm
The system implements a sophisticated state machine with temperature-based thresholds and integrated resource management:

```python
def adaptive_control(cpu_temp, battery_level, co2_available):
    """
    Determine cooling strategy based on current conditions.
    Returns tuple of (burst_duration, cycle_time, fan_duty, tec_active)
    """
    if cpu_temp < 60:  # Normal Operation
        return {
            'burst_duration': 0.3,
            'cycle_time': 8.0,
            'fan_duty': 0,
            'tec_active': False
        }
    elif cpu_temp < 70:  # Light Load
        return {
            'burst_duration': 0.5,
            'cycle_time': 5.0,
            'fan_duty': 30,
            'tec_active': False
        }
    elif cpu_temp < 75:  # Heavy Load
        return {
            'burst_duration': 0.7,
            'cycle_time': 4.0,
            'fan_duty': 50,
            'tec_active': True
        }
    else:  # Emergency Cooling
        return {
            'burst_duration': 1.0,
            'cycle_time': 3.0,
            'fan_duty': 100,
            'tec_active': True
        }
```

Control Features:
- Temperature-based state transitions
- Resource-aware decision making
- Integrated safety thresholds
- Adaptive duty cycle management

### Cooling Mechanisms Implementation

#### 1. Passive Thermal Management
- Base chassis dissipation (1.5W)
- Environmental derating factors
- Thermal mass effects (300 J/°C)
- Natural convection modeling

#### 2. CO2 Cooling System
- **Microburst Control**
  - Duration: 0.3-1.0s adaptive
  - Intervals: 3-8s dynamic
  - Efficiency: 85% of theoretical
  - Resource tracking

- **Emergency Features**
  - Full purge capability
  - Dual canister system
  - Automatic switchover
  - Low-level warnings

#### 3. Thermoelectric System
- **Efficiency Management**
  - Temperature-based derating
  - Power-draw optimization
  - Battery level monitoring
  - Thermal protection

- **Operating Modes**
  - Standard cooling (15W)
  - Post-purge boost
  - Emergency operation
  - Recovery periods

#### 4. Advanced Fan Control
- **Adaptive Modes**
  - PASSIVE: 0% duty
  - SLOW_HISS: 30% pulsed
  - NORMAL: 50% sustained
  - PURGE: 80% enhanced
  - EMERGENCY: 100% maximum

- **Enhancement Features**
  - Dynamic speed control
  - Post-purge optimization
  - Efficiency multipliers
  - Acoustic management

### Analysis & Validation

#### Performance Metrics
- Temperature progression over time
- Resource utilization tracking
- Component contribution analysis
- System state transitions

#### Data Visualization
- Real-time temperature plots
- Resource consumption graphs
- Cooling efficiency analysis
- State transition diagrams

#### Status Logging
- Event timestamps
- System state changes
- Resource levels
- Error conditions

#### Validation Results
- Theoretical predictions
- Simulated performance
- Component efficiency
- System limitations

## Usage Guide

### Basic Operation
```bash
# Run standard simulation
python laptopcoolingsim.py

# Run 1-year endurance test
python laptopcoolingsim1yearsim.py

# Run tactical cooling simulation
python tactical_cooling_sim.py
```

### Simulation Variants
1. **Standard Simulation** (laptopcoolingsim.py)
   - 60-minute mission profile
   - Standard workload patterns
   - Full instrumentation

2. **Extended Testing** (laptopcoolingsim1yearsim.py)
   - Long-duration validation
   - Resource management focus
   - Endurance analysis

3. **Tactical Simulation** (tactical_cooling_sim.py)
   - Field deployment scenarios
   - Extreme condition testing
   - Mission-critical operations

### Output Analysis
- Temperature graphs (PNG format)
- Detailed event logging
- Resource utilization stats
- Performance metrics
