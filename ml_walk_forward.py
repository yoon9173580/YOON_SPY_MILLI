"""
Honest walk-forward validation of the ML loss filter.

Trains the classifier on trades BEFORE a cutoff date, then evaluates on
trades AFTER the cutoff. This is the only way to get an unbiased PF
estimate — re-applying a model to its training set (as in naive backtest
+ --ml-model) shows fantasy results (WR 100%, PF inf).
"""
import json
import sys
import io
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

FEATURE_COLS = [
    "feat_gap_pct", "feat_pre_range_pct", "feat_pre_change_pct",
    "feat_pre_vol", "feat_rsi", "feat_adx", "feat_vol_ratio",
    "feat_qqq_pct", "feat_iwm_pct", "feat_dow", "vix", "score",
]


def load_df():
    d = json.load(Path("backtest_iron_condor_1min.json").open())
    df = pd.DataFrame(d["trades"])
    df["loss"] = (df["pnl"] <= 0).astype(int)
    df["date"] = pd.to_datetime(df["date"])
    for c in FEATURE_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def metrics_for_subset(sub, label=""):
    if len(sub) == 0:
        return None
    wins = sub[sub["pnl"] > 0]
    losses = sub[sub["pnl"] <= 0]
    gp = wins["pnl"].sum()
    gl = abs(losses["pnl"].sum())
    pf = gp / gl if gl > 0 else float("inf")
    wr = (sub["pnl"] > 0).mean() * 100
    return {"label": label, "n": len(sub), "wr": wr, "pf": pf, "net": sub["pnl"].sum(),
            "wins": len(wins), "losses": len(losses)}


def walk_forward(df, cutoff="2026-01-01"):
    cutoff_ts = pd.Timestamp(cutoff)
    train = df[df["date"] < cutoff_ts]
    test  = df[df["date"] >= cutoff_ts]

    print(f"  Train: {len(train)} trades (before {cutoff})")
    print(f"          wins {(train['loss']==0).sum()}  losses {(train['loss']==1).sum()}")
    print(f"  Test:  {len(test)} trades (on/after {cutoff})")
    print(f"          wins {(test['loss']==0).sum()}  losses {(test['loss']==1).sum()}")
    print()

    X_tr = train[FEATURE_COLS].fillna(train[FEATURE_COLS].median(numeric_only=True))
    y_tr = train["loss"].values
    X_te = test[FEATURE_COLS].fillna(train[FEATURE_COLS].median(numeric_only=True))
    y_te = test["loss"].values

    candidates = {
        "logreg": Pipeline([("sc", StandardScaler()),
                            ("lr", LogisticRegression(class_weight="balanced", max_iter=1000))]),
        "rf_shallow": RandomForestClassifier(n_estimators=200, max_depth=4,
                                             class_weight="balanced",
                                             random_state=42, n_jobs=-1),
    }

    # Baseline on TEST (no filter)
    baseline = metrics_for_subset(test, "baseline (no filter)")
    print(f"  BASELINE test set: n={baseline['n']}  WR {baseline['wr']:.1f}%  PF {baseline['pf']:.2f}  Net ${baseline['net']:+,.0f}")
    print()

    for name, model in candidates.items():
        model.fit(X_tr, y_tr)
        probs = model.predict_proba(X_te)[:, 1]

        try:
            auc = roc_auc_score(y_te, probs) if len(set(y_te)) > 1 else float("nan")
        except ValueError:
            auc = float("nan")

        print(f"  [{name}] test ROC-AUC: {auc:.3f}")
        for thr in [0.30, 0.40, 0.50, 0.60, 0.70]:
            keep_mask = probs <= thr
            kept = test[keep_mask]
            r = metrics_for_subset(kept, f"{name}@{thr}")
            if r is None:
                print(f"    thr={thr:.2f}: 0 trades kept")
                continue
            delta_pf = r["pf"] - baseline["pf"] if baseline["pf"] != float("inf") else 0
            delta_net = r["net"] - baseline["net"]
            print(f"    thr={thr:.2f} | keep {r['n']:3d}/{baseline['n']} | WR {r['wr']:5.1f}% | "
                  f"PF {r['pf']:5.2f} (Δ{delta_pf:+.2f}) | Net ${r['net']:+,.0f} (Δ${delta_net:+,.0f})")
        print()


def main():
    print("=" * 80)
    print("  WALK-FORWARD ML LOSS FILTER EVALUATION")
    print("=" * 80)
    df = load_df()
    print(f"  Total trades: {len(df)}  |  Date range: {df['date'].min().date()} → {df['date'].max().date()}")
    print()

    print("─" * 80)
    print(" SPLIT 1: train on 2024-2025, test on 2026")
    print("─" * 80)
    walk_forward(df, cutoff="2026-01-01")

    print("─" * 80)
    print(" SPLIT 2: train on 2024-H1-H2, test on 2025+")
    print("─" * 80)
    walk_forward(df, cutoff="2025-01-01")


if __name__ == "__main__":
    main()
