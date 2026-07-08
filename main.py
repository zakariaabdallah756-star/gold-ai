from core.logger import logger
from core.constants import APP_NAME, VERSION
from core.database import engine
from core.utils import generate_id
from core.events import Event
from data.data_provider import DataProvider
def main():
    logger.info(f"{APP_NAME} v{VERSION} avviato.")
    print(f"{APP_NAME} v{VERSION} avviato correttamente.")
    print(engine)
    print(generate_id())
    print(Event.NEW_CANDLE)
    provider = DataProvider()
    print(provider.get_server_time()) 
if __name__ == "__main__":
    main()