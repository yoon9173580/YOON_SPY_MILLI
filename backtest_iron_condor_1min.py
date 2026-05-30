"""
SPY 0DTE Iron Condor Backtest — 1-minute Precise

Different strategy from debit spread: SELL premium instead of buy.
Theta works FOR us. Profit when SPY stays in range, max loss when big move.

Structure (per contract):
  Sell  call at K_sc (ATM + short_offset)
  Buy   call at K_lc (K_sc + wing_width)
  Sell  put  at K_sp (ATM - short_offset)
  Buy   put  at K_lp (K_sp - wing_width)

Position:
  Receive credit at entry
  Buy back to close (cost = current IC value)
  PnL = credit_received - close_cost

Risk profile:
  Max profit = credit (SPY between K_sp and K_sc at expiry)
  Max loss   = wing_width - credit (SPY beyond wings)
"""

import math
import json
import sys
import argparse
from datetime import datetime, timedelta, time as dt_time
import pandas as pd
import numpy as np

# Reuse all helpers from debit spread backtest
from backtest_options_1min import (
    bs_price, dynamic_slippage,
    score_day, load_1min, fetch_daily, prep_daily_features,
    get_aligned, minutes_to_close,
    NY, TRADING_MIN_PER_YEAR, CSV_PATH,
    SESSION_OPEN, ENTRY_TIME, EOD_EXIT_TIME, SESSION_CLOSE,
)


def ic_value(S, K_sc, K_lc, K_sp, K_lp, T, r, sigma):
    """Net value of the iron condor (= credit if we open it, = close cost if we close it).

    From perspective of the IC: short_call - long_call + short_put - long_put.
    Always positive (or zero) since short strike is closer to money than long.
    """
    sc = bs_price(S, K_sc, T, r, sigma, "call")
    lc = bs_price(S, K_lc, T, r, sigma, "call")
    sp = bs_price(S, K_sp, T, r, sigma, "put")
    lp = bs_price(S, K_lp, T, r, sigma, "put")
    return max((sc - lc) + (sp - lp), 0.0)


def simulate_ic_intraday(day_bars, K_sc, K_lc, K_sp, K_lp, credit_received,
                         iv, r, tp_pct, sl_pct, wing_width, slip4, spread_pct):
    """
    Walk minute-by-minute. Returns (close_cost, exit_note, exit_time).

    TP triggers when close_cost <= credit_received * (1 - tp_pct)
    SL triggers when close_cost >= credit_received * (1 + sl_pct)
    """
    tp_threshold = credit_received * (1 - tp_pct)  # e.g., 0.5 * credit
    sl_threshold = credit_received * (1 + sl_pct)  # e.g., 2.0 * credit
    # Cap SL at wing width (max possible loss = wings exhausted)
    sl_threshold = min(sl_threshold, wing_width)

    walk = day_bars[(day_bars["time_of_day"] > ENTRY_TIME)
                  & (day_bars["time_of_day"] <= EOD_EXIT_TIME)]

    for ts, bar in walk.iterrows():
        t_rem = max(minutes_to_close(bar["time_of_day"]), 1) / TRADING_MIN_PER_YEAR
        # Compute IC value at bar's high and low
        h = float(bar["high"])
        l = float(bar["low"])
        val_h = ic_value(h, K_sc, K_lc, K_sp, K_lp, t_rem, r, iv)
        val_l = ic_value(l, K_sc, K_lc, K_sp, K_lp, t_rem, r, iv)
        max_val = max(val_h, val_l)
        min_val = min(val_h, val_l)

        # Cost to close = max_val * (1 + spread_pct) + slip (we pay more to close due to bid-ask)
        cost_h = max_val * (1 + spread_pct) + slip4
        cost_l = min_val * (1 + spread_pct) + slip4

        # SL: high cost meets SL threshold (price moved against us)
        if cost_h >= sl_threshold:
            return min(cost_h, sl_threshold + slip4), "SL", bar["time_of_day"]
        # TP: low cost meets TP threshold (price decayed in our favor)
        if cost_l <= tp_threshold:
            return max(cost_l, tp_threshold), "TP", bar["time_of_day"]

    if len(walk) == 0:
        return credit_received, "NODATA", None
    last_bar = walk.iloc[-1]
    t_rem = max(minutes_to_close(last_bar["time_of_day"]), 1) / TRADING_MIN_PER_YEAR
    eod_val = ic_value(float(last_bar["close"]), K_sc, K_lc, K_sp, K_lp, t_rem, r, iv)
    return eod_val * (1 + spread_pct) + slip4, "EOD", last_bar["time_of_day"]


def run_ic_backtest(start_date=None, end_date=None, balance=500000.0,
                    min_score=85, tp_pct=0.50, sl_pct=1.00,
                    short_offset=3, wing_width=5,
                    regime_filter="none", min_grade="MODERATE",
                    slip_multiplier=1.0, ml_model_path=None,
                    ml_threshold=None, max_contracts=20):
    # ML filter setup (optional)
    ml_bundle = None
    if ml_model_path:
        import joblib
        ml_bundle = joblib.load(ml_model_path)
        if ml_threshold is None:
            ml_threshold = ml_bundle.get("threshold", 0.5)
        print(f"  [ML] loaded {ml_model_path} (trained on n={ml_bundle.get('trained_on_n')}, threshold={ml_threshold:.2f})")
    """tp_pct: take profit at this fraction of credit captured (0.50 = close when half credit decayed)
       sl_pct: stop loss when close_cost = credit * (1 + sl_pct), capped by wing width
       short_offset: short strikes at ATM ± this (default 3)
       wing_width: long strikes at short ± this (default 5)
    """
    print("=" * 80)
    print("  SPY 0DTE IRON CONDOR BACKTEST — 1-MIN PRECISE")
    print(f"  Strikes: short ±{short_offset}, wings ±{short_offset + wing_width}")
    print(f"  TP @ {tp_pct*100:.0f}% credit kept, SL @ {sl_pct*100:.0f}%, regime: {regime_filter}")
    print("=" * 80)

    df_1min = load_1min(CSV_PATH)
    csv_start = df_1min["date"].min()
    csv_end = df_1min["date"].max()
    if start_date is None:
        start_date = csv_start
    else:
        start_date = pd.Timestamp(start_date).date()
    if end_date is None:
        end_date = csv_end
    else:
        end_date = pd.Timestamp(end_date).date()
    print(f"    Window: {start_date} to {end_date}")

    spy_d, qqq_d, iwm_d, vix_d = fetch_daily(start_date, end_date)
    if spy_d.empty:
        print("ERROR: no yfinance data"); return
    spy_d = prep_daily_features(spy_d)
    qqq_pcts = qqq_d["Close"].pct_change() * 100
    iwm_pcts = iwm_d["Close"].pct_change() * 100

    r = 0.05
    SPREAD_PCT = 0.03

    trades = []
    wins, losses = 0, 0
    initial_balance = balance

    grouped = dict(tuple(df_1min.groupby("date")))
    trading_dates = sorted([d for d in grouped.keys() if start_date <= d <= end_date])
    print(f"[*] Simulating {len(trading_dates)} trading days...\n")

    hdr = f"{'Date':<11} {'Sc':>3} {'G':<2} {'K_sp/sc':>10} {'Cred':>6} {'Cost':>6} {'Move':>7} {'P&L':>7} {'Ex':>4} {'Bal':>10}"
    print(hdr)
    print("-" * len(hdr))

    for date in trading_dates:
        ds = date.strftime("%m/%d")
        spy_row = None
        for idx in spy_d.index:
            if idx.date() == date:
                spy_row = spy_d.loc[idx]
                break
        if spy_row is None:
            continue
        prev_close = float(spy_row["PrevClose"]) if pd.notna(spy_row.get("PrevClose")) else None
        if prev_close is None:
            continue

        day_bars = grouped[date].copy()
        if len(day_bars) < 60:
            continue

        spy_o = float(day_bars.iloc[0]["open"])
        spy_h = float(day_bars["high"].max())
        spy_l = float(day_bars["low"].min())
        spy_c = float(day_bars.iloc[-1]["close"])

        vwap_cum = float(spy_row["VWAP"]) if pd.notna(spy_row.get("VWAP")) else spy_c
        pct_change = ((spy_c / prev_close) - 1) * 100 if prev_close > 0 else 0
        vol_ratio = float(spy_row["VolRatio"]) if pd.notna(spy_row.get("VolRatio")) else 0.0
        vix_val = get_aligned(vix_d["Close"], date) or 18.0
        qqq_p = get_aligned(qqq_pcts, date) or 0.0
        iwm_p = get_aligned(iwm_pcts, date) or 0.0
        adx_v = float(spy_row["ADX"]) if pd.notna(spy_row.get("ADX")) else None
        rsi_v = float(spy_row["RSI"]) if pd.notna(spy_row.get("RSI")) else None

        row_dict = {
            "Open": spy_o, "Close": spy_c, "High": spy_h, "Low": spy_l,
            "PrevClose": prev_close, "PctChange": pct_change,
            "VWAP": vwap_cum, "VolRatio": vol_ratio,
        }
        score, grade, _ = score_day(row_dict, vix_val, qqq_p, iwm_p, adx_v, rsi_v)

        grade_rank = {"STRONG": 3, "MODERATE": 2, "WEAK": 1, "NONE": 0, "LOCKED": 0}
        min_rank = {"STRONG": 3, "MODERATE": 2, "WEAK": 1, "NONE": 0}.get(min_grade, 3)
        if score < min_score or grade_rank.get(grade, 0) < min_rank:
            continue

        # Regime
        sma50 = float(spy_row["SMA50"]) if pd.notna(spy_row.get("SMA50")) else None
        if regime_filter == "sma50" and sma50 is not None and spy_c < sma50:
            continue

        # Entry bar
        entry_bars = day_bars[day_bars["time_of_day"] == ENTRY_TIME]
        if len(entry_bars) == 0:
            entry_bars = day_bars[day_bars["time_of_day"] >= ENTRY_TIME]
            if len(entry_bars) == 0:
                continue
        entry_bar = entry_bars.iloc[0]
        S_entry = float(entry_bar["close"])

        # ── Pre-10:30 intraday features (leak-free for ML) ──
        pre_bars = day_bars[(day_bars["time_of_day"] >= SESSION_OPEN)
                          & (day_bars["time_of_day"] <= ENTRY_TIME)]
        if len(pre_bars) >= 5:
            pre_open  = float(pre_bars.iloc[0]["open"])
            pre_high  = float(pre_bars["high"].max())
            pre_low   = float(pre_bars["low"].min())
            pre_close = float(pre_bars.iloc[-1]["close"])
            pre_vol   = float(pre_bars["volume"].sum())
            pre_range_pct  = ((pre_high - pre_low) / pre_open) * 100.0 if pre_open > 0 else 0.0
            pre_change_pct = ((pre_close - pre_open) / pre_open) * 100.0 if pre_open > 0 else 0.0
        else:
            pre_open = pre_high = pre_low = pre_close = pre_vol = 0.0
            pre_range_pct = pre_change_pct = 0.0
        gap_pct = ((spy_o / prev_close) - 1.0) * 100.0 if prev_close > 0 else 0.0

        # ── ML filter (optional, leak-free — only pre-entry features) ──
        if ml_bundle is not None:
            import pandas as _pd
            feat_dict = {
                "feat_gap_pct": gap_pct,
                "feat_pre_range_pct": pre_range_pct,
                "feat_pre_change_pct": pre_change_pct,
                "feat_pre_vol": pre_vol,
                "feat_rsi": rsi_v if rsi_v else ml_bundle["median_impute"].get("feat_rsi", 50.0),
                "feat_adx": adx_v if adx_v else ml_bundle["median_impute"].get("feat_adx", 20.0),
                "feat_vol_ratio": vol_ratio,
                "feat_qqq_pct": qqq_p,
                "feat_iwm_pct": iwm_p,
                "feat_dow": _pd.Timestamp(date).dayofweek,
                "vix": vix_val,
                "score": score,
            }
            feat_vec = _pd.DataFrame([[feat_dict[c] for c in ml_bundle["feature_cols"]]],
                                     columns=ml_bundle["feature_cols"])
            loss_prob = ml_bundle["model"].predict_proba(feat_vec)[0, 1]
            if loss_prob > ml_threshold:
                continue  # ML predicts loss — skip this trade

        iv = vix_val / 100.0

        # Strikes
        K_atm = round(S_entry)
        K_sc = K_atm + short_offset
        K_lc = K_sc + wing_width
        K_sp = K_atm - short_offset
        K_lp = K_sp - wing_width

        T_entry = minutes_to_close(ENTRY_TIME) / TRADING_MIN_PER_YEAR

        spy_range_pct = ((spy_h - spy_l) / spy_o) * 100 if spy_o > 0 else 0
        slip_per = dynamic_slippage(vix_val, spy_range_pct)
        slip4 = slip_per * 4 * slip_multiplier  # 4 legs × stress multiplier

        # Credit received (entry)
        credit_received = ic_value(S_entry, K_sc, K_lc, K_sp, K_lp, T_entry, r, iv)
        credit_received = credit_received * (1 - SPREAD_PCT) - slip4

        if credit_received <= 0.05:
            continue  # too small to bother

        # Sizing: max loss per contract
        max_loss_per_contract = (wing_width - credit_received) * 100.0
        if max_loss_per_contract <= 0:
            continue
        risk_pct = 0.10 if score >= 95 else (0.08 if vix_val >= 25 else (0.06 if vix_val >= 20 else 0.05))
        max_risk_dollars = balance * risk_pct
        num_contracts = max(1, int(max_risk_dollars / max_loss_per_contract))
        num_contracts = min(num_contracts, max_contracts)  # liquidity cap for 0DTE

        # Simulate intraday
        close_cost, exit_note, exit_time = simulate_ic_intraday(
            day_bars, K_sc, K_lc, K_sp, K_lp, credit_received,
            iv, r, tp_pct, sl_pct, wing_width, slip4, SPREAD_PCT
        )

        pnl_per_contract = (credit_received - close_cost) * 100
        # Cap loss at max wing loss
        max_loss = -(wing_width - credit_received) * 100
        pnl_per_contract = max(pnl_per_contract, max_loss)
        total_pnl = pnl_per_contract * num_contracts

        balance += total_pnl
        if total_pnl > 0:
            wins += 1
        else:
            losses += 1

        spy_move = spy_c - spy_o
        trades.append({
            "date": str(date), "score": score, "grade": grade,
            "K_sp": K_sp, "K_sc": K_sc, "K_lp": K_lp, "K_lc": K_lc,
            "S_entry": round(S_entry, 2),
            "credit": round(credit_received, 2),
            "close_cost": round(close_cost, 2),
            "spy_move": round(spy_move, 2),
            "contracts": num_contracts,
            "pnl": round(total_pnl, 2),
            "balance": round(balance, 2),
            "vix": round(vix_val, 1),
            "exit_type": exit_note,
            "exit_time": str(exit_time) if exit_time else "",
            # ML features (pre-10:30, leak-free)
            "feat_gap_pct": round(gap_pct, 3),
            "feat_pre_range_pct": round(pre_range_pct, 3),
            "feat_pre_change_pct": round(pre_change_pct, 3),
            "feat_pre_vol": round(pre_vol, 0),
            "feat_rsi": round(rsi_v, 1) if rsi_v else None,
            "feat_adx": round(adx_v, 1) if adx_v else None,
            "feat_vol_ratio": round(vol_ratio, 3),
            "feat_qqq_pct": round(qqq_p, 3),
            "feat_iwm_pct": round(iwm_p, 3),
            "feat_dow": pd.Timestamp(date).dayofweek,
        })

        sk = f"{K_sp}/{K_sc}"
        print(f"{ds:<11} {score:>3} G  {sk:>10} ${credit_received:>5.2f} ${close_cost:>5.2f} ${spy_move:>+6.2f} ${total_pnl:>+6.0f} {exit_note:>4} ${balance:>9,.0f}")

    # Summary
    total_trades = wins + losses
    total_pnl = balance - initial_balance
    wr = (wins / total_trades * 100) if total_trades > 0 else 0
    peak = initial_balance
    max_dd = 0
    for t in trades:
        if t["balance"] > peak:
            peak = t["balance"]
        dd = (peak - t["balance"]) / peak * 100
        if dd > max_dd:
            max_dd = dd
    avg_w = float(np.mean([t["pnl"] for t in trades if t["pnl"] > 0])) if wins > 0 else 0.0
    avg_l = float(np.mean([t["pnl"] for t in trades if t["pnl"] <= 0])) if losses > 0 else 0.0
    sharpe = 0.0
    if trades:
        rets = [t["pnl"] / initial_balance for t in trades]
        sharpe = (np.mean(rets) / np.std(rets)) * np.sqrt(252) if np.std(rets) > 0 else 0.0
    gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")

    exit_counts = {}
    for t in trades:
        exit_counts[t["exit_type"]] = exit_counts.get(t["exit_type"], 0) + 1

    print("\n" + "=" * 80)
    print("  IRON CONDOR BACKTEST RESULTS")
    print("=" * 80)
    print(f"  Window:            {start_date} to {end_date}")
    print(f"  Starting Balance:  ${initial_balance:,.2f}")
    print(f"  Final Balance:     ${balance:,.2f}")
    print(f"  Total P&L:         ${total_pnl:+,.2f} ({total_pnl/initial_balance*100:+.1f}%)")
    print(f"  Total Trades:      {total_trades}")
    print(f"  Win Rate:          {wr:.1f}% ({wins}W / {losses}L)")
    print(f"  Avg Win:           ${avg_w:+,.2f}")
    print(f"  Avg Loss:          ${avg_l:+,.2f}")
    print(f"  Profit Factor:     {pf}")
    print(f"  Max Drawdown:      {max_dd:.1f}%")
    print(f"  Sharpe Ratio:      {sharpe:.2f}")
    print(f"  Exit Breakdown:    {exit_counts}")
    print(f"  Strikes:           ATM ±{short_offset} short, wings +{wing_width}")
    print(f"  TP / SL:           +{tp_pct*100:.0f}% credit decay / {sl_pct*100:.0f}% credit loss")
    print("=" * 80)

    out_path = "backtest_iron_condor_1min.json"
    with open(out_path, "w") as f:
        json.dump({
            "model": f"IC short±{short_offset} wing±{wing_width}",
            "period_start": str(start_date), "period_end": str(end_date),
            "start_balance": initial_balance, "end_balance": round(balance, 2),
            "total_pnl": round(total_pnl, 2),
            "total_trades": total_trades, "wins": wins, "losses": losses,
            "win_rate": round(wr, 1), "avg_win": round(avg_w, 2), "avg_loss": round(avg_l, 2),
            "profit_factor": pf if pf != float("inf") else None,
            "max_drawdown": round(max_dd, 1), "sharpe": round(sharpe, 2),
            "exit_breakdown": exit_counts, "trades": trades,
        }, f, indent=2, default=str)
    print(f"\n  [*] Saved to {out_path}")
    return {
        "trades": total_trades, "wr": wr, "pf": pf, "dd": max_dd, "pnl": total_pnl, "sharpe": sharpe,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dates", nargs="*")
    parser.add_argument("--balance",      type=float, default=500000.0)
    parser.add_argument("--max-contracts", type=int, default=20, help="Liquidity cap on 0DTE IC contracts (default 20)")
    parser.add_argument("--min-score",    type=int,   default=85)
    parser.add_argument("--tp",           type=float, default=0.50, help="Take profit at 1 - tp_pct of credit (default 0.50 = close at half credit)")
    parser.add_argument("--sl",           type=float, default=1.00, help="Stop loss when cost = (1+sl)*credit, capped at wing")
    parser.add_argument("--short-offset", type=int,   default=3)
    parser.add_argument("--wing-width",   type=int,   default=5)
    parser.add_argument("--regime",       type=str,   default="none", choices=["none","sma50"])
    parser.add_argument("--min-grade",    type=str,   default="MODERATE",
                        choices=["STRONG", "MODERATE", "WEAK", "NONE"],
                        help="Minimum grade for entry (default MODERATE — freq tuning). STRONG = old conservative")
    parser.add_argument("--slip-mult",    type=float, default=1.0,
                        help="Slippage multiplier (default 1.0). 5.0 = gap-fill stress scenario")
    parser.add_argument("--ml-model",     type=str,   default=None,
                        help="Path to joblib ML model. If set, skips trades with predicted loss probability > threshold")
    parser.add_argument("--ml-threshold", type=float, default=None,
                        help="ML loss-probability threshold for skip (default = model's saved threshold)")
    args = parser.parse_args()

    start_date = args.dates[0] if len(args.dates) >= 1 else None
    end_date = args.dates[1] if len(args.dates) >= 2 else None

    run_ic_backtest(
        start_date=start_date, end_date=end_date, balance=args.balance,
        min_score=args.min_score, tp_pct=args.tp, sl_pct=args.sl,
        short_offset=args.short_offset, wing_width=args.wing_width,
        regime_filter=args.regime,
        min_grade=args.min_grade,
        slip_multiplier=args.slip_mult,
        ml_model_path=args.ml_model,
        ml_threshold=args.ml_threshold,
        max_contracts=args.max_contracts,
    )
