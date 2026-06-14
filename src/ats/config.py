from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://ats:ats@localhost:5432/ats"
    x_bearer_token: str | None = None
    log_level: str = "INFO"
    log_render: Literal["rich", "json"] = "rich"
    seed: int = 42

    # --- LLM-plan trading layer (POC, see LLM_BASED_ARCHITECTURE.md) ---
    # OpenAI client. Default to mock so the full pipeline + tests run with no key.
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # e.g. https://openrouter.ai/api/v1
    llm_mock: bool = True
    llm_plan_model: str = "gpt-4o"  # create_plan — heavier reasoning
    llm_confirm_model: str = "gpt-4o-mini"  # confirm_setup — cheap/fast
    llm_max_tokens: int = 2048

    # --- Engine / planning / risk knobs ---
    strategy_profile: str = "baseline"
    max_setups_per_plan: int = 2
    # Setups are resting limit orders at a narrow entry_zone; a limit fills when price
    # TOUCHES the zone, not only when a bar CLOSES inside it. "close" mode requires a 15m
    # bar to close inside a ~0.1%-wide band, which almost never happens and starves the
    # detector of fills (measured ~40% of setups ever close-in-zone vs ~68% that touch it).
    # "wick_limit" fills at the zone edge on a touch — realistic and far higher hit rate.
    entry_trigger_mode: Literal["close", "wick_limit"] = "wick_limit"
    # Deterministic entry-confirmation gate (#1, default OFF). When on, a detected setup only
    # fills if, on the decision bar's close, price is actually turning in the trade's direction
    # (close moved with-trade) AND at least one order-flow signal (cvd_slope_10 or macd_hist)
    # agrees. Kills bounce-fills where wick_limit touches a level price immediately rejects.
    entry_confirmation_enabled: bool = False
    plan_refresh_bars: int = 16  # replay: refresh the plan every N feature bars (~4h on 15m)
    soft_threshold: float = 0.6  # min weighted soft-rule score to "detect" a setup
    # risk: min reward:risk (lowered from 2.0 — ATR-wide stops reduce TP distance)
    min_rr: float = 1.5
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
    # Minimum stop distance as a multiple of ATR. Stops tighter than this multiple are noise-stops
    # that get swept by normal bar wiggle; the risk layer rejects them before they waste a confirm
    # call or execute into a structural loss. 0 = disabled.
    min_stop_atr_mult: float = 1.5

    # --- Exit management (scale-out + breakeven + trailing) ---
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
    # Trailing-stop distance as a multiple of atr_14, applied to the runner once the
    # stop is at breakeven. 0 disables trailing.
    trail_atr_mult: float = 1.5
    # Early protection before TP1. "trail" arms a real ATR trail instead of jumping the
    # stop to breakeven; "breakeven" preserves the old early-arm behavior; "off" disables.
    early_stop_mode: Literal["off", "breakeven", "trail"] = "trail"
    early_trail_arm_atr: float = 1.0
    early_trail_atr_mult: float = 2.0
    # Sideways regimes use range-trading exits by default: bank more at TP1, then protect
    # the remainder. Trend regimes keep the global exit knobs above.
    sideways_exit_mode: Literal["trend", "range"] = "range"
    sideways_scale_out_frac: float = 0.75
    sideways_breakeven_requires_tp1: bool = True
    sideways_trail_atr_mult: float = 2.0
    sideways_trail_after_tp1_only: bool = True
    sideways_early_stop_mode: Literal["off", "breakeven", "trail"] = "trail"
    sideways_early_trail_arm_atr: float = 1.0
    sideways_early_trail_atr_mult: float = 2.0
    # Round-trip trading costs charged at each leg-banking site (entry + exit), in basis
    # points. fee_bps = taker fee per side; slippage_bps = assumed adverse fill per side.
    fee_bps: float = 5.0
    slippage_bps: float = 2.0
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

    # --- Re-plan discipline ---
    # Don't refresh the strategist plan while a position is open (no thesis churn mid-trade,
    # no wasted create_plan calls); supersede the active plan on close so the next bar builds
    # a fresh one against current context.
    replan_on_close: bool = True
    # Re-plan when the regime cell flips (#6, default OFF), not only on the plan_refresh_bars
    # timer. Trade-close (replan_on_close) and hard-invalidation replans already exist; this
    # adds the missing "the world changed" trigger while a plan rests with no open position.
    # The timer / expires_at stay as a backstop.
    replan_on_regime_change: bool = False

    # --- Dynamic exit observation (finer-timeframe trade management) ---
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
    llm_observe_model: str = "gpt-4o-mini"  # tactical exit manager — cheap/fast

    # --- Multi-timeframe context (higher-timeframe bias for create_plan) ---
    # Slower charts whose most-recent CLOSED bar is shown to the strategist for bias and
    # direction only. Executable rules still reference base-timeframe features. Empty = off.
    context_timeframes: list[str] = ["1h", "4h"]

    # --- Episodic memory (post-mortem learnings + retrieval into create_plan) ---
    memory_enabled: bool = True
    memory_top_k: int = 3  # prior learnings injected into the plan envelope
    llm_reflect_model: str = "gpt-4o-mini"  # post-mortem — cheap, one call per close


settings = Settings()
