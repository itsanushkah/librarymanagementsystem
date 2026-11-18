"""Business logic: books, members, loans, search and reports."""
from datetime import date, timedelta
from decimal import Decimal

from . import config
from .db import Database, IntegrityError


class LibraryError(Exception):
    """A rule was broken (book unavailable, unknown member, ...)."""


def _to_date(value):
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _normalize_loan(row):
    for key in ("issued_on", "due_on", "returned_on"):
        if key in row:
            row[key] = _to_date(row[key])
    if "fine" in row:
        row["fine"] = Decimal(str(row["fine"])).quantize(Decimal("0.01"))
    return row


class Library:
    def __init__(self, db: Database, *, loan_days=None, max_active_loans=None,
                 fine_per_day=None):
        self.db = db
        self.loan_days = loan_days if loan_days is not None else config.LOAN_DAYS
        self.max_active_loans = (max_active_loans if max_active_loans is not None
                                 else config.MAX_ACTIVE_LOANS)
        self.fine_per_day = Decimal(str(
            fine_per_day if fine_per_day is not None else config.FINE_PER_DAY))

    # -- books -----------------------------------------------------------
    def add_book(self, isbn, title, author, genre=None, published_year=None,
                 copies=1):
        if not isbn or not title or not author:
            raise LibraryError("ISBN, title and author are required.")
        if copies < 1:
            raise LibraryError("A book needs at least one copy.")
        existing = self.db.query_one("SELECT id FROM books WHERE isbn = ?", (isbn,))
        if existing:
            raise LibraryError(f"A book with ISBN {isbn} already exists "
                               f"(id {existing['id']}). Use add copies instead.")
        with self.db.transaction():
            return self.db.execute(
                "INSERT INTO books (isbn, title, author, genre, published_year, "
                "total_copies, available_copies) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (isbn, title, author, genre, published_year, copies, copies),
            )

    def get_book(self, book_id):
        book = self.db.query_one("SELECT * FROM books WHERE id = ?", (book_id,))
        if not book:
            raise LibraryError(f"No book with id {book_id}.")
        return book

    def update_book(self, book_id, **fields):
        allowed = {"isbn", "title", "author", "genre", "published_year"}
        unknown = set(fields) - allowed
        if unknown:
            raise LibraryError(f"Cannot update: {', '.join(sorted(unknown))}")
        fields = {k: v for k, v in fields.items() if v not in (None, "")}
        self.get_book(book_id)
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        try:
            with self.db.transaction():
                self.db.execute(f"UPDATE books SET {assignments} WHERE id = ?",
                                (*fields.values(), book_id))
        except IntegrityError as exc:
            raise LibraryError("Another book already uses that ISBN.") from exc

    def change_copies(self, book_id, delta):
        """Add (positive) or remove (negative) copies of a book."""
        book = self.get_book(book_id)
        if book["available_copies"] + delta < 0:
            raise LibraryError("Cannot remove copies that are currently on loan.")
        with self.db.transaction():
            self.db.execute(
                "UPDATE books SET total_copies = total_copies + ?, "
                "available_copies = available_copies + ? WHERE id = ?",
                (delta, delta, book_id),
            )

    def delete_book(self, book_id):
        book = self.get_book(book_id)
        if book["available_copies"] < book["total_copies"]:
            raise LibraryError("This book has copies on loan; return them first.")
        has_history = self.db.query_one(
            "SELECT COUNT(*) AS n FROM loans WHERE book_id = ?", (book_id,))["n"]
        if has_history:
            raise LibraryError("This book has loan history; set its copies to 0 "
                               "instead of deleting it.")
        with self.db.transaction():
            self.db.execute("DELETE FROM books WHERE id = ?", (book_id,))

    def search_books(self, term="", available_only=False):
        like = f"%{term.lower()}%"
        sql = ("SELECT * FROM books WHERE (LOWER(title) LIKE ? OR LOWER(author) LIKE ? "
               "OR LOWER(COALESCE(genre, '')) LIKE ? OR isbn LIKE ?)")
        if available_only:
            sql += " AND available_copies > 0"
        return self.db.query(sql + " ORDER BY title", (like, like, like, like))

    # -- members ---------------------------------------------------------
    def add_member(self, name, email, phone=None, joined_on=None):
        if not name or not email or "@" not in email:
            raise LibraryError("A name and a valid email are required.")
        try:
            with self.db.transaction():
                return self.db.execute(
                    "INSERT INTO members (name, email, phone, joined_on) "
                    "VALUES (?, ?, ?, ?)",
                    (name, email.lower(), phone,
                     (joined_on or date.today()).isoformat()),
                )
        except IntegrityError as exc:
            raise LibraryError(f"A member with email {email} already exists.") from exc

    def get_member(self, member_id):
        member = self.db.query_one("SELECT * FROM members WHERE id = ?", (member_id,))
        if not member:
            raise LibraryError(f"No member with id {member_id}.")
        member["joined_on"] = _to_date(member["joined_on"])
        return member

    def search_members(self, term=""):
        like = f"%{term.lower()}%"
        return self.db.query(
            "SELECT * FROM members WHERE LOWER(name) LIKE ? OR LOWER(email) LIKE ? "
            "ORDER BY name", (like, like))

    def set_member_active(self, member_id, active):
        self.get_member(member_id)
        if not active and self.active_loans(member_id):
            raise LibraryError("Member still has books out; return them first.")
        with self.db.transaction():
            self.db.execute("UPDATE members SET active = ? WHERE id = ?",
                            (1 if active else 0, member_id))

    # -- loans -----------------------------------------------------------
    def active_loans(self, member_id):
        return [_normalize_loan(r) for r in self.db.query(
            "SELECT l.*, b.title FROM loans l JOIN books b ON b.id = l.book_id "
            "WHERE l.member_id = ? AND l.returned_on IS NULL ORDER BY l.due_on",
            (member_id,))]

    def issue_book(self, book_id, member_id, today=None):
        today = today or date.today()
        book = self.get_book(book_id)
        member = self.get_member(member_id)
        if not member["active"]:
            raise LibraryError(f"{member['name']}'s membership is inactive.")
        loans = self.active_loans(member_id)
        if any(l["due_on"] < today for l in loans):
            raise LibraryError(f"{member['name']} has overdue books; "
                               "they must be returned first.")
        if len(loans) >= self.max_active_loans:
            raise LibraryError(f"{member['name']} already has "
                               f"{self.max_active_loans} books out.")
        if any(l["book_id"] == book_id for l in loans):
            raise LibraryError(f"{member['name']} already has a copy of this book.")
        if book["available_copies"] < 1:
            raise LibraryError(f"No copies of '{book['title']}' are available.")
        due = today + timedelta(days=self.loan_days)
        with self.db.transaction():
            self.db.execute(
                "UPDATE books SET available_copies = available_copies - 1 "
                "WHERE id = ? AND available_copies > 0", (book_id,))
            loan_id = self.db.execute(
                "INSERT INTO loans (book_id, member_id, issued_on, due_on) "
                "VALUES (?, ?, ?, ?)",
                (book_id, member_id, today.isoformat(), due.isoformat()))
        return loan_id

    def get_loan(self, loan_id):
        loan = self.db.query_one(
            "SELECT l.*, b.title, m.name AS member_name FROM loans l "
            "JOIN books b ON b.id = l.book_id JOIN members m ON m.id = l.member_id "
            "WHERE l.id = ?", (loan_id,))
        if not loan:
            raise LibraryError(f"No loan with id {loan_id}.")
        return _normalize_loan(loan)

    def calculate_fine(self, due_on, returned_on):
        days_late = (returned_on - due_on).days
        return (self.fine_per_day * max(days_late, 0)).quantize(Decimal("0.01"))

    def return_book(self, loan_id, today=None):
        today = today or date.today()
        loan = self.get_loan(loan_id)
        if loan["returned_on"]:
            raise LibraryError(f"Loan {loan_id} was already returned on "
                               f"{loan['returned_on']}.")
        fine = self.calculate_fine(loan["due_on"], today)
        with self.db.transaction():
            self.db.execute(
                "UPDATE loans SET returned_on = ?, fine = ? WHERE id = ?",
                (today.isoformat(), str(fine), loan_id))
            self.db.execute(
                "UPDATE books SET available_copies = available_copies + 1 "
                "WHERE id = ?", (loan["book_id"],))
        return fine

    def renew_loan(self, loan_id, today=None):
        today = today or date.today()
        loan = self.get_loan(loan_id)
        if loan["returned_on"]:
            raise LibraryError("Cannot renew a returned loan.")
        if loan["due_on"] < today:
            raise LibraryError("Overdue loans cannot be renewed; return the book.")
        new_due = loan["due_on"] + timedelta(days=self.loan_days)
        with self.db.transaction():
            self.db.execute("UPDATE loans SET due_on = ? WHERE id = ?",
                            (new_due.isoformat(), loan_id))
        return new_due

    def member_history(self, member_id):
        self.get_member(member_id)
        return [_normalize_loan(r) for r in self.db.query(
            "SELECT l.*, b.title FROM loans l JOIN books b ON b.id = l.book_id "
            "WHERE l.member_id = ? ORDER BY l.issued_on DESC, l.id DESC",
            (member_id,))]

    # -- reports ---------------------------------------------------------
    def overdue_loans(self, today=None):
        today = today or date.today()
        rows = [_normalize_loan(r) for r in self.db.query(
            "SELECT l.*, b.title, m.name AS member_name, m.email FROM loans l "
            "JOIN books b ON b.id = l.book_id JOIN members m ON m.id = l.member_id "
            "WHERE l.returned_on IS NULL AND l.due_on < ? ORDER BY l.due_on",
            (today.isoformat(),))]
        for r in rows:
            r["days_overdue"] = (today - r["due_on"]).days
            r["fine_so_far"] = self.calculate_fine(r["due_on"], today)
        return rows

    def stats(self):
        q = self.db.query_one
        return {
            "titles": int(q("SELECT COUNT(*) AS n FROM books")["n"]),
            "copies": int(q("SELECT COALESCE(SUM(total_copies), 0) AS n FROM books")["n"]),
            "available": int(q("SELECT COALESCE(SUM(available_copies), 0) AS n FROM books")["n"]),
            "members": int(q("SELECT COUNT(*) AS n FROM members WHERE active = 1")["n"]),
            "on_loan": int(q("SELECT COUNT(*) AS n FROM loans WHERE returned_on IS NULL")["n"]),
            "fines_collected": Decimal(str(q("SELECT COALESCE(SUM(fine), 0) AS n FROM loans")["n"])
                                       ).quantize(Decimal("0.01")),
        }

    def popular_books(self, limit=5):
        return self.db.query(
            "SELECT b.id, b.title, b.author, COUNT(l.id) AS times_borrowed "
            "FROM books b JOIN loans l ON l.book_id = b.id "
            "GROUP BY b.id, b.title, b.author ORDER BY times_borrowed DESC, b.title "
            "LIMIT ?", (limit,))

    # -- web helpers -----------------------------------------------------
    def list_loans(self, status="active", today=None, term=""):
        """status: active | overdue | returned | all"""
        today = today or date.today()
        sql = ("SELECT l.*, b.title, b.isbn, m.name AS member_name FROM loans l "
               "JOIN books b ON b.id = l.book_id JOIN members m ON m.id = l.member_id "
               "WHERE (LOWER(b.title) LIKE ? OR LOWER(m.name) LIKE ?)")
        like = f"%{term.lower()}%"
        params = [like, like]
        if status == "active":
            sql += " AND l.returned_on IS NULL"
        elif status == "overdue":
            sql += " AND l.returned_on IS NULL AND l.due_on < ?"
            params.append(today.isoformat())
        elif status == "returned":
            sql += " AND l.returned_on IS NOT NULL"
        sql += " ORDER BY l.returned_on IS NOT NULL, l.due_on, l.id DESC"
        rows = [_normalize_loan(r) for r in self.db.query(sql, params)]
        for r in rows:
            self._annotate(r, today)
        return rows

    def _annotate(self, loan, today):
        """Add a human status ("overdue", "due soon", "on loan", "returned")."""
        if loan["returned_on"]:
            loan["status"] = "returned"
        elif loan["due_on"] < today:
            loan["status"] = "overdue"
            loan["days_overdue"] = (today - loan["due_on"]).days
            loan["fine_so_far"] = self.calculate_fine(loan["due_on"], today)
        elif (loan["due_on"] - today).days <= 3:
            loan["status"] = "due soon"
        else:
            loan["status"] = "on loan"
        return loan

    def book_history(self, book_id, today=None):
        today = today or date.today()
        self.get_book(book_id)
        rows = [_normalize_loan(r) for r in self.db.query(
            "SELECT l.*, m.name AS member_name FROM loans l "
            "JOIN members m ON m.id = l.member_id WHERE l.book_id = ? "
            "ORDER BY l.issued_on DESC, l.id DESC", (book_id,))]
        return [self._annotate(r, today) for r in rows]

    def member_loans(self, member_id, today=None):
        today = today or date.today()
        return [self._annotate(r, today) for r in self.member_history(member_id)]

    def due_soon(self, today=None, days=3):
        today = today or date.today()
        return [r for r in self.list_loans("active", today=today)
                if r["status"] == "due soon"]

    def genres(self):
        return [r["genre"] for r in self.db.query(
            "SELECT DISTINCT genre FROM books WHERE genre IS NOT NULL AND genre <> '' "
            "ORDER BY genre")]

    def books_by_genre(self, genre):
        return self.db.query("SELECT * FROM books WHERE genre = ? ORDER BY title", (genre,))
