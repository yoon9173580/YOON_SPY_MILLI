"""
MILLI-V3 Auto-Trading Bot — paper SPY proxy for MES futures signal.

Reuses the same market-data fetch and 7-layer score engine that powers /api/data,
so the live bot and dashboard never disagree on what the model said.
"""
import sys
import os
import time
import logging
import json
from datetime import datetime

import requests
import pytz

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False

# Make the api package importable regardless of CWD.
ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "api"))

from api.data import (
    _fetch_market_bundle,
    _snap_price,
    _snap_prev_close,
    _pct,
    load_portfolio,
    ALL_STOCKS,
    STOCK_SYMS,
)
from engines.score_engine import run_score_engine
from engines.ml_weights import feedback_trade_result

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("AutoTrader")

load_dotenv()
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
ALPACA_BASE_URL = "https://paper-api.alpaca.markets"

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY or "",
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY or "",
}

STATE_FILE = "data_cache/bot_state.json"
NY = pytz.timezone("America/New_York")


def get_bot_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"in_position": False, "last_trade_id": None, "last_dominant": None, "entry_equity": None}


def save_bot_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_open_positions():
    """Return Alpaca open positions as a list (empty on failure)."""
    try:
        r = requests.get(f"{ALPACA_BASE_URL}/v2/positions", headers=HEADERS, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Failed to get positions: {e}")
        return []


def get_account_equity():
    try:
        r = requests.get(f"{ALPACA_BASE_URL}/v2/account", headers=HEADERS, timeout=8)
        r.raise_for_status()
        return float(r.json().get("equity", 0))
    except Exception as e:
        logger.error(f"Failed to get account: {e}")
        return None


def place_bracket_order(symbol, qty, side, take_profit, stop_loss):
    order_data = {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "type": "market",
        "time_in_force": "day",
        "order_class": "bracket",
        "take_profit": {"limit_price": round(take_profit, 2)},
        "stop_loss": {"stop_price": round(stop_loss, 2)},
    }
    logger.info(f"Submitting Order: {json.dumps(order_data)}")
    r = requests.post(f"{ALPACA_BASE_URL}/v2/orders", json=order_data, headers=HEADERS, timeout=8)
    if r.status_code in (200, 201):
        logger.info("Order placed successfully")
        return r.json()
    logger.error(f"Order failed: {r.status_code} {r.text}")
    return None


def _dominant_layer(score_result):
    """Pick the layer that contributed the largest share of the total score."""
    layers = score_result.get("layers", {})
    candidates = {
        "regime":      layers.get("regime", {}).get("score", 0) or 0,
        "correlation": layers.get("correlation", {}).get("score", 0) or 0,
        "technical":   layers.get("technical", {}).get("score", 0) or 0,
    }
    if not any(candidates.values()):
        return "technical"
    return max(candidates, key=candidates.get)


def main_loop():
    logger.info("MILLI-V3 Auto-Trading Bot Started (Paper Trading)")
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        logger.error("Alpaca API keys not set — refusing to start. Set ALPACA_API_KEY / ALPACA_SECRET_KEY.")
        return

    while True:
        try:
            now = datetime.now(NY)

            # 1. Pull the same market bundle the dashboard uses.
            bundle = _fetch_market_bundle(ALL_STOCKS)
            snaps = bundle.get("snaps") or {}
            spy_snap = snaps.get("SPY", {})
            spy_price = _snap_price(spy_snap)
            if not spy_price:
                logger.warning("No SPY snapshot — skipping tick")
                time.sleep(10)
                continue

            spy_prev = _snap_prev_close(spy_snap) or spy_price
            vix_p, vix3m_p = bundle.get("vix", (18.0, None))
            spy_h = bundle.get("spy_h")

            # 2. Build the same pcts dict /api/data feeds the score engine.
            pcts = {}
            for sym in STOCK_SYMS:
                s = snaps.get(sym, {})
                pcts[sym] = _pct(_snap_price(s), _snap_prev_close(s) or 1)

            # VWAP / volume / range — keep simple, the engine tolerates approximations.
            vwap = spy_price
            vol_r = 1.0
            d_range = abs(spy_price - spy_prev)

            # 3. Score engine expects the paper-portfolio shape (history/trade_log/positions).
            portfolio = load_portfolio()

            t_min = now.hour * 60 + now.minute
            is_regular = 570 <= t_min <= 960

            score_result = run_score_engine(
                now_et=now,
                spy_price=spy_price,
                vix_price=vix_p,
                vix3m_price=vix3m_p,
                prev_close=spy_prev,
                vwap=vwap,
                vol_ratio=vol_r,
                range_value=d_range,
                pcts=pcts,
                spy_history=spy_h,
                portfolio=portfolio,
                session_name="REGULAR" if is_regular else "CLOSED",
            )

            signal = score_result["signal"]
            total_score = score_result["total_score"]
            bias = score_result["direction_bias"]

            logger.info(f"Signal: {total_score}/100 [{signal['grade']}] Bias: {bias}")

            # 4. Execution logic.
            state = get_bot_state()
            open_positions = get_open_positions()
            has_open = len(open_positions) > 0

            if has_open:
                if not state.get("in_position"):
                    state["in_position"] = True
                    state["entry_equity"] = get_account_equity()
                    save_bot_state(state)
            else:
                if state.get("in_position"):
                    # Position just closed — compute realized PnL and feed it back to ML.
                    new_equity = get_account_equity()
                    entry_eq = state.get("entry_equity")
                    if new_equity is not None and entry_eq is not None:
                        realized = new_equity - entry_eq
                    else:
                        realized = 0.0
                    dominant = state.get("last_dominant") or "technical"
                    feedback_trade_result({"pnl": realized, "dominant_layer": dominant})
                    logger.info(f"Position closed. PnL={realized:.2f} → feedback layer={dominant}")
                    state.update({"in_position": False, "entry_equity": None})
                    save_bot_state(state)

                # Entry criteria — match the dashboard's STRONG-only rule.
                if (
                    is_regular
                    and total_score >= 89
                    and signal.get("grade") == "STRONG"
                    and bias in ("LONG", "SHORT")
                ):
                    logger.info("STRONG SIGNAL DETECTED — submitting bracket order")
                    qty = 100
                    side = "buy" if bias == "LONG" else "sell"

                    atr = max(d_range, 2.0)
                    if bias == "LONG":
                        tp = spy_price + atr * 2
                        sl = spy_price - atr * 1.5
                    else:
                        tp = spy_price - atr * 2
                        sl = spy_price + atr * 1.5

                    res = place_bracket_order("SPY", qty, side, tp, sl)
                    if res:
                        state.update({
                            "in_position": True,
                            "last_trade_id": res.get("id"),
                            "last_dominant": _dominant_layer(score_result),
                            "entry_equity": get_account_equity(),
                        })
                        save_bot_state(state)

        except Exception as e:
            logger.exception(f"Error in main loop: {e}")

        time.sleep(60)


if __name__ == "__main__":
    main_loop()
