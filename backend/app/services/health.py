from app.core.config import database_path
from app.db.connection import database_ready
from app.services.reading_reports import ensure_startup_recovered


def is_ready() -> bool:
    return database_ready(database_path()) and ensure_startup_recovered()
