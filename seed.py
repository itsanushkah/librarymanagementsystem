"""Load sample books and members: python seed.py"""
from lms.db import Database
from lms.services import Library, LibraryError

BOOKS = [
    ("9780061120084", "To Kill a Mockingbird", "Harper Lee", "Fiction", 1960, 3),
    ("9780451524935", "1984", "George Orwell", "Dystopian", 1949, 2),
    ("9780743273565", "The Great Gatsby", "F. Scott Fitzgerald", "Fiction", 1925, 2),
    ("9780140449136", "Crime and Punishment", "Fyodor Dostoevsky", "Classic", 1866, 1),
    ("9780262033848", "Introduction to Algorithms", "Cormen et al.", "Computer Science", 2009, 2),
    ("9780132350884", "Clean Code", "Robert C. Martin", "Computer Science", 2008, 1),
    ("9780143424420", "The White Tiger", "Aravind Adiga", "Fiction", 2008, 2),
    ("9780679731726", "Malgudi Days", "R. K. Narayan", "Short Stories", 1943, 1),
]
MEMBERS = [
    ("Aarav Sharma", "aarav@example.com", "9876500001"),
    ("Priya Verma", "priya@example.com", "9876500002"),
    ("Rohan Gupta", "rohan@example.com", None),
]

if __name__ == "__main__":
    db = Database()
    lib = Library(db)
    for book in BOOKS:
        try:
            lib.add_book(*book)
        except LibraryError as exc:
            print(f"skip: {exc}")
    for member in MEMBERS:
        try:
            lib.add_member(*member)
        except LibraryError as exc:
            print(f"skip: {exc}")
    print("Sample data loaded:", lib.stats())
    db.close()
