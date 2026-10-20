"""Signal parser: real-world Telegram / WhatsApp formats, aliases, numbers and sanity checks."""

import pytest

from trading_os.services.signal_parser import parse_signal, resolve_close_pcts, split_lots

SYMBOLS = ("XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "US30", "NAS100", "SPX500", "GER40", "BTCUSD")


def parse(text, symbols=SYMBOLS):
    return parse_signal(text, symbols)


# (message, symbol, direction, order type, entry, stop, take profits)
CORPUS = [
    (
        "XAUUSD BUY NOW @ 2650 SL 2640 TP1 2660 TP2 2670 TP3 2685",
        "XAUUSD",
        "long",
        "market",
        2650,
        2640,
        [2660, 2670, 2685],
    ),
    (
        "Sell EUR/USD @1.0850\nStop Loss 1.0880\nTake Profit 1: 1.0820\nTake Profit 2: 1.0790",
        "EURUSD",
        "short",
        "market",
        1.085,
        1.088,
        [1.082, 1.079],
    ),
    (
        "#XAUUSDm SELL LIMIT 2675\nS/L 2685\nT/P 2665 / 2655 / 2640",
        "XAUUSD",
        "short",
        "limit",
        2675,
        2685,
        [2665, 2655, 2640],
    ),
    ("US30 long entry 39100 sl 38950 tp 39400", "US30", "long", "market", 39100, 38950, [39400]),
    ("NAS100 SHORT 18,550 SL: 18,620 TP: 18,400", "NAS100", "short", "market", 18550, 18620, [18400]),
    (
        "BTCUSD 🔴 SELL Entry: 67,250 SL 68,000 TP1 66,500 TP2 65,800 TP3 65,000 TP4 63,500",
        "BTCUSD",
        "short",
        "market",
        67250,
        68000,
        [66500, 65800, 65000, 63500],
    ),
    ("gold buy limit 2635 / sl 2628 / tp 2645, 2655", "XAUUSD", "long", "limit", 2635, 2628, [2645, 2655]),
    ("EURUSD buy 1,0850 sl 1,0820 tp 1,0900", "EURUSD", "long", "market", 1.085, 1.082, [1.09]),
    ("EURUSD sell 1,085 sl 1,088 tp 1,080", "EURUSD", "short", "market", 1.085, 1.088, [1.08]),
    ("USDJPY buy 151,20 sl 150,80 tp 152,00", "USDJPY", "long", "market", 151.2, 150.8, [152.0]),
    ("SPX500 sell 5,800.50 sl 5,820.00 tp 5,760.25", "SPX500", "short", "market", 5800.5, 5820, [5760.25]),
    ("DAX long 18 250 sl 18 200 tp 18 400", "GER40", "long", "market", 18250, 18200, [18400]),
    ("silver buy 31.20 sl 30.90 tp 31.80", "XAGUSD", "long", "market", 31.2, 30.9, [31.8]),
    ("XAG/USD sell 31.5 sl 31.8 tp 31", "XAGUSD", "short", "market", 31.5, 31.8, [31]),
    ("XAUUSD.pro sell 2660 sl 2670 tp 2650", "XAUUSD", "short", "market", 2660, 2670, [2650]),
    ("XAUUSD+ buy 2650 sl 2640 tp 2670", "XAUUSD", "long", "market", 2650, 2640, [2670]),
    ("Bitcoin long 67250 sl 66000 tp 70000", "BTCUSD", "long", "market", 67250, 66000, [70000]),
    ("BTC 🟢 67000 SL 66000 TP 69000", "BTCUSD", "long", "market", 67000, 66000, [69000]),
    ("USTEC buy 18500 sl 18450 tp 18600", "NAS100", "long", "market", 18500, 18450, [18600]),
    ("NQ long 18500 sl 18450 tp 18600", "NAS100", "long", "market", 18500, 18450, [18600]),
    ("GER30 sell 18300 sl 18350 tp 18200", "GER40", "short", "market", 18300, 18350, [18200]),
    ("WS30 buy 39000 sl 38900 tp 39200", "US30", "long", "market", 39000, 38900, [39200]),
    ("XAUUSD BUY 2,650.50 SL 2,640.00 TP 2,670.00", "XAUUSD", "long", "market", 2650.5, 2640, [2670]),
    (
        "XAUUSD buy 2650 sl 2640 tp 2660 R:R 1:3 20/10/2026 14:30 GMT",
        "XAUUSD",
        "long",
        "market",
        2650,
        2640,
        [2660],
    ),
    (
        "XAUUSD BUY 2650 SL 2640 (100 pips) TP1 2660 (+100 pips) TP2 2680",
        "XAUUSD",
        "long",
        "market",
        2650,
        2640,
        [2660, 2680],
    ),
    ("XAUUSD 2650 SL 2640 TP 2670", "XAUUSD", "long", "market", 2650, 2640, [2670]),  # direction from the stop
    ("XAUUSD 2648 - 2652 SL 2660 TP 2630", "XAUUSD", "short", "market", 2648, 2660, [2630]),
]


@pytest.mark.parametrize("text, symbol, direction, order_type, entry, stop, targets", CORPUS)
def test_corpus(text, symbol, direction, order_type, entry, stop, targets):
    result = parse(text)
    assert result.symbol == symbol
    assert result.direction == direction
    assert result.order_type == order_type
    assert result.entry == pytest.approx(entry)
    assert result.stop_loss == pytest.approx(stop)
    assert result.take_profits == pytest.approx(targets)
    assert result.warnings == []


def test_zone_uses_edge_farthest_from_stop_and_skips_open_target():
    result = parse("🟢 GOLD BUY 2648-2652\n🛑 SL: 2640\n🎯 TP1: 2660\n🎯 TP2: 2670\n🎯 TP3: open")
    assert (result.symbol, result.symbol_raw, result.direction) == ("XAUUSD", "GOLD", "long")
    assert (result.entry_low, result.entry_high, result.entry) == (2648, 2652, 2652)
    assert result.stop_loss == 2640
    assert result.take_profits == [2660, 2670]
    assert result.warnings == ["TP3 has no price (open / runner) - skipped; manage it manually."]


def test_short_zone_entry_is_the_low_edge():
    result = parse("GOLD SELL 2652—2648 SL 2660 TP 2630")  # em dash, edges in any order
    assert (result.direction, result.entry_low, result.entry_high, result.entry) == ("short", 2648, 2652, 2648)


def test_zone_without_stop_uses_midpoint():
    result = parse("XAUUSD BUY 2648 - 2652 TP 2670")
    assert result.entry == 2650
    assert result.stop_loss is None


def test_relative_stop_and_target_in_pips():
    result = parse("XAUUSD Buy  Entry zone: 2648 - 2652  SL: 30 pips  TP: 50 pips")
    assert (result.symbol, result.direction) == ("XAUUSD", "long")
    assert (result.entry_low, result.entry_high) == (2648, 2652)
    assert result.entry == 2652  # long with a relative stop: the worst (highest) fill
    assert result.stop_loss is None and result.stop_pips == 30
    assert result.take_profits == [] and result.target_pips == [50]


def test_small_numbers_next_to_a_price_are_read_as_pips():
    result = parse("Gold sell now 2650 SL 30 TP 50")
    assert (result.entry, result.stop_pips, result.target_pips) == (2650, 30, [50])
    assert any("looks like a distance" in w for w in result.warnings)


def test_missing_entry_is_left_blank():
    result = parse("BUY GOLD NOW SL 2640 TP 2660")
    assert (result.symbol, result.direction, result.order_type) == ("XAUUSD", "long", "market")
    assert result.entry is None
    assert (result.stop_loss, result.take_profits) == (2640, [2660])


def test_missing_stop_and_targets():
    result = parse("XAUUSD BUY 2650")
    assert (result.symbol, result.direction, result.entry) == ("XAUUSD", "long", 2650)
    assert result.stop_loss is None and result.stop_pips is None and result.take_profits == []


@pytest.mark.parametrize(
    "alias, symbol",
    [
        ("GOLD", "XAUUSD"),
        ("XAU", "XAUUSD"),
        ("SILVER", "XAGUSD"),
        ("XAG", "XAGUSD"),
        ("EUR/USD", "EURUSD"),
        ("DOW", "US30"),
        ("DJ30", "US30"),
        ("NASDAQ", "NAS100"),
        ("US100", "NAS100"),
        ("SP500", "SPX500"),
        ("SPX", "SPX500"),
        ("US500", "SPX500"),
        ("DE40", "GER40"),
        ("BTCUSDT", "BTCUSD"),
        ("#XAUUSDm", "XAUUSD"),
        ("EURUSD.pro", "EURUSD"),
    ],
)
def test_aliases_and_broker_suffixes(alias, symbol):
    assert parse(f"{alias} BUY").symbol == symbol


def test_configured_spelling_is_preferred():
    assert parse("XAUUSD BUY 2650 SL 2640 TP 2670", ["GOLD"]).symbol == "GOLD"
    assert parse("GOLD BUY 2650 SL 2640 TP 2670", ["XAUUSD.M", "EURUSD"]).symbol == "XAUUSD.M"


def test_unconfigured_and_unknown_symbols_warn():
    result = parse("GBPJPY buy 191.50 sl 191.00 tp 192.50")
    assert result.symbol == "GBPJPY"
    assert "GBPJPY is not one of your configured markets." in result.warnings
    unknown = parse("XYZABC BUY 100 SL 90 TP 120")
    assert unknown.symbol == "XYZABC"
    assert any("not a known instrument" in w for w in unknown.warnings)


def test_contradicting_direction_words_warn_and_use_first():
    result = parse("BUY SELL XAUUSD 2650 SL 2640 TP 2660")
    assert result.direction == "long"
    assert any("Both buy and sell" in w for w in result.warnings)


def test_stop_on_the_wrong_side_warns():
    result = parse("XAUUSD SELL 2650 SL 2640 TP 2630")
    assert result.direction == "short"
    assert any("stop loss (2640) is below the entry (2650)" in w for w in result.warnings)


def test_unordered_targets_are_resorted():
    result = parse("XAUUSD SELL 2650 SL 2660 TP1 2630 TP2 2640 TP3 2620")
    assert result.take_profits == [2640, 2630, 2620]
    assert "Take profits re-ordered nearest to farthest." in result.warnings


def test_targets_beyond_tp4_are_dropped_with_a_warning():
    result = parse("XAUUSD BUY 2650 SL 2640 TP 2655 2660 2665 2670 2680 2690")
    assert result.take_profits == [2655, 2660, 2665, 2670]
    assert any("kept TP1-TP4, dropped 2680, 2690" in w for w in result.warnings)


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "   \n ",
        "hello world, how are you?",
        "🚀🚀🚀 1234 !!!",
        "SL SL TP TP @@@",
        "\x00\x01 ;;;; 9999999999999999999999 1e308",
        "9" * 5000,
        "1," * 3000,
    ],
)
def test_garbage_never_raises(garbage):
    result = parse(garbage)
    assert result.symbol is None and result.direction is None and result.entry is None
    assert len(result.warnings) == 1
    assert result.to_dict()["take_profits"] == []


def test_huge_digit_runs_are_ignored():
    result = parse("XAUUSD buy " + "9" * 400 + " sl 2640 tp 2660")
    assert result.entry is None
    assert (result.stop_loss, result.take_profits) == (2640, [2660])


def test_to_dict_has_every_field():
    assert set(parse("XAUUSD BUY 2650 SL 2640 TP 2670").to_dict()) == {
        "symbol",
        "symbol_raw",
        "direction",
        "order_type",
        "entry",
        "entry_low",
        "entry_high",
        "stop_loss",
        "stop_pips",
        "take_profits",
        "target_pips",
        "warnings",
    }


def test_resolve_close_pcts():
    assert resolve_close_pcts([]) == []
    assert resolve_close_pcts([None, None, None]) == [50, 25, 25]
    assert resolve_close_pcts([60, None, None]) == [60, 20, 20]
    assert resolve_close_pcts([None, None, None, 10]) == [30, 30, 30, 10]
    with pytest.raises(ValueError, match="nothing is left for TP2"):
        resolve_close_pcts([100, None])


def test_split_lots_floors_to_step_and_last_takes_the_rest():
    assert split_lots(0.2, [50, 25, 25], 0.01) == [0.1, 0.05, 0.05]
    assert split_lots(0.15, [50, 25, 25], 0.01) == [0.07, 0.03, 0.05]
    assert split_lots(0.01, [50, 50], 0.01) == [0.0, 0.01]
