# SAAANS — Smart Indoor Air Quality Monitor

**An ESP32 + three-sensor indoor air quality monitor with a live web dashboard, an extended AQI scale, and a self-retraining 30-minute PM2.5 forecast that is only shown when it provably beats a naive baseline.**

Capstone project, Thapar Institute of Engineering & Technology (2026).

---

## Why this exists

Public AQI stations report *outdoor* air, from a sensor kilometres away. The air people actually breathe is indoors, where cooking, incense, closed windows and festival smoke (Diwali readings in Delhi passed **1,200 µg/m³ PM2.5** during development) behave very differently. SAAANS measures that room directly, minute by minute, explains what it sees, and predicts where it is heading in the next half hour.

## Highlights

| | |
|---|---|
| **Live dashboard** | Flask web app: AQI dial, PM1/2.5/10, particle counts, temperature, humidity, pressure, gas index, sparklines and history charts. Works from any device on the LAN. |
| **Honest AQI past 500** | US EPA PM2.5 breakpoints, extended above 500.4 µg/m³ at the slope of the top band (1,000 µg/m³ → AQI ≈ 830) instead of flat-lining at 500. |
| **EPA-corrected PM2.5** | Shows the US EPA (Barkjohn et al., 2021) Plantower correction `0.52·CF1 − 0.086·RH + 5.75` alongside the raw value. |
| **Gas index** | MQ-135 reported as a room-relative index (`100 × R0/Rs`, 100 = this room's normal air) instead of invented per-gas ppm, with warm-up detection after power cuts. |
| **ML forecast** | Predicts PM2.5 30 minutes ahead from recent movement features. Retrains nightly; a new model is promoted **only** if it beats the "stays the same" baseline by ≥ 5 % on held-out data. |
| **Reference validation** | `add_reference.py` logs side-by-side checks against a public station so sensor drift and indoor/outdoor gaps are documented, not hidden. |
| **Self-healing** | Windows Task Scheduler watchdog restarts the server within minutes after a crash, sleep or reboot. |

## Current results

Measured on ~200 hours of real logged data (test PM2.5 range 148 – 1,224 µg/m³, 2,143 held-out minutes):

| Model | MAE (µg/m³) | RMSE | R² |
|---|---:|---:|---:|
| **Robust linear (Huber) — promoted** | **40.5** | **56.8** | **0.951** |
| Gradient boosting (absolute-error loss) | 43.3 | 62.0 | 0.941 |
| Baseline: stays the same | 47.8 | 61.7 | 0.942 |
| Baseline: 30-min average | 63.4 | 81.1 | 0.900 |

The promoted model is **15.3 % better** than persistence. All numbers come from the real sensor log; nothing is simulated.

---

## Hardware

| Part | Role | ESP32 connection |
|---|---|---|
| ESP32 DevKit | Microcontroller, USB serial to PC | — |
| Plantower **PMS5003T** | PM1.0 / PM2.5 / PM10, particle counts, temp, RH | UART2 — RX GPIO16, TX GPIO17, 5 V |
| Bosch **BME280** | Temperature, humidity, pressure | I²C — SDA GPIO21, SCL GPIO22, 3.3 V |
| **MQ-135** | Combined VOC / gas response | AOUT → GPIO34 through an 18 kΩ / 33 kΩ divider, 5 V heater |
| Relay + fan *(planned)* | Automatic ventilation | GPIO26 |

```
 PMS5003T ──UART──┐
 BME280  ───I²C───┤  ESP32 ──USB serial──►  iaq_server.py (Flask) ──►  Browser dashboard
 MQ-135  ──ADC────┘                              │
                                                  ├── saans_log.csv (1 row / minute)
                                                  └── saaans_forecast.py (train · evaluate · promote)
```

MQ-135 calibration (`R0`) is stored in `calibration.json` and can be redone from the dashboard in clean air.

## Software

```
├── iaq_server.py              Flask server: serial reader, AQI, gas index, EPA correction, REST API
├── saaans_forecast.py         Feature engineering, model training, champion/challenger promotion
├── add_reference.py           Log a reference-station comparison against the last 60 min of data
├── start_saaans.ps1 / .bat    Start-up script used by the scheduled watchdog
├── capture_serial_data.py     Stand-alone serial → CSV capture
├── saaans_ml.py, saaans_live.py, saaans_demo.py, saaans_simple.py   Earlier analysis / CLI tools
├── templates/index.html       Dashboard markup
├── static/css, static/js      Dashboard styling and logic (no front-end build step)
├── ESP32_SAANS/               Production firmware
├── ESP32_DataLogger/, ESP32_Live_Monitor/, MQ135_IAQ_Monitor/, BME280*/   Bring-up and test sketches
├── saaans_output/             Trained model, metrics, retrain history, figures
├── reference_checks.csv       Sensor vs. public station comparisons
└── MQ135_GAS_ESTIMATION_METHODOLOGY.md
```

### REST API

| Endpoint | Description |
|---|---|
| `GET /api/data` | Latest reading with AQI, gas index, EPA-corrected PM2.5 |
| `GET /api/status` | Serial connection, uptime, MQ warm-up minutes |
| `GET /api/history`, `/api/trend` | Recent series for charts |
| `GET /api/forecast` | 30-min forecast, confidence, model metrics |
| `GET /api/forecast/series` | Past forecasts vs. what actually happened |
| `POST /api/forecast/retrain` | Retrain now (still subject to the promotion rule) |
| `POST /api/calibrate` | Re-measure MQ-135 `R0` in clean air |
| `GET /api/ports`, `POST /api/connect` | Choose the serial port |
| `GET /api/export` | Download the log as CSV |

---

## How the forecast works

1. **Target** — the *change* in PM2.5 over the next 30 minutes, not the level. Level-predicting tree models could not extrapolate to festival-smoke values they had never seen; predicting change can.
2. **Features** — short and long momentum (`d1, d2, d5, d10`), deviation from 15/30/60-min means, 15-min volatility, change in the 0.3 µm particle count, humidity trend, time of day (sin/cos), and the current level.
3. **Candidates** — Huber-loss linear regression and absolute-error gradient boosting, each with a shrink factor chosen on a validation split taken from the training period only. The test window is never touched during selection.
4. **Promotion gate** — a challenger replaces the champion only if it beats persistence by **≥ 5 %** MAE. Otherwise the dashboard says so instead of showing a forecast that isn't useful.
5. **Retraining** — nightly at 03:40 via Task Scheduler; every run is appended to `saaans_output/retrain_log.csv`.
6. **Data guard** — a forecast needs 60 continuous minutes of history, so it stays blank right after a power cut rather than guessing.

## Reading the numbers honestly

- Low-cost optical sensors like the PMS5003T **over-read in humid, hazy air**. The EPA-corrected value is shown next to the raw one; it was fitted mostly below ~250 µg/m³, so treat it as an estimate at very high levels.
- Indoor readings are often **higher than the nearest public station**, especially at night with windows closed. `reference_checks.csv` records this directly (ratios between 0.3× and 3.4×), which is why no single fixed correction factor is applied.
- The MQ-135 cannot identify individual gases. The gas index compares the room to its own baseline; it is greyed out for two hours after power-on while the heater settles.

---

## Getting started

### 1. Flash the firmware

```bash
arduino-cli core install esp32:esp32
arduino-cli lib install "Adafruit BME280 Library"
arduino-cli compile --fqbn esp32:esp32:esp32 ESP32_SAANS
arduino-cli upload  --fqbn esp32:esp32:esp32 -p COM11 ESP32_SAANS
```

### 2. Run the server

```bash
pip install -r requirements.txt
python iaq_server.py
```

Open **http://localhost:5000** and pick the ESP32's serial port if it isn't found automatically. With no ESP32 connected it falls back to simulated data; set `SAANS_SIM=0` to disable that.

### 3. Train the forecast

The server trains automatically once enough history is logged (about 3 hours to start, 72 hours for reliable results). To retrain by hand:

```bash
python saaans_forecast.py
```

### 4. Keep it running (Windows, optional)

Create two Task Scheduler tasks: one that runs `start_saaans.ps1` at log-on and every 5 minutes (the script exits immediately if the server is already up), and one that runs `python saaans_forecast.py` nightly.

### 5. Add a reference check

```bash
python add_reference.py 145 210 --source "Public station name"
```

---

## Roadmap

- [x] Live dashboard, extended AQI, gas index, EPA correction
- [x] Self-retraining forecast with a promotion gate
- [x] Watchdog auto-restart and nightly retrain
- [ ] Relay-controlled ventilation fan with a dashboard switch
- [ ] LSTM forecaster compared against the current champion
- [ ] Push alerts when AQI crosses a threshold
- [ ] Phone access over Wi-Fi

## License

GNU General Public License v3.0 — see [LICENSE.txt](LICENSE.txt).
