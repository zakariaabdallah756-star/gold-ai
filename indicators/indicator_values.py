from pydantic import BaseModel


class IndicatorValues(BaseModel):
    sma: float | None
    ema: float | None
    rsi: float | None