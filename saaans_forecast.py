"""
SAAANS - PM2.5 forecast from real logged readings only.

Trains on saans_log.csv and predicts PM2.5 30 minutes ahead. Every model is scored against two
simple guesses: "the value stays the same" and "the recent 30-minute average". A model only counts
as useful if it clearly beats both.

    python saaans_forecast.py                 train on saans_log.csv
    python saaans_forecast.py --log x.csv --out dir     train on another file (for testing)
"""

import argparse
import json
import os
import shutil
from collections import deque
from datetime import datetime
from io import StringIO

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import HuberRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "saans_log.csv")
OUT_DIR = os.path.join(BASE_DIR, "saaans_output")
MODEL_NAME = "forecast_model.joblib"
METRICS_NAME = "forecast_metrics.json"

HORIZON_MIN = 30
MAX_FILL_MIN = 2           # bridge short dropouts only; longer gaps stay missing
MIN_HOURS_TO_TRAIN = 3     # below this the split has too few points to score
HOURS_FOR_RESULT = 72      # below this a score is a pipeline test, not a project result
TEST_FRACTION = 0.2
MIN_IMPROVEMENT = 0.05     # must cut MAE by 5% against the better baseline; noise can win by less
LOG_COLUMNS = ["timestamp", "pm25", "pm10", "n03", "n25", "temperature", "humidity", "pressure"]

# MQ-135 is left out until it is burned in and calibrated: its drift would look like signal.
# Features describe how PM2.5 is moving (recent changes, distance from its own averages), not its
# level, so a model trained at 150 ug/m3 still applies at 600. Picked on a validation stretch carved
# from the training period on 13 Nov; the test window was not used for the choice.
FEATURES = [
    "pm25", "pm25_d1", "pm25_d2", "pm25_d5", "pm25_d10",
    "pm25_dev15", "pm25_dev30", "pm25_dev60", "pm25_std15",
    "n03_d5", "humidity_d10", "hour_sin", "hour_cos",
]
HISTORY_NEEDED_MIN = 60    # longest lag / rolling window in FEATURES
SHRINK = {"Gradient boosting": 0.75, "Robust linear": 0.5}   # share of the predicted change used, set on validation
# The models learn the change over the horizon, not the level. Tree models cannot predict outside the
# range they were trained on, so a level model pulled festival spikes (600+ ug/m3) back to the average.


def model_predict(bundle, X):
    """Forecast PM2.5 level for rows X. Older saved bundles predict the level directly."""
    out = bundle["model"].predict(X[bundle["features"]])
    if bundle.get("target") == "change":
        out = X["pm25"].to_numpy() + bundle.get("shrink", 1.0) * out
    return out


def load_log(path=LOG_FILE, last_lines=None):
    """Read the log and average it to one row per minute. Missing minutes stay as NaN."""
    with open(path, "r", encoding="utf-8") as f:
        header = f.readline()
        lines = deque(f, maxlen=last_lines) if last_lines else f.readlines()
    df = pd.read_csv(StringIO(header + "".join(lines)), usecols=LOG_COLUMNS, parse_dates=["timestamp"])
    df = df.dropna(subset=["pm25"])
    # Older firmware repeated the last frame when the sensor stopped. Particle counts change on
    # every real frame, so an identical count means a frozen reading, not a measurement.
    df = df[~(df["n03"].notna() & (df["n03"] == df["n03"].shift()))]
    if df.empty:
        return df
    minutes = df.set_index("timestamp").sort_index().resample("1min").mean()
    return minutes.ffill(limit=MAX_FILL_MIN)


def hours_of_data(minutes):
    return 0.0 if minutes.empty else float(minutes["pm25"].notna().sum()) / 60.0


def make_features(minutes):
    """Rolling windows use min_periods = full window, so a gap never produces a partial feature."""
    df = minutes.copy()
    for lag in (5, 10, 15, 30):
        df[f"pm25_lag{lag}"] = df["pm25"].shift(lag)
    df["pm25_mean15"] = df["pm25"].rolling(15, min_periods=15).mean()
    df["pm25_mean30"] = df["pm25"].rolling(30, min_periods=30).mean()
    df["pm25_slope10"] = (df["pm25"] - df["pm25"].shift(10)) / 10.0
    for lag in (1, 2, 5, 10):
        df[f"pm25_d{lag}"] = df["pm25"] - df["pm25"].shift(lag)
    df["pm25_dev15"] = df["pm25"] - df["pm25_mean15"]
    df["pm25_dev30"] = df["pm25"] - df["pm25_mean30"]
    df["pm25_dev60"] = df["pm25"] - df["pm25"].rolling(60, min_periods=60).mean()
    df["pm25_std15"] = df["pm25"].rolling(15, min_periods=15).std()
    df["n03_d5"] = df["n03"] - df["n03"].shift(5)
    df["humidity_d10"] = df["humidity"] - df["humidity"].shift(10)
    hour = df.index.hour + df.index.minute / 60.0
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    return df


def score(y_true, y_pred):
    return {
        "mae": round(float(mean_absolute_error(y_true, y_pred)), 2),
        "rmse": round(float(np.sqrt(mean_squared_error(y_true, y_pred))), 2),
        "r2": round(float(r2_score(y_true, y_pred)), 3),
    }


def train(log_path=LOG_FILE, out_dir=OUT_DIR):
    """Train and save the model. Returns the metrics, or a dict with status 'not_enough_data'."""
    minutes = load_log(log_path)
    hours = hours_of_data(minutes)
    if hours < MIN_HOURS_TO_TRAIN:
        return {"status": "not_enough_data", "hours": round(hours, 2), "hours_needed": MIN_HOURS_TO_TRAIN}

    df = make_features(minutes)
    df["target"] = df["pm25"].shift(-HORIZON_MIN)   # index is a regular 1-minute grid
    data = df.dropna(subset=FEATURES + ["target"])

    # Time order, never shuffled. Rows within one horizon of the split are dropped,
    # otherwise training targets would overlap the test period.
    split_at = data.index[int(len(data) * (1 - TEST_FRACTION))]
    train_rows = data[data.index < split_at - pd.Timedelta(minutes=HORIZON_MIN)]
    test_rows = data[data.index >= split_at]
    if len(train_rows) < 30 or len(test_rows) < 10:
        return {"status": "not_enough_data", "hours": round(hours, 2), "hours_needed": MIN_HOURS_TO_TRAIN,
                "detail": "Too few continuous stretches to split. Gaps in the log remove rows."}

    # Both are fitted on absolute error, the same measure they are scored on
    candidates = {
        "Gradient boosting": GradientBoostingRegressor(loss="absolute_error", n_estimators=300, max_depth=3,
                                                       learning_rate=0.05, subsample=0.8, random_state=42),
        "Robust linear": make_pipeline(StandardScaler(), HuberRegressor(alpha=1e-3, max_iter=500)),
    }
    results = {
        "Baseline: stays the same": score(test_rows["target"], test_rows["pm25"]),
        "Baseline: 30-min average": score(test_rows["target"], test_rows["pm25_mean30"]),
    }
    fitted = {}
    for name, model in candidates.items():
        model.fit(train_rows[FEATURES], train_rows["target"] - train_rows["pm25"])
        fitted[name] = model
        results[name] = score(test_rows["target"],
                              model_predict({"model": model, "features": FEATURES, "target": "change",
                                             "shrink": SHRINK[name]}, test_rows))

    best = min(fitted, key=lambda n: results[n]["mae"])
    baseline_mae = min(r["mae"] for name, r in results.items() if name.startswith("Baseline"))
    metrics = {
        "status": "trained",
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "horizon_min": HORIZON_MIN,
        "hours": round(hours, 2),
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "test_pm25_range": [float(test_rows["target"].min()), float(test_rows["target"].max())],
        "target": "change",
        "stage": "result" if hours >= HOURS_FOR_RESULT else "pipeline_test",
        "best_model": best,
        "beats_baseline": results[best]["mae"] < baseline_mae * (1 - MIN_IMPROVEMENT),
        "results": results,
    }

    os.makedirs(out_dir, exist_ok=True)
    model_path = os.path.join(out_dir, MODEL_NAME)
    decision, champion_mae = decide_promotion(model_path, metrics, results[best]["mae"], baseline_mae, test_rows)
    metrics["decision"] = decision
    metrics["champion_mae"] = champion_mae
    log_retrain(out_dir, metrics, results[best]["mae"], baseline_mae)

    if decision.startswith("kept_champion"):
        return metrics   # the live model and its metrics file stay as they are
    if os.path.exists(model_path):   # keep the model being replaced
        archive = os.path.join(out_dir, "models")
        os.makedirs(archive, exist_ok=True)
        stamp = datetime.fromtimestamp(os.path.getmtime(model_path)).strftime("%Y%m%d_%H%M%S")
        shutil.copy2(model_path, os.path.join(archive, f"forecast_model_{stamp}.joblib"))
    joblib.dump({"model": fitted[best], "features": FEATURES, "target": "change",
                 "shrink": SHRINK[best], "metrics": metrics}, model_path)
    with open(os.path.join(out_dir, METRICS_NAME), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    return metrics


def passes(mae, baseline_mae):
    return mae < baseline_mae * (1 - MIN_IMPROVEMENT)


def decide_promotion(model_path, metrics, new_mae, baseline_mae, test_rows):
    """Champion/challenger: the new model replaces the live one only if it is the better answer.

    The live model is scored on the same newest test window as the new one, so the two are compared
    on equal terms. Returns (decision, champion_mae on that window or None).
    """
    new_passes = metrics["beats_baseline"]
    champion = None
    if os.path.exists(model_path):
        try:
            champion = joblib.load(model_path)
        except Exception:
            champion = None   # unreadable file: treat as no champion
    if champion is None:
        return ("promoted_first_model" if new_passes else "saved_no_skill_model"), None

    try:
        champ_mae = score(test_rows["target"], model_predict(champion, test_rows))["mae"]
    except Exception:
        return ("promoted_champion_unscorable" if new_passes else "saved_no_skill_model"), None

    champ_passes = passes(champ_mae, baseline_mae)
    if new_passes and (not champ_passes or new_mae <= champ_mae):
        return "promoted", champ_mae
    if new_passes:
        return "kept_champion_better", champ_mae
    if champ_passes:
        return "kept_champion_new_failed", champ_mae
    return "withdrawn_no_skill", champ_mae   # neither passes: the dashboard shows no skill


def log_retrain(out_dir, metrics, new_mae, baseline_mae):
    """One line per retrain run, kept so the report can show how the model changed over time."""
    path = os.path.join(out_dir, "retrain_log.csv")
    new_file = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        if new_file:
            f.write("run_at,hours,best_model,new_mae,baseline_mae,champion_mae,decision\n")
        f.write(f"{metrics['trained_at']},{metrics['hours']},{metrics['best_model']},{new_mae},"
                f"{baseline_mae},{metrics['champion_mae'] if metrics['champion_mae'] is not None else ''},{metrics['decision']}\n")


def predict(bundle, log_path=LOG_FILE):
    """Forecast from the newest minute. Returns None unless the last 30 minutes are unbroken."""
    # 12 readings a minute at the firmware's 5 s interval
    minutes = load_log(log_path, last_lines=(HISTORY_NEEDED_MIN + 5) * 12 * 2)
    if minutes.empty:
        return None
    row = make_features(minutes).iloc[[-1]]
    if row[bundle["features"]].isna().any(axis=None):
        return None
    made_at = row.index[0]
    return {
        "pm25": round(float(model_predict(bundle, row)[0]), 1),
        "current_pm25": round(float(row["pm25"].iloc[0]), 1),
        "made_at": made_at.isoformat(),
        "for_time": (made_at + pd.Timedelta(minutes=HORIZON_MIN)).isoformat(),
    }


def forecast_series(bundle, hours=3, log_path=LOG_FILE):
    """Measured PM2.5 for the last `hours`, and the model's forecast drawn at the time it points to.

    Each forecast is made from readings up to minute t and plotted at t + HORIZON_MIN, so the
    forecast line can be read against what actually happened, and its last HORIZON_MIN minutes
    lie in the future. Minutes without 30 unbroken minutes behind them get no forecast.
    """
    span = hours * 60
    minutes = load_log(log_path, last_lines=(span + HISTORY_NEEDED_MIN + 5) * 12)
    if minutes.empty:
        return {"measured": [], "forecast": []}
    feats = make_features(minutes).iloc[-span:]
    ready = feats.dropna(subset=bundle["features"])
    horizon = pd.Timedelta(minutes=HORIZON_MIN)
    predicted = model_predict(bundle, ready) if len(ready) else []
    return {
        "horizon_min": HORIZON_MIN,
        "measured": [[t.isoformat(), round(float(v), 1)] for t, v in feats["pm25"].dropna().items()],
        "forecast": [[(t + horizon).isoformat(), round(float(v), 1)] for t, v in zip(ready.index, predicted)],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train the SAAANS PM2.5 forecast")
    parser.add_argument("--log", default=LOG_FILE)
    parser.add_argument("--out", default=OUT_DIR)
    args = parser.parse_args()

    out = train(args.log, args.out)
    if out["status"] == "not_enough_data":
        print(f"Not enough data: {out['hours']} h logged, {out['hours_needed']} h needed. Nothing was saved.")
        if "detail" in out:
            print(out["detail"])
    else:
        print(f"Trained on {out['hours']} h of readings ({out['train_rows']} train / {out['test_rows']} test minutes)")
        if out["stage"] == "pipeline_test":
            print(f"Pipeline test only: under {HOURS_FOR_RESULT} h of data, so don't report these scores as results.")
        for name, r in out["results"].items():
            print(f"  {name:26s} MAE {r['mae']:7.2f}   RMSE {r['rmse']:7.2f}   R2 {r['r2']:6.3f}")
        print(f"Best: {out['best_model']}. Clearly beats both baselines: {'yes' if out['beats_baseline'] else 'no'}")
        champ = out.get("champion_mae")
        print(f"Decision: {out['decision']}" + (f" (live model scored MAE {champ} on the same window)" if champ is not None else ""))
