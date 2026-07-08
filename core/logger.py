from loguru import logger

logger.add(
    "logs/goldai.log",
    rotation="10 MB",
    retention="30 days",
    level="INFO"
)