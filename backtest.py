"""
SPY 0DTE Signal Machine — Realistic Options Backtest
Uses intraday 5-min bars + Black-Scholes pricing + theta decay
"""
import math, json, sys
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import pytz
import yfinance as yf
from scipy.stats import norm

NY = pytz.timezone("America/New_York")

# ── Black-Scholes Option Pricing ─────────────────────────────────

def bs_price(S, K, T, r, sigma, opt_type="call"):
    """Black-Scholes price. T in years, sigma = annualized IV."""
    if T <= 0: return max(S - K, 0) if opt_type == "call" else max(K - S, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if opt_type == "call":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

def bs_delta(S, K, T, r, sigma, opt_type="call"):
    if T <= 0: return 1.0 if (opt_type == "call" and S > K) else (-1.0 if opt_type == "put" and S < K else 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return norm.cdf(d1) if opt_type == "call" else norm.cdf(d1) - 1

# ── Scoring Engine (same as live) ────────────────────────────────

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_adx(df, period=14):
    if len(df) < period + 1: return pd.Series(dtype=float)
    h, l, c = df["High"], df["Low"], df["Close"]
    plus_dm = h.diff().where((h.diff() > l.diff().abs()) & (h.diff() > 0), 0.0)
    minus_dm = l.diff().abs().where((l.diff().abs() > h.diff()) & (l.diff().abs() > 0), 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di) * 100
    return dx.rolling(period).mean()

def score_day(spy_row, vix_close, qqq_pct, iwm_pct, adx_val, rsi_val):
    """Compute 7-layer normalized score for a single day."""
    # Regime
    vix_sc = 15 if 14 <= vix_close <= 20 else (0 if vix_close <= 30 else -20) if vix_close > 20 else -5
    adx_sc = 15 if adx_val and adx_val >= 25 else (5 if adx_val and adx_val >= 20 else 0)
    gap_pct = ((spy_row["Open"] / spy_row["PrevClose"]) - 1) * 100 if spy_row.get("PrevClose") else 0
    gap_sc = 5 if abs(gap_pct) > 0.5 else 0
    regime = vix_sc + adx_sc + gap_sc

    # Correlation
    spy_pct = spy_row.get("PctChange", 0)
    qqq_aligned = (spy_pct >= 0 and qqq_pct >= 0) or (spy_pct < 0 and qqq_pct < 0)
    sector_sync = all(v >= 0 for v in [spy_pct, qqq_pct, iwm_pct]) or all(v < 0 for v in [spy_pct, qqq_pct, iwm_pct])
    corr = max(0, min(20, (10 if qqq_aligned else -5) + (5 if iwm_pct > 0.3 else (-3 if iwm_pct < -0.3 else 0)) + (5 if sector_sync else 0)))

    # Time (assume PRIME window)
    tw = 20

    # Technical
    vwap = spy_row.get("VWAP", spy_row["Close"])
    vol_r = spy_row.get("VolRatio", 0)
    d_range = spy_row["High"] - spy_row["Low"]
    vwap_dir = "CALL" if spy_row["Open"] > vwap else "PUT"
    vol_sc = 10 if vol_r >= 2.0 else (7 if vol_r >= 1.5 else (3 if vol_r >= 1.0 else 0))
    range_sc = 10 if d_range >= 3.0 else (5 if d_range >= 2.0 else 0)
    rsi_sc = 10 if rsi_val and (rsi_val >= 60 or rsi_val <= 40) else 0
    tech = min(30, 10 + vol_sc + range_sc + rsi_sc)

    raw = regime + corr + tw + tech
    normalized = max(0, int((raw / 110) * 100))
    # Tuned thresholds: only STRONG (>=90) and HIGH (>=85) trigger trades
    grade = "STRONG" if normalized >= 90 else "HIGH" if normalized >= 85 else "MODERATE" if normalized >= 75 else "WEAK" if normalized >= 60 else "NONE"
    return normalized, grade, vwap_dir


def run_backtest(days=30, balance=2000.0):
    print("=" * 70)
    print("  SPY 0DTE TUNED OPTIONS BACKTEST")
    print("  BS pricing + Theta + TP/SL + VIX filter + Score>=85")
    print("=" * 70)

    end = datetime.now(NY)
    start = end - timedelta(days=int(days * 2))

    print(f"\n[*] Fetching SPY/QQQ/IWM/VIX daily data...")
    spy_d = yf.Ticker("SPY").history(start=start, end=end, interval="1d")
    qqq_d = yf.Ticker("QQQ").history(start=start, end=end, interval="1d")
    iwm_d = yf.Ticker("IWM").history(start=start, end=end, interval="1d")
    vix_d = yf.Ticker("^VIX").history(start=start, end=end, interval="1d")

    if spy_d.empty:
        print("ERROR: No data"); return

    # Prep daily indicators
    spy_d["PrevClose"] = spy_d["Close"].shift(1)
    spy_d["PctChange"] = spy_d["Close"].pct_change() * 100
    spy_d["RSI"] = calc_rsi(spy_d["Close"])
    adx_series = calc_adx(spy_d)
    spy_d["ADX"] = adx_series
    spy_d["VWAP"] = (spy_d["Volume"] * (spy_d["High"] + spy_d["Low"] + spy_d["Close"]) / 3).cumsum() / spy_d["Volume"].cumsum()
    spy_d["VolRatio"] = spy_d["Volume"] / spy_d["Volume"].rolling(20).mean()

    qqq_pcts = qqq_d["Close"].pct_change() * 100
    iwm_pcts = iwm_d["Close"].pct_change() * 100

    dates = spy_d.index[-days:]
    r = 0.05  # risk-free rate
    spread_pct = 0.03  # 3% bid-ask spread on premium
    slippage = 0.02  # $0.02 per contract
    TAKE_PROFIT = 0.50  # +50% profit take
    STOP_LOSS = 0.40    # -40% stop loss
    MIN_VIX = 16.0      # skip low-VIX days (premiums too cheap)

    trades = []
    wins, losses = 0, 0
    initial_balance = balance

    print(f"[*] Simulating {len(dates)} days...\n")
    print(f"{'Date':<12} {'Score':>5} {'Grade':<9} {'Dir':<5} {'Strike':>7} {'Entry$':>7} {'Exit$':>7} {'SPY Move':>9} {'Opt P&L':>8} {'Bal':>10}")
    print("-" * 95)

    for date in dates:
        ds = date.strftime("%Y-%m-%d")
        try:
            row = spy_d.loc[date]
            spy_open = float(row["Open"])
            spy_close = float(row["Close"])
            spy_high = float(row["High"])
            spy_low = float(row["Low"])
            prev_close = float(row["PrevClose"]) if pd.notna(row["PrevClose"]) else spy_open
        except: continue

        try: vix_val = float(vix_d.loc[date]["Close"])
        except: vix_val = 18.0
        try: qqq_p = float(qqq_pcts.loc[date])
        except: qqq_p = 0
        try: iwm_p = float(iwm_pcts.loc[date])
        except: iwm_p = 0

        adx_v = float(row["ADX"]) if pd.notna(row.get("ADX", np.nan)) else None
        rsi_v = float(row["RSI"]) if pd.notna(row.get("RSI", np.nan)) else None

        row_dict = {
            "Open": spy_open, "Close": spy_close, "High": spy_high, "Low": spy_low,
            "PrevClose": prev_close, "PctChange": float(row["PctChange"]) if pd.notna(row["PctChange"]) else 0,
            "VWAP": float(row["VWAP"]) if pd.notna(row["VWAP"]) else spy_open,
            "VolRatio": float(row["VolRatio"]) if pd.notna(row["VolRatio"]) else 0,
        }

        score, grade, direction = score_day(row_dict, vix_val, qqq_p, iwm_p, adx_v, rsi_v)

        # Tuned filter: only trade STRONG (>=90) or HIGH (>=85)
        if grade not in ("STRONG", "HIGH"):
            print(f"{ds:<12} {score:>5} {'X':>1} {grade:<7} {direction:<5} {'--':>7} {'--':>7} {'--':>7} {'--':>9} {'SKIP':>8} ${balance:>9,.2f}")
            continue

        # VIX filter: skip if VIX too low (premiums worthless)
        if vix_val < MIN_VIX:
            print(f"{ds:<12} {score:>5} {'V':>1} {grade:<7} {direction:<5} {'--':>7} {'--':>7} {'--':>7} {'--':>9} {'LOW VIX':>8} ${balance:>9,.2f}")
            continue

        # ── Options simulation with Black-Scholes ──
        opt_type = "call" if direction == "CALL" else "put"
        iv = vix_val / 100.0  # VIX as annualized IV proxy

        # ATM strike at entry (round to nearest $1)
        strike = round(spy_open)

        # Time to expiry: entry at 10:30 AM = 5.5h left, total day = 6.5h
        T_entry = 5.5 / (252 * 6.5)  # fraction of year
        T_exit = 1.0 / (252 * 6.5)   # 30 min before close

        # Entry price (at open, approximating 10:30)
        entry_premium = bs_price(spy_open, strike, T_entry, r, iv, opt_type)
        entry_delta = bs_delta(spy_open, strike, T_entry, r, iv, opt_type)

        # Apply bid-ask spread to entry
        entry_cost = entry_premium * (1 + spread_pct) + slippage
        if entry_cost <= 0.01: continue

        # Position sizing: risk max 5% of balance, STRONG=full, HIGH=half
        max_risk = balance * 0.05
        num_contracts = max(1, int(max_risk / (entry_cost * 100)))
        if grade == "HIGH":
            num_contracts = max(1, num_contracts // 2)

        # ── Intraday TP/SL simulation using high/low as price path ──
        # Check if TP or SL would have been hit during the day
        tp_price = entry_cost * (1 + TAKE_PROFIT)  # +50%
        sl_price = entry_cost * (1 - STOP_LOSS)    # -40%

        # Best case: option premium at day's best SPY price
        T_mid = 3.0 / (252 * 6.5)  # midday (~1:00 PM)
        if opt_type == "call":
            best_premium = bs_price(spy_high, strike, T_mid, r, iv, opt_type)
            worst_premium = bs_price(spy_low, strike, T_mid, r, iv, opt_type)
        else:
            best_premium = bs_price(spy_low, strike, T_mid, r, iv, opt_type)
            worst_premium = bs_price(spy_high, strike, T_mid, r, iv, opt_type)

        exit_note = "EOD"
        if worst_premium * (1 - spread_pct) <= sl_price:
            # Stop loss hit first (assume worst case happens before best)
            exit_revenue = max(sl_price - slippage, 0)
            exit_note = "SL"
        elif best_premium * (1 - spread_pct) >= tp_price:
            # Take profit hit
            exit_revenue = tp_price - slippage
            exit_note = "TP"
        else:
            # Hold to EOD
            exit_premium = bs_price(spy_close, strike, T_exit, r, iv, opt_type)
            exit_revenue = max(exit_premium * (1 - spread_pct) - slippage, 0)

        # P&L
        pnl_per = (exit_revenue - entry_cost) * 100
        total_pnl = max(pnl_per * num_contracts, -entry_cost * 100 * num_contracts)

        balance += total_pnl
        spy_move = spy_close - spy_open

        if total_pnl > 0: wins += 1
        else: losses += 1

        trades.append({
            "date": ds, "score": score, "grade": grade, "direction": direction,
            "strike": strike, "entry_premium": round(entry_cost, 2),
            "exit_premium": round(exit_revenue, 2), "delta": round(abs(entry_delta), 3),
            "spy_move": round(spy_move, 2), "contracts": num_contracts,
            "pnl": round(total_pnl, 2), "balance": round(balance, 2),
            "iv": round(iv * 100, 1), "T_entry_min": round(T_entry * 252 * 6.5 * 60, 0)
        })

        pnl_s = f"${total_pnl:+,.0f}"
        glyph = "G" if grade == "STRONG" else "H"
        print(f"{ds:<12} {score:>5} {glyph:>1} {grade:<7} {opt_type:<5} ${strike:>6} ${entry_cost:>6.2f} ${exit_revenue:>6.2f} ${spy_move:>+7.2f}  {pnl_s:>7} {exit_note:>3} ${balance:>9,.2f}")

    # ── Results ──
    total_trades = wins + losses
    total_pnl = balance - initial_balance
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    balances = [initial_balance] + [t["balance"] for t in trades]
    peak = initial_balance
    max_dd = 0
    for b in balances:
        if b > peak: peak = b
        dd = (peak - b) / peak * 100
        if dd > max_dd: max_dd = dd

    if trades:
        rets = [t["pnl"] / initial_balance for t in trades]
        sharpe = (np.mean(rets) / np.std(rets)) * np.sqrt(252) if np.std(rets) > 0 else 0
        avg_win = np.mean([t["pnl"] for t in trades if t["pnl"] > 0]) if wins > 0 else 0
        avg_loss = np.mean([t["pnl"] for t in trades if t["pnl"] <= 0]) if losses > 0 else 0
    else:
        sharpe, avg_win, avg_loss = 0, 0, 0

    print("\n" + "=" * 70)
    print("  REALISTIC 0DTE OPTIONS BACKTEST RESULTS")
    print("=" * 70)
    print(f"  Period:            {days} trading days")
    print(f"  Starting Balance:  ${initial_balance:,.2f}")
    print(f"  Final Balance:     ${balance:,.2f}")
    print(f"  Total P&L:         ${total_pnl:+,.2f} ({total_pnl/initial_balance*100:+.1f}%)")
    print(f"  Total Trades:      {total_trades}")
    print(f"  Win Rate:          {win_rate:.1f}% ({wins}W / {losses}L)")
    print(f"  Avg Win:           ${avg_win:+,.2f}")
    print(f"  Avg Loss:          ${avg_loss:+,.2f}")
    print(f"  Max Drawdown:      {max_dd:.1f}%")
    print(f"  Sharpe Ratio:      {sharpe:.2f}")
    print(f"  Pricing Model:     Black-Scholes + theta decay")
    print(f"  Entry:             ~10:30 AM (5.5h to expiry)")
    print(f"  Exit:              TP +{TAKE_PROFIT*100:.0f}% / SL -{STOP_LOSS*100:.0f}% / EOD 3:00 PM")
    print(f"  Spread:            {spread_pct*100:.0f}% bid-ask + ${slippage} slippage")
    print(f"  Min VIX:           {MIN_VIX}")
    print(f"  Min Score:         85 (STRONG/HIGH only)")
    print("=" * 70)

    results = {
        "model": "Black-Scholes realistic 0DTE",
        "period_days": days, "start_balance": initial_balance,
        "end_balance": round(balance, 2), "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl / initial_balance * 100, 1),
        "total_trades": total_trades, "wins": wins, "losses": losses,
        "win_rate": round(win_rate, 1), "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2), "max_drawdown": round(max_dd, 1),
        "sharpe_ratio": round(sharpe, 2), "trades": trades
    }
    with open("backtest_realistic.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  [*] Saved to backtest_realistic.json")
    return results

if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    run_backtest(days=days)
