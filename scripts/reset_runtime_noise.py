#!/usr/bin/env python3
"""
런타임 노이즈 정리 — ML 가중치 / dryrun 로그 / 캐시.

오늘 portfolio.json 에 trade history는 비어 있는데
  data_cache/ml_weights.json    → wins=10 losses=0 (합성 피드백)
  data_cache/dryrun_orders.jsonl → TP/SL 5005/4995 (테스트 placeholder)
처럼 명백히 합성된 노이즈가 쌓인 경우 사용.

용법:
  python scripts/reset_runtime_noise.py                 # 확인만 (dry run)
  python scripts/reset_runtime_noise.py --apply         # 실제 정리
  python scripts/reset_runtime_noise.py --ml-only       # ML 가중치만 리셋
  python scripts/reset_runtime_noise.py --dryrun-only   # dryrun 로그만 비움

portfolio.json은 절대 건드리지 않음 (실 거래 기록 가능성).
"""
import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime


REPO = Path(__file__).resolve().parent.parent
ML_PATH     = REPO / "data_cache" / "ml_weights.json"
DRYRUN_PATH = REPO / "data_cache" / "dryrun_orders.jsonl"
VIX_BASELINE = REPO / "data_cache" / "vix_baseline.json"
PORTFOLIO   = REPO / "portfolio.json"


def _ml_default():
    return {
        "technical": 1.0, "regime": 1.0, "flow": 1.0, "correlation": 1.0,
        "last_updated": 0,
        "sample_count": 0, "wins": 0, "losses": 0,
        "updates_per_layer": {"technical": 0, "regime": 0, "flow": 0, "correlation": 0},
    }


def inspect():
    print("=== State inspection ===\n")
    if ML_PATH.exists():
        ml = json.loads(ML_PATH.read_text())
        print(f"ML weights:")
        print(f"  technical={ml.get('technical', '?'):.3f}  regime={ml.get('regime','?'):.3f}  flow={ml.get('flow','?'):.3f}  correlation={ml.get('correlation','?'):.3f}")
        print(f"  sample_count={ml.get('sample_count', 0)}  wins={ml.get('wins', 0)}  losses={ml.get('losses', 0)}")
    else:
        print("ML weights: file missing")

    if DRYRUN_PATH.exists():
        lines = DRYRUN_PATH.read_text().strip().splitlines()
        print(f"\nDryrun orders log: {len(lines)} entries")
        if lines:
            print(f"  oldest: {json.loads(lines[0]).get('ts', '?')}")
            print(f"  newest: {json.loads(lines[-1]).get('ts', '?')}")
            # Detect placeholder TP/SL
            placeholders = sum(1 for l in lines
                              if (e := json.loads(l)).get("tp") == 5005 and e.get("sl") == 4995)
            if placeholders == len(lines):
                print(f"  ⚠️  ALL {placeholders} entries use TP=5005/SL=4995 (test placeholder)")
    else:
        print("\nDryrun orders log: file missing")

    if PORTFOLIO.exists():
        pf = json.loads(PORTFOLIO.read_text())
        print(f"\nPortfolio (live trade ledger — NOT touched by this script):")
        print(f"  cash=${pf.get('cash')}  positions={len(pf.get('positions', {}) or {})}  history={len(pf.get('history', []) or [])}")
        print(f"  revision={pf.get('revision')}  last_saved={pf.get('last_saved')}")


def reset_ml(apply: bool):
    print(f"\n[ML] {'RESET' if apply else 'WOULD RESET'} ml_weights.json → defaults")
    if apply:
        ML_PATH.parent.mkdir(exist_ok=True)
        ML_PATH.write_text(json.dumps(_ml_default(), indent=2))


def truncate_dryrun(apply: bool):
    print(f"[DRYRUN] {'CLEAR' if apply else 'WOULD CLEAR'} dryrun_orders.jsonl")
    if apply and DRYRUN_PATH.exists():
        DRYRUN_PATH.unlink()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="실제 적용 (없으면 dry run)")
    p.add_argument("--ml-only", action="store_true")
    p.add_argument("--dryrun-only", action="store_true")
    args = p.parse_args()

    inspect()
    print()

    do_ml = args.ml_only or not args.dryrun_only
    do_dr = args.dryrun_only or not args.ml_only

    if do_ml:
        reset_ml(args.apply)
    if do_dr:
        truncate_dryrun(args.apply)

    if not args.apply:
        print("\n(dry run — pass --apply to make changes)")
    else:
        print("\n✅ Reset complete.")


if __name__ == "__main__":
    main()
