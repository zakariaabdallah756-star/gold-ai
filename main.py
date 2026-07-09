from core.logger import logger
from core.constants import APP_NAME, VERSION
from core.database import engine
from core.utils import generate_id
from core.events import Event
from data import candle
from data.data_provider import DataProvider
from data.candle import Candle
from datetime import datetime
from data.candle_repository import CandleRepository
from data.fake_data import get_fake_candle
from data.market_stream import MarketStream
from data.data_engine import DataEngine
from data.validator import CandleValidator
from indicators.sma import SMA
from indicators.ema import EMA
from indicators.rsi import RSI
from indicators.indicator_engine import IndicatorEngine
from strategy.signal import Signal, SignalType
from strategy.moving_average_strategy import MovingAverageStrategy
from strategy.strategy_engine import StrategyEngine
from risk.risk_model import RiskModel
from risk.position_sizer import PositionSizer
from risk.risk_engine import RiskEngine
from risk.trade_plan import TradePlan
def main():
    logger.info(f"{APP_NAME} v{VERSION} avviato.")
    print(f"{APP_NAME} v{VERSION} avviato correttamente.")
    # print(engine)
    print(generate_id())
    print(Event.NEW_CANDLE)
    provider = DataProvider()
    print(provider.get_server_time())
    candle = Candle(
        time=datetime.now(),
        open=3300.0,
        high=3310.0,
        low=3295.0,
        close=3305.0,
        volume=1200
    )
    print(candle)
    repository = CandleRepository()

    repository.add(candle)

    print(repository.last())
    fake_candle = get_fake_candle()

    print(fake_candle)
    stream = MarketStream()

    next_candle = stream.get_next_candle()

    print(next_candle)
    engine = DataEngine()

    latest = engine.update()

    print(latest)

    print(engine.repository.last())
    validator = CandleValidator()

    print(validator.validate(latest))
    sma = SMA()

    value = sma.calculate(engine.repository.get_all(), 1)

    print("SMA:", value)
    ema = EMA()

    ema_value = ema.calculate(engine.repository.get_all(), 1)

    print("EMA:", ema_value)
    rsi = RSI()

    rsi_value = rsi.calculate(engine.repository.get_all(), 1)

    print("RSI:", rsi_value)
    indicator_engine = IndicatorEngine()

    values = indicator_engine.calculate(engine.repository.get_all())

    print(values)
    signal = Signal(
        signal=SignalType.BUY,
        confidence=0.95
)

    print(signal)
    strategy = MovingAverageStrategy()

    strategy_signal = strategy.generate(values)

    print(strategy_signal)
    strategy_engine = StrategyEngine()

    final_signal = strategy_engine.generate_signal(values)

    print(final_signal)
    risk = RiskModel(
        risk_percent=1.0,
        stop_loss_pips=200,
        take_profit_pips=400,
    )

    print(risk)
    position_sizer = PositionSizer()

    lot = position_sizer.calculate(
        balance=10000,
        risk_percent=1,
        stop_loss_pips=200,
        pip_value=1,
    )

    print("LOT:", lot)
    risk_engine = RiskEngine()

    engine_lot = risk_engine.calculate_position_size(
        balance=10000,
        risk_percent=1,
        stop_loss_pips=200,
        pip_value=1,
    )

    print("ENGINE LOT:", engine_lot)
    trade_plan = TradePlan(
        signal=final_signal.signal,
        lot_size=engine_lot,
        stop_loss_pips=200,
        take_profit_pips=400,
    )

    print(trade_plan)
if __name__ == "__main__":
    main()