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
> alerts never execute anything: the decision to trade is always yours. Orders
> reach a connected trading account only when you confirm them, and then they are
> real trades. The app shows this warning on first launch and in the footer of
> every page.

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
| `--lan` | off | Let phones and other computers on your network open the app (same as `--host 0.0.0.0`) |
| `--allow-host NAME` | — | Also answer to this host name, e.g. `trading.lan` or `*.trycloudflare.com` (repeatable) |
| `--data-dir` | `trading_os/data` | Database, screenshots and backups folder (env: `TRADING_OS_DATA_DIR`) |
| `--demo` | off | Use `trading_os/demo_data` and seed sample data |
| `--no-browser` | off | Don't open the browser |

### "This site can't be reached" or a Google search instead of the app

- Type `http://localhost:8765` in the browser's **address bar** (the bar at the very top), not in the
  Google search box on the page. Without `http://` some browsers search for the words instead of opening
  the address. A bookmark avoids the problem.
- The page only opens while the server is running: keep the `run.bat` / `run.sh` window open. Closing it
  (or pressing Ctrl+C) stops Trading OS.
- If the window says the port is already in use, another copy is running (open the address above), or
  start this one on another port: `run.bat --port 8766`, then open `http://localhost:8766`.
- On first start the window shows the exact address to open, plus the network addresses when `--lan` is on.

## Open it from your phone or another computer

Trading OS runs on your computer; there is no cloud copy. To use it from another device, that device has to
reach your computer:

| Where the other device is | What to do |
|---|---|
| Same Wi-Fi / network | Start with `run.bat --lan` (or `run.sh --lan`) and open one of the *On your network* addresses the window prints, e.g. `http://192.168.1.20:8765`. If Windows Firewall asks, allow Python on **Private** networks. |
| Same network, by name | Your computer's own name (`mypc`, `mypc.local`) works automatically. Other names, e.g. `trading.lan` from your router's DNS, go in *Trading Plan → Network & access → Allowed host names*. |
| Anywhere (mobile data, office) | Keep the app local and run a tunnel on the same computer. *Trading Plan → Network & access* has copyable commands: `cloudflared tunnel --url http://localhost:8765` (Cloudflare quick tunnel), `ngrok http 8765`, `tailscale serve --bg 8765` (private to your own Tailscale devices, with HTTPS), or an SSH port forward (`ssh -L 8765:localhost:8765 you@this-computer`, which counts as this computer). Then allow the tunnel's name, e.g. `*.trycloudflare.com`, `*.ngrok-free.app` or `*.ts.net`. |

**Allowed host names.** Trading OS answers to `localhost`, IP addresses, your computer's own name and the
names you allow; any other name gets a short page explaining how to allow it (this blocks DNS-rebinding
attacks from websites). Allow names in any of three places:
- *Trading Plan → Network & access* (saved in the journal, takes effect immediately);
- the start-up option `--allow-host NAME` (repeatable);
- the environment variable `TRADING_OS_ALLOWED_HOSTS`, comma separated, e.g.
  `TRADING_OS_ALLOWED_HOSTS=trading.lan,*.trycloudflare.com` (Windows: `set TRADING_OS_ALLOWED_HOSTS=...`
  before `run.bat`; macOS/Linux: `TRADING_OS_ALLOWED_HOSTS=... trading_os/run.sh`).

Entries can be pasted as full URLs (`https://abc.ngrok-free.app/` becomes `abc.ngrok-free.app`). A `*`
replaces the first part only: `*.example.com` matches `a.example.com` but not `example.com`. A `*` before a
single name is allowed only for local network suffixes (`*.lan`, `*.local`, `*.home`, `*.internal`,
`*.home.arpa`, `*.localdomain`). Entries that cannot be used are listed in the start-up window and on the
settings tab.

**Remote-access password (recommended).** Anyone who can open the address can read and change your journal.
Set a password in *Trading Plan → Network & access*: other devices then see a sign-in form, and stay signed
in for 30 days (a cookie that scripts cannot read). The computer running Trading OS never needs it. After 5
wrong passwords from one address, sign-in pauses for a few minutes. *Sign out everywhere* and changing the
password end every other device's session. With a password set, a candle push from outside this computer
needs the push token from *Strategy Alerts → Data providers*.

Allowed names, the password and sign-outs can be changed only on the computer running Trading OS
(at `http://localhost:8765`, or at the address given with `--host`), never from a phone or through a tunnel, so a remote visitor cannot lock you
out or widen access. The tab also shows how the current device is connected and which rule let it in.

## What's inside

The sidebar groups the pages into **Trade**, **Improve** and **Setup**.

### Dashboard, accounts and getting started

**Dashboard hero.** The dashboard opens with the Trading OS logo, the account shown ("All accounts", or the account with its Live / Demo / Prop / Funded badge) and four live figures:
- **Account Balance**: the closed-trade balance, with the starting balance.
- **Live Equity**: balance plus floating P&L, with the change since the start in money and %.
- **Floating P&L**: open positions at their last price, with the number of open positions and how many have no live price yet.
- **Active Margin**: margin in use, with free margin and margin level when known. It is marked *estimated* when some positions have no margin figure: then it is notional ÷ the account's leverage.

A chip shows where the figures come from: *Live from bridge · updated 12s ago* when the account's bridge reported in the last 5 minutes, otherwise *From journal*. The figures refresh every 12 seconds while the dashboard is open. The promo carousel sits above the hero when it has cards.

**Account switcher.** The dropdown in the top bar lists:
- *All accounts*: the combined equity of every active real-money account in the main currency. The menu says how many accounts are left out (demo, another currency, archived).
- every active account, with its colour, type and equity.

Choosing one switches the whole app to that account (see *Trading accounts* below). It works with the keyboard (arrow keys, Home/End, Enter, Escape) and has a *Manage accounts* link.

**Trading Accounts** (*Setup → Trading Accounts*). Register every account you trade: prop-firm challenges, live and demo broker accounts. Quick templates (*Prop firm challenge*, *Live broker account*, *Demo account*) fill in the type and platform.
- Each card shows broker, platform, login, server, currency, leverage and status; balance, equity, floating P&L and margin; the connection status; and *Default* / *Shown* badges.
- From a card you can show the account on the dashboard, edit, archive, reactivate or delete it, and set up its connection and order sending.

**Profile menu.** The avatar shows the initials of the account shown. It opens Trading accounts, Trading plan, Network & access, the English / Italiano switch and the Getting started guide.

**Getting started guide.** The play button next to the avatar opens a guide with two tabs:
- **Video**: set a YouTube, Vimeo or direct .mp4 / .webm link, or a file under `/static/`, in *Trading Plan → Account & risk rules → Onboarding video link*. YouTube plays from youtube-nocookie.com.
- **Step-by-step**: nine steps, each with a *Take me there* link. They cover the trading plan and risk rules, accounts and the switcher, the pre-market checklist, signals and the risk calculator with execution, positions and journal, strategy alerts and Vision AI, review/calendar/incubator, prop-firm tracking, and backup and remote access.

On first launch the guide opens by itself once the risk disclaimer has been accepted, once per browser session, until you tick *Don't show this again* or finish the tour.

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

Key elements are drawn as an overlay on your image. Trade ideas come with entry, stop, targets, trigger and invalidation, and **Send to Signals** turns one into a sized, guardrail-checked ticket, checked against the strategy and timeframe the chart was analysed with. A chat panel lets you ask follow-up questions about the same chart. **Run again** replaces an analysis only when the new one succeeds. Analyses and answers come back in the language chosen under *Trading Plan → Account*. See [AI setup](#ai-setup).

**Anti-FOMO check** (on by default; *Trading Plan → Process rules*). Claude also rates how extended the latest move is against a visible mean (normal, extended, overextended). On top of that, each idea is checked against the chart's prices. These are warnings only, nothing is ever blocked:
- price already 1R or more past a limit/stop entry (chasing);
- price beyond the stop, or already at the first target;
- reward:risk from the current price below your minimum (the stricter of the account's and the strategy's);
- a long at the top or a short at the bottom tenth of the visible range;
- a stop a third or more of the visible range away;
- an "enter now" price far from any level or zone behind it.

Flagged ideas need a confirmation before **Send to Signals**, and the warnings travel with the ticket into the final execute confirmation.

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

**Calendar.** A monthly and weekly P&L calendar with daily cards and a weekly totals column. P&L is booked on the day it was realised: each partial exit on the day it was filled (also while the trade is still open), and the rest of the trade on its closing day. The day's total therefore matches the status bar's *Today*. Click a day to see the fills behind its P&L and its review. A *time of day* heatmap (weekday × hour) shows net P&L, win rate or trade count, with a table view.

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

**Language.** English or Italian, under *Trading Plan → Account & risk rules → Language / Lingua*. It covers menus, labels, messages, guardrail warnings, notifications, PDF reports and the AI answers (Vision AI and chart Q&A). What you type yourself, such as strategy names and notes, stays as written. The page reloads when you switch.

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
- Promotions (the dashboard carousel)
- Network & access (other devices, allowed host names, remote-access password)

**Partners.** A reference list of brokers and prop firms, shown with a plain trading-risk disclaimer. Edit the list in *Trading Plan → Partners*, where you can also give a partner a custom link that replaces its official website link.

**Promo carousel.** The dashboard can show a rotating banner carousel at the top:
- platform announcements;
- cards for your featured partners;
- discount codes you received yourself.

It rotates every few seconds and stops while you hover it, use the keyboard inside it, switch tabs or press pause. It never rotates when your system asks for reduced motion. Browse with the arrows, the dots, the keyboard arrow keys or a swipe. The × button hides a card on this device; *Show hidden* brings it back.

Manage it in *Trading Plan → Promotions*:
- switch it on or off and set the rotation interval;
- reorder cards, switch them off, or schedule them with start and end dates in your account time zone;
- add announcements, partner cards or discount cards, with a live preview.

Partner cards use the partner's own description and link. Partner and discount cards always show the risk disclaimer. Trading OS never suggests codes or offers: a discount card shows only what you typed. Button links can be app pages (`#/journal`) or `https://` links, which open in a new tab.

**Backup & Export.** Trades as CSV (Excel-friendly, formula-injection safe), a full JSON backup, and a ZIP with JSON plus screenshots. You can restore from JSON or ZIP; a safety snapshot of the current data is written to `backups/` first. Backups never contain secrets: no AI API key, notification channels, Web Push keys or market data keys. These stay on the machine and are kept as they are on restore.

## Trading accounts

Trading OS keeps one journal for several trading accounts: for example *Prop Firm Challenge 1*, *Prop Firm
Challenge 2* and a *Live broker account*. Every trade belongs to one account.

- **The default account.** It is created on first start from *Trading Plan → Account & risk rules*: account name,
  currency and starting balance. Those settings stay its source of truth. Journals from before v1.5 put all their
  trades on it, so nothing changes until you add a second account. Backups from older versions restore the same way.
- **The account shown.** The account switcher in the top bar picks one account, or *All accounts* together. The
  dashboard, journal, positions, calendar, reviews, analytics and PDF reports then show that account. New trades are
  logged on the account shown, and the trade form can pick another. The choice is remembered on this computer.
- **All accounts** adds up your real-money accounts in the main currency. Demo accounts, accounts in another currency
  and archived accounts are shown on their own.
- **Each account has its own guardrails.** A new trade is judged against its own account's equity, daily loss limit
  and trade count. A losing day on a challenge never locks your live account.
  - With *All accounts* shown, the status bar takes the worst account's state and names the account behind each
    warning ("Challenge 1: Daily loss limit reached"). Risk notifications name the account too.
  - The checklist, kill switch, cool-off and news rules apply to every account.
- **Prop challenges** can be linked to their trading account (*Prop Firm → Edit → Trading account*): only that
  account's trades count towards the target and the drawdown rules. Its rules lock only entries on that account.
  An unlinked challenge counts every trade, as before.
- **Strategy alerts** size their tickets on the rule's account (*Sized on account*; default: the default account).
- **CSV export** has an `account` column.
- **Deleting an account** is refused while trades are logged on it: archive it instead. Journal history is never
  deleted. The default account can be neither archived nor deleted.
- **Credentials** are write-only:
  - Broker passwords, API tokens, webhook secrets and bridge tokens are stored encrypted (Fernet) with a key kept
    in `secret.key` in the data folder.
  - No page or API returns them; only the last characters are shown as a hint.
  - They are never included in backups, and a restore never switches order routing on.

## Connecting accounts and sending orders

Each trading account can be connected, so that a signal you confirm is sent straight to it. Open *Trading Accounts*, pick an account and use its **Connection & order sending** panel.

| Connection | What happens | Needs |
|---|---|---|
| **Paper trading** | Market orders fill at the ticket's price; limit and stop orders wait as pending. Nothing leaves Trading OS: the safe way to try the flow (the demo journal has a paper account). | nothing |
| **MetaTrader EA bridge (MT4/MT5)** | Orders are queued. An Expert Advisor in your MetaTrader terminal, on this PC or a VPS, fetches them, places them and reports fills and account figures. Works with any MT4/MT5 broker. | the EA + a bridge token |
| **MetaTrader 5 on this computer** | Trading OS drives the MT5 terminal installed here through the official `MetaTrader5` Python package. **Windows only** (`pip install MetaTrader5`). | MT5 login and server on the account, MT5 password |
| **Webhook bridge** | Each order is POSTed as JSON to your own bridge (trade copier, VPS script). With a signing secret the body is signed: `X-TradingOS-Signature: sha256=<HMAC-SHA256 of the body>`. A 2xx answer means sent; a JSON answer with `ticket`/`order_id` and `fill_price` is shown as the fill. https only (plain http for localhost/LAN). | webhook URL, optional secret |
| **OANDA** | Orders go to your OANDA practice or live account through the v20 REST API (units = lots × contract size; XAUUSD becomes XAU_USD). | account ID, environment, API token |

Every connection can add a **symbol suffix** (`XAUUSD` → `XAUUSD.m`) or a **symbol map** (`XAUUSD=GOLD`) for broker symbol names. Passwords and tokens are write-only (see *Trading accounts* above).

**Sending an order.**
1. On *Signals*, choose the **Trading account**. It defaults to the account shown, else the default account, and sizing uses that account's equity.
2. Next to *Execute & log trade* (journal only), **Execute on <account>** is enabled when that account has order sending switched on.
3. It asks you to confirm account, symbol, side, lots, entry, stop loss and targets.
4. It then shows the result: filled at X with the ticket, queued for the MetaTrader terminal, or rejected with the reason.

The journal trade is logged on that account at the fill price. The order carries the stop loss and the last target; exits at the other targets are tracked in the journal.

**MetaTrader EA setup.**
1. Download `TradingOS_Bridge.mq5` (MT5) or `TradingOS_Bridge.mq4` (MT4) from the account's panel; both are also in `trading_os/bridges/`.
2. In MetaTrader open *File > Open Data Folder > MQL5 (or MQL4) > Experts* and copy the file there. Open it in MetaEditor and press **Compile**.
3. In *Tools > Options > Expert Advisors*, tick **Allow WebRequest for listed URL** and add the Trading OS address exactly as you type it in the EA, e.g. `http://127.0.0.1:8765`. From a VPS, use the address of the computer running Trading OS; if it is a host name, allow it under *Trading Plan → Network & access*.
4. On the computer running Trading OS, create the **bridge token** in the account's panel and copy it.
5. Attach the EA to one chart, paste the server address and the token, switch Algo Trading on, then press **Test connection**. "Last seen" turns green.
6. **Test on a demo account first.** The EA sources are templates that have not been compiled here.

**Bridge protocol.** It is plain text. The token goes in the `X-Bridge-Token` header or in `?token=`.
- `GET /api/bridge/orders` returns one order per line: `id|symbol|side|type|lots|price|sl|tp`.
- `POST /api/bridge/orders/{id}` takes `status=filled|placed|rejected&ticket=..&price=..&message=..`.
- `POST /api/bridge/account` takes `balance=..&equity=..&margin=..&free_margin=..&profit=..&positions=..`. These figures feed the live account metrics on the dashboard.

The bridge endpoints work from another machine even when a remote-access password is set: they check the token themselves, and an unknown token gets 401.

**Safety rails.**
- **Off by default, on deliberately.** Order sending is a per-account switch. Anything but paper trading needs the account's exact name typed to turn it on. These switch it off: changing the connection or where it sends, archiving the account, or changing an MT5 terminal account's login or server. A restore never switches it on.
- **Your guardrails first.** An order runs the same checks as logging the trade, on its own account: kill switch, daily loss limit, checklist gate, prop-firm rules and risk caps. A blocked trade is never sent (HTTP 423).
- **Never twice.** Each ticket has an idempotency key, so a double click or a retried request returns the first result. The EA receives each order once and remembers the last order it placed.
- **No stale fills.** MetaTrader orders not picked up within the order lifetime (default 120 s) expire. Switching sending off cancels the orders still waiting.
- **Size cap.** *Max lots per order* refuses a larger order before anything is sent.
- **Only on your click.** Strategy alerts and background jobs never send orders. Execution happens only when you press *Execute on <account>* and confirm.
- **Honest journal.** A pending order that is rejected, cancelled or expires removes its untouched journal trade; the execution log keeps the record. A trade you already edited is kept and tagged `order-rejected`.
- **No secrets in logs.** The execution log (`GET /api/executions`) never stores tokens, passwords or signatures. The bridge token can be shown or rotated only on the computer running Trading OS.

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
- **Expectancy** = net P&L / trades. **Max drawdown** = largest peak-to-trough fall of the realised equity curve.
- **When P&L counts.** Daily P&L, the equity curve, drawdown and return follow the money realised in the period. Each partial exit is booked on its fill day, and the closing exit carries the trade's fees. Trade statistics (count, win rate, profit factor, expectancy, R) count each trade once, at its close. With a date range, the dashboard and PDF start from the account equity at the start date and include partial exits taken in the range on trades that close later.
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
  api/            REST routers (plan, accounts, execution, checklist, risk, trades, positions, signals,
                  alerts, news, notifications, reviews, analytics, vision, backtest, prop, partners,
                  promos, reports, backup, network, i18n)
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
    accounts.py       trading accounts, the account shown (scope), live account metrics
    secrets_store.py  encrypted, machine-local account credentials (Fernet + secret.key)
    execution.py / bridges/  order sending: paper, webhook, MetaTrader EA, MT5 terminal, OANDA
    promos.py         dashboard carousel cards
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
  bridges/        MetaTrader Expert Advisors (TradingOS_Bridge.mq5 / .mq4) for the EA bridge
  locales/it/     Italian catalogs (server and web)
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
- The server binds to `127.0.0.1` by default. `--lan` listens on every network; set a remote-access password before you do, or use it only on a network you trust (see *Open it from your phone or another computer*).
- Requests are answered only for `localhost`, IP addresses, the computer's own name and allowed host names, which protects against DNS rebinding. Changes from other websites are refused: a write whose `Origin` or `Sec-Fetch-Site` header shows another site gets 403 (wildcard entries are never trusted as an `Origin`). Scripts that send no `Origin` header, such as the candle push, keep working.
- A request counts as coming from this computer only when it arrives on a loopback connection, with a `localhost` host name and without proxy or tunnel headers (`X-Forwarded-*`, `Forwarded`, `CF-Connecting-IP`, `Via`, ...). `X-Forwarded-Host` / `X-Forwarded-Proto` are read only from a proxy or tunnel on this computer. The password is stored as a salted scrypt hash and is never included in backups, like the session signing key.
- Chart.js 4.4.7 is vendored in `static/vendor/` (MIT, licence included), so the app works offline. PDF reports use ReportLab.
