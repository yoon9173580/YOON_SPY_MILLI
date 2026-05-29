"""
ML Loss-Prediction Filter for SPY 0DTE Iron Condor

Trains a binary classifier on pre-10:30 features to predict whether
an IC entry will result in a loss. Skipping high-loss-probability days
should push PF higher.

Pipeline:
  1. Load trades from backtest_iron_condor_1min.json (must have feat_* keys)
  2. Extract feature matrix X and loss labels y (1 = lost money)
  3. Train logistic regression + random forest with 5-fold stratified CV
  4. Evaluate ROC-AUC, precision/recall at filter thresholds
  5. Apply filter to original trades, compare PF before/after
  6. Persist best model to data_cache/ml_loss_model.joblib

Sample sizes:
  - 436 total trades (stress-test sample, min-grade WEAK + min-score 0)
  - 365 wins (83.7%) / 71 losses (16.3%) → imbalanced
"""
import json
import sys
import io
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score, cross_val_predict
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score, classification_report, confusion_matrix
import joblib

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

JSON_PATH  = Path("backtest_iron_condor_1min.json")
MODEL_PATH = Path("data_cache/ml_loss_model.joblib")
FEATURE_COLS = [
    "feat_gap_pct",
    "feat_pre_range_pct",
    "feat_pre_change_pct",
    "feat_pre_vol",
    "feat_rsi",
    "feat_adx",
    "feat_vol_ratio",
    "feat_qqq_pct",
    "feat_iwm_pct",
    "feat_dow",
    "vix",
    "score",
]


def load_trades_df():
    d = json.load(JSON_PATH.open())
    df = pd.DataFrame(d["trades"])
    df["loss"] = (df["pnl"] <= 0).astype(int)
    # Some features may be None (rsi/adx warmup) — fill with median
    for c in FEATURE_COLS:
        if c not in df.columns:
            raise RuntimeError(f"Missing feature column: {c} — rerun backtest first")
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def evaluate_filter(df, loss_probs, threshold, label=""):
    """Simulate skipping trades where predicted loss prob > threshold."""
    keep = loss_probs <= threshold
    kept = df[keep]
    skipped = df[~keep]

    if len(kept) == 0:
        return None

    gp = kept[kept["pnl"] > 0]["pnl"].sum()
    gl = abs(kept[kept["pnl"] <= 0]["pnl"].sum())
    pf = gp / gl if gl > 0 else float("inf")
    wr = (kept["pnl"] > 0).mean() * 100
    net = kept["pnl"].sum()
    # What we'd have saved by skipping
    saved_loss = skipped[skipped["pnl"] <= 0]["pnl"].sum()  # negative
    missed_win = skipped[skipped["pnl"]  > 0]["pnl"].sum()  # positive

    return {
        "label": label,
        "threshold": threshold,
        "trades_kept": len(kept),
        "trades_skipped": len(skipped),
        "wr": wr,
        "pf": pf,
        "net": net,
        "saved_from_losses": -saved_loss,  # positive number = $ saved
        "missed_wins": missed_win,
    }


def main():
    print("=" * 80)
    print("  ML LOSS-PREDICTION FILTER FOR IC TRADES")
    print("=" * 80)
    df = load_trades_df()
    print(f"  Loaded {len(df)} trades from {JSON_PATH}")
    print(f"  Wins: {(df['loss']==0).sum()} ({(df['loss']==0).mean()*100:.1f}%)")
    print(f"  Losses: {(df['loss']==1).sum()} ({(df['loss']==1).mean()*100:.1f}%)")
    print()

    X = df[FEATURE_COLS].copy()
    # Median impute for any NaN (rsi/adx warmup)
    X = X.fillna(X.median(numeric_only=True))
    y = df["loss"].values

    # Baseline: no filter
    baseline_pf = df[df["pnl"]>0]["pnl"].sum() / abs(df[df["pnl"]<=0]["pnl"].sum())
    baseline_wr = (df["pnl"]>0).mean() * 100
    baseline_net = df["pnl"].sum()
    print(f"  BASELINE (no filter): {len(df)} trades | WR {baseline_wr:.1f}% | PF {baseline_pf:.2f} | Net ${baseline_net:+.0f}")
    print()

    # Two candidate models
    candidates = {
        "logreg": Pipeline([("sc", StandardScaler()), ("lr", LogisticRegression(class_weight="balanced", max_iter=1000, C=1.0))]),
        "rf_shallow": RandomForestClassifier(n_estimators=200, max_depth=4, class_weight="balanced", random_state=42, n_jobs=-1),
    }

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    best = None
    for name, model in candidates.items():
        aucs = cross_val_score(model, X, y, cv=cv, scoring="roc_auc", n_jobs=1)
        probs = cross_val_predict(model, X, y, cv=cv, method="predict_proba", n_jobs=1)[:, 1]
        print(f"  [{name}] CV ROC-AUC: {aucs.mean():.3f} ± {aucs.std():.3f}  (per fold: {[f'{a:.2f}' for a in aucs]})")
        # Evaluate at several thresholds
        for thr in [0.30, 0.40, 0.50, 0.60, 0.70]:
            r = evaluate_filter(df, probs, thr, label=f"{name}@{thr}")
            if r is None:
                continue
            delta_pf = r["pf"] - baseline_pf
            print(f"    thr={thr:.2f} | keep {r['trades_kept']:3d}/{len(df)} | skip {r['trades_skipped']:3d} | WR {r['wr']:4.1f}% | PF {r['pf']:5.2f} (Δ{delta_pf:+.2f}) | Net ${r['net']:+,.0f}")
            if best is None or r["pf"] > best["pf"]:
                best = {**r, "model_name": name, "model": model, "probs": probs}
        print()

    print("=" * 80)
    print("  BEST CONFIG")
    print("=" * 80)
    print(f"  Model: {best['model_name']}  threshold: {best['threshold']:.2f}")
    print(f"  Trades kept:    {best['trades_kept']}/{len(df)}")
    print(f"  WR:             {best['wr']:.1f}%  (baseline {baseline_wr:.1f}%)")
    print(f"  PF:             {best['pf']:.2f}  (baseline {baseline_pf:.2f}, Δ{best['pf']-baseline_pf:+.2f})")
    print(f"  Net:            ${best['net']:+,.0f}  (baseline ${baseline_net:+,.0f})")
    print(f"  Avoided losses: ${best['saved_from_losses']:,.0f}")
    print(f"  Missed wins:    ${best['missed_wins']:,.0f}")
    print()

    # Confusion matrix at best threshold
    y_pred = (best["probs"] > best["threshold"]).astype(int)
    cm = confusion_matrix(y, y_pred)
    print(f"  Confusion (rows=actual, cols=predicted):")
    print(f"     pred_keep  pred_skip")
    print(f"  win    {cm[0,0]:4d}      {cm[0,1]:4d}    (skipping {cm[0,1]} winners by mistake)")
    print(f"  loss   {cm[1,0]:4d}      {cm[1,1]:4d}    (correctly skipping {cm[1,1]}/{cm[1,0]+cm[1,1]} losses)")
    print()

    # Train best on FULL data, save
    print("  [*] Training best model on full sample for production use...")
    final_model = best["model"]
    final_model.fit(X, y)

    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump({
        "model": final_model,
        "feature_cols": FEATURE_COLS,
        "median_impute": X.median(numeric_only=True).to_dict(),
        "threshold": best["threshold"],
        "trained_on_n": len(df),
        "cv_baseline_pf": baseline_pf,
        "cv_best_pf": best["pf"],
        "model_name": best["model_name"],
    }, MODEL_PATH)
    print(f"  [*] Saved to {MODEL_PATH}")

    # Show top features (for interpretability)
    if best["model_name"] == "logreg":
        lr = final_model.named_steps["lr"]
        sc = final_model.named_steps["sc"]
        coefs = pd.Series(lr.coef_[0], index=FEATURE_COLS).sort_values(key=lambda s: s.abs(), ascending=False)
        print("\n  Top features (standardized coefficients, positive = predicts LOSS):")
        for feat, coef in coefs.head(8).items():
            print(f"    {feat:<25}  {coef:+.3f}")
    elif best["model_name"] == "rf_shallow":
        imps = pd.Series(final_model.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
        print("\n  Top features (impurity importance):")
        for feat, imp in imps.head(8).items():
            print(f"    {feat:<25}  {imp:.3f}")


if __name__ == "__main__":
    main()
