# Library Management System

A Python library management system backed by a MySQL database. Librarians can manage the book catalogue and members, issue, return and renew books, charge late fines automatically, and run reports, all from a simple command-line menu.

## Features

- **Books:** add, update, delete, add or remove copies, and search by title, author, genre or ISBN (optionally only books that are on the shelf).
- **Members:** register, search, and activate or deactivate memberships.
- **Loans:** issue and return books with copy-level availability tracking, renew loans, and view each member's borrowing history.
- **Lending rules** (configurable): 14-day loan period, at most 3 books per member, a fine per day late, no new loans while a member has an overdue book, no deactivating a member who still has books out.
- **Reports:** overdue loans with the fine owed so far, library stats, and most borrowed books.
- **Data integrity:** unique ISBNs and member emails, foreign keys between loans, books and members, and every multi-step change runs in a single database transaction.

## Tech stack

- Python 3.9+
- MySQL 8 / MariaDB via `mysql-connector-python` (SQLite is supported too, for running with zero setup)
- pytest for tests
- DBeaver or any SQL client to browse the data

## Database schema

| Table     | Key columns |
|-----------|-------------|
| `books`   | `id`, `isbn` (unique), `title`, `author`, `genre`, `published_year`, `total_copies`, `available_copies` |
| `members` | `id`, `name`, `email` (unique), `phone`, `joined_on`, `active` |
| `loans`   | `id`, `book_id` → books, `member_id` → members, `issued_on`, `due_on`, `returned_on`, `fine` |

Tables are created automatically the first time the app connects, and the database itself is created if it does not exist.

## Setup

```bash
git clone https://github.com/itsanushkah/librarymanagementsystem.git
cd librarymanagementsystem
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env   # then set your MySQL user and password
```

Make sure MySQL is running, then optionally load some sample books and members:

```bash
python seed.py
```

## Run

```bash
python project.py
```

To try it without MySQL, set `LMS_DB_BACKEND=sqlite` in `.env`; data is then stored in `library.db`.

## Configuration

All settings live in `.env` (see `.env.example`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `LMS_DB_BACKEND` | `mysql` | `mysql` or `sqlite` |
| `LMS_MYSQL_HOST` / `PORT` / `USER` / `PASSWORD` / `DATABASE` | `localhost` / `3306` / `root` / empty / `library` | MySQL connection |
| `LMS_SQLITE_PATH` | `library.db` | SQLite file |
| `LMS_LOAN_DAYS` | `14` | Loan period in days |
| `LMS_MAX_ACTIVE_LOANS` | `3` | Books a member can hold at once |
| `LMS_FINE_PER_DAY` | `5` | Fine per day late |

## Project structure

```
project.py        # entry point (interactive menu)
seed.py           # sample data
lms/config.py     # settings from environment / .env
lms/db.py         # MySQL + SQLite connection and schema
lms/services.py   # business rules: books, members, loans, reports
lms/cli.py        # command-line interface
tests/            # pytest suite
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest                                  # runs on an in-memory SQLite database
LMS_TEST_BACKEND=mysql LMS_MYSQL_DATABASE=library_test pytest   # runs against MySQL
```

## License

MIT License
