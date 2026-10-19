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
