import sys
import os
import time
import logging
import json
from datetime import datetime
import requests
from dotenv import load_dotenv
import pytz

sys.path.append(os.path.abspath(os.path.dirname(__file__)))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'api')))

from api.data import _fetch_market_bundle, ALL_STOCKS
from engines.score_engine import run_score_engine
from engines.ml_weights import feedback_trade_result

# Setup logging
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger("AutoTrader")

load_dotenv()
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = "https://paper-api.alpaca.markets"

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}

STATE_FILE = "data_cache/bot_state.json"

def get_bot_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"in_position": False, "last_trade_id": None}

def save_bot_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def get_open_positions():
    try:
        r = requests.get(f"{ALPACA_BASE_URL}/v2/positions", headers=HEADERS)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Failed to get positions: {e}")
        return []

def place_bracket_order(symbol, qty, side, take_profit, stop_loss):
    order_data = {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "type": "market",
        "time_in_force": "gtc",
        "order_class": "bracket",
        "take_profit": {
            "limit_price": round(take_profit, 2)
        },
        "stop_loss": {
            "stop_price": round(stop_loss, 2)
        }
    }
    logger.info(f"Submitting Order: {json.dumps(order_data)}")
    r = requests.post(f"{ALPACA_BASE_URL}/v2/orders", json=order_data, headers=HEADERS)
    if r.status_code in [200, 201]:
        logger.info("Order placed successfully!")
        return r.json()
    else:
        logger.error(f"Order failed: {r.text}")
        return None

def main_loop():
    logger.info("MILLI-V3 Auto-Trading Bot Started (Paper Trading)")
    while True:
        try:
            NY = pytz.timezone('America/New_York')
            now = datetime.now(NY)
            
            # 1. Fetch data
            bundle = _fetch_market_bundle(ALL_STOCKS)
            spy_price = bundle["snapshots"].get("SPY", {}).get("c", 0)
            if not spy_price:
                time.sleep(10)
                continue
                
            vix_price = bundle.get("vix", 0)
            vix3m_price = bundle.get("vix3m", 0)
            prev_close = bundle["snapshots"].get("SPY", {}).get("p", spy_price)
            spy_history = bundle["spy_h"]
            pcts = {sym: snap.get("todaysChangePerc", 0) for sym, snap in bundle["snapshots"].items()}
            
            # Simple dummy portfolio for score engine
            portfolio = {"buying_power": 100000, "positions": get_open_positions()}
            
            # 2. Run Score Engine
            score_result = run_score_engine(
                now, spy_price, vix_price, vix3m_price, prev_close,
                spy_price, 1.0, 5.0, pcts, spy_history, portfolio, "REGULAR"
            )
            
            signal = score_result["signal"]
            total_score = score_result["total_score"]
            bias = score_result["direction_bias"]
            
            logger.info(f"Signal: {total_score}/100 [{signal['grade']}] Bias: {bias}")
            
            # 3. Execution Logic
            state = get_bot_state()
            open_pos = len(portfolio["positions"]) > 0
            
            if open_pos:
                # We have an open position, wait for bracket to close it
                if not state["in_position"]:
                    state["in_position"] = True
                    save_bot_state(state)
            else:
                # We are flat, check if we just closed a trade
                if state["in_position"]:
                    state["in_position"] = False
                    save_bot_state(state)
                    # Here we would analyze PnL and feedback to ML engine
                    feedback_trade_result({"pnl": 1, "dominant_layer": "technical"})
                    logger.info("Position closed. Feedback sent to ML Engine.")
                
                # Check entry criteria
                if total_score >= 89 and signal["grade"] == "STRONG" and bias in ["LONG", "SHORT"]:
                    logger.info("🔥 STRONG SIGNAL DETECTED! Executing Auto-Trade...")
                    
                    # We will trade SPY shares as a proxy for ES futures for safety
                    qty = 100
                    side = "buy" if bias == "LONG" else "sell"
                    
                    # ATR for stop loss
                    atr = 2.0 
                    if bias == "LONG":
                        tp = spy_price + (atr * 2)
                        sl = spy_price - (atr * 1.5)
                    else:
                        tp = spy_price - (atr * 2)
                        sl = spy_price + (atr * 1.5)
                        
                    res = place_bracket_order("SPY", qty, side, tp, sl)
                    if res:
                        state["in_position"] = True
                        state["last_trade_id"] = res.get("id")
                        save_bot_state(state)
            
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            
        # Poll every minute
        time.sleep(60)

if __name__ == "__main__":
    main_loop()
