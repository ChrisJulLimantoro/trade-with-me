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
    plan_refresh_bars: int = 16  # replay: refresh the plan every N feature bars (~4h on 15m)
    soft_threshold: float = 0.6  # min weighted soft-rule score to "detect" a setup
    min_rr: float = 1.5  # risk: min reward:risk (lowered from 2.0 — ATR-wide stops reduce TP distance)
    paper_equity_usd: float = 10_000.0
    # Risk-based sizing: size each trade so a stop-out loses at most this fraction of equity.
    risk_per_trade_pct: float = 0.01
    # Cap on notional / equity. Leverage emerges from risk-based sizing up to this ceiling.
    max_leverage: float = 3.0
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
    # Round-trip trading costs charged at each leg-banking site (entry + exit), in basis
    # points. fee_bps = taker fee per side; slippage_bps = assumed adverse fill per side.
    fee_bps: float = 5.0
    slippage_bps: float = 2.0
    # Only take setups aligned with the regime: skip entries in sideways regimes and
    # require trend-aligned direction in trending regimes.
    regime_filter: bool = True

    # --- Re-plan discipline ---
    # Don't refresh the strategist plan while a position is open (no thesis churn mid-trade,
    # no wasted create_plan calls); supersede the active plan on close so the next bar builds
    # a fresh one against current context.
    replan_on_close: bool = True

    # --- Dynamic exit observation (finer-timeframe trade management) ---
    # Open trades are managed on this finer timeframe between decision bars.
    observe_timeframe: str = "5m"
    observe_enabled: bool = True
    # Cadence (in finer-tf bars) at which the LLM observation agent is consulted while holding.
    observe_every_bars: int = 1  # every 5m bar when observe_timeframe=5m (tightened from 3)
    # EXIT_NOW is only honoured when the observation confidence clears this floor.
    observe_exit_min_conf: float = 0.7
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
