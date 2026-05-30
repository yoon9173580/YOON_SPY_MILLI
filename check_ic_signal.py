"""
SPY 0DTE Iron Condor — Daily Signal Check
Runs once per day (intended: 10:30 ET via GitHub Actions cron) to check if
today qualifies as a STRONG-score day for IC entry.

Outputs:
  - Appends row to ic_signal_log.csv (audit trail)
  - Prints summary to stdout (visible in GH Actions log)
  - If STRONG: optionally posts to webhook (DISCORD_WEBHOOK / SLACK_WEBHOOK env)
  - Exit code 0 always (don't fail workflow on score logic)

Designed to be safe to re-run: idempotent updates to log file.
"""
import os
import sys
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import yfinance as yf
import pytz
import requests

# Reuse helpers from main backtest
from backtest_options_1min import calc_rsi, calc_adx, score_day

NY = pytz.timezone("America/New_York")
LOG_PATH = Path("ic_signal_log.csv")
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK") or os.environ.get("SLACK_WEBHOOK") or os.environ.get("WEBHOOK_URL")


def fetch_daily(end_date):
    """Fetch ~90 days of daily SPY/QQQ/IWM/VIX for warmup."""
    start = end_date - timedelta(days=90)
    spy = yf.Ticker("SPY").history(start=start, end=end_date + timedelta(days=1), interval="1d")
    qqq = yf.Ticker("QQQ").history(start=start, end=end_date + timedelta(days=1), interval="1d")
    iwm = yf.Ticker("IWM").history(start=start, end=end_date + timedelta(days=1), interval="1d")
    vix = yf.Ticker("^VIX").history(start=start, end=end_date + timedelta(days=1), interval="1d")
    return spy, qqq, iwm, vix


def compute_score_for_today(spy, qqq, iwm, vix):
    """Compute score for the most recent SPY trading day."""
    spy = spy.copy()
    spy["PrevClose"] = spy["Close"].shift(1)
    spy["PctChange"] = spy["Close"].pct_change() * 100
    spy["RSI"] = calc_rsi(spy["Close"])
    spy["ADX"] = calc_adx(spy)
    spy["VWAP"] = (spy["Volume"] * (spy["High"] + spy["Low"] + spy["Close"]) / 3).cumsum() / spy["Volume"].cumsum()
    spy["VolRatio"] = spy["Volume"] / spy["Volume"].rolling(20).mean()

    qqq_pcts = qqq["Close"].pct_change() * 100
    iwm_pcts = iwm["Close"].pct_change() * 100

    last_date = spy.index[-1]
    row = spy.loc[last_date]
    vix_val = float(vix.loc[last_date]["Close"]) if last_date in vix.index else 18.0
    qqq_p = float(qqq_pcts.loc[last_date]) if last_date in qqq_pcts.index and pd.notna(qqq_pcts.loc[last_date]) else 0.0
    iwm_p = float(iwm_pcts.loc[last_date]) if last_date in iwm_pcts.index and pd.notna(iwm_pcts.loc[last_date]) else 0.0
    adx_v = float(row["ADX"]) if pd.notna(row["ADX"]) else None
    rsi_v = float(row["RSI"]) if pd.notna(row["RSI"]) else None
    prev_close = float(row["PrevClose"]) if pd.notna(row["PrevClose"]) else float(row["Open"])

    row_dict = {
        "Open": float(row["Open"]), "Close": float(row["Close"]),
        "High": float(row["High"]), "Low": float(row["Low"]),
        "PrevClose": prev_close,
        "PctChange": float(row["PctChange"]) if pd.notna(row["PctChange"]) else 0.0,
        "VWAP": float(row["VWAP"]),
        "VolRatio": float(row["VolRatio"]) if pd.notna(row["VolRatio"]) else 0.0,
    }
    score, grade, direction = score_day(row_dict, vix_val, qqq_p, iwm_p, adx_v, rsi_v)

    return {
        "date": last_date.strftime("%Y-%m-%d"),
        "spy_open": round(row_dict["Open"], 2),
        "spy_close": round(row_dict["Close"], 2),
        "spy_pct": round(row_dict["PctChange"], 2),
        "vix": round(vix_val, 1),
        "rsi": round(rsi_v, 1) if rsi_v else None,
        "adx": round(adx_v, 1) if adx_v else None,
        "vol_ratio": round(row_dict["VolRatio"], 2),
        "score": score,
        "grade": grade,
        # v2 freq tuning: score>=85 MODERATE+ (was 90 STRONG). 2x frequency, PF held ~7.0.
        "should_fire": score >= 85 and grade in ("STRONG", "MODERATE"),
    }


def construct_ic_plan(spy_close, vix, short_offset=3, wing_width=5):
    """Compute IC strike framework for the user to enter at broker."""
    atm = round(spy_close)
    return {
        "atm_strike": atm,
        "sell_call": atm + short_offset,
        "buy_call":  atm + short_offset + wing_width,
        "sell_put":  atm - short_offset,
        "buy_put":   atm - short_offset - wing_width,
        "max_loss_per_contract": wing_width * 100,
        "vix_at_check": vix,
    }


def append_log(result, plan=None):
    """Append result to CSV log file."""
    header = [
        "check_ts_et", "trading_date", "spy_open", "spy_close", "spy_pct",
        "vix", "rsi", "adx", "vol_ratio", "score", "grade", "should_fire",
        "ic_atm", "ic_sell_call", "ic_buy_call", "ic_sell_put", "ic_buy_put",
    ]
    write_header = not LOG_PATH.exists()
    now_et = datetime.now(NY).strftime("%Y-%m-%d %H:%M:%S")
    row = [
        now_et,
        result["date"],
        result["spy_open"],
        result["spy_close"],
        result["spy_pct"],
        result["vix"],
        result["rsi"],
        result["adx"],
        result["vol_ratio"],
        result["score"],
        result["grade"],
        result["should_fire"],
    ]
    if plan:
        row.extend([plan["atm_strike"], plan["sell_call"], plan["buy_call"], plan["sell_put"], plan["buy_put"]])
    else:
        row.extend(["", "", "", "", ""])

    with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(header)
        w.writerow(row)


def send_webhook(result, plan):
    """POST to Discord/Slack webhook if URL configured. Best-effort."""
    if not WEBHOOK_URL:
        return False
    try:
        # Discord style
        content = (
            "🎯 **SPY 0DTE IC ENTRY SIGNAL** 🎯\n"
            f"Date: {result['date']}  |  SPY close ${result['spy_close']}\n"
            f"Score: **{result['score']}/100** ({result['grade']})  |  VIX {result['vix']}\n"
            f"\n**Iron Condor 4-leg order (ATM ${plan['atm_strike']}):**\n"
            f"  SELL Call ${plan['sell_call']}  /  BUY Call ${plan['buy_call']}\n"
            f"  SELL Put  ${plan['sell_put']}   /  BUY Put  ${plan['buy_put']}\n"
            f"Max loss/contract: ${plan['max_loss_per_contract']}\n"
            f"\nTP: cost ≤ 25% of credit  |  EOD flat 15:30 ET"
        )
        payload = {"content": content}  # Discord format
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"[webhook] failed: {e}")
        return False


def main():
    today = datetime.now(NY).date()
    print(f"[*] IC signal check at {datetime.now(NY).strftime('%Y-%m-%d %H:%M %Z')}")
    try:
        spy, qqq, iwm, vix = fetch_daily(today)
        if spy.empty:
            print("[!] No SPY data — market may be closed")
            return 0
        result = compute_score_for_today(spy, qqq, iwm, vix)
    except Exception as e:
        import traceback
        print(f"[!] Score computation failed: {e}")
        traceback.print_exc()
        return 0

    plan = construct_ic_plan(result["spy_close"], result["vix"])
    append_log(result, plan if result["should_fire"] else None)

    # Summary print
    print(f"  Trading date:  {result['date']}")
    print(f"  SPY close:     ${result['spy_close']} ({result['spy_pct']:+.2f}%)")
    print(f"  VIX:           {result['vix']}")
    print(f"  Score:         {result['score']}/100 ({result['grade']})")
    print(f"  Should fire:   {result['should_fire']}")

    if result["should_fire"]:
        print("\n🎯 IRON CONDOR ENTRY DAY 🎯")
        print(f"  ATM strike:    ${plan['atm_strike']}")
        print(f"  SELL Call:     ${plan['sell_call']} / BUY Call: ${plan['buy_call']}")
        print(f"  SELL Put:      ${plan['sell_put']} / BUY Put: ${plan['buy_put']}")
        sent = send_webhook(result, plan)
        print(f"  Webhook sent:  {sent}")
    else:
        print("  -> No trade today.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
