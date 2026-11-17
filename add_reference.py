"""
SAAANS - record an outside PM reading next to the sensor's own average for the same hour.

    python add_reference.py 318 271                 NDTV said PM2.5 318, PM10 271, just now
    python add_reference.py 318 271 --source CPCB   name a different source
    python add_reference.py 318 271 --at "2026-11-04 01:35"   the time the reference was read

Appends to reference_checks.csv. After about 20 rows spread over day and night, the ratio column
shows whether the sensor and the reference differ by a steady factor.
"""

import argparse
import csv
import os
from datetime import datetime, timedelta

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "saans_log.csv")
REF_FILE = os.path.join(BASE_DIR, "reference_checks.csv")
WINDOW_MIN = 60
MIN_READINGS = 30   # of the 360 a full hour holds at one reading every 10 s
COLUMNS = ["checked_at", "source", "ref_pm25", "ref_pm10", "sensor_pm25", "sensor_pm10",
           "sensor_readings", "ratio_pm25", "humidity"]


def sensor_average(at):
    df = pd.read_csv(LOG_FILE, usecols=["timestamp", "pm25", "pm10", "humidity"], parse_dates=["timestamp"])
    window = df[(df["timestamp"] > at - timedelta(minutes=WINDOW_MIN)) & (df["timestamp"] <= at)].dropna(subset=["pm25"])
    return window


def main():
    parser = argparse.ArgumentParser(description="Record a reference PM reading beside the sensor's average")
    parser.add_argument("pm25", type=float)
    parser.add_argument("pm10", type=float)
    parser.add_argument("--source", default="NDTV Patiala")
    parser.add_argument("--at", default=None, help='time the reference was read, "YYYY-MM-DD HH:MM"')
    args = parser.parse_args()

    at = datetime.fromisoformat(args.at) if args.at else datetime.now()
    window = sensor_average(at)
    if len(window) < MIN_READINGS:
        print(f"Only {len(window)} sensor readings in the hour before {at:%Y-%m-%d %H:%M}, "
              f"{MIN_READINGS} needed. Nothing was saved.")
        return

    row = {
        "checked_at": at.isoformat(timespec="minutes"),
        "source": args.source,
        "ref_pm25": args.pm25,
        "ref_pm10": args.pm10,
        "sensor_pm25": round(window["pm25"].mean(), 1),
        "sensor_pm10": round(window["pm10"].mean(), 1),
        "sensor_readings": len(window),
        "ratio_pm25": round(window["pm25"].mean() / args.pm25, 2),
        "humidity": round(window["humidity"].mean(), 1),
    }
    new_file = not os.path.exists(REF_FILE) or os.path.getsize(REF_FILE) == 0
    with open(REF_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)

    rows = pd.read_csv(REF_FILE)
    print(f"Saved. Sensor PM2.5 {row['sensor_pm25']} vs {args.source} {args.pm25} (ratio {row['ratio_pm25']}).")
    print(f"{len(rows)} reference checks so far; about 20 spread over day and night are needed.")


if __name__ == "__main__":
    main()
