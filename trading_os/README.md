# Trading OS & Journal

A local, single-user web app that runs your trading day end to end. You set up a
trading plan once. A pre-market routine and an economic calendar gate your trade
entries. Position sizing comes with hard risk rules and a signal parser, and open
positions can be managed with scale-out targets. The trade journal includes
mistakes and post-market reviews. A statistical dashboard tells you whether you
actually have an edge. On top of that there are a strategy incubator for
backtests, a prop-firm challenge tracker, an optional AI chart assistant, a
strategy alert engine that scans the market for your setups, and push
notifications to your phone.

Everything runs on your machine. Data lives in one SQLite file plus a
screenshots folder, and there are no accounts or telemetry. The only network
calls are the optional ones you set up yourself: Vision AI, the economic
calendar import, market data for strategy alerts and your notification channels.

> **Risk warning.** Trading involves substantial risk of loss and is not
> suitable for everyone. Content and tools are for organizational and
> educational purposes only and do not constitute financial advice. Strategy
> alerts never execute anything: the decision to trade is always yours. The app
> shows this warning on first launch and in the footer of every page.

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

**Signals.** Paste a Telegram/WhatsApp signal and the parser extracts symbol, direction, entry (or entry zone), stop and TP1 to TP4. It copes with aliases like GOLD/XAU, broker suffixes, emojis, comma decimals, thousands separators (`18.550` on an index is 18550) and stops or targets given in pips. The price after BUY/SELL is the entry; notes such as "add more at 2645" after the stop and targets don't replace it, but they do raise a warning.
- The resulting ticket is sized with your risk %, shows the blended R:R of the scale-out plan and runs the full guardrail and Strategy Co-Pilot check before you can execute it.
- Executed signals are logged as managed trades with their targets and the original text.

**Strategy Alerts.** A background scanner watches the market for your strategies and notifies you when a setup lines up. **It never trades:** every alert is information, and the decision is yours.
- A *rule* binds a strategy to symbols, a timeframe (M1 to D1), a market data source and a list of conditions. The conditions are EMA trend or cross, RSI, liquidity sweep, break of structure, engulfing candle, pin bar, fair value gap, range breakout, key level and an ATR volatility filter. Each condition counts as one of your plan's confluences.
- On every closed candle the engine checks the conditions. Directional conditions must agree, then the strategy's Co-Pilot rules must pass: required confluences, minimum count, sessions, timeframes, direction and minimum R:R. Candles that closed between two scans are checked too (up to five), and an alert on such a candle says when it closed.
- A setup gets entry, stop (swing, ATR or the signal's own structure), scale-out targets, blended R:R and a position size at your risk %. It also gets a *guardrail pre-check* that tells you whether an entry would be blocked right now, e.g. checklist not done, news window or daily loss limit.
- The alert is stored, pushed to your phone (see Notifications) and listed on the page. **Review in Signals** opens it in the signal ticket with the alert's strategy, timeframe, confluences and risk, and sizing, guardrails and the Co-Pilot run again before you decide. A trade executed from it marks the alert *taken*.
- Each rule can be tested on the latest data with per-condition diagnostics. The scanner can be switched on or off, and a scan can be run on demand.
- Market data sources:
  - *Yahoo Finance*: free and unofficial, may be delayed; gold uses COMEX futures, not spot.
  - *Binance*: crypto.
  - *Twelve Data*: needs your API key.
  - *Pushed from your platform*: an MT4/MT5 script, a TradingView webhook or your own script, see below.
  - *Demo*: synthetic prices for trying the scanner offline. Never trade from them.

**Vision AI.** Upload, drop or paste a chart screenshot and Claude returns an objective technical read of everything on the chart, whatever your strategy:
- trend and market structure (BOS / CHoCH)
- support/resistance levels
- supply/demand and liquidity zones, order blocks, FVGs
- candlestick and chart patterns

Key elements are drawn as an overlay on your image. Trade ideas come with entry, stop, targets, trigger and invalidation, and **Send to Signals** turns one into a sized, guardrail-checked ticket, checked against the strategy and timeframe the chart was analysed with. A chat panel lets you ask follow-up questions about the same chart. **Run again** replaces an analysis only when the new one succeeds. See [AI setup](#ai-setup).

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
- A side-by-side **backtest vs live** comparison gives a verdict once enough live trades exist. Its **expectancy gap** shows how far live trading is from the backtest per trade: in R, in money at the backtest's risk per trade (so position size and fees don't distort it), and as the share of the backtest edge kept live, next to what live trades really made. Drawdowns are compared in R, money and %. Renaming a strategy keeps its live history: journal trades logged under the old name still count (a new strategy that takes the old name owns them from then on).
- Strategies being tested are shown as *In test*. Promote a strategy to *live* or retire it. A **PDF report** is available per strategy.

**Prop Firm.** Track a funded-account challenge (FTMO, FundedNext presets or custom rules). It shows:
- profit target progress
- daily loss used vs limit
- remaining room to the static or trailing max drawdown
- trading days and days left, with a balance chart and daily history

With *enforce* on, live entries that would breach the challenge's daily loss or drawdown are blocked. Presets are a starting point: firms change their rules, so always verify them on the firm's website.

### Setup

**Notifications** (*Trading Plan → Notifications*). Send strategy alerts, risk events and news reminders to your phone or browser. Risk events cover daily loss limit near or reached, max trades, kill switch and prop-firm rule breaches. News reminders arrive a few minutes before high-impact releases. A master switch turns everything on or off, and each event type can be chosen separately. Channels:
- **ntfy**, recommended for phones: install the free ntfy app (Android/iOS), subscribe to a private, hard-to-guess topic name and enter the same topic here.
- **Telegram**: create a bot with @BotFather, send it a message, then enter the bot token and your chat id.
- **Pushover**, **Discord** and **Slack** (incoming webhooks).
- **Webhook**: JSON POST to any URL, e.g. Home Assistant, n8n or your own script. It is signed with `X-TradingOS-Signature: sha256=<HMAC-SHA256 of the body>` when you set a secret.
- **Web Push** to this browser or device. Browsers only allow it on `https://` or `localhost`, so it works on the computer running Trading OS. Phones need the app served over HTTPS (on iOS 16.4+ also added to the Home Screen); otherwise use ntfy, Telegram or Pushover.

  Set *App link* (e.g. `http://192.168.1.20:8765`) so a tap on a notification opens the right page. Every channel has a Test button, and the history shows what was delivered and why something failed.

  *Quiet outside my active sessions* holds strategy alerts and news reminders while none of your active sessions is open: they stay on the Alerts page and in the history (marked *held*), but are not pushed. Risk events and tests always come through.

**Trading Plan.** Renaming a confluence or session updates the strategies and alert rules that use it, and deleting a strategy that still has backtest trades asks for confirmation first. Tabs for:
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

**Backup & Export.** Trades as CSV (Excel-friendly, formula-injection safe), a full JSON backup, and a ZIP with JSON plus screenshots. You can restore from JSON or ZIP; a safety snapshot of the current data is written to `backups/` first. Backups never contain secrets: no AI API key, notification channels, Web Push keys or market data keys. These stay on the machine and are kept as they are on restore.

## How the guardrails work

| Rule | Triggers when | Severity |
|---|---|---|
| Pre-market checklist | required items not all ticked today | always **blocks** (if enabled) |
| Minimum R:R | planned R:R < your minimum (default 1:2); with several targets, the blended R:R | `trade_rule_mode`: block / warn |
| Max risk per trade | risk > max % of equity (default 2%) | `trade_rule_mode` |
| Strategy Co-Pilot | the strategy's required confluences, minimum confluence count, allowed sessions / timeframes / direction, stricter min R:R or bias alignment are not met | `trade_rule_mode` (incubator and retired strategies only warn) |
| No take profit | no target, so R:R can't be verified | warn |
| Outside trading window | entry outside your active sessions (*Trading Plan → Sessions*, "Part of my trading window") | off / warn / block (`session_rule_mode`) |
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

## Strategy alerts: market data

The scanner runs in the background while Trading OS is running. Turn it on and set its interval on the
*Strategy Alerts* page. Each source is asked for each symbol and timeframe once per scan, and only closed
candles are evaluated. A candle counts as closed once its real end time (in UTC, so daylight-saving changes
don't matter) has passed, plus a short grace period for feeds that publish late. Yahoo H4 candles are built from
hourly bars and wait for their last hourly bar to end. Each rule and symbol alerts at most once per signal
candle, even if you delete the alert, and not again within the rule's cooldown. The page warns when the scan
interval is so long that candles could be skipped. Conditions can look back at most 480 candles.

To use your own feed, choose the *Pushed from my platform* source and send closed candles from an MT4/MT5 script,
a TradingView webhook or any script:

```http
POST /api/alerts/candles
Content-Type: application/json

{"symbol": "XAUUSD", "timeframe": "M15", "token": "<push token, if set>",
 "bars": [{"time": "2026-10-20T09:15:00Z", "open": 2650.1, "high": 2654.0, "low": 2648.7, "close": 2653.2}]}
```

`time` is the bar's open time. Times with a zone are converted to the account time zone. Bars that have not closed
yet are skipped (the response counts them in `skipped_forming`), so sending the last N bars including the forming
one is fine. Symbols are stored upper-case; set a rule's provider symbol (e.g. `XAUUSDm`) to match what your
platform sends. Set a push token on the Strategy Alerts page before exposing this endpoint beyond your machine
(e.g. through a tunnel for TradingView).

## Calculations

- **Pip value per lot** = contract size × pip size × FX. FX is 1 when the quote currency is the account currency, 1/price when the base currency is (e.g. USDJPY on a USD account), and otherwise the rate you enter.
- **Lots** = floor((equity × risk %) / (stop pips × pip value), lot step), capped at the max lot. Zero, with an explanation, if even the minimum lot would exceed your risk.
- **Planned R:R** = |target − entry| / |entry − stop|. With several targets, the **blended R:R** = Σ (close % × target R:R).
- **Executed R:R** = signed (exit − entry) / |entry − stop|. **R-multiple** = net P&L / risk amount.
- **Win rate** = wins / all closed trades (breakevens count as non-wins). **Profit factor** = gross profit / |gross loss|.
- **Expectancy** = net P&L / trades. **Max drawdown** = largest peak-to-trough fall of the closed-trade equity curve. Reports filter trades by close date; with a start date, the curve starts from the account equity at that date (without partial profits that reported trades took earlier, since each trade counts its whole P&L when it closes).
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
  api/            REST routers (plan, checklist, risk, trades, positions, signals, alerts, news,
                  notifications, reviews, analytics, vision, backtest, prop, partners, reports, backup)
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
    alerts.py         strategy alert engine and scanner job
    detectors.py      indicators and alert conditions        (pure)
    market_data.py    candle sources (Yahoo, Binance, Twelve Data, pushed, demo)
    notify.py / notify_channels.py  notification delivery, risk and news watchers
    webpush.py        Web Push: VAPID signing and payload encryption
    scheduler.py      background job thread
    demo.py           demo data generator
  static/         dark-themed SPA (vanilla ES modules, no build step; Chart.js vendored),
                  service worker (sw.js) and web app manifest
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
- The test suite never calls the network. The Claude client, the calendar feed, market data sources and notification channels are replaced with fakes.
- Background jobs (alert scanner, risk and news watchers) run on one thread inside the server. Set `TRADING_OS_BACKGROUND_JOBS=0` to switch them off; the tests do this and run jobs on demand.
- The server binds to `127.0.0.1` by default and has no authentication. Don't expose it to a network. If you open it on your LAN (`--host 0.0.0.0`) so a phone can open notification links, do it only on a network you trust.
- Requests are answered only for `localhost` and IP addresses, which protects against DNS rebinding. To use a host name (a LAN name, or a tunnel or reverse proxy), list it in `TRADING_OS_ALLOWED_HOSTS`, e.g. `TRADING_OS_ALLOWED_HOSTS=trading.lan,my-tunnel.example`. Changes from other websites are refused: a write whose `Origin` or `Sec-Fetch-Site` header shows another site gets 403. Scripts that send no `Origin` header, such as the candle push, keep working.
- Chart.js 4.4.7 is vendored in `static/vendor/` (MIT, licence included), so the app works offline. PDF reports use ReportLab.
