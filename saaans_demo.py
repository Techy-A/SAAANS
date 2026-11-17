"""
SAAANS Demo - Generate sample data and create all visualizations
Shows what the system will produce with real sensor data
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from datetime import datetime, timedelta
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, r2_score
from matplotlib.dates import DateFormatter, AutoDateLocator
import os

# Create output folder
OUT = "./saaans_output"
os.makedirs(OUT, exist_ok=True)

print("="*60)
print("  SAAANS DEMO - Generating Sample Data & Visualizations")
print("="*60)

# Generate data for April 30 - May 25, 2026
print("\n[1/5] Generating data for April 30 - May 25, 2026...")
start_date = datetime(2026, 4, 30, 0, 0, 0)
end_date = datetime(2026, 5, 25, 23, 55, 0)
periods = int((end_date - start_date).total_seconds() / 300)  # 5-min intervals
timestamps = pd.date_range(start=start_date, end=end_date, periods=periods)

# Simulate realistic patterns for Patiala in late April/May
hour = timestamps.hour
day_of_month = timestamps.day
t = np.linspace(0, len(timestamps) / (24*12) * 2 * np.pi, periods)

# Temperature: Patiala May - hot (28-38°C) with daily cycle
temp = 33 + 4*np.sin(t - np.pi/2) + np.random.randn(periods)*1.0

# Humidity: Lower in May (25-50%) inverse to temperature  
humidity = 38 - 8*np.sin(t - np.pi/2) + np.random.randn(periods)*3

# Pressure: around 970 hPa
pressure = 970 + 2*np.sin(t*0.1) + np.random.randn(periods)*0.5

# MQ-135: Higher during cooking/evening hours
occupancy = ((hour >= 7) & (hour <= 23)).astype(float)
cooking = np.zeros(periods)
for h in [7, 13, 19]:
    mask = (hour == h) | (hour == h + 1)
    cooking += mask.astype(float) * np.random.uniform(10, 40, periods)

mq_raw = (800 + 300*occupancy + cooking + 
          150*np.sin(t*2) + 80*np.random.randn(periods)).clip(300, 3500).astype(int)

voltage = (mq_raw * 3.3 / 4095).round(3)

# Outdoor PM (Patiala late April/May - pre-monsoon, moderate pollution)
# Higher in late April, slightly better in May
pm25_base = 95 - (day_of_month - 30) * 1.5  # Decreasing trend
pm10_base = 155 - (day_of_month - 30) * 2.0
pm25_outdoor = pm25_base + 20*np.sin(t*0.5) + np.random.randn(periods)*15
pm10_outdoor = pm10_base + 30*np.sin(t*0.5) + np.random.randn(periods)*20

# Indoor PM (infiltration + indoor sources)
pm25_indoor = pm25_outdoor * 0.6 + mq_raw/4095*30 + np.random.randn(periods)*5

# Create DataFrame
df = pd.DataFrame({
    'timestamp': timestamps,
    'temperature': temp.round(2),
    'humidity': humidity.round(2),
    'pressure': pressure.round(2),
    'mq_raw': mq_raw,
    'voltage': voltage,
    'pm25_outdoor': pm25_outdoor.round(1),
    'pm10_outdoor': pm10_outdoor.round(1),
    'pm25_indoor': pm25_indoor.round(1)
})

# Calculate AQI
def calculate_aqi_pm25(pm25):
    if pm25 <= 12.0: return pm25 * 50/12
    elif pm25 <= 35.4: return 50 + (pm25-12.1)*(50/23.3)
    elif pm25 <= 55.4: return 100 + (pm25-35.5)*(50/19.9)
    elif pm25 <= 150.4: return 150 + (pm25-55.5)*(50/94.9)
    else: return 200 + (pm25-150.5)*(100/99.9)

df['aqi_composite'] = df['pm25_indoor'].apply(calculate_aqi_pm25)
df['aqi_category'] = df['aqi_composite'].apply(
    lambda x: 'Good' if x<=50 else 'Satisfactory' if x<=100 else 
              'Moderate' if x<=150 else 'Poor'
)

# Save sample data
df.to_csv(f"{OUT}/sample_sensor_data.csv", index=False)
print(f"   Saved: {OUT}/sample_sensor_data.csv ({len(df)} readings)")

# Train simple ML model
print("\n[2/5] Training ML model for predictions...")
df['hour'] = df['timestamp'].dt.hour
df['day_of_week'] = df['timestamp'].dt.dayofweek
df['aqi_lag1'] = df['aqi_composite'].shift(1)
df['aqi_r6'] = df['aqi_composite'].rolling(6, min_periods=1).mean()
df = df.dropna()

X = df[['temperature', 'humidity', 'mq_raw', 'pm25_outdoor', 
        'hour', 'day_of_week', 'aqi_lag1', 'aqi_r6']]
y = df['aqi_composite']

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

rf = RandomForestRegressor(n_estimators=100, max_depth=10, random_state=42)
rf.fit(X_train, y_train)
y_pred = rf.predict(X_test)

rmse = np.sqrt(mean_squared_error(y_test, y_pred))
r2 = r2_score(y_test, y_pred)
print(f"   Model R² = {r2:.3f}, RMSE = {rmse:.2f}")

# Generate 48h forecast
print("\n[3/5] Generating 48-hour forecast...")
last_row = X.iloc[-1:].copy()
forecast_vals = []
forecast_times = []

for step in range(576):  # 48h in 5-min intervals
    pred = rf.predict(last_row)[0]
    forecast_vals.append(pred)
    forecast_times.append(df['timestamp'].iloc[-1] + timedelta(minutes=5*(step+1)))
    
    # Update features
    last_row['aqi_lag1'] = pred
    last_row['aqi_r6'] = (last_row['aqi_r6'].values[0]*5 + pred)/6
    last_row['hour'] = (last_row['hour'].values[0] + 1) % 24

forecast = pd.Series(forecast_vals, index=forecast_times)

# Create visualizations
print("\n[4/5] Creating visualizations...")

# Figure 1: Main Dashboard
fig = plt.figure(figsize=(18, 14))
fig.patch.set_facecolor('#f8f9fa')
fig.suptitle('SAAANS - Indoor Air Quality Dashboard\nPatiala, Punjab | April 30 - May 25, 2026',
             fontsize=14, fontweight='bold', y=0.98)

gs = gridspec.GridSpec(3, 2, figure=fig, hspace=0.35, wspace=0.25)

# AQI Time Series
ax1 = fig.add_subplot(gs[0, :])
tail = df.tail(500)
ax1.plot(tail['timestamp'], tail['aqi_composite'], color='#9B59B6', lw=1.5, alpha=0.8)
ax1.fill_between(tail['timestamp'], 0, tail['aqi_composite'], alpha=0.15, color='#9B59B6')
ax1.axhline(50, color='#00e400', ls='--', lw=1, alpha=0.6, label='Good')
ax1.axhline(100, color='#ffff00', ls='--', lw=1, alpha=0.6, label='Satisfactory')
ax1.axhline(150, color='#ff7e00', ls='--', lw=1, alpha=0.6, label='Moderate')
ax1.set_title('Indoor Air Quality Index - April 30 to May 25, 2026', fontsize=11, fontweight='bold')
ax1.set_ylabel('AQI Value')
ax1.set_ylim(0, 250)
ax1.legend(fontsize=8, loc='upper right')
ax1.grid(alpha=0.3)
ax1.xaxis.set_major_formatter(DateFormatter('%b %d'))
ax1.xaxis.set_major_locator(AutoDateLocator())
plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')

# Temperature & Humidity
ax2 = fig.add_subplot(gs[1, 0])
ax2b = ax2.twinx()
ax2.plot(tail['timestamp'], tail['temperature'], color='#E74C3C', lw=1.2, label='Temperature')
ax2b.plot(tail['timestamp'], tail['humidity'], color='#3498DB', lw=1.2, ls='--', label='Humidity')
ax2.set_ylabel('Temperature (°C)', color='#E74C3C')
ax2b.set_ylabel('Humidity (%)', color='#3498DB')
ax2.set_title('Temperature & Humidity (BME280)', fontsize=10, fontweight='bold')
ax2.legend(loc='upper left', fontsize=8)
ax2b.legend(loc='upper right', fontsize=8)
ax2.grid(alpha=0.3)
ax2.xaxis.set_major_formatter(DateFormatter('%b %d'))
plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

# MQ-135 Raw
ax3 = fig.add_subplot(gs[1, 1])
ax3.plot(tail['timestamp'], tail['mq_raw'], color='#27AE60', lw=1.2, alpha=0.8)
ax3.fill_between(tail['timestamp'], tail['mq_raw'], alpha=0.2, color='#27AE60')
ax3.set_ylabel('ADC Value (0-4095)')
ax3.set_title('MQ-135 Gas Sensor Readings', fontsize=10, fontweight='bold')
ax3.grid(alpha=0.3)
ax3.xaxis.set_major_formatter(DateFormatter('%b %d'))
plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha='right')

# 48h Forecast
ax4 = fig.add_subplot(gs[2, :])
historical = df['aqi_composite'].tail(288)
ax4.plot(historical.index, historical.values, color='#2C3E50', lw=1.5, label='Historical')
ax4.plot(forecast.index, forecast.values, color='#9B59B6', lw=2, ls='--', 
         label='48h Forecast', alpha=0.9)
ax4.fill_between(forecast.index, forecast.values*0.9, forecast.values*1.1,
                 color='#9B59B6', alpha=0.12, label='±10% uncertainty')
ax4.axvline(df['timestamp'].iloc[-1], color='gray', ls=':', lw=1.5, label='Now')
ax4.set_title('48-Hour AQI Forecast', fontsize=11, fontweight='bold')
ax4.set_ylabel('AQI Value')
ax4.legend(fontsize=8)
ax4.grid(alpha=0.3)
ax4.xaxis.set_major_formatter(DateFormatter('%b %d %H:%M'))
plt.setp(ax4.xaxis.get_majorticklabels(), rotation=45, ha='right')

plt.tight_layout()
plt.savefig(f"{OUT}/aqi_dashboard.png", dpi=150, bbox_inches='tight', facecolor='#f8f9fa')
print(f"   Saved: {OUT}/aqi_dashboard.png")

# Figure 2: Gas Breakdown
fig2, axes = plt.subplots(2, 2, figsize=(16, 12))
fig2.patch.set_facecolor('#f8f9fa')
fig2.suptitle('MQ-135 Gas Detection Analysis', fontsize=13, fontweight='bold')

# Calculate gas concentrations for last 500 readings
tail_mq = tail['mq_raw'].values
gases = {
    'CO2': [],
    'NH3': [],
    'Benzene': [],
    'VOC': []
}

for mq in tail_mq:
    voltage = (mq / 4095.0) * 3.3
    sensor_voltage = voltage * 2.833
    if sensor_voltage <= 0.1:
        sensor_voltage = 0.1
    RS = ((5.0 * 10.0) / sensor_voltage) - 10.0
    ratio = RS / 76.63
    
    gases['CO2'].append(max(400, min(10000, 116.6 * pow(ratio, -2.769))))
    gases['NH3'].append(max(0, min(300, 102.2 * pow(ratio, -2.473))))
    gases['Benzene'].append(max(0, min(10, 4.8 * pow(ratio, -2.68))))
    gases['VOC'].append(min(500, max(0, (ratio - 1) * 200)))

# CO2 trend
axes[0,0].plot(tail['timestamp'], gases['CO2'], color='#E74C3C', lw=1.2)
axes[0,0].axhline(1000, color='red', ls='--', lw=1, alpha=0.6, label='Poor ventilation')
axes[0,0].set_title('CO₂ Concentration', fontweight='bold')
axes[0,0].set_ylabel('ppm')
axes[0,0].legend(fontsize=8)
axes[0,0].grid(alpha=0.3)
axes[0,0].xaxis.set_major_formatter(DateFormatter('%b %d'))
plt.setp(axes[0,0].xaxis.get_majorticklabels(), rotation=45, ha='right')

# NH3 trend
axes[0,1].plot(tail['timestamp'], gases['NH3'], color='#3498DB', lw=1.2)
axes[0,1].set_title('NH₃ (Ammonia) Concentration', fontweight='bold')
axes[0,1].set_ylabel('ppm')
axes[0,1].grid(alpha=0.3)
axes[0,1].xaxis.set_major_formatter(DateFormatter('%b %d'))
plt.setp(axes[0,1].xaxis.get_majorticklabels(), rotation=45, ha='right')

# Benzene trend
axes[1,0].plot(tail['timestamp'], gases['Benzene'], color='#9B59B6', lw=1.2)
axes[1,0].axhline(1, color='orange', ls='--', lw=1, alpha=0.6, label='Safe limit')
axes[1,0].set_title('Benzene (VOC) Concentration', fontweight='bold')
axes[1,0].set_ylabel('ppm')
axes[1,0].legend(fontsize=8)
axes[1,0].grid(alpha=0.3)
axes[1,0].xaxis.set_major_formatter(DateFormatter('%b %d'))
plt.setp(axes[1,0].xaxis.get_majorticklabels(), rotation=45, ha='right')

# VOC Index
axes[1,1].plot(tail['timestamp'], gases['VOC'], color='#27AE60', lw=1.2)
axes[1,1].fill_between(tail['timestamp'], gases['VOC'], alpha=0.2, color='#27AE60')
axes[1,1].set_title('General VOC Index', fontweight='bold')
axes[1,1].set_ylabel('Index (0-500)')
axes[1,1].grid(alpha=0.3)
axes[1,1].xaxis.set_major_formatter(DateFormatter('%b %d'))
plt.setp(axes[1,1].xaxis.get_majorticklabels(), rotation=45, ha='right')

plt.tight_layout()
plt.savefig(f"{OUT}/gas_breakdown.png", dpi=150, bbox_inches='tight', facecolor='#f8f9fa')
print(f"   Saved: {OUT}/gas_breakdown.png")

# Figure 3: ML Model Performance
fig3, axes = plt.subplots(1, 2, figsize=(14, 5))
fig3.patch.set_facecolor('#f8f9fa')
fig3.suptitle('Machine Learning Model Performance', fontsize=13, fontweight='bold')

# Actual vs Predicted
axes[0].plot(range(len(y_test)), y_test.values, color='#2C3E50', lw=1.5, 
             label='Actual', alpha=0.8)
axes[0].plot(range(len(y_pred)), y_pred, color='#E74C3C', lw=1.5, ls='--',
             label='Predicted', alpha=0.8)
axes[0].set_title('Actual vs Predicted AQI', fontweight='bold')
axes[0].set_xlabel('Test Samples')
axes[0].set_ylabel('AQI Value')
axes[0].legend()
axes[0].grid(alpha=0.3)

# Scatter plot
axes[1].scatter(y_test, y_pred, alpha=0.4, s=20, color='#3498DB')
axes[1].plot([y_test.min(), y_test.max()], [y_test.min(), y_test.max()], 
             'k--', lw=2, label='Perfect prediction')
axes[1].set_title(f'Prediction Accuracy (R² = {r2:.3f})', fontweight='bold')
axes[1].set_xlabel('Actual AQI')
axes[1].set_ylabel('Predicted AQI')
axes[1].legend()
axes[1].grid(alpha=0.3)

plt.tight_layout()
plt.savefig(f"{OUT}/ml_performance.png", dpi=150, bbox_inches='tight', facecolor='#f8f9fa')
print(f"   Saved: {OUT}/ml_performance.png")

# Summary
print("\n[5/5] Summary")
print("="*60)
print(f"  Data period:         April 30 - May 25, 2026")
print(f"  Total readings:      {len(df)} (5-min intervals)")
print(f"  Current AQI:         {df['aqi_composite'].iloc[-1]:.1f} ({df['aqi_category'].iloc[-1]})")
print(f"  48h forecast peak:   {forecast.max():.1f}")
print(f"  ML Model R²:         {r2:.3f}")
print(f"\n  Output folder:       {OUT}/")
print(f"    - sample_sensor_data.csv")
print(f"    - aqi_dashboard.png")
print(f"    - gas_breakdown.png")
print(f"    - ml_performance.png")
print("="*60)
print("\n[DONE] Demo complete! Check the saaans_output folder for graphs.")
print("\nTo use with REAL data:")
print("  1. Run: python saaans_simple.py (collect data for hours/days)")
print("  2. Run: python saaans_ml.py (analyze real data)")
