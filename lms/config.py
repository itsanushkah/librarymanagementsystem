"""Configuration, read from environment variables (or a .env file)."""
import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so no extra dependency is needed."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# "mysql" (default, as described in the README) or "sqlite" (zero setup).
DB_BACKEND = os.getenv("LMS_DB_BACKEND", "mysql").lower()

MYSQL = {
    "host": os.getenv("LMS_MYSQL_HOST", "localhost"),
    "port": int(os.getenv("LMS_MYSQL_PORT", "3306")),
    "user": os.getenv("LMS_MYSQL_USER", "root"),
    "password": os.getenv("LMS_MYSQL_PASSWORD", ""),
    "database": os.getenv("LMS_MYSQL_DATABASE", "library"),
}

SQLITE_PATH = os.getenv("LMS_SQLITE_PATH", "library.db")

# Lending rules
LOAN_DAYS = int(os.getenv("LMS_LOAN_DAYS", "14"))
MAX_ACTIVE_LOANS = int(os.getenv("LMS_MAX_ACTIVE_LOANS", "3"))
FINE_PER_DAY = float(os.getenv("LMS_FINE_PER_DAY", "5"))
