# Setup Reference

Use these to choose `market_bias` and place `entry_zone`/`stop_loss` by reading
`recent_ohlcv` (and `higher_timeframes`). Executable `hard_rules`/`soft_rules`/
`invalidation_rules` MUST reference only features present in the envelope (e.g.
`rsi_14`, `ema_20/50/200`, `macd_hist`, `atr_14`, `cvd_30`, `cvd_slope_10`,
`funding_rate`, `funding_divergence`, `basis_premium`, `oi_delta_pct_1h`,
`vol_zscore_20`, `momentum_composite`, `pr_*`) or `price` — NEVER a setup name.
Each entry: name [strength] — definition — how to express.

## ICT / price-structure (locate from recent_ohlcv; confirm with features)
- FVG [trend continuation] — 3-bar imbalance: a gap between bar1.high and bar3.low (bull) left by an impulsive move. Bias with the impulse; set entry_zone inside the unfilled gap; confirm long `price > ema_50`, soft `rsi_14` rising / `macd_hist > 0`.
- IFVG [reversal] — an FVG that price closes through and fails to hold; it flips to act as the opposite (a filled bull gap becomes resistance). Trade the flip direction; entry_zone at the inverted gap; confirm with momentum turning (`macd_hist` sign flip, `rsi_14` crossing 50).
- Order block [trend continuation] — the last opposing candle before an impulsive break. Entry_zone at the OB body on retest; stop beyond the OB extreme; confirm trend with `ema_50`/`ema_200` alignment.
- Liquidity sweep / stop-run [exhaustion fade] — a wick beyond a prior swing high/low that quickly reclaims. Fade it: entry_zone at the reclaimed level, stop beyond the wick; confirm reversal `rsi_14` extreme unwinding, `cvd_slope_10` opposing the wick.
- Market-structure shift / BOS [trend change] — price breaks the most recent swing against the prior trend. Use for `market_bias` flip; enter on the first pullback (entry_zone at the broken level); confirm `ema_20` vs `ema_50` cross, `macd_hist`.

## Classic technical (map directly to features)
- S/R retest [trend continuation] — re-test of a broken support/resistance. Entry_zone at the level; confirm `price` vs `ema_50`, soft `pr_rsi` not extreme.
- EMA pullback [trend continuation] — in an uptrend, buy the dip to `ema_20`/`ema_50`. Entry_zone around the EMA; hard `price > ema_200` (long); soft `rsi_14` recovering from ~40.
- Range reversion [mean-reversion] — price at range edge with no trend. Entry_zone at the edge; hard `rsi_14 < 30` (long) / `> 70` (short) or `pr_rsi` extreme; stop beyond the edge.
- Breakout-retest [breakout] — enter the retest after a range break, not the break itself. Entry_zone at the broken boundary; confirm `vol_zscore_20 > 0`, `macd_hist` with direction.
- RSI/MACD divergence [reversal] — price makes a new extreme but `rsi_14`/`macd_hist` does not. Counter-trend; entry_zone at the swing; confirm divergence resolving (`macd_hist` crossing 0).

## Derivatives / flow (perp-specific features)
- Funding extreme [exhaustion fade] — crowded positioning. Fade with `pr_funding_imbalance` extreme or `funding_rate` stretched; entry against the crowded side; confirm price structure reclaim.
- Funding divergence [relative value] — one venue's funding diverges from peers (`funding_divergence`, `funding_divergence_z_30d` extreme). Lean toward the cheaper-to-hold side; confirm with trend features.
- Basis/premium stretch [mean-reversion] — `basis_premium` / `basis_z_30d` over-extended signals over-eager perp longs/shorts; fade toward fair value; confirm `rsi_14` extreme.
- OI delta [trend continuation / squeeze] — rising `oi_delta_pct_1h` with price up = real buying (continuation); rising OI with price stalling = squeeze risk (fade). Use to weight conviction, not as sole trigger.
- CVD divergence [reversal] — price up but `cvd_30`/`cvd_slope_10` down (or `pr_cvd_divergence` extreme) = weak move. Counter-trend; entry_zone at the swing; confirm momentum rollover.
