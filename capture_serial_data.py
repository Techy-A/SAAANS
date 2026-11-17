"""
Serial Data Capture Script for ESP32
Captures sensor data from ESP32 and saves to sensor_log.csv

Usage:
python capture_serial_data.py COM11 115200

Press Ctrl+C to stop
"""

import serial
import sys
import time
from datetime import datetime

def capture_data(port, baudrate, output_file="sensor_log.csv"):
    """Capture serial data from ESP32 and save to CSV"""
    
    print(f"[INFO] Connecting to {port} at {baudrate} baud...")
    
    try:
        ser = serial.Serial(port, baudrate, timeout=1)
        time.sleep(2)  # Wait for connection
        
        print(f"[INFO] Connected! Capturing data to {output_file}")
        print("[INFO] Press Ctrl+C to stop\n")
        
        # Open file in append mode
        with open(output_file, 'a') as f:
            line_count = 0
            
            while True:
                if ser.in_waiting > 0:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    
                    if line:
                        print(line)
                        
                        # Write to file (skip header if already exists)
                        if line_count == 0 and "timestamp" in line:
                            # Check if file is empty
                            try:
                                with open(output_file, 'r') as check:
                                    if check.read(1):
                                        continue  # Skip header if file not empty
                            except:
                                pass
                        
                        f.write(line + '\n')
                        f.flush()
                        line_count += 1
                        
                time.sleep(0.1)
                
    except KeyboardInterrupt:
        print(f"\n[INFO] Stopped. Captured {line_count} lines to {output_file}")
        
    except serial.SerialException as e:
        print(f"[ERROR] Serial error: {e}")
        print(f"[INFO] Available ports:")
        import serial.tools.list_ports
        for port in serial.tools.list_ports.comports():
            print(f"  - {port.device}: {port.description}")
        
    except Exception as e:
        print(f"[ERROR] {e}")
    
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            print("[INFO] Serial port closed")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python capture_serial_data.py <PORT> [BAUDRATE]")
        print("Example: python capture_serial_data.py COM11 115200")
        sys.exit(1)
    
    port = sys.argv[1]
    baudrate = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
    
    capture_data(port, baudrate)
