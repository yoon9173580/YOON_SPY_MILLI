"""
SPY 0DTE Options Backtest — 1-minute Precise (BS + Intraday Path)

Combines `backtest.py`'s Black-Scholes debit spread pricing with the local
1-minute SPY CSV for accurate intraday TP/SL execution.

What's new vs backtest.py:
  • Path-dependent intraday TP/SL (first-trigger wins) instead of daily H/L
  • Theta decay applied minute-by-minute (T_rem updated each bar)
  • Realistic intraday whipsaw modeling (no look-ahead on stop hits)
  • Uses CSV's vwap column directly

Same as backtest.py:
  • Score formula (regime + correlation + technical + prime window)
  • BS pricing with VIX as IV proxy
  • Dynamic slippage based on VIX and intraday range
  • Sizing: 5% base, 10% on score >= 95
  • Strategy: $5 wide ATM/+5 OTM debit spread

Inputs:
  • SPY_1min_data.csv (local, ~2 years of 1-min OHLCV+VWAP)
  • yfinance daily VIX, QQQ, IWM, SPY (for score)
"""

import math
import json
import sys
from datetime import datetime, timedelta, time as dt_time
import pandas as pd
import numpy as np
import pytz
import yfinance as yf
from scipy.stats import norm

NY = pytz.timezone("America/New_York")
TRADING_MIN_PER_YEAR = 252 * 390  # 98,280

# Session hours (ET)
SESSION_OPEN  = dt_time(9, 30)
ENTRY_TIME    = dt_time(10, 30)
EOD_EXIT_TIME = dt_time(15, 30)  # match backtest.py (15:30 ET exit, preserve time value)
SESSION_CLOSE = dt_time(16, 0)

CSV_PATH = "SPY_1min_data.csv"


# ── Black-Scholes (identical to backtest.py) ──────────────────────
def bs_price(S, K, T, r, sigma, opt="call"):
    if T <= 0:
        return max(S - K, 0) if opt == "call" else max(K - S, 0)
    if sigma <= 0 or S <= 0 or K <= 0:
        return max(S - K, 0) if opt == "call" else max(K - S, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if opt == "call":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def spread_value(S, K_buy, K_sell, T_rem, r, iv, opt):
    """Net debit spread value: long(K_buy) - short(K_sell), floored at 0."""
    lp = bs_price(S, K_buy, T_rem, r, iv, opt)
    sp = bs_price(S, K_sell, T_rem, r, iv, opt)
    return max(lp - sp, 0)


def dynamic_slippage(vix, spy_range_pct=0.0):
    if vix >= 30 or spy_range_pct >= 2.0:
        return 0.08
    if vix >= 25 or spy_range_pct >= 1.5:
        return 0.06
    if vix >= 20 or spy_range_pct >= 1.0:
        return 0.05
    return 0.03


# ── Indicators (identical to backtest.py) ─────────────────────────
def calc_rsi(series, period=14):
    d = series.diff()
    g = d.where(d > 0, 0.0).rolling(period).mean()
    l = (-d.where(d < 0, 0.0)).rolling(period).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))


def calc_adx(df, period=14):
    if len(df) < period + 1:
        return pd.Series(dtype=float)
    h, l, c = df["High"], df["Low"], df["Close"]
    pm = h.diff().where((h.diff() > l.diff().abs()) & (h.diff() > 0), 0.0)
    mm = l.diff().abs().where((l.diff().abs() > h.diff()) & (l.diff().abs() > 0), 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    pdi = 100 * (pm.rolling(period).mean() / atr)
    mdi = 100 * (mm.rolling(period).mean() / atr)
    dx = (pdi - mdi).abs() / (pdi + mdi) * 100
    return dx.rolling(period).mean()


def score_day(row, vix, qqq_pct, iwm_pct, adx, rsi):
    """Same scoring as backtest.py — daily-resolution features."""
    vix_sc = 15 if 14 <= vix <= 20 else (0 if vix <= 30 else -20) if vix > 20 else -5
    adx_sc = 15 if adx and adx >= 25 else (5 if adx and adx >= 20 else 0)
    gap = ((row["Open"] / row["PrevClose"]) - 1) * 100 if row.get("PrevClose") else 0
    regime = vix_sc + adx_sc + (5 if abs(gap) > 0.5 else 0)

    sp = row.get("PctChange", 0)
    qa = (sp >= 0 and qqq_pct >= 0) or (sp < 0 and qqq_pct < 0)
    ss = all(v >= 0 for v in [sp, qqq_pct, iwm_pct]) or all(v < 0 for v in [sp, qqq_pct, iwm_pct])
    corr = max(0, min(20, (10 if qa else -5) + (5 if iwm_pct > 0.3 else (-3 if iwm_pct < -0.3 else 0)) + (5 if ss else 0)))

    vwap = row.get("VWAP", row["Close"])
    vr = row.get("VolRatio", 0)
    dr = row["High"] - row["Low"]
    direction = "CALL" if row["Open"] > vwap else "PUT"
    vol_sc = 10 if vr >= 2.0 else (7 if vr >= 1.5 else (3 if vr >= 1.0 else 0))
    rng_sc = 10 if dr >= 3.0 else (5 if dr >= 2.0 else 0)
    rsi_sc = 10 if rsi and (rsi >= 60 or rsi <= 40) else 0
    tech = min(30, 10 + vol_sc + rng_sc + rsi_sc)

    raw = regime + corr + 20 + tech
    norm_score = max(0, int((raw / 110) * 100))
    grade = "STRONG" if norm_score >= 90 else "MODERATE" if norm_score >= 75 else "WEAK" if norm_score >= 60 else "NONE"
    return norm_score, grade, direction


# ── Data loading ──────────────────────────────────────────────────
def load_1min(csv_path):
    print(f"[*] Loading 1-min CSV: {csv_path}")
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    # CSV timestamps are in ET (per inspection of 08:00 pre-market opening)
    df["timestamp"] = df["timestamp"].dt.tz_localize(NY, ambiguous="infer", nonexistent="shift_forward")
    df["date"] = df["timestamp"].dt.date
    df["time_of_day"] = df["timestamp"].dt.time
    # Filter to regular session: 9:30 to 16:00 ET
    mask = (df["time_of_day"] >= SESSION_OPEN) & (df["time_of_day"] < SESSION_CLOSE)
    df = df[mask].copy()
    df = df.set_index("timestamp")
    print(f"    Loaded {len(df):,} regular-session bars across {df['date'].nunique()} trading days")
    return df


def fetch_daily(start_date, end_date):
    """Fetch daily SPY/QQQ/IWM/VIX via yfinance with warmup buffer."""
    yf_start = pd.Timestamp(start_date) - timedelta(days=60)
    yf_end   = pd.Timestamp(end_date) + timedelta(days=1)
    print(f"[*] Fetching daily data ({yf_start.date()} to {yf_end.date()})...")
    spy_d = yf.Ticker("SPY").history(start=yf_start, end=yf_end, interval="1d")
    qqq_d = yf.Ticker("QQQ").history(start=yf_start, end=yf_end, interval="1d")
    iwm_d = yf.Ticker("IWM").history(start=yf_start, end=yf_end, interval="1d")
    vix_d = yf.Ticker("^VIX").history(start=yf_start, end=yf_end, interval="1d")
    return spy_d, qqq_d, iwm_d, vix_d


def prep_daily_features(spy_d):
    spy_d["PrevClose"] = spy_d["Close"].shift(1)
    spy_d["PctChange"] = spy_d["Close"].pct_change() * 100
    spy_d["RSI"] = calc_rsi(spy_d["Close"])
    spy_d["ADX"] = calc_adx(spy_d)
    spy_d["VWAP"] = (spy_d["Volume"] * (spy_d["High"] + spy_d["Low"] + spy_d["Close"]) / 3).cumsum() / spy_d["Volume"].cumsum()
    spy_d["VolRatio"] = spy_d["Volume"] / spy_d["Volume"].rolling(20).mean()
    spy_d["SMA50"] = spy_d["Close"].rolling(50).mean()
    spy_d["SMA20"] = spy_d["Close"].rolling(20).mean()
    return spy_d


def get_aligned(series, target_date, max_offset=3):
    """Get value from a daily series aligned to target_date, with fallback to nearest prior."""
    for offset in range(max_offset + 1):
        check = pd.Timestamp(target_date) - timedelta(days=offset)
        # Match by date ignoring tz
        matches = [idx for idx in series.index if idx.date() == check.date()]
        if matches:
            v = series.loc[matches[0]]
            if pd.notna(v):
                return float(v)
    return None


# ── Intraday simulation ───────────────────────────────────────────
def minutes_to_close(current_time):
    """Minutes from current_time until 16:00 ET."""
    delta = (datetime.combine(datetime.today(), SESSION_CLOSE)
             - datetime.combine(datetime.today(), current_time))
    return max(int(delta.total_seconds() / 60), 0)


def simulate_intraday(day_bars, K_buy, K_sell, net_debit, opt, iv, r,
                      tp_pct, sl_pct, slip, spread_pct):
    """
    Walk 1-min bars from entry+1 to 15:55, return (exit_val, exit_note, exit_time).
    First TP or SL trigger wins. EOD if neither triggered.
    """
    tp_price = net_debit * (1 + tp_pct)
    sl_price = max(net_debit * (1 - sl_pct), 0)  # floored at 0

    # Bars after entry: from 10:31 to 15:55 inclusive
    entry_bar_time = ENTRY_TIME
    end_check_time = EOD_EXIT_TIME

    walk = day_bars[(day_bars["time_of_day"] > entry_bar_time)
                  & (day_bars["time_of_day"] <= end_check_time)]

    for ts, bar in walk.iterrows():
        t_rem_min = minutes_to_close(bar["time_of_day"])
        T_rem = max(t_rem_min, 1) / TRADING_MIN_PER_YEAR
        S_now = float(bar["close"])

        # Use bar's high to check optimistic exit, bar's low for pessimistic
        # For calls: spread value moves WITH price (rises with S)
        # For puts:  spread value moves AGAINST price (rises as S falls)
        if opt == "call":
            high_val = spread_value(float(bar["high"]), K_buy, K_sell, T_rem, r, iv, opt)
            low_val  = spread_value(float(bar["low"]),  K_buy, K_sell, T_rem, r, iv, opt)
        else:
            high_val = spread_value(float(bar["low"]),  K_buy, K_sell, T_rem, r, iv, opt)
            low_val  = spread_value(float(bar["high"]), K_buy, K_sell, T_rem, r, iv, opt)

        # Conservative: check SL first (loser wins ties)
        if low_val * (1 - spread_pct) <= sl_price:
            return max(sl_price - slip, 0), "SL", bar["time_of_day"]
        if high_val * (1 - spread_pct) >= tp_price:
            return tp_price - slip, "TP", bar["time_of_day"]

    # EOD: use last bar's close value
    if len(walk) == 0:
        return net_debit, "NODATA", None
    last_bar = walk.iloc[-1]
    t_rem_min = minutes_to_close(last_bar["time_of_day"])
    T_rem = max(t_rem_min, 1) / TRADING_MIN_PER_YEAR
    eod_val = spread_value(float(last_bar["close"]), K_buy, K_sell, T_rem, r, iv, opt)
    return max(eod_val * (1 - spread_pct) - slip, 0), "EOD", last_bar["time_of_day"]


# ── Main backtest ─────────────────────────────────────────────────
def run_backtest(start_date=None, end_date=None, balance=2000.0,
                 min_score=90, tp_pct=1.00, sl_pct=1.00, spread_width=5,
                 direction_mode="cum_vwap", regime_filter="none",
                 vix_max=None, vix_min=None):
    """direction_mode: cum_vwap | orb | first_hour | intraday_vwap
       regime_filter: none | sma50 | sma20 | both_sma
       vix_max: skip if VIX > vix_max
       vix_min: skip if VIX < vix_min
    """
    print("=" * 80)
    print("  SPY 0DTE BACKTEST — 1-MIN PRECISE (BS + Intraday Path)")
    print("=" * 80)

    # Load data
    df_1min = load_1min(CSV_PATH)
    csv_start = df_1min["date"].min()
    csv_end   = df_1min["date"].max()
    if start_date is None:
        start_date = csv_start
    else:
        start_date = pd.Timestamp(start_date).date()
    if end_date is None:
        end_date = csv_end
    else:
        end_date = pd.Timestamp(end_date).date()
    print(f"    Backtest window: {start_date} to {end_date}")

    spy_d, qqq_d, iwm_d, vix_d = fetch_daily(start_date, end_date)
    if spy_d.empty:
        print("ERROR: yfinance returned no data"); return

    spy_d = prep_daily_features(spy_d)
    qqq_pcts = qqq_d["Close"].pct_change() * 100
    iwm_pcts = iwm_d["Close"].pct_change() * 100

    # Constants
    r = 0.05
    SPREAD_PCT = 0.03

    trades = []
    wins, losses = 0, 0
    initial_balance = balance

    # Group 1-min by date
    grouped = dict(tuple(df_1min.groupby("date")))
    trading_dates = sorted([d for d in grouped.keys() if start_date <= d <= end_date])
    print(f"[*] Simulating {len(trading_dates)} trading days...\n")

    hdr = f"{'Date':<11} {'Sc':>3} {'G':<2} {'Dir':<4} {'K':>5}/{'>5':<3} {'Debit':>6} {'Exit':>6} {'Move':>7} {'P&L':>7} {'Ex':>4} {'Bal':>10}"
    print(hdr)
    print("-" * len(hdr))

    for date in trading_dates:
        ds = date.strftime("%m/%d")
        # yfinance index alignment
        date_ts = pd.Timestamp(date)
        try:
            spy_row = None
            for idx in spy_d.index:
                if idx.date() == date:
                    spy_row = spy_d.loc[idx]
                    break
            if spy_row is None:
                continue
        except Exception:
            continue

        prev_close = float(spy_row["PrevClose"]) if pd.notna(spy_row.get("PrevClose")) else None
        if prev_close is None:
            continue

        # Daily features for scoring
        day_bars = grouped[date].copy()
        if len(day_bars) < 60:  # need enough bars for the day
            continue

        spy_o = float(day_bars.iloc[0]["open"])
        spy_h = float(day_bars["high"].max())
        spy_l = float(day_bars["low"].min())
        spy_c = float(day_bars.iloc[-1]["close"])

        # Use cumulative VWAP from daily features (matches backtest.py exactly)
        # NOTE: backtest.py's "VWAP" is cumulative across all history, not per-day.
        # This biases direction toward CALL in long bull runs (Open usually > cum VWAP).
        # We replicate this for direct comparability to backtest.py.
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
        score, grade, direction = score_day(row_dict, vix_val, qqq_p, iwm_p, adx_v, rsi_v)

        if score < min_score or grade != "STRONG":
            print(f"{ds:<11} {score:>3} {'X':<2} {grade:<4} {'':>5} {'':>3} {'':>6} {'':>6} {'':>7} {'SKIP':>7} {'':>4} ${balance:>9,.0f}")
            continue

        # ─── Regime filter (v2 NEW) ───
        sma50 = float(spy_row["SMA50"]) if pd.notna(spy_row.get("SMA50")) else None
        sma20 = float(spy_row["SMA20"]) if pd.notna(spy_row.get("SMA20")) else None

        if regime_filter == "sma50" and sma50 is not None and spy_c < sma50:
            print(f"{ds:<11} {score:>3} {'R':<2} {'<S50':<4} {'':>5} {'':>3} {'':>6} {'':>6} {'':>7} {'SKIP':>7} {'':>4} ${balance:>9,.0f}")
            continue
        if regime_filter == "sma20" and sma20 is not None and spy_c < sma20:
            print(f"{ds:<11} {score:>3} {'R':<2} {'<S20':<4} {'':>5} {'':>3} {'':>6} {'':>6} {'':>7} {'SKIP':>7} {'':>4} ${balance:>9,.0f}")
            continue
        if regime_filter == "both_sma":
            if sma50 is not None and spy_c < sma50:
                print(f"{ds:<11} {score:>3} {'R':<2} {'<S50':<4} {'':>5} {'':>3} {'':>6} {'':>6} {'':>7} {'SKIP':>7} {'':>4} ${balance:>9,.0f}")
                continue
            if sma20 is not None and spy_c < sma20:
                print(f"{ds:<11} {score:>3} {'R':<2} {'<S20':<4} {'':>5} {'':>3} {'':>6} {'':>6} {'':>7} {'SKIP':>7} {'':>4} ${balance:>9,.0f}")
                continue
        if vix_max is not None and vix_val > vix_max:
            print(f"{ds:<11} {score:>3} {'V':<2} {'>VX':<4} {'':>5} {'':>3} {'':>6} {'':>6} {'':>7} {'SKIP':>7} {'':>4} ${balance:>9,.0f}")
            continue
        if vix_min is not None and vix_val < vix_min:
            print(f"{ds:<11} {score:>3} {'V':<2} {'<VX':<4} {'':>5} {'':>3} {'':>6} {'':>6} {'':>7} {'SKIP':>7} {'':>4} ${balance:>9,.0f}")
            continue

        # Find 10:30 AM entry bar
        entry_bars = day_bars[day_bars["time_of_day"] == ENTRY_TIME]
        if len(entry_bars) == 0:
            entry_bars = day_bars[day_bars["time_of_day"] >= ENTRY_TIME]
            if len(entry_bars) == 0:
                continue
        entry_bar = entry_bars.iloc[0]
        S_entry = float(entry_bar["close"])

        # ─── DIRECTION OVERRIDE based on mode ───
        if direction_mode == "orb":
            orb_bars = day_bars[(day_bars["time_of_day"] >= SESSION_OPEN)
                              & (day_bars["time_of_day"] < dt_time(10, 0))]
            if len(orb_bars) < 5:
                print(f"{ds:<11} {score:>3} {'X':<2} {'NORB':<4} {'':>5} {'':>3} {'':>6} {'':>6} {'':>7} {'SKIP':>7} {'':>4} ${balance:>9,.0f}")
                continue
            orb_high = float(orb_bars["high"].max())
            orb_low  = float(orb_bars["low"].min())
            if S_entry > orb_high:
                direction = "CALL"
            elif S_entry < orb_low:
                direction = "PUT"
            else:
                print(f"{ds:<11} {score:>3} {'X':<2} {'INRG':<4} {'':>5} {'':>3} {'':>6} {'':>6} {'':>7} {'SKIP':>7} {'':>4} ${balance:>9,.0f}")
                continue
        elif direction_mode == "first_hour":
            # 9:30 open vs 10:30 entry %
            open_bar = day_bars[day_bars["time_of_day"] == SESSION_OPEN]
            if len(open_bar) == 0:
                continue
            S_open = float(open_bar.iloc[0]["open"])
            fh_pct = ((S_entry / S_open) - 1.0) * 100.0
            if fh_pct > 0.3:
                direction = "CALL"
            elif fh_pct < -0.3:
                direction = "PUT"
            else:
                print(f"{ds:<11} {score:>3} {'X':<2} {'FLAT':<4} {'':>5} {'':>3} {'':>6} {'':>6} {'':>7} {'SKIP':>7} {'':>4} ${balance:>9,.0f}")
                continue
        elif direction_mode == "intraday_vwap":
            entry_vwap = float(entry_bar["vwap"]) if pd.notna(entry_bar["vwap"]) else S_entry
            if S_entry > entry_vwap:
                direction = "CALL"
            elif S_entry < entry_vwap:
                direction = "PUT"
            else:
                continue
        # else: cum_vwap (already set by score_day)

        opt = "call" if direction == "CALL" else "put"
        iv = vix_val / 100.0

        K_buy = round(S_entry)
        K_sell = K_buy + spread_width if opt == "call" else K_buy - spread_width

        T_entry = minutes_to_close(ENTRY_TIME) / TRADING_MIN_PER_YEAR
        long_entry  = bs_price(S_entry, K_buy,  T_entry, r, iv, opt)
        short_entry = bs_price(S_entry, K_sell, T_entry, r, iv, opt)

        spy_range_pct = ((spy_h - spy_l) / spy_o) * 100 if spy_o > 0 else 0
        slip = dynamic_slippage(vix_val, spy_range_pct)

        net_debit = (long_entry - short_entry) * (1 + SPREAD_PCT) + slip * 2
        if net_debit <= 0.05:
            continue

        # Sizing
        if score >= 95:
            risk_pct = 0.10
        elif vix_val >= 25:
            risk_pct = 0.08
        elif vix_val >= 20:
            risk_pct = 0.06
        else:
            risk_pct = 0.05

        max_risk = balance * risk_pct
        num_contracts = max(1, int(max_risk / (net_debit * 100)))

        # Intraday simulation
        exit_val, exit_note, exit_time = simulate_intraday(
            day_bars, K_buy, K_sell, net_debit, opt, iv, r,
            tp_pct, sl_pct, slip, SPREAD_PCT
        )

        pnl_per   = (exit_val - net_debit) * 100
        total_pnl = pnl_per * num_contracts
        # Can't lose more than debit paid
        total_pnl = max(total_pnl, -net_debit * 100 * num_contracts)

        balance += total_pnl
        if total_pnl > 0:
            wins += 1
        else:
            losses += 1

        spy_move = spy_c - spy_o
        g = "G" if grade == "STRONG" else "H"

        trades.append({
            "date": str(date), "score": score, "grade": grade,
            "direction": direction, "K_buy": K_buy, "K_sell": K_sell,
            "S_entry": round(S_entry, 2),
            "net_debit": round(net_debit, 2), "exit_val": round(exit_val, 2),
            "spy_move": round(spy_move, 2), "contracts": num_contracts,
            "pnl": round(total_pnl, 2), "balance": round(balance, 2),
            "vix": round(vix_val, 1), "exit_type": exit_note,
            "exit_time": str(exit_time) if exit_time else "",
            "slippage": slip,
        })

        print(f"{ds:<11} {score:>3} {g:<2} {opt:<4} {K_buy:>5}/{K_sell:>3} ${net_debit:>5.2f} ${exit_val:>5.2f} ${spy_move:>+6.2f} ${total_pnl:>+6.0f} {exit_note:>4} ${balance:>9,.0f}")

    # ── Summary ──
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

    # Exit breakdown
    exit_counts = {}
    for t in trades:
        exit_counts[t["exit_type"]] = exit_counts.get(t["exit_type"], 0) + 1

    print("\n" + "=" * 80)
    print("  BACKTEST RESULTS — 1-MIN PRECISE OPTIONS")
    print("=" * 80)
    print(f"  Period:            {start_date} to {end_date}")
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
    print(f"  ---")
    print(f"  Strategy:          ${spread_width} wide debit spread (ATM/OTM)")
    print(f"  Exit:              TP +{tp_pct*100:.0f}% / SL -{sl_pct*100:.0f}% / EOD (intraday path)")
    print(f"  Score filter:      >= {min_score} (95+ = 2x size)")
    print(f"  Resolution:        1-minute path with bar high/low TP-SL check")
    print("=" * 80)

    results = {
        "model": "1-min Precise Options BS Debit Spread",
        "period_start": str(start_date),
        "period_end": str(end_date),
        "start_balance": initial_balance,
        "end_balance": round(balance, 2),
        "total_pnl": round(total_pnl, 2),
        "pnl_pct": round(total_pnl / initial_balance * 100, 1),
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "avg_win": round(avg_w, 2),
        "avg_loss": round(avg_l, 2),
        "profit_factor": pf if pf != float("inf") else None,
        "max_drawdown": round(max_dd, 1),
        "sharpe": round(sharpe, 2),
        "exit_breakdown": exit_counts,
        "trades": trades,
    }
    out_path = "backtest_options_1min.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  [*] Saved to {out_path}")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SPY 0DTE 1-min precise options backtest")
    parser.add_argument("dates", nargs="*", help="Optional: start_date [end_date] (YYYY-MM-DD)")
    parser.add_argument("--balance",   type=float, default=2000.0, help="Starting balance ($)")
    parser.add_argument("--min-score", type=int,   default=90,     help="Minimum score for entry")
    parser.add_argument("--tp",        type=float, default=1.00,   help="Take profit (fraction of debit, 1.00 = +100%)")
    parser.add_argument("--sl",        type=float, default=1.00,   help="Stop loss (fraction of debit, 1.00 = no SL)")
    parser.add_argument("--width",     type=int,   default=5,      help="Debit spread width ($)")
    parser.add_argument("--direction", type=str,   default="cum_vwap",
                        choices=["cum_vwap", "orb", "first_hour", "intraday_vwap"],
                        help="Direction signal mode")
    parser.add_argument("--regime",    type=str,   default="none",
                        choices=["none", "sma50", "sma20", "both_sma"],
                        help="Regime filter (skip when SPY below SMA)")
    parser.add_argument("--vix-max",   type=float, default=None, help="Skip if VIX > this")
    parser.add_argument("--vix-min",   type=float, default=None, help="Skip if VIX < this")
    args = parser.parse_args()

    start_date = args.dates[0] if len(args.dates) >= 1 else None
    end_date   = args.dates[1] if len(args.dates) >= 2 else None

    run_backtest(
        start_date=start_date,
        end_date=end_date,
        balance=args.balance,
        min_score=args.min_score,
        tp_pct=args.tp,
        sl_pct=args.sl,
        spread_width=args.width,
        direction_mode=args.direction,
        regime_filter=args.regime,
        vix_max=args.vix_max,
        vix_min=args.vix_min,
    )
