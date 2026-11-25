# Breaker stop study (R&D #130) and the breaker line in the summary email

## What changes on PROD

- `circuit_breaker.enforce_circuit_breaker` writes `Data/breaker_last_run.json` each run, its own file and not the game state: the date, on or off, the reason, and the stocks it tightened.
- The daily summary email now shows "Profile (from the morning SPY Long60 score)" instead of "Active Strategy". When the breaker was on in today's run, it adds a red line: "Circuit breaker ON this run: <reason>. New buys and scale-ins frozen; stops tightened to 1x ATR on ...".
- The breaker's log line now reads "Tightened stop-losses to 1.0x ATR on: X, Y (scarcity holdings are left wide)". Before, the label made the list look like the names left wide.
- Trading behaviour is unchanged. The study kept the tightening rule.

## Steps

Pull main. No task registration is needed.

## Evidence to expect in the next drop

- `Data/breaker_last_run.json` exists with the run's date. Nothing is added to `ai_portfolio_game.json`.
- On a breaker day, the summary email shows the red breaker line. If the record is from an earlier day, no line is shown: the line can be missing, but never stale.
