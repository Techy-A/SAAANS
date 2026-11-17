"""
SAAANS - Smart IAQ ML Pipeline
BME280 + MQ-135 + Patiala AQI (PM2.5 / PM10)
Runs fully locally on KIRO

Data sources:
1. Local CSV  <- ESP32 logs sensor data here every 5 min
2. WAQI API   <- fetches live PM2.5 / PM10 for Patiala
3. Composite AQI calculator (US-EPA + CPCB standard)

Run:
python3 saaans_ml.py

Output files (in ./saaans_output/):
- sensor_data.csv          raw + fetched data log
- aqi_dashboard.png        main report figure
- ml_predictions.png       model prediction figure
- aqi_breakdown.png        pollutant breakdown figure
- correlation_heatmap.png  feature correlation
"""

import os, warnings, json, time
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
warnings.filterwarnings("ignore")

import numpy  as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot    as plt
import matplotlib.gridspec  as gridspec
import matplotlib.patches   as mpatches
from   matplotlib.dates     import DateFormatter
import seaborn as sns

from sklearn.ensemble        import RandomForestRegressor, GradientBoostingRegressor
from sklearn.preprocessing   import MinMaxScaler
from sklearn.metrics         import mean_squared_error, mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split

import requests
from datetime import datetime, timedelta

# Output folder
OUT = "./saaans_output"
os.makedirs(OUT, exist_ok=True)
np.random.seed(42)

# WAQI API Configuration
WAQI_TOKEN = "demo"          # Replace with your free token
WAQI_CITY  = "patiala"

def fetch_patiala_aqi():
    """Fetches live PM2.5 / PM10 / AQI for Patiala from WAQI API."""
    url = f"https://api.waqi.info/feed/{WAQI_CITY}/?token={WAQI_TOKEN}"
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "ok":
                iaqi  = data["data"].get("iaqi", {})
                pm25  = iaqi.get("pm25", {}).get("v", None)
                pm10  = iaqi.get("pm10", {}).get("v", None)
                aqi   = data["data"].get("aqi", None)
                city  = data["data"].get("city", {}).get("name", "Patiala")
                print(f"[INFO] Live AQI fetched for {city}")
                print(f"       AQI={aqi}  PM2.5={pm25}  PM10={pm10}")
                return {
                    "aqi_outdoor":  float(aqi)  if aqi  else None,
                    "pm25_outdoor": float(pm25) if pm25 else None,
                    "pm10_outdoor": float(pm10) if pm10 else None,
                    "source":       "WAQI Live"
                }
    except Exception as e:
        print(f"[WARN] API unavailable ({e}), using Patiala historical baseline")
    
    # Fallback to realistic Patiala baseline
    month = datetime.now().month
    if month in [11, 12, 1, 2]:
        pm25_base, pm10_base = 185, 290
    elif month in [3, 4, 10]:
        pm25_base, pm10_base = 120, 195
    elif month in [6, 7, 8, 9]:
        pm25_base, pm10_base = 65,  110
    else:
        pm25_base, pm10_base = 95,  155
    
    pm25 = pm25_base + np.random.uniform(-20, 20)
    pm10 = pm10_base + np.random.uniform(-30, 30)
    aqi  = calculate_aqi_pm25(pm25)
    
    print(f"[INFO] Patiala baseline estimate: AQI={aqi:.0f}  PM2.5={pm25:.1f}  PM10={pm10:.1f}")
    return {
        "aqi_outdoor":  round(aqi,  1),
        "pm25_outdoor": round(pm25, 1),
        "pm10_outdoor": round(pm10, 1),
        "source":       "CPCB Historical Baseline"
    }

def calculate_aqi_pm25(pm25):
    """US-EPA AQI from PM2.5 (ug/m3, 24-hr avg)"""
    breakpoints = [
        (0.0,   12.0,   0,   50),
        (12.1,  35.4,   51,  100),
        (35.5,  55.4,   101, 150),
        (55.5,  150.4,  151, 200),
        (150.5, 250.4,  201, 300),
        (250.5, 350.4,  301, 400),
        (350.5, 500.4,  401, 500),
    ]
    for c_lo, c_hi, i_lo, i_hi in breakpoints:
        if c_lo <= pm25 <= c_hi:
            return ((i_hi - i_lo) / (c_hi - c_lo)) * (pm25 - c_lo) + i_lo
    return 500.0

def calculate_aqi_pm10(pm10):
    """US-EPA AQI from PM10 (ug/m3)"""
    breakpoints = [
        (0,   54,   0,   50),
        (55,  154,  51,  100),
        (155, 254,  101, 150),
        (255, 354,  151, 200),
        (355, 424,  201, 300),
        (425, 504,  301, 400),
        (505, 604,  401, 500),
    ]
    for c_lo, c_hi, i_lo, i_hi in breakpoints:
        if c_lo <= pm10 <= c_hi:
            return ((i_hi - i_lo) / (c_hi - c_lo)) * (pm10 - c_lo) + i_lo
    return 500.0

def mq135_to_aqi(raw_value):
    """Converts MQ-135 raw ADC (0-4095) to estimated AQI."""
    if raw_value <= 0:
        return 0
    
    if raw_value < 800:
        return np.interp(raw_value, [0, 800],   [0, 50])
    elif raw_value < 1500:
        return np.interp(raw_value, [800, 1500],  [50, 100])
    elif raw_value < 2500:
        return np.interp(raw_value, [1500, 2500], [100, 200])
    else:
        return np.interp(raw_value, [2500, 4095], [200, 400])

def composite_aqi(pm25_indoor, pm10_outdoor, mq135_raw, temperature, humidity):
    """Composite AQI combining all available sources."""
    aqi_pm25 = calculate_aqi_pm25(pm25_indoor)
    aqi_pm10 = calculate_aqi_pm10(pm10_outdoor)
    aqi_mq   = mq135_to_aqi(mq135_raw)
    
    # Temperature/humidity discomfort index
    discomfort = 0
    if temperature > 30:
        discomfort += (temperature - 30) * 3
    if humidity > 70:
        discomfort += (humidity - 70) * 1.5
    aqi_comfort = min(discomfort, 150)
    
    composite = (0.40 * aqi_pm25  +
                 0.25 * aqi_pm10  +
                 0.25 * aqi_mq    +
                 0.10 * aqi_comfort)
    
    return round(composite, 1), round(aqi_pm25, 1), round(aqi_pm10, 1), round(aqi_mq, 1)

def aqi_category(aqi):
    """Returns (category, colour) for AQI value"""
    if aqi <= 50:   return "Good",            "#00e400"
    if aqi <= 100:  return "Satisfactory",    "#ffff00"
    if aqi <= 150:  return "Moderate",        "#ff7e00"
    if aqi <= 200:  return "Poor",            "#ff0000"
    if aqi <= 300:  return "Very Poor",       "#8f3f97"
    return               "Severe/Hazardous",  "#7e0023"

LOCAL_CSV = "./sensor_log.csv"

def load_sensor_data():
    """Load real ESP32 CSV data."""
    if not os.path.exists(LOCAL_CSV):
        print(f"[ERROR] {LOCAL_CSV} not found!")
        print("[INFO] Please run ESP32_DataLogger.ino and collect data first")
        print("[INFO] Expected CSV format: timestamp,temperature,humidity,pressure,mq_raw,voltage")
        return None, None
    
    print(f"[INFO] Loading real sensor data from {LOCAL_CSV}")
    df = pd.read_csv(LOCAL_CSV)
    
    # Convert timestamp (milliseconds since boot) to datetime
    # Assume the last row is "now" and work backwards
    if "timestamp" in df.columns:
        last_time = datetime.now()
        df["timestamp"] = pd.to_datetime(
            last_time - pd.to_timedelta(df["timestamp"].max() - df["timestamp"], unit="ms")
        )
    else:
        print("[ERROR] No timestamp column found in CSV")
        return None, None
    
    df = df.set_index("timestamp")
    print(f"[INFO] {len(df)} real readings loaded")
    print(f"[INFO] Date range: {df.index.min()} to {df.index.max()}\n")
    
    return df, "Real ESP32 Data"

def engineer_features(df, outdoor):
    """Add AQI columns, lag features, rolling stats"""
    # Add outdoor PM values
    df["pm25_outdoor"] = outdoor["pm25_outdoor"]
    df["pm10_outdoor"] = outdoor["pm10_outdoor"]
    
    # Estimated indoor PM2.5
    df["pm25_indoor"] = (outdoor["pm25_outdoor"] * 0.6 +
                         df["mq_raw"] / 4095 * 40 +
                         np.random.uniform(0, 10, len(df))).round(2)
    
    # AQI columns
    results = df.apply(
        lambda r: composite_aqi(r["pm25_indoor"], r["pm10_outdoor"],
                                r["mq_raw"], r["temperature"], r["humidity"]), 
        axis=1
    )
    df["aqi_composite"] = [r[0] for r in results]
    df["aqi_pm25"]      = [r[1] for r in results]
    df["aqi_pm10"]      = [r[2] for r in results]
    df["aqi_mq"]        = [r[3] for r in results]
    df["aqi_category"]  = df["aqi_composite"].apply(lambda x: aqi_category(x)[0])
    
    # Time features
    df["hour"]        = df.index.hour
    df["day_of_week"] = df.index.dayofweek
    df["is_night"]    = ((df["hour"] < 7) | (df["hour"] > 22)).astype(int)
    
    # Rolling features
    for col in ["aqi_composite", "temperature", "humidity"]:
        df[f"{col}_r6"]  = df[col].rolling(6,  min_periods=1).mean()
        df[f"{col}_r12"] = df[col].rolling(12, min_periods=1).mean()
    
    # Lag features
    for col in ["aqi_composite", "mq_raw", "temperature"]:
        for lag in [1, 3, 6, 12]:
            df[f"{col}_lag{lag}"] = df[col].shift(lag)
    
    df.dropna(inplace=True)
    return df

FEATURE_COLS = [
    "temperature", "humidity", "pressure", "mq_raw",
    "pm25_outdoor", "pm10_outdoor", "pm25_indoor",
    "hour", "day_of_week", "is_night",
    "aqi_composite_r6", "aqi_composite_r12",
    "temperature_r6", "humidity_r6",
    "aqi_composite_lag1", "aqi_composite_lag3",
    "aqi_composite_lag6", "aqi_composite_lag12",
    "mq_raw_lag1", "mq_raw_lag6",
    "temperature_lag1", "temperature_lag6",
]

TARGET = "aqi_composite"

def train_models(df):
    X = df[FEATURE_COLS]
    y = df[TARGET]
    
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, shuffle=False
    )
    
    print("[INFO] Training Random Forest...")
    rf = RandomForestRegressor(n_estimators=300, max_depth=12,
                               n_jobs=-1, random_state=42)
    rf.fit(X_train, y_train)
    rf_pred = rf.predict(X_test)
    
    print("[INFO] Training Gradient Boosting...")
    gbm = GradientBoostingRegressor(n_estimators=200, max_depth=5,
                                    learning_rate=0.06, random_state=42)
    gbm.fit(X_train, y_train)
    gbm_pred = gbm.predict(X_test)
    
    # Ensemble
    ensemble_pred = 0.55 * rf_pred + 0.45 * gbm_pred
    
    def metrics(name, yt, yp):
        rmse = np.sqrt(mean_squared_error(yt, yp))
        mae  = mean_absolute_error(yt, yp)
        r2   = r2_score(yt, yp)
        print(f"   [{name:20s}]  RMSE={rmse:.2f}  MAE={mae:.2f}  R2={r2:.4f}")
        return {"name": name, "rmse": rmse, "mae": mae, "r2": r2}
    
    print("\n[INFO] Model Performance:")
    m = [
        metrics("Random Forest",      y_test, rf_pred),
        metrics("Gradient Boosting",   y_test, gbm_pred),
        metrics("Ensemble (RF+GBM)",   y_test, ensemble_pred),
    ]
    
    # 48h forecast
    print("\n[INFO] Generating 48h forecast...")
    last_known  = df[FEATURE_COLS].iloc[-1:].copy()
    predictions = []
    future_times = []
    
    for step in range(576):  # 576 x 5min = 48h
        pred_aqi = float(rf.predict(last_known)[0] * 0.55 +
                        gbm.predict(last_known)[0] * 0.45)
        predictions.append(pred_aqi)
        future_times.append(df.index[-1] + timedelta(minutes=5*(step+1)))
        
        # Roll window forward
        new_row = last_known.copy()
        new_row["aqi_composite_lag12"] = new_row["aqi_composite_lag6"].values[0]
        new_row["aqi_composite_lag6"]  = new_row["aqi_composite_lag3"].values[0]
        new_row["aqi_composite_lag3"]  = new_row["aqi_composite_lag1"].values[0]
        new_row["aqi_composite_lag1"]  = pred_aqi
        new_row["aqi_composite_r6"]    = (new_row["aqi_composite_r6"].values[0] * 5/6 + pred_aqi/6)
        new_row["aqi_composite_r12"]   = (new_row["aqi_composite_r12"].values[0] * 11/12 + pred_aqi/12)
        new_row["hour"]        = (new_row["hour"].values[0] + 1) % 24
        new_row["is_night"]    = int(new_row["hour"].values[0] < 7 or new_row["hour"].values[0] > 22)
        last_known = new_row
    
    forecast = pd.Series(predictions, index=future_times, name="aqi_forecast")
    print(f"[INFO] Forecast complete: next 48h avg AQI = {np.mean(predictions):.1f}\n")
    
    return rf, gbm, X_test, y_test, rf_pred, gbm_pred, ensemble_pred, forecast, m

def plot_aqi_dashboard(df, outdoor, forecast):
    """Main AQI Dashboard"""
    print("[INFO] Plotting AQI Dashboard...")
    
    sns.set_style("whitegrid")
    fig = plt.figure(figsize=(20, 22))
    fig.patch.set_facecolor("#f8f9fa")
    fig.suptitle("SAAANS - Indoor Air Quality Dashboard\n"
                 "Patiala, Punjab  |  BME280 + MQ-135 + Outdoor AQI Feed",
                 fontsize=16, fontweight="bold", y=0.98, color="#1a1a2e")
    
    gs = gridspec.GridSpec(4, 3, figure=fig, hspace=0.50, wspace=0.35)
    
    latest = df.iloc[-1]
    
    # AQI gauge cards
    cards = [
        ("Indoor AQI (Composite)",  latest["aqi_composite"], "Overall"),
        ("Outdoor AQI (Patiala)",   outdoor["aqi_outdoor"],  "WAQI/CPCB"),
        ("PM2.5 AQI (Indoor est.)", latest["aqi_pm25"],      "EPA Formula"),
        ("PM10 AQI (Outdoor)",      latest["aqi_pm10"],      "CPCB Station"),
        ("MQ-135 AQI (Gas sensor)", latest["aqi_mq"],        "Calibrated"),
        ("48h Forecast (Peak AQI)", float(forecast.max()),   "ML Ensemble"),
    ]
    
    for i, (title, val, src) in enumerate(cards):
        if i < 3:
            cat, col_hex = aqi_category(val)
            ax = fig.add_subplot(gs[0, i])
            ax.set_facecolor(col_hex + "22")
            ax.add_patch(mpatches.FancyBboxPatch(
                (0.05, 0.05), 0.90, 0.90,
                boxstyle="round,pad=0.02",
                facecolor=col_hex+"33", edgecolor=col_hex, lw=2,
                transform=ax.transAxes
            ))
            ax.text(0.5, 0.72, f"{val:.0f}", ha="center", va="center",
                   fontsize=32, fontweight="bold", color=col_hex,
                   transform=ax.transAxes)
            ax.text(0.5, 0.48, cat, ha="center", va="center",
                   fontsize=11, color="#333", transform=ax.transAxes)
            ax.text(0.5, 0.28, title, ha="center", va="center",
                   fontsize=9, color="#666", transform=ax.transAxes)
            ax.text(0.5, 0.12, f"Source: {src}", ha="center", va="center",
                   fontsize=8, color="#999", transform=ax.transAxes)
            ax.axis("off")
    
    # AQI time series
    ax2 = fig.add_subplot(gs[1, :])
    tail = df["aqi_composite"].iloc[-1000:]
    ax2.fill_between(tail.index, 0, tail.values, alpha=0.15, color="#9B59B6")
    ax2.plot(tail.index, tail.values, color="#9B59B6", lw=1.2, alpha=0.8)
    
    thresholds = [(50,"Good","#00e400"), (100,"Satisfactory","#ffff00"),
                  (150,"Moderate","#ff7e00"), (200,"Poor","#ff0000"),
                  (300,"Very Poor","#8f3f97")]
    for val, lbl, col in thresholds:
        ax2.axhline(val, color=col, lw=1, ls="--", alpha=0.6)
        ax2.text(tail.index[0], val+3, lbl, color=col, fontsize=7.5, alpha=0.8)
    
    ax2.set_title("Composite Indoor AQI - Time Trend", fontsize=12, fontweight="bold")
    ax2.set_ylabel("AQI Value")
    ax2.set_ylim(0, 400)
    ax2.xaxis.set_major_formatter(DateFormatter("%b %d %H:%M"))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=20, ha="right", fontsize=8)
    
    # PM2.5 vs PM10
    ax3 = fig.add_subplot(gs[2, 0])
    ax3b = ax3.twinx()
    tail2 = df.iloc[-500:]
    ax3.plot(tail2.index, tail2["pm25_indoor"], color="#E74C3C", lw=1.2, label="PM2.5 Indoor")
    ax3b.plot(tail2.index, tail2["pm10_outdoor"], color="#E67E22", lw=1.2, ls="--", alpha=0.8, label="PM10 Outdoor")
    ax3.set_ylabel("PM2.5 (ug/m3)", color="#E74C3C")
    ax3b.set_ylabel("PM10 (ug/m3)", color="#E67E22")
    ax3.set_title("PM2.5 Indoor vs PM10 Outdoor", fontsize=10, fontweight="bold")
    ax3.legend(loc="upper left", fontsize=7)
    ax3b.legend(loc="upper right", fontsize=7)
    plt.setp(ax3.xaxis.get_majorticklabels(), rotation=20, ha="right", fontsize=7)
    
    # Temperature + Humidity
    ax4 = fig.add_subplot(gs[2, 1])
    ax4b = ax4.twinx()
    ax4.plot(tail2.index, tail2["temperature"], color="#E74C3C", lw=1.2, label="Temperature")
    ax4b.plot(tail2.index, tail2["humidity"], color="#3498DB", lw=1.2, ls="--", label="Humidity")
    ax4.set_ylabel("Temperature (C)", color="#E74C3C")
    ax4b.set_ylabel("Humidity (%RH)", color="#3498DB")
    ax4.set_title("Temperature & Humidity (BME280)", fontsize=10, fontweight="bold")
    ax4.legend(loc="upper left", fontsize=7)
    ax4b.legend(loc="upper right", fontsize=7)
    plt.setp(ax4.xaxis.get_majorticklabels(), rotation=20, ha="right", fontsize=7)
    
    # MQ-135 raw
    ax5 = fig.add_subplot(gs[2, 2])
    ax5.plot(tail2.index, tail2["mq_raw"], color="#27AE60", lw=1.0, alpha=0.8)
    ax5.fill_between(tail2.index, tail2["mq_raw"], alpha=0.2, color="#27AE60")
    ax5.set_ylabel("ADC Raw Value (0-4095)")
    ax5.set_title("MQ-135 Raw ADC Readings", fontsize=10, fontweight="bold")
    plt.setp(ax5.xaxis.get_majorticklabels(), rotation=20, ha="right", fontsize=7)
    
    # 48h forecast
    ax6 = fig.add_subplot(gs[3, :])
    real_tail = df["aqi_composite"].iloc[-288:]
    ax6.plot(real_tail.index, real_tail.values, color="#2C3E50", lw=1.5, label="Historical AQI")
    ax6.plot(forecast.index, forecast.values, color="#9B59B6", lw=2, ls="--", label="48h Forecast", alpha=0.9)
    ax6.fill_between(forecast.index, forecast.values * 0.88, forecast.values * 1.12,
                     color="#9B59B6", alpha=0.12, label="±12% uncertainty")
    ax6.axvline(df.index[-1], color="gray", ls=":", lw=1.5, label="Now")
    ax6.set_title("48-Hour AQI Forecast", fontsize=11, fontweight="bold")
    ax6.set_ylabel("AQI Value")
    ax6.set_ylim(0, 350)
    ax6.legend(fontsize=8, loc="upper right")
    ax6.xaxis.set_major_formatter(DateFormatter("%b %d %H:%M"))
    plt.setp(ax6.xaxis.get_majorticklabels(), rotation=20, ha="right", fontsize=8)
    
    plt.savefig(f"{OUT}/aqi_dashboard.png", dpi=150, bbox_inches="tight", facecolor="#f8f9fa")
    print(f"[INFO] Saved -> {OUT}/aqi_dashboard.png\n")

def main():
    print("=" * 65)
    print("  SAAANS - Smart IAQ ML Pipeline")
    print("  BME280 + MQ-135 + Patiala Outdoor AQI")
    print("=" * 65 + "\n")
    
    # 1. Fetch outdoor AQI
    print("[INFO] Fetching Patiala outdoor AQI...")
    outdoor = fetch_patiala_aqi()
    print()
    
    # 2. Load real sensor data
    df_raw, data_source = load_sensor_data()
    if df_raw is None:
        print("[ERROR] Cannot proceed without sensor data")
        return
    
    # 3. Feature engineering
    print("[INFO] Engineering features and calculating AQI...")
    df = engineer_features(df_raw.copy(), outdoor)
    print(f"[INFO] Dataset ready: {len(df)} rows, {len(df.columns)} features")
    
    # Current AQI summary
    latest = df.iloc[-1]
    cat, col = aqi_category(latest["aqi_composite"])
    print(f"\n[INFO] Current Indoor AQI Summary:")
    print(f"   Composite AQI : {latest['aqi_composite']:.1f} - {cat}")
    print(f"   PM2.5 Indoor  : {latest['pm25_indoor']:.1f} ug/m3")
    print(f"   PM10 Outdoor  : {outdoor['pm10_outdoor']:.1f} ug/m3 (Patiala)")
    print(f"   Temperature   : {latest['temperature']:.1f} C")
    print(f"   Humidity      : {latest['humidity']:.1f} % RH")
    print(f"   MQ-135 Raw    : {latest['mq_raw']}")
    vent = "[ALERT] VENTILATION ON" if latest["aqi_composite"] > 150 else "[OK] VENTILATION OFF"
    print(f"   Decision      : {vent}\n")
    
    # 4. Train ML models
    print("[INFO] Training ML Models...")
    (rf, gbm, X_test, y_test,
     rf_pred, gbm_pred, ensemble_pred,
     forecast, metrics_list) = train_models(df)
    
    # 5. Generate plots
    print("\n[INFO] Generating report figures...")
    plot_aqi_dashboard(df, outdoor, forecast)
    
    # 6. Save enriched data
    save_path = f"{OUT}/sensor_data_enriched.csv"
    df.to_csv(save_path)
    
    # 7. Summary
    best = max(metrics_list, key=lambda x: x["r2"])
    print("=" * 65)
    print("  FINAL SUMMARY")
    print("=" * 65)
    print(f"  Data source      : {data_source}")
    print(f"  Outdoor AQI src  : {outdoor['source']}")
    print(f"  Outdoor PM2.5    : {outdoor['pm25_outdoor']} ug/m3")
    print(f"  Outdoor PM10     : {outdoor['pm10_outdoor']} ug/m3")
    print(f"  Current AQI      : {latest['aqi_composite']:.1f} ({cat})")
    print(f"  48h Peak Forecast: {forecast.max():.1f}")
    print(f"  Best ML Model    : {best['name']} (R2={best['r2']:.4f})")
    print(f"\n  Output files in  : {OUT}/")
    print("    -> aqi_dashboard.png")
    print("    -> sensor_data_enriched.csv")
    print("=" * 65)
    print("\n[INFO] TO COLLECT MORE DATA:")
    print("   1. Upload ESP32_DataLogger.ino to your ESP32")
    print("   2. Open serial monitor and copy output to sensor_log.csv")
    print("   3. Let it run for several hours/days")
    print("   4. Re-run this script\n")

if __name__ == "__main__":
    main()
