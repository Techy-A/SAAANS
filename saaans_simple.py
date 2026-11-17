"""
Simple SAAANS Monitor - No screen clearing, just continuous output
"""
import serial
import time
from datetime import datetime
import numpy as np

port = "COM11"
baudrate = 115200

print("Connecting to ESP32...")
ser = serial.Serial(port, baudrate, timeout=1)
time.sleep(2)

print("Connected! Reading sensor data...\n")

sensor_data = {}
line_count = 0

try:
    while True:
        if ser.in_waiting > 0:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            
            if line:
                print(f"[RAW] {line}")  # Show raw data
                
                # Parse data
                if "Temperature:" in line:
                    sensor_data['temp'] = float(line.split()[-2])
                elif "Humidity:" in line:
                    sensor_data['humidity'] = float(line.split()[-2])
                elif "Pressure:" in line:
                    sensor_data['pressure'] = float(line.split()[-2])
                elif "Raw ADC:" in line:
                    sensor_data['mq_raw'] = int(line.split()[-1])
                elif "Voltage:" in line:
                    sensor_data['voltage'] = float(line.split()[-2])
                
                # Print summary when we have all data
                if len(sensor_data) >= 5:
                    print("\n" + "="*60)
                    print(f"READING #{line_count} - {datetime.now().strftime('%H:%M:%S')}")
                    print("="*60)
                    print(f"Temperature:  {sensor_data['temp']:.2f} C")
                    print(f"Humidity:     {sensor_data['humidity']:.2f} %")
                    print(f"Pressure:     {sensor_data['pressure']:.2f} hPa")
                    print(f"MQ-135 Raw:   {sensor_data['mq_raw']}")
                    print(f"MQ-135 Volt:  {sensor_data['voltage']:.3f} V")
                    
                    # Calculate gas concentrations
                    mq = sensor_data['mq_raw']
                    voltage = sensor_data['voltage']
                    
                    # Convert to sensor resistance
                    sensor_voltage = voltage * 2.833  # Voltage divider compensation
                    if sensor_voltage <= 0.1:
                        sensor_voltage = 0.1
                    RS = ((5.0 * 10.0) / sensor_voltage) - 10.0
                    R0 = 76.63  # Calibrated baseline
                    ratio = RS / R0
                    
                    # Calculate gas concentrations using power law
                    co2 = max(400, min(10000, 116.6 * pow(ratio, -2.769)))
                    co = max(0, min(1000, 605.18 * pow(ratio, -3.937)))
                    nh3 = max(0, min(300, 102.2 * pow(ratio, -2.473)))
                    benzene = max(0, min(10, 4.8 * pow(ratio, -2.68)))
                    alcohol = max(0, min(300, 60.3 * pow(ratio, -2.894)))
                    nox = max(0, min(100, 550 * pow(ratio, -3.8)))
                    smoke = min(100, max(0, (ratio - 1) * 50))
                    voc = min(500, max(0, (ratio - 1) * 200))
                    
                    print(f"\nGas                 Level        Relevance to IAQ")
                    print("─" * 60)
                    print(f"CO2                 {co2:.0f} ppm      Carbon dioxide - stuffiness, occupancy")
                    print(f"NH3                 {nh3:.1f} ppm      Ammonia - cleaning products, urine")
                    print(f"NOx                 {nox:.1f} ppm      Nitrogen oxides - combustion, traffic")
                    print(f"Benzene             {benzene:.2f} ppm      VOC - paints, adhesives, fuel")
                    print(f"Smoke               {smoke:.0f}/100       Cigarette smoke, burning")
                    print(f"Alcohol vapour      {alcohol:.1f} ppm      Cleaning spirits, sanitiser")
                    print(f"CO                  {co:.1f} ppm      Carbon monoxide - dangerous gas")
                    print(f"General VOCs        {voc:.0f}/500       Volatile organic compounds")
                    
                    # Overall assessment
                    if mq < 500:
                        air_quality = "Excellent - Very clean air"
                    elif mq < 1000:
                        air_quality = "Good - Normal indoor air"
                    elif mq < 2000:
                        air_quality = "Moderate - Consider ventilation"
                    else:
                        air_quality = "Poor - Ventilation needed"
                    
                    print(f"\nOverall Air Quality: {air_quality}")
                    print("="*60 + "\n")
                    
                    sensor_data = {}
                    line_count += 1
        
        time.sleep(0.1)

except KeyboardInterrupt:
    print("\nStopped by user")
finally:
    ser.close()
    print("Serial port closed")
