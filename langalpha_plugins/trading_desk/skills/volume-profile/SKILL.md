---
name: volume-profile
description: "Volume profile from price bars: point of control (POC), value area high/low (VAH/VAL), high and low volume nodes, per-session profiles and an estimated buying/selling delta. Use for 'where is the volume in X', 'value area for NVDA this month', 'POC and VAH/VAL', 'is price above value', 'volume nodes as support/resistance'."
---

# Volume profile

Computes volume at price from OHLCV bars and writes it as JSON, with a summary on stdout. To show it, draw it on a chart with `--profile` in `.agents/skills/price-chart/SKILL.md`.

## Run it

Save bars to the task directory first, the same way `.agents/skills/price-chart/SKILL.md` step 1 does. Then:

```bash
python .agents/skills/volume-profile/scripts/volume_profile.py nvda_vp/bars.json --per-session
```

- `--bins` (default 60) sets the price resolution: more bins for a narrow range, fewer for a wide one.
- `--value-area` (default 0.70) is the share of volume the value area holds.
- `--per-session` adds one POC/VAH/VAL per trading day (intraday bars only), which is how day traders read prior-day value.

The summary gives POC, VAH, VAL, where the last close sits against the value area, the main high and low volume nodes, and the net delta estimate.

## What the numbers are

- **POC**: the price band with the most volume. **Value area**: the band around the POC holding 70% of the volume, grown outward from the POC. **HVN/LVN**: local peaks and gaps in the profile; price tends to stall at HVNs and move quickly through LVNs.
- Bars do not carry individual trades, so the profile spreads each bar's volume evenly over its high-low range, and the delta splits each bar's volume into buying and selling by where it closed in its range. Say so when you report them: they are estimates, close to a real profile on fine bars (1-5 minute) and coarse on daily bars. A true footprint or order-flow delta needs trade-by-trade data, which this does not have.
- The profile covers exactly the bars in the file. Choose the window to match the question (this session, this week, since a breakout) and state it.
