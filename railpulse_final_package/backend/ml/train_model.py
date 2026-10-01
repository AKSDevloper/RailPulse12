"""
train_model.py
------------------
Phase 2, step 3: Model Training.

Trains a gradient-boosted regressor to predict actual_time_min (time to
clear a track section) from the engineered features. Uses XGBoost if it's
installed (the spec'd choice); if not, falls back automatically to
scikit-learn's GradientBoostingRegressor so the pipeline still runs.

Also derives an empirical 80% prediction interval (P10 / P90) from the
held-out residuals, which is what lets the dashboard show
"arriving between X and Y" instead of a single point estimate.

Saves everything the API needs to backend/data/model_bundle.pkl.

Run:  python ml/train_model.py
"""

import os
import json
import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
FEATURES_PATH = os.path.join(DATA_DIR, "training_features.csv")
ENCODING_PATH = os.path.join(DATA_DIR, "feature_encoding.json")
MODEL_PATH = os.path.join(DATA_DIR, "model_bundle.pkl")


def build_model():
    """XGBoost if available (spec'd in Phase 2), otherwise a scikit-learn
    gradient boosting regressor with an equivalent API -- keeps the
    project runnable even without XGBoost installed."""
    try:
        import xgboost as xgb
        model = xgb.XGBRegressor(
            n_estimators=300, max_depth=5, learning_rate=0.06,
            subsample=0.85, colsample_bytree=0.85, random_state=42,
        )
        return model, "XGBoost (XGBRegressor)"
    except ImportError:
        from sklearn.ensemble import GradientBoostingRegressor
        model = GradientBoostingRegressor(
            n_estimators=300, max_depth=4, learning_rate=0.06, random_state=42,
        )
        return model, "scikit-learn GradientBoostingRegressor (XGBoost not installed - fallback)"


def main():
    print(f"[1/5] Loading engineered features from {FEATURES_PATH} ...")
    df = pd.read_csv(FEATURES_PATH)
    with open(ENCODING_PATH) as f:
        encoding = json.load(f)
    feature_columns = encoding["feature_columns"]
    target_column = encoding["target_column"]
    print(f"       {len(df)} rows, {len(feature_columns)} features, target = {target_column}")

    X = df[feature_columns].values
    y = df[target_column].values

    print("\n[2/5] Splitting train/test (80/20) ...")
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    print(f"       train: {len(X_train)} rows   test: {len(X_test)} rows")

    print("\n[3/5] Training regressor ...")
    model, model_name = build_model()
    print(f"       Model: {model_name}")
    model.fit(X_train, y_train)

    print("\n[4/5] Evaluating on held-out test set ...")
    preds = model.predict(X_test)
    mae = mean_absolute_error(y_test, preds)
    rmse = mean_squared_error(y_test, preds) ** 0.5
    r2 = r2_score(y_test, preds)
    print(f"       MAE  = {mae:.2f} min")
    print(f"       RMSE = {rmse:.2f} min")
    print(f"       R2   = {r2:.3f}")

    residuals = y_test - preds
    p10_offset = float(np.percentile(residuals, 10))
    p90_offset = float(np.percentile(residuals, 90))
    print(f"       Empirical residual range -> P10 offset {p10_offset:+.2f} min, P90 offset {p90_offset:+.2f} min")

    importances = getattr(model, "feature_importances_", None)
    if importances is not None:
        print("\n       Feature importance:")
        for name, imp in sorted(zip(feature_columns, importances), key=lambda x: -x[1]):
            print(f"         {name:30s} {imp*100:5.1f}%")

    print(f"\n[5/5] Saving model bundle -> {MODEL_PATH}")
    feature_importances = None
    if importances is not None:
        feature_importances = sorted(
            [{"name": n, "value": round(float(imp) * 100, 1)} for n, imp in zip(feature_columns, importances)],
            key=lambda x: -x["value"],
        )

    bundle = {
        "model": model,
        "model_name": model_name,
        "feature_columns": feature_columns,
        "target_column": target_column,
        "train_type_map": encoding["train_type_map"],
        "metrics": {"mae": mae, "rmse": rmse, "r2": r2},
        "feature_importances": feature_importances,
        "training_samples": len(df),
        "p10_offset": p10_offset,
        "p90_offset": p90_offset,
    }
    joblib.dump(bundle, MODEL_PATH)
    print("\nTraining complete. Model bundle ready for inference.py / the API.")


if __name__ == "__main__":
    main()
