import os
import sys
from pathlib import Path

# Add project root directory to Python search path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import joblib
import pandas as pd
import numpy as np
import lightgbm as lgb
from sklearn.metrics import roc_auc_score, accuracy_score

from core.features import extract_features
from core.data_engine import NIFTY_BASKET
from core.delivery import fetch_delivery_metrics

MODEL_PATH = os.path.join(ROOT_DIR, "models", "lgbm_stock_ranker.pkl")

# Exact 6-feature schema
FEATURE_COLS = [
    "dist_ema20_pct",
    "trend_spread_pct",
    "atr_pct",
    "rvol",
    "rsi_14",
    "deliv_shock"
]

def build_training_dataset() -> pd.DataFrame:
    """
    Extracts features for all synced symbols across the NIFTY_BASKET,
    incorporates delivery shock values, and returns a unified time-ordered dataset.
    """
    frames = []
    print("Building training dataset from engineered features and delivery dynamics...")
    
    for sym in NIFTY_BASKET:
        clean_sym = sym.replace(".NS", "")
        
        # Pull institutional delivery shock metric
        deliv_info = fetch_delivery_metrics(clean_sym)
        deliv_shock = deliv_info.get("deliv_shock", 1.0)
        
        # Extract features with delivery parameter
        df = extract_features(clean_sym, deliv_shock=deliv_shock)
        if not df.empty:
            df["symbol"] = clean_sym
            frames.append(df)
            print(f"  → Processed {clean_sym} ({len(df)} samples, deliv_shock={deliv_shock}x)")
            
    if not frames:
        return pd.DataFrame()
        
    full_df = pd.concat(frames, ignore_index=True)
    full_df = full_df.sort_values(by="date").reset_index(drop=True)
    return full_df

def train_model():
    """Trains the LightGBM classifier using walk-forward chronological splitting."""
    df = build_training_dataset()
    if df.empty:
        print("No training data available. Run 'python core/data_engine.py' first.")
        return

    # Chronological Split (80% Train, 20% Out-of-sample Test)
    split_idx = int(len(df) * 0.8)
    train_df = df.iloc[:split_idx]
    test_df = df.iloc[split_idx:]

    X_train = train_df[FEATURE_COLS]
    y_train = train_df["target_success"]
    
    X_test = test_df[FEATURE_COLS]
    y_test = test_df["target_success"]

    print(f"\nTraining set size: {len(X_train)} samples")
    print(f"Test set size:     {len(X_test)} samples")
    print(f"Base breakout rate: {round(y_train.mean() * 100, 1)}% positive setups")

    # LightGBM Classifier tuned for tabular financial features
    model = lgb.LGBMClassifier(
        n_estimators=150,
        learning_rate=0.03,
        num_leaves=15,
        max_depth=4,
        random_state=42,
        importance_type='gain',
        verbose=-1
    )

    model.fit(X_train, y_train)

    # Evaluate out-of-sample performance
    preds_proba = model.predict_proba(X_test)[:, 1]
    preds = (preds_proba >= 0.5).astype(int)

    acc = accuracy_score(y_test, preds)
    auc = roc_auc_score(y_test, preds_proba)

    print("\n--- Out-of-Sample Performance ---")
    print(f"Test Accuracy: {round(acc * 100, 2)}%")
    print(f"ROC-AUC Score: {round(auc, 3)}")

    importance = pd.DataFrame({
        'Feature': FEATURE_COLS,
        'Importance (Gain)': model.feature_importances_
    }).sort_values(by='Importance (Gain)', ascending=False)
    
    print("\nFeature Importance Rankings:")
    print(importance.to_string(index=False))

    # Serialize and overwrite model artifact
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    print(f"\n✓ Saved 6-feature retrained model artifact to {MODEL_PATH}")

if __name__ == "__main__":
    train_model()