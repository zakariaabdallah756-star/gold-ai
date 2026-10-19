# Trading OS & Journal

A local, single-user web app that runs your trading day end to end: a trading
plan you configure once, a pre-market routine that gates trade entries,
automated position sizing with hard risk rules, a complete trade journal, and a
statistical dashboard that tells you whether you actually have an edge.

Everything runs on your machine. Data lives in one SQLite file plus a
screenshots folder, and there are no accounts, cloud services or telemetry.

## Quick start

| Platform | Command |
|---|---|
| Windows | double-click **`run_trading_os.bat`** |
| macOS / Linux | `./run_trading_os.sh` |
| Any (manual) | `pip install -r trading_os/requirements.txt` then `python run_trading_os.py` |

The scripts create a `.venv` on first run, install the dependencies and open
<http://localhost:8765> in your browser. Python 3.10+ is required.

Want to look around first? `python run_trading_os.py --demo` starts a separate
demo journal pre-filled with ~100 realistic trades. Your real journal is not
touched.

| Option | Default | Purpose |
|---|---|---|
| `--port` | `8765` | HTTP port |
| `--host` | `127.0.0.1` | Bind address (keep it local unless you know why) |
| `--data-dir` | `./trading_os_data` | Database, screenshots and backups folder (env: `TRADING_OS_DATA_DIR`) |
| `--demo` | off | Use `./trading_os_demo_data` and seed sample trades |
| `--no-browser` | off | Don't open the browser |

## What's inside

**1. Trading plan & pre-market routine** (`Trading Plan`, `Pre-Market`)
- Markets with full contract specs (contract size, pip/point size, quote currency, lot limits).
- Session windows (Asia, London, New York, ...) in UTC; mark the ones that form your trading window.
- Strategies with written rules and a *minimum confluences* requirement.
- A confluence library and a configurable, categorised pre-market checklist.
- A daily checklist with bias, key levels, news and notes. It auto-saves, keeps a 35-day history and tracks your streak. Until every required item is ticked, new trade entries stay locked.

**2. Risk management** (`Risk Calculator`)
- Inputs: equity (defaults to live equity), risk %, stop as prices or pips/points, optional target, FX rate for cross-currency instruments.
- Outputs: lot size (always rounded **down** to the lot step), units, max loss, actual risk %, pip value, R:R, potential profit, notional and leverage.
- Live guardrail feedback, then a one-click "Log this trade" that pre-fills the journal.

**3. Journal** (`Journal`)
- Date/time, asset, direction, entry, initial stop, target, exit, size, risk, fees and net P&L (auto-calculated from prices or entered manually).
- Planned R:R vs executed R:R and R-multiple, strategy, timeframe, session (auto-detected), confluences, emotional state, rule compliance and tags.
- Notes and chart screenshots: upload, drag & drop, **paste from clipboard**, or attach a TradingView link.
- Search, filters, sorting, pagination and full CRUD. Open trades can be closed from their detail view.

**4. Dashboard** (`Dashboard`)
- Net P&L, total trades, win rate, profit factor, expectancy ($ and R), max drawdown, payoff ratio, streaks, holding time and fees.
- *Discipline* panel: the stats for trades that followed the plan vs trades that broke it, i.e. what indiscipline costs you.
- Equity curve, daily P&L and R-multiple distribution charts, each with a table view.
- Edge breakdown by asset, strategy, session, emotion, direction, confluence, weekday and compliance.
- Filters by date range, asset, strategy, session and direction.

**5. Backup & export** (`Backup & Export`)
- Trades as CSV (Excel-friendly, formula-injection safe), full JSON backup, and a ZIP with JSON plus screenshots.
- Restore from JSON or ZIP. A safety snapshot of the current data is written to `backups/` first.

## How the guardrails work

| Rule | Triggers when | Severity |
|---|---|---|
| Pre-market checklist | required items not all ticked today | always **blocks** (if enabled) |
| Minimum R:R | planned R:R < your minimum (default 1:2) | `trade_rule_mode`: block / warn |
| Max risk per trade | risk > max % of equity (default 2%) | `trade_rule_mode` |
| Strategy confluences | fewer confluences than the strategy requires | `trade_rule_mode` |
| No take profit | no target, so R:R can't be verified | warn |
| Outside trading window | entry outside your active sessions | warn (optional) |
| Max daily loss | today's realised loss ≥ limit (default 3% of start-of-day equity) | `daily_limit_mode`: lock / warn |
| Max trades per day | trade count reached (0 = off) | `daily_limit_mode` |
| Daily risk budget | this trade's risk + open risk could breach the daily limit | `daily_limit_mode` |

**Blocking only applies to live entries.** A live entry is a new *open* position
dated today. A journal must stay truthful, so trades you log after the fact (closed
trades, or past dates) are always saved, with any violations attached and the
trade marked as "broke rules". Closing or editing an existing trade is never
blocked, so you can always close a position while trading is locked.

The status bar on every page shows the checklist state, today's P&L, the daily
loss meter, the trade count and whether entries are enabled. When entries are
locked, a banner explains why.

## Calculations

- **Pip value per lot** = contract size × pip size × FX. FX is 1 when the quote currency is the account currency, 1/price when the base currency is (e.g. USDJPY on a USD account), and otherwise the rate you enter.
- **Lots** = floor((equity × risk %) / (stop pips × pip value), lot step), capped at the max lot. Zero, with an explanation, if even the minimum lot would exceed your risk.
- **Planned R:R** = |target − entry| / |entry − stop|. **Executed R:R** = signed (exit − entry) / |entry − stop|.
- **R-multiple** = net P&L / risk amount (falls back to executed R:R when no risk amount is known).
- **Win rate** = wins / all closed trades (breakevens count as non-wins). **Profit factor** = gross profit / |gross loss|.
- **Expectancy** = net P&L / trades. **Max drawdown** = largest peak-to-trough fall of the closed-trade equity curve.
- **Equity** = starting balance + net P&L of closed trades. The daily loss limit uses equity at the start of the day.

Trade times are wall-clock times in the account time zone (`Trading Plan → Account`),
and sessions are defined in UTC. Entries are converted with DST awareness before
the session is detected.

## Project layout

```
run_trading_os.py / .sh / .bat   launchers
trading_os/
  app.py          FastAPI app factory (routes, static files, error handling)
  cli.py          command-line launcher
  config.py       data-dir resolution and limits
  models.py       SQLAlchemy models        schemas.py   Pydantic validation
  seed.py         default plan (markets, sessions, strategies, checklist)
  api/            REST routers: plan, checklist, risk, trades, analytics, backup
  services/       domain logic
    sizing.py       position sizing & R:R math        (pure)
    guardrails.py   risk rules & day state            (pure)
    sessions.py     time-zone-aware session detection (pure)
    analytics.py    metrics, curves, breakdowns       (pure)
    trades.py       trade lifecycle, derived fields, queries
    account.py      equity, daily status              checklist.py  daily routine
    backup.py       CSV / JSON / ZIP export & restore uploads.py    screenshot storage
    demo.py         demo data generator
  static/         dark-themed SPA (vanilla ES modules, no build step; Chart.js vendored)
tests/trading_os_tests/   unit + API tests
```

Interactive API documentation is served at <http://localhost:8765/api/docs>.

## Development

```bash
pip install -r trading_os/requirements.txt
python -m pytest tests/trading_os_tests -q
black -l 120 trading_os tests/trading_os_tests && isort --profile black -l 120 trading_os tests/trading_os_tests
```

Notes:
- Tables are created automatically on startup. If you change a model later, add the column with a migration or export → recreate → restore.
- The server binds to `127.0.0.1` by default and has no authentication. Don't expose it to a network.
- Chart.js 4.4.7 is vendored in `static/vendor/` (MIT, licence included), so the app works fully offline.
