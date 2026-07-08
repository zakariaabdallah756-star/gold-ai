from core.database import engine
from sqlalchemy import text


def initialize_database():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        print("Database inizializzato correttamente.")


if __name__ == "__main__":
    initialize_database()