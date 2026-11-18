"""Database access layer supporting MySQL, PostgreSQL and SQLite.

All SQL in the app is written with ``?`` placeholders; for MySQL they are
translated to ``%s`` before execution on MySQL and PostgreSQL.
"""
import sqlite3
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


class IntegrityError(Exception):
    """Raised for unique/foreign key violations on either backend."""


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
            self.conn = psycopg.connect(database_url or config.DATABASE_URL,
                                        row_factory=dict_row)
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
            server = mysql.connector.connect(**settings)
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
        cur = self.conn.cursor()
        for statement in SCHEMA[self.backend]:
            cur.execute(statement)
        self.conn.commit()
        cur.close()

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
