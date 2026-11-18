"""Database access layer supporting MySQL, PostgreSQL and SQLite.

All SQL in the app is written with ``?`` placeholders; for MySQL they are
translated to ``%s`` before execution on MySQL and PostgreSQL.
"""
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = {
    "sqlite": [
        """CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            isbn VARCHAR(20) NOT NULL UNIQUE,
            title VARCHAR(255) NOT NULL,
            author VARCHAR(255) NOT NULL,
            genre VARCHAR(100),
            published_year INTEGER,
            total_copies INTEGER NOT NULL DEFAULT 1 CHECK (total_copies >= 0),
            available_copies INTEGER NOT NULL DEFAULT 1 CHECK (available_copies >= 0),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(255) NOT NULL UNIQUE,
            phone VARCHAR(30),
            joined_on DATE NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )""",
        """CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id),
            member_id INTEGER NOT NULL REFERENCES members(id),
            issued_on DATE NOT NULL,
            due_on DATE NOT NULL,
            returned_on DATE,
            fine DECIMAL(10, 2) NOT NULL DEFAULT 0
        )""",
        "CREATE INDEX IF NOT EXISTS idx_loans_member ON loans(member_id)",
        "CREATE INDEX IF NOT EXISTS idx_loans_book ON loans(book_id)",
    ],
    "postgres": [
        """CREATE TABLE IF NOT EXISTS books (
            id SERIAL PRIMARY KEY,
            isbn VARCHAR(20) NOT NULL UNIQUE,
            title VARCHAR(255) NOT NULL,
            author VARCHAR(255) NOT NULL,
            genre VARCHAR(100),
            published_year INTEGER,
            total_copies INTEGER NOT NULL DEFAULT 1 CHECK (total_copies >= 0),
            available_copies INTEGER NOT NULL DEFAULT 1 CHECK (available_copies >= 0),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS members (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(255) NOT NULL UNIQUE,
            phone VARCHAR(30),
            joined_on DATE NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )""",
        """CREATE TABLE IF NOT EXISTS loans (
            id SERIAL PRIMARY KEY,
            book_id INTEGER NOT NULL REFERENCES books(id),
            member_id INTEGER NOT NULL REFERENCES members(id),
            issued_on DATE NOT NULL,
            due_on DATE NOT NULL,
            returned_on DATE,
            fine NUMERIC(10, 2) NOT NULL DEFAULT 0
        )""",
        "CREATE INDEX IF NOT EXISTS idx_loans_member ON loans(member_id)",
        "CREATE INDEX IF NOT EXISTS idx_loans_book ON loans(book_id)",
        "CREATE INDEX IF NOT EXISTS idx_books_title ON books(title)",
    ],
    "mysql": [
        """CREATE TABLE IF NOT EXISTS books (
            id INT AUTO_INCREMENT PRIMARY KEY,
            isbn VARCHAR(20) NOT NULL UNIQUE,
            title VARCHAR(255) NOT NULL,
            author VARCHAR(255) NOT NULL,
            genre VARCHAR(100),
            published_year INT,
            total_copies INT NOT NULL DEFAULT 1 CHECK (total_copies >= 0),
            available_copies INT NOT NULL DEFAULT 1 CHECK (available_copies >= 0),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_books_title (title),
            INDEX idx_books_author (author)
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS members (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(255) NOT NULL UNIQUE,
            phone VARCHAR(30),
            joined_on DATE NOT NULL,
            active TINYINT(1) NOT NULL DEFAULT 1
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS loans (
            id INT AUTO_INCREMENT PRIMARY KEY,
            book_id INT NOT NULL,
            member_id INT NOT NULL,
            issued_on DATE NOT NULL,
            due_on DATE NOT NULL,
            returned_on DATE NULL,
            fine DECIMAL(10, 2) NOT NULL DEFAULT 0,
            FOREIGN KEY (book_id) REFERENCES books(id),
            FOREIGN KEY (member_id) REFERENCES members(id),
            INDEX idx_loans_member (member_id),
            INDEX idx_loans_book (book_id)
        ) ENGINE=InnoDB""",
    ],
}


_ID = {"sqlite": "INTEGER PRIMARY KEY AUTOINCREMENT",
       "postgres": "SERIAL PRIMARY KEY",
       "mysql": "INT AUTO_INCREMENT PRIMARY KEY"}

# Schema upgrades, applied in order on startup. Version 1 is SCHEMA above.
# Never edit a released migration; add a new one instead.
MIGRATIONS = {
    2: lambda backend: [
        "ALTER TABLE loans ADD COLUMN fine_paid INTEGER NOT NULL DEFAULT 0",
        f"""CREATE TABLE reservations (
            id {_ID[backend]},
            book_id INTEGER NOT NULL REFERENCES books(id),
            member_id INTEGER NOT NULL REFERENCES members(id),
            reserved_on DATE NOT NULL,
            ready_on DATE NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'waiting'
        )""",
        "CREATE INDEX idx_reservations_book ON reservations(book_id, status)",
    ],
}
LATEST_VERSION = max(MIGRATIONS)


class IntegrityError(Exception):
    """Raised for unique/foreign key violations on either backend."""


def _retry(connect, attempts=5, delay=2.0):
    """Hosted databases can take a few seconds to wake up; retry briefly."""
    for attempt in range(1, attempts + 1):
        try:
            return connect()
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(delay * attempt)


class Database:
    def __init__(self, backend=None, sqlite_path=None, mysql_settings=None,
                 database_url=None):
        database_url = database_url or (config.DATABASE_URL if backend is None else None)
        if database_url and database_url.startswith(("postgres://", "postgresql://")):
            backend = "postgres"
        self.backend = (backend or config.DB_BACKEND).lower()
        if self.backend == "postgres":
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("psycopg is not installed. "
                                   "Run: pip install -r requirements.txt") from exc
            self.conn = _retry(lambda: psycopg.connect(
                database_url or config.DATABASE_URL, row_factory=dict_row,
                connect_timeout=10))
            self._integrity_errors = (psycopg.IntegrityError,)
        elif self.backend == "sqlite":
            self.conn = sqlite3.connect(sqlite_path or config.SQLITE_PATH)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            self._integrity_errors = (sqlite3.IntegrityError,)
        elif self.backend == "mysql":
            try:
                import mysql.connector
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "mysql-connector-python is not installed. "
                    "Run: pip install -r requirements.txt"
                ) from exc
            settings = dict(mysql_settings or config.MYSQL)
            database = settings.pop("database")
            server = _retry(lambda: mysql.connector.connect(**settings))
            cur = server.cursor()
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
            cur.close()
            server.close()
            self.conn = mysql.connector.connect(database=database, **settings)
            self._integrity_errors = (mysql.connector.IntegrityError,)
        else:
            raise ValueError(f"Unknown database backend: {self.backend}")
        self.init_schema()

    # -- helpers ---------------------------------------------------------
    def _sql(self, query):
        return query if self.backend == "sqlite" else query.replace("?", "%s")

    def _cursor(self):
        if self.backend == "mysql":
            return self.conn.cursor(dictionary=True)
        if self.backend == "postgres":
            return self.conn.cursor()
        return self.conn.cursor()

    def init_schema(self):
        """Create the base tables, then apply any pending migrations."""
        cur = self.conn.cursor()
        for statement in SCHEMA[self.backend]:
            cur.execute(statement)
        cur.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        cur.execute("SELECT MAX(version) FROM schema_version")
        row = cur.fetchone()
        current = (row[0] if not isinstance(row, dict) else list(row.values())[0]) or 1
        self.conn.commit()
        for version in sorted(v for v in MIGRATIONS if v > current):
            try:
                for statement in MIGRATIONS[version](self.backend):
                    cur.execute(statement)
                cur.execute(self._sql("INSERT INTO schema_version (version) VALUES (?)"),
                            (version,))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                # Another process may have just applied it; re-check before failing.
                cur.execute("SELECT MAX(version) FROM schema_version")
                row = cur.fetchone()
                done = row[0] if not isinstance(row, dict) else list(row.values())[0]
                self.conn.commit()
                if not done or done < version:
                    raise
        cur.close()
        self.schema_version = LATEST_VERSION

    def query(self, sql, params=()):
        cur = self._cursor()
        cur.execute(self._sql(sql), params)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=()):
        """Run a write statement and return the last inserted id."""
        cur = self._cursor()
        returning = self.backend == "postgres" and sql.lstrip().upper().startswith("INSERT")
        if returning:
            sql += " RETURNING id"
        try:
            cur.execute(self._sql(sql), params)
        except self._integrity_errors as exc:
            raise IntegrityError(str(exc)) from exc
        last_id = cur.fetchone()["id"] if returning else getattr(cur, "lastrowid", None)
        cur.close()
        return last_id

    @contextmanager
    def transaction(self):
        """Commit on success, roll back on any error."""
        try:
            yield self
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()
