# ESP32 Firmware Pseudocode

```c
// High level control loop for CO2 cooling
initialize_sensors();
initialize_actuators();

while(true) {
    read_temperatures();
    read_pressure();
    if (temp > EMERGENCY_TEMP) {
        trigger_purge();
    } else if (temp > TARGET_TEMP) {
        enable_peltier();
        set_fan_speed(HIGH);
    } else {
        disable_peltier();
        set_fan_speed(LOW);
    }
    log_telemetry();
    delay(100);
}
```

This sketch outlines how the microcontroller monitors temperature and pressure sensors to manage the CO₂ purge valve, Peltier module, and cooling fan.
