from pathlib import Path
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

APP_NAME = os.getenv("APP_NAME")
VERSION = os.getenv("VERSION")
SYMBOL = os.getenv("SYMBOL")
TIMEFRAME = os.getenv("TIMEFRAME")
DATABASE_URL = os.getenv("DATABASE_URL")

LOG_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"

LOG_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)