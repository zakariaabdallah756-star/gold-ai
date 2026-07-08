from core.logger import logger
from core.constants import APP_NAME, VERSION
from core.database import engine

def main():
    logger.info(f"{APP_NAME} v{VERSION} avviato.")
    print(f"{APP_NAME} v{VERSION} avviato correttamente.")
    print(engine)

if __name__ == "__main__":
    main()