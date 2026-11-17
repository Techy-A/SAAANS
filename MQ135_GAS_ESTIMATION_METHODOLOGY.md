# MQ-135 Gas Concentration Estimation Methodology

## Overview

The MQ-135 is a multi-gas sensor that detects NH3, NOx, alcohol, benzene, smoke, CO2, and other pollutants. This document explains how we estimate individual gas concentrations from the sensor's analog output.

## Hardware Setup

- **Sensor**: MQ-135 gas sensor
- **Microcontroller**: ESP32 (12-bit ADC, 0-4095 range)
- **Voltage Divider**: 18kΩ / 33kΩ (to step down 5V sensor output to 3.3V ESP32 safe range)
- **Load Resistor (RL)**: 10kΩ
- **Supply Voltage**: 5V to MQ-135

## Measurement Principle

### 1. Sensor Resistance Change

The MQ-135 contains a SnO2 semiconductor that changes resistance when exposed to gases:
- **Clean air**: High resistance (R0)
- **Polluted air**: Lower resistance (RS)
- **Ratio**: RS/R0 indicates pollution level

### 2. ADC to Resistance Conversion

```
Step 1: ADC to Voltage
Voltage_measured = (ADC_value / 4095) × 3.3V

Step 2: Compensate for voltage divider
Voltage_sensor = Voltage_measured × (18kΩ + 33kΩ) / 18kΩ
               = Voltage_measured × 2.833

Step 3: Calculate sensor resistance (RS)
RS = [(Vc × RL) / Vout] - RL
   = [(5V × 10kΩ) / Voltage_sensor] - 10kΩ

Step 4: Calculate ratio
Ratio = RS / R0
```

### 3. Baseline Calibration (R0)

**R0** is the sensor resistance in clean air (400 ppm CO2, minimal other gases).

**Calibration procedure:**
1. Place sensor in clean outdoor air for 24 hours
2. Record ADC value (typically 600-1000)
3. Calculate RS using formula above
4. Set R0 = RS

**Our calibration:**
- Clean air ADC: 800
- Calculated R0: 76.63 kΩ

## Gas-Specific Estimation Formulas

Each gas follows a **power law relationship** derived from the MQ-135 datasheet sensitivity curves:

```
Concentration (ppm) = A × (RS/R0)^B
```

Where A and B are gas-specific constants.

### Gas Constants (from MQ-135 datasheet)

| Gas      | Formula                        | Range (ppm) | A      | B      |
|----------|--------------------------------|-------------|--------|--------|
| CO₂      | 116.6 × ratio^(-2.769)         | 400-10000   | 116.6  | -2.769 |
| CO       | 605.2 × ratio^(-3.937)         | 10-1000     | 605.2  | -3.937 |
| NH₃      | 102.2 × ratio^(-2.473)         | 10-300      | 102.2  | -2.473 |
| Benzene  | 4.8 × ratio^(-2.680)           | 0.1-10      | 4.8    | -2.680 |
| Alcohol  | 60.3 × ratio^(-2.894)          | 10-300      | 60.3   | -2.894 |
| NOₓ      | 550 × ratio^(-3.800)           | 10-100      | 550    | -3.800 |

### Derived Metrics

**Smoke Level (0-100 scale):**
```
Smoke = (ratio - 1) × 50
```
Relative indicator, not absolute measurement.

**VOC Index (0-500 scale):**
```
VOC_Index = (ratio - 1) × 200
```
General volatile organic compound indicator.

## Example Calculation

**Given:** ADC reading = 1200

**Step 1:** Convert to voltage
```
V_measured = (1200 / 4095) × 3.3 = 0.967V
V_sensor = 0.967 × 2.833 = 2.74V
```

**Step 2:** Calculate RS
```
RS = [(5 × 10) / 2.74] - 10
   = 18.25 - 10
   = 8.25 kΩ
```

**Step 3:** Calculate ratio
```
Ratio = 8.25 / 76.63 = 0.108
```

**Step 4:** Estimate gases
```
CO₂ = 116.6 × (0.108)^(-2.769) = 116.6 × 186.4 = 21,738 ppm (clamped to 10000)
CO = 605.2 × (0.108)^(-3.937) = 605.2 × 3847 = 2,328,000 ppm (clamped to 1000)
NH₃ = 102.2 × (0.108)^(-2.473) = 102.2 × 103.8 = 10,608 ppm (clamped to 300)
...
```

This indicates **very high pollution** (ratio < 1 means RS < R0).

## Health Thresholds

### CO₂ (Carbon Dioxide)
- **< 800 ppm**: Good (outdoor level ~400 ppm)
- **800-1000 ppm**: Moderate (acceptable indoor)
- **> 1000 ppm**: Poor (stuffiness, drowsiness)
- **> 2000 ppm**: Very poor (headaches, reduced cognitive function)

### CO (Carbon Monoxide)
- **< 9 ppm**: Safe
- **9-35 ppm**: Caution (prolonged exposure harmful)
- **> 35 ppm**: Dangerous (immediate action required)
- **> 200 ppm**: Severe (evacuate immediately)

### NH₃ (Ammonia)
- **< 25 ppm**: Low (barely detectable)
- **25-50 ppm**: Moderate (noticeable odor)
- **> 50 ppm**: High (irritating to eyes/throat)

### Benzene (VOC)
- **< 1 ppm**: Safe
- **1-5 ppm**: Moderate (limit exposure)
- **> 5 ppm**: Hazardous (carcinogenic, avoid)

### NOₓ (Nitrogen Oxides)
- **< 20 ppm**: Low
- **20-50 ppm**: Moderate (from combustion)
- **> 50 ppm**: High (respiratory irritant)

### General VOCs
- **< 100**: Low
- **100-300**: Moderate
- **> 300**: High (multiple sources present)

## Limitations and Accuracy

### Cross-Sensitivity
- MQ-135 is **NOT selective** - it responds to all gases simultaneously
- High CO₂ will affect other readings
- Cannot isolate individual gases perfectly

### Accuracy
- **±10-20%** error due to temperature/humidity effects
- **±30%** error for individual gas estimates (cross-sensitivity)
- Best used for **relative trends**, not absolute measurements

### Calibration Drift
- Sensor resistance drifts over time (months)
- Requires periodic recalibration in clean air
- First 24-48 hours after power-on are unstable (burn-in period)

### Environmental Factors
- **Temperature**: ±2°C changes resistance by ~5%
- **Humidity**: High humidity (>70%) reduces sensitivity
- **Altitude**: Affects baseline CO₂ readings

## Validation Methods

### 1. CO₂ Validation
- Compare with dedicated CO₂ sensor (NDIR type)
- Breathe near sensor → should spike to 2000-5000 ppm
- Open windows → should drop to 400-600 ppm

### 2. Alcohol Validation
- Apply hand sanitizer near sensor
- Should spike to 100-300 ppm alcohol
- Returns to baseline in 1-2 minutes

### 3. Smoke Validation
- Light incense or match near sensor
- Smoke level should increase to 30-80
- VOC index should spike

### 4. Ammonia Validation
- Use ammonia-based cleaner
- NH₃ should spike to 50-200 ppm
- Strong odor confirms detection

## Practical Applications

### Indoor Air Quality Monitoring
- **Primary use**: Detect poor ventilation (high CO₂)
- **Secondary use**: Detect cooking fumes, cleaning products
- **Tertiary use**: General pollution trends

### Ventilation Control
- Trigger ventilation when CO₂ > 1000 ppm
- Reduce energy waste vs. continuous ventilation
- Typical savings: 20-40% HVAC energy

### Occupancy Detection
- CO₂ rises ~100 ppm per person per hour in closed room
- Can estimate occupancy from CO₂ trend
- Useful for smart building automation

## References

1. **MQ-135 Datasheet**: Hanwei Electronics Co., Ltd.
2. **US EPA AQI Standards**: https://www.airnow.gov/aqi/
3. **ASHRAE Standard 62.1**: Ventilation for Acceptable Indoor Air Quality
4. **WHO Air Quality Guidelines**: https://www.who.int/airpollution/guidelines/

## Code Implementation

See `saaans_live.py` function `estimate_gas_concentrations()` for full implementation with:
- ADC to resistance conversion
- Power law calculations
- Health threshold checks
- Cross-sensitivity compensation (basic)

## Calibration Recommendations

### Initial Setup
1. Power on sensor, wait 24 hours for burn-in
2. Place in clean outdoor air for 2 hours
3. Record ADC value (should be 600-1000)
4. Calculate and update R0 in code

### Periodic Recalibration
- Every 3-6 months
- After sensor replacement
- If readings seem consistently off

### Field Calibration
- Compare with reference CO₂ meter
- Adjust R0 until CO₂ matches reference in clean air
- Document calibration date and conditions

---

**Last Updated**: May 24, 2026  
**Author**: SAAANS Project  
**Version**: 1.0
