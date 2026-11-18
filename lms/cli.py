"""Interactive command-line interface."""
from .db import Database
from .services import Library, LibraryError


def _table(rows, columns):
    """Print rows (list of dicts) as an aligned text table."""
    if not rows:
        print("  (nothing to show)")
        return
    headers = [label for label, _ in columns]
    data = [[("" if r.get(key) is None else str(r.get(key))) for _, key in columns]
            for r in rows]
    widths = [max(len(h), *(len(row[i]) for row in data)) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("-" * len(line))
    for row in data:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)))


def _ask(prompt, required=True, cast=str, default=None):
    while True:
        raw = input(f"{prompt}{f' [{default}]' if default is not None else ''}: ").strip()
        if not raw:
            if default is not None:
                return default
            if not required:
                return None
            print("  This field is required.")
            continue
        try:
            return cast(raw)
        except ValueError:
            print("  Please enter a valid value.")


BOOK_COLS = [("ID", "id"), ("ISBN", "isbn"), ("Title", "title"), ("Author", "author"),
             ("Genre", "genre"), ("Year", "published_year"),
             ("Avail", "available_copies"), ("Total", "total_copies")]
MEMBER_COLS = [("ID", "id"), ("Name", "name"), ("Email", "email"),
               ("Phone", "phone"), ("Active", "active")]
LOAN_COLS = [("Loan", "id"), ("Book", "title"), ("Issued", "issued_on"),
             ("Due", "due_on"), ("Returned", "returned_on"), ("Fine", "fine")]


class CLI:
    def __init__(self, library: Library):
        self.lib = library
        self.menu = [
            ("Add a book", self.add_book),
            ("Search books", self.search_books),
            ("Update a book", self.update_book),
            ("Add or remove copies", self.change_copies),
            ("Delete a book", self.delete_book),
            ("Register a member", self.add_member),
            ("Search members", self.search_members),
            ("Activate / deactivate a member", self.toggle_member),
            ("Issue a book", self.issue_book),
            ("Return a book", self.return_book),
            ("Renew a loan", self.renew_loan),
            ("Member borrowing history", self.history),
            ("Overdue report", self.overdue),
            ("Library stats", self.stats),
        ]

    def run(self):
        print("\n=== Library Management System ===")
        while True:
            print()
            for i, (label, _) in enumerate(self.menu, 1):
                print(f"{i:>2}. {label}")
            print(" 0. Exit")
            choice = input("\nChoose an option: ").strip()
            if choice == "0":
                print("Goodbye!")
                return
            if not choice.isdigit() or not 1 <= int(choice) <= len(self.menu):
                print("Invalid choice.")
                continue
            print()
            try:
                self.menu[int(choice) - 1][1]()
            except LibraryError as exc:
                print(f"Error: {exc}")

    # -- actions ---------------------------------------------------------
    def add_book(self):
        book_id = self.lib.add_book(
            _ask("ISBN"), _ask("Title"), _ask("Author"),
            _ask("Genre", required=False),
            _ask("Published year", required=False, cast=int),
            _ask("Number of copies", cast=int, default=1))
        print(f"Book added with id {book_id}.")

    def search_books(self):
        term = _ask("Search title/author/genre/ISBN (blank for all)", required=False) or ""
        only = (_ask("Only available books? (y/n)", default="n").lower() == "y")
        _table(self.lib.search_books(term, available_only=only), BOOK_COLS)

    def update_book(self):
        book = self.lib.get_book(_ask("Book id", cast=int))
        print("Press Enter to keep the current value.")
        self.lib.update_book(
            book["id"],
            isbn=_ask("ISBN", default=book["isbn"]),
            title=_ask("Title", default=book["title"]),
            author=_ask("Author", default=book["author"]),
            genre=_ask("Genre", required=False, default=book["genre"]),
            published_year=_ask("Published year", required=False, cast=int,
                                default=book["published_year"]))
        print("Book updated.")

    def change_copies(self):
        book_id = _ask("Book id", cast=int)
        delta = _ask("Copies to add (use a negative number to remove)", cast=int)
        self.lib.change_copies(book_id, delta)
        print("Copies updated.")

    def delete_book(self):
        book = self.lib.get_book(_ask("Book id", cast=int))
        if _ask(f"Delete '{book['title']}'? (y/n)", default="n").lower() == "y":
            self.lib.delete_book(book["id"])
            print("Book deleted.")

    def add_member(self):
        member_id = self.lib.add_member(_ask("Name"), _ask("Email"),
                                        _ask("Phone", required=False))
        print(f"Member registered with id {member_id}.")

    def search_members(self):
        term = _ask("Search name/email (blank for all)", required=False) or ""
        _table(self.lib.search_members(term), MEMBER_COLS)

    def toggle_member(self):
        member = self.lib.get_member(_ask("Member id", cast=int))
        self.lib.set_member_active(member["id"], not member["active"])
        print(f"{member['name']} is now {'inactive' if member['active'] else 'active'}.")

    def issue_book(self):
        loan_id = self.lib.issue_book(_ask("Book id", cast=int), _ask("Member id", cast=int))
        loan = self.lib.get_loan(loan_id)
        print(f"Issued '{loan['title']}' to {loan['member_name']} (loan {loan_id}). "
              f"Due on {loan['due_on']}.")

    def return_book(self):
        member_id = _ask("Member id (to list their loans)", required=False, cast=int)
        if member_id:
            _table(self.lib.active_loans(member_id), LOAN_COLS)
        fine = self.lib.return_book(_ask("Loan id", cast=int))
        print("Book returned." + (f" Late fine due: {fine}" if fine else " No fine."))

    def renew_loan(self):
        new_due = self.lib.renew_loan(_ask("Loan id", cast=int))
        print(f"Loan renewed. New due date: {new_due}.")

    def history(self):
        _table(self.lib.member_history(_ask("Member id", cast=int)), LOAN_COLS)

    def overdue(self):
        _table(self.lib.overdue_loans(),
               [("Loan", "id"), ("Book", "title"), ("Member", "member_name"),
                ("Email", "email"), ("Due", "due_on"), ("Days late", "days_overdue"),
                ("Fine so far", "fine_so_far")])

    def stats(self):
        for key, value in self.lib.stats().items():
            print(f"  {key.replace('_', ' ').capitalize():<16} {value}")
        print("\nMost borrowed:")
        _table(self.lib.popular_books(),
               [("ID", "id"), ("Title", "title"), ("Author", "author"),
                ("Times borrowed", "times_borrowed")])


def main():
    db = Database()
    try:
        CLI(Library(db)).run()
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")
    finally:
        db.close()
