"""Sample data so a fresh install (or the live demo) has something to show."""
from datetime import date, timedelta

from .services import Library, LibraryError

BOOKS = [
    ("9780061120084", "To Kill a Mockingbird", "Harper Lee", "Fiction", 1960, 3),
    ("9780451524935", "1984", "George Orwell", "Dystopian", 1949, 2),
    ("9780743273565", "The Great Gatsby", "F. Scott Fitzgerald", "Fiction", 1925, 2),
    ("9780140449136", "Crime and Punishment", "Fyodor Dostoevsky", "Classic", 1866, 1),
    ("9780262033848", "Introduction to Algorithms", "Thomas H. Cormen", "Computer Science", 2009, 2),
    ("9780132350884", "Clean Code", "Robert C. Martin", "Computer Science", 2008, 1),
    ("9781416562597", "The White Tiger", "Aravind Adiga", "Fiction", 2008, 2),
    ("9780143039655", "Malgudi Days", "R. K. Narayan", "Short Stories", 1943, 1),
    ("9780547928227", "The Hobbit", "J. R. R. Tolkien", "Fantasy", 1937, 3),
    ("9780590353427", "Harry Potter and the Sorcerer's Stone", "J. K. Rowling", "Fantasy", 1997, 4),
    ("9780141439518", "Pride and Prejudice", "Jane Austen", "Classic", 1813, 2),
    ("9780062316097", "Sapiens", "Yuval Noah Harari", "History", 2011, 2),
    ("9780735211292", "Atomic Habits", "James Clear", "Self-help", 2018, 3),
    ("9780553380163", "A Brief History of Time", "Stephen Hawking", "Science", 1988, 1),
    ("9780316769488", "The Catcher in the Rye", "J. D. Salinger", "Fiction", 1951, 1),
    ("9780060850524", "Brave New World", "Aldous Huxley", "Dystopian", 1932, 2),
]
MEMBERS = [
    ("Aarav Sharma", "aarav@example.com", "98765 00001"),
    ("Priya Verma", "priya@example.com", "98765 00002"),
    ("Rohan Gupta", "rohan@example.com", "98765 00003"),
    ("Meera Iyer", "meera@example.com", None),
    ("Kabir Singh", "kabir@example.com", "98765 00005"),
    ("Ananya Rao", "ananya@example.com", None),
]
# (book index, member index, issued days ago, returned days ago or None)
LOANS = [
    (1, 0, 20, None),   # overdue
    (8, 1, 12, None),   # due soon
    (9, 2, 5, None),
    (12, 3, 2, None),
    (0, 4, 30, 18),
    (9, 5, 40, 20),     # returned late, fine
    (11, 0, 60, 50),
    (9, 1, 70, 58),
    (5, 2, 25, None),   # overdue
]


def seed_demo_data(lib: Library, today=None):
    """Load sample data. Does nothing if the library already has books."""
    today = today or date.today()
    if lib.stats()["titles"]:
        return False
    book_ids = [lib.add_book(*b) for b in BOOKS]
    member_ids = [lib.add_member(name, email, phone,
                                 joined_on=today - timedelta(days=90 - 10 * i))
                  for i, (name, email, phone) in enumerate(MEMBERS)]
    for book_i, member_i, issued_ago, returned_ago in LOANS:
        try:
            loan = lib.issue_book(book_ids[book_i], member_ids[member_i],
                                  today=today - timedelta(days=issued_ago))
            if returned_ago is not None:
                lib.return_book(loan, today=today - timedelta(days=returned_ago))
        except LibraryError:
            pass
    return True
