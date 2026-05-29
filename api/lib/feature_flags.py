"""
경량 A/B 피쳐 플래그.

목적:
  새 룰 도입 시 split-test 가능. 동일 dashboard 인스턴스가 여러
  variant를 동시 운영하면서 결과를 비교 — 한 룰만 켜고 끄는 식이 아닌
  side-by-side 검증.

사용:
  from lib.feature_flags import is_enabled, variant_for
  if is_enabled("require_options_flow_confirmation"):
      ...

값 소스 우선순위:
  1. 환경변수 FF_<NAME>=true|false
  2. 코드 디폴트 (DEFAULTS dict)

variant_for(name, default='A'):
  사용자/세션 그룹별 결정적 분배. 현재는 단일 사용자라 noop.
"""
import os
import hashlib

DEFAULTS = {
    # 신규 룰의 디폴트 ON/OFF
    "require_options_flow_confirmation": True,   # OF 반대 방향 시 NEUTRAL
    "regime_aware_rr":                   True,   # RR 비율 레짐 기반
    "macro_gate_blocking":               True,   # FOMC/CPI 차단
    "quarterly_roll_block":              True,   # 분기 롤 차단
    "vix_cap_25":                        True,   # VIX>25 진입 차단
    "score_band_90_100":                 True,   # 점수 90-100 sweet spot
    "short_requires_93":                 True,   # SHORT≥93
    "ml_magnitude_aware":                True,   # R-multiple 가중
    "ml_decay_to_one":                   True,   # 가중치 decay
    "trailing_stop_only":                True,   # BE 비활성, TRAIL만
    "tail_risk_monitor":                 True,   # VIX EWMA 모니터
    # ── PERMISSIVE PAPER MODE ─────────────────────────────────────
    # 활성화 시 진입 임계값을 완화 — 실거래 알고는 그대로 유지하되
    # 페이퍼 트레이딩이 실제로 실행되는지 검증할 수 있게 함.
    #   • 점수 75~110 (90~100 → 75~110)
    #   • MODERATE grade도 진입 허용
    #   • VIX 캡 30 (25 → 30)
    #   • PRIME/GAMMA/REENTRY 시간대 모두 허용 (score>=8)
    #   • SHORT 임계값 88 (93 → 88)
    # 포지션 size는 self-protect: STRONG의 50%
    # ENV로만 활성화: FF_PAPER_PERMISSIVE=1
    "paper_permissive":                  False,
}


def is_enabled(name: str, default: bool = None) -> bool:
    """Feature flag check. ENV var FF_<NAME> wins; falls back to DEFAULTS."""
    env_key = f"FF_{name.upper()}"
    env_val = os.getenv(env_key)
    if env_val is not None:
        return env_val.lower() in ("1", "true", "yes", "on")
    if default is None:
        default = DEFAULTS.get(name, False)
    return bool(default)


def all_flags() -> dict:
    """Snapshot of current flag state (for dashboard exposure / debug)."""
    return {name: is_enabled(name) for name in DEFAULTS}


def variant_for(experiment: str, default_variant: str = "A") -> str:
    """Deterministic variant assignment for an experiment.

    Currently 100% A for single-user setup. Extend by hashing
    (experiment + user_id) % 2 once multi-user.
    """
    env_key = f"VARIANT_{experiment.upper()}"
    return os.getenv(env_key, default_variant)
