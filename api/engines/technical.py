"""
LAYER 6 — Technical Entry Triggers
Layer 1~5가 모두 통과(스코어 60+)한 경우에만 실행.
이 레이어에서 실제 Long/Short 방향 결정.
"""
import pandas as pd


def _score_vwap_position(spy_price: float, vwap: float) -> tuple:
    """Price vs VWAP: above = Long bias, below = Short bias.

    When VWAP isn't available (None/0) or equals price exactly, return NEUTRAL
    with 0 points — otherwise /api/data injects a phantom SHORT vote with 10
    points every time bar data is missing (vwap defaults to spy_p upstream).
    """
    if spy_price is None or vwap is None or vwap <= 0:
        return 0, "NEUTRAL", "VWAP data unavailable"
    dist = spy_price - vwap
    if dist == 0:
        return 0, "NEUTRAL", "Price at VWAP — no directional edge"
    pct = (dist / vwap) * 100

    if dist > 0:
        return 10, "LONG", f"Above VWAP by ${dist:+.2f} ({pct:+.2f}%)"
    else:
        return 10, "SHORT", f"Below VWAP by ${dist:+.2f} ({pct:+.2f}%)"


def _score_vwap_bands(spy_price: float, vwap: float, spy_history: pd.DataFrame) -> tuple:
    """
    VWAP ± 1SD, ± 2SD band analysis.
    Overextension beyond 2SD → fading opportunity.

    Uses volume-weighted deviation of each bar's typical price from the
    cumulative session VWAP at that bar — not the current VWAP applied
    across the whole series (the previous approximation under-weighted
    early-session deviation).
    """
    if (spy_history is None or spy_history.empty or vwap is None
            or vwap <= 0 or spy_price is None):
        return 0, "Band data unavailable", "NEUTRAL"

    try:
        tp = (spy_history["High"] + spy_history["Low"] + spy_history["Close"]) / 3.0
        vol = spy_history["Volume"].astype(float)
        total_vol = float(vol.sum())
        if total_vol <= 0:
            return 0, "Band data unavailable (no volume)", "NEUTRAL"

        cum_vol = vol.cumsum().replace(0, pd.NA)
        rolling_vwap = (tp * vol).cumsum() / cum_vol
        dev_sq = (tp - rolling_vwap) ** 2
        weighted_var = (dev_sq * vol).sum() / total_vol
        deviation = float(weighted_var ** 0.5)
        if deviation <= 0:
            return 0, "Band data degenerate", "NEUTRAL"

        upper_1sd = vwap + deviation
        lower_1sd = vwap - deviation
        upper_2sd = vwap + 2 * deviation
        lower_2sd = vwap - 2 * deviation

        if spy_price > upper_2sd:
            return -5, "Over-extended above 2SD — fade risk", "SHORT_FADE"
        elif spy_price < lower_2sd:
            return -5, "Over-extended below 2SD — fade risk", "LONG_FADE"
        elif spy_price > upper_1sd:
            return 5, "Above 1SD — momentum", "LONG"
        elif spy_price < lower_1sd:
            return 5, "Below 1SD — momentum", "SHORT"
        else:
            return 0, "Within 1SD — neutral zone", "NEUTRAL"
    except Exception:
        return 0, "Band calculation error", "NEUTRAL"


def _score_volume(vol_ratio: float) -> tuple:
    """Volume confirmation: breakout/bounce needs 1.5x+ average volume."""
    if vol_ratio is None:
        return 0, "Volume data unavailable"
    if vol_ratio >= 2.0:
        return 10, f"Strong volume {vol_ratio:.2f}x — confirmed"
    elif vol_ratio >= 1.5:
        return 7, f"Adequate volume {vol_ratio:.2f}x"
    elif vol_ratio >= 1.0:
        return 3, f"Normal volume {vol_ratio:.2f}x"
    else:
        return 0, f"Weak volume {vol_ratio:.2f}x — caution"


def _score_range(range_value: float, atr: float = None) -> tuple:
    """
    Daily range adequacy check.
    If already moved most of ATR, overextension risk.
    """
    if range_value is None:
        return 0, "Range data unavailable"
    if atr and atr > 0:
        ratio = range_value / atr
        if ratio > 1.5:
            return 0, f"Range ${range_value:.2f} = {ratio:.1f}x ATR — overextended"
        elif ratio > 1.0:
            return 5, f"Range ${range_value:.2f} = {ratio:.1f}x ATR — extended"
        else:
            return 10, f"Range ${range_value:.2f} = {ratio:.1f}x ATR — room to move"
    else:
        if range_value >= 3.0:
            return 10, f"Range ${range_value:.2f} — adequate"
        elif range_value >= 2.0:
            return 5, f"Range ${range_value:.2f} — marginal"
        else:
            return 0, f"Range ${range_value:.2f} — too tight"


def _calculate_rsi(spy_history: pd.DataFrame, period: int = 14) -> float:
    """Calculate RSI from close prices."""
    if spy_history is None or len(spy_history) < period + 1:
        return None
    try:
        delta = spy_history["Close"].diff()
        gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
        rs = gain / loss.replace(0, pd.NA)
        rsi = 100 - (100 / (1 + rs))
        last = rsi.dropna()
        return float(last.iloc[-1]) if not last.empty else None
    except Exception:
        return None


def _score_momentum(rsi: float) -> tuple:
    """RSI momentum: >60 = Long bias, <40 = Short bias."""
    if rsi is None:
        return 0, "NEUTRAL", "RSI unavailable"
    if rsi >= 70:
        return 5, "LONG", f"RSI {rsi:.1f} — Overbought (caution)"
    elif rsi >= 60:
        return 10, "LONG", f"RSI {rsi:.1f} — Bullish momentum"
    elif rsi <= 30:
        return 5, "SHORT", f"RSI {rsi:.1f} — Oversold (caution)"
    elif rsi <= 40:
        return 10, "SHORT", f"RSI {rsi:.1f} — Bearish momentum"
    else:
        return 0, "NEUTRAL", f"RSI {rsi:.1f} — Neutral"


def calculate_technical_score(spy_price: float, vwap: float,
                              vol_ratio: float, range_value: float,
                              spy_history: pd.DataFrame = None) -> dict:
    """
    Calculate Layer 6 technical entry trigger score.

    Returns
    -------
    dict with keys:
        score          : int (max ~30)
        max            : int (30)
        direction_bias : str (LONG / SHORT / NEUTRAL)
        details        : dict — component breakdown
        rsi            : float or None
    """
    details = {}

    # VWAP position (primary signal — weight 2)
    vwap_score, vwap_dir, vwap_detail = _score_vwap_position(spy_price, vwap)
    details["vwap_position"] = {"score": vwap_score, "detail": vwap_detail}

    # VWAP bands (momentum 1SD ±1, over-extension 2SD ignored in trend vote)
    band_score, band_detail, band_dir = _score_vwap_bands(spy_price, vwap, spy_history)
    details["vwap_bands"] = {"score": band_score, "detail": band_detail, "region": band_dir}

    # Volume
    vol_score, vol_detail = _score_volume(vol_ratio)
    details["volume"] = {"score": vol_score, "detail": vol_detail}

    # Range
    range_score, range_detail = _score_range(range_value)
    details["range"] = {"score": range_score, "detail": range_detail}

    # RSI Momentum (weight 1)
    rsi = _calculate_rsi(spy_history)
    rsi_score, rsi_dir, rsi_detail = _score_momentum(rsi)
    details["momentum"] = {"score": rsi_score, "detail": rsi_detail}

    # Total (cap at 30)
    total = min(30, vwap_score + band_score + vol_score + range_score + rsi_score)

    # ── Weighted directional vote ────────────────────────────────
    # Trend signals (vwap pos, RSI, 1SD bands) contribute; 2SD fade signals
    # are over-extension warnings and are *not* used here — counter-trend
    # mode uses them separately in the score engine.
    bias_sum = 0
    if vwap_dir == "LONG":
        bias_sum += 2
    elif vwap_dir == "SHORT":
        bias_sum -= 2
    if rsi_dir == "LONG":
        bias_sum += 1
    elif rsi_dir == "SHORT":
        bias_sum -= 1
    if band_dir == "LONG":   # 1SD momentum
        bias_sum += 1
    elif band_dir == "SHORT":
        bias_sum -= 1

    # Require ≥2 net votes (i.e. VWAP confirmed OR multi-signal agreement)
    if bias_sum >= 2:
        direction = "LONG"
    elif bias_sum <= -2:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    return {
        "score": total,
        "max": 30,
        "direction_bias": direction,
        "details": details,
        "rsi": rsi,
    }
