class GoldAIError(Exception):
    """Eccezione base di GoldAI."""
    pass


class ConfigurationError(GoldAIError):
    pass


class DataError(GoldAIError):
    pass


class StrategyError(GoldAIError):
    pass


class ExecutionError(GoldAIError):
    pass