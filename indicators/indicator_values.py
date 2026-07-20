from pydantic import BaseModel


class IndicatorValues(BaseModel):
    ema50: float | None = None
    ema200: float | None = None
    rsi: float | None = None
    atr: float | None = None
    adx: float | None = None
    current_close: float | None = None
    recent_high: float | None = None
    recent_low: float | None = None
    current_volume: float | None = None
    average_volume: float | None = None