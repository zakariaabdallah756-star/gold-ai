from datetime import datetime
import uuid


def now() -> datetime:
    return datetime.now()


def generate_id() -> str:
    return str(uuid.uuid4())