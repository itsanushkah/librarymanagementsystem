# Shelfwise: Library Management System

[![Tests](https://github.com/itsanushkah/librarymanagementsystem/actions/workflows/tests.yml/badge.svg)](https://github.com/itsanushkah/librarymanagementsystem/actions/workflows/tests.yml)

A web app for running a small library. Librarians manage the catalogue and members, issue and return books, and see at a glance what is overdue. Visitors can browse the public catalogue. It is built with Python and Flask and stores its data in a relational database (MySQL, PostgreSQL or SQLite).

![Dashboard](docs/screenshots/dashboard.png)

| Catalogue | Book page with a waiting list |
|---|---|
| ![Catalogue](docs/screenshots/catalogue.png) | ![Book](docs/screenshots/book.png) |
| **Member page** | **Loans** |
| ![Member](docs/screenshots/member.png) | ![Loans](docs/screenshots/loans.png) |

## Features

- **Public catalogue** with search by title, author, genre or ISBN, a genre filter, an "on the shelf now" filter, and cover images from Open Library.
- **Librarian dashboard** with live stats, books ready for pickup, overdue and due-soon loans, recent activity and the most borrowed books.
- **Books:** add (type an ISBN and the details fill in automatically from Open Library), edit, add or remove copies, delete, and see each book's loan history.
- **Members:** register, edit, search, activate or deactivate, and see each member's current books, reservations, history and fines.
- **Loans:** issue, return and renew in one click. Late fines are calculated automatically.
- **Reservations:** when every copy is out, members join a waiting list. The next copy returned is held for the first person in line for 3 days, then passes to the next person. Books with a waiting list can't be renewed.
- **Fines:** a fines page shows what's owed. Mark fines as paid, and members who owe too much can't borrow until they pay.
- **CSV export** of books, members and loans.
- **Lending rules** (configurable): 14-day loans, at most 3 books and 3 reservations per member, a fine per day late, no new loans while a book is overdue or fines are unpaid.
- **Security:** librarian sign-in with lockout after repeated wrong passwords, CSRF protection on every form, secure cookies and security headers in production, safe redirects, and sign-in refuses to work in production with a weak password.
- **Reliable hosting:** database upgrades run automatically on start, the app retries while a hosted database wakes up, and there are friendly error pages and a health check.
- **Data integrity:** foreign keys, unique ISBNs and emails, and transactions around every multi-step change.
- **Works on phones** as well as desktops, with light and dark mode.
- A **command-line interface** (`python project.py`) is included as well.

## Tech stack

| Part | Choice |
|---|---|
| Backend | Python 3, Flask, Gunicorn |
| Database | PostgreSQL (production on Render), MySQL / MariaDB, or SQLite (zero setup) |
| Frontend | Server-rendered Jinja templates, hand-written CSS, a little vanilla JS |
| Tests | pytest (35 tests), run on every push by GitHub Actions against SQLite, PostgreSQL and MySQL |
| Hosting | Render (Blueprint in `render.yaml`) |

## Database schema

```
books          (id, isbn UNIQUE, title, author, genre, published_year, total_copies, available_copies, created_at)
members        (id, name, email UNIQUE, phone, joined_on, active)
loans          (id, book_id → books, member_id → members, issued_on, due_on, returned_on, fine, fine_paid)
reservations   (id, book_id → books, member_id → members, reserved_on, ready_on, status)
schema_version (version)
```

Tables are created automatically on first start. Later schema changes are numbered migrations in `lms/db.py` and are applied automatically, so an existing database is upgraded in place.

## Run it locally

```bash
git clone https://github.com/itsanushkah/librarymanagementsystem.git
cd librarymanagementsystem
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

- **Quickest:** set `LMS_DB_BACKEND=sqlite`. No database server needed.
- **MySQL:** keep `LMS_DB_BACKEND=mysql` and fill in your MySQL user and password. The `library` database is created for you.
- **PostgreSQL:** set `DATABASE_URL=postgresql://user:password@localhost/library`.

Then load sample data and start the app:

```bash
python seed.py
python app.py
```

Open http://localhost:5000 and sign in with the `LMS_ADMIN_USERNAME` / `LMS_ADMIN_PASSWORD` from your `.env`.

## Deploy on Render

1. Push this repo to GitHub.
2. In Render, choose **New → Blueprint** and pick this repository. Render reads `render.yaml` and creates a free PostgreSQL database and the web service.
3. When asked, enter a value for `LMS_ADMIN_PASSWORD`. This is the librarian password. Pick a strong one: in production, sign-in stays switched off if the password is missing or something like `admin`.
4. Wait for the deploy, then open the `.onrender.com` link. Sample books, members and loans are loaded on first start (set `LMS_SEED_DEMO_DATA=0` to start empty).

Notes about Render's free plan (as of late 2026): the web service sleeps after 15 minutes without visitors, so the first visit afterwards takes about a minute. Free databases expire after 30 days unless you upgrade.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | none | A `postgres://` URL. If set, PostgreSQL is used. |
| `LMS_DB_BACKEND` | `mysql` | `mysql`, `postgres` or `sqlite` |
| `LMS_MYSQL_HOST` / `PORT` / `USER` / `PASSWORD` / `DATABASE` | `localhost` / `3306` / `root` / empty / `library` | MySQL connection |
| `LMS_SQLITE_PATH` | `library.db` | SQLite file |
| `SECRET_KEY` | none | Signs session cookies. Set a long random value in production (Render generates one). |
| `LMS_ADMIN_USERNAME` / `LMS_ADMIN_PASSWORD` | `admin` / `admin` | Librarian sign-in. Change the password! |
| `LMS_SEED_DEMO_DATA` | `0` | `1` loads sample data when the library is empty |
| `LMS_LOAN_DAYS` | `14` | Loan period in days |
| `LMS_MAX_ACTIVE_LOANS` | `3` | Books a member can hold at once |
| `LMS_FINE_PER_DAY` | `5` | Fine per day late |
| `LMS_MAX_UNPAID_FINES` | `50` | Members owing more than this can't borrow |
| `LMS_MAX_RESERVATIONS` | `3` | Reservations a member can have at once |
| `LMS_HOLD_DAYS` | `3` | Days a returned copy is held for the person who reserved it |
| `LMS_LOGIN_MAX_ATTEMPTS` / `LMS_LOGIN_LOCK_MINUTES` | `5` / `10` | Sign-in lockout after wrong passwords |
| `LMS_PAGE_SIZE` | `24` | Books per catalogue page |
| `LMS_ENV` | none | Set to `production` to turn on production safety settings outside Render (Render is detected automatically) |

## Project structure

```
app.py              web entry point (gunicorn app:app)
project.py          command-line interface
seed.py             loads sample data
render.yaml         Render deployment blueprint
lms/config.py       settings from environment / .env
lms/db.py           database layer for MySQL, PostgreSQL and SQLite
lms/services.py     business rules: books, members, loans, fines, reports
lms/web.py          Flask routes, sign-in, CSRF
lms/demo.py         sample data
lms/templates/      HTML pages
lms/static/         stylesheet
tests/              pytest suite
.github/workflows/  GitHub Actions test run
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest                                                    # in-memory SQLite
LMS_TEST_BACKEND=mysql LMS_MYSQL_DATABASE=library_test pytest
LMS_TEST_BACKEND=postgres LMS_TEST_DATABASE_URL=postgresql://user:pass@localhost/library_test pytest
```

## License

MIT License
