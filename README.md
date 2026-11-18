# Shelfwise: Library Management System

A web app for running a small library. Librarians manage the catalogue and members, issue and return books, and see at a glance what is overdue. Visitors can browse the public catalogue. It is built with Python and Flask and stores its data in a relational database (MySQL, PostgreSQL or SQLite).

![Dashboard](docs/screenshots/dashboard.png)

| Catalogue | Book page |
|---|---|
| ![Catalogue](docs/screenshots/catalogue.png) | ![Book](docs/screenshots/book.png) |

## Features

- **Public catalogue** with search by title, author, genre or ISBN, a genre filter, an "on the shelf now" filter, and cover images from Open Library.
- **Librarian dashboard** with live stats, overdue and due-soon loans, recent activity and the most borrowed books.
- **Books:** add (type an ISBN and the details fill in automatically from Open Library), edit, add or remove copies, delete, and see each book's loan history.
- **Members:** register, search, activate or deactivate, and see each member's current books, history and fines.
- **Loans:** issue, return and renew in one click. Late fines are calculated automatically.
- **Lending rules** (configurable): 14-day loans, at most 3 books per member, a fine per day late, no new loans while a book is overdue, no renewing overdue loans.
- **Security:** librarian sign-in, CSRF protection on every form, and safe redirects.
- **Data integrity:** foreign keys, unique ISBNs and emails, and transactions around every multi-step change.
- **Works on phones** as well as desktops, with light and dark mode.
- A **command-line interface** (`python project.py`) is included as well.

## Tech stack

| Part | Choice |
|---|---|
| Backend | Python 3, Flask, Gunicorn |
| Database | PostgreSQL (production on Render), MySQL / MariaDB, or SQLite (zero setup) |
| Frontend | Server-rendered Jinja templates, hand-written CSS, a little vanilla JS |
| Tests | pytest (22 tests, run against all three databases) |
| Hosting | Render (Blueprint in `render.yaml`) |

## Database schema

```
books    (id, isbn UNIQUE, title, author, genre, published_year, total_copies, available_copies, created_at)
members  (id, name, email UNIQUE, phone, joined_on, active)
loans    (id, book_id → books, member_id → members, issued_on, due_on, returned_on, fine)
```

Tables are created automatically on first start.

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
3. When asked, enter a value for `LMS_ADMIN_PASSWORD`. This is the librarian password.
4. Wait for the deploy, then open the `.onrender.com` link. Sample books, members and loans are loaded on first start (set `LMS_SEED_DEMO_DATA=0` to start empty).

Notes about Render's free plan (as of late 2026): the web service sleeps after 15 minutes without visitors, so the first visit afterwards takes about a minute. Free databases expire after 30 days unless you upgrade.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | none | A `postgres://` URL. If set, PostgreSQL is used. |
| `LMS_DB_BACKEND` | `mysql` | `mysql`, `postgres` or `sqlite` |
| `LMS_MYSQL_HOST` / `PORT` / `USER` / `PASSWORD` / `DATABASE` | `localhost` / `3306` / `root` / empty / `library` | MySQL connection |
| `LMS_SQLITE_PATH` | `library.db` | SQLite file |
| `SECRET_KEY` | dev value | Signs session cookies. Set a long random value in production. |
| `LMS_ADMIN_USERNAME` / `LMS_ADMIN_PASSWORD` | `admin` / `admin` | Librarian sign-in. Change the password! |
| `LMS_SEED_DEMO_DATA` | `0` | `1` loads sample data when the library is empty |
| `LMS_LOAN_DAYS` | `14` | Loan period in days |
| `LMS_MAX_ACTIVE_LOANS` | `3` | Books a member can hold at once |
| `LMS_FINE_PER_DAY` | `5` | Fine per day late |

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
```

## Tests

```bash
pip install pytest
pytest                                                    # in-memory SQLite
LMS_TEST_BACKEND=mysql LMS_MYSQL_DATABASE=library_test pytest
LMS_TEST_BACKEND=postgres LMS_TEST_DATABASE_URL=postgresql://user:pass@localhost/library_test pytest
```

## License

MIT License
