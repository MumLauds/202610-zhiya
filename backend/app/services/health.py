from app.core.config import database_path
from app.db.connection import database_ready


def is_ready() -> bool:
    return database_ready(database_path())
