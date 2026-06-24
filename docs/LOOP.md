do a loop in these steps:
1. grab the latest replays for these symbols and timeframe:
    * BTC 2025-01-01 to 2025-06-01
    * BTC 2025-06-01 to 2026-01-01
    * BTC 2026-01-01 to 2026-06-01
    * ETH 2025-01-01 to 2025-06-01
    * ETH 2025-06-01 to 2026-01-01
    * ETH 2026-01-01 to 2026-06-01    
2. analyze what failed and find out how we can improve the win rate (you can be as wild as you can, attack every part of the engine is allowed).
3. Verification:
    * Run 2 symbols across 3 separate timeframe (you can run these concurrently but do a controlled sleep before each run start so a symbol wont have the same timestamp):
        * Symbols: BTCUSDT, ETHUSDT
        * Timeframe: 
            * 2025-01-01 to 2025-06-01
            * 2025-06-01 to 2026-01-01
            * 2026-01-01 and 2026-06-01
    * Command: uv run ats engine replay --symbol <symbol> --timeframe 15m --from <from> --to <to> --profile scalper --run-label <custom-run-label>.
4. state what you change in this iteration in the @ITERATIONS.md
5. go back to step 1 (use latest log)

Only end your iterations when such condition is fulfilled in each timeframe and symbol:
1. closed_trades >= 150 AND 
2. win rate >= 67% AND 
3. pnl_usd >= $200

IMPORTANT CONSTRAINT:
1. NEVER cheat the system, the backtest should be treated like a live system with honest entry and exit.
2. NEVER include any future data to prevent lookahead bias (which is cheating the system)
3. If you found any bug that is cheating the system, fix it first before applying your next changes

in the end, summarize all your findings (problems) and what you changed (solutions)

STOP IF you are not capable to improve the system even further after 5 failed consecutive iterations.