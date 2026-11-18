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
    for key in ("issued_on", "due_on", "returned_on", "reserved_on", "ready_on"):
        if key in row:
            row[key] = _to_date(row[key])
    if "fine" in row:
        row["fine"] = Decimal(str(row["fine"])).quantize(Decimal("0.01"))
    if "fine_paid" in row:
        row["fine_paid"] = bool(row["fine_paid"])
    return row


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


class Library:
    def __init__(self, db: Database, *, loan_days=None, max_active_loans=None,
                 fine_per_day=None, max_unpaid_fines=None, max_reservations=None,
                 hold_days=None):
        self.db = db
        self.max_unpaid_fines = _money(max_unpaid_fines if max_unpaid_fines is not None
                                       else config.MAX_UNPAID_FINES)
        self.max_reservations = (max_reservations if max_reservations is not None
                                 else config.MAX_RESERVATIONS)
        self.hold_days = hold_days if hold_days is not None else config.HOLD_DAYS
        self.promoted_holds = []
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
        self.promoted_holds = self.process_holds(book_id)

    def delete_book(self, book_id):
        book = self.get_book(book_id)
        if book["available_copies"] < book["total_copies"]:
            raise LibraryError("This book has copies on loan; return them first.")
        has_history = self.db.query_one(
            "SELECT COUNT(*) AS n FROM loans WHERE book_id = ?", (book_id,))["n"]
        if has_history:
            raise LibraryError("This book has loan history; set its copies to 0 "
                               "instead of deleting it.")
        if self._active_holds(book_id):
            raise LibraryError("Members have reserved this book; cancel those first.")
        with self.db.transaction():
            self.db.execute("DELETE FROM reservations WHERE book_id = ?", (book_id,))
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

    def update_member(self, member_id, name, email, phone=None):
        self.get_member(member_id)
        if not name or not email or "@" not in email:
            raise LibraryError("A name and a valid email are required.")
        try:
            with self.db.transaction():
                self.db.execute(
                    "UPDATE members SET name = ?, email = ?, phone = ? WHERE id = ?",
                    (name, email.lower(), phone or None, member_id))
        except IntegrityError as exc:
            raise LibraryError(f"Another member already uses {email}.") from exc

    def set_member_active(self, member_id, active):
        self.get_member(member_id)
        if not active and self.active_loans(member_id):
            raise LibraryError("Member still has books out; return them first.")
        holds = [] if active else self.member_reservations(member_id)
        with self.db.transaction():
            self.db.execute("UPDATE members SET active = ? WHERE id = ?",
                            (1 if active else 0, member_id))
            for h in holds:
                self.db.execute("UPDATE reservations SET status = 'cancelled' "
                                "WHERE id = ?", (h["id"],))
        for book_id in {h["book_id"] for h in holds}:
            self.process_holds(book_id)

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
        owed = self.unpaid_fines(member_id)
        if owed > self.max_unpaid_fines:
            raise LibraryError(f"{member['name']} owes {owed} in unpaid fines; "
                               "collect payment first.")
        if book["available_copies"] < 1:
            raise LibraryError(f"No copies of '{book['title']}' are available. "
                               "You can reserve it instead.")
        self.process_holds(book_id, today)
        holds = self._active_holds(book_id)
        own_hold = next((h for h in holds if h["member_id"] == member_id), None)
        held_for_others = [h for h in holds if h["status"] == "ready"
                           and h["member_id"] != member_id]
        if (own_hold is None or own_hold["status"] != "ready") and \
                book["available_copies"] - len(held_for_others) < 1:
            names = ", ".join(h["member_name"] for h in held_for_others)
            raise LibraryError(f"The copies on the shelf are being held for {names}.")
        due = today + timedelta(days=self.loan_days)
        with self.db.transaction():
            if own_hold:
                self.db.execute("UPDATE reservations SET status = 'fulfilled' "
                                "WHERE id = ?", (own_hold["id"],))
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
        self.promoted_holds = self.process_holds(loan["book_id"], today)
        return fine

    def renew_loan(self, loan_id, today=None):
        today = today or date.today()
        loan = self.get_loan(loan_id)
        if loan["returned_on"]:
            raise LibraryError("Cannot renew a returned loan.")
        if loan["due_on"] < today:
            raise LibraryError("Overdue loans cannot be renewed; return the book.")
        if self._active_holds(loan["book_id"]):
            raise LibraryError("Someone has reserved this book, so it can't be renewed.")
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
            "fines_collected": _money(q(
                "SELECT COALESCE(SUM(fine), 0) AS n FROM loans WHERE fine_paid = 1")["n"]),
            "fines_outstanding": _money(q(
                "SELECT COALESCE(SUM(fine), 0) AS n FROM loans "
                "WHERE fine_paid = 0 AND returned_on IS NOT NULL")["n"]),
            "reservations": int(q("SELECT COUNT(*) AS n FROM reservations "
                                  "WHERE status IN ('waiting', 'ready')")["n"]),
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

    # -- fines -----------------------------------------------------------
    def unpaid_fines(self, member_id):
        return _money(self.db.query_one(
            "SELECT COALESCE(SUM(fine), 0) AS n FROM loans WHERE member_id = ? "
            "AND fine_paid = 0 AND returned_on IS NOT NULL", (member_id,))["n"])

    def pay_fine(self, loan_id):
        loan = self.get_loan(loan_id)
        if not loan["returned_on"]:
            raise LibraryError("Fines are settled when the book is returned.")
        if not loan["fine"]:
            raise LibraryError("There is no fine on this loan.")
        if loan["fine_paid"]:
            raise LibraryError("This fine has already been paid.")
        with self.db.transaction():
            self.db.execute("UPDATE loans SET fine_paid = 1 WHERE id = ?", (loan_id,))
        return loan["fine"]

    def unpaid_fine_loans(self):
        return [_normalize_loan(r) for r in self.db.query(
            "SELECT l.*, b.title, m.name AS member_name FROM loans l "
            "JOIN books b ON b.id = l.book_id JOIN members m ON m.id = l.member_id "
            "WHERE l.fine > 0 AND l.fine_paid = 0 AND l.returned_on IS NOT NULL "
            "ORDER BY l.returned_on")]

    # -- reservations ----------------------------------------------------
    _RES_SQL = ("SELECT r.*, b.title, b.author, m.name AS member_name, m.email "
                "FROM reservations r JOIN books b ON b.id = r.book_id "
                "JOIN members m ON m.id = r.member_id ")

    def _active_holds(self, book_id):
        return [_normalize_loan(r) for r in self.db.query(
            self._RES_SQL + "WHERE r.book_id = ? AND r.status IN ('waiting', 'ready') "
            "ORDER BY r.reserved_on, r.id", (book_id,))]

    def reserve_book(self, book_id, member_id, today=None):
        today = today or date.today()
        book = self.get_book(book_id)
        member = self.get_member(member_id)
        if not member["active"]:
            raise LibraryError(f"{member['name']}'s membership is inactive.")
        if any(l["book_id"] == book_id for l in self.active_loans(member_id)):
            raise LibraryError(f"{member['name']} already has this book.")
        holds = self._active_holds(book_id)
        if any(h["member_id"] == member_id for h in holds):
            raise LibraryError(f"{member['name']} has already reserved this book.")
        ready = sum(1 for h in holds if h["status"] == "ready")
        if book["available_copies"] - ready > 0:
            raise LibraryError("A copy is on the shelf; issue it instead of reserving.")
        if len(self.member_reservations(member_id)) >= self.max_reservations:
            raise LibraryError(f"{member['name']} already has "
                               f"{self.max_reservations} reservations.")
        with self.db.transaction():
            return self.db.execute(
                "INSERT INTO reservations (book_id, member_id, reserved_on, status) "
                "VALUES (?, ?, ?, 'waiting')", (book_id, member_id, today.isoformat()))

    def get_reservation(self, reservation_id):
        res = self.db.query_one(self._RES_SQL + "WHERE r.id = ?", (reservation_id,))
        if not res:
            raise LibraryError(f"No reservation with id {reservation_id}.")
        return _normalize_loan(res)

    def cancel_reservation(self, reservation_id, today=None):
        res = self.get_reservation(reservation_id)
        if res["status"] not in ("waiting", "ready"):
            raise LibraryError("This reservation is no longer active.")
        with self.db.transaction():
            self.db.execute("UPDATE reservations SET status = 'cancelled' WHERE id = ?",
                            (reservation_id,))
        self.promoted_holds = self.process_holds(res["book_id"], today)
        return res

    def process_holds(self, book_id, today=None):
        """Expire stale holds and hold free copies for the next people in line.

        Returns the reservations that just became ready for pickup.
        """
        today = today or date.today()
        book = self.get_book(book_id)
        holds = self._active_holds(book_id)
        promoted = []
        with self.db.transaction():
            for h in holds:
                if h["status"] == "ready" and \
                        (today - h["ready_on"]).days > self.hold_days:
                    self.db.execute("UPDATE reservations SET status = 'expired' "
                                    "WHERE id = ?", (h["id"],))
                    h["status"] = "expired"
            ready = sum(1 for h in holds if h["status"] == "ready")
            free = book["available_copies"] - ready
            for h in holds:
                if free <= 0:
                    break
                if h["status"] == "waiting":
                    self.db.execute("UPDATE reservations SET status = 'ready', "
                                    "ready_on = ? WHERE id = ?",
                                    (today.isoformat(), h["id"]))
                    h["status"], h["ready_on"] = "ready", today
                    promoted.append(h)
                    free -= 1
        return promoted

    def process_all_holds(self, today=None):
        for row in self.db.query("SELECT DISTINCT book_id FROM reservations "
                                 "WHERE status IN ('waiting', 'ready')"):
            self.process_holds(row["book_id"], today)

    def _with_queue_positions(self, rows):
        position = {}
        for r in sorted(rows, key=lambda r: (r["reserved_on"], r["id"])):
            if r["status"] == "waiting":
                position[r["book_id"]] = position.get(r["book_id"], 0) + 1
                r["position"] = position[r["book_id"]]
            if r["status"] == "ready":
                r["hold_until"] = r["ready_on"] + timedelta(days=self.hold_days)
        return rows

    def list_reservations(self, today=None):
        self.process_all_holds(today)
        rows = [_normalize_loan(r) for r in self.db.query(
            self._RES_SQL + "WHERE r.status IN ('waiting', 'ready') "
            "ORDER BY r.status DESC, r.reserved_on, r.id")]
        return self._with_queue_positions(rows)

    def member_reservations(self, member_id):
        rows = [_normalize_loan(r) for r in self.db.query(
            self._RES_SQL + "WHERE r.member_id = ? AND r.status IN ('waiting', 'ready') "
            "ORDER BY r.reserved_on", (member_id,))]
        # queue position counts everyone ahead for the same book
        for r in rows:
            if r["status"] == "waiting":
                queue = [h for h in self._active_holds(r["book_id"])
                         if h["status"] == "waiting"]
                r["position"] = [h["id"] for h in queue].index(r["id"]) + 1
            else:
                r["hold_until"] = r["ready_on"] + timedelta(days=self.hold_days)
        return rows

    def held_copies(self):
        """{book_id: copies on the shelf that are held for a reservation}"""
        return {r["book_id"]: int(r["n"]) for r in self.db.query(
            "SELECT book_id, COUNT(*) AS n FROM reservations WHERE status = 'ready' "
            "GROUP BY book_id")}

    def book_reservations(self, book_id):
        return self._with_queue_positions(self._active_holds(book_id))
