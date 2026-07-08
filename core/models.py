from pydantic import BaseModel
from datetime import datetime


class BaseEntity(BaseModel):
    created_at: datetime = datetime.now()

    class Config:
        from_attributes = True