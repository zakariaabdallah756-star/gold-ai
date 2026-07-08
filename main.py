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
def main():
    logger.info(f"{APP_NAME} v{VERSION} avviato.")
    print(f"{APP_NAME} v{VERSION} avviato correttamente.")
    print(engine)
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
if __name__ == "__main__":
    main()