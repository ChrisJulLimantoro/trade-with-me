from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RiskConfig(BaseModel):
    """Risk sizing + the round-trip cost model. A strategy profile overrides this as a unit."""

    paper_equity_usd: float = 10_000.0
    # Risk-based sizing: size each trade so a stop-out loses at most this fraction of equity.
    risk_per_trade_pct: float = 0.05
    # Cap on notional / equity. Leverage emerges from risk-based sizing up to this ceiling.
    max_leverage: float = 3.0
    # Isolated margin/risk heat caps. ``margin`` is the capital committed before leverage;
    # notional exposure is margin * leverage. These caps prevent the risk sizer from using
    # the whole account as margin for every tight-stop setup.
    max_margin_pct_per_trade: float = 0.20
    max_total_margin_pct: float = 0.60
    max_portfolio_risk_pct: float = 0.03
    # min reward:risk (lowered from 2.0 — ATR-wide stops reduce TP distance)
    min_rr: float = 1.5
    # Take-profit distance as an ATR multiple (structural target in sl_tp.compute_levels).
    # 3.0 ATR is rarely tagged on the 15m scalper (TP1 hit on ~3% of trades in BTC replay),
    # so scale-out wins never bank. A profile may pull this in to a more reachable target;
    # the synthesizer's RR floor still rejects geometry that doesn't clear ``min_rr``.
    reward_atr_mult: float = 3.0
    # Minimum stop distance as a multiple of ATR. Stops tighter than this multiple are
    # noise-stops that get swept by normal bar wiggle; the risk layer rejects them before they
    # waste a confirm call or execute into a structural loss. 0 = disabled.
    min_stop_atr_mult: float = 1.5
    # When enabled, the stop multiple scales
    # with the trailing ATR percentile rank (``pr_atr`` ∈ [0,1], causal): locally EXPANDED
    # volatility (high pr_atr — trend legs) uses the TIGHT end (``min_stop_atr_mult``) for a
    # better payoff, while locally COMPRESSED volatility (low pr_atr — the choppy regimes whose
    # tight-stop whipsaw losses are the dominant bleed, esp. on BTC) widens toward
    # ``stop_atr_wide`` so the noise can't sweep the stop. stop_mult = min_stop_atr_mult +
    # (stop_atr_wide − min_stop_atr_mult) * (1 − pr_atr). min_stop_atr_mult stays the risk-layer
    # noise floor, so the adaptive stop (always ≥ it) is never rejected. 0/False = legacy fixed.
    adaptive_stop_enabled: bool = False
    stop_atr_wide: float = 1.1
    # Absolute-volatility band (atr_pct = ATR/price) the adaptive stop interpolates across:
    # atr_pct >= stop_vol_hi → tight (min_stop_atr_mult); atr_pct <= stop_vol_lo → wide.
    stop_vol_lo: float = 0.0020
    stop_vol_hi: float = 0.0050
    # Win rate is flat across most features,
    # but PnL/trade is structurally higher in locally EXPANDED volatility (high pr_atr — trend
    # legs run further at the SAME win rate) and negative in compressed chop (low pr_atr, 59% win
    # vs 74-83%). So scale the per-trade risk budget by the causal ATR percentile rank:
    # mult = vol_size_min + (vol_size_max − vol_size_min) * pr_atr. This concentrates capital on
    # the higher-edge trades and starves the chop bleed. Applied AFTER admission (does not change
    # which setups trade, so no volume side-effect). 0/False = flat sizing.
    vol_sizing_enabled: bool = False
    vol_size_min: float = 0.6
    vol_size_max: float = 1.6
    # Depth (as an ATR fraction) the default entry zone sits on the *favorable/pullback* side of
    # the current close — a long limit-buys this far below close, a short limit-sells this far
    # above. A deeper pullback fills at a better price closer to local exhaustion (fewer immediate-
    # adverse stop-outs, bigger room to target) at the cost of fewer fills. 0.25 = the historical
    # default baked into sl_tp._PULLBACK_ATR_FRAC.
    entry_pullback_atr_frac: float = 0.25
    # Round-trip trading costs charged at each leg-banking site (entry + exit), in basis
    # points. fee_bps = taker fee per side; slippage_bps = assumed adverse fill per side.
    fee_bps: float = 5.0
    slippage_bps: float = 2.0


class ExitConfig(BaseModel):
    """Exit machine: time-stop, scale-out, breakeven, trailing — trend + sideways postures."""

    # Max bars an OPEN trade may be held before it is time-stopped, measured from the
    # entry bar (NOT the plan/setup entry-window expiry). Breakeven-protected runners are
    # exempt and keep trailing. Decoupling this from the setup window stops a healthy,
    # still-running trade from being cut just because the plan's clock ran out.
    max_hold_bars: int = 48  # ~12h on 15m
    # When an unprotected, non-profitable trade reaches max_hold_bars, do NOT blind-close it:
    # consult the exit manager (thesis review). HOLD → extend one more hold window; a clear
    # reversal → EXIT_NOW. Demotes the time-stop from a guillotine to a review trigger.
    expiry_review_enabled: bool = True
    # Hard backstop on the review above: after this many HOLD-driven extensions, force the
    # time-stop close regardless, so a stuck trade can't tie up the one-per-symbol slot forever.
    max_hold_extensions: int = 2
    # Fraction of the *remaining* position closed at each non-final take-profit. The
    # remainder rides to the next target with the stop moved to breakeven.
    scale_out_frac: float = 0.5
    # Move the stop to breakeven once the trade's favorable excursion reaches this multiple
    # of atr_14, WITHOUT waiting for the first scale-out. Arming breakeven early protects a
    # green runner from the time-stop and starts the trail sooner. 0 disables early arming.
    breakeven_arm_atr: float = 1.0
    # Profit floor on the early arm: only arm breakeven once the favorable excursion also
    # clears this many round-trip costs of profit. Stops the arm from scratching trades at
    # entry for a guaranteed fee-only loss when price wiggles straight back. 0 disables.
    breakeven_arm_cost_mult: float = 2.0
    # Profit lock on the early breakeven arm: instead of parking the stop at the cost-only
    # breakeven (which scratches the trade to ~0% — a non-win that tanks win rate), lock the
    # stop this many ATR of *profit* beyond the cost breakeven. A trade that arms and then
    # retraces then exits at a small REAL gain (a win) rather than flat. 0 = legacy behavior
    # (park at cost-breakeven). The trail still ratchets above this floor as the runner extends.
    breakeven_lock_atr: float = 0.0
    # Trailing-stop distance as a multiple of atr_14, applied to the runner once the
    # stop is at breakeven. 0 disables trailing.
    trail_atr_mult: float = 1.5
    # What the trail distance is measured FROM. "chandelier" anchors off the high-water extreme
    # since entry (highest high for longs / lowest low for shorts), so a winner keeps the room
    # it earned through a pullback; "close" trails the current bar close (legacy behavior, kept
    # for replay/ablation A/B). Either way the stop only ever tightens.
    trail_mode: Literal["close", "chandelier"] = "chandelier"
    # Within-symbol vol-conditional runner trail (LOOP6). The fixed trail trades BTC-chop against
    # ETH-trend: a loose trail rides trend legs but bleeds give-back in chop. Absolute vol can't
    # separate them (a high-vol symbol's chop ≈ a low-vol symbol's trend), so key off the WITHIN-
    # symbol ATR percentile ``pr_atr``: on bars with ``pr_atr >= trail_trend_pr_atr_min`` (each
    # symbol's own expanded/trend regime) loosen the trail to ``trail_atr_mult_trend`` so winners
    # ride further; quieter (chop) bars keep the tight base ``trail_atr_mult``. The trail still
    # only ratchets, so a per-bar widen never loosens an already-locked stop. 0.0 = disabled.
    trail_atr_mult_trend: float = 0.0
    trail_trend_pr_atr_min: float = 0.0
    # Early protection before TP1. "trail" arms a real ATR trail instead of jumping the
    # stop to breakeven; "breakeven" preserves the old early-arm behavior; "off" disables.
    early_stop_mode: Literal["off", "breakeven", "trail"] = "trail"
    early_trail_arm_atr: float = 1.0
    early_trail_atr_mult: float = 2.0
    # Conditional loser-cut (SPEC3-LOOP / Iter 3). The exit machine protects WINNERS with a
    # ratcheting stop (early arm → profit lock → trail) but gives losers nothing symmetric: an
    # unprotected red trade just waits for the full ``min_stop_atr_mult``-ATR stop, so avg_loss
    # ≈ 0.8R while avg_win ≈ 0.45R (inverted payoff). This gives losers the same ratchet: once a
    # still-red, never-armed trade has spent ``loss_cut_hold_frac`` of its hold window, tighten its
    # stop to ``loss_cut_atr_mult`` ATR from entry (only-tighten, takes effect next bar like the
    # trail). Unlike the reverted "tighten every stop from entry" experiments this touches ONLY the
    # stalled-red bucket, so it cuts the loser tail without re-introducing entry-noise stop-outs.
    # Both default 0.0 = DISABLED (baseline behavior byte-unchanged); the scalper profile opts in.
    loss_cut_hold_frac: float = 0.0
    loss_cut_atr_mult: float = 0.0
    # Sideways regimes use range-trading exits by default: bank more at TP1, then protect
    # the remainder. Trend regimes keep the global exit knobs above.
    sideways_exit_mode: Literal["trend", "range"] = "range"
    sideways_scale_out_frac: float = 0.75
    sideways_breakeven_requires_tp1: bool = True
    sideways_trail_atr_mult: float = 2.0
    sideways_trail_mode: Literal["close", "chandelier"] = "chandelier"
    sideways_trail_after_tp1_only: bool = True
    sideways_early_stop_mode: Literal["off", "breakeven", "trail"] = "trail"
    sideways_early_trail_arm_atr: float = 1.0
    sideways_early_trail_atr_mult: float = 2.0


class ObserverConfig(BaseModel):
    """Dynamic exit observation (finer-timeframe trade management by the LLM exit manager)."""

    # Open trades are managed on this finer timeframe between decision bars.
    observe_timeframe: str = "5m"
    observe_enabled: bool = True
    # Cadence (in finer-tf bars) at which the LLM observation agent is consulted while holding.
    observe_every_bars: int = 1  # every 5m bar when observe_timeframe=5m (tightened from 3)
    # Event-driven observer (#5, default OFF). When on, the per-bar LLM observer call is only
    # made when the deterministic thesis health is decaying/broken, the trade is a stale
    # candidate, or the periodic fallback cadence below elapses — deterministic exit management
    # (step_trade: trailing/scale/breakeven/time-stop) still runs every bar regardless.
    observe_only_on_health: bool = False
    observe_health_fallback_bars: int = 12  # consult periodically even when healthy (~1h on 5m)
    # EXIT_NOW is only honoured when the observation confidence clears this floor.
    observe_exit_min_conf: float = 0.7
    # Stagnation heuristic shown to the observation agent: once this fraction of the current
    # hold window is spent, a trade that is still flat and never made meaningful favorable
    # progress should be considered an opportunity-cost exit candidate.
    observe_stale_hold_progress: float = 0.75
    observe_stale_unrealized_abs_pct: float = 0.001
    observe_stale_mfe_pct: float = 0.003


class PlanConfig(BaseModel):
    """Planning / strategist / entry-gate knobs and re-plan discipline."""

    # Minimum synthesized confidence for the deterministic proposer to emit a setup, and the
    # floor the adjudicated (post-delta) confidence must still clear or the trade is vetoed.
    signal_min_confidence: float = 0.55
    # Volatility-conditional confidence floor (LOOP6). The strategy is a trend-pullback system
    # with no edge in low-volatility CHOP — the bleed is the quiet, compressed bars where
    # pullback entries whipsaw (~54–62% win vs 74–83% in trend). Rather than subtract a regime
    # (non-local under single-position), RAISE the confidence bar only when ABSOLUTE volatility
    # ``atr_pct`` (= ATR/price) sits at/below ``chop_atr_pct_max``: such bars must clear
    # ``chop_min_confidence`` instead of the base ``signal_min_confidence``. Absolute (not a
    # within-symbol percentile) is deliberate — a structurally low-vol symbol (BTC, p65≈0.0034)
    # is mostly below the threshold while a high-vol one (ETH, p20≈0.0031) is mostly above it,
    # so the gate tightens chop where it actually lives without touching the high-vol book. This
    # is the cross-symbol discrimination a single universal knob cannot give. 0.0 floor = disabled.
    chop_atr_pct_max: float = 0.0
    chop_min_confidence: float = 0.0
    max_setups_per_plan: int = 2
    # How a detected setup turns into a fill:
    #  - "close": market-on-close. The decision bar closes inside the entry_zone → fill at that
    #    close. Deciding on and filling at the same close is executable (no look-ahead).
    #  - "wick_limit": a real resting limit order. The thesis (hard+soft rules) is validated on a
    #    CLOSED bar, which ARMS a resting limit at the zone edge; the fill is then deferred to a
    #    LATER bar that trades through that limit (orchestrator Phase 1). The fill never consults
    #    the filling bar's own close. NOTE: an earlier implementation armed AND filled inside the
    #    same closed bar — that booked entries at intrabar wicks authorized by that bar's own
    #    close (look-ahead), which inflated backtest win rate and did not survive live. The
    #    arm→pending→fill split removes it; re-baseline any tuning done before this change.
    entry_trigger_mode: Literal["close", "wick_limit"] = "wick_limit"
    # Deterministic entry-confirmation gate (#1, default OFF). When on, a detected setup only
    # fills if, on the decision bar's close, price is actually turning in the trade's direction
    # (close moved with-trade) AND at least one order-flow signal (cvd_slope_10 or macd_hist)
    # agrees. Kills bounce-fills where wick_limit touches a level price immediately rejects.
    entry_confirmation_enabled: bool = False
    plan_refresh_bars: int = 16  # replay: refresh the plan every N feature bars (~4h on 15m)
    soft_threshold: float = 0.6  # min weighted soft-rule score to "detect" a setup
    # Only take setups aligned with the regime: skip entries in sideways regimes and
    # require trend-aligned direction in trending regimes.
    regime_filter: bool = True
    # Counter-trend relief: when a higher timeframe is deeply RSI-oversold (bear regime) or
    # overbought (bull regime), let the gate also permit the mean-reversion direction. Without
    # this, a long bear-low stretch forces shorts into every oversold bounce — the main
    # win-rate sink observed in BTC replays. The HTF exhaustion state is read from the planner
    # envelope at plan time and persisted onto the plan, so the runtime filter stays in sync.
    counter_trend_on_htf_exhaustion: bool = True
    # Deterministic preferred direction (#4, default OFF). When on, the planner computes a soft
    # directional steer from regime + HTF exhaustion + price structure and passes it as
    # risk_limits.preferred_direction; the prompt instructs the strategist to design in that
    # direction or stand aside (it may still take another allowed direction with a stated
    # contrarian reason). allowed_directions remains the hard gate.
    deterministic_direction_hint: bool = False
    # Don't refresh the strategist plan while a position is open (no thesis churn mid-trade,
    # no wasted create_plan calls); supersede the active plan on close so the next bar builds
    # a fresh one against current context.
    replan_on_close: bool = True
    # Re-plan when the regime cell flips (#6, default OFF), not only on the plan_refresh_bars
    # timer. Trade-close (replan_on_close) and hard-invalidation replans already exist; this
    # adds the missing "the world changed" trigger while a plan rests with no open position.
    # The timer / expires_at stay as a backstop.
    replan_on_regime_change: bool = False
    # Higher-timeframe trend filter (default OFF). When on, the plan-time allowed_directions
    # gate also drops the direction that fights the higher-timeframe (4h, then 1h) trend —
    # no longs when the 4h close is below its EMA50 with MACD negative, no shorts in the
    # mirror case. The existing HTF-exhaustion counter-trend relief is preserved (a deeply
    # oversold 4h still permits a mean-reversion long). Kills the dominant BTC-replay loss
    # bucket: 15m "bull-low"/chop longs taken inside a 4h downtrend.
    htf_trend_filter: bool = False
    # Drop LONG setups in sideways ("side-*") regimes (default OFF). A trend-pullback strategy
    # has no long edge in low-vol chop: the pullback entries are just noise and get whipsawed.
    # In the full-2026 BTC replay, side-low LONGs were the single worst bucket (43t / 40% win /
    # -70% margin) while side-low shorts were ~neutral — so this gate removes the bleed without
    # killing the (with-bearish-bias) short side or the trade-count floor.
    sideways_block_longs: bool = False
    # Drop SHORT setups in sideways ("side-*") regimes (default OFF). Companion to
    # sideways_block_longs. Under the look-ahead-corrected fills the side-low shorts that were
    # ~neutral became a clear sink (35t / 34% win / -32% margin): a trend-pullback short has no
    # edge in low-vol chop either — the limit fills on a bounce that simply chops back. With both
    # flags ON the engine takes NO trades in side-* regimes (skip chop entirely).
    sideways_block_shorts: bool = False
    # Slower charts whose most-recent CLOSED bar is shown to the strategist for bias and
    # direction only. Executable rules still reference base-timeframe features. Empty = off.
    context_timeframes: list[str] = ["1h", "4h"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_nested_delimiter="__"
    )

    database_url: str = "postgresql+asyncpg://ats:ats@localhost:5432/ats"
    x_bearer_token: str | None = None
    log_level: str = "INFO"
    log_render: Literal["rich", "json"] = "rich"
    seed: int = 42

    # --- LLM trading layer (bounded judge; see docs/redesign/02-llm-bias-veto.md) ---
    # OpenAI client. Default to mock so the full pipeline + tests run with no key.
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # e.g. https://openrouter.ai/api/v1
    llm_mock: bool = True
    # The plan is authored by the deterministic 8-agent engine; the LLM's only plan-time role
    # is the bounded ±0.20 veto/bias (adjudicate). When False, the run is the pure
    # deterministic baseline (no plan-time LLM call at all). With llm_mock=True the judge
    # returns delta 0, so leaving this on still reproduces the deterministic baseline.
    adjudication_enabled: bool = False
    llm_adjudicate_model: str = "deepseek-v4-flash"  # plan-time bounded veto/bias — heavier reasoning
    llm_max_tokens: int = 2048
    llm_observe_model: str = "deepseek-v4-flash"  # tactical exit manager — cheap/fast

    # --- Engine knob groups (each a coherent unit a strategy profile overrides) ---
    strategy_profile: str = "baseline"
    risk: RiskConfig = Field(default_factory=RiskConfig)
    exits: ExitConfig = Field(default_factory=ExitConfig)
    observer: ObserverConfig = Field(default_factory=ObserverConfig)
    plan: PlanConfig = Field(default_factory=PlanConfig)

    # --- Episodic memory (post-mortem learnings + retrieval into create_plan) ---
    memory_enabled: bool = False
    memory_top_k: int = 3  # prior learnings injected into the plan envelope
    llm_reflect_model: str = "deepseek-v4-flash"  # post-mortem — cheap, one call per close


settings = Settings()
