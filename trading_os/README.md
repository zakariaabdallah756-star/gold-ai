# Trading OS & Journal

A local, single-user web app that runs your trading day end to end. You set up a
trading plan once. A pre-market routine and an economic calendar gate your trade
entries. Position sizing comes with hard risk rules and a signal parser, and open
positions can be managed with scale-out targets. The trade journal includes
mistakes and post-market reviews. A statistical dashboard tells you whether you
actually have an edge. On top of that there are a strategy incubator for
backtests, a prop-firm challenge tracker, and an optional AI chart assistant.

Everything runs on your machine. Data lives in one SQLite file plus a
screenshots folder, and there are no accounts or telemetry. The only network
calls are the optional ones you trigger yourself: Vision AI and the economic
calendar import.

> **Risk warning.** Trading forex, CFDs, futures and crypto, especially with
> leverage, carries a high risk of losing money. Statistics, backtests, AI
> analysis and parsed signals in this app are for education and record-keeping
> only. They are not financial advice. The app shows this warning on first launch
> and in the footer of every page.

## Quick start

| Platform | Command (from the folder that contains `trading_os/`) |
|---|---|
| Windows | double-click **`trading_os\run.bat`** |
| macOS / Linux | `trading_os/run.sh` |
| Any (manual) | `pip install -r trading_os/requirements.txt` then `python trading_os/run.py` |

The scripts create `trading_os/.venv` on first run, install the dependencies and
open <http://localhost:8765> in your browser. Python 3.10+ is required.

Want to look around first? `python trading_os/run.py --demo` starts a separate
demo journal. It is pre-filled with about 110 realistic trades, partial
take-profits, mistakes, daily reviews, upcoming news and two backtested
strategies. Your real journal is not touched.

| Option | Default | Purpose |
|---|---|---|
| `--port` | `8765` | HTTP port |
| `--host` | `127.0.0.1` | Bind address (keep it local unless you know why) |
| `--data-dir` | `trading_os/data` | Database, screenshots and backups folder (env: `TRADING_OS_DATA_DIR`) |
| `--demo` | off | Use `trading_os/demo_data` and seed sample data |
| `--no-browser` | off | Don't open the browser |

## What's inside

The sidebar groups the pages into **Trade**, **Improve** and **Setup**.

### Trade

**Dashboard.** Shows net P&L, win rate, profit factor, expectancy ($ and R), max drawdown, payoff ratio, streaks, holding time and fees.
- *Discipline* panel: the stats of trades that followed the plan vs trades that broke it.
- *Cost of mistakes*: what each tagged mistake costs you, and mistake-free vs mistake-tagged trades.
- Charts: equity curve, daily P&L and R-multiple distribution, each with a table view.
- Edge breakdown by asset, strategy, session, emotion, direction, confluence, mistake, weekday and compliance.
- Filters: date range, asset, strategy, session and direction.
- **Export PDF** creates a performance report for the current filters.

**Pre-Market.** The daily checklist covers bias, key levels, the news plan and notes. It auto-saves and tracks your streak; until every required item is ticked, new entries stay locked.
- The **economic calendar** card lists the coming week's releases (NFP, CPI, FOMC, ...) with their impact and guardrail window.
- Add releases from presets or import this week's high-impact events from the ForexFactory feed.

**Signals.** Paste a Telegram/WhatsApp signal and the parser extracts symbol, direction, entry (or entry zone), stop and TP1 to TP4. It copes with aliases like GOLD/XAU, broker suffixes, emojis, comma decimals and stops or targets given in pips.
- The resulting ticket is sized with your risk %, shows the blended R:R of the scale-out plan and runs the full guardrail and Strategy Co-Pilot check before you can execute it.
- Executed signals are logged as managed trades with their targets and the original text.

**Vision AI.** Upload, drop or paste a chart screenshot and Claude returns an objective technical read of everything on the chart, whatever your strategy:
- trend and market structure (BOS / CHoCH)
- support/resistance levels
- supply/demand and liquidity zones, order blocks, FVGs
- candlestick and chart patterns

Key elements are drawn as an overlay on your image. Trade ideas come with entry, stop, targets, trigger and invalidation, and **Send to Signals** turns one into a sized, guardrail-checked ticket. A chat panel lets you ask follow-up questions about the same chart. See [AI setup](#ai-setup).

**Risk Calculator.**
- Inputs: equity, risk %, stop as a price or in pips/points, optional target, FX rate for cross-currency instruments.
- Outputs: lot size (always rounded **down** to the lot step), max loss, actual risk %, pip value, R:R, potential profit and leverage, with live guardrail feedback.

**Positions.** Manages open trades with up to four take-profits. Each target closes a share of the original size, floored to the lot step, and the last one closes the rest.
- Optional auto **break-even after TP1** (with an offset) and a ratcheting **trailing stop**.
- Feed it the current price, or the high/low since the last update, per trade or per symbol. Stops are checked before targets when one bar touches both.
- Record partial closes, stop moves and full closes manually; every fill is kept in an event ledger and the last event can be undone.
- Each position card shows realised P&L, unrealised P&L at the last price, remaining lots, remaining risk (or *risk-free*), the target ladder and the timeline.

**Journal.** Each trade records:
- date/time, asset, direction, entry, initial stop, targets, exit, size, risk, fees and net P&L (auto-calculated from prices, or from the fill ledger for managed trades)
- planned vs executed R:R and R-multiple
- strategy, timeframe, session (auto-detected), confluences, emotion, mistakes, rule compliance and tags
- notes and chart screenshots: upload, drag & drop, paste from the clipboard, or a TradingView link

The journal has search, filters, sorting, pagination and full CRUD.

### Improve

**Calendar.** A monthly and weekly P&L calendar with daily cards and a weekly totals column; click a day to see its trades and review. A *time of day* heatmap (weekday × hour) shows net P&L, win rate or trade count, with a table view.

**Review.** A post-market review with a discipline rating (1-5), mood, whether you followed the plan, mistakes (pre-filled from the day's trades), what went well, what went wrong, lessons and tomorrow's focus. It autosaves and keeps a 30-day history.

**Incubator.** Test a strategy before trading it live:
- Add backtest trades by hand, or paste or import a CSV batch with a preview. Delimiters, header names, date formats and comma decimals are auto-detected.
- Metrics: win rate, profit factor, max drawdown (R), average R:R, expectancy in R and $ (at a configurable risk per trade), plus an R equity curve and distribution.
- A side-by-side **backtest vs live** comparison gives a verdict once enough live trades exist.
- Promote a strategy to *live* or retire it. A **PDF report** is available per strategy.

**Prop Firm.** Track a funded-account challenge (FTMO, FundedNext presets or custom rules). It shows:
- profit target progress
- daily loss used vs limit
- remaining room to the static or trailing max drawdown
- trading days and days left, with a balance chart and daily history

With *enforce* on, live entries that would breach the challenge's daily loss or drawdown are blocked. Presets are a starting point: firms change their rules, so always verify them on the firm's website.

### Setup

**Trading Plan.** Tabs for:
- Account & risk rules, including news guardrail, cool-off and position-management defaults
- Markets (contract specs)
- Sessions (UTC windows)
- Strategies, with the Strategy Co-Pilot requirements
- Confluences
- Checklist
- Mistakes
- AI assistant
- Notifications
- Partners

**Partners.** A reference list of brokers and prop firms, shown with a plain trading-risk disclaimer. Edit the list in *Trading Plan → Partners*, where you can also give a partner a custom link that replaces its official website link.

**Backup & Export.** Trades as CSV (Excel-friendly, formula-injection safe), a full JSON backup, and a ZIP with JSON plus screenshots. You can restore from JSON or ZIP; a safety snapshot of the current data is written to `backups/` first. Backups never contain your saved AI API key.

## How the guardrails work

| Rule | Triggers when | Severity |
|---|---|---|
| Pre-market checklist | required items not all ticked today | always **blocks** (if enabled) |
| Minimum R:R | planned R:R < your minimum (default 1:2); with several targets, the blended R:R | `trade_rule_mode`: block / warn |
| Max risk per trade | risk > max % of equity (default 2%) | `trade_rule_mode` |
| Strategy Co-Pilot | the strategy's required confluences, minimum confluence count, allowed sessions / timeframes / direction, stricter min R:R or bias alignment are not met | `trade_rule_mode` (incubator and retired strategies only warn) |
| No take profit | no target, so R:R can't be verified | warn |
| Outside trading window | entry outside your active sessions | warn (optional) |
| Max daily loss | today's realised loss ≥ limit (default 3% of start-of-day equity) | `daily_limit_mode`: lock / warn |
| Max trades per day | trade count reached (0 = off) | `daily_limit_mode` |
| Daily risk budget | this trade's risk + open risk could breach the daily limit | `daily_limit_mode` |
| News window | a release at or above your minimum impact is within ±N minutes (default 30) and affects the traded currency | `news_mode`: off / warn / block |
| Kill switch | you started a cool-off (1-72 h, default 24 h) | always **blocks**; ends early only after typing `UNLOCK` |
| Prop challenge | the active challenge's daily loss, max drawdown or remaining room would be breached; target reached or challenge ended | block when the challenge is *enforced*, else warn |

**Blocking only applies to live entries.** A live entry is a new *open* position
dated today. A journal must stay truthful, so trades you log after the fact (closed
trades, or past dates) are always saved, with any violations attached and the
trade marked as "broke rules". Closing or editing an existing trade is never
blocked, so you can always close a position while trading is locked.

The status bar on every page shows:
- the checklist state
- today's P&L and the daily loss meter
- the trade count
- whether entries are enabled
- the next high-impact release
- any running cool-off

It also has the **Kill switch** button. When entries are locked, a banner explains why.

## AI setup

Vision AI uses Anthropic's Claude API. It is optional, and the rest of the app works without it. Provide credentials in any of these ways:

1. Set the `ANTHROPIC_API_KEY` environment variable before starting the app, or
2. sign in once with the `ant auth login` CLI, or
3. paste a key in *Trading Plan → AI assistant*. The key is stored only in your local database, is never sent back to the browser, and is excluded from backups and exports.

In the same tab you choose the model and the effort level (higher effort is more thorough, but slower and more expensive). Charts you analyse are sent to Anthropic's API for processing. AI output can be wrong, so always verify levels and prices yourself.

## Calculations

- **Pip value per lot** = contract size × pip size × FX. FX is 1 when the quote currency is the account currency, 1/price when the base currency is (e.g. USDJPY on a USD account), and otherwise the rate you enter.
- **Lots** = floor((equity × risk %) / (stop pips × pip value), lot step), capped at the max lot. Zero, with an explanation, if even the minimum lot would exceed your risk.
- **Planned R:R** = |target − entry| / |entry − stop|. With several targets, the **blended R:R** = Σ (close % × target R:R).
- **Executed R:R** = signed (exit − entry) / |entry − stop|. **R-multiple** = net P&L / risk amount.
- **Win rate** = wins / all closed trades (breakevens count as non-wins). **Profit factor** = gross profit / |gross loss|.
- **Expectancy** = net P&L / trades. **Max drawdown** = largest peak-to-trough fall of the closed-trade equity curve.
- **Equity** = starting balance + realised P&L. Partial closes count when they are filled, and the daily loss limit uses equity at the start of the day.
- **Prop drawdown floor**: static = account size × (1 − max drawdown %); trailing = highest end-of-day balance − max drawdown % of the account size, capped at the account size.

Trade times are wall-clock times in the account time zone (`Trading Plan → Account`),
and sessions are defined in UTC. Entries are converted with DST awareness before
the session is detected.

## Project layout

```
trading_os/
  run.py / run.sh / run.bat   launchers
  app.py          FastAPI app factory (routes, static files, error handling)
  cli.py          command-line launcher           config.py   data dir and limits
  models.py       SQLAlchemy models               schemas.py  Pydantic validation
  database.py     engine, in-place schema migrations
  seed.py         default plan (markets, sessions, strategies, checklist, mistakes, partners)
  api/            REST routers (plan, checklist, risk, trades, positions, signals, news,
                  reviews, analytics, vision, backtest, prop, partners, reports, backup)
  services/       domain logic
    sizing.py         position sizing & R:R math           (pure)
    guardrails.py     risk rules & day state               (pure)
    copilot.py        Strategy Co-Pilot rules              (pure)
    position_engine.py targets, break-even, trailing stop  (pure)
    signal_parser.py  Telegram/WhatsApp signal parsing     (pure)
    analytics.py / time_analytics.py  metrics, breakdowns, heatmap, calendar
    sessions.py       time-zone-aware session detection    (pure)
    trades.py         trade lifecycle, derived fields      positions.py  fill ledger
    account.py        equity, realised P&L, daily status   checklist.py  daily routine
    news.py / context_rules.py  economic calendar, kill switch
    prop.py / prop_rules.py     prop challenge metrics and rules
    backtest.py       incubator metrics, CSV import, backtest vs live
    vision.py / ai_client.py    Claude chart analysis and Q&A
    reviews.py        post-market reviews               partners.py  partner listings
    report_pdf.py     PDF reports                        disclaimer.py risk disclaimer
    backup.py         CSV / JSON / ZIP export & restore  uploads.py   image storage
    demo.py           demo data generator
  static/         dark-themed SPA (vanilla ES modules, no build step; Chart.js vendored)
  tests/          unit + API tests
```

Interactive API documentation is served at <http://localhost:8765/api/docs>.

## Development

```bash
pip install -r trading_os/requirements.txt
python -m pytest trading_os/tests -q
black -l 120 trading_os && isort --profile black -l 120 trading_os
ruff check --select F,E9,B --line-length 120 trading_os
```

Notes:
- Tables are created on startup, and older databases are upgraded in place: new columns are added with their defaults and the schema version is kept in `PRAGMA user_version`. Backups from v1.0 onwards can be restored.
- The test suite never calls the network. The Claude client and the calendar feed are replaced with fakes.
- The server binds to `127.0.0.1` by default and has no authentication. Don't expose it to a network.
- Chart.js 4.4.7 is vendored in `static/vendor/` (MIT, licence included), so the app works offline. PDF reports use ReportLab.
