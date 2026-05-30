# SPY 0DTE Iron Condor — Paper Trading Guide

## Why paper trading first

Backtest shows PF 6.86 / WR 90% / Max DD 13% on $1K capital. **Real PF likely 1.7-2.3** after stress testing for:
- Vol regime: backtest's STRONG-only filter cherry-picked VIX 14-20 days. Stress test at VIX 22-25 still shows PF 3+, but VIX >30 days are deadly (2 of 2 lost in expanded sample).
- Slippage: at realistic 3× modeled slippage (real 4-leg IC fills are 2-3× theoretical), PF drops to **2.39**.
- Operational risk: order mistakes, panic exits, FOMO entries.

Forward test is non-negotiable.

**Goal**: 4 weeks of paper trading. Decide to go live (or kill) based on observed PF.

---

## Daily Checklist (per trading day)

### Pre-market (8:30 AM ET)
- [ ] Check FOMC / NFP / CPI calendar — **skip entire day** if scheduled
- [ ] Check overnight gap — if SPY gap > 0.5%, mark "elevated risk"
- [ ] Note prior close, current pre-market level
- [ ] VIX level — if VIX > 30, **skip day** (regime not modeled)

### 10:30 AM ET — Score check
- [ ] Open Pine MILLI v3-0 IC (or backtest signal table)
- [ ] Read STRONG / score from chart panel
- [ ] If score < 85 or grade is WEAK/NONE/LOCKED → **no trade** (freq-tuned: STRONG *or* MODERATE with score ≥ 85 both qualify)
- [ ] If LOCKED → **no trade**

### 10:30 AM — Entry (if signal)
- [ ] Get SPY current price → round to ATM strike (e.g., $710)
- [ ] Construct IC:
  - SELL call at ATM + 3 (e.g., $713)
  - BUY call at ATM + 8 (e.g., $718)
  - SELL put at ATM - 3 (e.g., $707)
  - BUY put at ATM - 8 (e.g., $702)
- [ ] Check net credit on order ticket: should be ~$1.5-2.5
- [ ] Position size: 1 contract per $300 of paper capital
- [ ] Submit as combo order (all 4 legs, limit at mid)

### During day — Manage
- [ ] **TP target**: close IC when net cost ≤ 25% of received credit (e.g., credit $2 → close at $0.50)
- [ ] **SL target**: close IC when net cost ≥ 200% of received credit OR cost ≥ wing width ($5)
- [ ] Watch for: SPY approaching short strike → consider early close at smaller loss

### 15:30 PM ET — EOD exit
- [ ] If not yet TP'd or SL'd, close IC at market
- [ ] Do NOT hold to expiration (pin risk on 0DTE)

### Post-close — Log
- [ ] Record in journal (template below)

---

## Position Sizing ($500K paper trading account)

| Account | Risk/trade (5%) | Theoretical contracts ($300 max loss IC) | Practical cap |
|---|---|---|---|
| $5,000 | $250 | 0 (force 1) | 1 |
| $10,000 | $500 | 1 | 1 |
| $25,000 | $1,250 | 4 | 4 |
| $100,000 | $5,000 | 16 | 16 |
| **$500,000** (current) | **$25,000** | **83** | **20** |

**Liquidity cap: 20 contracts** (4-leg = 80 leg-contracts simultaneously). 0DTE SPY at ATM has ~1000-5000 daily volume per strike — 20 contracts is the comfortable ceiling. Larger sizes face material slippage scaling.

At $500K with 20-contract cap:
- Effective risk per trade: 20 × $300 = $6,000 = **1.2% of account**
- Per-trade win (TP at 75% credit kept): 20 × $150 = $3,000 = 0.6% account
- Expected daily PnL: ~$1.5K-$3K on signal days (vs $6K worst case loss)

---

## Pine Script setup

1. Open TradingView, load 5-min SPY chart
2. Pine Editor → paste `YOON_SPY_MILLI_signal_v2-8.pine` (or v3-0 when IC variant ready)
3. Save as "MILLI IC v3"
4. Add to chart
5. Create alert:
   - Condition: `MILLI 0DTE CALL` or `MILLI 0DTE PUT` (either signals STRONG day → IC entry)
   - Trigger: Once per bar close
   - Action: Push notification + email (do NOT auto-execute)

---

## Trade Journal Template (per trade)

```
Date: 2026-MM-DD
SPY entry (10:30): $___
ATM strike: $___
Strikes: K_sp=$___ / K_lc=$___ / K_sc=$___ / K_lp=$___
Credit received: $___
Theoretical max loss: $___
Number of contracts: ___

VIX: ___
Score: ___ / Grade: ___
SPY range so far (9:30-10:30): $___ low to $___ high

Exit type: TP / SL / EOD
Exit time: HH:MM
Exit cost: $___
Net PnL: $___

Notes:
  - Did SPY breach short strike intraday? Y/N which side
  - Any news / FOMC / earnings affecting move? 
  - Fill quality vs theoretical?
```

Use Google Sheets or simple CSV. Track these fields → calculate weekly PF.

---

## "Ship to live" decision criteria (after 4 weeks)

### Go-live conditions (ALL must be true)
- [ ] At least 12 paper trades executed (need sample)
- [ ] **Observed PF ≥ 1.8** (revised down from 2.5 after stress testing)
- [ ] **WR ≥ 70%** (revised from 75%)
- [ ] Max single-trade loss ≤ theoretical max ($300/contract)
- [ ] **No catastrophic days** (loss > 2× modeled max — would indicate gamma/IV blowout)
- [ ] Real fill quality observed: net credit within 15% of theoretical mid

### Hard kill conditions (ANY one = stop)
- 3 consecutive losses (early signal of regime change)
- Single day loss > 3× theoretical max (modeling completely wrong)
- 2 weeks with WR < 60%
- **Observed PF < 1.3 after 4 weeks** (revised from 1.5)
- Any VIX-30+ day where signal fired and we lost — confirms strategy fails in extreme vol

### Go-live capital
- Paper start: **$500,000** (per current `paper_portfolio.json`)
- Sizing: 5% risk per trade, **20-contract liquidity cap**
- Effective per-trade risk: 1.2% of account ($6K max loss / $500K)
- Scale up sizing cap (toward 50 contracts) only if first 2 weeks show fill quality holds at 20

---

## Common failure modes (learn from these)

1. **FOMO entries**: Trading on score < 85 or WEAK grade "because it looked good" — entry gate is score ≥ 85 AND grade STRONG/MODERATE. Don't chase below that.
2. **Early close on red**: SPY hits short strike intraday, you panic close at -50%, then SPY reverses by EOD. **Hold to TP/SL/EOD only**
3. **Size creep**: Wins → bigger size → one bad day wipes profits. **5% rule is non-negotiable**
4. **Skip the journal**: Without data, you can't tell signal-vs-noise. **Log every trade**
5. **Trading high-vol days**: VIX > 25 = IC expensive AND probability of breach high. **Skip**

---

## Comparison reference (don't conflate these)

| Strategy | Backtest PF | Realistic PF | Live status |
|---|---|---|---|
| SPY 0DTE Debit Spread | 1.14 | <1.0 | **DEAD** — structurally limited |
| **SPY 0DTE Iron Condor** | **6.86** | **1.7-2.3** | **PAPER TRADING** — this doc |
| MES Futures | 4.04 | ~2.5-3 (est) | LIVE (per memory) |

## Stress test findings (2026-05-26 session)

### VIX regime breakdown (relaxed filter — what happens at each VIX bucket)

| VIX entry | Trades | WR | PF | Behavior |
|---|---|---|---|---|
| ≥ 16 | 310 | 81% | 6.07 | normal |
| ≥ 20 | 85 | 73% | 3.01 | still profitable |
| ≥ 22 | 49 | 73% | 3.32 | still profitable |
| ≥ 25 | 21 | 62% | 2.01 | thin but positive |
| **≥ 30** | **2** | **0%** | **0.00** | **danger zone** |

The STRONG-score filter naturally avoids VIX > 22 (qTrend hard cutoff + volChop penalty). Don't override this in live.

### Slippage sensitivity (4-leg IC)

| Slip multiplier | PF | Net PnL ($1K) |
|---|---|---|
| 1× (theoretical) | 7.38 | +$6,304 |
| **3× (realistic 4-leg fill)** | **2.39** | **+$2,342** |
| 5× (gap-fill day) | 0.11 | -$6,702 |
| 10× (catastrophic) | 0.0 | -$11,730 |

**Implication**: order quality is critical. Use combo orders at limit, not 4 separate market orders. Re-evaluate if observed fills consistently >20% off theoretical mid.

### Per-trade risk realities
- Modeled max loss: $300/contract (wing $5 × 100 - credit $2)
- Realized max single-trade loss in cherry-picked sample: $139 (37% of theoretical)
- Realized max in stress-test sample (436 trades): $14,642 — but that was at large position size; per-contract still ~$300
- **0DTE gap risk is real**: if SPY moves $5+ in one bar past entry, SL slippage will be brutal

Iron Condor is **not** a replacement for MES futures system. They're different products, different strategies. Run both if both work.

---

## Files

- Strategy backtest: `backtest_iron_condor_1min.py`
- Most recent results: `backtest_iron_condor_1min.json`
- Score / signal engine: Pine `YOON_SPY_MILLI_signal_v2-8.pine` (TRADINGVIEW repo)
- This guide: `PAPER_TRADING_IC.md`
