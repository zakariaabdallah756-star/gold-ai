from pydantic import BaseModel


class IndicatorValues(BaseModel):
    ema50: float | None = None
    ema200: float | None = None
    rsi: float | None = None
    atr: float | None = None
    adx: float | None = None