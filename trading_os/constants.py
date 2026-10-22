"""Enumerations shared by the API, services and frontend."""

DIRECTIONS = ("long", "short")
TRADE_STATUSES = ("open", "closed")
OUTCOMES = ("win", "loss", "breakeven")

TRADE_RULE_MODES = ("block", "warn")
DAILY_LIMIT_MODES = ("lock", "warn")

ASSET_CLASSES = ("forex", "metal", "index", "commodity", "crypto", "stock")
CURRENCIES = ("USD", "EUR", "GBP", "CHF", "JPY", "AUD", "CAD", "NZD")
BIASES = ("bullish", "bearish", "neutral")
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1")

EMOTIONS = (
    "Calm",
    "Confident",
    "Focused",
    "Anxious",
    "FOMO",
    "Greedy",
    "Fearful",
    "Impatient",
    "Revenge",
    "Bored",
    "Euphoric",
    "Tired",
)

# Share of the daily loss limit used at which the dashboard turns amber.
DAILY_LOSS_WARNING_RATIO = 0.75

# Psychological / execution mistakes seeded as the default mistake library.
DEFAULT_MISTAKES = (
    ("FOMO entry", "Entered out of fear of missing the move, without a valid trigger."),
    ("Revenge trade", "Traded to win back a loss rather than on a setup."),
    ("Oversized", "Risked more than the plan allows."),
    ("Early exit", "Closed before the target or stop without a plan reason."),
    ("Late entry", "Chased the entry after the planned price had gone."),
    ("Moved stop loss", "Widened or removed the stop after entry."),
    ("Ignored plan", "Took a trade outside the written plan."),
    ("Overtrading", "Too many trades / trading out of boredom."),
    ("Held loser too long", "Hoped instead of respecting the stop."),
    ("Traded the news", "Entered around a high-impact release against the rules."),
)

NEWS_MODES = ("off", "warn", "block")
NEWS_IMPACTS = ("low", "medium", "high")
TRADE_SOURCES = ("manual", "signal", "calculator")
TRAIL_TRIGGERS = ("tp1", "immediate")
MOODS = ("Calm", "Satisfied", "Neutral", "Frustrated", "Anxious", "Tilted", "Exhausted", "Euphoric")

MAX_TARGETS = 4
# Default share of the original size closed at each target, by number of targets.
DEFAULT_TP_SPLITS = {1: (100,), 2: (50, 50), 3: (50, 25, 25), 4: (40, 30, 20, 10)}

# Quick-add presets for the economic calendar: (title, currency).
NEWS_PRESETS = (
    ("Non-Farm Payrolls (NFP)", "USD"),
    ("CPI (Inflation)", "USD"),
    ("Core PCE", "USD"),
    ("FOMC Rate Decision", "USD"),
    ("FOMC Minutes", "USD"),
    ("Fed Chair Speech", "USD"),
    ("PPI", "USD"),
    ("Retail Sales", "USD"),
    ("GDP", "USD"),
    ("Unemployment Claims", "USD"),
    ("ISM Manufacturing PMI", "USD"),
    ("ECB Rate Decision", "EUR"),
    ("BoE Rate Decision", "GBP"),
    ("BoJ Rate Decision", "JPY"),
)

STRATEGY_STATUSES = ("live", "incubator", "retired")

# Claude models selectable for Vision AI (first = default). Server-side refusal
# fallbacks are only requested for the models in AI_FALLBACK_MODELS.
AI_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1")
AI_FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"})
AI_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Anti-FOMO checks on Vision AI trade ideas (warnings only, never blocks). Shares are of the visible price range.
FOMO_CHASE_R = 1.0  # price already ran this many R past a limit/stop idea's entry: entering now is chasing
FOMO_STOP_RANGE_SHARE = 1 / 3  # entry-to-stop distance at least this share of the range: far from invalidation
FOMO_STRUCTURE_RANGE_SHARE = 0.20  # an enter-now entry this far from the nearest level/zone behind it
FOMO_RANGE_EXTREME = 0.10  # long in the top / short in the bottom tenth of the visible range
FOMO_NEAR_PRICE_R = 0.25  # an entry within this many R of the last price counts as entering now
