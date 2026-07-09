from pydantic import BaseModel


class PaperAccount(BaseModel):
    balance: float = 10000.0
    equity: float = 10000.0
    margin: float = 0.0
    free_margin: float = 10000.0