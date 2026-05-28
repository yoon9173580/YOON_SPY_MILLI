"""
Iron Condor signal — shared safety filters + /api/data integration.

핵심:
  • IC의 코어 스코어링(backtest_options_1min.score_day)은 보존.
  • Macro Gate (FOMC/CPI/NFP) + VIX>25 가드만 공유 — 안전망 통합.
  • /api/data 응답에 ic_signal 객체로 노출.

The scoring is inlined here (vs imported from repo root) so Vercel's
api/-only deploy can still compute it. Kept BYTE-IDENTICAL to
backtest_options_1min.score_day so the live signal matches the backtest.
"""


def _ic_score_day(row, vix, qqq_pct, iwm_pct, adx, rsi):
    """Vendored copy of backtest_options_1min.score_day — keep in sync.

    4-layer normalized 0-100 score. Used to ID STRONG-score days
    (>=90 STRONG grade) for IC entry. Direction is informational only
    (IC is non-directional; direction just biases the strike center).
    """
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
    norm = max(0, int((raw / 110) * 100))
    grade = "STRONG" if norm >= 90 else "MODERATE" if norm >= 75 else "WEAK" if norm >= 60 else "NONE"
    return norm, grade, direction


_IC_AVAILABLE = True


def evaluate_ic_signal(now_et,
                       spy_open, spy_close, spy_high, spy_low,
                       prev_close, vwap, vol_ratio,
                       vix, qqq_pct, iwm_pct, adx, rsi,
                       macro_gate_status=None):
    """
    Returns dict shaped for /api/data exposure:
      {
        "available":     bool,
        "should_fire":   bool,
        "score":         int,
        "grade":         "STRONG" | "MODERATE" | "WEAK" | "NONE",
        "direction":     "CALL" | "PUT"  (informational — IC is non-directional)
        "block_reason":  str | None       — macro window / vix cap
        "structure":     {short_call_offset, wing_width, target_credit}
        "detail":        str
      }

    Safety filters applied (consistent with MES Futures algo):
      • Macro window (FOMC/CPI/NFP)  → block_reason="MACRO_xxx"
      • VIX > 25                      → block_reason="VIX_HIGH"
    """
    if not _IC_AVAILABLE:
        return {
            "available": False, "should_fire": False,
            "score": 0, "grade": "NONE", "direction": None,
            "block_reason": "IC_MODULE_UNAVAILABLE",
            "structure": None,
            "detail": "backtest_options_1min not importable from api/",
        }

    # Build the row dict shape that score_day expects (daily-resolution).
    row = {
        "Open":      spy_open,
        "Close":     spy_close,
        "High":      spy_high,
        "Low":       spy_low,
        "PrevClose": prev_close or spy_open,
        "PctChange": ((spy_close / prev_close - 1) * 100) if prev_close else 0.0,
        "VWAP":      vwap or spy_close,
        "VolRatio":  vol_ratio or 0.0,
    }
    try:
        score, grade, direction = _ic_score_day(row, vix or 18.0, qqq_pct or 0.0,
                                                iwm_pct or 0.0, adx, rsi)
    except Exception as e:
        return {
            "available": True, "should_fire": False,
            "score": 0, "grade": "NONE", "direction": None,
            "block_reason": "IC_SCORE_ERROR",
            "structure": None,
            "detail": f"score_day raised: {e}",
        }

    # Safety filters — shared with MES algo.
    block = None
    if macro_gate_status == "BLOCKED":
        block = "MACRO_BLOCKED"
    elif vix is not None and vix > 25.0:
        block = "VIX_HIGH"

    should_fire = (
        block is None
        and grade == "STRONG"
        and score >= 90
    )

    structure = {
        "short_call_offset": 3,
        "short_put_offset":  3,
        "wing_width":        5,
        "target_credit":     "1.5-2.5 per IC",
        "tp_close_at_pct":   25,    # close when net cost ≤ 25% of credit
        "sl_close_at_pct":   200,   # close when net cost ≥ 200% of credit
        "max_loss_per_ic":   "≈ wing_width - credit (~$3-$3.50)",
    }

    if block:
        detail = f"🚫 IC blocked: {block}"
    elif should_fire:
        detail = f"🟢 IC ENTRY — score {score} {grade}"
    else:
        detail = f"IC standby — score {score} {grade} (need ≥90 STRONG)"

    return {
        "available":    True,
        "should_fire":  should_fire,
        "score":        int(score),
        "grade":        grade,
        "direction":    direction,
        "block_reason": block,
        "structure":    structure,
        "detail":       detail,
    }
