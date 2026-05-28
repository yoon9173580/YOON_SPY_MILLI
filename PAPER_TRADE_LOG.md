# SPY 0DTE Iron Condor — Paper Trade Log

Manual journal of paper trades. One row per trading day (signal or no-signal).
Automated daily check writes to `ic_signal_log.csv`; this file is the **human-curated** record with actual fills, notes, and learnings.

## Format

| Date | Score | Grade | Fire? | Strikes (sp/sc) | Wings (lp/lc) | Credit | Exit | PnL | Notes |
|---|---|---|---|---|---|---|---|---|---|

---

## 2026-05 (start)

| Date | Score | Grade | Fire? | Strikes (sp/sc) | Wings (lp/lc) | Credit | Exit | PnL | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 05/26 (Tue) | **95** | STRONG | ❌ **MISSED** | (would have been 747/753) | (742/758) | — | — | — | **System was offline since 5/23 21:52. Auto-paper-trading not wired. Signal confirmed retroactively via daily yfinance recompute.** |
| 05/27 (Wed) | 86 | MODERATE | ✓ skip | n/a | n/a | n/a | n/a | $0 | Correct: MODERATE < STRONG threshold |
| 05/28 (Thu) | _pending_ | _pending_ | _TBD_ | | | | | | Awaiting EOD or live score check |

---

## Status summary

- **Started forward test**: 2026-05-26 (technically — but missed due to system being off)
- **Paper account balance**: $1,000 (per `paper_portfolio.json`)
- **Trades executed**: 0
- **Signals seen**: 0 (no fires that were caught live)
- **Signals missed**: 1 (5/26 STRONG 95)
- **Days verified no-fire**: 1 (5/27 MODERATE 86)

## Lessons / Action items

- [ ] **5/26 miss**: Wire up automated daily check (done via `.github/workflows/ic_daily_check.yml`)
- [ ] **Webhook setup**: Configure `DISCORD_WEBHOOK` or `SLACK_WEBHOOK` repo secret to get instant alerts
- [ ] **Manual fallback**: Phone reminder at 10:25 ET on weekdays — check this log if no webhook
- [ ] After 4 weeks (≈ 6/22): compute PF/WR from this log → go-live decision per `PAPER_TRADING_IC.md`

## How to use this log

After each STRONG signal day, manually fill in:
1. Actual broker net credit (vs theoretical from `ic_signal_log.csv`)
2. Exit type (TP / SL / EOD / discretionary)
3. Realized PnL (per contract, then totaled)
4. Notes (fill quality, surprises, IV behavior, etc.)

After each MODERATE/WEAK/skip day, just confirm "no-fire correct" with a brief note.
