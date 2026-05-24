import os
import json
import time

CACHE_FILE = os.path.join("data_cache", "ml_weights.json")

class AdaptiveWeightEngine:
    def __init__(self):
        self.weights = {
            "technical": 1.0,
            "regime": 1.0,
            "flow": 1.0,
            "correlation": 1.0,
            "last_updated": 0
        }
        self.load_weights()

    def load_weights(self):
        """Load weights from local cache if available."""
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    data = json.load(f)
                    # Validate keys
                    for k in self.weights.keys():
                        if k in data:
                            self.weights[k] = data[k]
            except Exception:
                pass

    def save_weights(self):
        """Save weights to local cache."""
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        self.weights["last_updated"] = time.time()
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(self.weights, f, indent=2)
        except Exception:
            pass

    def get_multipliers(self):
        """Return the current multipliers for scoring layers."""
        return {
            "technical": self.weights["technical"],
            "regime": self.weights["regime"],
            "flow": self.weights["flow"],
            "correlation": self.weights["correlation"]
        }

    def update_weights(self, trade_result: dict):
        """
        Adjust weights based on trade outcome (Reinforcement feedback).
        trade_result should contain:
        - "pnl": float (profit or loss)
        - "dominant_layer": str (the layer that contributed most to entry)
        """
        pnl = trade_result.get("pnl", 0)
        dominant = trade_result.get("dominant_layer")
        
        if not dominant or dominant not in self.weights:
            return

        # Simple learning rate
        lr = 0.05
        
        if pnl > 0:
            # Reward
            self.weights[dominant] = min(1.5, self.weights[dominant] + lr)
        elif pnl < 0:
            # Punish
            self.weights[dominant] = max(0.5, self.weights[dominant] - lr)

        self.save_weights()

# Singleton instance
_ml_engine = AdaptiveWeightEngine()

def get_ml_multipliers():
    return _ml_engine.get_multipliers()

def feedback_trade_result(trade_result):
    _ml_engine.update_weights(trade_result)
