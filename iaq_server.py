"""
SAAANS - Smart Indoor Air Quality (IAQ) Monitor Backend Server
Handles ESP32 Serial Communication, Gas Estimation Models, and Real-Time Web API.
"""

import os
import sys
import time
import csv
import json
import math
import random
import shutil
import statistics
import threading
import collections
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, send_file, render_template

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

try:
    import joblib
    import saaans_forecast
    HAS_FORECAST = True
except ImportError:
    HAS_FORECAST = False

app = Flask(__name__, template_folder="templates", static_folder="static")

# New file with one fixed schema; the old sensor_log.csv mixed several formats and is left untouched
LOG_FILE = os.path.join(os.path.dirname(__file__), "saans_log.csv")

# ==========================================
# IAQ & Gas Estimation Model Constants
# ==========================================
VCC = 5.0            # MQ-135 supply voltage
RL = 10.0            # Load resistance in kOhms (on the MQ-135 module)
R_TOP = 18.0         # Divider: AOUT -> R_TOP -> ADC pin -> R_BOTTOM -> GND (kOhms)
R_BOTTOM = 33.0      # CONFIRM this matches the wiring; swapping them changes every reading
DIVIDER_RATIO = (R_TOP + R_BOTTOM) / R_BOTTOM   # ADC-pin volts -> sensor volts (~1.545)
CAL_FILE = os.path.join(os.path.dirname(__file__), "calibration.json")
STALE_AFTER_S = 30   # live hardware counts as stale if no valid line for this long
ALLOW_SIM = os.environ.get("SAANS_SIM", "1") == "1"   # fall back to simulation only when no hardware connected
DEFAULT_R0 = 40.0    # kOhms; placeholder until calibrated in clean air via POST /api/calibrate
CAL_WINDOW_S = 600   # R0 is the median Rs over this window; single readings bounce by 10% or more
CAL_MIN_READINGS = 30
# The BME280 sits next to the ESP32 and the MQ-135 heater and read 2.1-2.8 C above the PMS5003T's own
# sensor in every hour logged (mean about 2.5). The log keeps the raw value; the dashboard API shows
# it minus this offset. Set to 0 to show raw readings.
BME_TEMP_OFFSET_C = 2.5
# PMS5003T data words 1-13 as sent by the firmware after the first 10 CSV columns. Confirmed from real
# frames: counts fall with particle size, and humidity tracks the BME280 once divided by 10.
PMS_WORDS = ("cf_pm1", "cf_pm25", "cf_pm10", None, None, None, "n03", "n05", "n10", "n25",
             "pms_temp", "pms_hum", "pms_status")
PMS_FIELDS = ("cf_pm1", "cf_pm25", "cf_pm10", "n03", "n05", "n10", "n25", "pms_temp", "pms_hum",
              "pms_version", "pms_error")
LOG_COLUMNS = ("timestamp", "temperature", "humidity", "pressure", "mq_raw", "voltage", "rs", "ratio",
               "pm1", "pm25", "pm10", "fan", "aqi", "aqi_category") + PMS_FIELDS
AUTO_CONNECT = os.environ.get("SAANS_AUTOCONNECT", "1") == "1"
AUTO_CONNECT_RETRY_S = 5
ESP_PORT_HINTS = ("CH340", "CH910", "CP210", "USB-SERIAL", "USB SERIAL")   # USB-UART chips on ESP32 dev boards


def load_r0():
    try:
        with open(CAL_FILE, "r", encoding="utf-8") as f:
            return float(json.load(f)["r0"])
    except Exception:
        return DEFAULT_R0


R0 = load_r0()


def recent_log_rows(window_s):
    """Logged rows from the last window_s seconds that have an Rs value, oldest first."""
    now = datetime.now()
    with open(LOG_FILE, "r", encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f)
                if r.get("rs") and (now - datetime.fromisoformat(r["timestamp"])).total_seconds() <= window_s]


POWER_GAP_S = 600          # no data for 10 min = board was unplugged, heater went cold
MQ_SETTLE_MIN = 120        # MQ-135 readings swing for about 2 h after power-on


def last_power_on_from_log(path=None):
    """Start of the newest unbroken stretch in the log, so a server restart does not reset warm-up."""
    path = path or LOG_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            f.readline()
            tail = collections.deque(f, maxlen=12 * 60 * 48)   # about two days
    except (FileNotFoundError, OSError):
        return None
    start, prev = None, None
    for line in tail:
        try:
            t = datetime.fromisoformat(line.split(",", 1)[0]).timestamp()
        except ValueError:
            continue
        if prev is None or t - prev > POWER_GAP_S:
            start = t
        prev = t
    if prev is None or time.time() - prev > POWER_GAP_S:
        return None   # nothing recent: the board is off now
    return start


def epa_corrected_pm25(cf_pm25, rh):
    """US EPA nationwide correction for Plantower PMS5003 sensors (Barkjohn et al. 2021):
    PM2.5 = 0.52 x CF=1 - 0.086 x RH + 5.75. Fitted mostly below about 250 ug/m3, so at north
    Indian winter levels it is a plausible estimate, not a calibration. Display only; AQI is unchanged."""
    if cf_pm25 is None or rh is None:
        return None
    return round(max(0.0, 0.52 * cf_pm25 - 0.086 * rh + 5.75), 1)


class SensorDataManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.port = "COM11"
        self.baudrate = 115200
        self.ser = None
        self.is_connected = False
        self.is_simulated = False
        self.running = True
        self.history = []
        self.max_history = 720   # one hour at the firmware's 5 s interval
        self.last_data_time = None
        self.mq_powered_since = last_power_on_from_log()   # MQ-135 heater on since (epoch s)
        self.user_disconnected = False
        self.last_auto_try = 0.0

        # Current state
        self.current_data = {
            "timestamp": datetime.now().isoformat(),
            "time_str": datetime.now().strftime("%H:%M:%S"),
            "temperature": 27.5,
            "temp_f": round(27.5 * 9/5 + 32, 1),
            "humidity": 45.0,
            "pressure": 972.4,
            "mq_raw": 850,
            "voltage": 0.685,
            "rs": 75.0,
            "ratio": 0.98,
            "co2": 520.0,
            "co": 1.2,
            "nh3": 4.5,
            "benzene": 0.15,
            "alcohol": 5.2,
            "nox": 1.8,
            "smoke": 8.0,
            "voc_index": 45.0,
            "pm1": None,
            "pm25_est": None,
            "pm10_est": None,
            "fan": 0,
            "aqi": 65.0,
            "aqi_category": "Satisfactory",
            "aqi_color": "#EAB308",
            "ventilation_status": "OK",
            "ventilation_message": "Indoor air quality is satisfactory. Natural ventilation is safe.",
            "source": "Initializing"
        }
        
        self._upgrade_log()
        self._load_history()

        # Background worker thread
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()

    def _upgrade_log(self):
        """Add any new columns to an older log. Old rows stay as they are, with the new columns blank."""
        if not os.path.exists(LOG_FILE):
            return
        with open(LOG_FILE, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
            old_columns = rows[0].keys() if rows else ()
        if tuple(old_columns) == LOG_COLUMNS or not rows:
            return
        shutil.copyfile(LOG_FILE, LOG_FILE + f".before_upgrade_{datetime.now():%Y%m%d_%H%M%S}.bak")
        tmp = LOG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS, restval="")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, LOG_FILE)

    def _load_history(self):
        """Seed the trend charts from the real readings already logged, so a restart keeps them."""
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))[-self.max_history:]
            for r in rows:
                self.history.append({
                    "timestamp": r["timestamp"],
                    "time_str": r["timestamp"][11:19],
                    "temperature": float(r["temperature"]),
                    "humidity": float(r["humidity"]),
                    "pressure": float(r["pressure"]),
                    "mq_raw": int(r["mq_raw"]),
                    "voltage": float(r["voltage"]),
                    "pm1": float(r["pm1"]) if r.get("pm1") else None,
                    "pm25_est": float(r["pm25"]) if r["pm25"] else None,
                    "pm10_est": float(r["pm10"]) if r["pm10"] else None,
                    "fan": int(r["fan"]),
                    "aqi": float(r["aqi"]),
                    "aqi_category": r["aqi_category"],
                })
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[WARN] Could not load {LOG_FILE}: {e}")

    def get_available_ports(self):
        """List all available COM ports"""
        if not HAS_SERIAL:
            return []
        ports = []
        try:
            for p in serial.tools.list_ports.comports():
                ports.append({
                    "port": p.device,
                    "description": p.description or p.device,
                    "hwid": p.hwid or ""
                })
        except Exception as e:
            print(f"[ERROR] listing ports: {e}")
        return ports

    def connect(self, port_name, baud=115200):
        """Connect to a specific serial port"""
        with self.lock:
            self.user_disconnected = False
            if self.ser and self.ser.is_open:
                try:
                    self.ser.close()
                except:
                    pass
            self.port = port_name
            self.baudrate = int(baud)
            self.is_connected = False
            
            if not HAS_SERIAL:
                return False, "pyserial not installed"
                
            try:
                # DTR/RTS drive the ESP32's BOOT and EN pins. Keep BOOT released and pulse EN,
                # so opening the port always starts the firmware instead of the bootloader.
                self.ser = serial.Serial()
                self.ser.port = self.port
                self.ser.baudrate = self.baudrate
                self.ser.timeout = 1
                self.ser.dtr = False
                self.ser.rts = True
                self.ser.open()
                time.sleep(0.1)
                self.ser.rts = False
                time.sleep(1.5)
                self.is_connected = True
                self.is_simulated = False
                return True, f"Connected to {self.port}"
            except Exception as e:
                self.is_connected = False
                return False, str(e)

    def disconnect(self):
        """Disconnect active serial connection"""
        with self.lock:
            self.user_disconnected = True
            if self.ser and self.ser.is_open:
                try:
                    self.ser.close()
                except:
                    pass
            self.is_connected = False
            return True, "Disconnected"

    def _auto_connect(self):
        """Connect (or reconnect after a replug) to the ESP32 unless the user disconnected on purpose."""
        if not AUTO_CONNECT or self.user_disconnected or not HAS_SERIAL:
            return
        if time.time() - self.last_auto_try < AUTO_CONNECT_RETRY_S:
            return
        self.last_auto_try = time.time()
        ports = self.get_available_ports()
        names = [p["port"] for p in ports]
        if self.port in names:
            target = self.port
        else:
            target = next((p["port"] for p in ports
                           if any(h in p["description"].upper() for h in ESP_PORT_HINTS)), None)
        if target is None:
            return
        ok, msg = self.connect(target, self.baudrate)
        print(f"[AUTO] {msg}")

    def calculate_gases(self, mq_raw):
        """Estimate gas concentrations using calibrated curves for indoor air"""
        mq_raw = max(50, min(4095, mq_raw))
        voltage = (mq_raw / 4095.0) * 3.3            # volts at the ESP32 ADC pin
        sensor_v = voltage * DIVIDER_RATIO            # undo the divider -> MQ-135 AOUT volts

        # RS = [(Vc * RL) / Vout] - RL
        safe_v = max(0.05, sensor_v)
        rs = ((VCC * RL) / safe_v) - RL
        rs = max(1.0, rs)
        
        # Clean air baseline ratio RS/R0
        ratio = rs / R0
        ratio = max(0.15, min(5.0, ratio))
        
        # Physical model calibrated to clean air baseline (ratio ~ 1.0 -> clean air)
        # Ratio drops as gas concentration increases
        inv_r = 1.0 / ratio
        
        co2_ppm = max(400.0, min(5000.0, 400.0 + 120.0 * math.pow(inv_r, 2.2)))
        co_ppm = max(0.1, min(100.0, 0.8 * math.pow(inv_r, 3.2)))
        nh3_ppm = max(0.1, min(150.0, 3.2 * math.pow(inv_r, 2.4)))
        benzene_ppm = max(0.01, min(15.0, 0.12 * math.pow(inv_r, 2.6)))
        alcohol_ppm = max(0.1, min(200.0, 3.5 * math.pow(inv_r, 2.5)))
        nox_ppm = max(0.1, min(50.0, 1.2 * math.pow(inv_r, 3.0)))
        smoke_level = max(0.0, min(100.0, (inv_r - 0.9) * 28.0))
        voc_index = max(10.0, min(500.0, 30.0 + (inv_r - 0.8) * 95.0))
        
        return {
            "voltage": round(voltage, 3),
            "rs": round(rs, 2),
            "ratio": round(ratio, 3),
            "co2": round(co2_ppm, 1),
            "co": round(co_ppm, 2),
            "nh3": round(nh3_ppm, 2),
            "benzene": round(benzene_ppm, 3),
            "alcohol": round(alcohol_ppm, 1),
            "nox": round(nox_ppm, 2),
            "smoke": round(smoke_level, 1),
            "voc_index": round(voc_index, 1),
            # Honest single number: 100 = this room's usual air (R0 is the median Rs).
            # 200 means the sensor resistance has halved, i.e. roughly twice the usual gas load.
            "gas_index": round(100.0 * inv_r)
        }

    @staticmethod
    def pm25_to_aqi(pm25):
        """US-EPA AQI sub-index from PM2.5 (ug/m3)"""
        bps = [(0.0, 12.0, 0, 50), (12.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
               (55.5, 150.4, 151, 200), (150.5, 250.4, 201, 300),
               (250.5, 350.4, 301, 400), (350.5, 500.4, 401, 500)]
        for c_lo, c_hi, i_lo, i_hi in bps:
            if pm25 <= c_hi:   # readings between bands (e.g. 12.05) take the upper band
                return (i_hi - i_lo) / (c_hi - c_lo) * (max(pm25, c_lo) - c_lo) + i_lo
        # Above the EPA scale (PM2.5 over 500.4): carry on at the slope of the top band.
        # Not an official value; the PMS5003T reads up to 1000 ug/m3.
        if pm25 > 500.4:
            return 500.0 + (99.0 / 149.9) * (min(pm25, 1000.0) - 500.4)
        return 0.0

    def calculate_aqi(self, temp, humidity, mq_raw, pm25=None, pm10=None):
        """Calculate composite AQI and ventilation advice.
        pm25/pm10 are measured values (PMS5003) or None when no particulate sensor is attached."""
        # MQ135 sub-AQI
        if mq_raw < 800:
            mq_aqi = (mq_raw / 800.0) * 50.0
        elif mq_raw < 1500:
            mq_aqi = 50.0 + ((mq_raw - 800.0) / 700.0) * 50.0
        elif mq_raw < 2500:
            mq_aqi = 100.0 + ((mq_raw - 1500.0) / 1000.0) * 100.0
        else:
            mq_aqi = 200.0 + ((mq_raw - 2500.0) / 1595.0) * 200.0
        mq_aqi = min(500.0, max(10.0, mq_aqi))

        # Discomfort / Thermal stress index
        discomfort = 0.0
        if temp > 30:
            discomfort += (temp - 30) * 3.0
        if humidity > 70:
            discomfort += (humidity - 70) * 1.5
        elif humidity < 30:
            discomfort += (30 - humidity) * 0.8
        comfort_factor = min(100.0, discomfort)

        # Composite AQI
        composite = 0.75 * mq_aqi + 0.25 * comfort_factor
        if pm25 is not None:
            # Standard practice: overall AQI is the worst sub-index
            composite = max(composite, self.pm25_to_aqi(pm25))
        composite = round(composite, 1)

        # Categorization
        if composite <= 50:
            cat = "Good"
            color = "#10B981"  # Emerald
            vent_status = "EXCELLENT"
            msg = "Air quality is ideal. Fresh and clean indoor air."
        elif composite <= 100:
            cat = "Satisfactory"
            color = "#F59E0B"  # Amber
            vent_status = "OK"
            msg = "Air quality is acceptable. Normal ventilation is sufficient."
        elif composite <= 150:
            cat = "Moderate"
            color = "#F97316"  # Orange
            vent_status = "VENTILATE_SOON"
            msg = "Mild stuffiness or contaminants detected. Open a window for 10-15 minutes."
        elif composite <= 200:
            cat = "Poor"
            color = "#EF4444"  # Red
            vent_status = "VENTILATE_NOW"
            msg = "Poor air quality detected! Open windows or turn on mechanical air purifier."
        elif composite <= 300:
            cat = "Very Poor"
            color = "#8B5CF6"  # Purple
            vent_status = "ALERT"
            msg = "High level of contaminants or CO2! Immediate ventilation required."
        else:
            cat = "Hazardous"
            color = "#B91C1C"  # Dark red
            vent_status = "DANGER"
            msg = "Critical air quality level! Check for smoke or gas leaks and ventilate immediately."

        # Particulates are only reported when measured; the MQ-135 cannot sense PM
        pm25_est = round(pm25, 1) if pm25 is not None else None
        pm10_est = round(pm10, 1) if pm10 is not None else None

        return {
            "aqi": composite,
            "aqi_category": cat,
            "aqi_color": color,
            "ventilation_status": vent_status,
            "ventilation_message": msg,
            "pm25_est": pm25_est,
            "pm10_est": pm10_est
        }

    def _parse_serial_line(self, line):
        """Parse lines coming from ESP32 firmware"""
        data_updates = {}
        line = line.strip()
        if not line:
            return data_updates

        # Check for CSV format: timestamp,temp,humidity,pressure,mq_raw,voltage
        if "," in line and not line.startswith("timestamp") and not line.startswith("==="):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5:
                try:
                    data_updates["temperature"] = float(parts[1])
                    data_updates["humidity"] = float(parts[2])
                    data_updates["pressure"] = float(parts[3])
                    data_updates["mq_raw"] = int(float(parts[4]))
                    if len(parts) >= 6:
                        data_updates["voltage"] = float(parts[5])
                    if len(parts) >= 9:
                        # pm columns are -1 when no PMS5003 is attached
                        for key, idx in (("pm1", 6), ("pm25", 7), ("pm10", 8)):
                            val = float(parts[idx])
                            data_updates[key] = val if val >= 0 else None
                    if len(parts) >= 10:
                        data_updates["fan"] = int(float(parts[9]))
                    if len(parts) >= 23:
                        words = dict(zip(PMS_WORDS, (int(float(p)) for p in parts[10:23])))
                        if words["pms_status"] >= 0:   # -1 until the first valid frame
                            for key in ("cf_pm1", "cf_pm25", "cf_pm10", "n03", "n05", "n10", "n25"):
                                data_updates[key] = words[key]
                            data_updates["pms_temp"] = words["pms_temp"] / 10.0
                            data_updates["pms_hum"] = words["pms_hum"] / 10.0
                            data_updates["pms_version"] = words["pms_status"] >> 8
                            data_updates["pms_error"] = words["pms_status"] & 0xFF
                        else:
                            for key in PMS_FIELDS:
                                data_updates[key] = None
                    return data_updates
                except:
                    pass

        # Check for key-value formats
        lower = line.lower()
        try:
            if "temp" in lower:
                tokens = line.replace(":", " ").replace("°c", " ").replace("c", " ").split()
                for token in tokens:
                    try:
                        val = float(token)
                        if -20 <= val <= 85:
                            data_updates["temperature"] = val
                            break
                    except:
                        pass
            elif "humid" in lower:
                tokens = line.replace(":", " ").replace("%", " ").split()
                for token in tokens:
                    try:
                        val = float(token)
                        if 0 <= val <= 100:
                            data_updates["humidity"] = val
                            break
                    except:
                        pass
            elif "press" in lower:
                tokens = line.replace(":", " ").replace("hpa", " ").split()
                for token in tokens:
                    try:
                        val = float(token)
                        if 300 <= val <= 1200:
                            data_updates["pressure"] = val
                            break
                    except:
                        pass
            elif "adc" in lower or "mq135" in lower or "raw" in lower:
                tokens = line.replace(":", " ").split()
                for token in tokens:
                    try:
                        val = int(token)
                        if 0 <= val <= 4095:
                            data_updates["mq_raw"] = val
                            break
                    except:
                        pass
            elif "voltage" in lower:
                tokens = line.replace(":", " ").replace("v", " ").split()
                for token in tokens:
                    try:
                        val = float(token)
                        if 0.0 <= val <= 5.0:
                            data_updates["voltage"] = val
                            break
                    except:
                        pass
        except Exception as e:
            pass

        return data_updates

    def log_reading_to_csv(self, record):
        """Append reading to sensor_log.csv"""
        file_exists = os.path.exists(LOG_FILE)
        row = {c: record.get(c) for c in LOG_COLUMNS}
        row["pm25"] = record["pm25_est"]
        row["pm10"] = record["pm10_est"]
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                if not file_exists or os.path.getsize(LOG_FILE) == 0:
                    f.write(",".join(LOG_COLUMNS) + "\n")
                f.write(",".join("" if row[c] is None else str(row[c]) for c in LOG_COLUMNS) + "\n")
        except Exception as e:
            print(f"[ERROR] writing to CSV: {e}")

    def _run_loop(self):
        """Main background reading / simulation loop"""
        sim_step = 0
        last_log_time = 0

        while self.running:
            read_success = False
            incoming_updates = {}

            if not self.is_connected:
                self._auto_connect()

            # 1. Attempt reading from physical serial port if connected
            if self.is_connected and self.ser and self.ser.is_open:
                try:
                    while self.ser.in_waiting > 0:
                        raw_line = self.ser.readline().decode("utf-8", errors="ignore")
                        updates = self._parse_serial_line(raw_line)
                        if updates:
                            incoming_updates.update(updates)
                            read_success = True
                except Exception as e:
                    print(f"[WARN] Serial read error: {e}")
                    self.is_connected = False

            if read_success:
                now_s = time.time()
                # A long silence means the board (and the MQ-135 heater) was unplugged
                if self.last_data_time is None and self.mq_powered_since is None or                         self.last_data_time is not None and now_s - self.last_data_time > POWER_GAP_S:
                    self.mq_powered_since = now_s
                self.last_data_time = now_s

            hw_live = bool(self.is_connected and self.ser and self.ser.is_open)
            if hw_live and not read_success:
                # Hardware is connected: never fabricate data. Keep the last real reading and flag it stale.
                with self.lock:
                    self.is_simulated = False
                    age = None if self.last_data_time is None else time.time() - self.last_data_time
                    if age is None:
                        self.current_data["source"] = f"Live Hardware ({self.port}) - waiting for first reading"
                    elif age > STALE_AFTER_S:
                        self.current_data["source"] = f"Live Hardware ({self.port}) - STALE ({int(age)}s old)"
                time.sleep(1.0)
                continue
            if not hw_live and not ALLOW_SIM:
                time.sleep(1.0)
                continue

            # 2. No hardware connected (demo mode): run smooth mathematical simulation
            with self.lock:
                if not read_success:
                    self.is_simulated = True
                    sim_step += 1
                    t = sim_step * 0.1
                    
                    # Simulated natural drift
                    sim_temp = round(27.0 + 2.5 * math.sin(t * 0.05) + random.uniform(-0.15, 0.15), 2)
                    sim_hum = round(48.0 - 5.0 * math.sin(t * 0.05) + random.uniform(-0.3, 0.3), 2)
                    sim_press = round(972.5 + 1.2 * math.cos(t * 0.02) + random.uniform(-0.1, 0.1), 2)
                    
                    # Occasional VOC spikes (e.g. simulated cooking / occupant activity)
                    base_adc = 780 + int(120 * math.sin(t * 0.08))
                    noise = random.randint(-25, 25)
                    sim_mq = max(300, min(3500, base_adc + noise))
                    
                    temp = sim_temp
                    hum = sim_hum
                    press = sim_press
                    mq_raw = sim_mq
                    pm25 = round(18.0 + 8.0 * math.sin(t * 0.07) + random.uniform(-1.5, 1.5), 1)
                    pm10 = round(pm25 * 1.5, 1)
                    pm1 = round(pm25 * 0.6, 1)
                    fan = 0
                    source = "Simulation (ESP32 Disconnected)"
                else:
                    self.is_simulated = False
                    temp = incoming_updates.get("temperature", self.current_data["temperature"])
                    hum = incoming_updates.get("humidity", self.current_data["humidity"])
                    press = incoming_updates.get("pressure", self.current_data["pressure"])
                    mq_raw = incoming_updates.get("mq_raw", self.current_data["mq_raw"])
                    # PM columns present => use them (None = no PMS5003); absent => keep previous
                    pm25 = incoming_updates["pm25"] if "pm25" in incoming_updates else self.current_data.get("pm25_est")
                    pm10 = incoming_updates["pm10"] if "pm10" in incoming_updates else self.current_data.get("pm10_est")
                    pm1 = incoming_updates["pm1"] if "pm1" in incoming_updates else self.current_data.get("pm1")
                    fan = incoming_updates.get("fan", self.current_data.get("fan", 0))
                    source = f"Live Hardware ({self.port})"

                # 3. Compute gas estimates & AQI
                gases = self.calculate_gases(mq_raw)
                aqi_info = self.calculate_aqi(temp, hum, mq_raw, pm25, pm10)

                # Assemble full record
                now = datetime.now()
                record = {
                    "timestamp": now.isoformat(),
                    "time_str": now.strftime("%H:%M:%S"),
                    "temperature": round(temp, 2),
                    "temp_f": round(temp * 9/5 + 32, 1),
                    "humidity": round(hum, 2),
                    "pressure": round(press, 2),
                    "mq_raw": mq_raw,
                    "voltage": gases["voltage"],
                    "rs": gases["rs"],
                    "ratio": gases["ratio"],
                    "co2": gases["co2"],
                    "co": gases["co"],
                    "nh3": gases["nh3"],
                    "benzene": gases["benzene"],
                    "alcohol": gases["alcohol"],
                    "nox": gases["nox"],
                    "smoke": gases["smoke"],
                    "voc_index": gases["voc_index"],
                    "gas_index": gases["gas_index"],
                    "pm1": pm1,
                    "pm25_est": aqi_info["pm25_est"],
                    "pm10_est": aqi_info["pm10_est"],
                    "aqi": aqi_info["aqi"],
                    "aqi_category": aqi_info["aqi_category"],
                    "aqi_color": aqi_info["aqi_color"],
                    "ventilation_status": aqi_info["ventilation_status"],
                    "ventilation_message": aqi_info["ventilation_message"],
                    "fan": fan,
                    "source": source
                }
                for key in PMS_FIELDS:
                    record[key] = None if not read_success else incoming_updates.get(key, self.current_data.get(key))

                record["pm25_epa"] = epa_corrected_pm25(record.get("cf_pm25"),
                                                        record.get("pms_hum") or record.get("humidity"))
                self.current_data = record
                self.history.append(record)
                if len(self.history) > self.max_history:
                    self.history.pop(0)

            # Log real hardware readings only (never simulated ones), at most every 5 seconds
            if read_success and time.time() - last_log_time >= 5:
                self.log_reading_to_csv(record)
                last_log_time = time.time()

            time.sleep(1.5)

sensor_manager = SensorDataManager()

# ==========================================
# Flask Routes
# ==========================================

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def api_status():
    with sensor_manager.lock:
        return jsonify({
            "is_connected": sensor_manager.is_connected,
            "is_simulated": sensor_manager.is_simulated,
            "port": sensor_manager.port,
            "baudrate": sensor_manager.baudrate,
            "total_readings": len(sensor_manager.history),
            "log_file": LOG_FILE,
            "last_data_age_s": None if sensor_manager.last_data_time is None else round(time.time() - sensor_manager.last_data_time, 1),
            "r0": R0,
            "calibrated": os.path.exists(CAL_FILE),
            "mq_powered_min": None if sensor_manager.mq_powered_since is None
                else round((time.time() - sensor_manager.mq_powered_since) / 60)
        })

@app.route("/api/ports")
def api_ports():
    ports = sensor_manager.get_available_ports()
    return jsonify({"ports": ports})

@app.route("/api/connect", methods=["POST"])
def api_connect():
    data = request.get_json() or {}
    action = data.get("action", "connect")
    port = data.get("port", "COM11")
    baud = data.get("baudrate", 115200)

    if action == "connect":
        success, msg = sensor_manager.connect(port, baud)
    else:
        success, msg = sensor_manager.disconnect()

    return jsonify({"success": success, "message": msg, "is_connected": sensor_manager.is_connected})

@app.route("/api/calibrate", methods=["POST"])
def api_calibrate():
    """Set R0 = median Rs of the last 10 minutes. Call this with the sensor warmed up (24h+) in clean air."""
    global R0
    with sensor_manager.lock:
        if sensor_manager.is_simulated or sensor_manager.last_data_time is None:
            return jsonify({"success": False, "message": "Needs live hardware data"}), 400
    rows = recent_log_rows(CAL_WINDOW_S)
    if len(rows) < CAL_MIN_READINGS:
        return jsonify({"success": False, "message": f"Only {len(rows)} readings in the last 10 minutes, "
                                                     f"{CAL_MIN_READINGS} needed"}), 400
    rs_values = [float(r["rs"]) for r in rows]
    record = {
        "r0": round(statistics.median(rs_values), 2),
        "calibrated_at": datetime.now().isoformat(),
        "readings": len(rs_values),
        "rs_min": min(rs_values),
        "rs_max": max(rs_values),
        "temperature": rows[-1]["temperature"],
        "humidity": rows[-1]["humidity"],
        "pm25": rows[-1]["pm25"],
        "previous_r0": R0,
    }
    with open(CAL_FILE, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    R0 = record["r0"]
    return jsonify({"success": True, **record})

def with_temp_offset(record):
    """Copy of a record for display: temperature minus the BME280 offset, raw value kept alongside."""
    out = dict(record)
    raw = record.get("temperature")
    if raw is not None:
        out["temperature_raw"] = raw
        out["temperature"] = round(raw - BME_TEMP_OFFSET_C, 2)
        out["temp_f"] = round(out["temperature"] * 9 / 5 + 32, 1)
    out["temp_offset_c"] = BME_TEMP_OFFSET_C
    return out

@app.route("/api/data")
def api_data():
    with sensor_manager.lock:
        return jsonify(with_temp_offset(sensor_manager.current_data))

@app.route("/api/history")
def api_history():
    with sensor_manager.lock:
        return jsonify({
            "history": [with_temp_offset(r) for r in sensor_manager.history[-360:]]
        })

TREND_RANGES = {60: 1, 360: 5, 1440: 15}   # minutes shown -> minutes averaged into one point

@app.route("/api/trend")
def api_trend():
    """AQI averaged into buckets from the log, for the dashboard's 1 h, 6 h and 24 h views."""
    try:
        minutes = int(request.args.get("minutes", 60))
    except ValueError:
        minutes = 60
    if minutes not in TREND_RANGES:
        return jsonify({"error": f"minutes must be one of {sorted(TREND_RANGES)}"}), 400
    bucket = TREND_RANGES[minutes]
    cutoff = datetime.now() - timedelta(minutes=minutes)
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            header = f.readline().strip().split(",")
            tail = collections.deque(f, maxlen=minutes * 14)   # at most ~12 readings a minute
    except FileNotFoundError:
        return jsonify({"bucket_min": bucket, "points": []})
    ti, ai, pi = header.index("timestamp"), header.index("aqi"), header.index("pm25")
    sums = {}
    for line in tail:
        parts = line.rstrip("\n").split(",")
        try:
            t = datetime.fromisoformat(parts[ti])
            if t < cutoff or not parts[pi] or not parts[ai]:
                continue   # AQI without particle data would read falsely low
            key = t.replace(minute=(t.minute // bucket) * bucket if bucket < 60 else 0, second=0, microsecond=0)
            s = sums.setdefault(key, [0.0, 0])
            s[0] += float(parts[ai]); s[1] += 1
        except (ValueError, IndexError):
            continue
    points = [[k.isoformat(), round(v[0] / v[1], 1)] for k, v in sorted(sums.items())]
    return jsonify({"bucket_min": bucket, "minutes": minutes, "points": points})

_forecast_cache = {"at": 0.0, "body": None, "model_mtime": None, "bundle": None}

def forecast_confidence(metrics):
    """How far to trust the forecast, from the held-out test: typical error and skill against 'stays the same'.

    Skill is the share of the better baseline's error that the model removes. Below the 5% bar the
    level is 'low'; at or below zero it is 'none' (no better than repeating the current value).
    """
    results = metrics["results"]
    model_mae = results[metrics["best_model"]]["mae"]
    baseline_mae = min(r["mae"] for n, r in results.items() if n.startswith("Baseline"))
    skill = (baseline_mae - model_mae) / baseline_mae * 100 if baseline_mae else 0.0
    level = "moderate" if skill >= saaans_forecast.MIN_IMPROVEMENT * 100 else ("low" if skill > 0 else "none")
    return {"typical_error": round(model_mae, 1), "skill_pct": round(skill, 1), "confidence": level,
            "skill_needed_pct": saaans_forecast.MIN_IMPROVEMENT * 100}

def build_forecast():
    """Forecast state for the dashboard. A number is only offered when a saved model clearly beats the baselines."""
    if not HAS_FORECAST:
        return {"state": "unavailable", "message": "Install pandas, scikit-learn and joblib to use the forecast."}
    body = {"horizon_min": saaans_forecast.HORIZON_MIN,
            "hours_to_train": saaans_forecast.MIN_HOURS_TO_TRAIN,
            "hours_for_result": saaans_forecast.HOURS_FOR_RESULT}
    try:
        body["hours_logged"] = round(saaans_forecast.hours_of_data(saaans_forecast.load_log()), 2)
    except FileNotFoundError:
        body["hours_logged"] = 0.0

    model_path = os.path.join(saaans_forecast.OUT_DIR, saaans_forecast.MODEL_NAME)
    if not os.path.exists(model_path):
        body["state"] = "no_model"
        return body
    mtime = os.path.getmtime(model_path)
    if _forecast_cache["model_mtime"] != mtime:
        _forecast_cache["bundle"] = joblib.load(model_path)
        _forecast_cache["model_mtime"] = mtime
    bundle = _forecast_cache["bundle"]
    body["metrics"] = bundle["metrics"]
    body.update(forecast_confidence(bundle["metrics"]))
    body["forecast"] = saaans_forecast.predict(bundle)
    if not body["forecast"]:
        body["state"] = "gap"
    else:
        # A forecast that does not beat the baselines is still shown, but marked unverified
        body["state"] = "ready" if bundle["metrics"]["beats_baseline"] else "unverified"
    return body

@app.route("/api/forecast")
def api_forecast():
    now = time.time()
    if _forecast_cache["body"] is None or now - _forecast_cache["at"] > 30:
        try:
            _forecast_cache["body"] = build_forecast()
        except Exception as e:
            _forecast_cache["body"] = {"state": "unavailable", "message": f"Forecast failed: {e}"}
        _forecast_cache["at"] = now
    return jsonify(_forecast_cache["body"])

_series_cache = {"at": 0.0, "body": None}

_retrain = {"running": False, "started": None, "finished": None, "result": None, "error": None}
_retrain_lock = threading.Lock()

def _run_retrain():
    try:
        result = saaans_forecast.train()
        _retrain["result"] = {k: result.get(k) for k in
                              ("status", "hours", "best_model", "beats_baseline", "decision", "champion_mae", "results")}
        _retrain["error"] = None
    except Exception as e:
        _retrain["result"], _retrain["error"] = None, str(e)
    finally:
        _forecast_cache["at"] = 0.0     # next forecast request reloads the model and recomputes
        _series_cache["at"] = 0.0
        _retrain["finished"] = time.time()
        _retrain["running"] = False

@app.route("/api/forecast/retrain", methods=["GET", "POST"])
def api_forecast_retrain():
    """POST starts a retrain in the background (the same gate as the nightly run); GET reports progress."""
    if request.method == "POST":
        if not HAS_FORECAST:
            return jsonify({"success": False, "message": "Install pandas, scikit-learn and joblib first."}), 400
        with _retrain_lock:
            if _retrain["running"]:
                return jsonify({"success": True, "already_running": True})
            _retrain.update(running=True, started=time.time(), finished=None, result=None, error=None)
        threading.Thread(target=_run_retrain, daemon=True).start()
        return jsonify({"success": True, "already_running": False})
    return jsonify(_retrain)

@app.route("/api/forecast/refresh", methods=["POST"])
def api_forecast_refresh():
    """Drop the 30 s caches so the next request recomputes the forecast from the newest readings."""
    _forecast_cache["at"] = 0.0
    _series_cache["at"] = 0.0
    return jsonify({"success": True})

@app.route("/api/forecast/series")
def api_forecast_series():
    """Last 3 hours of measured PM2.5 with the forecast line, for the dashboard graph."""
    now = time.time()
    if _series_cache["body"] is None or now - _series_cache["at"] > 30:
        try:
            state = api_forecast().get_json()["state"]   # refreshes the model cache too
            if state in ("ready", "unverified", "gap"):
                body = saaans_forecast.forecast_series(_forecast_cache["bundle"])
                body["typical_error"] = forecast_confidence(_forecast_cache["bundle"]["metrics"])["typical_error"]
            else:
                body = {"measured": [], "forecast": []}
            body["state"] = state
        except Exception as e:
            body = {"state": "unavailable", "measured": [], "forecast": [], "message": f"Forecast graph failed: {e}"}
        _series_cache["body"], _series_cache["at"] = body, now
    return jsonify(_series_cache["body"])

@app.route("/api/export")
def api_export():
    if os.path.exists(LOG_FILE):
        return send_file(LOG_FILE, as_attachment=True, download_name="sensor_log.csv", mimetype="text/csv")
    return jsonify({"error": "No log file found"}), 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"==================================================")
    print(f"  SAAANS IAQ Web Dashboard")
    print(f"  Running on: http://localhost:{port}")
    print(f"==================================================")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
